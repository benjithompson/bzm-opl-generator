"""What the tool does, with nothing about how it was asked.

Everything here was inside a FastAPI route handler. That was fine while the web
UI was the only caller, and stopped being fine for two reasons.

The first is testing. The decisions worth checking -- which ship a token is
fetched for, which namespace a preflight is about, what counts as an agent
being online -- were reachable only through a TestClient, so `tests/test_server`
importorskips fastapi and a venv without it reported a clean pass having tested
none of them.

The second is that a second caller is coming. An MCP server speaks JSON-RPC over
stdin, has no request object and no status codes, and would otherwise have had
to restate this orchestration -- which is how the token-fetch rule came to exist
twice already, once here and once in `cli.py`, each looking obviously right.

So: no fastapi, no pydantic, no request or response objects, stdlib and this
package only. Failures are `CoreError`, which carries the status code the web
layer answers with -- the code belongs to the refusal, not to the route, or the
same refusal answers 400 on one endpoint and 500 on another.

What is deliberately *not* here: holding a client. Each transport owns its own
credential lifetime and the remedy it names when there is none -- a browser
posts to a form, a stdio server is restarted with a different environment.

A bundle's *delivery* is here, which reads like an exception and is not. Which
container it arrives in is the transport's (the UI streams a zip, the CLI
prints what it wrote), but `zip_bundle`, `write_bundle`, `read_bundle_file` and
`redact_tokens` carry rules no transport should get to re-decide: that a name
cannot escape the directory it is read from, that a written path is absolute,
that a token is blanked on the way out. Those are the same rules for every
caller, and the one that has them wrong is the one that would never say so.
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
import urllib.request
import zipfile

from . import (api, doctor, evidence as evidence_mod, facts as facts_mod,
               generate as gen_mod, livetest, options as options_mod, plan,
               suggest as suggest_mod, workstation)


# -- failures ------------------------------------------------------------------

class CoreError(Exception):
    """A refusal, with the HTTP status the web layer answers it with.

    Carried rather than decided per route so that a caller with no status codes
    at all -- stdio JSON-RPC -- still gets the distinction between "you sent
    the wrong thing" and "BlazeMeter did".

    500 on the base, not 400: a subclass that names no status is a mistake in
    here, and blaming the caller for it is how a bug gets reported as bad
    input. Raise one of the subclasses.
    """
    status = 500


class BadRequest(CoreError):
    status = 400


class NotFound(CoreError):
    status = 404


class NotConfigured(CoreError):
    """No usable credential, and the message says how to supply one.

    Its own type rather than a BadRequest because what to do about it is not
    "you sent the wrong thing" -- nothing about the call was wrong. 401 is what
    a web layer would answer, though only the MCP server raises it today: the
    UI holds its client in session state and decides this for itself, since the
    remedy there is a form rather than an environment.
    """
    status = 401


class EvidenceUnreadable(CoreError):
    """A cluster-evidence file the caller named and nothing could be read from.

    Its own type, and deliberately neither a BadRequest nor a NotFound, because
    "could not read it" and "read it and it is not evidence" are the pair this
    package has collapsed four times over. They have opposite remedies -- fix
    the path, or get the file sent at all, versus stop pointing this at a facts
    file -- so a transport catching one type for both would answer with
    whichever sentence it happened to be holding.

    400 rather than 404: the file is often there and merely unparseable, and
    what the caller has to change is the argument either way.
    """
    status = 400


class UpstreamError(CoreError):
    """BlazeMeter answered, and what it said was an error. 502 because the
    caller's request was fine; something upstream of us was not."""
    status = 502


class TokenRefused(UpstreamError):
    """BlazeMeter would not issue an agent credential.

    An UpstreamError because that is what it is -- nothing the caller sent was
    wrong, and on an account that restricts the token endpoint no argument to it
    would have worked -- but its own type so the message can be written here
    instead of being the endpoint's body. `.upstream` keeps that body, because
    the point is to say more than it does, not less: see fetch_ship_token.
    """

    def __init__(self, message, upstream):
        super().__init__(message)
        self.upstream = upstream


def _upstream(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except api.BzmApiError as e:
        raise UpstreamError(str(e))


# -- where an API key might be ------------------------------------------------

CONFIG_DIR = os.path.expanduser("~/.config/bzm-opl-gen")
SAVED_KEY_PATH = os.path.join(CONFIG_DIR, "api-key.json")


def key_candidates():
    """The paths an api-key.json is looked for, in precedence order.

    A function rather than a constant because BZM_API_KEY_FILE is read from the
    environment: as a module-level list it froze at import, which is wrong for
    anything that sets the variable after startup -- `ui --dev` does exactly
    that, passing the key to its reloader subprocess.
    """
    return [os.environ.get("BZM_API_KEY_FILE"),
            "api-key.json",
            SAVED_KEY_PATH,
            os.path.expanduser("~/.bzm/api-key.json")]


KEY_ID_ENV = "BZM_API_KEY_ID"
KEY_SECRET_ENV = "BZM_API_KEY_SECRET"
KEY_FILE_ENV = "BZM_API_KEY_FILE"


def client_from_env(api_key_file=None, *, key_id=None, secret=None):
    """The construction: a BzmClient from a path, an id and secret, or the
    environment -- and a CoreError, never anything else, when there is none.

    Precedence, and each step is somebody's real input: the path in the
    argument (a `--api-key` flag, an MCP tool argument, a file picked in the
    page), then an id and secret in the argument (typed into the connect form,
    where there is no file), then KEY_FILE_ENV, then the id/secret pair in the
    environment. Nothing is discovered from the working directory -- see the
    comment at the refusal for why that matters here and not for a command.

    Widened rather than joined by a sibling (#92). The promise worth having in
    one place is the one this already made -- never SystemExit, because the
    file-reading constructor raises one and a BaseException walks past every
    `except Exception` between a route and the top of a server process -- and a
    second function making the same promise about a different input is the
    thirteen constructions again, one order of magnitude down. The name is now
    narrower than the function; renaming it belongs to the half of this that
    migrates every call site at once (#95), because a rename today either
    breaks the point all three suites stand in at or adds the second name this
    was written to remove.

    That widening spends a rule this docstring used to state: that a secret is
    never an argument. It still holds where it was argued -- `mcp_server`
    passes a path and nothing else, and an argument there has travelled through
    a model's context to get here. It does not hold for the UI, whose key
    arrives pasted into a form with no file behind it: `key_set` writes it to a
    temp file purely to have a path for a constructor that takes only one, and
    a secret written to disk to satisfy an argument list is worse than the
    argument.

    Not "connected" in the sense of having reached BlazeMeter -- there is no
    connection to make, the client is stateless HTTP Basic. Proving the
    credential works is `user(client)`, one call, made by the callers that need
    the proof; making it here would put a network round-trip inside every
    construction and a second one inside most.
    """
    if key_id or secret:
        if api_key_file:
            # Both, in one call, and only ever as arguments -- a key in the
            # environment losing to one the caller named is precedence, but two
            # in the same call is a caller that does not know which account it
            # is about, and taking one silently is how a bundle gets built
            # against the wrong one with nothing anywhere saying so.
            raise BadRequest("give an API key file path, or an id and secret, "
                             "not both")
        if not (key_id and secret):
            # Half a pair gets its own sentence: this caller plainly has a key
            # and is one field short, and the "no API key anywhere" message
            # below -- which talks about environment variables -- answers a
            # question it did not ask.
            missing = "secret" if key_id else "id"
            raise BadRequest(f"an API key needs both an id and a secret; "
                             f"the {missing} is missing")
        return api.BzmClient(credentials=(key_id, secret))
    path = api_key_file or os.environ.get(KEY_FILE_ENV)
    if path:
        try:
            return api.BzmClient(credentials=api.read_key_file(
                os.path.expanduser(path)))
        except ValueError as e:
            # Every failure read_key_file has is a ValueError, deliberately:
            # an OSError or a UnicodeDecodeError escaping as itself is a bare
            # exception out of a route.
            raise NotConfigured(str(e))
    key_id = os.environ.get(KEY_ID_ENV)
    secret = os.environ.get(KEY_SECRET_ENV)
    if key_id and secret:
        return api.BzmClient(credentials=(key_id, secret))
    # Deliberately no fall back to detect_keys(): its first candidate is
    # `./api-key.json`, which is fine for a command someone ran in their own
    # checkout and wrong for a server whose working directory is wherever a
    # client launched it -- a customer's project, quite possibly holding an
    # api-key.json that is theirs. Asking is cheap; using the wrong account is
    # not. The UI does its own detection, where the person can see the path.
    raise NotConfigured(
        f"no BlazeMeter API key. Set {KEY_FILE_ENV} to the path of an "
        f"api-key.json ({api.KEY_FILE_SHAPE}), or {KEY_ID_ENV} and "
        f"{KEY_SECRET_ENV}, in the environment of whatever started this. "
        f"Create the key under Settings -> API Keys in BlazeMeter.")


def detect_keys():
    """Which of those exist and parse, with the key id each holds.

    The secret is never read back out -- only the id, which is what identifies
    a key without being able to act as one.
    """
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
    """Who this key is. Also the cheapest call that proves it works, which is
    what every caller uses it for."""
    return _upstream(client.user)


def accounts(client):
    return _upstream(client.accounts)


def workspaces(client, account_id):
    return _upstream(client.workspaces, account_id)


def require_location_scope(account_id=None, workspace_id=None):
    """A locations listing has to be scoped to something.

    Separate from locations(), rather than only inside it, because /api/locations
    has a credential to check as well and the order between the two is visible:
    a request naming neither scope is malformed with or without a key, so
    answering "no API key" sends the person off to configure one and then
    refuses them anyway. Asking this first is what keeps that 400 a 400.
    """
    if not account_id and not workspace_id:
        raise BadRequest("account_id or workspace_id required")


def locations(client, account_id=None, workspace_id=None):
    require_location_scope(account_id, workspace_id)
    return _upstream(client.private_locations, account_id, workspace_id)


def location(client, harbor_id):
    """One location with its ships -- the per-ship detail a listing leaves out,
    paid for on the one the caller has chosen."""
    return _upstream(client.private_location, harbor_id)


# How many locations a listing hands back when the caller did not say. The
# account this was built against has two; a customer's has 171 with 221 ships,
# which came back as 84,779 characters -- past an MCP caller's result ceiling,
# so it was truncated to a file and never read. A cap is only safe because
# select_locations counts what it left out; see its docstring.
DEFAULT_LOCATION_LIMIT = 50


def select_locations(locs, name_contains=None, limit=DEFAULT_LOCATION_LIMIT):
    """Narrow a listing, and account for every location that does not come back.

    The counts are the point, not decoration. A caller handed 50 of 171 with no
    numbers reads the account as having 50, and then reports a location that is
    right there as missing -- which is a worse failure than the response being
    too big, because it looks like an answer. Filter and cap are counted apart:
    one is what the caller asked for and the other is not, so only the numbers
    say which is worth undoing.

    Here rather than in a response layer because what a partial answer owes its
    caller is the same wherever the request came from, while how each entry is
    *shaped* differs by caller and belongs to them. The CLI deliberately does
    not narrow -- a terminal scrolls, and it is the caller with a result ceiling
    that cannot afford 171 of these -- so `limit=None` (no cap at all) exists
    for it and for anyone else who genuinely wants the lot.
    """
    if limit is not None:
        # Before the comparison, because a model writing `"10"` is likelier
        # than one writing 10, and `"10" < 1` is a TypeError -- not a CoreError,
        # so it escapes the transports as an internal error instead of a
        # sentence. bool is an int subclass and `limit=True` is nobody's intent.
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise BadRequest(
                f"limit must be a whole number, not {limit!r}. Omit it for the "
                f"default of {DEFAULT_LOCATION_LIMIT}")
        if limit < 1:
            # `limit=0` means "no limit" to whoever passed it and "nothing
            # matched" in the answer; nothing good is downstream of guessing.
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


def create_location(client, name, account_id, workspace_id,
                    func_ids=("performance",), slots=1,
                    threads_per_engine=api.DEFAULT_THREADS_PER_ENGINE):
    return _upstream(client.create_private_location, name, account_id,
                     [workspace_id], func_ids=list(func_ids), slots=slots,
                     threads_per_engine=threads_per_engine)


def create_ship(client, harbor_id, name):
    return _upstream(client.create_ship, harbor_id, name)


def add_func_id(client, harbor_id, func_id):
    """Turn a feature on for a location, and hand the location back.

    An agent serves what its location says it runs, so a bundle configured for
    mock services against a location that does not carry mockServices deploys
    cleanly and is never asked to serve one. This is the one call that changes
    that, and it is additive by construction: the PATCH replaces `funcIds`
    wholesale, so it is built from what the location already has. Sending the
    single funcId the caller asked for is how a location that ran performance
    and mocks comes back running only mocks.

    Idempotent -- already present is not an error, it is the answer -- and it
    reads the location first for that reason rather than trusting a caller's
    copy, which may be a list a browser has been holding for an hour.
    """
    loc = _upstream(client.private_location, harbor_id)
    have = list(loc.get("funcIds") or [])
    if func_id in have:
        return loc
    return _upstream(client.update_private_location, harbor_id,
                     func_ids=have + [func_id])


# The location settings this tool will change, as {name: the field BlazeMeter
# calls it}. A closed set on purpose: `funcIds` is add_func_id's, which is
# additive by construction, and a general "PATCH whatever you send" would let a
# caller replace it wholesale by accident -- the exact failure that function
# exists to prevent.
LOCATION_SETTINGS = {
    "slots": "slots",
    "threads_per_engine": "threadsPerEngine",
    "override_cpu": "overrideCPU",
    "override_memory": "overrideMemory",
}


def update_location(client, harbor_id, **settings):
    """Change a location's concurrency settings, and report what actually took.

    The case this exists for: the location and its agent were set up, a test
    was planned against 500 virtual users per engine, and the real figure turns
    out to be 1,000. That is a change to the *location*, not to the bundle --
    none of these four values appears in a manifest, so nothing has to be
    regenerated, re-applied or restarted for a new one to take effect on the
    next test start.

    **Reads back rather than trusting the write.** The answer names, per field,
    what it was, what was asked for and what the location says afterwards --
    because this API has already been caught accepting a field and not storing
    it: `create_private_location` sends threadsPerEngine to POST, which ignores
    it, and the location comes back null and 403s every test start. A UI that
    reported the request as the outcome would show the number the user typed
    while the account held something else, which is the same failure wearing a
    tick. `ignored` is what came back unchanged.

    Unknown settings are refused rather than passed through: a typo in a field
    name would otherwise be a silent no-op that this function then reported as
    "nothing changed", which reads as the account rejecting a legitimate value.

    `None` means "leave this one alone", so there is deliberately no way to
    *clear* a setting here -- an override that has been set can be changed but
    not unset. Clearing one is a different intent from not mentioning it, and
    collapsing the two is how a partial update wipes a field nobody named.
    """
    unknown = sorted(set(settings) - set(LOCATION_SETTINGS))
    if unknown:
        raise BadRequest(
            f"not a location setting: {', '.join(unknown)} -- this changes "
            f"{', '.join(sorted(LOCATION_SETTINGS))}. Features are "
            f"add_func_id's, which is additive; anything else is BlazeMeter's "
            f"own UI")
    wanted = {k: v for k, v in settings.items() if v is not None}
    before = _upstream(client.private_location, harbor_id)
    # Snapshotted here, not after the write. Reading the four values out of
    # `before` further down would be right only for as long as the client hands
    # back a document nothing else holds a reference to -- and "what it was" is
    # the one thing that cannot be re-derived once the PATCH has landed.
    was = _settings_of(before)
    if not wanted:
        # Not an error: a form submitted with nothing changed is a no-op, and
        # answering with the location keeps one shape for every caller.
        return {"location": before, "changed": {}, "ignored": [],
                "before": was, "after": dict(was)}
    _upstream(client.update_private_location, harbor_id, **wanted)
    # A second GET rather than the PATCH's own body: the response to a write is
    # what the write claimed, and what this has to report is what the account
    # now holds.
    after = _upstream(client.private_location, harbor_id)
    now = _settings_of(after)
    changed = {k: now[k] for k in wanted if now[k] != was[k]}
    ignored = sorted(k for k in wanted if now[k] == was[k] and wanted[k] != was[k])
    return {"location": after, "changed": changed, "ignored": ignored,
            "before": was, "after": now}


def _settings_of(location):
    """The four settings as this tool names them, from a location document."""
    return {name: location.get(field) for name, field in LOCATION_SETTINGS.items()}


def issue_auth_token(client, harbor_id, ship_id):
    """Mint a new AUTH_TOKEN for an existing agent, and return it.

    Named for the effect, like rotate_auth_token: the endpoint is a fetch and
    what it does to an agent already running on the previous credential is
    revoke it. Separated from resolve_auth_token's rotation branch because the
    two answer different questions -- that one asks what a *bundle* should
    carry, this one is a person deciding to replace a credential nobody kept,
    with nothing generated yet.

    The caller is expected to have said what it costs first; core does not
    confirm, it performs.
    """
    return fetch_ship_token(client, harbor_id, ship_id)


def gather_facts(client, harbor_id):
    return _upstream(facts_mod.gather, client, harbor_id)


def manual_facts(harbor_id, ship_id, func_ids=("performance",)):
    """Facts from the three values BlazeMeter shows on the agent, with no API
    key involved -- the case where you are producing manifests for a customer's
    cluster and have access to neither their account nor their cluster.

    Takes no client on purpose: requiring one here would defeat the point. It
    reads nothing and writes nothing; it only fills in the shape `gather` would
    have returned.

    The ids are not validated. There is nothing here to validate them against,
    and a guess at their format would reject input that is correct.
    """
    facts = facts_mod.manual(harbor_id, ship_id, func_ids=list(func_ids))
    return {"facts": facts,
            # Carried rather than left for the caller to notice -- see
            # facts.gui_images_incomplete for what it means.
            "gui_images_incomplete": facts_mod.gui_images_incomplete(facts)}


# How stale a heartbeat may be and still count as online. Two poll intervals of
# the agent's own reporting: one missed beat is a slow network, two is an agent
# that has stopped.
HEARTBEAT_FRESH_S = 120
ONLINE_STATES = ("idle", "running")


def ship_reporting(ship):
    """Whether one ship is reporting now -- or None where the payload cannot say.

    None is not "no". A locations *listing* is not the same read as a single
    location, and a payload that never carried `lastHeartBeat` is no evidence
    that an agent has stopped: answering False there would have a session
    redeploy an agent that is working. Present-and-stale is False; absent is
    unknown, and the caller has to go and ask.
    """
    if "lastHeartBeat" not in ship:
        return None
    hb = ship.get("lastHeartBeat") or 0
    return bool(hb and time.time() - hb < HEARTBEAT_FRESH_S
                and ship.get("state") in ONLINE_STATES)


def agent_status(client, harbor_id, ship_id):
    """Is this agent actually reporting, as opposed to merely remembered?

    `state` alone would read as healthy forever: an agent that stops reporting
    keeps whatever state it last had, so the heartbeat is what separates a live
    agent from a record of one.
    """
    harbor = _upstream(client.private_location, harbor_id)
    ship = next((s for s in harbor.get("ships", []) if s["id"] == ship_id), None)
    if not ship:
        raise NotFound(f"ship {ship_id} not in location {harbor_id}")
    hb = ship.get("lastHeartBeat") or 0
    return {
        "state": ship.get("state"),
        # None, not a huge number: an agent that has never reported is a
        # different thing from one that reported a long time ago, and the two
        # want different next steps.
        "heartbeat_age_s": int(time.time() - hb) if hb else None,
        "installed_version": ship.get("installedVersion"),
        # A bool, and it does not carry the third case on its own: an agent that
        # has never reported answers False here exactly as one that went quiet
        # does. `heartbeat_age_s` above is what separates them -- null for the
        # first, a number for the second -- and it is the pair a caller reads,
        # which is why the two get different `next` steps and why a test pins
        # that rather than this comment promising it. Not widened to null:
        # `online` is a boolean in the web API's own contract, and squeezing
        # "unknown" into it would break a consumer that already branches on it.
        "online": bool(ship_reporting(ship)),
    }


# -- the agent credential ------------------------------------------------------

# The way forward when the fetch cannot happen at all. Worth stating in the
# refusal rather than left to the reader: BlazeMeter shows the token on the agent
# itself, and a bundle built with one supplied fetches nothing (token_ship_id
# returns None), so a closed endpoint stops nothing except the convenience.
#
# There is one sentence for this and it is token_recovery_hint, below. There were
# two, and `resolve_auth_token` used one in its no-client branch and the other
# everywhere else -- so which sources a caller was told about depended on which
# way the same function had failed. The refusal path was the worse of the two: it
# named the BlazeMeter UI and not the agent already deployed, which is the one
# source needing no account access, and an account that just refused the endpoint
# is exactly the thing the caller cannot rely on.
TOKEN_CANNOT_BE_FETCHED = (
    "The AUTH_TOKEN can be supplied instead of fetched, and a bundle built with "
    "one supplied fetches nothing, so a closed endpoint costs only the "
    "convenience.")


def fetch_ship_token(client, harbor_id, ship_id):
    """The ship's AUTH_TOKEN, or a refusal that says what to do without one.

    The single place the token endpoint is called, because what is interesting
    about it is the failure. Some accounts refuse /docker-command outright --
    observed as `HTTP 403 {"message": "Forbidden: Should access from Private-Data
    gateway"}` -- and through the generic upstream wrapper that body was the
    whole message: it names no ship, does not distinguish the credential fetch
    failing from the operation the caller asked for being wrong, and offers
    nothing to do next, so an MCP session that hit it could not get past it.

    The body still travels, in the message and on `.upstream`. It is the only
    clue that the account is configured this way deliberately, and a refusal
    that hid it would just be a differently-unhelpful message.
    """
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
    """The ship an operation is about, when the caller did not name one.

    None means "say which", never "the first one": a location with two agents
    has no default, and every caller of this does something to the ship it gets
    back -- fetch its token, deploy against it, watch it -- so picking one by
    position acts on an agent nobody mentioned. Three call sites had their own
    copy of this, which agreed, which is the only reason it was not a bug.
    """
    ships = facts.get("ships") or []
    return explicit or (ships[0]["id"] if len(ships) == 1 else None)


def token_ship_id(facts, options):
    """Which ship an AUTH_TOKEN would be *rotated* for, or None for no rotation.

    Minting a token rotates it -- the previous one stops working, and whatever
    agent holds it sits at 0/1 Running -- so this adds one clause to
    sole_ship_id: a token already in the options is the caller's, and must never
    be replaced by a fresh one that breaks their running agent.

    Where the ship is ambiguous nothing is minted; resolve_auth_token refuses
    the ambiguity by name when a rotation was actually asked for, and otherwise
    generate() refuses it, with a sentence naming both ships.
    """
    if options.get("auth_token"):
        return None
    return sole_ship_id(facts, options.get("ship_id"))


def rotate_auth_token(client, facts, options):
    """Put a freshly-minted AUTH_TOKEN into `options`, if one is wanted.

    Returns the ship it was minted for, or None if nothing was -- which is what
    a caller that wants to say so out loud needs, and is why the call is here
    rather than inlined into the resolution. It mutates `options`, because every
    caller's next move is to render from them.

    Named for what it does to the account rather than for the HTTP verb: the
    endpoint is a fetch and the effect is a rotation, and it was the verb that
    made `fetch_token=True` read like a harmless default for years.
    """
    ship_id = token_ship_id(facts, options)
    if ship_id:
        options["auth_token"] = fetch_ship_token(client, facts["harbor_id"],
                                                 ship_id)
    return ship_id


# Which of the four ways a bundle's AUTH_TOKEN arrived. Named, and reported, so
# that every caller can say which happened without restating the rule: the four
# have wildly different consequences for an agent that is already running, and
# the one that revokes its credential used to be the default and to say nothing
# at all (the MCP surface answered `warnings: []`).
TOKEN_GIVEN = "given"
TOKEN_ROTATED = "rotated"
TOKEN_REUSED = "reused"
TOKEN_PLACEHOLDER = "placeholder"

# `message` is always a sentence for whoever asked -- there is no branch worth
# taking silently. `ship_id` is the ship the token belongs to where that is
# known, following rotate_auth_token's precedent of handing the ship back for a
# caller that wants to name it (an affordance that existed and was discarded by
# both callers, which is how a rotation got reported as `warnings: []`).
#
# Serialisable as it stands -- `_asdict()` is what both transports answer with,
# rather than either of them listing the three fields again. All three are safe
# to put in a response: none of the four messages carries a token value, which is
# the only reason this can be a response field at all, and a transport composing
# its own summary from `branch` would be a second copy of the rule above in
# whatever language it was written in.
TokenSource = collections.namedtuple("TokenSource", "branch ship_id message")


def rotation_warning(ship_id):
    """What a rotation is about to do, for a caller to say *before* it happens.

    Before, because afterwards this is a post-mortem: the credential is already
    dead and the pod is already broken. And it has to be said at all because the
    failure is silent at every layer -- crane answers a dead token with 404 on
    /versions, logs `Sleeping for 300` and never starts its health service, so
    the pod sits `0/1 Running` and reads as a slow boot rather than as a revoked
    credential. That cost a live debugging session.
    """
    return (
        f"ROTATING the AUTH_TOKEN for ship {ship_id}: BlazeMeter issues a new "
        f"one and the previous one stops working immediately. Any agent already "
        f"running on it will start answering 404 and sit at `0/1 Running`, "
        f"which looks like a slow boot, until you re-apply this bundle -- "
        f"Secret included.")


def token_recovery_hint(options=None):
    """Where a real AUTH_TOKEN comes from, for a bundle that has no token.

    Two sources, and neither of them is this tool going and getting one: what
    `create-ship` printed when the agent was made -- the durable copy, and the
    reason that command prints it -- or the Secret of an agent already running.
    The kubectl for the second is *named*, never run: nothing in this package
    reads a cluster to build a bundle, and the person at the terminal is the one
    who can see what their own cluster answers.
    """
    o = options or {}
    ns = o.get("namespace") or gen_mod.DEFAULT_OPTIONS["namespace"]
    # Named in every register, because this sentence is not the CLI's: the web UI
    # renders it verbatim under the download button and an MCP session quotes it
    # back. A tail that said only `--auth-token` told a browser to type a flag it
    # has no prompt for -- so the *option* leads, and each surface's own spelling
    # of it follows in brackets.
    return (
        f"A real one comes from what was shown when the agent was created "
        f"(`create-ship` prints it; the web page puts it in the field) -- keep "
        f"it, nothing here stores it -- or from the agent's install command in "
        f"the BlazeMeter UI (Settings -> Private Locations -> the location -> "
        f"the agent), or out of an agent already deployed:\n"
        f"    kubectl -n {ns} get secret {gen_mod.SECRET_NAME} "
        f"-o jsonpath='{{.data.AUTH_TOKEN}}' | base64 -d\n"
        f"  Supply it as the bundle's auth_token -- `--auth-token` on the "
        f"command line, the AUTH_TOKEN field on the web page -- and the bundle "
        f"is complete. Issuing a fresh one instead (`--rotate-token`, or the "
        f"tick-box that says so on the page) takes down whatever is running on "
        f"the current one until you re-apply.")


def _bundle_ship_id(out_dir):
    """Which ship the bundle in `out_dir` was generated for, or None.

    Absent and unreadable collapse into None here, deliberately: both mean the
    ship cannot be *confirmed*, and the remedy for both is the same -- do not
    reuse that directory's token. What must stay apart, and does, is either of
    those from a ship id that is present and different, which is the value
    itself and gets a refusal naming both ships.
    """
    try:
        return gen_mod.load_profile(out_dir).get("ship_id") or None
    except (OSError, ValueError):
        # FileNotFoundError for a directory no generate has written, ValueError
        # for a profile.json that will not parse.
        return None


def resolve_auth_token(facts, options, client=None, rotate=False, out_dir=None,
                       announce=None):
    """Put the AUTH_TOKEN into `options`, and say which of four ways it arrived.

    The one copy of #64's rule, in precedence order:

      1. a token already in the options wins outright -- it is the caller's, and
         replacing it is what broke running agents;
      2. `rotate` mints a new one, and is the only thing here that does;
      3. otherwise the token already written into `out_dir` is reused, but only
         if that bundle names the same ship;
      4. otherwise the placeholder stays, with a message saying where a real
         token comes from.

    `announce` is called with `rotation_warning(...)` immediately before the
    mint. A parameter rather than something each caller remembers to do first,
    because the ordering is the whole value of the warning and only this
    function knows when the call is about to happen. A caller with nowhere to say
    it -- JSON-RPC, where stdout is the protocol -- leaves it unset and reports
    `.message` afterwards.

    Idempotent: resolving twice takes branch 1 the second time, which is what
    lets a caller that wants the report resolve here and still hand the options
    to generate_bundle.

    `out_dir` need not exist and need not be absolute -- it is read, not
    written; write_bundle is where the absolute-path rule belongs.
    """
    placeholder = gen_mod.DEFAULT_OPTIONS["auth_token"]
    held = options.get("auth_token")
    if held and held != placeholder:
        # A rotation asked for *alongside* a token is a contradiction with one
        # safe reading, so it is answered rather than refused: minting and then
        # writing the supplied value over it would kill the agent holding the
        # supplied one and put nothing usable in the bundle. Said out loud,
        # because a flag that was quietly dropped is the shape of this whole bug.
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
            # Refused, not warned. Reusing across ships would write another
            # agent's credential into this bundle -- but simply declining to
            # reuse is not enough, because the next thing that happens is
            # write_bundle overwriting this directory, and the API only ever
            # mints: that bundle was the only copy of that token outside a
            # running cluster. So carrying on with a warning trades one silent
            # 0/1 for a credential nothing can get back, which is worse. The
            # escape is to say what *this* bundle's token is -- a supplied or
            # rotated token never reads the directory at all -- so replacing
            # another ship's bundle stays available to whoever means it.
            # Three ways to get here, and each names what is actually unknown.
            # `want` is None when the location has several agents and none was
            # named -- reporting that as "not None" invents a ship and buries the
            # remedy, which is to say which one this bundle is for.
            if theirs and not want:
                named = (f"a bundle for ship {theirs}, and nothing here says "
                         f"which ship the new one is for -- this location has "
                         f"several agents, so pass ship_id (--ship-id)")
            elif theirs:
                named = f"a bundle for ship {theirs}, not {want}"
            else:
                named = (f"a bundle whose {gen_mod.PROFILE_FILE} does not say "
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


def generate_bundle(facts, options=None, client=None, rotate_token=False,
                    out_dir=None):
    """The manifests, as {name: content}.

    `client=None` is a first-class case, not a degraded one: the manual-entry
    path has no account to ask, and holding a client is no longer permission to
    mint -- `rotate_token=True` is, and it is the caller saying "replace the
    credential of whatever is running", which is why it defaults to off.

    `out_dir` is where the bundle is about to be written, and is read for the
    token its predecessor holds. A caller that wants to report which branch the
    resolution took calls resolve_auth_token itself and passes the options on;
    doing both is harmless, since a resolved token wins outright the second time.
    """
    opts = dict(options or {})
    resolve_auth_token(facts, opts, client=client, rotate=rotate_token,
                       out_dir=out_dir)
    try:
        return gen_mod.generate(facts, opts)
    except (ValueError, KeyError) as e:
        # Every refusal generate() makes is a sentence written for the person
        # who set the option, so it travels as-is rather than being summarised.
        raise BadRequest(str(e))


def preview_order(files):
    """Which file to read first, and the rest after it.

    A generator decision, not a presentation one -- a helm bundle leads with
    the values overlay because it is the only file in a chart that came from
    the account -- so it is offered here rather than left for each caller to
    remember to reach past core for. A `def` rather than
    `preview_order = gen_mod.preview_order`, because an alias is a second name
    that stops tracking the first one the moment anything replaces it.
    """
    return gen_mod.preview_order(files)


ZIP_PREFIX = "bzm-opl"


def zip_bundle(files, prefix=ZIP_PREFIX):
    buf = io.BytesIO()
    # Names may carry directories (the helm format emits a chart), which zip
    # stores as-is -- the slash is the path separator in the archive too.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in preview_order(files):
            info = zipfile.ZipInfo(f"{prefix}/{name}")
            if name.endswith(".sh"):
                info.external_attr = 0o755 << 16
            z.writestr(info, files[name])
    return buf.getvalue()


def zip_filename(options):
    return f"{ZIP_PREFIX}-{(options or {}).get('namespace', 'blazemeter')}.zip"


def require_absolute_out_dir(out_dir):
    """Refuse a relative bundle directory, and say why it cannot be one.

    Absolute paths only. Every caller of this but a shell is somewhere it did
    not choose -- a server's working directory is whatever launched it -- so a
    relative path resolves against a directory nobody named, and the files turn
    up somewhere the caller then cannot describe.

    Reachable on its own, and not only from write_bundle, because a caller that
    might *rotate* the AUTH_TOKEN has to fail this before it mints: the refusal
    used to arrive at the write, by which point a running agent's credential had
    already been revoked over a mistake in an argument that has nothing to do
    with the credential. One copy of the rule, two moments it can be applied.
    """
    if not os.path.isabs(out_dir):
        raise BadRequest(
            f"out_dir must be an absolute path, not {out_dir!r} -- a relative "
            f"one resolves against this process's working directory, which is "
            f"whatever started it rather than anywhere you chose")
    return out_dir


def write_bundle(files, out_dir):
    """Write a generated bundle to `out_dir`, and say what landed where.

    Returns [{name, bytes}] rather than the content: a bundle is ~40KB of YAML
    with a CA bundle sometimes far larger, and a caller that wanted to read one
    file should read that one file.
    """
    require_absolute_out_dir(out_dir)
    gen_mod.write(files, out_dir)
    return [{"name": n, "bytes": len(files[n].encode())} for n in preview_order(files)]


# Matched by field name rather than by value, because a reader has no idea what
# the value is. The names come from generate.TOKEN_FIELDS -- the module that
# writes them -- rather than being restated here, which is how the reader and
# the redactor came to know different sets in the first place.
_TOKEN_FIELDS = re.compile(
    r'^(?P<lead>\s*(?:' + "|".join(gen_mod.TOKEN_FIELDS) +
    r')\s*:\s*)(?P<quote>["\']?)(?P<value>.+?)(?P=quote)\s*$', re.M)
REDACTED = "<redacted -- opl_location reveal_token>"


def redact_tokens(text):
    """Blank out any AUTH_TOKEN a bundle file carries, and say how many.

    For readers that hand a whole file to somebody: the point of keeping the
    token out of responses is that responses are transcribed and quoted back,
    and that is no less true of one fetched by name. What a reader actually
    wants from the Secret is that it is there and shaped right, which survives
    redaction; the value itself has a call of its own that says out loud that
    asking for it rotates it.
    """
    return _TOKEN_FIELDS.subn(lambda m: f"{m.group('lead')}\"{REDACTED}\"", text)


def read_bundle_file(out_dir, name):
    """One file out of a written bundle, by the name write_bundle reported.

    The name is joined and then checked to be inside `out_dir`, because it
    arrives from outside: `../../.ssh/id_rsa` is a name too, and this would
    otherwise be a general-purpose file reader with a bundle-shaped argument.
    Checked after normalising rather than by scanning for "..", which misses
    symlinks and absolute names.
    """
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


def mirror_images(refs, mirror=None, platform="linux/amd64", dry_run=False):
    """Pull each image and, with `mirror`, push it under that prefix.

    The push is why this is not a read: it writes to somebody's registry. The
    pull is not free either -- BlazeMeter images are amd64-only and large, and
    `platform` is explicit because on an arm64 host docker will otherwise pick
    a manifest that does not exist and fail halfway through the set.

    Returns what it ran, so a dry run is a plan somebody can read and then run
    by hand -- which is what the bundle's own mirror script is for.
    """
    ran = []
    for ref in refs:
        ran.append(_docker(["pull", "--platform", platform, ref], dry_run))
        if mirror:
            # Last path segment only: the target registry has its own
            # namespace, and carrying the source project into it produces
            # repositories nobody asked for.
            target = f"{mirror.rstrip('/')}/{ref.rsplit('/', 1)[-1]}"
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
    """Every image reference this location's bundle will pull. See
    facts.image_refs, which is where the crane-first rule lives."""
    return facts_mod.image_refs(facts, all_images=all_images)


# -- planning, before any of the above exists ---------------------------------

def capacity_plan(users, vus_per_engine=None, engine_cpu=None,
                  engine_mem=None, engines_per_node=None, agents=None):
    """What a load target needs, as numbers and as a document to request it with.

    The only thing here that reaches nothing at all -- no key, no account, no
    cluster, no evidence file. That is deliberate and it is the whole case:
    this is used *before* there is an account to connect to or a cluster to
    preflight, by somebody who has to raise a ticket for the infrastructure the
    rest of this tool assumes. Putting it behind a credential would put the
    first step behind the last one.

    The document comes back with the numbers rather than from a second call.
    Both describe one plan, and two round trips is two answers that can end up
    describing different ones -- the same reason preflight() returns its
    suggestions alongside its verdicts.
    """
    try:
        # Blanks are forwarded as they arrive: what "not given" defaults to is
        # plan's, and restating it here was a second copy that could drift.
        p = plan.capacity_plan(
            users, vus_per_engine=vus_per_engine,
            engine_cpu=engine_cpu, engine_mem=engine_mem,
            engines_per_node=engines_per_node, agents=agents)
    except ValueError as e:
        # Every one of these is the caller's number rather than a failure here,
        # and each names the field it is about. 400, not 500.
        raise BadRequest(str(e))
    return dict(p,
                document=plan.plan_document(p),
                document_file=plan.DOCUMENT_FILE)


def engine_vus(engine_cpu=None, engine_mem=None):
    """How many virtual users an engine of this size is rated for.

    The same ratio capacity_plan assumes from and doctor judges against, asked
    on its own so a form can *suggest* the figure beside the field rather than
    leaving "virtual users per engine" as a number the user has to know. 500 is
    only right for the 2 CPU / 8Gi engine, which is exactly the mistake the
    planner's own default used to make.
    """
    try:
        cpu, mem = gen_mod.engine_size({"engine_cpu_limit": engine_cpu,
                                        "engine_mem_limit": engine_mem})
    except ValueError as e:
        raise BadRequest(str(e))
    return {"cpu": gen_mod.format_cpu(cpu),
            "memory": gen_mod.format_memory(mem),
            "supported_vus": plan.supported_vus(cpu, mem)}


def account_capacity(client, account_id):
    """Rated virtual-user capacity across an account, by workspace.

    "Rated", not "allowed", and the distinction was measured rather than
    assumed. A live run settled two halves of it on a location with 2 agents,
    slots=1 and threadsPerEngine=50:

      * `agents x slots` is the **engine** count, and it is enforced -- asking
        for 3 engines allocated 2, and a start while those 2 are busy is
        refused with 403 "Not enough available resources".
      * `x threadsPerEngine` is what those engines are *sized* for, and is not
        a gate: 101 virtual users started happily, packed onto the same 2
        engines. So this number is what the location is built to serve well,
        not a ceiling BlazeMeter enforces.

    A location in several workspaces is *shared*: its capacity is claimable
    from either, so adding it into both workspace totals counts engines that
    cannot run twice. It is flagged, and the account total counts it once --
    which is why the account figure is not the sum of the workspace figures.
    """
    # Both at once: they are independent reads and the locations one is the
    # slow half (1.3s on a 171-location account), so in series the workspace
    # names were pure added wait on every cold view. Threads rather than async
    # because the client is stdlib urllib and every caller here is synchronous
    # -- and because two of them is the whole concurrency this needs.
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
            # Two counts, for the reason mcp_server's listing carries two: a
            # locations *listing* need not carry `lastHeartBeat` at all, and
            # `ship_reporting` answers None there rather than False. Folding
            # that into "not reporting" would print "1 not reporting" about an
            # agent nothing had looked at -- the collapse this package has made
            # four times. Reporting is what the payload vouches for; unknown is
            # what it declined to say.
            "agents_reporting": sum(1 for s in ships if ship_reporting(s)),
            "agents_unknown": sum(1 for s in ships
                                  if ship_reporting(s) is None),
            "slots": slots, "threads_per_engine": tpe,
            "engines": engines,
            # None, not 0: a location with slots or threadsPerEngine unset has
            # no rating to state, and 0 would read as "no capacity" when the
            # truth is "nobody has said".
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
    """The evidence document, from either a path to the collector's file or the
    parsed contents of one.

    A path is accepted for the same reason `api_key_file` is: what a customer
    sends back is a *file*, and the caller most likely to be holding one is the
    one furthest from a shell. Inlining it costs several KB of node lists and
    permission maps travelling through a model to reach a check that only needed
    somewhere to read them from (#77).

    Whose call this is, is the transport's -- the MCP server passes what its
    caller sent, because that caller shares this filesystem; the web UI does not
    offer it, since a browser has already parsed the file it uploaded and a path
    posted from one would be read on the machine serving the page. Same division
    as api_key_file: the rule and the refusals live here, offering the argument
    does not.

    A string is always a path and never JSON text. Reading it as either would
    make a mistyped path come back as a complaint about JSON syntax, and no
    caller has the text without the object -- one that parsed it passes the
    object.

    Anything else travels on untouched, so a list, a number or a facts file's
    contents is refused further down by the check that names what it found. Only
    the read is decided here, which is why its failure has a type of its own.
    """
    if not isinstance(evidence, str):
        return evidence
    try:
        return doctor.load_evidence(os.path.expanduser(evidence))
    except ValueError as e:
        # Both of load_evidence's sentences -- no file there, and not JSON --
        # mean nothing was read, so nothing can yet be said about whether what
        # was named is evidence. That is the distinction this type carries.
        raise EvidenceUnreadable(str(e))


def preflight(facts, options, evidence):
    """The verdicts `doctor --cluster-evidence` prints, for one configuration.

    Reaches nothing: no BlazeMeter account and no cluster. That is the whole
    case it exists for -- the customer whose account and cluster are both out
    of reach -- and it is why the imported evidence has to supply both the
    cluster read and the probes, which is what stops doctor.evaluate looking
    for a kubectl on this machine.

    Everything the file carries beyond the cluster read -- when it was
    collected, which namespace for, what the collector was refused -- arrives
    as the leading Check rather than as a second field beside the verdicts,
    because it qualifies every one of them.
    """
    options = options or {}
    doc_ns = (evidence.get(evidence_mod.NAMESPACE)
              if isinstance(evidence, dict) else None)
    # Same precedence as the command: the namespace being configured wins, and
    # the one the file was collected for is the last resort. A file collected
    # elsewhere is then reported by cluster_from_evidence rather than adopted.
    namespace = options.get("namespace") or doc_ns
    try:
        imported = doctor.cluster_from_evidence(evidence, namespace)
    except ValueError as e:
        # Every way a file can be the wrong one is a sentence doctor already
        # writes, and it carries no verdicts -- so whatever the caller was
        # already showing stays on screen.
        raise BadRequest(str(e))
    namespace = doctor.resolve_namespace(namespace, options)
    try:
        checks = doctor.evaluate(facts, options, namespace, evidence=imported)
    except (ValueError, KeyError) as e:
        # An engine limit that does not parse, say. This re-runs on every
        # keystroke in those fields, so it answers the way generate does.
        raise BadRequest(str(e))
    # What the same file implies about the options, and how each implication
    # stands against the ones that were sent. Here rather than in a call of its
    # own: it is one file judged against one configuration, both halves move on
    # every option change, and two round trips is two answers that can end up
    # describing different configurations in the same panel. Nothing is applied
    # -- `state` says what applying would mean, and the choice is the caller's.
    suggestions = suggest_mod.from_evidence(evidence)
    return {"namespace": namespace,
            **_verdicts(checks),
            # The same three facts the leading check states in prose, apart
            # from it: a caller can put them in a header, where they cannot be
            # read past. Which namespace the *file* describes is not
            # `namespace` above -- that is the one being preflighted, and the
            # difference is the point.
            "evidence": doctor.evidence_summary(evidence),
            **suggestions_from_evidence(evidence, options)}


def suggestions_from_evidence(evidence, options=None):
    """What a cluster's evidence implies about the generate options.

    The other half of preflight(), asked on its own: `doctor` answers whether a
    deployment survives this cluster, and this answers how it should have been
    configured. Same file, different question, and nothing is applied.
    """
    try:
        suggestions = suggest_mod.from_evidence(evidence)
    except ValueError as e:
        raise BadRequest(str(e))
    return {"suggestions": [suggest_mod.merged_as_dict(s, options or {})
                            for s in suggestions],
            "why_nothing": None if suggestions
                           else suggest_mod.why_nothing(evidence)}


def toolcheck(cluster=None, local_registry=None, local_proxy=False):
    """The workstation preflight, for the rig flags you mean to pass.

    Evaluates rather than runs, and answers rather than exits: `workstation.run`
    prints its report, and core is not a terminal -- for the MCP server stdout
    is the JSON-RPC channel. `ok` is the caller's to act on.
    """
    checks = workstation.evaluate({"cluster": cluster,
                                   "local_registry": local_registry,
                                   "local_proxy": local_proxy})
    return _verdicts(checks)


def _verdicts(checks):
    """Checks as data, with the one summary every caller recomputes.

    `ok` is not "no FAILs" spelled out at each call site: doctor's contract is
    that a denied read is a WARN and only an answered one can FAIL, so a caller
    that treated WARN as failure would report a locked-down cluster as a broken
    one.
    """
    return {"checks": [c._asdict() for c in checks],
            "ok": not doctor.has_failures(checks)}


# -- the location, as something that gets changed -----------------------------

def reveal_token(client, harbor_id, ship_id):
    """The ship's AUTH_TOKEN, as the answer rather than as a side effect.

    **This rotates it.** The previous token stops working, and an agent already
    running on it starts logging 404 on /ships/<id>/status while sitting at 0/1
    -- which reads like a deleted ship, not like a credential problem. So it is
    its own named call and never something another action does on the way past.
    """
    return {"harbor_id": harbor_id, "ship_id": ship_id,
            "auth_token": fetch_ship_token(client, harbor_id, ship_id),
            "warning": "this issued a NEW token and invalidated the previous "
                       "one. Any agent already running for this ship must be "
                       "re-applied with it, Secret included."}


def delete_location(client, harbor_id):
    """Delete a private location and every ship in it.

    Reads it first so the answer can name what went, which is the only record
    anyone will have afterwards.
    """
    harbor = _upstream(client.private_location, harbor_id)
    ships = harbor.get("ships", [])
    _upstream(client.delete_private_location, harbor_id)
    return {"deleted": harbor_id, "name": harbor.get("name"),
            "ships_deleted": [s["id"] for s in ships]}


# -- what is deployed in the namespace ----------------------------------------

# What each unreadable cluster means, in the user's terms -- a reason without a
# way forward is the dead panel the watch list must never become.
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


def sv_read_message(read):
    """The sentence shown for an unreadable cluster.

    `.get`, not `[]`, because livetest owns the set of reasons -- a fifth one
    should degrade to the raw detail, not raise out of the one call whose
    contract is that it never returns a bare error.
    """
    return SV_READ_MESSAGES.get(read.status, read.detail)


def sv_mocks(namespace, sv_subdomain=None):
    """What is deployed in `namespace`, and the host each one answers at.

    This rides the UI's existing status poll: the agent reports idle whether or
    not its virtual services ever became reachable, so a deploy stalled at
    WAITING_FOR_DOMAIN looks identical to a healthy one in the watch panel.

    Reading a cluster is the only thing this tool does beyond the BlazeMeter
    API, and it is optional: an unreadable cluster comes back saying which of
    the four reasons applied, never as a raised error. A poll that fails every
    ten seconds either fills the console or gets swallowed by the caller's
    catch and silently reads as "nothing deployed", which is the one answer
    this must never fake.
    """
    read = livetest.sv_read(namespace)
    return {
        "status": read.status,
        "mocks": [{"name": m["name"], "port": m["port"],
                   "host": gen_mod.sv_endpoint_host(
                       m["name"], m["port"], namespace, sv_subdomain)}
                  for m in read.mocks],
        "message": sv_read_message(read),
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
# arrives from outside -- a browser, or a model deciding what to probe -- and a
# URL carrying a path, credentials or a second word would turn a reachability
# probe into a general-purpose fetcher aimed by whatever supplied it.
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


def sv_check_reason(err):
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
    # socket.timeout is an alias of TimeoutError from 3.10, which is the floor,
    # so one name catches both. It was two separate classes on 3.9 and matching
    # on either alone silently dropped half the timeouts -- worth remembering
    # if the floor ever moves back down.
    if isinstance(e, TimeoutError):
        return SV_CHECK_TIMEOUT
    if isinstance(e, ConnectionRefusedError):
        return SV_CHECK_REFUSED
    # Reset connections, a proxy that hung up, an http.client parse failure.
    # One bucket rather than a fifth guess, with the raw reason alongside.
    return SV_CHECK_ERROR


def sv_check(host, scheme="http"):
    """Ask whether the endpoint a deployed virtual service publishes answers.

    `host` is the string sv_mocks handed back, passed in rather than rebuilt
    here: what gets probed has to be what the caller was shown and what
    BlazeMeter advertises, or a green tick would be vouching for an address
    nobody was given.

    Returns a verdict whatever happened, for the same reason the cluster reads
    do: an endpoint that does not answer is the expected finding, not a broken
    request. The two refusals are inputs that are not an endpoint at all.
    """
    if scheme not in ("http", "https"):
        raise BadRequest(f"scheme must be http or https, not {scheme!r}")
    if not _SV_HOST_RE.match(host or ""):
        raise BadRequest(f"not an endpoint host: {host!r}")
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
        status = sv_check_reason(e)
        detail = str(e) or repr(e)
        return {"status": status, "code": None, "url": url, "detail": detail,
                "message": SV_CHECK_MESSAGES.get(status)
                or f"The endpoint could not be reached: {detail}"}
    return {"status": SV_CHECK_OK, "code": code, "url": url, "detail": detail,
            "message": SV_CHECK_503 if code == 503
            else f"HTTP {code} -- the endpoint answered."}


# -- the vocabulary ------------------------------------------------------------

def option_defaults():
    """Bare option -> default, and nothing else.

    The UI spreads this straight into the options it submits and diffs against
    it, so any metadata key added here would arrive at generate() as an option
    named after it. The descriptions are option_docs() for that reason.
    """
    return gen_mod.DEFAULT_OPTIONS


def option_docs():
    """What each option is for, from the registry docs/options.md is built from.

    The one-line `summary`, not the full argued paragraph: this is help beside
    a control or in a tool schema, and the long version is a doc link away.
    """
    return {o.name: {"summary": o.summary,
                     "group": o.group,
                     "type": o.type,
                     "nullable": o.nullable,
                     "choices": list(o.choices) if o.choices else None,
                     "secret": o.secret}
            for o in options_mod.OPTIONS}


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


def func_ids():
    """The funcIds a location can be created with, in declaration order.

    Served rather than restated by each caller: the create-location form used
    to hold its own list in TypeScript, and whatever was missing from that copy
    could not be selected from the UI at all. Derived from the facts layer, so
    adding a funcId there -- already required for its images to be selected --
    is the only edit needed.

    `changes_images` marks the ones worth offering where a funcId's only job is
    to pick images -- the manual-entry form. functionalApi and performance both
    mean "the taurus engine", so offering both there is a choice that cannot
    change the output. Answered here rather than filtered by the caller for the
    same reason the list itself is: a copy in the frontend is how the
    vocabulary and the thing it describes drift apart.
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
        # sv_constants serves and _sv_cfg validates against.
        "func_ids": list(gen_mod.SV_FUNC_IDS),
    },
]


def features():
    """The features the configure step can be pointed at, in selector order."""
    return FEATURES


def sv_constants():
    """The two service-virtualization enumerations a caller must not hardcode.

    Kept apart from option_defaults() because that is spread straight into the
    options the UI submits, and these are not options. Answering them at all is
    what stops a fifth expose backend from being added to generate() and
    silently missing from the picker -- the funcId list in particular was
    duplicated in TypeScript with a comment asking the next person to keep it
    in step by hand.
    """
    return {"func_ids": list(gen_mod.SV_FUNC_IDS),
            "ingress_types": list(gen_mod.SV_INGRESS_TYPES),
            # What each backend publishes, so a caller can name the Role the
            # bundle grants without keeping its own copy of SV_INGRESS_BACKENDS
            # -- which is mechanical, unlike the prose around it. Only the four
            # fields the UI renders; via_ingress_class is doctor's, and serving
            # it here would be a field nothing reads. nodeport_ok is here
            # because the UI decides something with it -- whether to offer
            # NODEPORT beside this backend -- not merely to display it.
            "backends": {name: {"group": b.group,
                                "resources": list(b.resources),
                                "creates": b.creates,
                                "nodeport_ok": b.nodeport_ok}
                         for name, b in gen_mod.SV_INGRESS_BACKENDS.items()}}
