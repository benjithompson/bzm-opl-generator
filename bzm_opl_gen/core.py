"""What the tool does, independent of how it was asked.

The CLI, the web server and the MCP server are thin transports over this
module. It imports no web framework and holds no client. Failures are
`CoreError` subclasses carrying the HTTP status a web layer answers with.
"""

import collections
import concurrent.futures
import http.client
import io
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import (agent_env as agent_env_mod, api, bundle_env, bundle_names,
               bundle_options, ca_check as ca_check_mod, ca_trust, doctor,
               evidence as evidence_mod, facts as facts_mod, footprint,
               generate as gen_mod, image_catalog as image_catalog_mod,
               image_registry, markers, options as options_mod, plan,
               quantity, registry_client, required_fields, service_virt,
               suggest as suggest_mod, sv_read, verdict, workstation)


# -- failures ------------------------------------------------------------------

class CoreError(Exception):
    """A refusal, with its HTTP status. Raise a subclass: the base's 500 means
    a bug here, not bad input."""
    status = 500


class BadRequest(CoreError):
    status = 400


class NotFound(CoreError):
    status = 404


class NotConfigured(CoreError):
    """No usable credential; the message says how to supply one."""
    status = 401


class EvidenceUnreadable(CoreError):
    """A cluster-evidence file that could not be read at all -- distinct from
    one that was read and is not evidence (a BadRequest)."""
    status = 400


class CaBundleUnreadable(CoreError):
    """A CA bundle file that could not be opened -- distinct from one that was
    read and holds no certificate, which the lint reports as a FAIL."""
    status = 400


class UpstreamError(CoreError):
    """BlazeMeter answered with an error; the caller's request was fine."""
    status = 502


class TokenRefused(UpstreamError):
    """BlazeMeter would not issue an agent credential. `.upstream` keeps the
    API's own body, which is the only clue the account restricts the endpoint."""

    def __init__(self, message, upstream):
        super().__init__(message)
        self.upstream = upstream


def _upstream(fn, *args, **kw):
    """A BlazeMeter call, its refusal turned into ours. Only 404 is NotFound
    (re-read the account); anything else is UpstreamError (retry later)."""
    try:
        return fn(*args, **kw)
    except api.BzmApiError as e:
        if e.status == 404:
            raise NotFound(str(e))
        raise UpstreamError(str(e))


# -- where an API key might be ------------------------------------------------

CONFIG_DIR = os.path.expanduser("~/.config/bzm-opl-gen")
SAVED_KEY_PATH = os.path.join(CONFIG_DIR, "api-key.json")


def key_candidates():
    """The paths an api-key.json is looked for, in precedence order.

    A function because BZM_API_KEY_FILE may be set after import (`ui --dev`
    passes the key to its reloader that way).
    """
    return [os.environ.get("BZM_API_KEY_FILE"),
            "api-key.json",
            SAVED_KEY_PATH,
            os.path.expanduser("~/.bzm/api-key.json")]


KEY_ID_ENV = "BZM_API_KEY_ID"
KEY_SECRET_ENV = "BZM_API_KEY_SECRET"
KEY_FILE_ENV = "BZM_API_KEY_FILE"


def client_from_key(api_key_file=None, *, key_id=None, secret=None):
    """The one construction of a BzmClient; raises CoreError, never SystemExit.

    Precedence: the path argument, an id and secret argument, KEY_FILE_ENV,
    then the environment's id/secret pair. The key is not verified here.
    """
    if key_id or secret:
        if api_key_file:
            # Two keys in one call is a caller unsure which account it means.
            raise BadRequest("give an API key file path, or an id and secret, "
                             "not both")
        if not (key_id and secret):
            missing = "secret" if key_id else "id"
            raise BadRequest(f"an API key needs both an id and a secret; "
                             f"the {missing} is missing")
        credentials = (key_id, secret)
    else:
        path = api_key_file or os.environ.get(KEY_FILE_ENV)
        if path:
            try:
                credentials = api.read_key_file(os.path.expanduser(path))
            except ValueError as e:
                raise NotConfigured(
                    f"{e}. Create the key under Settings -> API Keys in "
                    f"BlazeMeter.")
        else:
            env_id = os.environ.get(KEY_ID_ENV)
            env_secret = os.environ.get(KEY_SECRET_ENV)
            if not (env_id and env_secret):
                # No fallback to detect_keys(): a server's working directory is
                # wherever a client launched it, and an api-key.json there may
                # belong to somebody else's account.
                raise NotConfigured(
                    f"no BlazeMeter API key. Set {KEY_FILE_ENV} to the path of "
                    f"an api-key.json ({api.KEY_FILE_SHAPE}), or {KEY_ID_ENV} "
                    f"and {KEY_SECRET_ENV}, in the environment of whatever "
                    f"started this. Create the key under Settings -> API Keys "
                    f"in BlazeMeter.")
            credentials = (env_id, env_secret)
    return api.BzmClient(credentials=credentials)


def detect_keys():
    """Which key candidates exist and parse, with the key id each holds (never
    the secret)."""
    found = []
    for p in key_candidates():
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
    return found


# -- the account tree ----------------------------------------------------------

def user(client):
    """Who this key is. Also the cheapest call that proves it works."""
    return _upstream(client.user)


def whoami(client):
    """The key's user and default account, as every surface reports it."""
    u = user(client)
    return {"email": u.get("email"), "display_name": u.get("displayName"),
            "default_account_id": (u.get("defaultProject") or {}).get("accountId")}


def default_account_id(client):
    """The account this key defaults to, or None if the user record names none."""
    return whoami(client)["default_account_id"]


def accounts(client):
    return _upstream(client.accounts)


def workspaces(client, account_id):
    return _upstream(client.workspaces, account_id)


def require_location_scope(account_id=None, workspace_id=None):
    """A locations listing needs a scope. Separate from locations() so a caller
    can refuse a malformed request before asking for a credential."""
    if not account_id and not workspace_id:
        raise BadRequest("account_id or workspace_id required")


def locations(client, account_id=None, workspace_id=None):
    require_location_scope(account_id, workspace_id)
    return _upstream(client.private_locations, account_id, workspace_id)


def location(client, harbor_id):
    """One location with its ships in full."""
    return _upstream(client.private_location, harbor_id)


# Locations a listing returns when the caller did not say. Real accounts hold
# 170+, which overflows an MCP caller's result budget.
DEFAULT_LOCATION_LIMIT = 50


def select_locations(locs, name_contains=None, limit=DEFAULT_LOCATION_LIMIT):
    """Narrow a listing, counting every location that does not come back.

    The counts are what stop a partial list reading as the whole account; the
    filter and the cap are counted apart. `limit=None` means no cap.
    """
    if limit is not None:
        # bool is an int subclass, and "10" < 1 would be a TypeError.
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise BadRequest(
                f"limit must be a whole number, not {limit!r}. Omit it for the "
                f"default of {DEFAULT_LOCATION_LIMIT}")
        if limit < 1:
            raise BadRequest("limit must be at least 1, or omitted for the "
                             f"default of {DEFAULT_LOCATION_LIMIT}")
    needle = (name_contains or "").strip().lower()
    matched = [l for l in locs
               if not needle or needle in (l.get("name") or "").lower()]
    kept = matched if limit is None else matched[:limit]
    return {"locations": kept,
            "total": len(locs),
            "matched": len(matched),
            "returned": len(kept),
            "omitted_by_filter": len(locs) - len(matched),
            "omitted_by_limit": len(matched) - len(kept)}


# BlazeMeter's 403 for a location missing slots or threadsPerEngine names
# neither field, so every surface that creates a location says it.
LOCATION_UNRUNNABLE = (
    "WARNING: location is not runnable -- tests will fail to start with "
    "403 'Not enough available resources'. Set the missing field(s) in "
    "the BlazeMeter UI (Settings -> Private Locations).")


def location_runnable(location):
    """Whether a location has both fields a test start needs."""
    return bool(location.get("slots") and location.get("threadsPerEngine"))


# Slots a functionality needs before BlazeMeter will create the location at
# all, by funcId. Found on a live create, not documented. `message` is
# BlazeMeter's own sentence. The default is not raised to meet it: slots is a
# real per-agent cost the caller chooses.
SLOT_MINIMUMS = {
    "functionalGui": {
        "label": "GUI Functional",
        "minimum": 2,
        "message": ("The option Parallel engine runs must be greater than 1 "
                    "for a Private Location with the GUI Functional "
                    "Functionality enabled."),
    },
}


def slot_minimums():
    """The per-functionality slot minimums, as {funcId: {label, minimum,
    message}}, served so a form can state the rule before the account does."""
    return SLOT_MINIMUMS


def slots_refusal(func_ids, slots):
    """Why BlazeMeter would refuse a location with these funcIds and slots, or
    None if it would not."""
    for func_id in func_ids or ():
        rule = SLOT_MINIMUMS.get(func_id)
        if rule and (slots or 0) < rule["minimum"]:
            return (f"{rule['message']} Create this location with "
                    f"slots={rule['minimum']} or more, or leave "
                    f"{rule['label']} off it.")
    return None


def create_location(client, name, account_id, workspace_id,
                    func_ids=api.DEFAULT_FUNC_IDS, slots=1,
                    threads_per_engine=footprint.DEFAULT_THREADS_PER_ENGINE):
    """Create a private location, and say whether a test can start on it.

    Returns {location, runnable, warning}; `warning` is None for a runnable
    location. A slot minimum is refused before the POST.
    """
    refusal = slots_refusal(func_ids, slots)
    if refusal:
        raise BadRequest(refusal)
    loc = _upstream(client.create_private_location, name, account_id,
                    [workspace_id], func_ids=list(func_ids), slots=slots,
                    threads_per_engine=threads_per_engine)
    runnable = location_runnable(loc)
    return {"location": loc, "runnable": runnable,
            "warning": None if runnable else LOCATION_UNRUNNABLE}


def create_ship(client, harbor_id, name):
    return _upstream(client.create_ship, harbor_id, name)


def create_agent(client, harbor_id, name, issue_token=True):
    """Create an agent, and by default issue its first AUTH_TOKEN with it.

    Returns {ship, auth_token, token_error}. A new agent has no previous token
    to revoke, so issuing here is free. A refused token endpoint is reported in
    `token_error` rather than raised: the agent exists either way, and losing
    its id invites a second one. `issue_token=False` issues nothing (the MCP
    server never returns a token).
    """
    ship = create_ship(client, harbor_id, name)
    token = refused = None
    if issue_token:
        try:
            token = fetch_ship_token(client, harbor_id, ship["id"])
        except CoreError as e:
            refused = str(e)
    return {"ship": ship, "auth_token": token, "token_error": refused}


# The location settings this tool changes, as {name: BlazeMeter's field}. A
# closed set: BlazeMeter's PATCH replaces `funcIds` wholesale, so a passthrough
# could drop functionalities nobody named. funcIds are changed in BlazeMeter's UI.
LOCATION_SETTINGS = {
    "slots": "slots",
    "threads_per_engine": "threadsPerEngine",
    "override_cpu": "overrideCPU",
    "override_memory": "overrideMemory",
}


def update_location(client, harbor_id, **settings):
    """Change a location's concurrency settings, and report what actually took.

    The location is re-read after the write: `before`/`after` per field, and
    `ignored` for what BlazeMeter accepted without storing. None means "leave
    alone", so nothing can be cleared. Unknown settings are refused.
    """
    unknown = sorted(set(settings) - set(LOCATION_SETTINGS))
    if unknown:
        raise BadRequest(
            f"not a location setting: {', '.join(unknown)} -- this changes "
            f"{', '.join(sorted(LOCATION_SETTINGS))}. Functionalities (funcIds) "
            f"and "
            f"anything else are BlazeMeter's own UI")
    wanted = {k: v for k, v in settings.items() if v is not None}
    before = _upstream(client.private_location, harbor_id)
    was = _settings_of(before)
    if not wanted:
        return {"location": before, "changed": {}, "ignored": [],
                "before": was, "after": dict(was)}
    _upstream(client.update_private_location, harbor_id, **wanted)
    after = _upstream(client.private_location, harbor_id)
    now = _settings_of(after)
    changed = {k: now[k] for k in wanted if now[k] != was[k]}
    ignored = sorted(k for k in wanted if now[k] == was[k] and wanted[k] != was[k])
    return {"location": after, "changed": changed, "ignored": ignored,
            "before": was, "after": now}


def _settings_of(location):
    """The four settings as this tool names them, from a location document."""
    return {name: location.get(field) for name, field in LOCATION_SETTINGS.items()}


def gather_facts(client, harbor_id):
    return _upstream(facts_mod.gather, client, harbor_id)


# The whole of pinning to the newest releases, however many repositories.
PIN_BUDGET_S = 8


def release_pins(budget_s=None):
    """facts.release_pins: the newest release tag of each repository with a
    release series, read from BlazeMeter's registry in parallel. Never raises
    and never waits past the budget: an unanswered read is unread."""
    budget_s = PIN_BUDGET_S if budget_s is None else budget_s
    repos = image_catalog_mod.release_repos()

    def one(repo):
        reg, path, _ = registry_client.registry_for(f"{repo}:latest")
        t = reg.tags(path)
        if t["state"] != registry_client.READ:
            return {"state": facts_mod.PIN_UNREAD, "tag": None,
                    "detail": t["detail"]}
        tag = image_catalog_mod.release_tag(
            image_catalog_mod.repo_path(repo), t["tags"])
        if tag is None:
            return {"state": facts_mod.PIN_NO_RELEASE, "tag": None,
                    "detail": "the registry lists no release tag for it"}
        return {"state": facts_mod.PIN_PINNED, "tag": tag, "detail": None}

    pool = concurrent.futures.ThreadPoolExecutor(REGISTRY_WORKERS)
    futures = {repo: pool.submit(one, repo) for repo in repos}
    concurrent.futures.wait(futures.values(), timeout=budget_s)
    # Not waiting for a read still in flight: its thread ends on its own timeout.
    pool.shutdown(wait=False, cancel_futures=True)
    images = {}
    for repo, fut in futures.items():
        if fut.done() and not fut.cancelled() and fut.exception() is None:
            images[repo] = fut.result()
        else:
            images[repo] = {"state": facts_mod.PIN_UNREAD, "tag": None,
                            "detail": f"no answer within {budget_s}s"}
    unread = [r for r, p in images.items() if p["state"] == facts_mod.PIN_UNREAD]
    if not unread:
        state, detail = registry_client.READ, None
    else:
        state = (registry_client.UNREAD if len(unread) == len(images)
                 else "partial")
        detail = (f"{len(unread)} of {len(images)} repositories could not be "
                  f"read; the first: {images[unread[0]]['detail']}")
    return {"state": state, "detail": detail, "images": images}


def manual_facts(harbor_id=None, ship_id=None, func_ids=api.DEFAULT_FUNC_IDS,
                 pin=True):
    """Facts from ids read off the BlazeMeter UI, with no API key.

    Neither id is required or validated: a blank one becomes its marker (see
    facts.manual), for a location that does not exist yet. With `pin`, each
    image is pinned to its newest release in BlazeMeter's registry (bounded
    by PIN_BUDGET_S); `warnings` says what that cannot tell.
    """
    facts = facts_mod.manual(harbor_id, ship_id, func_ids=list(func_ids),
                             release_pins=release_pins() if pin else None)
    return {"facts": facts,
            "gui_images_incomplete": facts_mod.gui_images_incomplete(facts),
            "warnings": facts_warnings(facts)}


def facts_warnings(facts):
    """What these facts cannot tell a bundle, as sentences for whoever made them.

    A refused image list (the images are the catalogue's), a GUI location with
    no browser image, and ids left blank (the bundle carries markers).
    """
    out = []
    if facts_mod.image_list_state(facts) == facts_mod.IMAGE_LIST_UNREAD:
        out.append(
            f"the location's own image list could not be read "
            f"({facts['image_list']['detail']}), so the images are the fallback "
            f"catalogue's rather than this location's. Versions may be wrong "
            f"and browser images are missing.")
    if facts_mod.gui_images_incomplete(facts):
        out.append(
            "this location runs GUI/browser tests, and these facts carry no "
            "version-pinned browser image (charmander/chrome_*, firefox_*, ...). "
            "The account names the pinned build: gather facts with an API key, "
            "or add the key to IMAGE_OVERRIDES by hand. Fine against the public "
            "registry; against a private one the browser engines fail to pull.")
    out += _pin_warnings(facts.get("release_pins") or {})
    blank = [f"{k} ({markers.marker(k)})" for k, v in
             (("harbor_id", facts.get("harbor_id")),
              ("ship_id", sole_ship_id(facts))) if markers.is_placeholder(v)]
    if blank:
        out.append(
            f"{' and '.join(blank)} left blank, so every bundle generated from "
            f"these facts carries the marker instead. The cluster refuses it -- "
            f"a marker is not a legal label value -- so the bundle is for "
            f"review until the ids are filled in, or the facts re-made once the "
            f"location exists.")
    return out


def _pin_warnings(pins):
    """What pinning to the newest releases leaves unsaid. Plain prose: these
    are shown in Markdown and in a terminal."""
    out = []
    unread = [r.rsplit("/", 1)[-1] for r, p in (pins.get("images") or {}).items()
              if p.get("state") == facts_mod.PIN_UNREAD]
    if unread:
        out.append(
            f"BlazeMeter's registry could not be read ({pins.get('detail')}), "
            f"so {', '.join(unread)} keep the tag latest. On BlazeMeter's "
            f"registry latest names releases far older than the newest; for "
            f"the test engine it was an earlier major version when this was "
            f"checked. Connect an API key: the location's own image list "
            f"gives the exact versions.")
    if any(p.get("state") == facts_mod.PIN_PINNED
           for p in (pins.get("images") or {}).values()):
        out.append(
            "The image versions are the newest releases in BlazeMeter's "
            "registry, not read from your location. A location can ask for an "
            "older release than the newest, so a mirror built from these facts "
            "can lack the image the agent asks for. Connect an API key for the "
            "exact list, put a pull-through cache in front of BlazeMeter's "
            "registry, or check the mirror with the verify option of "
            "bzm-opl-gen images once the agent is online.")
    return out


# A heartbeat counts as fresh for two of the agent's reporting intervals.
HEARTBEAT_FRESH_S = 120
ONLINE_STATES = ("idle", "running")


def ship_reporting(ship):
    """Whether one ship is reporting now, or None where the payload cannot say.

    A listing payload may omit `lastHeartBeat`; absent is unknown, not "no".
    """
    if "lastHeartBeat" not in ship:
        return None
    hb = ship.get("lastHeartBeat") or 0
    return bool(hb and time.time() - hb < HEARTBEAT_FRESH_S
                and ship.get("state") in ONLINE_STATES)


def reporting_counts(ships):
    """{agents_reporting, agents_unknown} over a location's ships.

    Reporting counts only agents the payload vouches for; unknown counts those
    it says nothing about, so a zero can be read as looked-and-none or not.
    """
    states = [ship_reporting(s) for s in ships or ()]
    return {"agents_reporting": sum(1 for r in states if r),
            "agents_unknown": sum(1 for r in states if r is None)}


def agent_status(client, harbor_id, ship_id):
    """Is this agent reporting now (a fresh heartbeat, not just a state)?
    `heartbeat_age_s` is None if it never reported, a number if it went quiet."""
    harbor = _upstream(client.private_location, harbor_id)
    ship = next((s for s in harbor.get("ships", []) if s["id"] == ship_id), None)
    if not ship:
        raise NotFound(f"ship {ship_id} not in location {harbor_id}")
    hb = ship.get("lastHeartBeat") or 0
    return {
        "state": ship.get("state"),
        "heartbeat_age_s": int(time.time() - hb) if hb else None,
        "installed_version": ship.get("installedVersion"),
        "online": bool(ship_reporting(ship)),
    }


# -- the agent credential ------------------------------------------------------

TOKEN_CANNOT_BE_FETCHED = (
    "The AUTH_TOKEN can be supplied instead of fetched, and a bundle built with "
    "one supplied fetches nothing, so a closed endpoint costs only the "
    "convenience.")


def fetch_ship_token(client, harbor_id, ship_id):
    """Issue the ship's AUTH_TOKEN (revoking the previous one) and return it.
    Some accounts refuse the endpoint outright; the refusal names the way on."""
    try:
        return client.auth_token(harbor_id, ship_id)
    except api.BzmApiError as e:
        raise TokenRefused(
            f"The AUTH_TOKEN for ship {ship_id} in location {harbor_id} could "
            f"not be issued: BlazeMeter refused the credential fetch itself, "
            f"not the operation you asked for. Some accounts allow this "
            f"endpoint only from BlazeMeter's own gateway, in which case every "
            f"attempt from here fails whatever it sends. "
            f"{TOKEN_CANNOT_BE_FETCHED} {token_recovery_hint()} "
            f"BlazeMeter said: {e}", str(e))


# -- generating a bundle -------------------------------------------------------

def sole_ship_id(facts, explicit=None):
    """The ship an operation is about: `explicit`, else the only ship, else
    None. A location with two agents has no default."""
    ships = facts.get("ships") or []
    return explicit or (ships[0]["id"] if len(ships) == 1 else None)


def token_ship_id(facts, options):
    """Which ship an AUTH_TOKEN would be rotated for, or None. A token already
    in the options is the caller's and is never replaced."""
    if options.get("auth_token"):
        return None
    return sole_ship_id(facts, options.get("ship_id"))


def rotate_auth_token(client, facts, options):
    """Mint a fresh AUTH_TOKEN into `options` (mutated) if one is wanted, and
    return the ship it was minted for, or None."""
    ship_id = token_ship_id(facts, options)
    if ship_id:
        options["auth_token"] = fetch_ship_token(client, facts["harbor_id"],
                                                 ship_id)
    return ship_id


# Which of four ways a bundle's AUTH_TOKEN arrived. Every caller reports it,
# because the four differ in what happens to an agent already running.
TOKEN_GIVEN = "given"
TOKEN_ROTATED = "rotated"
TOKEN_REUSED = "reused"
TOKEN_PLACEHOLDER = "placeholder"

# `message` is a sentence for whoever asked; `ship_id` is the token's ship where
# known. No field ever carries the token value, so `_asdict()` is safe to return.
TokenSource = collections.namedtuple("TokenSource", "branch ship_id message")


def rotation_warning(ship_id):
    """What a rotation is about to do, to say before it happens: an agent on a
    dead token sits `0/1 Running`, which looks like a slow boot."""
    return (
        f"ROTATING the AUTH_TOKEN for ship {ship_id}: BlazeMeter issues a new "
        f"one and the previous one stops working immediately. Any agent already "
        f"running on it will start answering 404 and sit at `0/1 Running`, "
        f"which looks like a slow boot, until you re-apply this bundle -- "
        f"Secret included.")


def token_recovery_hint(options=None):
    """Where a real AUTH_TOKEN comes from, in words every surface can show."""
    o = options or {}
    ns = o.get("namespace") or bundle_options.DEFAULT_OPTIONS["namespace"]
    return (
        f"A real one comes from what was shown when the agent was created "
        f"(`create-agent` prints it; the web page puts it in the field) -- keep "
        f"it, nothing here stores it -- or from the agent's install command in "
        f"the BlazeMeter UI (Settings -> Private Locations -> the location -> "
        f"the agent), or out of an agent already deployed:\n"
        f"    kubectl -n {ns} get secret {bundle_names.SECRET_NAME} "
        f"-o jsonpath='{{.data.AUTH_TOKEN}}' | base64 -d\n"
        f"  Supply it as the bundle's auth_token -- `--auth-token` on the "
        f"command line, the AUTH_TOKEN field on the web page -- and the bundle "
        f"is complete. Issuing a fresh one instead (`--rotate-token`, or the "
        f"tick-box that says so on the page) takes down whatever is running on "
        f"the current one until you re-apply.")


def _bundle_ship_id(out_dir):
    """Which ship the bundle in `out_dir` was generated for, or None where that
    cannot be confirmed (no profile, or one that does not parse)."""
    try:
        return gen_mod.load_profile(out_dir).get("ship_id") or None
    except (OSError, ValueError):
        return None


def resolve_auth_token(facts, options, client=None, rotate=False, out_dir=None,
                       announce=None):
    """Put the AUTH_TOKEN into `options` (mutated), and say how it arrived.

    Precedence:
      1. a token already in the options wins, and nothing is issued;
      2. `rotate` mints a new one (needs `client`) -- the only branch that does;
      3. otherwise a bundle in `out_dir` for the same ship lends its token;
         a bundle there for another (or an unknown) ship is refused, because
         overwriting it would lose a credential BlazeMeter cannot return;
      4. otherwise the placeholder stays, with where a real token comes from.

    `announce` is called with `rotation_warning(...)` just before a mint.
    Idempotent: a second call takes branch 1. `out_dir` is only read here.
    """
    placeholder = bundle_options.DEFAULT_OPTIONS["auth_token"]
    held = options.get("auth_token")
    if held and held != placeholder:
        # Rotating alongside a supplied token would revoke the one supplied.
        ignored = (" --rotate-token was NOT acted on: rotating would have "
                   "revoked the very token you passed." if rotate else "")
        return TokenSource(TOKEN_GIVEN, sole_ship_id(facts,
                                                     options.get("ship_id")),
                           "AUTH_TOKEN as supplied -- nothing was issued, so "
                           "an agent already running on it keeps working."
                           + ignored)

    if rotate:
        if client is None:
            raise BadRequest(
                "rotating the AUTH_TOKEN needs a BlazeMeter API key -- pass "
                "--api-key (the CLI) or connect first. Without one the bundle "
                "can still be completed by hand: "
                + TOKEN_CANNOT_BE_FETCHED + " " + token_recovery_hint(options))
        ship_id = token_ship_id(facts, options)
        if not ship_id:
            ships = [s["id"] for s in facts.get("ships") or []]
            raise BadRequest(
                f"say which ship to rotate the AUTH_TOKEN for: this location "
                f"has {len(ships)} agents ({ships}). Rotating the wrong one "
                f"revokes the credential of an agent nobody mentioned, and that "
                f"agent then sits at 0/1 Running -- so nothing is guessed here. "
                f"Pass --ship-id.")
        if announce:
            announce(rotation_warning(ship_id))
        rotate_auth_token(client, facts, options)
        return TokenSource(
            TOKEN_ROTATED, ship_id,
            f"rotated: a NEW AUTH_TOKEN was issued for ship {ship_id} and the "
            f"previous one is now dead. Re-apply this whole bundle, Secret "
            f"included, or that agent stays at 0/1.")

    want = sole_ship_id(facts, options.get("ship_id"))
    if out_dir:
        found = gen_mod.existing_auth_token(out_dir)
        theirs = _bundle_ship_id(out_dir) if found else None
        if found and want and theirs == want:
            options["auth_token"] = found
            return TokenSource(
                TOKEN_REUSED, want,
                f"reused the AUTH_TOKEN already in {out_dir} (ship {want}) -- "
                f"nothing was issued, so this bundle is byte-identical to the "
                f"last one and the agent running from it is unaffected.")
        if found:
            if theirs and not want:
                named = (f"a bundle for ship {theirs}, and nothing here says "
                         f"which ship the new one is for -- this location has "
                         f"several agents, so pass ship_id (--ship-id)")
            elif theirs:
                named = f"a bundle for ship {theirs}, not {want}"
            else:
                named = (f"a bundle whose {bundle_names.PROFILE_FILE} does not say "
                         f"which ship its AUTH_TOKEN belongs to")
            remedy = ("Pass --auth-token (auth_token) to say what this "
                      "bundle's credential is, or --rotate-token to issue a "
                      "fresh one -- either makes replacing that bundle "
                      "deliberate. Or generate somewhere else and keep it."
                      if theirs else
                      f"Pass --auth-token (auth_token) with the token that "
                      f"bundle belongs to, and it will be written back. "
                      f"{token_recovery_hint(options)}")
            raise BadRequest(
                f"{out_dir} already holds {named}, and generating here would "
                f"overwrite it. Its AUTH_TOKEN cannot be read back from "
                f"BlazeMeter afterwards -- the only endpoint that returns one "
                f"issues a new one -- so that token would be recoverable only "
                f"from an agent already running on it. {remedy}")
    return TokenSource(
        TOKEN_PLACEHOLDER, want,
        f"AUTH_TOKEN left as {placeholder}, so this bundle cannot be applied "
        f"as it stands. {token_recovery_hint(options)}")


# What build_bundle returns: the files, how the token arrived, and what was
# written ([{name, bytes}], or None when nothing was).
Bundle = collections.namedtuple("Bundle", "files token written")


def build_bundle(facts, options=None, *, client=None, rotate=False,
                 out_dir=None, write=False, announce=None):
    """Resolve the AUTH_TOKEN, render the bundle, and optionally write it.

    With `write`, the absolute-path check on `out_dir` runs before any mint.
    `client` is needed only for `rotate`; `options` is not mutated.
    """
    if write:
        if not out_dir:
            raise BadRequest("out_dir is required to write a bundle")
        require_absolute_out_dir(out_dir)
    opts = dict(options or {})
    source = resolve_auth_token(facts, opts, client=client, rotate=rotate,
                                out_dir=out_dir, announce=announce)
    try:
        files = gen_mod.generate(facts, opts)
    except (ValueError, KeyError) as e:
        # generate()'s refusals are sentences for whoever set the option. A
        # rotation that already happened must not be lost with them.
        tail = f" ({source.message})" if source.branch == TOKEN_ROTATED else ""
        raise BadRequest(f"{e}{tail}")
    written = write_bundle(files, out_dir) if write else None
    return Bundle(files, source, written)


def generate_bundle(facts, options=None, client=None, rotate_token=False,
                    out_dir=None):
    """The manifests, as {name: content}. See build_bundle."""
    return build_bundle(facts, options, client=client, rotate=rotate_token,
                        out_dir=out_dir).files


def preview_order(files):
    """Which file to read first, and the rest after it (a helm bundle leads
    with its values overlay)."""
    return gen_mod.preview_order(files)


ZIP_PREFIX = "bzm-opl"


def zip_bundle(files, prefix):
    """The bundle as a zip whose entries sit under `prefix/` (use zip_stem)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in preview_order(files):
            info = zipfile.ZipInfo(f"{prefix}/{name}")
            if name.endswith(".sh"):
                info.external_attr = 0o755 << 16
            z.writestr(info, files[name])
    return buf.getvalue()


def zip_stem(options):
    """`bzm-opl-<namespace>`: the archive's name and its top directory, with
    characters an extractor may refuse (a marker's brackets) dropped."""
    ns = (options or {}).get("namespace") or "blazemeter"
    ns = re.sub(r"[^A-Za-z0-9._-]", "", ns) or "blazemeter"
    return f"{ZIP_PREFIX}-{ns}"


def zip_filename(options):
    return f"{zip_stem(options)}.zip"


def require_absolute_out_dir(out_dir):
    """Refuse a relative bundle directory: a server's working directory is
    whatever launched it, not anywhere the caller chose."""
    if not os.path.isabs(out_dir):
        raise BadRequest(
            f"out_dir must be an absolute path, not {out_dir!r} -- a relative "
            f"one resolves against this process's working directory, which is "
            f"whatever started it rather than anywhere you chose")
    return out_dir


def write_bundle(files, out_dir):
    """Write a bundle to an absolute `out_dir`; returns [{name, bytes}]."""
    require_absolute_out_dir(out_dir)
    gen_mod.write(files, out_dir)
    return [{"name": n, "bytes": len(files[n].encode())} for n in preview_order(files)]


# Matched by field name, since a reader does not know the value. The names come
# from the module that writes them.
_TOKEN_FIELDS = re.compile(
    r'^(?P<lead>\s*(?:' + "|".join(gen_mod.TOKEN_FIELDS) +
    r')\s*:\s*)(?P<quote>["\']?)(?P<value>.+?)(?P=quote)\s*$', re.M)
REDACTED = "<redacted -- opl_location reveal_token>"


def redact_tokens(text):
    """Blank any AUTH_TOKEN a bundle file carries; returns (text, count)."""
    return _TOKEN_FIELDS.subn(lambda m: f"{m.group('lead')}\"{REDACTED}\"", text)


def read_bundle_file(out_dir, name):
    """One file of a written bundle. `name` comes from outside, so the resolved
    path (symlinks included) must stay inside `out_dir`."""
    if not os.path.isabs(out_dir):
        raise BadRequest(f"out_dir must be an absolute path, not {out_dir!r}")
    root = os.path.realpath(out_dir)
    path = os.path.realpath(os.path.join(root, name))
    if path != root and not path.startswith(root + os.sep):
        raise BadRequest(f"{name!r} is not inside the bundle at {out_dir}")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (FileNotFoundError, IsADirectoryError):
        raise NotFound(f"no file {name!r} in the bundle at {out_dir}")
    except UnicodeDecodeError:
        raise BadRequest(f"{name!r} is not text")


def mirror_images(facts, mirror=None, platform="linux/amd64", dry_run=False,
                  all_images=False, options=None):
    """Pull each image the location's bundle needs and, with `mirror`, tag and
    push it under that prefix.

    Returns the commands (a dry run is a readable plan). Targets are
    image_registry.mirror_targets, the names the bundle's own mirror script
    pushes: `options` are the bundle's (its profile.json), whose format and
    crane_hook decide them; without them, a Kubernetes bundle's.
    """
    if mirror:
        o = {**bundle_options.DEFAULT_OPTIONS, **(options or {}),
             "private_registry": mirror}
        pairs = image_registry.mirror_targets(facts, o, all_images=all_images)
    else:
        pairs = [(ref, None) for ref in bundle_images(facts, all_images)]
    ran = []
    for ref, target in pairs:
        ran.append(_docker(["pull", "--platform", platform, ref], dry_run))
        if target:
            ran.append(_docker(["tag", ref, target], dry_run))
            ran.append(_docker(["push", target], dry_run))
    return {"mirror": mirror, "platform": platform, "dry_run": bool(dry_run),
            "commands": ran}


def _docker(args, dry_run):
    cmd = ["docker"] + args
    if not dry_run:
        import subprocess
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise UpstreamError(
                f"{' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return " ".join(cmd)


def bundle_images(facts, all_images=False):
    """Every image reference this location's bundle will pull (crane first)."""
    return facts_mod.image_refs(facts, all_images=all_images)


# Registry reads run side by side; each is short-timed in registry_client.
REGISTRY_WORKERS = 8

_NOT_LOOKED_UP = {"registry_state": registry_client.NOT_ASKED,
                  "registry_detail": None, "digest": None, "size_mb": None,
                  "newest_tag": None, "update_available": None,
                  "resolves_to": None}


def image_catalog(facts=None, lookup=True, all_images=False):
    """Each image a location pulls, with what it is for: {source, location,
    image_list_state, registry_lookup, images}.

    Without facts, every image the catalogue knows (`required` null). With
    `lookup`, BlazeMeter's public registry adds each image's digest, size and
    newest tag in its series, and names the version a floating tag
    (`latest`) currently is (`resolves_to`). A registry read never raises: its state is per
    image, and `registry_lookup.state` is read, unread, partial or not-asked.
    """
    if facts is None:
        # Pinned only when the registry is asked anyway.
        rows = image_catalog_mod.catalogue_rows(
            release_pins() if lookup else None)
        head = {"source": "catalogue", "location": None,
                "image_list_state": facts_mod.IMAGE_LIST_NOT_ASKED}
    else:
        rows = image_catalog_mod.location_rows(facts, all_images=all_images)
        head = {"source": "location",
                "location": {"harbor_id": facts.get("harbor_id") or "",
                             "name": facts.get("harbor_name") or "",
                             "func_ids": list(facts.get("func_ids") or [])},
                "image_list_state": facts_mod.image_list_state(facts)}
    if not lookup:
        return {**head, "registry_lookup": {"state": registry_client.NOT_ASKED,
                                            "detail": None},
                "images": [{**r, **_NOT_LOOKED_UP} for r in rows]}
    with concurrent.futures.ThreadPoolExecutor(REGISTRY_WORKERS) as pool:
        found = list(pool.map(registry_client.lookup, [r["ref"] for r in rows]))
    images = [{**r, **f} for r, f in zip(rows, found)]
    unread = [i for i in images
              if i["registry_state"] != registry_client.READ]
    if not unread:
        summary = {"state": registry_client.READ, "detail": None}
    else:
        summary = {"state": registry_client.UNREAD if len(unread) == len(images)
                   else "partial",
                   "detail": f"{len(unread)} of {len(images)} images could not "
                             f"be read from the registry; the first: "
                             f"{unread[0]['registry_detail']}"}
    return {**head, "registry_lookup": summary, "images": images}


def verify_mirror(facts, registry, options=None, ca_file=None):
    """Is each image this bundle pulls in the customer's `registry`, under the
    name the mirror script pushes it to? Each is present, missing or unread.

    `options` are the bundle's (its profile.json): the format and crane_hook
    decide the names. Credentials come from the environment or the docker
    config, never from an argument. A leading `http://` marks a plain-HTTP
    registry.
    """
    reg_prefix = registry_client.strip_scheme(registry)
    if not reg_prefix:
        raise BadRequest("--verify needs a registry, such as "
                         "registry.example.com/blazemeter")
    o = {**bundle_options.DEFAULT_OPTIONS, **(options or {}),
         "private_registry": reg_prefix}
    targets = image_registry.mirror_targets(facts, o)
    scheme, host, _, _ = registry_client.split_ref(
        f"{registry.rstrip('/')}/probe:latest")
    user, password, where = registry_client.credentials_for(host)
    try:
        client = registry_client.Registry(
            host, scheme=scheme,
            credentials=(user, password) if user else None, ca_file=ca_file)
    except (OSError, ssl.SSLError) as e:
        raise BadRequest(f"the CA file {ca_file!r} could not be used: {e}")

    def check(pair):
        ref, target = pair
        _, _, path, tag = registry_client.split_ref(target)
        return {"ref": ref, "target": target, **client.check(path, tag)}

    with concurrent.futures.ThreadPoolExecutor(REGISTRY_WORKERS) as pool:
        images = list(pool.map(check, targets))
    counts = collections.Counter(i["state"] for i in images)
    return {"registry": reg_prefix,
            "credentials": where if user else f"anonymous ({where})",
            "images": images,
            "present": counts[registry_client.PRESENT],
            "missing": counts[registry_client.MISSING],
            "unread": counts[registry_client.UNREAD]}


# -- planning, before any of the above exists ---------------------------------

def capacity_plan(users=None, vus_per_engine=None, engine_cpu=None,
                  engine_mem=None, engines_per_node=None, agents=None,
                  sizings=None):
    """What a sizing needs, as numbers plus `document`, a request to hand to
    whoever provisions the cluster. Needs no key, account or cluster."""
    try:
        p = plan.capacity_plan(
            users, vus_per_engine=vus_per_engine,
            engine_cpu=engine_cpu, engine_mem=engine_mem,
            engines_per_node=engines_per_node, agents=agents,
            sizings=sizings)
    except ValueError as e:
        raise BadRequest(str(e))
    return dict(p,
                document=plan.plan_document(p),
                document_file=plan.DOCUMENT_FILE)


def sizing_models():
    """What each covered functionality is sized in. `measured` False means no
    per-pod figure exists for that unit at all."""
    labels = covered_func_ids()
    return [{"functionality": fid,
             "label": labels.get(fid, m["name"]),
             "unit": m["unit"],
             "target_field": m["target_field"],
             "figure_field": m["figure_field"],
             "figure_unit": m["figure_unit"],
             "measured": m["baseline"] is not None,
             "pods": m["pods"],
             "example_target": m["example_target"]}
            for fid, m in plan.SIZING_MODELS.items()]


def engine_vus(engine_cpu=None, engine_mem=None):
    """What a pod of this size is rated for, per model (`rated`, None where
    unmeasured); `supported_vus` is the performance figure."""
    try:
        cpu, mem = bundle_options.engine_size({"engine_cpu_limit": engine_cpu,
                                        "engine_mem_limit": engine_mem})
    except ValueError as e:
        raise BadRequest(str(e))
    return {"cpu": quantity.format_cpu(cpu),
            "memory": quantity.format_memory(mem),
            "supported_vus": plan.supported_vus(cpu, mem),
            "rated": {fid: plan.per_pod_capacity(fid, cpu, mem)
                      for fid in plan.SIZING_MODELS}}


def account_capacity(client, account_id):
    """Rated virtual-user capacity across an account, per location.

    `agents x slots` engines (enforced) `x threadsPerEngine` (sized for, not
    enforced). `rated_vus` is None where a field is unset; a `shared` location
    counts once in the account total.
    """
    # The two reads are independent and the locations one is slow.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        want_locs = pool.submit(_upstream, client.private_locations,
                                account_id=account_id)
        want_spaces = pool.submit(_upstream, client.workspaces, account_id)
        locs = want_locs.result()
        spaces = {w["id"]: w["name"] for w in want_spaces.result()}
    out = []
    for l in locs:
        ships = l.get("ships") or []
        slots, tpe = l.get("slots"), l.get("threadsPerEngine")
        engines = (slots or 0) * len(ships)
        ws = list(l.get("workspacesId") or [])
        out.append({
            "id": l["id"], "name": l.get("name"),
            "func_ids": l.get("funcIds") or [],
            "agents": len(ships),
            **reporting_counts(ships),
            "slots": slots, "threads_per_engine": tpe,
            "engines": engines,
            "rated_vus": engines * tpe if (slots and tpe) else None,
            "workspace_ids": ws,
            "workspace_names": [spaces.get(w, str(w)) for w in ws],
            "shared": len(ws) > 1,
        })
    return {"account_id": account_id,
            "workspaces": [{"id": i, "name": n} for i, n in spaces.items()],
            "locations": out,
            "rated_vus": sum(x["rated_vus"] or 0 for x in out),
            "unrated": sum(1 for x in out if x["rated_vus"] is None)}


# -- preflight -----------------------------------------------------------------

def evidence_document(evidence):
    """The evidence document from a path (a string is always a path) or the
    parsed object, which passes through to be judged by the checks."""
    if not isinstance(evidence, str):
        return evidence
    try:
        return doctor.load_evidence(os.path.expanduser(evidence))
    except ValueError as e:
        raise EvidenceUnreadable(str(e))


def preflight_cluster(evidence, options=None, namespace=None):
    """(Evidence, namespace) for a preflight. Namespace precedence: argument,
    options, the evidence's own, default. Falsy `evidence` is a live cluster."""
    options = options or {}
    doc_ns = (evidence.get(evidence_mod.NAMESPACE)
              if isinstance(evidence, dict) else None)
    want = namespace or options.get("namespace") or doc_ns
    if not evidence:
        return doctor.Evidence(None, None, ()), doctor.resolve_namespace(
            want, options)
    try:
        imported = doctor.cluster_from_evidence(evidence, want)
    except ValueError as e:
        raise BadRequest(str(e))
    return imported, doctor.resolve_namespace(want, options)


def preflight(facts, options, evidence):
    """The verdicts `doctor --cluster-evidence` prints, plus the suggestions the
    same file implies. Reaches no account and no cluster."""
    options = options or {}
    imported, namespace = preflight_cluster(evidence, options)
    try:
        checks = doctor.evaluate(facts, options, namespace, evidence=imported)
    except (ValueError, KeyError) as e:
        raise BadRequest(str(e))
    return {"namespace": namespace,
            **_verdicts(checks),
            "summary": doctor.summary_line(checks),
            # Which namespace the file describes, which may differ from the one
            # being preflighted.
            "evidence": doctor.evidence_summary(evidence, namespace),
            **suggestions_from_evidence(evidence, options)}


def suggestions_from_evidence(evidence, options=None):
    """What a cluster's evidence implies about the generate options, each
    merged against `options`. Nothing is applied."""
    try:
        suggestions = suggest_mod.from_evidence(evidence)
    except ValueError as e:
        raise BadRequest(str(e))
    return {"suggestions": [suggest_mod.merged_as_dict(s, options or {})
                            for s in suggestions],
            "why_nothing": None if suggestions
                           else suggest_mod.why_nothing(evidence)}


def toolcheck(cluster=None, local_registry=None, local_proxy=False):
    """The workstation preflight for the rig flags you mean to pass, as data."""
    checks = workstation.evaluate({"cluster": cluster,
                                   "local_registry": local_registry,
                                   "local_proxy": local_proxy})
    return _verdicts(checks)


def _verdicts(checks):
    """Checks as data. `ok` means no FAIL; a WARN (a denied read) is not one."""
    return {"checks": [c._asdict() for c in checks],
            "ok": not doctor.has_failures(checks)}


# -- the location, as something that gets changed -----------------------------

def reveal_token(client, harbor_id, ship_id):
    """The ship's AUTH_TOKEN as the answer. **This rotates it**: an agent on the
    previous token logs 404 and sits at 0/1."""
    return {"harbor_id": harbor_id, "ship_id": ship_id,
            "auth_token": fetch_ship_token(client, harbor_id, ship_id),
            "warning": "this issued a NEW token and invalidated the previous "
                       "one. Any agent already running for this ship must be "
                       "re-applied with it, Secret included."}


def delete_location(client, harbor_id):
    """Delete a private location and every ship in it, naming what went."""
    harbor = _upstream(client.private_location, harbor_id)
    ships = harbor.get("ships", [])
    _upstream(client.delete_private_location, harbor_id)
    return {"deleted": harbor_id, "name": harbor.get("name"),
            "ships_deleted": [s["id"] for s in ships]}


# -- what is deployed in the namespace ----------------------------------------

SV_READ_MESSAGES = {
    sv_read.SV_READ_NO_CLI:
        "No kubectl or oc on this machine, so the namespace cannot be read "
        "from here. Nothing else in this tool needs one.",
    # One message for several causes; the raw reason travels as `detail`.
    sv_read.SV_READ_NO_CONTEXT:
        "kubectl/oc is installed, but no cluster could be read -- no context "
        "is configured, or the one that is did not answer.",
    sv_read.SV_READ_DENIED:
        "The cluster refused the read -- this context is not allowed to list "
        "pods in that namespace.",
    sv_read.SV_READ_NO_MOCKS:
        "That namespace holds no virtual-service pods. Deploy the virtual "
        "service in BlazeMeter first; this list refreshes on the poll.",
}


def sv_read_message(read):
    """The sentence for an unreadable cluster; an unknown reason falls back to
    the raw detail."""
    return SV_READ_MESSAGES.get(read.status, read.detail)


def sv_mocks(namespace, sv_subdomain=None):
    """What is deployed in `namespace` and the host each answers at. An
    unreadable cluster is a `status`, never an exception or an empty list."""
    read = sv_read.sv_read(namespace)
    return {
        "status": read.status,
        "mocks": [{"name": m["name"], "port": m["port"],
                   "host": service_virt.sv_endpoint_host(
                       m["name"], m["port"], namespace, sv_subdomain)}
                  for m in read.mocks],
        "message": sv_read_message(read),
    }


# -- does the published endpoint answer? --------------------------------------
# A Running mock pod says nothing about routing: crane's nginx Ingress names
# port 8080 while its Service exposes 80, so a strict controller routes nothing
# and the endpoint 503s. That 503 is the finding.

SV_CHECK_OK = "ok"
SV_CHECK_DNS = "dns"
SV_CHECK_REFUSED = "refused"
SV_CHECK_TLS = "tls"
SV_CHECK_TIMEOUT = "timeout"
SV_CHECK_ERROR = "error"

# Under the watch panel's 10s poll, so an answer never lands after the next tick.
SV_CHECK_TIMEOUT_S = 5

# <name>-<port>-<namespace>.<domain>[:port] and nothing else: the host arrives
# from outside, and a path or credentials would make this a general fetcher.
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

SV_CHECK_503 = (
    "HTTP 503 -- the endpoint is published but nothing routes to it, while the "
    "mock pod itself is healthy. That is this cluster rejecting crane's Ingress: "
    "its backend names port 8080 where the Service crane created exposes port "
    "80. Run `bzm-opl-gen sv-expose` where you have cluster access to publish a "
    "Service+Ingress pair that does route.")


def sv_check_reason(err):
    """Classify a probe that got no status line; the four have four fixes."""
    e = getattr(err, "reason", err)      # URLError wraps; a read timeout does not
    if isinstance(e, ssl.SSLError):
        # First: SSLError is itself an OSError.
        return SV_CHECK_TLS
    if isinstance(e, socket.gaierror):
        return SV_CHECK_DNS
    if isinstance(e, TimeoutError):      # socket.timeout is an alias on 3.10+
        return SV_CHECK_TIMEOUT
    if isinstance(e, ConnectionRefusedError):
        return SV_CHECK_REFUSED
    return SV_CHECK_ERROR


def sv_check(host, scheme="http"):
    """Whether a virtual service's published endpoint (a host sv_mocks
    returned) answers. Only an input that is not an endpoint is refused."""
    if scheme not in ("http", "https"):
        raise BadRequest(f"scheme must be http or https, not {scheme!r}")
    if not _SV_HOST_RE.match(host or ""):
        raise BadRequest(f"not an endpoint host: {host!r}")
    url = f"{scheme}://{host}/"
    try:
        # Redirects are followed, as a browser would.
        with urllib.request.urlopen(url, timeout=SV_CHECK_TIMEOUT_S) as r:
            code, detail = r.status, ""
    except urllib.error.HTTPError as e:
        # A status line means something routed and replied -- 503 included.
        code, detail = e.code, str(e)
    except (OSError, http.client.HTTPException) as e:
        status = sv_check_reason(e)
        detail = str(e) or repr(e)
        return {"status": status, "code": None, "url": url, "detail": detail,
                "message": SV_CHECK_MESSAGES.get(status)
                or f"The endpoint could not be reached: {detail}"}
    return {"status": SV_CHECK_OK, "code": code, "url": url, "detail": detail,
            "message": SV_CHECK_503 if code == 503
            else f"HTTP {code} -- the endpoint answered."}


# -- CA trust, checked before deploying -----------------------------------------

def read_ca_bundle(path):
    """The bytes of a CA bundle file. CaBundleUnreadable where it cannot be
    opened; a file that opens and holds no certificate is the lint's FAIL."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as e:
        raise CaBundleUnreadable(
            f"could not read the CA bundle {path}: {e.strerror or e}")


def ca_bundle_pem(data):
    """A CA bundle as the PEM text a bundle carries: DER and PKCS#7 converted,
    CRLF made LF, comments kept."""
    # cert imports cryptography, a compiled extension; only CA work loads it.
    from . import cert
    return cert.normalise(data)


def ca_lint(data):
    """What is wrong with a CA bundle, from the file alone (cert.lint)."""
    from . import cert
    return cert.lint(data)


def ca_bundle_warnings(options):
    """An inline CA bundle's lint findings, as sentences a generate reports.

    Warned, never refused: an inline PEM has always been accepted as given,
    and a FAIL here is a bundle that will not connect, not one that will not
    apply. [] for every other CA mode, which carries no PEM to read.
    """
    ca = ca_trust.resolved_ca(options)
    pem = (options or {}).get("ca_bundle")
    if (ca is ca_trust.CA_UNRESOLVED or not ca or ca["mode"] != "inline"
            or not pem or markers.is_placeholder(pem)):
        return []
    out = []
    for f in ca_lint(pem)["findings"]:
        tail = (" The bundle was written anyway. Fix the CA bundle before "
                "deploying." if f["severity"] == verdict.FAIL else "")
        out.append(f"CA bundle {f['severity']}: {f['message']}{tail}")
    return out


def ca_check_hosts():
    """The hosts ca_check tries by default: the API crane registers with and
    the hosts engines upload results to."""
    return [urllib.parse.urlsplit(footprint.API_BASE).hostname,
            *footprint.ENGINE_UPLOAD_HOSTS]


def ca_check(data, hosts=None, proxy=None, registry=None, env=None,
             timeout=ca_check_mod.TIMEOUT_S):
    """Lint a CA bundle, then verify each host's presented chain with it alone.

    `hosts` replaces ca_check_hosts(); `registry` (a host, or a registry
    prefix like reg.corp:5001/blazemeter) is added to either. `proxy` is an
    http:// URL; without one HTTPS_PROXY and NO_PROXY in `env` decide. Returns
    {lint, hosts, ok}; `ok` is False on a lint FAIL or any not-verified host.
    An unreachable host is no verdict and leaves `ok` alone.
    """
    from . import cert
    targets = list(hosts or ca_check_hosts())
    if registry:
        targets.append(registry.split("/")[0])
    # Every proxy is parsed before the first connection, so a bad URL is a
    # refusal rather than a column of unreachable hosts.
    proxies = []
    for target in targets:
        host, _ = ca_check_mod.split_host(target)
        try:
            proxies.append(ca_check_mod.proxy_for(host, proxy, env))
        except ValueError as e:
            raise BadRequest(str(e))
    lint = cert.lint(data)
    pem = cert.verify_pem(data)
    results = []
    for target, p in zip(targets, proxies):
        r = ca_check_mod.check_host(target, pem, p, timeout)
        results.append({
            "host": r["host"], "port": r["port"], "status": r["status"],
            "detail": r["detail"], "proxy": p.shown if p else None,
            # None for a presented certificate that does not parse.
            "chain": [cert.describe_der(der) for der in r["chain"]],
            "missing_issuer": (cert.missing_issuer(r["chain"], data)
                               if r["status"] == ca_check_mod.NOT_VERIFIED
                               else None)})
    ok = (not any(f["severity"] == verdict.FAIL for f in lint["findings"])
          and not any(r["status"] == ca_check_mod.NOT_VERIFIED
                      for r in results))
    return {"lint": lint, "hosts": results, "ok": ok}


# -- the vocabulary ------------------------------------------------------------

def option_defaults():
    """Bare option -> default, and nothing else: the UI spreads this into the
    options it submits, so any extra key would become an option."""
    return bundle_options.DEFAULT_OPTIONS


def option_docs():
    """What each option is for: its one-line summary, group, type and choices."""
    return {o.name: {"summary": o.summary,
                     "group": o.group,
                     "type": o.type,
                     "nullable": o.nullable,
                     "choices": list(o.choices) if o.choices else None,
                     "secret": o.secret}
            for o in options_mod.OPTIONS}


# The functionalities a bundle can be configured for, one per covered funcId
# (`id` is the funcId). The configure step shows a card each. Labels are the
# account's own display names, written down because this is the keyless answer.
# `namespace` is only a suggestion: one namespace per functionality keeps
# redeploying one agent from touching the other's pods.
FUNCTIONALITIES = [
    {
        "id": "performance",
        "label": "Performance",
        "hint": "load tests -- engines started on demand",
        "namespace": "blazemeter",
    },
    {
        "id": "functionalGui",
        "label": "GUI Functional",
        "hint": "browser tests -- a Selenium grid and browser pods "
                "beside the engine",
        "namespace": "blazemeter-gui",
    },
    {
        "id": "mockServices",
        "label": "Service Virtualization",
        "hint": "virtual services / mocks -- needs an ingress",
        "namespace": "blazemeter-sv",
    },
]


def functionalities():
    """The functionalities the configure step offers, in card order, each with
    `runs_engine` (whether its agent carries a taurus engine)."""
    return [{**f, "runs_engine": facts_mod.runs_engine(f["id"])}
            for f in FUNCTIONALITIES]


def covered_func_ids():
    """The funcIds this tool covers, as {funcId: label}. A function so a
    monkeypatched FUNCTIONALITIES is followed."""
    return {f["id"]: f["label"] for f in FUNCTIONALITIES}


def func_ids(client=None, account_id=None):
    """The funcId vocabulary: `{"source": "account" | "baseline", "choices":
    [{id, label, changes_images, covered, sub_func_ids}]}`.

    The account's list where there is one, else the covered funcIds. Missing
    from an account list means retired; missing from the baseline means
    nothing. `sub_func_ids` are browser pins under their parent.
    """
    covered = covered_func_ids()
    if client is None or account_id is None:
        source, rows = "baseline", [(f, label, []) for f, label in covered.items()]
    else:
        source = "account"
        served = _upstream(client.functionalities, account_id) or {}
        # An unnamed entry is offered under its raw id, as a location shows it.
        rows = [(f["funcId"], f.get("displayName") or f["funcId"],
                 [s["id"] for s in f.get("subFunctionalities") or [] if s.get("id")])
                for f in served.get("functionalities") or [] if f.get("funcId")]
    distinct = set(facts_mod.image_distinct_funcs())
    return {"source": source,
            "choices": [{"id": f, "label": label,
                         "changes_images": f in distinct,
                         "covered": f in covered,
                         "sub_func_ids": subs}
                        for f, label, subs in rows]}


def ignored_options():
    """{format: {option: why}} for options a format cannot carry. Every format
    has an entry; `{}` ignores nothing."""
    return {fmt: dict(table)
            for fmt, table in bundle_options.IGNORED_BY_FORMAT.items()}


def reserved_env():
    """Environment names a bundle writes for itself, as {NAME: owning option or
    None}. `extra_env` refuses every one of them."""
    return {name: bundle_env.ENV_OWNER.get(name)
            for name in sorted(bundle_env.RESERVED_ENV)}


def agent_env(func_ids=None):
    """The agent variables `extra_env` can usefully carry: BlazeMeter's
    reference minus RESERVED_ENV and minus other functionalities' variables.

    `func_ids` None offers everything; a list (even `[]`) offers the untagged
    variables plus those tagged for its funcIds.
    """
    runs = None if func_ids is None else set(func_ids)
    return [dict(v) for v in agent_env_mod.AGENT_ENV
            if v["name"] not in bundle_env.RESERVED_ENV
            and (runs is None or not v["functionalities"]
                 or bool(runs & set(v["functionalities"])))]


def placeholders():
    """Every field a bundle can carry a marker for, as {option: {marker,
    source}}, keyed by option (plus `harbor_id` and `ship_id`). `source` says
    where the real value comes from."""
    return {key: {"marker": markers.marker(key), "source": source}
            for key, source in required_fields.PLACEHOLDER_SOURCE.items()}


def sv_constants():
    """The service-virtualization enumerations a caller must not hardcode:
    funcIds, ingress types, and what each backend publishes."""
    return {"func_ids": list(service_virt.SV_FUNC_IDS),
            "ingress_types": list(service_virt.SV_INGRESS_TYPES),
            # The fields the UI uses; nodeport_ok decides whether NODEPORT is
            # offered beside a backend.
            "backends": {name: {"group": b.group,
                                "resources": list(b.resources),
                                "creates": b.creates,
                                "nodeport_ok": b.nodeport_ok}
                         for name, b in service_virt.SV_INGRESS_BACKENDS.items()}}
