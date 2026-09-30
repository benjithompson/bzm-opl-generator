"""MCP over core.py, for an AI session that has no checkout of this repo.

`bzm-opl-gen mcp` speaks stdio JSON-RPC. The tool descriptions, INSTRUCTIONS
and the served docs are the entire documentation such a session has. Six tools,
each dispatching on an `action` declared as a Literal, so a wrong one is refused
by the client's own schema validation.

Rules this layer keeps that core does not:
  * the AUTH_TOKEN is never in a response, except from `reveal_token`, a whole
    action so it cannot happen as a side effect;
  * a secret is never an argument (a path may be);
  * nothing writes to a cluster, except `opl_agent livetest`, which is off
    unless its own environment variable is set.
"""

import contextlib
import json
import os
import sys
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import (__version__, api, core, evidence as evidence_mod,
               generate as gen_mod, livetest, plan)
from . import bundle_names, bundle_options, ca_trust, footprint, readme_parts

SERVER_NAME = "bzm-opl-gen"
RESOURCE_SCHEME = "bzm-opl"

# Both gates are read at call time, so setting the variable takes effect
# without a restart.
ENABLE_LIVETEST_ENV = "BZM_OPL_ENABLE_LIVETEST"
ALLOW_DESTRUCTIVE_ENV = "BZM_OPL_ALLOW_DESTRUCTIVE"


INSTRUCTIONS = f"""\
Generate and verify a BlazeMeter private-location (OPL) agent deployment for
Kubernetes or OpenShift, from a real account rather than from a template.

The path through it:

  0. opl_plan capacity          -- how much cluster a sizing needs. Before
                                   everything else, and needs none of it: no
                                   key, no account, no cluster.
  1. opl_location list          -- find the location, then `show` for its
                                   agents. Accounts hold hundreds of
                                   locations, so `list` is one line each and
                                   capped: narrow it with name_contains, and
                                   read the omitted counts it comes back with
                                   rather than treating the list as the account.
  2. opl_facts gather           -- the images and ids that location actually uses
  3. opl_preflight doctor       -- will this cluster take it? (needs evidence)
  4. opl_bundle generate        -- write the manifests to a directory
  5. kubectl apply -f <dir>     -- YOU run this, in your own shell
  6. opl_agent status           -- did the agent come online?
  7. opl_agent triage           -- if not, or a run hangs at BOOT_STARTING:
                                   the known failures in the namespace, each
                                   with its fix (see triage.md)

Step 5 is deliberately not a tool. This server does not apply anything to a
cluster: the person you are working with needs to see what is being applied to
theirs, and `kubectl apply` in their shell is where they see it. The same goes
for `helm install` when the bundle is a chart. (The one tool that does deploy is
opl_agent livetest, which is off unless its own variable is set.) Step 7 reads
the namespace with this machine's own kubectl or oc context and writes nothing.

Sizing before there is a cluster: `opl_plan capacity` turns what a customer has
to run ("5,000 virtual users", "40 browsers at once") into pods, nodes and a
machine size, plus a `document` written for the platform team who has to provide
them. That request is often the actual blocker -- a customer with no cluster
cannot start at step 1, and this is what unblocks them. Each covered
functionality is sized in its own unit and not everything has a figure: how many
virtual users an engine carries is assumed unless told, and how many requests
per second a mock pod serves has never been measured here at all. Say which
whenever you report what it produced; the answer says so field by field.

The vocabulary, and it is worth keeping to: a **location** holds **agents**, an
agent runs **engines**, and each engine drives some number of **virtual users**.
"Slots" and "threadsPerEngine" are the names of two location *fields* (concurrent
engines, and virtual users per engine) rather than terms to explain anything in.
Neither a location nor an agent needs a cluster to exist -- both can be created
in BlazeMeter first, and an agent that has never sent a heartbeat is the normal
state until its manifests are applied.

Facts without an account: `opl_facts manual` builds the same structure from the
harbor_id and ship_id read off the BlazeMeter UI (BlazeMeter's own field names
for a private location and one agent in it), so you can produce a bundle for a
customer whose account you cannot reach. It cannot know which browser image a
GUI location uses -- only the account names the pinned build -- and says so
rather than guessing.

Facts without a location either: both ids are optional there. A customer whose
private location does not exist yet has no id to read off, and the manifests are
often what their platform team has to approve first -- so the bundle carries
<HARBOR_ID> and <SHIP_ID> where the ids belong, the README names them, and the
cluster refuses to apply it (a marker is not a legal label value). Report that
plainly: it is a bundle for review, and it becomes deployable when the location
and the agent exist and their ids are filled in.

Preflight without a cluster: `opl_preflight doctor` reads a cluster *evidence*
file, which the customer collects and sends. It never runs kubectl here. If
you have not got one, say so -- do not report a preflight you did not run.

Reference, readable as resources on this server ({RESOURCE_SCHEME}://docs/...):
options.md (every generate option), preflight.md (evidence files and what the
checks mean), triage.md (what a deployed namespace shows, and each fix),
capacity-planning.md (sizing a cluster nobody has yet), helm.md
and docker.md (the two non-manifest output formats), service-virtualization.md,
hardened-engines.md, images.md (what each image does, mirroring and checking
a mirror), live-test.md, ca-trust.md (a corporate CA, and the
`bzm-opl-gen ca-check` command the customer runs on their own network to test
it). Read the one that covers the question rather
than guessing at an option name -- `opl_bundle options` lists them all with a
one-line summary each, and every page this server serves is in `docs`.

OTHER BLAZEMETER MCP SERVERS, IF THIS SESSION HAS THEM
This server covers the *deployment*: locations, agents, manifests, preflight.
It does not run tests or manage virtual services. Two sibling servers do, and
where they are available they are the right tool:

  blazemeter_tests, blazemeter_execution -- create and run tests, read results.
      Use these to prove the location works end to end: run a test against it
      and read the report. This server can tell you the agent is online; only
      a real run tells you an engine started and reported back.
  virtual_services_* -- virtual services / mocks on a service-virtualization
      location, once its agent is deployed and its ingress is serving.

If those tools are not present in this session, say so and stop. Do NOT
simulate, invent or describe what they would have returned: a plausible test
report for a run that never happened is indistinguishable from a real one, and
it is the failure that gets caught last. Ask for the server to be enabled, or
hand the person the BlazeMeter UI step instead.

Two things about credentials. The API key comes from this server's environment
({core.KEY_FILE_ENV}, or {core.KEY_ID_ENV} and {core.KEY_SECRET_ENV}) -- never
pass a secret as a tool argument. And issuing an agent's AUTH_TOKEN *rotates*
it: the previous token stops working, and an agent already running on it sits at
0/1 logging 404 on its status endpoint, which reads like a deleted agent. That is
why the token is written into the bundle and never returned to you, and why the
two actions that can issue one -- `opl_bundle generate` with rotate_token=true,
and `opl_location reveal_token` -- have to be asked for by name. Generating
without rotate_token touches no credential at all: it reuses the token already in
out_dir, or leaves a placeholder and says so. Every generate reports which of
those happened as `token_source`; read it before you deploy.
"""


# Each tool's description sits beside its action list and dispatch function.
DESCRIPTIONS = {}

# Side-effect hints for all six, kept together so they read against each other.
_ANNOTATIONS = {
    "opl_location": ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                    idempotent_hint=False, open_world_hint=True),
    "opl_facts": ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=True),
    "opl_bundle": ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                    idempotent_hint=False, open_world_hint=False),
    "opl_plan": ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
    "opl_preflight": ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
    "opl_agent": ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                    idempotent_hint=False, open_world_hint=True),
}

# -- the docs this server serves ----------------------------------------------

def docs_dir():
    """Where the shipped documentation is: inside the package in a wheel (the
    `bzm_opl_gen.docs` mapping in pyproject.toml), at the repo root in a
    checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    packaged = os.path.join(here, "docs")
    if os.path.isdir(packaged):
        return packaged
    return os.path.join(os.path.dirname(here), "docs")


def doc_files():
    d = docs_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".md"))


# What each doc is for. A doc missing from here is still served, described by
# its file name.
DOC_SUMMARIES = {
    "options.md": "Every generate option: what it does, what it defaults to, "
                  "and what breaks if it is wrong.",
    "preflight.md": "Cluster evidence files: what to ask the customer to "
                    "collect, and what each verdict means.",
    "capacity-planning.md": "Sizing a cluster for a load target, before there "
                            "is a cluster or an account to ask.",
    "helm.md": "The chart output format, and managing the release afterwards.",
    "docker.md": "The docker output format: one agent as one container on a "
                 "host, and which options reach it.",
    "service-virtualization.md": "Mock-service locations: the ingress backends, "
                                 "which combinations are refused, and how to "
                                 "generate such a location for performance alone.",
    "hardened-engines.md": "The restricted engine posture, and which images "
                           "have run under it.",
    "images.md": "What each image does and which functionality needs it, "
                 "mirroring them, carrying them to an air-gapped site with "
                 "images --save and --load, and checking a mirror with "
                 "images --verify.",
    "live-test.md": "The live rig: what it proves and what it costs.",
    "ca-trust.md": "A corporate TLS-inspecting CA: the four ways to supply it, "
                   "and checking it with ca-check before deploying.",
    "web-ui.md": "The local web UI, for a human doing this by hand.",
    # That page's fix is a patch to crane's source; the runnable workarounds
    # are elsewhere, and the summary has to say so.
    "crane-nginx-ingress-port.md": "The upstream crane defect behind a mock "
                                   "endpoint that 503s; the workarounds are in "
                                   "service-virtualization.md.",
    "mcp.md": "This server: its tools, its gates, and what it will not do.",
    "triage.md": "After deploying: the known failures triage recognises in "
                 "a namespace, and the fix for each.",
}


# -- argument handling ---------------------------------------------------------

def _args(args):
    """Tool arguments as a dict; None (no arguments) is {}."""
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise core.BadRequest(
            f"args must be an object, not {type(args).__name__}")
    return args


def _need(args, *names):
    """Required arguments, all missing ones refused in one message."""
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise core.BadRequest(
            f"missing required argument(s): {', '.join(missing)}")
    return [args[n] for n in names]


def _given(args, key, default):
    """`args[key]`, or `default` where it is absent or null (a client's usual
    way of saying "unset")."""
    value = args.get(key)
    return default if value is None else value


def _no_secrets(options):
    """Refuse a credential passed as an option: by the time it arrives it has
    already travelled through the model and the transcript."""
    sent = sorted(set(options) & set(gen_mod.SECRET_OPTIONS))
    if sent:
        raise core.BadRequest(
            f"{', '.join(sent)} is a credential and must not be passed as a "
            f"tool argument -- it goes through the model and into the "
            f"transcript on the way here. Leave it out: a bundle already in "
            f"out_dir has its token read back, or set the value in the written "
            f"Secret yourself, or pass rotate_token=true to issue a fresh one "
            f"(which stops the running agent until you re-apply). "
            f"opl_location reveal_token is how you read the current value.")
    return options


# A former argument name, refused so a session using a cached description does
# not silently get a placeholder where it expected a credential.
_RENAMED_TOKEN_ARG = "fetch_token"


def _no_stale_fetch_token(args):
    if _RENAMED_TOKEN_ARG in args:
        raise core.BadRequest(
            f"{_RENAMED_TOKEN_ARG} is not an argument -- use `rotate_token`, "
            f"which defaults to false. Rotating POSTs for a new AUTH_TOKEN and "
            f"the previous one stops working, so any agent running on it sits "
            f"at 0/1 Running "
            f"until the bundle is re-applied. Pass rotate_token=true only to "
            f"replace a credential on purpose; leave it out to generate without "
            f"touching the account.")


def _gate(env, what):
    if os.environ.get(env) not in ("1", "true", "yes"):
        raise core.BadRequest(
            f"{what} is disabled. Set {env}=1 in this server's environment to "
            f"allow it -- it is off by default because it cannot be undone "
            f"from here.")


def _client(args):
    return core.client_from_key(args.get("api_key_file"))


def _unknown(action, valid):
    return core.BadRequest(
        f"unknown action {action!r}. This tool takes: {', '.join(valid)}")


_DEFAULT_NS = bundle_options.DEFAULT_OPTIONS["namespace"]


# -- opl_location --------------------------------------------------------------

# Older action names, kept working. The word is "agent"; `ship` survives only
# in `ship_id`, BlazeMeter's own field name.
LOCATION_ALIASES = {"create_ship": "create_agent"}

LOCATION_ACTIONS = ("list", "show", "whoami", "create", "create_agent",
                    "reveal_token", "delete") + tuple(LOCATION_ALIASES)

DESCRIPTIONS["opl_location"] = (
    "BlazeMeter private locations (harbors) and their agents.\n"
    "  list         -- one line per location {account_id?, workspace_id?, "
    "name_contains?, limit?}. Defaults to this key's own account and to the "
    f"first {core.DEFAULT_LOCATION_LIMIT}; accounts hold hundreds, so narrow "
    "with name_contains rather than raising limit. Whatever it leaves out is "
    "counted in the response.\n"
    "  show         -- one location with its agents in full {harbor_id}\n"
    "  whoami       -- who this API key is, and its default account\n"
    "  create       -- a new private location {name, account_id, "
    "workspace_id, func_ids?, slots?, threads_per_engine?}. func_ids are "
    f"BlazeMeter's own, and the ones this tool configures a bundle for are "
    f"{', '.join(core.covered_func_ids())} (default performance). An account "
    "carries others -- proxyRecorder, tdm, delphix -- which a location may "
    "hold and nothing here generates for. `slots` is engines per agent and "
    "defaults to 1; "
    + "; ".join(f"{r['label']} is refused below {r['minimum']}"
                for r in core.SLOT_MINIMUMS.values())
    + ", by BlazeMeter rather than by this tool, so ask for the number rather "
    "than raising it for them.\n"
    "  create_agent -- a new agent in a location {harbor_id, name}"
    + "".join(f" (also accepted as {old})" for old in LOCATION_ALIASES) + "\n"
    "  reveal_token -- the agent's AUTH_TOKEN {harbor_id, ship_id}. "
    "ROTATES it: the previous token stops working and any agent "
    "running on it goes to 0/1. Use only when re-applying that agent.\n"
    "  delete       -- delete a location and every agent in it "
    "{harbor_id}. Off unless " + ALLOW_DESTRUCTIVE_ENV + "=1.\n"
    "create/create_agent/delete change a real customer account -- "
    "confirm with the person before calling them.\n"
    "`ship_id` is BlazeMeter's own name for an agent's id, spelled as the "
    "account spells it, so what you read here matches what its API answers.")


def _location(action, args):
    action = LOCATION_ALIASES.get(action, action)

    if action == "whoami":
        return dict(core.whoami(_client(args)),
                    next=["opl_location list, with that account_id"])

    if action == "list":
        client = _client(args)
        account_id, workspace_id = args.get("account_id"), args.get("workspace_id")
        if not account_id and not workspace_id:
            # The key's default account is almost always the one meant.
            account_id = core.default_account_id(client)
            core.require_location_scope(account_id, workspace_id)
        locs = core.locations(client, account_id, workspace_id)
        # No uncapped option: there is no size a result budget cannot overflow.
        sel = core.select_locations(
            locs, name_contains=args.get("name_contains"),
            limit=_given(args, "limit", core.DEFAULT_LOCATION_LIMIT))
        body = {"account_id": account_id, "workspace_id": workspace_id,
                "total": sel["total"], "matched": sel["matched"],
                "returned": sel["returned"],
                "omitted_by_filter": sel["omitted_by_filter"],
                "omitted_by_limit": sel["omitted_by_limit"],
                "locations": [_location_brief(l) for l in sel["locations"]],
                "next": ["opl_location show, or opl_facts gather, with the "
                         "harbor_id of the one you want"]}
        note = _omission_note(sel, args.get("name_contains"))
        if note:
            body["note"] = note
        return body

    if action == "show":
        harbor_id, = _need(args, "harbor_id")
        loc = core.location(_client(args), harbor_id)
        return {"location": _location_summary(loc),
                "next": [f"opl_facts gather with harbor_id {harbor_id!r}"]}

    if action == "create":
        name, account_id, workspace_id = _need(args, "name", "account_id",
                                               "workspace_id")
        made = core.create_location(
            _client(args), name, account_id, workspace_id,
            func_ids=args.get("func_ids") or list(api.DEFAULT_FUNC_IDS),
            slots=_given(args, "slots", 1),
            threads_per_engine=_given(args, "threads_per_engine",
                                      footprint.DEFAULT_THREADS_PER_ENGINE))
        loc = made["location"]
        body = {"location": _location_summary(loc),
                "next": [f"opl_location create_agent with harbor_id "
                         f"{loc.get('id')!r} -- a location with no agent has "
                         f"nothing to deploy"]}
        if made["warning"]:
            body["warning"] = made["warning"]
        return body

    if action == "create_agent":
        harbor_id, name = _need(args, "harbor_id", "name")
        # issue_token=False: a token is never returned by this server.
        agent = core.create_agent(_client(args), harbor_id, name,
                                  issue_token=False)["ship"]
        return {"harbor_id": harbor_id, "agent": agent,
                "next": [f"opl_facts gather with harbor_id {harbor_id!r}"],
                "note": "this call issues no AUTH_TOKEN, and neither does "
                        "opl_bundle generate unless you pass rotate_token=true "
                        "(which is how this new agent gets its first one). "
                        "opl_location reveal_token returns the value, and "
                        "rotates it."}

    if action == "reveal_token":
        harbor_id, ship_id = _need(args, "harbor_id", "ship_id")
        return dict(core.reveal_token(_client(args), harbor_id, ship_id),
                    next=["re-apply the whole bundle, Secret included -- the "
                          "agent is now holding a token the API has stopped "
                          "accepting"])

    if action == "delete":
        harbor_id, = _need(args, "harbor_id")
        _gate(ALLOW_DESTRUCTIVE_ENV,
              "deleting a private location (and every agent in it)")
        gone = dict(core.delete_location(_client(args), harbor_id))
        # This surface says "agents"; pop so a renamed core key fails loudly.
        gone["agents_deleted"] = gone.pop("ships_deleted")
        return dict(gone,
                    next=["any agent still deployed for it is now orphaned: "
                          "kubectl delete -f <its bundle>"])

    raise _unknown(action, LOCATION_ACTIONS)


def _location_summary(loc):
    """One location for `show` and `create`: the ids a session passes on, and
    each agent's state. `reporting` is null where the payload carried no
    heartbeat; opl_agent status is the authority."""
    return {"harbor_id": loc.get("id"), "name": loc.get("name"),
            "slots": loc.get("slots"), "func_ids": loc.get("funcIds"),
            "agents": [{"ship_id": s.get("id"), "name": s.get("name"),
                        "state": s.get("state"),
                        "reporting": core.ship_reporting(s)}
                       for s in loc.get("ships", [])]}


def _location_brief(loc):
    """One location as a listing entry: enough to choose one, with no per-agent
    detail (which overflowed the result budget on large accounts).

    `agents_reporting` is null when no agent's state is known at all, since a
    0 there would read as "none alive"; it counts the vouched-for ones
    otherwise, with `agents_unknown` beside it. (core.account_capacity keeps it
    numeric: the web page computes with it.)
    """
    agents = loc.get("ships") or []
    counts = core.reporting_counts(agents)
    all_unknown = bool(agents) and counts["agents_unknown"] == len(agents)
    return {"harbor_id": loc.get("id"), "name": loc.get("name"),
            "func_ids": loc.get("funcIds"), "slots": loc.get("slots"),
            "agent_count": len(agents),
            "agents_reporting": None if all_unknown else counts["agents_reporting"],
            "agents_unknown": counts["agents_unknown"]}


def _omission_note(sel, name_contains):
    """The omitted counts as a sentence, present only when the list is partial."""
    parts = []
    if sel["omitted_by_filter"]:
        parts.append(f"{sel['omitted_by_filter']} of the account's "
                     f"{sel['total']} locations do not match "
                     f"name_contains={name_contains!r}")
    if sel["omitted_by_limit"]:
        parts.append(f"{sel['omitted_by_limit']} matching locations are not in "
                     f"this list -- narrow with name_contains, or raise limit "
                     f"(default {core.DEFAULT_LOCATION_LIMIT})")
    return ". ".join(parts) + "." if parts else None


# -- opl_facts -----------------------------------------------------------------

FACTS_ACTIONS = ("gather", "manual")

DESCRIPTIONS["opl_facts"] = (
    "The account facts a bundle is generated from: image references, "
    "ids, and which functionalities the location is enabled for.\n"
    "  gather -- read them from the account {harbor_id}\n"
    "  manual -- build the same structure from ids read off the "
    "BlazeMeter UI {harbor_id?, ship_id?, func_ids?}, for a customer "
    "whose account you cannot reach. Both ids are optional: leave one out "
    "and the bundle carries <HARBOR_ID>/<SHIP_ID> where it belongs and names "
    "the field, which is the answer for a location BlazeMeter has not issued "
    "ids for yet. Supply them when the customer has them -- a marked bundle "
    "cannot be applied\n"
    "func_ids decide which images the bundle carries, so `manual` needs the "
    f"ones the location really runs -- {', '.join(core.covered_func_ids())} "
    "are the ones this tool configures for, and the default is performance. "
    "`manual` pins each image to its newest release in BlazeMeter's "
    "registry, which the location may not use yet; `warnings` says so.\n"
    "Pass the `facts` object straight to opl_bundle and opl_preflight.")


def _facts(action, args):
    if action == "gather":
        harbor_id, = _need(args, "harbor_id")
        facts = core.gather_facts(_client(args), harbor_id)
    elif action == "manual":
        # Neither id is required; a blank one becomes a marker and a warning.
        facts = core.manual_facts(
            args.get("harbor_id"), args.get("ship_id"),
            func_ids=args.get("func_ids") or list(api.DEFAULT_FUNC_IDS))["facts"]
    else:
        raise _unknown(action, FACTS_ACTIONS)
    return {"facts": facts, "warnings": core.facts_warnings(facts),
            "next": _after_facts(facts)}


def _after_facts(facts):
    return ["opl_preflight doctor -- with a cluster evidence file, if you "
            "have one",
            f"opl_bundle generate with these facts, harbor_id "
            f"{facts.get('harbor_id')!r}, and an absolute out_dir"]


# -- opl_bundle ----------------------------------------------------------------

BUNDLE_ACTIONS = ("generate", "read", "options", "images")

DESCRIPTIONS["opl_bundle"] = (
    "The manifests, written to a directory you name.\n"
    "  generate -- {facts, out_dir (ABSOLUTE), options?, rotate_token?}. "
    "Writes the bundle and answers with file names and sizes, never the "
    "YAML and never the AUTH_TOKEN. Read a file back with `read`.\n"
    "             rotate_token (default false) ISSUES A NEW AUTH_TOKEN and "
    "kills the old one: an agent already running goes to 0/1 Running until "
    "this bundle is re-applied. Without it the token comes from the "
    "auth_token option, or from a bundle already in out_dir, or stays a "
    "placeholder -- `token_source` in the response says which, every time.\n"
    "  read     -- one file out of a written bundle {out_dir, name}\n"
    "  options  -- every generate option, its default and what it does\n"
    "  images   -- the image references this bundle pulls {facts, "
    "all?, lookup?}, and a `catalogue` row per image: what it does, "
    "the funcIds that need it, when it is pulled, whether that was seen "
    "live. lookup=true adds digest, size, newest tag and, for `latest`, "
    "the version it is now (resolves_to) from BlazeMeter's public registry. With mirror=<prefix> it also pulls them and pushes them "
    "into that registry, under the names the bundle's own mirror script "
    "uses (pass the bundle's options, e.g. output_format, for a docker "
    "bundle), which writes to it -- confirm before calling it that way.\n"
    "             For a site that cannot reach BlazeMeter's registry: "
    "transfer='save' {facts, dir, all?, options?, tool?} plans saving each "
    "image to an archive in dir; transfer='load' {dir, mirror, options?, "
    "tool?} plans pushing a finished save to mirror. Both are dry runs: "
    "they return the commands, and the user runs `images --save` and "
    "`images --load` themselves.\n"
    "Applying the bundle is yours: `kubectl apply -f <out_dir>`. No "
    "action on this tool touches a cluster at all.")


def _bundle(action, args):
    if action == "options":
        docs = core.option_docs()
        return {name: dict(docs[name], default=default)
                for name, default in core.option_defaults().items()}

    if action == "generate":
        facts, out_dir = _need(args, "facts", "out_dir")
        _no_stale_fetch_token(args)
        options = _no_secrets(args.get("options") or {})
        rotate = bool(args.get("rotate_token"))
        # No `announce`: stdout is the JSON-RPC channel, so the rotation is
        # reported afterwards in token_source and warnings. A client only for
        # the branch that needs an account.
        built = core.build_bundle(
            facts, options, client=_client(args) if rotate else None,
            rotate=rotate, out_dir=out_dir, write=True)
        return {"out_dir": out_dir, "files": built.written,
                "profile": json.loads(built.files[bundle_names.PROFILE_FILE]),
                # The branch and the ship, never the value.
                "token_source": built.token._asdict(),
                "warnings": (core.facts_warnings(facts)
                             + _bundle_warnings(options)
                             + _token_warnings(built.token)),
                "next": _after_generate(out_dir, options)}

    if action == "read":
        out_dir, name = _need(args, "out_dir", "name")
        # Redacted, or reading the Secret would be a quiet reveal_token.
        content, redacted = core.redact_tokens(
            core.read_bundle_file(out_dir, name))
        return {"out_dir": out_dir, "name": name, "content": content,
                "redacted_fields": redacted,
                "next": [f"kubectl apply -f {out_dir}/ -n <namespace>, when "
                         f"the bundle reads right"],
                **({"note": "the AUTH_TOKEN in this file is redacted here and "
                            "intact on disk. opl_location reveal_token returns "
                            "the value -- and rotates it."} if redacted else {})}

    if action == "images" and args.get("transfer") and \
            not os.path.isabs(args.get("dir") or ""):
        raise ToolError("transfer needs dir, an absolute path: a relative one "
                        "resolves against this server's working directory")

    if action == "images" and args.get("transfer") == "save":
        facts, directory = _need(args, "facts", "dir")
        # A plan only: the save pulls gigabytes, so the user runs it.
        return {**core.save_images(
            facts, directory, options=_no_secrets(args.get("options") or {}),
            all_images=bool(args.get("all")), tool=args.get("tool"),
            dry_run=True),
            "next": [f"bzm-opl-gen images --save {directory} ... on a machine "
                     f"that reaches BlazeMeter's registry (YOU run it)"]}

    if action == "images" and args.get("transfer") == "load":
        directory, mirror = _need(args, "dir", "mirror")
        return {**core.load_images(
            directory, mirror, options=_no_secrets(args["options"])
            if args.get("options") is not None else None,
            tool=args.get("tool"), dry_run=True),
            "next": [f"bzm-opl-gen images --load {directory} --mirror {mirror} "
                     f"on the air-gapped side (YOU run it)"]}

    if action == "images" and args.get("transfer"):
        raise ToolError(f"transfer is 'save' or 'load', not "
                        f"{args['transfer']!r}")

    if action == "images":
        facts, = _need(args, "facts")
        refs = core.bundle_images(facts, all_images=bool(args.get("all")))
        if not (args.get("pull") or args.get("mirror")):
            cat = core.image_catalog(facts, lookup=bool(args.get("lookup")),
                                     all_images=bool(args.get("all")))
            return {"images": refs,
                    "catalogue": cat["images"],
                    "registry_lookup": cat["registry_lookup"],
                    "next": ["pass mirror=<registry-prefix> to copy these into "
                             "a private registry, or run the bundle's "
                             "bzm-opl-image-mirror.sh yourself"]}
        # Not gated like `delete`: mirroring only adds images to a registry the
        # caller named. The destructive hint makes a client confirm it.
        return core.mirror_images(
            facts, mirror=args.get("mirror"),
            platform=args.get("platform", "linux/amd64"),
            dry_run=bool(args.get("dry_run")), all_images=bool(args.get("all")),
            options=_no_secrets(args.get("options") or {}))

    raise _unknown(action, BUNDLE_ACTIONS)


def _after_generate(out_dir, options):
    if options.get("output_format") == "docker":
        return [f"chmod +x {out_dir}/{bundle_names.DOCKER_RUN_FILE}",
                f"{out_dir}/{bundle_names.DOCKER_RUN_FILE}   (YOU run this, on the "
                f"docker host itself -- nothing here reaches it)",
                # Same container name, so running both refuses the second.
                f"...or `docker compose up -d` in {out_dir}, which starts the "
                f"same container from {bundle_names.DOCKER_COMPOSE_FILE}. One or the "
                f"other, not both",
                "opl_agent status, to see whether the agent reported in"]
    if options.get("output_format") == "helm":
        return [f"helm install bzm-opl {out_dir}/helm "
                f"-f {out_dir}/bzm-opl-values.yaml "
                f"-n {options.get('namespace', _DEFAULT_NS)} --create-namespace",
                "opl_agent status, once the release is up"]
    ns = options.get("namespace", _DEFAULT_NS)
    # The bundle's own namespace command and CLI binary (kubectl or oc).
    o = {**bundle_options.DEFAULT_OPTIONS, **options}
    return [readme_parts.create_namespace_cmd(o),
            f"{bundle_options.cli(o)} apply -f {out_dir}/ -n {ns}   (YOU run this -- "
            f"no tool here applies anything)",
            "opl_agent status, to see whether the agent reported in"]


def _after_doctor(report, options):
    """Where a preflight leads: a failing report points at changing something."""
    if not report.get("ok"):
        return ["opl_preflight suggest with the same evidence -- it says which "
                "options this cluster settles and which it only narrows",
                "fix what FAILed, then run this again. A WARN is a read the "
                "cluster refused, not a check that failed."]
    return [f"opl_bundle generate with these options and an absolute out_dir "
            f"(namespace {options.get('namespace', _DEFAULT_NS)!r})"]


def _token_warnings(source):
    """A rotation, in `warnings` too: it is the one outcome that takes a working
    agent down."""
    if source.branch != core.TOKEN_ROTATED:
        return []
    return [core.rotation_warning(source.ship_id)]


def _bundle_warnings(options):
    out = []
    slot = ca_trust.ca_slot_notice(options)
    if slot:
        out.append(slot)
    out += core.ca_bundle_warnings(options)
    if options.get("auto_update"):
        out.append(
            "auto_update is on: crane will take field ownership of its own "
            "Deployment within seconds, and `helm upgrade` will then fail on a "
            "conflict that --force-conflicts cannot resolve.")
    if options.get("restrict_engines") is False:
        out.append(
            "restrict_engines is off: engines will run privileged, which "
            "restricted PodSecurity, OpenShift SCC and GKE Autopilot all "
            "reject -- and they reject it after the agent is online, so the "
            "run hangs at BOOT_STARTING rather than failing at apply.")
    return out


# -- opl_plan ------------------------------------------------------------------

PLAN_ACTIONS = ("capacity",)

# Generated from plan.SIZING_MODELS, so a new model reaches the description.
_PLAN_ARGS = ", ".join(
    f"{m['target_field']}?" + (f", {m['figure_field']}?" if m["figure_field"]
                               else "")
    for m in plan.SIZING_MODELS.values())

_PLAN_MODELS = "".join(
    f"  {m['target_field']:<20} -- {m['unit']}, for {m['name']}. "
    + (f"{m['figure_field']} is how many one pod carries; unset, what a pod "
       f"that size is rated for is assumed and the answer says which.\n"
       if m["figure_field"] else
       f"Stated, never sized: no {m['figure_unit']} figure has been measured "
       f"here and none is invented, so this target reaches no pod count. "
       f"Asked on its own it is a refusal rather than a plan with a number "
       f"nobody measured in it.\n")
    for m in plan.SIZING_MODELS.values())

DESCRIPTIONS["opl_plan"] = (
    "How much infrastructure a sizing needs, before any of it exists.\n"
    f"  capacity -- {{{_PLAN_ARGS}, engine_cpu?, engine_mem?, "
    "engines_per_node?, agents?}\n"
    "The one tool here that reaches nothing: no API key, no account, no "
    "cluster. Use it when someone asks 'what would we need to run this?' "
    "-- typically before there is a cluster to deploy to, because the "
    "answer is what they raise the request for one with.\n"
    "It returns the numbers AND `document`, a ready-to-send "
    "infrastructure request written for a platform team that has never "
    "heard of BlazeMeter. Offer that document -- it is the deliverable, "
    "not a formatting of the numbers.\n"
    "Three sizings, each asked for in its own unit. At least one:\n"
    + _PLAN_MODELS +
    "Where several are given, the largest pod count decides the pool and "
    "`driven_by` names which -- one agent applies a single CPU/memory "
    "pair to every pod it creates, so these are three routes to a count "
    "of pods of one size, not three sizes. Two workloads running at once "
    "want the counts added.\n"
    "A location holds agents, an agent runs engines, and each engine "
    "drives some number of virtual users -- that is the vocabulary to "
    "answer in.\n"
    "`slots` is engines per *agent*, not per location, so a location's "
    "concurrency is agents x slots and `agents` divides the run. Pass "
    "how many agents will serve it; the returned `location.slots` is "
    "already the per-agent figure.\n"
    "Every per-pod figure is a property of the workload rather than of "
    "the pod -- what the script does between requests, what the browser "
    "renders -- so each row says whether its figure was supplied, "
    "assumed or never measured, and `vus_per_engine_assumed` says it for "
    "the load target. Pass that qualifier on rather than reporting the "
    "node count as measured.\n"
    "Nothing here waits for a cluster: the location and its agent can "
    "be created in BlazeMeter now, and an agent that has never sent a "
    "heartbeat is the expected state until the manifests are applied.")


def _plan(action, args):
    if action == "capacity":
        # A call naming no target is refused by the planner, naming `users`.
        return core.capacity_plan(
            args.get("users"),
            vus_per_engine=args.get("vus_per_engine"),
            engine_cpu=args.get("engine_cpu"),
            engine_mem=args.get("engine_mem"),
            engines_per_node=args.get("engines_per_node"),
            agents=args.get("agents"),
            sizings=plan.sizings_from(args))

    raise _unknown(action, PLAN_ACTIONS)


# -- opl_preflight -------------------------------------------------------------

PREFLIGHT_ACTIONS = ("doctor", "suggest", "toolcheck")

DESCRIPTIONS["opl_preflight"] = (
    "Will this land? Checks that run before anything is applied.\n"
    "  doctor    -- the cluster against this configuration {facts, "
    "evidence, options?, namespace?}. It never runs kubectl "
    "itself.\n"
    "  suggest   -- what that same evidence implies the options should "
    "be {evidence, options?}\n"
    "  toolcheck -- this machine's own tools, for the live rig "
    "{cluster?, local_registry?, local_proxy?}\n"
    "`evidence` is the JSON a customer collects: pass the PATH of the "
    "file they sent, which is read here, or the object itself if you "
    "already have it parsed -- do not read a file's contents through "
    "yourself to turn one into the other.\n"
    "A denied read is a WARN, not a FAIL: a cluster that refused a "
    "probe is not a cluster that failed one, and `ok` already knows the "
    "difference.")


def _preflight(action, args):
    if action == "doctor":
        facts, = _need(args, "facts")
        options = dict(args.get("options") or {})
        if args.get("namespace"):
            options["namespace"] = args["namespace"]
        evidence = args.get("evidence")
        if evidence is None:
            raise core.BadRequest(
                f"doctor needs `evidence`: the JSON produced by "
                f"{evidence_mod.SCRIPT}, which someone with cluster "
                f"access runs read-only and sends back "
                f"({RESOURCE_SCHEME}://docs/preflight.md has what to ask them "
                f"for). Pass the path of the file they sent, or the object "
                f"itself. Doctor never runs kubectl, so without one there "
                f"is no cluster to check against -- and a preflight of no "
                f"cluster would report nothing wrong with one you have not "
                f"seen.")
        # This transport shares a filesystem with its caller, so it may pass a
        # path; see core.evidence_document.
        report = core.preflight(facts, options,
                                core.evidence_document(evidence))
        return dict(report, next=_after_doctor(report, options))

    if action == "suggest":
        evidence, = _need(args, "evidence")
        found = core.suggestions_from_evidence(
            core.evidence_document(evidence), args.get("options"))
        return dict(found, next=[
            "apply the ones you agree with as opl_bundle generate options -- "
            "nothing here is applied for you",
            "opl_preflight doctor with the same evidence, to check the result"])

    if action == "toolcheck":
        return core.toolcheck(cluster=args.get("cluster"),
                              local_registry=args.get("local_registry"),
                              local_proxy=bool(args.get("local_proxy")))

    raise _unknown(action, PREFLIGHT_ACTIONS)


# -- opl_agent -----------------------------------------------------------------

AGENT_ACTIONS = ("status", "triage", "livetest")

DESCRIPTIONS["opl_agent"] = (
    "The deployed agent.\n"
    "  status   -- is it reporting? {harbor_id, ship_id}. State alone "
    "reads as healthy forever, so this is really about the heartbeat.\n"
    "  triage   -- why not, or why a run hangs at BOOT_STARTING {namespace, "
    "since?, crane_log_lines?}. Reads events, pods and crane's log with "
    "this machine's kubectl or oc context and writes nothing. Each "
    "finding names the option or action that fixes it; `unread` lists "
    "reads the cluster refused, which are not findings, and "
    "`unrecognised` lists warnings no rule knows. Report both.\n"
    "  livetest -- deploy a bundle to a cluster and wait for the agent "
    "{manifests, namespace, harbor_id, ship_id, cluster?, timeout?}. "
    "Off unless " + ENABLE_LIVETEST_ENV + "=1, blocks for minutes, and "
    "the full rig is the `bzm-opl-gen livetest` command. Manifests only: "
    "a --format docker bundle is refused here and started with "
    "`bzm-opl-gen livetest --manifests <dir>`, which brings it up with "
    "docker compose on the host and needs no namespace and no cluster.\n"
    "An online agent is not proof a test runs: for that, run one "
    "through the blazemeter_execution MCP server if this session has "
    "it.")


def _agent(action, args):
    if action == "status":
        harbor_id, ship_id = _need(args, "harbor_id", "ship_id")
        st = core.agent_status(_client(args), harbor_id, ship_id)
        return dict(st, next=_after_status(st))

    if action == "triage":
        namespace, = _need(args, "namespace")
        report = core.triage(namespace, since=args.get("since"),
                             log_lines=args.get("crane_log_lines"))
        return dict(report, next=_after_triage(report))

    if action == "livetest":
        _gate(ENABLE_LIVETEST_ENV,
              "the live rig (it deploys to a cluster and starts real work)")
        manifests, namespace, harbor_id, ship_id = _need(
            args, "manifests", "namespace", "harbor_id", "ship_id")
        ok = livetest.run(_client(args), manifests, namespace, harbor_id,
                          ship_id, cluster=_given(args, "cluster", "current"),
                          timeout=_given(args, "timeout", 600),
                          keep=bool(args.get("keep")))
        return {"ok": bool(ok),
                "next": (["opl_agent status, and then a real test through the "
                          "blazemeter_execution server if this session has it"]
                         if ok else
                         ["kubectl -n " + namespace + " logs -l "
                          "role=role-crane --tail=50"]),
                "note": "this is the plain deploy-and-wait. The local "
                        "registry, proxy, negative control and engine run are "
                        "`bzm-opl-gen livetest` flags -- they need a shell and "
                        "12-20 minutes."}

    raise _unknown(action, AGENT_ACTIONS)


def _after_status(st):
    if st.get("online"):
        return ["the agent is reporting. To prove it actually runs work, use "
                "the blazemeter_tests / blazemeter_execution MCP server to run "
                "a real test against this location -- an online agent is not "
                "the same as an engine that started."]
    if st.get("heartbeat_age_s") is None:
        return ["no heartbeat ever: the agent has not reached BlazeMeter. "
                "Check the pod is running and its AUTH_TOKEN is current -- a "
                "stale token logs 404 on /ships/<id>/status and sits at 0/1.",
                "opl_agent triage with the namespace, where this machine has "
                "cluster access: it names the known failures and their fixes"]
    return ["the agent reported once and has gone quiet.",
            "opl_agent triage with the namespace, where this machine has "
            "cluster access: it names the known failures and their fixes"]


def _after_triage(report):
    """Where a triage leads: a finding's fix, else what the report could not
    see."""
    if not report["ok"]:
        return ["apply the fix each FAIL names, regenerating with opl_bundle "
                "generate where it names an option, then run this again"]
    steps = []
    if report["unread"]:
        steps.append("some reads were refused, so a clean report is not a "
                     "clean namespace: ask someone with read access to events, "
                     "pods and pods/log in it to run bzm-opl-gen triage")
    if report["unrecognised"]:
        steps.append("no rule knows the unrecognised warnings: pass them on "
                     "as found rather than guessing a cause")
    return steps or ["nothing known is wrong in the namespace. opl_agent "
                     "status says whether the agent is reporting"]


# -- the server ----------------------------------------------------------------

def _answer(fn, action, args):
    """Run one action and hand back its JSON as text.

    CoreError becomes the SDK's ToolError, whose message reaches the model
    intact; any other exception reaches the client with its message withheld.
    stdout is redirected to stderr throughout, because on stdio it is the
    JSON-RPC channel and the layers underneath print freely.
    """
    with contextlib.redirect_stdout(sys.stderr):
        try:
            return json.dumps(fn(action, _args(args)), indent=2, default=str)
        except core.CoreError as e:
            raise ToolError(str(e)) from None


def build():
    """A fresh server. Nothing is captured from the environment at build time."""
    srv = MCPServer(name=SERVER_NAME, version=__version__,
                    instructions=INSTRUCTIONS)

    @srv.tool(name="opl_location",
              annotations=_ANNOTATIONS["opl_location"],
              description=DESCRIPTIONS["opl_location"])
    def opl_location(action: Literal[LOCATION_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_location, action, args)

    @srv.tool(name="opl_facts",
              annotations=_ANNOTATIONS["opl_facts"],
              description=DESCRIPTIONS["opl_facts"])
    def opl_facts(action: Literal[FACTS_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_facts, action, args)

    @srv.tool(name="opl_bundle",
              annotations=_ANNOTATIONS["opl_bundle"],
              description=DESCRIPTIONS["opl_bundle"])
    def opl_bundle(action: Literal[BUNDLE_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_bundle, action, args)

    @srv.tool(name="opl_plan",
              annotations=_ANNOTATIONS["opl_plan"],
              description=DESCRIPTIONS["opl_plan"])
    def opl_plan(action: Literal[PLAN_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_plan, action, args)

    @srv.tool(name="opl_preflight",
              annotations=_ANNOTATIONS["opl_preflight"],
              description=DESCRIPTIONS["opl_preflight"])
    def opl_preflight(action: Literal[PREFLIGHT_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_preflight, action, args)

    @srv.tool(name="opl_agent",
              annotations=_ANNOTATIONS["opl_agent"],
              description=DESCRIPTIONS["opl_agent"])
    def opl_agent(action: Literal[AGENT_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_agent, action, args)

    for name in doc_files():
        _add_doc(srv, name)
    return srv


def _add_doc(srv, name):
    """Serve one doc file, read at call time so edits show without a restart."""
    def read():
        # `name` by closure: the SDK reads handler parameters as URI variables.
        with open(os.path.join(docs_dir(), name), encoding="utf-8") as fh:
            return fh.read()

    # The SDK keys handlers by function name, so each needs its own.
    read.__name__ = "doc_" + name.replace(".", "_").replace("-", "_")
    srv.resource(f"{RESOURCE_SCHEME}://docs/{name}", name=name,
                 mime_type="text/markdown",
                 description=DOC_SUMMARIES.get(name, f"Reference: {name}"))(read)


def main():
    """Serve on stdio. Nothing may print to stdout: it is the JSON-RPC channel."""
    import anyio
    anyio.run(build().run_stdio_async)
