"""Local web UI backend: a thin JSON API over api.py / facts.py / generate.py.

Run with `bzm-opl-gen ui` (requires `pip install bzm-opl-gen[ui]`). Serves the
prebuilt SPA from ui_dist/ and the API under /api. Single-user, local-only by
design: binds 127.0.0.1, holds one BzmClient in process memory. The API key
secret never leaves this machine and is never echoed back to the browser.
"""

import io
import json
import os
import time
import zipfile
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import api, facts as facts_mod, generate as gen_mod

app = FastAPI(title="bzm-opl-gen", docs_url="/api/docs", openapi_url="/api/openapi.json")

_state = {"client": None, "key_id": None}

CONFIG_DIR = os.path.expanduser("~/.config/bzm-opl-gen")
SAVED_KEY_PATH = os.path.join(CONFIG_DIR, "api-key.json")
KEY_CANDIDATES = [
    os.environ.get("BZM_API_KEY_FILE"),
    "api-key.json",
    SAVED_KEY_PATH,
    os.path.expanduser("~/.bzm/api-key.json"),
]
PROFILE_DIR = os.path.join(os.path.dirname(__file__), "profiles")


def _client():
    if _state["client"] is None:
        raise HTTPException(401, "no API key configured -- POST /api/key first")
    return _state["client"]


def _wrap(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except api.BzmApiError as e:
        raise HTTPException(502, str(e))


# -- key management -----------------------------------------------------------

class KeyIn(BaseModel):
    path: Optional[str] = None    # use an existing api-key.json
    id: Optional[str] = None      # or paste id+secret
    secret: Optional[str] = None
    save: bool = False            # persist pasted key to SAVED_KEY_PATH


@app.get("/api/key/detect")
def key_detect():
    found = []
    for p in KEY_CANDIDATES:
        if not p:
            continue
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isfile(p) and p not in [f["path"] for f in found]:
            try:
                with open(p) as fh:
                    kid = json.load(fh).get("id", "?")
                found.append({"path": p, "key_id": kid})
            except (ValueError, OSError):
                continue
    return {"candidates": found, "active_key_id": _state["key_id"]}


@app.post("/api/key")
def key_set(k: KeyIn):
    if k.path:
        path = os.path.expanduser(k.path)
        if not os.path.isfile(path):
            raise HTTPException(400, f"no such file: {path}")
    elif k.id and k.secret:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        path = SAVED_KEY_PATH if k.save else os.path.join(CONFIG_DIR, ".session-key.json")
        with open(path, "w") as fh:
            json.dump({"id": k.id, "secret": k.secret}, fh)
        os.chmod(path, 0o600)
        if not k.save:
            # session-only: file exists just long enough to construct the client
            pass
    else:
        raise HTTPException(400, "provide path, or id+secret")
    client = api.BzmClient(path)
    user = _wrap(client.user)
    if k.id and k.secret and not k.save:
        os.unlink(path)
    _state["client"] = client
    _state["key_id"] = json.load(open(os.path.expanduser(k.path)))["id"] if k.path else k.id
    return {
        "user": {"email": user.get("email"), "display_name": user.get("displayName")},
        "default_account_id": (user.get("defaultProject") or {}).get("accountId"),
        "key_id": _state["key_id"],
        "saved": bool(k.id and k.save),
    }


# -- account tree -------------------------------------------------------------

@app.get("/api/accounts")
def accounts():
    return _wrap(_client().accounts)


@app.get("/api/workspaces")
def workspaces(account_id: int):
    return _wrap(_client().workspaces, account_id)


@app.get("/api/locations")
def locations(account_id: Optional[int] = None, workspace_id: Optional[int] = None):
    if not account_id and not workspace_id:
        raise HTTPException(400, "account_id or workspace_id required")
    return _wrap(_client().private_locations, account_id, workspace_id)


class LocationIn(BaseModel):
    name: str
    account_id: int
    workspace_id: int
    func_ids: list[str] = ["performance"]
    slots: int = 1
    threads_per_engine: int = api.DEFAULT_THREADS_PER_ENGINE


@app.post("/api/locations")
def location_create(loc: LocationIn):
    return _wrap(_client().create_private_location, loc.name, loc.account_id,
                 [loc.workspace_id], func_ids=loc.func_ids, slots=loc.slots,
                 threads_per_engine=loc.threads_per_engine)


class ShipIn(BaseModel):
    harbor_id: str
    name: str


@app.post("/api/ships")
def ship_create(s: ShipIn):
    ship = _wrap(_client().create_ship, s.harbor_id, s.name)
    return {"ship": ship}


@app.get("/api/facts")
def get_facts(harbor_id: str):
    return _wrap(facts_mod.gather, _client(), harbor_id)


@app.get("/api/status")
def agent_status(harbor_id: str, ship_id: str):
    harbor = _wrap(_client().private_location, harbor_id)
    ship = next((s for s in harbor.get("ships", []) if s["id"] == ship_id), None)
    if not ship:
        raise HTTPException(404, f"ship {ship_id} not in location {harbor_id}")
    hb = ship.get("lastHeartBeat") or 0
    return {
        "state": ship.get("state"),
        "heartbeat_age_s": int(time.time() - hb) if hb else None,
        "installed_version": ship.get("installedVersion"),
        "online": bool(hb and time.time() - hb < 120
                       and ship.get("state") in ("idle", "running")),
    }


# -- generation ---------------------------------------------------------------

class GenerateIn(BaseModel):
    facts: dict
    options: dict = {}
    fetch_token: bool = True      # pull AUTH_TOKEN via docker-command endpoint


def _generate(g: GenerateIn):
    opts = dict(g.options)
    if g.fetch_token and not opts.get("auth_token") and _state["client"]:
        ships = g.facts.get("ships") or []
        ship_id = opts.get("ship_id") or (ships[0]["id"] if len(ships) == 1 else None)
        if ship_id:
            opts["auth_token"] = _wrap(_client().auth_token, g.facts["harbor_id"], ship_id)
    try:
        return gen_mod.generate(g.facts, opts)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/generate")
def generate_preview(g: GenerateIn):
    files = _generate(g)
    order = gen_mod.APPLY_ORDER + ["bzm-opl-image-mirror.sh", "README.md"]
    names = [n for n in order if n in files] + sorted(set(files) - set(order))
    return {"files": [{"name": n, "content": files[n]} for n in names]}


@app.post("/api/generate/zip")
def generate_zip(g: GenerateIn):
    files = _generate(g)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            info = zipfile.ZipInfo(f"bzm-opl/{name}")
            if name.endswith(".sh"):
                info.external_attr = 0o755 << 16
            z.writestr(info, content)
    ns = g.options.get("namespace", "blazemeter")
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="bzm-opl-{ns}.zip"'})


# -- profiles -----------------------------------------------------------------

@app.get("/api/profiles")
def profiles():
    out = []
    for fn in sorted(os.listdir(PROFILE_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(PROFILE_DIR, fn)) as fh:
                out.append({"name": fn[:-5], "options": json.load(fh)})
    return out


@app.get("/api/option-defaults")
def option_defaults():
    return gen_mod.DEFAULT_OPTIONS


@app.get("/api/sv-constants")
def sv_constants():
    """The two service-virtualization enumerations the UI must not hardcode.

    Kept out of /api/option-defaults because that response is spread straight
    into the options the UI submits, and these are not options. Serving them is
    what stops a fifth expose backend from being added to generate() and
    silently missing from the picker -- the funcId list in particular was
    duplicated in TypeScript with a comment asking the next person to keep it in
    step by hand.
    """
    return {"func_ids": list(gen_mod.SV_FUNC_IDS),
            "ingress_types": list(gen_mod.SV_INGRESS_TYPES)}


# -- SPA ----------------------------------------------------------------------

UI_DIST = os.path.join(os.path.dirname(__file__), "ui_dist")
if os.path.isdir(UI_DIST):
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
else:
    @app.get("/")
    def no_ui():
        return {"error": "ui_dist not built -- run `npm run build` in frontend/",
                "api_docs": "/api/docs"}


def main(port=8765, open_browser=True, api_key_path=None, dev=False):
    import uvicorn
    if api_key_path:
        _state["client"] = api.BzmClient(api_key_path)
        with open(api_key_path) as fh:
            _state["key_id"] = json.load(fh).get("id")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.8, webbrowser.open, [f"http://127.0.0.1:{port}"]).start()
    if dev:
        # Reload needs an import string; the reloader subprocess starts fresh,
        # so pass the key via env for /api/key/detect to find.
        if api_key_path:
            os.environ["BZM_API_KEY_FILE"] = os.path.abspath(api_key_path)
        uvicorn.run("bzm_opl_gen.server:app", host="127.0.0.1", port=port,
                    reload=True, reload_dirs=[os.path.dirname(__file__)],
                    log_level="info")
    else:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
