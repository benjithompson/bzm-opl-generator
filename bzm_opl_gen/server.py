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

# Nothing of core's is re-exported here, not even as a convenience: an alias is
# a second name for one value, it does not follow when the value is replaced,
# and reaching for it is how a caller ends up describing something core is no
# longer serving. That is not hypothetical -- FEATURES was aliased here for one
# commit, and a test patched this name while asserting against the list core
# was still handing out.


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


@app.get("/api/key")
def key_status():
    """Whether this server still holds a usable key, and whose.

    The connection lives in the process, not in the browser, so a refresh has
    never actually disconnected anything -- the page just forgot. This is how it
    remembers. The user call is made rather than assumed: a key that was revoked
    or expired since it was accepted should read as disconnected here, not fail
    later on whichever call happens to be first.
    """
    client = _state["client"]
    if client is None:
        return {"connected": False}
    try:
        user = core.user(client)
    except core.CoreError:
        # It was accepted once and is not working now. Drop it rather than
        # leaving a client behind that every later call would fail on.
        _state["client"] = _state["key_id"] = None
        return {"connected": False}
    return {
        "connected": True,
        "user": {"email": user.get("email"), "display_name": user.get("displayName")},
        "default_account_id": (user.get("defaultProject") or {}).get("accountId"),
        "key_id": _state["key_id"],
    }


@app.delete("/api/key")
def key_clear():
    """Forget the key this server is holding.

    Only what is in memory: a key saved with `save: true` is at
    core.SAVED_KEY_PATH and stays there, because deleting a file the user asked
    to keep is not what a Disconnect button on a web page should mean. Detect
    still lists it, so reconnecting is one click.
    """
    _state["client"] = _state["key_id"] = None
    return {"connected": False}


@app.post("/api/key")
def key_set(k: KeyIn):
    if k.path:
        path = os.path.expanduser(k.path)
        if not os.path.isfile(path):
            raise HTTPException(400, f"no such file: {path}")
    elif k.id and k.secret:
        os.makedirs(core.CONFIG_DIR, exist_ok=True)
        path = (core.SAVED_KEY_PATH if k.save
                else os.path.join(core.CONFIG_DIR, ".session-key.json"))
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
    # Scope checked before the client is resolved, and the order is the whole
    # reason this is two calls: a request naming neither scope is malformed
    # with or without a key, so answering 401 would send the caller off to
    # configure one and then refuse them anyway.
    _answer(core.require_location_scope, account_id, workspace_id)
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
    """Create the agent, and issue its credential with it.

    The one place in this server that mints as a matter of course, and #64's
    reason the rest of it no longer does: the token is captured at the single
    moment it is free, when the ship is new and has no previous credential for a
    fetch to invalidate. `core.create_ship` deliberately does not fetch -- for an
    existing ship that would rotate a live agent's token on an action whose name
    says nothing about credentials -- and the reservation does not apply here, so
    this mirrors what `bzm-opl-gen create-ship` has always done.

    A refusal is reported *with* the ship rather than instead of it. Some accounts
    allow the token endpoint only from BlazeMeter's own gateway; answering 502
    here would leave the created agent's id nowhere but a browser console, and the
    next click makes a second agent in the same location. Same ordering, same
    reason, as the CLI printing the ids before it fetches.
    """
    client = _client()
    ship = _answer(core.create_ship, client, s.harbor_id, s.name)
    token, refused = None, None
    try:
        token = core.fetch_ship_token(client, s.harbor_id, ship["id"])
    except core.CoreError as e:
        # The sentence core wrote, which names the ship and says a token read off
        # the agent in BlazeMeter's own UI works just as well. Not _answer'd:
        # this is the one refusal here that must not become the response.
        refused = str(e)
    return {"ship": ship, "auth_token": token, "token_error": refused}


class FuncIdIn(BaseModel):
    harbor_id: str
    func_id: str


@app.post("/api/locations/func-id", description=core.add_func_id.__doc__)
def location_add_func_id(f: FuncIdIn):
    return _answer(core.add_func_id, _client(), f.harbor_id, f.func_id)


class TokenIn(BaseModel):
    harbor_id: str
    ship_id: str


@app.post("/api/ships/token")
def ship_issue_token(t: TokenIn):
    """Issue a NEW AUTH_TOKEN for an existing agent.

    Deliberately its own route rather than a flag on generate: rotating as a
    side effect of asking for files is what #64 took out, and the page that
    calls this has already said, in the words core wrote, that the agent
    currently running on the old credential starts answering 404 until the
    bundle is re-applied. A route whose whole name is the action cannot be
    reached by accident.

    The token comes back in the body because there is nowhere else for it to
    go: BlazeMeter will not show it again, and the caller's next move is to put
    it in the Secret. Nothing here writes it down -- same as ship_create.
    """
    return {"auth_token": _answer(core.issue_auth_token, _client(),
                                  t.harbor_id, t.ship_id)}


@app.get("/api/facts")
def get_facts(harbor_id: str):
    return _answer(core.gather_facts, _client(), harbor_id)


class ManualFactsIn(BaseModel):
    harbor_id: str
    ship_id: str
    func_ids: list = ["performance"]


@app.post("/api/facts/manual", description=core.manual_facts.__doc__)
def manual_facts(m: ManualFactsIn):
    """Deliberately not behind _client(): requiring a key here would defeat the
    point of the manual path -- see core.manual_facts."""
    return _answer(core.manual_facts, m.harbor_id, m.ship_id,
                   func_ids=m.func_ids)


@app.get("/api/status", description=core.agent_status.__doc__)
def agent_status(harbor_id: str, ship_id: str):
    return _answer(core.agent_status, _client(), harbor_id, ship_id)


# -- generation ---------------------------------------------------------------

class GenerateIn(BaseModel):
    facts: dict
    options: dict = {}
    # Off, and named for what the endpoint does rather than for its HTTP verb:
    # asking BlazeMeter for an AUTH_TOKEN *issues* one, and the previous one dies
    # with the request. `fetch_token: true` was the default here, so a download
    # taken to read the manifests revoked the credential of the agent already
    # running (#64). A page still posting that field is not refused -- pydantic
    # ignores it, which is what a browser holding the previous bundle needs, and
    # ignoring it errs towards not minting.
    rotate_token: bool = False
    # Optional here, required on SaveIn. The preview and the zip take it so they
    # can answer the question the page is actually asking -- "what will the
    # bundle I am about to save carry?" -- because a folder that already holds
    # this ship's bundle supplies its own token. Without it the preview could
    # only ever say `placeholder`, and a page reporting "fill it in before
    # applying" over a folder whose token a save was about to keep invites a
    # rotation nothing needed, which is the harm this whole change is about.
    # Read only: nothing is written unless the route is the save one.
    out_dir: str | None = None


class SaveIn(GenerateIn):
    out_dir: str


def _generate(g: GenerateIn, out_dir=None):
    """The bundle, and which of four ways its AUTH_TOKEN arrived.

    Resolved here rather than left to `generate_bundle` alone, because the branch
    is the answer: the four have different consequences for an agent that is
    already running, and the one that revokes its credential used to happen in
    silence. Resolving and then generating is core's own documented pairing --
    the resolved token wins outright the second time round, so nothing is issued
    twice.

    `out_dir` is the directory a bundle is about to be written to, and it is read
    (never written) for the token its predecessor holds. The save route passes
    the one it is about to write; the *preview* passes whatever folder the page
    has typed, so it can answer for the bundle that would be saved rather than
    for a hypothetical one with no history. The zip deliberately does not: it
    lands in a browser's downloads directory, and borrowing a token from an
    unrelated folder on the server would be a different bundle than the one
    asked for. The parameter wins over the model's field, because by then the
    save route has expanded `~`.
    """
    out_dir = out_dir or g.out_dir
    opts = dict(g.options)
    source = _answer(core.resolve_auth_token, g.facts, opts,
                     client=_state["client"], rotate=g.rotate_token,
                     out_dir=out_dir)
    files = _answer(core.generate_bundle, g.facts, opts,
                    client=_state["client"], rotate_token=g.rotate_token,
                    out_dir=out_dir)
    return files, source


# The zip route answers with bytes, so the one thing a download must not lose
# travels beside the filename in the headers. Not in the body: a zip wrapped in a
# JSON envelope is not a zip, and the browser saves whatever it is handed.
TOKEN_BRANCH_HEADER = "X-Bzm-Token-Branch"
TOKEN_MESSAGE_HEADER = "X-Bzm-Token-Message"


def _wire_safe(headers):
    """Header values in what an HTTP header can actually hold.

    latin-1 by the spec, and starlette raises encoding anything else -- which
    would lose the download itself rather than the header on it. Both values this
    route sets are a person's typing away from that: the filename carries the
    namespace and so does the token message. A namespace is an RFC 1123 label on
    any cluster that would take the bundle, so nothing worth keeping is replaced
    here; the zip is worth keeping.
    """
    return {k: v.encode("latin-1", "replace").decode("latin-1")
            for k, v in headers.items()}


def _token_headers(source):
    """The same report, on one line each -- which is what a header is, and the
    recovery hint is three lines with a kubectl in it."""
    return {TOKEN_BRANCH_HEADER: source.branch,
            TOKEN_MESSAGE_HEADER: " ".join(source.message.split())}


@app.post("/api/generate")
def generate_preview(g: GenerateIn):
    files, source = _generate(g)
    return {"files": [{"name": n, "content": files[n]}
                      for n in core.preview_order(files)],
            "token": source._asdict()}


@app.post("/api/generate/zip")
def generate_zip(g: GenerateIn):
    files, source = _generate(g)
    name = core.zip_filename(g.options)
    return Response(core.zip_bundle(files), media_type="application/zip",
                    headers=_wire_safe({
                        "Content-Disposition": f'attachment; filename="{name}"',
                        **_token_headers(source)}))


@app.post("/api/generate/save")
def generate_save(g: SaveIn):
    """Write the bundle to a directory on this machine, not down to the browser.

    The zip is for handing a bundle to somebody; this is for continuing with it
    here -- the directory it writes (profile.json included) is the same shape
    `opl_bundle generate` produces and `livetest` consumes, so an MCP session
    or a shell picks up exactly where the UI left off, with the filesystem as
    the shared state.

    `~` is expanded here rather than in core: this path was typed by a person
    into a browser, and `~` is how people name their home directory. Core's
    callers pass paths a program chose, and core still refuses a relative one.

    The directory goes into the generation as well as being written to, which is
    what reaches core's reuse branch: saving again into a folder that already
    holds this ship's bundle keeps that bundle's token instead of issuing one, so
    re-rendering with an option changed leaves the agent deployed from the last
    save working.
    """
    out_dir = os.path.expanduser(g.out_dir)
    # Before the generation, not at the write: with rotate_token set, resolving
    # first meant a credential was issued and *then* thrown away by the path
    # refusal -- which has already taken the running agent down, for a request
    # that produced nothing. write_bundle checks it again, which is the point of
    # require_absolute_out_dir being its own function: one copy of the rule, two
    # moments. The MCP's generate orders it the same way.
    _answer(core.require_absolute_out_dir, out_dir)
    files, source = _generate(g, out_dir=out_dir)
    written = _answer(core.write_bundle, files, out_dir)
    return {"out_dir": out_dir, "files": written,
            "token": source._asdict()}


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


@app.post("/api/preflight", description=core.preflight.__doc__)
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

@app.get("/api/sv-mocks", description=core.sv_mocks.__doc__)
def sv_mocks(namespace: str, sv_subdomain: Optional[str] = None):
    """What is deployed in `namespace`, and the host each one answers at.

    Answers 200 whatever the cluster did, including "there is no kubectl here"
    -- this rides the UI's 10s status poll, and a route that 4xx'd every tick
    either fills the console or gets swallowed by the caller's catch and reads
    as "nothing deployed", which is the one answer it must never fake.
    """
    return _answer(core.sv_mocks, namespace, sv_subdomain)


@app.get("/api/sv-check", description=core.sv_check.__doc__)
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
# Each of these routes takes its /api/docs description from core's docstring
# rather than restating it. The prose is about what the answer means, which is
# core's to say; a second copy here is what goes stale, and /api/docs is
# exactly where nobody would notice.

@app.get("/api/option-defaults", description=core.option_defaults.__doc__)
def option_defaults():
    return core.option_defaults()


@app.get("/api/option-docs", description=core.option_docs.__doc__)
def option_docs():
    return core.option_docs()


@app.get("/api/func-ids", description=core.func_ids.__doc__)
def func_ids():
    return core.func_ids()


@app.get("/api/features", description=core.features.__doc__)
def features():
    return core.features()


@app.get("/api/sv-constants", description=core.sv_constants.__doc__)
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
# the page is already reachable, and reading manifests is not the expensive
# mistake behind it. Two things are -- creating locations and agents in a real
# account, and asking for a new AUTH_TOKEN, which revokes the one a running agent
# holds (crane logs 404 on /versions and the pod sits at 0/1, which reads like a
# deleted ship). Neither happens by accident on this page any more; both are one
# click for whoever reaches it.
EXPOSED_WARNING = """\
!! bzm-opl-gen ui is bound to {host}, so it is reachable from outside this
!! machine. Anyone who reaches it can act as your BlazeMeter API key: create
!! locations and agents, and issue a new AUTH_TOKEN, which REVOKES the one
!! whatever agent is already running for that ship is using.
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
