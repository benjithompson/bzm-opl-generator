"""MCP over core.py: the tool for an AI session that has no checkout of this repo.

`bzm-opl-gen mcp` speaks stdio JSON-RPC. The caller it is written for is an SE
sitting in a customer's directory with a cluster, a BlazeMeter account, and none
of this repository -- so the tool descriptions, the `instructions` block and the
shipped docs are the entire documentation. Anything a session needs to know that
is not in one of those three does not exist as far as it is concerned.

Five tools, each dispatching on an `action`, which is the shape the sibling
BlazeMeter servers already use: a session that has those does not have to learn
a second convention. The actions are a `Literal`, so they land in the schema as
an enum and a wrong one is refused by the client's own validation, naming the
valid ones, rather than arriving here to be guessed at.

Three rules this layer keeps that core does not:

  **The AUTH_TOKEN is never in a response.** It is written to disk inside the
  Secret and that is all -- `generate` answers with file names and byte counts.
  A response goes into a transcript, gets summarised, and is quoted back; a
  credential that rotates on every fetch must not travel that way. `reveal_token`
  is the single exception, and it is a whole action so that it cannot happen as
  a side effect of something else.

  **A secret is never an argument.** A *path* may be (`api_key_file`); the id
  and secret come from the environment of whatever launched the server, because
  arguments pass through everything between the caller and here.

  **Nothing writes to a cluster, with one gated exception.** The cluster reads
  are reads. Applying is the session's own `kubectl`, which is also the only way
  the person watching sees what was applied. The exception is `opl_agent
  livetest`, which deploys because that is the whole of what it does -- and is
  why it is off unless its own variable is set, rather than sharing the
  destructive one.
"""

import contextlib
import json
import os
import sys
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import (__version__, core, doctor, generate as gen_mod,
               facts as facts_mod, livetest)

SERVER_NAME = "bzm-opl-gen"
RESOURCE_SCHEME = "bzm-opl"

# Both gates are read at call time, not at build time. A client that sets the
# variable and expects the next call to work is right to; refusing until the
# server is restarted would make the message below a lie about what is needed.
ENABLE_LIVETEST_ENV = "BZM_OPL_ENABLE_LIVETEST"
ALLOW_DESTRUCTIVE_ENV = "BZM_OPL_ALLOW_DESTRUCTIVE"


INSTRUCTIONS = f"""\
Generate and verify a BlazeMeter private-location (OPL) agent deployment for
Kubernetes or OpenShift, from a real account rather than from a template.

The path through it:

  1. opl_location list          -- find the location, then `show` for its ship
                                   (agent). Accounts hold hundreds of
                                   locations, so `list` is one line each and
                                   capped: narrow it with name_contains, and
                                   read the omitted counts it comes back with
                                   rather than treating the list as the account.
  2. opl_facts gather           -- the images and ids that location actually uses
  3. opl_preflight doctor       -- will this cluster take it? (needs evidence)
  4. opl_bundle generate        -- write the manifests to a directory
  5. kubectl apply -f <dir>     -- YOU run this, in your own shell
  6. opl_agent status           -- did the agent come online?

Step 5 is deliberately not a tool. This server does not apply anything to a
cluster: the person you are working with needs to see what is being applied to
theirs, and `kubectl apply` in their shell is where they see it. The same goes
for `helm install` when the bundle is a chart. (The one tool that does deploy is
opl_agent livetest, which is off unless its own variable is set.)

Facts without an account: `opl_facts manual` builds the same structure from a
harbor id and ship id read off the BlazeMeter UI, so you can produce a bundle
for a customer whose account you cannot reach. It cannot know which browser
image a GUI location uses -- only a live agent reports that -- and says so
rather than guessing.

Preflight without a cluster: `opl_preflight doctor` reads a cluster *evidence*
file, which the customer collects and sends. It never runs kubectl here. If
you have not got one, say so -- do not report a preflight you did not run.

Reference, readable as resources on this server ({RESOURCE_SCHEME}://docs/...):
options.md (every generate option), preflight.md (evidence files and what the
checks mean), helm.md, service-virtualization.md, hardened-engines.md,
live-test.md. Read the one that covers the question rather than guessing at an
option name -- `opl_bundle options` lists them all with a one-line summary each.

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
0/1 logging 404 on its status endpoint, which reads like a deleted ship. That is
why the token is written into the bundle and never returned to you, and why the
two actions that can issue one -- `opl_bundle generate` with rotate_token=true,
and `opl_location reveal_token` -- have to be asked for by name. Generating
without rotate_token touches no credential at all: it reuses the token already in
out_dir, or leaves a placeholder and says so. Every generate reports which of
those happened as `token_source`; read it before you deploy.
"""


# Filled in beside each tool's actions and dispatch function, below --
# the description, the action list and the code that reads them are three
# statements of the same thing, and they go stale as a set.
DESCRIPTIONS = {}

# Kept together, unlike the descriptions: what a client is told about
# side effects is a property of the whole surface, and the five want
# reading against each other rather than one at a time.
_ANNOTATIONS = {
    "opl_location": ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                    idempotent_hint=False, open_world_hint=True),
    "opl_facts": ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=True),
    "opl_bundle": ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                    idempotent_hint=False, open_world_hint=False),
    "opl_preflight": ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
    "opl_agent": ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                    idempotent_hint=False, open_world_hint=True),
}

# -- the docs this server serves ----------------------------------------------

def docs_dir():
    """Where the shipped documentation is.

    Two places, because there are two ways this is installed. A wheel carries
    the doc files inside the package (see the `bzm_opl_gen.docs` mapping in
    pyproject.toml); a checkout has them at the repo root, which is where they
    are edited and where every relative link between them resolves. One copy on
    disk either way -- the wheel's is built from the checkout's.
    """
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


# What each doc is for, so a session can pick one without opening all of them.
# Names not listed still ship -- the description falls back to the file name --
# because a doc that is missing from here should be undescribed, not unserved.
DOC_SUMMARIES = {
    "options.md": "Every generate option: what it does, what it defaults to, "
                  "and what breaks if it is wrong.",
    "preflight.md": "Cluster evidence files: what to ask the customer to "
                    "collect, and what each verdict means.",
    "helm.md": "The chart output format, and managing the release afterwards.",
    "service-virtualization.md": "Mock-service locations: the ingress backends, "
                                 "and which combinations are refused.",
    "hardened-engines.md": "The restricted engine posture, and which images "
                           "have run under it.",
    "live-test.md": "The live rig: what it proves and what it costs.",
    "web-ui.md": "The local web UI, for a human doing this by hand.",
    "crane-nginx-ingress-port.md": "Why a published mock endpoint 503s, and "
                                   "the command that fixes it.",
    "mcp.md": "This server: its tools, its gates, and what it will not do.",
}


# -- argument handling ---------------------------------------------------------

def _args(args):
    """Tool arguments as a dict, whatever a client sent for "no arguments".

    None and {} both happen, and a client that omits the field entirely is not
    making a mistake worth refusing.
    """
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise core.BadRequest(
            f"args must be an object, not {type(args).__name__}")
    return args


def _need(args, *names):
    """Required arguments, refused by name.

    All of them named in one message rather than one per round trip: a session
    that has to discover three missing arguments one at a time spends three
    calls learning what one sentence could have said.
    """
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise core.BadRequest(
            f"missing required argument(s): {', '.join(missing)}")
    return [args[n] for n in names]


def _no_secrets(options):
    """Refuse a credential passed as an option, rather than writing it.

    `auth_token` is a real generate option and the UI sets it, so this is not
    an impossible argument -- it is one that must not arrive *this* way. A
    secret in a tool call has already travelled through the model, the
    transcript and whatever logs either of those keeps; by the time it reaches
    here the damage is upstream, and the only thing left worth doing is to
    refuse loudly enough that nobody sends the next one.

    Where the token should come from instead is the point of the refusal, and
    since #64 that is no longer "the account, automatically" -- the alternatives
    are a bundle already in out_dir, the value set on disk afterwards, or
    rotate_token, which issues a new one and revokes whatever is running.
    """
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


# The argument this used to take. Kept named rather than dropped, because a
# session working from a cached tool description would send it, get a bundle with
# a placeholder in it where it expected a working credential, and be told
# nothing. Refusing costs one round trip and says what the word is now.
_RENAMED_TOKEN_ARG = "fetch_token"


def _no_stale_fetch_token(args):
    if _RENAMED_TOKEN_ARG in args:
        raise core.BadRequest(
            f"{_RENAMED_TOKEN_ARG} is no longer an argument -- it is "
            f"`rotate_token`, and it defaults to false. The rename is the "
            f"warning: that call POSTs for a new AUTH_TOKEN and the previous one "
            f"stops working, so any agent running on it sits at 0/1 Running "
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
    return core.client_from_env(args.get("api_key_file"))


# -- opl_location --------------------------------------------------------------

LOCATION_ACTIONS = ("list", "show", "whoami", "create", "create_ship",
                    "reveal_token", "delete")

DESCRIPTIONS["opl_location"] = (
    "BlazeMeter private locations (harbors) and their agents (ships).\n"
    "  list         -- one line per location {account_id?, workspace_id?, "
    "name_contains?, limit?}. Defaults to this key's own account and to the "
    f"first {core.DEFAULT_LOCATION_LIMIT}; accounts hold hundreds, so narrow "
    "with name_contains rather than raising limit. Whatever it leaves out is "
    "counted in the response.\n"
    "  show         -- one location with its ships in full {harbor_id}\n"
    "  whoami       -- who this API key is, and its default account\n"
    "  create       -- a new private location {name, account_id, "
    "workspace_id, func_ids?, slots?, threads_per_engine?}\n"
    "  create_ship  -- a new agent in a location {harbor_id, name}\n"
    "  reveal_token -- the ship's AUTH_TOKEN {harbor_id, ship_id}. "
    "ROTATES it: the previous token stops working and any agent "
    "running on it goes to 0/1. Use only when re-applying that agent.\n"
    "  delete       -- delete a location and every ship in it "
    "{harbor_id}. Off unless " + ALLOW_DESTRUCTIVE_ENV + "=1.\n"
    "create/create_ship/delete change a real customer account -- "
    "confirm with the person before calling them.")


def _location(action, args):
    if action == "whoami":
        u = core.user(_client(args))
        return {"email": u.get("email"), "display_name": u.get("displayName"),
                "default_account_id": (u.get("defaultProject") or {}).get("accountId"),
                "next": ["opl_location list, with that account_id"]}

    if action == "list":
        client = _client(args)
        account_id, workspace_id = args.get("account_id"), args.get("workspace_id")
        if not account_id and not workspace_id:
            # Rather than refusing: the account this key defaults to is almost
            # always the one meant, and one round trip is cheaper than a
            # refusal the session then has to work out how to satisfy.
            account_id = (core.user(client).get("defaultProject") or {}).get("accountId")
            core.require_location_scope(account_id, workspace_id)
        locs = core.locations(client, account_id, workspace_id)
        # `.get(key, default)` fills in only an *absent* key, and `{"limit":
        # null}` is an ordinary way for a client to say "unset" -- which reached
        # core as "no cap" and handed back the whole account this action exists
        # to keep out of a result. Uncapped is not offered here on purpose:
        # raising the cap is a number, and there is no size a session's result
        # budget cannot be broken by.
        limit = args.get("limit")
        sel = core.select_locations(
            locs, name_contains=args.get("name_contains"),
            limit=core.DEFAULT_LOCATION_LIMIT if limit is None else limit)
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
            # In prose as well as in the counts above. A session summarising
            # this reads the sentence; the fields are what it can compute with,
            # and the one thing that must survive both is that the list is
            # partial.
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
        loc = core.create_location(
            _client(args), name, account_id, workspace_id,
            func_ids=args.get("func_ids") or ["performance"],
            slots=args.get("slots", 1),
            threads_per_engine=args.get("threads_per_engine",
                                        core.api.DEFAULT_THREADS_PER_ENGINE))
        return {"location": _location_summary(loc),
                "next": [f"opl_location create_ship with harbor_id "
                         f"{loc.get('id')!r} -- a location with no ship has "
                         f"nothing to deploy"]}

    if action == "create_ship":
        harbor_id, name = _need(args, "harbor_id", "name")
        ship = core.create_ship(_client(args), harbor_id, name)
        return {"harbor_id": harbor_id, "ship": ship,
                "next": [f"opl_facts gather with harbor_id {harbor_id!r}"],
                # Not issued here on purpose: it would rotate a token on an
                # action whose name says nothing about credentials. And nothing
                # else issues one by accident either, so the session has to be
                # told which action to ask for -- `generate` on its own leaves a
                # placeholder in the Secret.
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
              "deleting a private location (and every ship in it)")
        return dict(core.delete_location(_client(args), harbor_id),
                    next=["any agent still deployed for it is now orphaned: "
                          "kubectl delete -f <its bundle>"])

    raise _unknown(action, LOCATION_ACTIONS)


def _location_summary(loc):
    """A location as a session needs it: the ids it will pass on, and the two
    fields that decide whether a bundle can be generated at all.

    One location's worth, for `show` and `create`. A listing uses
    _location_brief -- per-ship detail on 171 locations is the size problem
    this pair exists to separate.
    """
    return {"harbor_id": loc.get("id"), "name": loc.get("name"),
            "slots": loc.get("slots"), "func_ids": loc.get("funcIds"),
            "ships": [{"ship_id": s.get("id"), "name": s.get("name"),
                       "state": s.get("state"),
                       # null where the payload carried no heartbeat -- see
                       # core.ship_reporting. opl_agent status is the authority.
                       "reporting": core.ship_reporting(s)}
                      for s in loc.get("ships", [])]}


def _location_brief(loc):
    """One location as a *listing* entry: enough to pick one and go on.

    An account with 171 locations and 221 ships listed the long way came back
    at 84,779 characters, past the caller's result ceiling, so step 1 of the
    path never completed. Almost all of it was per-ship detail about locations
    the caller was never going to choose -- and choosing needs only whether
    there is an agent there and whether anything is alive. `show` pays for the
    detail on the one that gets picked.
    """
    ships = loc.get("ships") or []
    reporting = [core.ship_reporting(s) for s in ships]
    return {"harbor_id": loc.get("id"), "name": loc.get("name"),
            "func_ids": loc.get("funcIds"), "slots": loc.get("slots"),
            "ship_count": len(ships),
            # Two counts, because one cannot carry both facts. `ships_reporting`
            # counts only agents the payload vouches for, so a location with one
            # live agent and one heartbeat-less record still shows the live one
            # -- reporting the pair as wholly unknown lost exactly the "one of
            # two" signal a count exists to give. `ships_unknown` is how a
            # reader tells 0-because-we-looked from 0-because-we-could-not, so
            # nobody redeploys a working agent on the strength of a zero. Where
            # nothing at all is vouched for, `ships_reporting` is null rather
            # than 0: with every ship unknown there is no count to give, and a 0
            # beside it would be read as "none alive".
            "ships_reporting": (None if reporting and all(r is None
                                                          for r in reporting)
                                else sum(1 for r in reporting if r)),
            "ships_unknown": sum(1 for r in reporting if r is None)}


def _omission_note(sel, name_contains):
    """The counts as a sentence, when something is missing from the list.

    Absent when nothing was left out, so its presence means the list is
    partial. A list that quietly stops reads as the whole account, and "that
    location does not exist" about one that was merely omitted is a worse
    answer than a response that was too big.
    """
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
    "ids, and what the location is enabled for.\n"
    "  gather -- read them from the account {harbor_id}\n"
    "  manual -- build the same structure from ids read off the "
    "BlazeMeter UI {harbor_id, ship_id, func_ids?}, for a customer "
    "whose account you cannot reach\n"
    "Pass the `facts` object straight to opl_bundle and opl_preflight.")


def _facts(action, args):
    if action == "gather":
        harbor_id, = _need(args, "harbor_id")
        facts = core.gather_facts(_client(args), harbor_id)
    elif action == "manual":
        harbor_id, ship_id = _need(args, "harbor_id", "ship_id")
        facts = core.manual_facts(
            harbor_id, ship_id,
            func_ids=args.get("func_ids") or ["performance"])["facts"]
    else:
        raise _unknown(action, FACTS_ACTIONS)
    # However they arrived, the answer is the same shape -- which is the point
    # of facts.manual() returning what gather() returns.
    return {"facts": facts, "warnings": _facts_warnings(facts),
            "next": _after_facts(facts)}


def _after_facts(facts):
    return [f"opl_preflight doctor -- with a cluster evidence file, if you "
            f"have one",
            f"opl_bundle generate with these facts, harbor_id "
            f"{facts.get('harbor_id')!r}, and an absolute out_dir"]


def _facts_warnings(facts):
    """What these facts cannot tell you, said once at the point they are made.

    The GUI image gap is the one that matters: the account carries 60+
    version-pinned browser repos and only a live agent says which a location
    uses, so a bundle built without one selects an image that may not be the
    right version. There is no default worth inventing.
    """
    out = []
    if facts_mod.gui_images_incomplete(facts):
        out.append(
            "this location runs GUI/browser tests, and these facts carry no "
            "browser image. Only a live agent reports which of the account's "
            "pinned browser images it uses -- ask for the agent's image list, "
            "or expect the browser engines to fail to pull.")
    return out


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
    "all?}. With mirror=<prefix> it also pulls them and pushes them "
    "into that registry, which writes to it -- confirm before "
    "calling it that way.\n"
    "Applying the bundle is yours: `kubectl apply -f <out_dir>`. No "
    "action on this tool touches a cluster at all.")


def _bundle(action, args):
    if action == "options":
        # Both halves together: the default is what you get, the summary is
        # what it means, and a session picking options needs them side by side.
        docs = core.option_docs()
        return {name: dict(docs[name], default=default)
                for name, default in core.option_defaults().items()}

    if action == "generate":
        facts, out_dir = _need(args, "facts", "out_dir")
        _no_stale_fetch_token(args)
        # Before the resolution below, not at the write: a rotation that is then
        # thrown away by a path refusal has still killed a running agent.
        core.require_absolute_out_dir(out_dir)
        options = _no_secrets(args.get("options") or {})
        rotate = bool(args.get("rotate_token"))
        # Resolved here rather than left to generate_bundle so the branch can be
        # reported: which of the four ways the token arrived decides whether an
        # agent is still running, and this surface used to answer a rotation with
        # `warnings: []`. A copy, because resolving mutates -- and the caller's
        # own `args` must not come back carrying a credential.
        resolved = dict(options)
        # `announce` is left unset on purpose: stdout is the JSON-RPC channel
        # here, so there is nowhere to say a thing *before* it happens. The
        # ordering the warning exists for cannot be had, and `token_source`
        # afterwards is what a session gets instead.
        source = core.resolve_auth_token(
            facts, resolved,
            # A client only for the one branch that needs an account. Anything
            # else must not require a key, and must not be able to spend one.
            client=_client(args) if rotate else None,
            rotate=rotate, out_dir=out_dir)
        # out_dir to both, so the second resolution inside generate_bundle takes
        # the same branch this one did rather than a different one.
        files = core.generate_bundle(facts, resolved, out_dir=out_dir)
        written = core.write_bundle(files, out_dir)
        return {"out_dir": out_dir, "files": written,
                "profile": json.loads(files[gen_mod.PROFILE_FILE]),
                # The branch and the ship, never the value: naming the agent
                # whose credential was just replaced is the whole point, and it
                # is not a secret.
                "token_source": {"branch": source.branch,
                                 "ship_id": source.ship_id,
                                 "message": source.message},
                "warnings": (_facts_warnings(facts) + _bundle_warnings(options)
                             + _token_warnings(source)),
                "next": _after_generate(out_dir, options)}

    if action == "read":
        out_dir, name = _need(args, "out_dir", "name")
        content, redacted = core.redact_tokens(
            core.read_bundle_file(out_dir, name))
        # Otherwise this is a second way to get the token, and a quiet one:
        # `read bzm_secret.yaml` does not look like asking for a credential the
        # way `reveal_token` does, which is the whole reason that action exists.
        return {"out_dir": out_dir, "name": name, "content": content,
                "redacted_fields": redacted,
                "next": [f"kubectl apply -f {out_dir}/ -n <namespace>, when "
                         f"the bundle reads right"],
                **({"note": "the AUTH_TOKEN in this file is redacted here and "
                            "intact on disk. opl_location reveal_token returns "
                            "the value -- and rotates it."} if redacted else {})}

    if action == "images":
        facts, = _need(args, "facts")
        refs = core.bundle_images(facts, all_images=bool(args.get("all")))
        if not (args.get("pull") or args.get("mirror")):
            return {"images": refs,
                    "next": ["pass mirror=<registry-prefix> to copy these into "
                             "a private registry, or run the bundle's "
                             "bzm-opl-image-mirror.sh yourself"]}
        # Not behind the destructive gate, unlike `delete`, and the difference
        # is what the two do: mirroring *adds* images to a registry the caller
        # named, and the worst case is repositories nobody wanted. Deleting a
        # location destroys an agent and its ships with nothing to restore from.
        # The tool's destructiveHint is what makes a client confirm this one.
        return core.mirror_images(
            refs, mirror=args.get("mirror"),
            platform=args.get("platform", "linux/amd64"),
            dry_run=bool(args.get("dry_run")))

    raise _unknown(action, BUNDLE_ACTIONS)


def _after_generate(out_dir, options):
    if options.get("output_format") == "helm":
        return [f"helm install bzm-opl {out_dir}/helm "
                f"-f {out_dir}/bzm-opl-values.yaml "
                f"-n {options.get('namespace', 'blazemeter')} --create-namespace",
                "opl_agent status, once the release is up"]
    ns = options.get("namespace", "blazemeter")
    return [f"kubectl create namespace {ns}",
            f"kubectl apply -f {out_dir}/ -n {ns}   (YOU run this -- no "
            f"tool here applies anything)",
            "opl_agent status, to see whether the agent reported in"]


def _after_doctor(report, options):
    """Where a preflight leads, which depends on what it found.

    A clean report and a failing one want opposite next moves, and the failing
    one is where a session is most likely to carry on regardless -- so the
    suggestion to go and change something is attached to the verdict itself.
    """
    if not report.get("ok"):
        return ["opl_preflight suggest with the same evidence -- it says which "
                "options this cluster settles and which it only narrows",
                "fix what FAILed, then run this again. A WARN is a read the "
                "cluster refused, not a check that failed."]
    return [f"opl_bundle generate with these options and an absolute out_dir "
            f"(namespace {options.get('namespace', 'blazemeter')!r})"]


def _token_warnings(source):
    """A rotation, in the field a session actually reads.

    `token_source` says it too, but a caller that scans `warnings` for what went
    wrong would miss the one event here that takes a working agent down -- and
    that is exactly what happened: the issue reports a live rotation answering
    `warnings: []`. Only the rotation warns; the other three branches change
    nothing about what is deployed.
    """
    if source.branch != core.TOKEN_ROTATED:
        return []
    return [core.rotation_warning(source.ship_id)]


def _bundle_warnings(options):
    out = []
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


# -- opl_preflight -------------------------------------------------------------

PREFLIGHT_ACTIONS = ("doctor", "suggest", "toolcheck")

DESCRIPTIONS["opl_preflight"] = (
    "Will this land? Checks that run before anything is applied.\n"
    "  doctor    -- the cluster against this configuration {facts, "
    "evidence, options?, namespace?}. This server never runs kubectl "
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
            # The collector is named from doctor's own constant rather than
            # spelled out here. This message used to offer `doctor --collect`, a
            # flag that existed in this string and nowhere else in the tool --
            # and a session with no checkout has no way to find that out.
            raise core.BadRequest(
                f"doctor needs `evidence`: the JSON produced by "
                f"{doctor.EVIDENCE_SCRIPT}, which someone with cluster "
                f"access runs read-only and sends back "
                f"({RESOURCE_SCHEME}://docs/preflight.md has what to ask them "
                f"for). Pass the path of the file they sent, or the object "
                f"itself. This server never runs kubectl, so without one there "
                f"is no cluster to check against -- and a preflight of no "
                f"cluster would report nothing wrong with one you have not "
                f"seen.")
        # A path is resolved here rather than inside core.preflight: this is the
        # transport whose caller shares a filesystem with the server, so it is
        # the one that may name a local file. See core.evidence_document.
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

AGENT_ACTIONS = ("status", "livetest")

DESCRIPTIONS["opl_agent"] = (
    "The deployed agent.\n"
    "  status   -- is it reporting? {harbor_id, ship_id}. State alone "
    "reads as healthy forever, so this is really about the heartbeat.\n"
    "  livetest -- deploy a bundle to a cluster and wait for the agent "
    "{manifests, namespace, harbor_id, ship_id, cluster?, timeout?}. "
    "Off unless " + ENABLE_LIVETEST_ENV + "=1, blocks for minutes, and "
    "the full rig is the `bzm-opl-gen livetest` command.\n"
    "An online agent is not proof a test runs: for that, run one "
    "through the blazemeter_execution MCP server if this session has "
    "it.")


def _agent(action, args):
    if action == "status":
        harbor_id, ship_id = _need(args, "harbor_id", "ship_id")
        st = core.agent_status(_client(args), harbor_id, ship_id)
        return dict(st, next=_after_status(st))

    if action == "livetest":
        _gate(ENABLE_LIVETEST_ENV,
              "the live rig (it deploys to a cluster and starts real work)")
        manifests, namespace, harbor_id, ship_id = _need(
            args, "manifests", "namespace", "harbor_id", "ship_id")
        ok = livetest.run(_client(args), manifests, namespace, harbor_id,
                          ship_id, cluster=args.get("cluster", "current"),
                          timeout=args.get("timeout", 600),
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
                "kubectl -n <namespace> logs -l role=role-crane --tail=50"]
    return ["the agent reported once and has gone quiet.",
            "kubectl -n <namespace> logs -l role=role-crane --tail=50"]


def _unknown(action, valid):
    return core.BadRequest(
        f"unknown action {action!r}. This tool takes: {', '.join(valid)}")


# -- the server ----------------------------------------------------------------

def _answer(fn, action, args):
    """Run one action and hand back its JSON.

    Text, not a structured result: these actions return whatever shape suits
    the question, and the SDK's structured output wants one declared type per
    tool. JSON in a text block is what the model reads either way.

    CoreError becomes a plain exception so the SDK reports it as a tool error
    with the message intact -- every one of them is a sentence written for
    whoever has to fix it.

    **stdout is redirected to stderr for the duration.** On stdio transport
    stdout *is* the JSON-RPC channel, and one stray line desynchronises the
    session -- the client stops being able to parse anything, which does not
    look like a print, it looks like the server died. The layers underneath
    were written for a command line and print freely: `workstation.run` writes
    a seven-line report, `livetest.run` narrates a whole deployment. Rather
    than hunting those down one at a time and re-hunting them whenever
    something new is called, the channel is protected here, once, for
    everything. stderr is where a client shows server logs anyway.
    """
    with contextlib.redirect_stdout(sys.stderr):
        try:
            return json.dumps(fn(action, _args(args)), indent=2, default=str)
        except core.CoreError as e:
            raise ValueError(str(e)) from None


def build():
    """A fresh server. Built per call rather than at import so that tests get a
    clean one and so nothing is captured from the environment at import time --
    both gates are read when an action runs."""
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
    """Serve one doc file.

    Read at call time, not at build: in a checkout these are being edited while
    the server runs, and a resource that answers with what the file said at
    startup is worse than one that is slightly slower.
    """
    def read():
        # `name` by closure, not as a defaulted parameter: the SDK reads a
        # handler's parameters as URI template variables, and a resource whose
        # URI has none is refused for declaring one.
        with open(os.path.join(docs_dir(), name), encoding="utf-8") as fh:
            return fh.read()

    # The SDK derives the resource's handler identity from the function name,
    # so nine closures all called `read` collide on registration.
    read.__name__ = "doc_" + name.replace(".", "_").replace("-", "_")
    srv.resource(f"{RESOURCE_SCHEME}://docs/{name}", name=name,
                 mime_type="text/markdown",
                 description=DOC_SUMMARIES.get(name, f"Reference: {name}"))(read)


def main():
    """Serve on stdio.

    Nothing may print to stdout here -- it is the JSON-RPC channel, and one
    stray line makes the session unparseable to the client. Everything this
    package prints for a human goes through the CLI, which is a different
    entry point.
    """
    import anyio
    anyio.run(build().run_stdio_async)
