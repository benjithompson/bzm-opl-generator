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

  **Nothing writes to a cluster.** The cluster reads are reads. Applying is the
  session's own `kubectl`, which is also the only way the person watching sees
  what was applied.
"""

import json
import os
from typing import Any, Literal

from . import core, generate as gen_mod, facts as facts_mod, livetest

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

  1. opl_location list          -- find the location and its ship (agent)
  2. opl_facts gather           -- the images and ids that location actually uses
  3. opl_preflight doctor       -- will this cluster take it? (needs evidence)
  4. opl_bundle generate        -- write the manifests to a directory
  5. kubectl apply -f <dir>     -- YOU run this, in your own shell
  6. opl_agent status           -- did the agent come online?

Step 5 is deliberately not a tool. This server never writes to a cluster: the
person you are working with needs to see what is being applied to theirs, and
`kubectl apply` in their shell is where they see it. The same goes for `helm
install` when the bundle is a chart.

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
pass a secret as a tool argument. And fetching an agent's AUTH_TOKEN *rotates*
it: the previous token stops working, and an agent already running on it sits at
0/1 logging 404 on its status endpoint, which reads like a deleted ship. That is
why the token is written into the bundle and never returned to you.
"""


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


def _gate(env, what):
    if os.environ.get(env) not in ("1", "true", "yes"):
        raise core.BadRequest(
            f"{what} is disabled. Set {env}=1 in this server's environment to "
            f"allow it -- it is off by default because it cannot be undone "
            f"from here.")


def _client(args):
    return core.client_from_env(args.get("api_key_file"))


# -- opl_location --------------------------------------------------------------

LOCATION_ACTIONS = ("list", "whoami", "create", "create_ship", "reveal_token",
                    "delete")


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
        return {"account_id": account_id,
                "locations": [_location_summary(l) for l in locs],
                "next": ["opl_facts gather, with the harbor_id of the one you want"]}

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
                # Not fetched here on purpose: it would rotate a token on an
                # action whose name says nothing about credentials.
                "note": "the AUTH_TOKEN is not fetched by this call. "
                        "opl_bundle generate puts it in the bundle; "
                        "opl_location reveal_token returns it and rotates it."}

    if action == "reveal_token":
        harbor_id, ship_id = _need(args, "harbor_id", "ship_id")
        return core.reveal_token(_client(args), harbor_id, ship_id)

    if action == "delete":
        harbor_id, = _need(args, "harbor_id")
        _gate(ALLOW_DESTRUCTIVE_ENV,
              "deleting a private location (and every ship in it)")
        return core.delete_location(_client(args), harbor_id)

    raise _unknown(action, LOCATION_ACTIONS)


def _location_summary(loc):
    """A location as a session needs it: the ids it will pass on, and the two
    fields that decide whether a bundle can be generated at all."""
    return {"harbor_id": loc.get("id"), "name": loc.get("name"),
            "slots": loc.get("slots"), "func_ids": loc.get("funcIds"),
            "ships": [{"ship_id": s.get("id"), "name": s.get("name"),
                       "state": s.get("state")}
                      for s in loc.get("ships", [])]}


# -- opl_facts -----------------------------------------------------------------

FACTS_ACTIONS = ("gather", "manual")


def _facts(action, args):
    if action == "gather":
        harbor_id, = _need(args, "harbor_id")
        facts = core.gather_facts(_client(args), harbor_id)
        return {"facts": facts, "warnings": _facts_warnings(facts),
                "next": _after_facts(facts)}

    if action == "manual":
        harbor_id, ship_id = _need(args, "harbor_id", "ship_id")
        got = core.manual_facts(harbor_id, ship_id,
                                func_ids=args.get("func_ids") or ["performance"])
        facts = got["facts"]
        return {"facts": facts, "warnings": _facts_warnings(facts),
                "next": _after_facts(facts)}

    raise _unknown(action, FACTS_ACTIONS)


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


def _bundle(action, args):
    if action == "options":
        # Both halves together: the default is what you get, the summary is
        # what it means, and a session picking options needs them side by side.
        docs = core.option_docs()
        return {name: dict(docs[name], default=default)
                for name, default in core.option_defaults().items()}

    if action == "generate":
        facts, out_dir = _need(args, "facts", "out_dir")
        options = args.get("options") or {}
        files = core.generate_bundle(
            facts, options,
            # A client only when a token is actually wanted: with fetch_token
            # false, or a token already in the options, there is nothing to ask
            # an account and no reason to require one.
            client=(_client(args)
                    if args.get("fetch_token", True)
                    and core.token_ship_id(facts, options) else None),
            fetch_token=args.get("fetch_token", True))
        written = core.write_bundle(files, out_dir)
        return {"out_dir": out_dir, "files": written,
                "profile": json.loads(files[gen_mod.PROFILE_FILE]),
                "warnings": _facts_warnings(facts) + _bundle_warnings(options),
                "next": _after_generate(out_dir, options)}

    if action == "read":
        out_dir, name = _need(args, "out_dir", "name")
        return {"out_dir": out_dir, "name": name,
                "content": core.read_bundle_file(out_dir, name)}

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
            f"kubectl apply -f {out_dir}/ -n {ns}   (YOU run this -- this "
            f"server never writes to a cluster)",
            "opl_agent status, to see whether the agent reported in"]


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


def _preflight(action, args):
    if action == "doctor":
        facts, = _need(args, "facts")
        options = dict(args.get("options") or {})
        if args.get("namespace"):
            options["namespace"] = args["namespace"]
        evidence = args.get("evidence")
        if evidence is None:
            raise core.BadRequest(
                "doctor needs `evidence`: the JSON a customer collects with "
                "`bzm-opl-gen doctor --collect` (see the preflight doc). This "
                "server never runs kubectl, so without a file there is no "
                "cluster to check against -- and a preflight of no cluster "
                "would report nothing wrong with one you have not seen.")
        return core.preflight(facts, options, evidence)

    if action == "suggest":
        evidence, = _need(args, "evidence")
        return core.suggestions_from_evidence(evidence, args.get("options"))

    if action == "toolcheck":
        return core.toolcheck(cluster=args.get("cluster"),
                              local_registry=args.get("local_registry"),
                              local_proxy=bool(args.get("local_proxy")))

    raise _unknown(action, PREFLIGHT_ACTIONS)


# -- opl_agent -----------------------------------------------------------------

AGENT_ACTIONS = ("status", "livetest")


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
    """
    try:
        return json.dumps(fn(action, _args(args)), indent=2, default=str)
    except core.CoreError as e:
        raise ValueError(str(e)) from None


def build():
    """A fresh server. Built per call rather than at import so that tests get a
    clean one and so nothing is captured from the environment at import time --
    both gates are read when an action runs."""
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations

    srv = MCPServer(name=SERVER_NAME, version=_version(),
                    instructions=INSTRUCTIONS)

    @srv.tool(
        name="opl_location",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                    idempotent_hint=False, open_world_hint=True),
        description=(
            "BlazeMeter private locations (harbors) and their agents (ships).\n"
            "  list         -- locations in an account or workspace, with their "
            "ships. Defaults to this key's own account.\n"
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
            "confirm with the person before calling them."))
    def opl_location(action: Literal[LOCATION_ACTIONS],  # type: ignore[valid-type]
                     args: dict[str, Any] | None = None) -> str:
        return _answer(_location, action, args)

    @srv.tool(
        name="opl_facts",
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=True),
        description=(
            "The account facts a bundle is generated from: image references, "
            "ids, and what the location is enabled for.\n"
            "  gather -- read them from the account {harbor_id}\n"
            "  manual -- build the same structure from ids read off the "
            "BlazeMeter UI {harbor_id, ship_id, func_ids?}, for a customer "
            "whose account you cannot reach\n"
            "Pass the `facts` object straight to opl_bundle and opl_preflight."))
    def opl_facts(action: Literal[FACTS_ACTIONS],  # type: ignore[valid-type]
                  args: dict[str, Any] | None = None) -> str:
        return _answer(_facts, action, args)

    @srv.tool(
        name="opl_bundle",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True,
                                    idempotent_hint=False, open_world_hint=False),
        description=(
            "The manifests, written to a directory you name.\n"
            "  generate -- {facts, out_dir (ABSOLUTE), options?, fetch_token?}. "
            "Writes the bundle and answers with file names and sizes, never the "
            "YAML and never the AUTH_TOKEN. Read a file back with `read`.\n"
            "  read     -- one file out of a written bundle {out_dir, name}\n"
            "  options  -- every generate option, its default and what it does\n"
            "  images   -- the image references this bundle pulls {facts, "
            "all?}. With mirror=<prefix> it also pulls them and pushes them "
            "into that registry, which writes to it -- confirm before "
            "calling it that way.\n"
            "Applying the bundle is yours: `kubectl apply -f <out_dir>`. This "
            "server never writes to a cluster."))
    def opl_bundle(action: Literal[BUNDLE_ACTIONS],  # type: ignore[valid-type]
                   args: dict[str, Any] | None = None) -> str:
        return _answer(_bundle, action, args)

    @srv.tool(
        name="opl_preflight",
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
        description=(
            "Will this land? Checks that run before anything is applied.\n"
            "  doctor    -- the cluster against this configuration {facts, "
            "evidence, options?, namespace?}. `evidence` is the JSON file the "
            "customer collects; this server never runs kubectl itself.\n"
            "  suggest   -- what that same evidence implies the options should "
            "be {evidence, options?}\n"
            "  toolcheck -- this machine's own tools, for the live rig "
            "{cluster?, local_registry?, local_proxy?}\n"
            "A denied read is a WARN, not a FAIL: a cluster that refused a "
            "probe is not a cluster that failed one, and `ok` already knows the "
            "difference."))
    def opl_preflight(action: Literal[PREFLIGHT_ACTIONS],  # type: ignore[valid-type]
                      args: dict[str, Any] | None = None) -> str:
        return _answer(_preflight, action, args)

    @srv.tool(
        name="opl_agent",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                    idempotent_hint=False, open_world_hint=True),
        description=(
            "The deployed agent.\n"
            "  status   -- is it reporting? {harbor_id, ship_id}. State alone "
            "reads as healthy forever, so this is really about the heartbeat.\n"
            "  livetest -- deploy a bundle to a cluster and wait for the agent "
            "{manifests, namespace, harbor_id, ship_id, cluster?, timeout?}. "
            "Off unless " + ENABLE_LIVETEST_ENV + "=1, blocks for minutes, and "
            "the full rig is the `bzm-opl-gen livetest` command.\n"
            "An online agent is not proof a test runs: for that, run one "
            "through the blazemeter_execution MCP server if this session has "
            "it."))
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

    read.__name__ = "doc_" + name.replace(".", "_").replace("-", "_")
    srv.resource(f"{RESOURCE_SCHEME}://docs/{name}", name=name,
                 mime_type="text/markdown",
                 description=DOC_SUMMARIES.get(name, f"Reference: {name}"))(read)


def _version():
    try:
        from importlib.metadata import version
        return version("bzm-opl-gen")
    except Exception:
        # An unimportable version is not a reason to refuse to start; the
        # number is a label on the handshake and nothing depends on it.
        return "0"


def main():
    """Serve on stdio.

    Nothing may print to stdout here -- it is the JSON-RPC channel, and one
    stray line makes the session unparseable to the client. Everything this
    package prints for a human goes through the CLI, which is a different
    entry point.
    """
    import anyio
    anyio.run(build().run_stdio_async)
