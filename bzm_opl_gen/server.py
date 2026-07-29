"""Local web UI backend: HTTP over core.py.

Run with `bzm-opl-gen ui` (requires `pip install bzm-opl-gen[ui]`). Serves the
prebuilt SPA from ui_dist/ and the API under /api. Single-user by design: it
holds one BzmClient in process memory, so reaching the page is equivalent to
holding the API key. The secret itself never leaves this machine and is never
echoed back to the browser.

Binds 127.0.0.1 by default for that reason. `--host` widens it for the case
where the machine running this is not the machine you are sitting at, and warns
on the way out; an SSH tunnel to the default bind is the safer shape and costs
nothing extra.

What is here and what is in core: this module is the request. Routes, request
bodies, status codes, the zip download's headers, where an API key is kept for
the length of a browser session, and how the process is bound. Every decision
*about OPL* is core's, so that a caller with no requests at all -- the MCP
server -- reaches the same answers rather than restating them. The translation
is one line, `_answer`: core raises CoreError carrying the status, and nothing
here re-decides what a given refusal means.
"""

import json
import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import api, core

app = FastAPI(title="bzm-opl-gen", docs_url="/api/docs", openapi_url="/api/openapi.json")

_state = {"client": None, "key_id": None}

# Where a pasted key is kept for a browser session -- this module's business,
# so named here. The vocabulary (FEATURES, the sv-check constants) deliberately
# is *not* re-exported: an alias is a second name for one list, it does not
# follow when the list is replaced, and reaching for it is how a caller ends up
# describing something core is no longer serving.
CONFIG_DIR = core.CONFIG_DIR
SAVED_KEY_PATH = core.SAVED_KEY_PATH


def _client():
    """The client this browser session is acting as.

    The 401 is the transport's, not core's: what to do about a missing
    credential is "POST /api/key" here and something else entirely over stdio,
    so core takes a client and never goes looking for one.
    """
    if _state["client"] is None:
        raise HTTPException(401, "no API key configured -- POST /api/key first")
    return _state["client"]


def _answer(fn, *args, **kw):
    """Run a core call, and turn its refusal into this transport's."""
    try:
        return fn(*args, **kw)
    except core.CoreError as e:
        raise HTTPException(e.status, str(e))


# -- key management -----------------------------------------------------------
# Not core's: this is a browser session's credential lifetime -- where a pasted
# key is kept, and for how long -- which is a fact about running a single-user
# web server and about nothing else.

class KeyIn(BaseModel):
    path: Optional[str] = None    # use an existing api-key.json
    id: Optional[str] = None      # or paste id+secret
    secret: Optional[str] = None
    save: bool = False            # persist pasted key to SAVED_KEY_PATH


@app.get("/api/key/detect")
def key_detect():
    return {"candidates": core.detect_keys(), "active_key_id": _state["key_id"]}


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
    user = _answer(core.user, client)
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
    return _answer(core.accounts, _client())


@app.get("/api/workspaces")
def workspaces(account_id: int):
    return _answer(core.workspaces, _client(), account_id)


@app.get("/api/locations")
def locations(account_id: Optional[int] = None, workspace_id: Optional[int] = None):
    return _answer(core.locations, _client(), account_id, workspace_id)


class LocationIn(BaseModel):
    name: str
    account_id: int
    workspace_id: int
    func_ids: list[str] = ["performance"]
    slots: int = 1
    threads_per_engine: int = api.DEFAULT_THREADS_PER_ENGINE


@app.post("/api/locations")
def location_create(loc: LocationIn):
    return _answer(core.create_location, _client(), loc.name, loc.account_id,
                   loc.workspace_id, func_ids=loc.func_ids, slots=loc.slots,
                   threads_per_engine=loc.threads_per_engine)


class ShipIn(BaseModel):
    harbor_id: str
    name: str


@app.post("/api/ships")
def ship_create(s: ShipIn):
    return {"ship": _answer(core.create_ship, _client(), s.harbor_id, s.name)}


@app.get("/api/facts")
def get_facts(harbor_id: str):
    return _answer(core.gather_facts, _client(), harbor_id)


class ManualFactsIn(BaseModel):
    harbor_id: str
    ship_id: str
    func_ids: list = ["performance"]


@app.post("/api/facts/manual")
def manual_facts(m: ManualFactsIn):
    """Deliberately not behind _client(): requiring a key here would defeat the
    point of the manual path -- see core.manual_facts."""
    return _answer(core.manual_facts, m.harbor_id, m.ship_id,
                   func_ids=m.func_ids)


@app.get("/api/status")
def agent_status(harbor_id: str, ship_id: str):
    return _answer(core.agent_status, _client(), harbor_id, ship_id)


# -- generation ---------------------------------------------------------------

class GenerateIn(BaseModel):
    facts: dict
    options: dict = {}
    fetch_token: bool = True      # pull AUTH_TOKEN via docker-command endpoint


def _generate(g: GenerateIn):
    return _answer(core.generate_bundle, g.facts, g.options,
                   client=_state["client"], fetch_token=g.fetch_token)


@app.post("/api/generate")
def generate_preview(g: GenerateIn):
    files = _generate(g)
    return {"files": [{"name": n, "content": files[n]}
                      for n in core.preview_order(files)]}


@app.post("/api/generate/zip")
def generate_zip(g: GenerateIn):
    body = core.zip_bundle(_generate(g))
    name = core.zip_filename(g.options)
    return Response(body, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# -- preflight ----------------------------------------------------------------

class PreflightIn(BaseModel):
    facts: dict
    options: dict = {}
    # `Any`, not `dict`: a file that is not an object at all -- a JSON array, a
    # number -- has to reach doctor's own refusal, which names what it found and
    # what it wanted. Typed as dict here it would come back as a 422 naming a
    # field of this model, which tells the person who picked the wrong file
    # nothing about the file.
    evidence: Any


@app.post("/api/preflight")
def preflight(p: PreflightIn):
    """The verdicts `doctor --cluster-evidence` prints, for the configuration
    the browser currently holds.

    Deliberately not behind _client(), for the same reason /api/facts/manual is
    not: reading an evidence file needs no BlazeMeter account and no cluster,
    and requiring a key would put a preflight behind the one thing this case
    does not have. See core.preflight for the rest.
    """
    return _answer(core.preflight, p.facts, p.options, p.evidence)


# -- reading the cluster ------------------------------------------------------

@app.get("/api/sv-mocks")
def sv_mocks(namespace: str, sv_subdomain: Optional[str] = None):
    """What is deployed in `namespace`, and the host each one answers at.

    Answers 200 whatever the cluster did, including "there is no kubectl here"
    -- this rides the UI's 10s status poll, and a route that 4xx'd every tick
    either fills the console or gets swallowed by the caller's catch and reads
    as "nothing deployed", which is the one answer it must never fake.
    """
    return _answer(core.sv_mocks, namespace, sv_subdomain)


@app.get("/api/sv-check")
def sv_check(host: str, scheme: str = "http"):
    """Ask whether the endpoint a deployed virtual service publishes answers.

    Answers 200 whatever happened, for the same reason: an endpoint that does
    not answer is the expected finding of this button, not a broken request.
    The two 4xx cases are inputs that are not an endpoint at all.

    Declared `def`, not `async def`, so FastAPI runs it on a worker thread --
    a probe waiting out its deadline must not stop the status poll behind it.
    """
    return _answer(core.sv_check, host, scheme)


# -- the vocabulary -----------------------------------------------------------

@app.get("/api/option-defaults")
def option_defaults():
    """Bare option -> default. The UI spreads this straight into the options it
    submits, so a metadata key here would arrive at generate() as an option --
    which is why the descriptions are a route of their own."""
    return core.option_defaults()


@app.get("/api/option-docs")
def option_docs():
    return core.option_docs()


@app.get("/api/func-ids")
def func_ids():
    return core.func_ids()


@app.get("/api/features")
def features():
    return core.features()


@app.get("/api/sv-constants")
def sv_constants():
    return core.sv_constants()


# -- SPA ----------------------------------------------------------------------

UI_DIST = os.path.join(os.path.dirname(__file__), "ui_dist")
if os.path.isdir(UI_DIST):
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
else:
    @app.get("/")
    def no_ui():
        return {"error": "ui_dist not built -- run `npm run build` in frontend/",
                "api_docs": "/api/docs"}


LOOPBACK = ("127.0.0.1", "::1", "localhost")

# Said at startup rather than left to the README: by the time the bind is wrong
# the page is already reachable, and the expensive mistake behind it is not
# reading manifests -- it is the download button, which fetches an AUTH_TOKEN
# and thereby rotates it, leaving any agent already running for that ship on a
# token the API no longer accepts (it logs 404 on /ships/<id>/status and sits
# at 0/1, which reads like a deleted ship).
EXPOSED_WARNING = """\
!! bzm-opl-gen ui is bound to {host}, so it is reachable from outside this
!! machine. Anyone who reaches it can act as your BlazeMeter API key -- and
!! downloading a bundle fetches an AUTH_TOKEN, which ROTATES it and breaks
!! whatever agent is already running for that ship.
!! Prefer the default 127.0.0.1 plus an SSH tunnel:
!!   ssh -L {port}:127.0.0.1:{port} <this-machine>
"""


def main(port=8765, open_browser=True, api_key_path=None, dev=False,
         host="127.0.0.1"):
    import uvicorn
    if host not in LOOPBACK:
        # flush: redirected stdout is block-buffered, and a warning that only
        # materialises when the process exits is no warning at all -- this is
        # the one line that has to arrive before the port is open.
        print(EXPOSED_WARNING.format(host=host, port=port), flush=True)
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
        uvicorn.run("bzm_opl_gen.server:app", host=host, port=port,
                    reload=True, reload_dirs=[os.path.dirname(__file__)],
                    log_level="info")
    else:
        uvicorn.run(app, host=host, port=port, log_level="warning")
