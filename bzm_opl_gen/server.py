"""Local web UI backend: HTTP over core.py.

Run with `bzm-opl-gen ui` (requires `pip install bzm-opl-gen[ui]`). Serves the
prebuilt SPA from ui_dist/ and the API under /api. Single-user by design: it
holds one BzmClient in process memory, so reaching the page is equivalent to
holding the API key; the secret is never echoed back to the browser. Binds
127.0.0.1 by default for that reason.

This module is the transport: routes, request bodies, the zip's headers, where
a pasted key and a minted token live for a browser session, the TTL cache, and
how the process is bound. Every decision about OPL is core's. The
`CoreError` exception handler is the only place a core refusal becomes an HTTP
status.
"""

import functools
import json
import os
import time
from typing import Annotated, Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, BeforeValidator

from . import api, core, footprint, ui_build

app = FastAPI(title="bzm-opl-gen", docs_url="/api/docs", openapi_url="/api/openapi.json")

@app.exception_handler(core.CoreError)
def _answer(request: Request, e: core.CoreError):
    """core's refusal, as the status it carries and FastAPI's usual body."""
    return JSONResponse(status_code=e.status, content={"detail": str(e)})

# The single-user seam is these three, all process-global: the client, the
# tokens this process minted, and the TTL cache. Two tabs are one session.
# Nothing of core's is re-exported here: an alias does not follow a monkeypatch.
_state = {"client": None, "key_id": None}

# AUTH_TOKENs this process minted, by ship id, in memory only. BlazeMeter shows
# a token once, so without this a browser refresh loses the only copy of one
# this app just created. Keyed by ship so a token can only be found under the
# ship it belongs to. A restart forgets; the downloaded bundle is the durable copy.
_minted_tokens: dict[str, str] = {}

def _client():
    """The client this browser session acts as; 401 when none is connected."""
    if _state["client"] is None:
        raise HTTPException(401, "no API key configured -- POST /api/key first")
    return _state["client"]

Client = Annotated[api.BzmClient, Depends(_client)]

# -- the account tree, remembered for a minute ---------------------------------
# A page load is several BlazeMeter round trips (~2.5s), and reloading is what
# you do all day while configuring. Here rather than in core: this process's own
# writes are the only changes it can miss, and they clear it; the long-lived MCP
# server has no such guarantee.
CACHE_TTL_S = 60
_cache: dict = {}

def _cached(key, fn, *args, **kw):
    """`fn(*args)`, remembered under `key` for CACHE_TTL_S."""
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    value = fn(*args, **kw)
    _cache[key] = (time.monotonic() + CACHE_TTL_S, value)
    return value

def _forget():
    """Drop the whole cache. Per-key invalidation would go wrong quietly."""
    _cache.clear()

def _writes(fn):
    """A route that changes the customer's account: drops the cache after it,
    including when the write half-succeeded."""
    @functools.wraps(fn)
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        finally:
            _forget()
    return wrapped

def _typed(value):
    """A form field the user left alone (an untouched number input posts ""),
    as "not given"."""
    return None if isinstance(value, str) and not value.strip() else value

# The same rule as a type, so an optional field cannot be declared without it.
Blank = Annotated[Any, BeforeValidator(_typed)]

# -- key management -----------------------------------------------------------

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
    """Whether this server holds a usable key, and whose. The user call is made
    rather than assumed, so a key revoked since reads as disconnected."""
    client = _state["client"]
    if client is None:
        return {"connected": False}
    try:
        who = core.whoami(client)
    except core.CoreError:
        _state["client"] = _state["key_id"] = None
        return {"connected": False}
    return {"connected": True,
            "user": {"email": who["email"], "display_name": who["display_name"]},
            "default_account_id": who["default_account_id"],
            "key_id": _state["key_id"]}

@app.delete("/api/key")
def key_clear():
    """Forget the key in memory, and the tokens minted with it. A key saved to
    core.SAVED_KEY_PATH stays on disk."""
    _state["client"] = _state["key_id"] = None
    _minted_tokens.clear()
    _forget()
    return {"connected": False}

@app.post("/api/key")
def key_set(k: KeyIn):
    if k.path:
        path = os.path.expanduser(k.path)
        if not os.path.isfile(path):
            raise HTTPException(400, f"no such file: {path}")
        client = core.client_from_key(path)
    elif k.id and k.secret:
        if k.save:
            # Written only because the user asked to keep it.
            os.makedirs(core.CONFIG_DIR, exist_ok=True)
            with open(core.SAVED_KEY_PATH, "w") as fh:
                json.dump({"id": k.id, "secret": k.secret}, fh)
            os.chmod(core.SAVED_KEY_PATH, 0o600)
        client = core.client_from_key(key_id=k.id, secret=k.secret)
    else:
        raise HTTPException(400, "provide path, or id+secret")
    who = core.whoami(client)
    _state["client"] = client
    _forget()
    _state["key_id"] = k.id or client.key_id
    return {"user": {"email": who["email"], "display_name": who["display_name"]},
            "default_account_id": who["default_account_id"],
            "key_id": _state["key_id"],
            "saved": bool(k.id and k.save)}

# -- account tree -------------------------------------------------------------

@app.post("/api/refresh")
def refresh():
    """Forget what was read from BlazeMeter, so the next read is a real one.

    Not `_writes`: nothing reaches the account. What to re-read is the caller's.
    """
    _forget()

@app.get("/api/accounts")
def accounts(client: Client):
    return _cached("accounts", core.accounts, client)

@app.get("/api/workspaces")
def workspaces(account_id: int, client: Client):
    return _cached(f"workspaces:{account_id}", core.workspaces, client, account_id)

@app.get("/api/locations")
def locations(account_id: Optional[int] = None, workspace_id: Optional[int] = None):
    # Scope before credential: a request naming neither is malformed either way.
    core.require_location_scope(account_id, workspace_id)
    return _cached(f"locations:{account_id}:{workspace_id}",
                   core.locations, _client(), account_id, workspace_id)

class LocationIn(BaseModel):
    name: str
    account_id: int
    workspace_id: int
    func_ids: list[str] = list(api.DEFAULT_FUNC_IDS)
    slots: int = 1
    threads_per_engine: int = footprint.DEFAULT_THREADS_PER_ENGINE

@app.post("/api/locations")
@_writes
def location_create(loc: LocationIn, client: Client):
    made = core.create_location(client, loc.name, loc.account_id,
                                loc.workspace_id, func_ids=loc.func_ids,
                                slots=loc.slots,
                                threads_per_engine=loc.threads_per_engine)
    # The location document with the warning beside it (null when runnable).
    return {**made["location"], "warning": made["warning"]}

class ShipIn(BaseModel):
    harbor_id: str
    name: str

@app.post("/api/ships")
@_writes
def ship_create(s: ShipIn, client: Client):
    """Create the agent and issue its credential with it (free for a new agent).

    A refused token endpoint is reported in `token_error` beside the ship, so
    the new agent's id is never lost.
    """
    made = core.create_agent(client, s.harbor_id, s.name)
    if made["auth_token"] is not None:
        _minted_tokens[made["ship"]["id"]] = made["auth_token"]
    return made

class LocationSettingsIn(BaseModel):
    harbor_id: str
    # A partial update: only what the browser sends is written.
    slots: Blank = None
    threads_per_engine: Blank = None
    override_cpu: Blank = None
    override_memory: Blank = None

@app.post("/api/locations/settings", description=core.update_location.__doc__)
@_writes
def location_update(s: LocationSettingsIn, client: Client):
    """Change the selected location's concurrency settings."""
    settings = {k: getattr(s, k) for k in core.LOCATION_SETTINGS}
    return core.update_location(client, s.harbor_id, **settings)

class TokenIn(BaseModel):
    harbor_id: str
    ship_id: str

@app.post("/api/ships/token")
@_writes
def ship_issue_token(t: TokenIn, client: Client):
    """Issue a NEW AUTH_TOKEN for an existing agent, revoking the old one.

    Its own route so it cannot happen as a side effect. The token is returned
    because BlazeMeter will not show it again, and remembered in memory.
    """
    token = core.fetch_ship_token(client, t.harbor_id, t.ship_id)
    _minted_tokens[t.ship_id] = token
    return {"auth_token": token}

@app.get("/api/ships/minted-token")
def ship_minted_token(ship_id: str):
    """The AUTH_TOKEN this process minted for `ship_id`, or null if it holds
    none (including after a restart). Reads memory only; mints nothing."""
    return {"auth_token": _minted_tokens.get(ship_id)}

@app.delete("/api/ships/minted-token")
def ship_forget_minted_token(ship_id: str):
    """Forget what was minted for `ship_id`, because a pasted token replaces it.

    The ship is the argument and never the token (query strings are logged).
    Not `_writes`: nothing in the account changed.
    """
    return {"forgotten": _minted_tokens.pop(ship_id, None) is not None}

@app.get("/api/facts")
def get_facts(harbor_id: str, client: Client):
    # Cached like the lists. Liveness is /api/status, which is never cached.
    return _cached(f"facts:{harbor_id}", core.gather_facts, client, harbor_id)

class ManualFactsIn(BaseModel):
    # "" is what a form sends for an empty box; a blank id becomes its marker.
    harbor_id: str = ""
    ship_id: str = ""
    func_ids: list = list(api.DEFAULT_FUNC_IDS)

@app.post("/api/facts/manual", description=core.manual_facts.__doc__)
def manual_facts(m: ManualFactsIn):
    """Needs no key: that is the point of the manual path."""
    return core.manual_facts(m.harbor_id, m.ship_id, func_ids=m.func_ids)

@app.get("/api/status", description=core.agent_status.__doc__)
def agent_status(harbor_id: str, ship_id: str, client: Client):
    return core.agent_status(client, harbor_id, ship_id)

@app.get("/api/capacity", description=core.account_capacity.__doc__)
def capacity(account_id: int, client: Client):
    return _cached(f"capacity:{account_id}", core.account_capacity, client,
                   account_id)

# -- generation ---------------------------------------------------------------

class GenerateIn(BaseModel):
    facts: dict
    options: dict = {}
    # Off by default: issuing a token revokes the running agent's. A stale
    # `fetch_token` field is ignored by pydantic, which errs towards not minting.
    rotate_token: bool = False
    # Read (never written) by the preview and the zip, so they report the token
    # the folder being saved to would supply.
    out_dir: str | None = None

class SaveIn(GenerateIn):
    out_dir: str

def _build(g: GenerateIn, out_dir=None, write=False):
    return core.build_bundle(g.facts, g.options, client=_state["client"],
                             rotate=g.rotate_token,
                             out_dir=out_dir or g.out_dir, write=write)

# The zip answers with bytes, so the token report travels in headers.
TOKEN_BRANCH_HEADER = "X-Bzm-Token-Branch"
TOKEN_MESSAGE_HEADER = "X-Bzm-Token-Message"

def _wire_safe(headers):
    """Header values in latin-1, which is all a header can hold; starlette
    raises on anything else and the download would be lost."""
    return {k: v.encode("latin-1", "replace").decode("latin-1")
            for k, v in headers.items()}

def _token_headers(source):
    """The token report on one line per header."""
    return {TOKEN_BRANCH_HEADER: source.branch,
            TOKEN_MESSAGE_HEADER: " ".join(source.message.split())}

@app.post("/api/generate")
def generate_preview(g: GenerateIn):
    built = _build(g)
    return {"files": [{"name": n, "content": built.files[n]}
                      for n in core.preview_order(built.files)],
            "token": built.token._asdict()}

@app.post("/api/generate/zip")
def generate_zip(g: GenerateIn):
    built = _build(g)
    stem = core.zip_stem(g.options)
    return Response(core.zip_bundle(built.files, stem),
                    media_type="application/zip",
                    headers=_wire_safe({
                        "Content-Disposition":
                            f'attachment; filename="{core.zip_filename(g.options)}"',
                        **_token_headers(built.token)}))

@app.post("/api/generate/save")
def generate_save(g: SaveIn):
    """Write the bundle to a directory on this machine.

    No caller in the page; kept as an HTTP capability for other clients. The
    directory is the same shape `opl_bundle generate` and `livetest` use, and
    saving again into this ship's bundle keeps its token. `~` is expanded.
    """
    out_dir = os.path.expanduser(g.out_dir)
    built = _build(g, out_dir=out_dir, write=True)
    return {"out_dir": out_dir, "files": built.written,
            "token": built.token._asdict()}

# -- planning -----------------------------------------------------------------

class SizingIn(BaseModel):
    """One functionality being sized, in that model's own unit."""
    functionality: str
    target: Blank = None
    figure: Blank = None

class PlanIn(BaseModel):
    # Loose types: core's refusal names the field in the planner's own words,
    # where pydantic's 422 would name a model attribute.
    users: Blank = None
    vus_per_engine: Blank = None
    engine_cpu: Blank = None
    engine_mem: Blank = None
    engines_per_node: Blank = None
    agents: Blank = None
    sizings: Optional[list[SizingIn]] = None

@app.post("/api/plan", description=core.capacity_plan.__doc__)
def capacity_plan(p: PlanIn):
    """Size a load target. Needs no key: it comes before there is an account."""
    return core.capacity_plan(p.users,
                              vus_per_engine=p.vus_per_engine,
                              engine_cpu=p.engine_cpu, engine_mem=p.engine_mem,
                              engines_per_node=p.engines_per_node,
                              agents=p.agents,
                              sizings=[s.model_dump() for s in p.sizings]
                              if p.sizings is not None else None)

@app.get("/api/sizing-models", description=core.sizing_models.__doc__)
def sizing_models():
    return core.sizing_models()

@app.get("/api/engine-vus", description=core.engine_vus.__doc__)
def engine_vus(cpu: Optional[str] = None, mem: Optional[str] = None):
    return core.engine_vus(_typed(cpu), _typed(mem))

# -- reading the cluster ------------------------------------------------------

@app.get("/api/sv-mocks", description=core.sv_mocks.__doc__)
def sv_mocks(namespace: str, sv_subdomain: Optional[str] = None):
    """Answers 200 whatever the cluster did: it rides the UI's status poll."""
    return core.sv_mocks(namespace, sv_subdomain)

@app.get("/api/sv-check", description=core.sv_check.__doc__)
def sv_check(host: str, scheme: str = "http"):
    """Answers 200 whatever the endpoint did. A plain `def`, so FastAPI runs it
    on a worker thread and a slow probe does not stall the poll."""
    return core.sv_check(host, scheme)

# -- the vocabulary -----------------------------------------------------------
# Each route's /api/docs description is core's docstring.

@app.get("/api/option-defaults", description=core.option_defaults.__doc__)
def option_defaults():
    return core.option_defaults()

@app.get("/api/option-docs", description=core.option_docs.__doc__)
def option_docs():
    return core.option_docs()

@app.get("/api/func-ids", description=core.func_ids.__doc__)
def func_ids(account_id: Optional[int] = None):
    """Without `account_id` (the page on mount, no key yet) the keyless
    baseline; with one, the account's list, cached like the other reads."""
    if account_id is None:
        return core.func_ids()
    return _cached(f"func-ids:{account_id}", core.func_ids, _client(), account_id)

@app.get("/api/functionalities", description=core.functionalities.__doc__)
def functionalities():
    return core.functionalities()

@app.get("/api/sv-constants", description=core.sv_constants.__doc__)
def sv_constants():
    return core.sv_constants()

@app.get("/api/slot-minimums", description=core.slot_minimums.__doc__)
def slot_minimums():
    return core.slot_minimums()

@app.get("/api/ignored-options", description=core.ignored_options.__doc__)
def ignored_options():
    return core.ignored_options()

@app.get("/api/reserved-env", description=core.reserved_env.__doc__)
def reserved_env():
    return core.reserved_env()

@app.get("/api/placeholders", description=core.placeholders.__doc__)
def placeholders():
    return core.placeholders()

@app.get("/api/agent-env", description=core.agent_env.__doc__)
def agent_env(func_ids: Optional[str] = None):
    """The location's funcIds, comma-separated. A string rather than a repeated
    parameter so absent (nobody has said) and empty (runs nothing covered) stay
    two answers; FastAPI would give `[]` for both."""
    return core.agent_env(
        None if func_ids is None else [f for f in func_ids.split(",") if f])

# -- what is actually being served --------------------------------------------

UI_DIST = os.path.join(os.path.dirname(__file__), "ui_dist")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The whole directory: the fingerprint covers index.html and vite.config.ts too.
_FRONTEND = os.path.join(_REPO, "frontend")

def build_state():
    """What this process is serving: version, build time, commit, and `stale`.

    `stale` compares the built page's recorded source fingerprint with the
    sources on disk (never mtimes): True (rebuild needed), False (matches),
    "unrecorded" (the page records no fingerprint), or None (an installed
    wheel, which has no frontend to compare against).
    """
    dist = os.path.join(UI_DIST, ui_build.BUILT_PAGE)
    built = os.path.getmtime(dist) if os.path.exists(dist) else None
    return {"version": _version(), "built": built,
            "stale": ui_build.staleness(_FRONTEND, UI_DIST),
            "commit": _commit()}

def _version():
    try:
        from importlib.metadata import version
        return version("bzm-opl-gen")
    except Exception:
        return None

def _commit():
    """The checkout's HEAD, or None off a checkout. Read from .git directly to
    avoid a subprocess per request."""
    head = os.path.join(_REPO, ".git", "HEAD")
    try:
        with open(head) as fh:
            ref = fh.read().strip()
        if ref.startswith("ref: "):
            with open(os.path.join(_REPO, ".git", ref[5:])) as fh:
                return fh.read().strip()[:12]
        return ref[:12]
    except OSError:
        return None

@app.get("/api/build", description=build_state.__doc__)
def build():
    return build_state()

# -- SPA ----------------------------------------------------------------------
# Mounted after the API: a mount at "/" matches every path.

if os.path.isdir(UI_DIST):
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
else:
    @app.get("/")
    def no_ui():
        return {"error": "ui_dist not built -- run `npm run build` in frontend/",
                "api_docs": "/api/docs"}

LOOPBACK = ("127.0.0.1", "::1", "localhost")

# Said at startup: whoever reaches the page can create locations and agents and
# issue a new AUTH_TOKEN, which revokes a running agent's.
EXPOSED_WARNING = """\
!! bzm-opl-gen ui is bound to {host}, so it is reachable from outside this
!! machine. Anyone who reaches it can act as your BlazeMeter API key: create
!! locations and agents, and issue a new AUTH_TOKEN, which REVOKES the one
!! whatever agent is already running for that ship is using.
!! Prefer the default 127.0.0.1 plus an SSH tunnel:
!!   ssh -L {port}:127.0.0.1:{port} <this-machine>
"""

STALE_UI_WARNING = """\
!! The built page was not built from the current frontend/, so this serves a UI
!! that does not match the code behind it. A route the page needs may answer
!! 404, which the page reads as "not read yet" rather than as an error -- it
!! then shows fields a format hides, and bundles it builds may be missing files.
!!   cd frontend && npm run build
"""

# Not a `!!` line: nothing is known to be wrong, only unchecked.
UNRECORDED_UI_NOTICE = """\
The built page records nothing about the sources it was built from, so whether
it matches the code behind it has not been checked. It predates that record.
A rebuild answers the question:
  cd frontend && npm run build
"""

def main(port=8765, open_browser=True, api_key_path=None, dev=False,
         host="127.0.0.1"):
    import uvicorn
    if host not in LOOPBACK:
        # flush: redirected stdout is block-buffered, and this must arrive
        # before the port is open.
        print(EXPOSED_WARNING.format(host=host, port=port), flush=True)
    # By identity: "unrecorded" is a truthy string.
    stale = build_state()["stale"]
    if stale is True:
        print(STALE_UI_WARNING, flush=True)
    elif stale == ui_build.UNRECORDED:
        print(UNRECORDED_UI_NOTICE, flush=True)
    if api_key_path:
        # The page has a connect form, so a bad key file is reported, not fatal.
        try:
            _state["client"] = core.client_from_key(api_key_path)
        except core.CoreError as e:
            print(f"!! --api-key ignored: {e}", flush=True)
        else:
            _state["key_id"] = _state["client"].key_id
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
