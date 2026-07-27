"""Local web UI backend: a thin JSON API over api.py / facts.py / generate.py.

Run with `bzm-opl-gen ui` (requires `pip install bzm-opl-gen[ui]`). Serves the
prebuilt SPA from ui_dist/ and the API under /api. Single-user by design: it
holds one BzmClient in process memory, so reaching the page is equivalent to
holding the API key. The secret itself never leaves this machine and is never
echoed back to the browser.

Binds 127.0.0.1 by default for that reason. `--host` widens it for the case
where the machine running this is not the machine you are sitting at, and warns
on the way out; an SSH tunnel to the default bind is the safer shape and costs
nothing extra.
"""

import io
import json
import os
import shlex
import time
import zipfile
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import api, facts as facts_mod, generate as gen_mod, livetest

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


class SvExposeIn(BaseModel):
    """Everything sv-expose needs and nothing else -- the namespace to read,
    plus the three fields generate.sv_publish_cfg resolves."""
    namespace: str = "blazemeter"
    sv_subdomain: Optional[str] = None
    sv_tls_secret: Optional[str] = None
    sv_ingress_class: Optional[str] = None


# What each unreadable cluster means, in the user's terms. The UI pairs these
# with the CLI equivalent below; a reason without a way forward is the dead
# panel this endpoint exists to avoid.
def _sv_read_message(read):
    """The sentence shown for an unreadable cluster.

    Shared by both endpoints that do this read: they answer the same question
    about the same failure, and a message that differed between them would be
    the UI contradicting itself. `.get`, not `[]`, because livetest owns the set
    of reasons -- a fifth one should degrade to the raw detail, not 500 the one
    endpoint whose contract is that it never returns a bare error.
    """
    return SV_READ_MESSAGES.get(read.status, read.detail)


SV_READ_MESSAGES = {
    livetest.SV_READ_NO_CLI:
        "No kubectl or oc on this machine, so the namespace cannot be read "
        "from here. Nothing else in this tool needs one.",
    # One message for several causes -- no kubeconfig, no current context, a
    # server that refused, one that never answered, output that would not
    # parse. The way forward is the same for all of them, and the raw reason
    # travels alongside as `detail`; what it must not do is name only one of
    # them, which reads as false to anyone whose context is fine but slow.
    livetest.SV_READ_NO_CONTEXT:
        "kubectl/oc is installed, but no cluster could be read -- no context "
        "is configured, or the one that is did not answer.",
    livetest.SV_READ_DENIED:
        "The cluster refused the read -- this context is not allowed to list "
        "pods in that namespace.",
    livetest.SV_READ_NO_MOCKS:
        "That namespace holds no virtual-service pods. Deploy the virtual "
        "service in BlazeMeter first, then read it again.",
}


def _sv_expose_command(x: SvExposeIn):
    """The `sv-expose` invocation equivalent to this request, to run wherever
    the user does have cluster access.

    `--manifests ''` on purpose: the flag defaults to `out/` and the CLI reads
    profile.json from it, which is a file a browser user need never have
    downloaded. Every option is on the command line instead, so the suggestion
    runs from any directory.
    """
    cmd = ["bzm-opl-gen", "sv-expose", "--manifests", "",
           "--namespace", x.namespace]
    for flag, value in (("--sv-subdomain", x.sv_subdomain),
                        ("--sv-tls-secret", x.sv_tls_secret),
                        ("--ingress-class", x.sv_ingress_class)):
        if value:
            cmd += [flag, value]
    # shlex.join quotes what needs it and nothing else -- quoting per element
    # on the way in means a flag added without it emits a command that will not
    # run, and nothing here would catch that.
    return shlex.join(cmd)


@app.post("/api/sv-expose")
def sv_expose_render(x: SvExposeIn):
    """Render the Service+Ingress pair for the virtual services deployed in a
    namespace -- the `sv-expose` command, from the browser.

    Reading a cluster is the only thing this server ever does beyond the
    BlazeMeter API, and it is optional: an unreadable cluster comes back 200
    with which of the four reasons applied and the command to run elsewhere,
    never an HTTP error the browser can only print in red. The single 4xx is a
    request that could not be rendered even with a cluster in reach.
    """
    publish_opts = {"sv_subdomain": x.sv_subdomain,
                    "sv_tls_secret": x.sv_tls_secret,
                    "sv_ingress_class": x.sv_ingress_class}
    try:
        publish = gen_mod.sv_publish_cfg(publish_opts)
    except ValueError as e:
        raise HTTPException(400, str(e))
    command = _sv_expose_command(x)
    read = livetest.sv_read(x.namespace)
    if read.status != livetest.SV_READ_OK:
        return {"status": read.status, "mocks": [], "files": [],
                "message": _sv_read_message(read),
                "detail": read.detail, "command": command}
    return {
        "status": read.status,
        # host alongside each mock so the UI never rebuilds it: the same string
        # the Ingress below routes, and the one a person is told to try.
        "mocks": [dict(m, host=gen_mod.sv_endpoint_host(
            m["name"], m["port"], x.namespace, publish.subdomain))
            for m in read.mocks],
        # Same shape as /api/generate, so the UI previews it in the same pane.
        "files": [{"name": gen_mod.SV_EXPOSE_FILE,
                   "content": gen_mod.sv_expose(read.mocks, x.namespace, publish)}],
        "message": (f"{len(read.mocks)} virtual service(s) in {x.namespace}: "
                    + ", ".join(f"{m['name']}:{m['port']}" for m in read.mocks)),
        "detail": f"apply with: kubectl apply -n {x.namespace} -f {gen_mod.SV_EXPOSE_FILE}",
        "command": command,
    }


@app.get("/api/sv-mocks")
def sv_mocks(namespace: str, sv_subdomain: Optional[str] = None):
    """What is deployed in `namespace`, and the host each one answers at.

    The same cluster read as /api/sv-expose without the rendering, because this
    one rides the UI's existing status poll: the agent reports idle whether or
    not its virtual services ever became reachable, so a deploy stalled at
    WAITING_FOR_DOMAIN looks identical to a healthy one in the watch panel.

    Always 200, for the same reason as /api/sv-expose and one more: a poll that
    401s or 500s every ten seconds either fills the console or gets swallowed by
    the caller's catch and silently reads as "nothing deployed", which is the
    one answer this must never fake.
    """
    read = livetest.sv_read(namespace)
    return {
        "status": read.status,
        "mocks": [{"name": m["name"], "port": m["port"],
                   "host": gen_mod.sv_endpoint_host(
                       m["name"], m["port"], namespace, sv_subdomain)}
                  for m in read.mocks],
        "message": _sv_read_message(read),
    }


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


# Display names only -- the vocabulary itself is facts.CATEGORY_BY_FUNC, which
# already has to list every funcId to pick the right images. A funcId missing
# from here is served under its raw name rather than dropped.
FUNC_ID_LABELS = {
    "performance": "Performance",
    "functionalApi": "Functional API",
    "functionalGui": "Functional GUI",
    "mockServices": "Mock Services",
    "proxyRecorder": "Proxy Recorder",
}


@app.get("/api/func-ids")
def func_ids():
    """The funcIds a location can be created with, in declaration order.

    Served for the same reason as /api/sv-constants: the create-location form
    used to hold its own list in TypeScript, and whatever was missing from that
    copy could not be selected from the UI at all. Derived from the facts layer so adding a funcId there -- which is
    already required for its images to be selected -- is the only edit needed.
    """
    return [{"id": f, "label": FUNC_ID_LABELS.get(f, f)}
            for f in facts_mod.CATEGORY_BY_FUNC]


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
            "ingress_types": list(gen_mod.SV_INGRESS_TYPES),
            # What each backend publishes, so the UI can name the Role the
            # bundle grants without keeping its own copy of SV_INGRESS_BACKENDS
            # -- which is mechanical, unlike the prose around it. Only the
            # three fields the UI renders; via_ingress_class is doctor's, and
            # serving it here would be a field nothing reads.
            "backends": {name: {"group": b.group,
                                "resources": list(b.resources),
                                "creates": b.creates}
                         for name, b in gen_mod.SV_INGRESS_BACKENDS.items()}}


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
