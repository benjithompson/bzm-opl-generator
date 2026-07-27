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

import http.client
import io
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
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


class ManualFactsIn(BaseModel):
    harbor_id: str
    ship_id: str
    func_ids: list = ["performance"]


@app.post("/api/facts/manual")
def manual_facts(m: ManualFactsIn):
    """Facts from the three values BlazeMeter shows on the agent, with no API
    key involved -- the case where you are producing manifests for a customer's
    cluster and have access to neither their account nor their cluster.

    Deliberately not behind _client(): requiring a key here would defeat the
    point. It reads nothing and writes nothing; it only fills in the shape
    `gather` would have returned.

    The ids are not validated. There is nothing here to validate them against,
    and a guess at their format would reject input that is correct.
    """
    facts = facts_mod.manual(m.harbor_id, m.ship_id, func_ids=m.func_ids)
    return {"facts": facts,
            # Carried rather than left for the caller to notice -- see
            # facts.gui_images_incomplete for what it means.
            "gui_images_incomplete": facts_mod.gui_images_incomplete(facts)}


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
    names = gen_mod.preview_order(files)
    return {"files": [{"name": n, "content": files[n]} for n in names]}


@app.post("/api/generate/zip")
def generate_zip(g: GenerateIn):
    files = _generate(g)
    buf = io.BytesIO()
    # Names may carry directories (the helm format emits a chart), which zip
    # stores as-is -- the slash is the path separator in the archive too.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in gen_mod.preview_order(files):
            info = zipfile.ZipInfo(f"bzm-opl/{name}")
            if name.endswith(".sh"):
                info.external_attr = 0o755 << 16
            z.writestr(info, files[name])
    ns = g.options.get("namespace", "blazemeter")
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="bzm-opl-{ns}.zip"'})


# What each unreadable cluster means, in the user's terms -- a reason without a
# way forward is the dead panel the watch list must never become.
def _sv_read_message(read):
    """The sentence shown for an unreadable cluster.

    `.get`, not `[]`, because livetest owns the set of reasons -- a fifth one
    should degrade to the raw detail, not 500 the one endpoint whose contract is
    that it never returns a bare error.
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
        "service in BlazeMeter first; this list refreshes on the poll.",
}


@app.get("/api/sv-mocks")
def sv_mocks(namespace: str, sv_subdomain: Optional[str] = None):
    """What is deployed in `namespace`, and the host each one answers at.

    This rides the UI's existing status poll: the agent reports idle whether or
    not its virtual services ever became reachable, so a deploy stalled at
    WAITING_FOR_DOMAIN looks identical to a healthy one in the watch panel.

    Reading a cluster is the only thing this server ever does beyond the
    BlazeMeter API, and it is optional: the UI is API-only by design and most
    people running it have no kubecontext, so an unreadable cluster comes back
    200 saying which of the four reasons applied, never an HTTP error the
    browser can only print in red. A poll that 401s or 500s every ten seconds
    either fills the console or gets swallowed by the caller's catch and
    silently reads as "nothing deployed", which is the one answer this must
    never fake.
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


# -- does the published endpoint answer? --------------------------------------
# The list above is pods, and a Running pod says nothing about whether anything
# routes to it: crane's nginx Ingress backend names port 8080 while the Service
# it created exposes port 80, so a strict controller builds no route and the
# published endpoint 503s while the mock serves happily inside the cluster.
# That 503 is the finding, not a failure of the check.

SV_CHECK_OK = "ok"
SV_CHECK_DNS = "dns"
SV_CHECK_REFUSED = "refused"
SV_CHECK_TLS = "tls"
SV_CHECK_TIMEOUT = "timeout"
SV_CHECK_ERROR = "error"

# Deliberately under the watch panel's 10s poll: this runs inside that panel, so
# a deadline longer than the interval would leave answers landing against a list
# that has already been replaced, and a hung endpoint holding a worker thread
# across two ticks. Nothing legitimate needs longer -- a controller that routes
# answers in milliseconds, and the 503 this exists to catch is written by the
# controller itself without ever reaching a backend. 5s leaves room for one slow
# DNS lookup and still returns well inside the tick.
SV_CHECK_TIMEOUT_S = 5

# What BlazeMeter publishes is <name>-<port>-<namespace>.<domain>, plus an
# optional port. Anything else is refused rather than fetched: this string
# arrives from the browser, and a URL carrying a path, credentials or a second
# word would turn a reachability probe into a general-purpose fetcher aimed by
# whatever loaded the page.
_SV_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d+)?$")

SV_CHECK_MESSAGES = {
    SV_CHECK_DNS:
        "That host does not resolve from this machine. The wildcard domain has "
        "to point at the ingress controller before anything can reach the "
        "endpoint -- including BlazeMeter.",
    SV_CHECK_REFUSED:
        "The host resolves but nothing accepted a connection. What it resolves "
        "to is not the ingress controller, or the controller is not listening "
        "on this scheme's port.",
    # One message for the whole handshake, because the two causes look the same
    # from here and the raw reason (CERTIFICATE_VERIFY_FAILED vs
    # WRONG_VERSION_NUMBER) travels alongside as the detail.
    SV_CHECK_TLS:
        "Something answered but the TLS handshake failed: either the "
        "certificate served for that host is not one this machine trusts -- "
        "usual where the router serves the cluster's own CA -- or nothing "
        "there speaks TLS at all, in which case check over http.",
    SV_CHECK_TIMEOUT:
        f"No answer within {SV_CHECK_TIMEOUT_S}s. The connection is being "
        "accepted and never replied to, which is a network in between rather "
        "than the virtual service.",
}

# The one status code with a diagnosis attached, because on this endpoint it has
# exactly one cause and a command that fixes it.
SV_CHECK_503 = (
    "HTTP 503 -- the endpoint is published but nothing routes to it, while the "
    "mock pod itself is healthy. That is this cluster rejecting crane's Ingress: "
    "its backend names port 8080 where the Service crane created exposes port "
    "80. Run `bzm-opl-gen sv-expose` where you have cluster access to publish a "
    "Service+Ingress pair that does route.")


def _sv_check_reason(err):
    """Classify a probe that never got a status line, in the same terms as
    livetest._sv_read_reason: by inspecting what came back, because these four
    have four different fixes and "could not connect" has none."""
    e = getattr(err, "reason", err)      # URLError wraps; a read timeout does not
    if isinstance(e, ssl.SSLError):
        # CERTIFICATE_VERIFY_FAILED and the rest of the handshake failures.
        # First, because SSLError is itself an OSError like the two below.
        return SV_CHECK_TLS
    if isinstance(e, socket.gaierror):
        return SV_CHECK_DNS
    # Both names, not one: socket.timeout only became an alias of TimeoutError
    # in 3.10, and this package supports 3.9, where they are separate classes
    # and matching on either alone silently drops half the timeouts.
    if isinstance(e, (TimeoutError, socket.timeout)):
        return SV_CHECK_TIMEOUT
    if isinstance(e, ConnectionRefusedError):
        return SV_CHECK_REFUSED
    # Reset connections, a proxy that hung up, an http.client parse failure.
    # One bucket rather than a fifth guess, with the raw reason alongside.
    return SV_CHECK_ERROR


@app.get("/api/sv-check")
def sv_check(host: str, scheme: str = "http"):
    """Ask whether the endpoint a deployed virtual service publishes answers.

    `host` is the string /api/sv-mocks handed the panel, passed back rather than
    rebuilt here: what gets probed has to be what the row displays and what
    BlazeMeter advertises, or a green tick would be vouching for an address
    nobody was given.

    Answers 200 whatever happened, for the same reason the cluster reads do: an
    endpoint that does not answer is the expected finding of this button, not a
    broken request. The two 4xx cases are inputs that are not an endpoint at all.

    Declared `def`, not `async def`, so FastAPI runs it on a worker thread --
    a probe waiting out its deadline must not stop the status poll behind it.
    """
    if scheme not in ("http", "https"):
        raise HTTPException(400, f"scheme must be http or https, not {scheme!r}")
    if not _SV_HOST_RE.match(host or ""):
        raise HTTPException(400, f"not an endpoint host: {host!r}")
    url = f"{scheme}://{host}/"
    try:
        # Redirects are followed, as they would be by the browser this is
        # standing in for -- so a router configured to redirect http to https is
        # reported by what the https leg said, including its certificate. The
        # 503 this exists to catch is written by the controller directly and
        # never redirects, so the diagnosis below is unaffected either way.
        with urllib.request.urlopen(url, timeout=SV_CHECK_TIMEOUT_S) as r:
            code, detail = r.status, ""
    except urllib.error.HTTPError as e:
        # Not an error here: a status line means something routed to this host
        # and replied, which is the whole question. 503 included -- especially.
        code, detail = e.code, str(e)
    except (OSError, http.client.HTTPException) as e:
        # OSError covers URLError and everything it wraps, plus a bare
        # TimeoutError from a read that stalls after the connect succeeded.
        status = _sv_check_reason(e)
        detail = str(e) or repr(e)
        return {"status": status, "code": None, "url": url, "detail": detail,
                "message": SV_CHECK_MESSAGES.get(status)
                or f"The endpoint could not be reached: {detail}"}
    return {"status": SV_CHECK_OK, "code": code, "url": url, "detail": detail,
            "message": SV_CHECK_503 if code == 503
            else f"HTTP {code} -- the endpoint answered."}


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

    `changes_images` marks the ones worth offering where a funcId's only job is
    to pick images -- the manual-entry form. functionalApi and performance both
    mean "the taurus engine", so offering both there is a choice that cannot
    change the output. Served rather than filtered in TypeScript for the same
    reason the list itself is: a copy in the frontend is how the vocabulary and
    the thing it describes drift apart.
    """
    distinct = set(facts_mod.image_distinct_funcs())
    return [{"id": f, "label": FUNC_ID_LABELS.get(f, f),
             "changes_images": f in distinct}
            for f in facts_mod.CATEGORY_BY_FUNC]


# The features a bundle can be configured for. The UI shows one feature's
# options at a time and builds its selector from this list, so a feature becomes
# offered by being added here -- the frontend enumerates nothing. The other half
# of adding one is tagging whichever option groups it owns with its `id`; a
# feature no group is tagged with is still selectable and shows the groups that
# apply to any deployment (registry, proxy, CA trust, scheduling).
#
# `func_ids` is how a location's funcIds pick the feature to start on. Locations
# carry funcIds no feature claims (tdm, dataPublisher, delphix,
# secretsPrivateVault); those are no signal rather than an error, which is what
# lets this list model less than the account does.
#
# `namespace` is a suggestion, applied only while the field still holds one --
# a namespace per feature is what keeps redeploying one agent from taking the
# other's pods down with it, and typing over it has to win.
FEATURES = [
    {
        "id": "performance",
        "label": "Performance & functional testing",
        "hint": "load and functional tests -- engines started on demand",
        "namespace": "blazemeter",
        # Every non-SV funcId the facts layer models: the recorder and the
        # functional suites all run on this agent, so they configure as it.
        "func_ids": ["performance", "functionalApi", "functionalGui",
                     "proxyRecorder"],
    },
    {
        "id": "sv",
        "label": "Service virtualization",
        "hint": "virtual services / mocks -- needs an ingress",
        "namespace": "blazemeter-sv",
        # Which funcIds mean SV is generate.SV_FUNC_IDS', the same list
        # /api/sv-constants serves and _sv_cfg validates against.
        "func_ids": list(gen_mod.SV_FUNC_IDS),
    },
]


@app.get("/api/features")
def features():
    """The features the configure step can be pointed at, in selector order."""
    return FEATURES


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
