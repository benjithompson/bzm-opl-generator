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

What is deliberately *not* here: holding a client (each transport owns its own
credential lifetime and the remedy it names when there is none), and anything
about how a bundle reaches the user -- the UI streams a zip, the CLI writes a
directory, and neither is a decision about OPL.
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

from . import (api, doctor, facts as facts_mod, generate as gen_mod, livetest,
               options as options_mod, suggest as suggest_mod)


# -- failures ------------------------------------------------------------------

class CoreError(Exception):
    """A refusal, with the HTTP status the web layer answers it with.

    Carried rather than decided per route so that a caller with no status codes
    at all -- stdio JSON-RPC -- still gets the distinction between "you sent
    the wrong thing" and "BlazeMeter did".
    """
    status = 400


class BadRequest(CoreError):
    status = 400


class NotFound(CoreError):
    status = 404


class UpstreamError(CoreError):
    """BlazeMeter answered, and what it said was an error. 502 because the
    caller's request was fine; something upstream of us was not."""
    status = 502


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


def locations(client, account_id=None, workspace_id=None):
    if not account_id and not workspace_id:
        raise BadRequest("account_id or workspace_id required")
    return _upstream(client.private_locations, account_id, workspace_id)


def create_location(client, name, account_id, workspace_id,
                    func_ids=("performance",), slots=1,
                    threads_per_engine=api.DEFAULT_THREADS_PER_ENGINE):
    return _upstream(client.create_private_location, name, account_id,
                     [workspace_id], func_ids=list(func_ids), slots=slots,
                     threads_per_engine=threads_per_engine)


def create_ship(client, harbor_id, name):
    return _upstream(client.create_ship, harbor_id, name)


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
        "online": bool(hb and time.time() - hb < HEARTBEAT_FRESH_S
                       and ship.get("state") in ONLINE_STATES),
    }


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
    """Which ship an AUTH_TOKEN would be fetched for, or None to fetch none.

    Fetching a token *rotates* it -- the previous one stops working, and
    whatever agent holds it starts logging 404 on /ships/<id>/status while
    sitting at 0/1, which reads like a deleted ship. So this adds one clause to
    sole_ship_id: a token already in the options is the caller's, and must
    never be replaced by a fresh one that breaks their running agent.

    Where the ship is ambiguous nothing is fetched and generate() refuses the
    ambiguity itself, with a sentence naming both.
    """
    if options.get("auth_token"):
        return None
    return sole_ship_id(facts, options.get("ship_id"))


def generate_bundle(facts, options=None, client=None, fetch_token=True):
    """The manifests, as {name: content}.

    `client=None` is a first-class case, not a degraded one: the manual-entry
    path has no account to ask, and `fetch_token=False` is the caller saying
    the token was typed and a key left over from an earlier connect must not be
    asked for one belonging to somebody else's agent.
    """
    opts = dict(options or {})
    if fetch_token and client is not None:
        ship_id = token_ship_id(facts, opts)
        if ship_id:
            opts["auth_token"] = _upstream(client.auth_token,
                                           facts["harbor_id"], ship_id)
    try:
        return gen_mod.generate(facts, opts)
    except (ValueError, KeyError) as e:
        # Every refusal generate() makes is a sentence written for the person
        # who set the option, so it travels as-is rather than being summarised.
        raise BadRequest(str(e))


# Which file to read first, and the rest after it. A generator decision, not a
# presentation one -- the helm bundle leads with the values overlay because it
# is the only file in a chart that came from the account -- so it is aliased
# here rather than left for each caller to remember to apply.
preview_order = gen_mod.preview_order

ZIP_PREFIX = "bzm-opl"


def zip_bundle(files, prefix=ZIP_PREFIX):
    buf = io.BytesIO()
    # Names may carry directories (the helm format emits a chart), which zip
    # stores as-is -- the slash is the path separator in the archive too.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in gen_mod.preview_order(files):
            info = zipfile.ZipInfo(f"{prefix}/{name}")
            if name.endswith(".sh"):
                info.external_attr = 0o755 << 16
            z.writestr(info, files[name])
    return buf.getvalue()


def zip_filename(options):
    return f"{ZIP_PREFIX}-{(options or {}).get('namespace', 'blazemeter')}.zip"


# -- preflight -----------------------------------------------------------------

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
    doc_ns = evidence.get("namespace") if isinstance(evidence, dict) else None
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
            # The same three facts the leading check states in prose, apart
            # from it: a caller can put them in a header, where they cannot be
            # read past. Which namespace the *file* describes is not
            # `namespace` above -- that is the one being preflighted, and the
            # difference is the point.
            "evidence": doctor.evidence_summary(evidence),
            "checks": [c._asdict() for c in checks],
            "suggestions": [suggest_mod.merged_as_dict(s, options)
                            for s in suggestions],
            # An empty list from a file that never reached a cluster reads like
            # one from a cluster that constrains nothing, and only the first is
            # worth re-collecting for. Null once there is anything to show, so
            # the caller has nothing to decide.
            "why_nothing": None if suggestions
                           else suggest_mod.why_nothing(evidence)}


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
