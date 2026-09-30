# The MCP server

`bzm-opl-gen mcp` speaks [MCP](https://modelcontextprotocol.io) over stdio, so
an AI session can do the whole OPL deployment — find the location, read its real
image references, preflight a cluster, write the manifests — without a checkout
of this repo.

The session this is written for sits in a customer's directory with a cluster
and a BlazeMeter account and nothing else of ours. The tool descriptions, the
`instructions` block the server returns at startup, and the reference docs it
serves as resources are the *entire* documentation it has.

## Install and configure

```
pipx install "bzm-opl-gen[mcp]"
```

`[ui,mcp]` installs the web UI as well. To track `main` or a tag PyPI has not
seen, install from git instead —
`pipx install "bzm-opl-gen[mcp] @ git+https://github.com/benjithompson/bzm-opl-generator@v0.4.1"`
— and see the [README](../README.md#install) for the release-wheel route.

Then add it to your client. The API key goes in the server's environment — never
in a tool argument, and never in chat.

**Claude Code** (`.mcp.json` in the project, or `claude mcp add`):

```json
{
  "mcpServers": {
    "bzm-opl": {
      "command": "bzm-opl-gen",
      "args": ["mcp"],
      "env": { "BZM_API_KEY_FILE": "/absolute/path/to/api-key.json" }
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`) takes the same block.

Instead of a key file, `BZM_API_KEY_ID` and `BZM_API_KEY_SECRET` work. With
neither, the server still starts and everything that needs no account still
works — `opl_bundle options`, `opl_facts manual`, `opl_plan`, `opl_preflight` —
and anything that does explains which variable to set.

Where the key lives is your decision, so there is no `.mcp.json` checked into
this repo and no install subcommand that writes one.

## The tools

Each dispatches on an `action`, the same convention the sibling BlazeMeter MCP
servers use.

| tool | actions |
|---|---|
| `opl_location` | `list` · `show` · `whoami` · `create` · `create_agent` · `reveal_token` · `delete`\* |
| `opl_facts` | `gather` · `manual` |
| `opl_bundle` | `generate` · `read` · `options` · `images` · `review` |
| `opl_plan` | `capacity` |
| `opl_preflight` | `doctor` · `suggest` · `toolcheck` |
| `opl_agent` | `status` · `triage` · `livetest`\* |

\* off unless an environment variable is set — see [The gates](#the-gates).

**One deployment inside a private location is an agent**, and every action name,
response key and sentence here uses that word. The exception is `ship_id`,
BlazeMeter's own field name for an agent's id, kept as the account spells it.
`create_ship` is accepted as an alias of `create_agent`, as `bzm-opl-gen
create-ship` is of `create-agent`.

### Sizing: `opl_plan capacity`

The one tool that reaches nothing — no key, no account, no cluster. It answers
"what would we need to run this?", often before the cluster exists. Alongside
the numbers it returns `document`: a ready-to-send infrastructure request for a
platform team that has never heard of BlazeMeter. Offer that document — it is
the deliverable.

**Three sizings, each in its own unit**, at least one required: `users` for
virtual users, `browsers` for browser instances, `requests_per_second` for
service virtualization. Where several are given, the largest pod count decides
and `driven_by` names which — one agent applies a single CPU/memory pair to
every pod it creates, so these are three routes to a count of pods of one size.

Each figure says whether it was supplied, assumed or never measured.
`vus_per_engine` depends on what the script does between requests; unset, what
an engine of that size is rated for is assumed and `vus_per_engine_assumed`
comes back `true`. `browsers_per_engine` works the same way. Requests per second
per mock pod has not been measured, so that target is stated in the document and
sizes nothing; asked for alone it is refused. Pass the qualifier on rather than
reporting a node count as measured.

Answer in BlazeMeter's hierarchy — a location holds agents, an agent runs
engines, each engine drives virtual users. Neither the location nor its agent
needs a cluster to exist. See [capacity-planning.md](capacity-planning.md).

### Listing locations

`opl_location list` gives one compact line per location — its id, name,
`funcIds`, slots, how many agents it has and how many are reporting — and the
first 50 of them, since real accounts hold hundreds. Narrow with
`name_contains` (a case-insensitive substring of the name) rather than raising
`limit`, and use `show` for the agents of the one you pick. Whatever the cap or
the filter left out comes back as a count with a sentence saying so.

Two counts describe the agents: `agents_reporting` counts the agents with a
recent heartbeat, and `agents_unknown` those with no heartbeat to judge by. A
`0` beside a non-zero `agents_unknown` means "none that we could see", not "none
alive", and where nothing could be judged `agents_reporting` is `null`. Don't
redeploy on the strength of a zero: `opl_agent status` is the authority on a
single agent.

### Preflight evidence

`opl_preflight doctor` and `suggest` take `evidence` as the **path** of the
cluster-evidence JSON the customer sent — read on the machine running the
server — or as the parsed object. Prefer the path: a real collector file is
several KB. A file that could not be read and one with no recognised `schema`
are separate refusals, each naming its remedy.

### After deploying

`opl_agent triage` with `{namespace}` reads the namespace's events, pods and
crane log with the kubectl or oc context of the machine that runs the server.
It writes nothing. Each finding names its fix; `unread` lists the reads the
cluster refused, which are not findings; `unrecognised` lists the warnings no
rule knows. [triage.md](triage.md) has the rule table.

### Bundles and docs

The reference pages under `docs/` are served as resources at
`bzm-opl://docs/<name>.md`, so a session can read [options.md](options.md) or
[preflight.md](preflight.md) rather than guessing at an option name.

`bzm-opl-gen generate -o <dir>` writes exactly the shape `opl_bundle generate`
produces, profile.json included, so a session pointed at a folder somebody else
rendered reads what was configured (`opl_bundle read` with `name=profile.json`)
and carries on. `livetest` consumes the same directory.

`options.output_format` takes `docker` as well as `manifests` and `helm`. That
is one agent as one container on a host, emitted as a `docker run` script and an
equivalent `compose.yaml` (use one or the other), so the Kubernetes options are
ignored rather than refused, and the `next` steps are those two rather than a
`kubectl apply`. See [docker.md](docker.md) before offering it.

## `generate` issues no credential unless you say `rotate_token`

Issuing an AUTH_TOKEN invalidates the previous one, and an agent left holding
the old one reports no error: crane answers `404`, logs `Sleeping for 300`, and
the pod sits `0/1 Running` like a slow boot. So `generate` only mints with
`rotate_token: true`, and the default touches nothing. The older argument name
`fetch_token` is refused rather than ignored.

Every `generate` reports how its token arrived, as
`token_source: {branch, ship_id, message}`:

| branch | what happened |
|---|---|
| `given` | the `auth_token` option was already set — nothing was issued |
| `rotated` | `rotate_token: true` — a **new** token, and the previous one is dead |
| `reused` | `out_dir` already held a bundle for this agent; its token was read back, so the bytes are identical and the running agent is unaffected |
| `placeholder` | no token available — the bundle cannot be applied as it stands, and the message names both places a real one comes from |

A rotation also appears in `warnings`, naming the agent, because it is the one
event in a generate that takes a working agent down. The token itself is never
in the response.

In practice: `opl_location create_agent`, then `generate` once with
`rotate_token: true`; every later regenerate into the same directory takes the
`reused` branch.

## Three rules the server keeps

**The AUTH_TOKEN is never in a response.** `generate` writes the Secret to disk
and answers with file names and byte counts, not the YAML. `opl_location
reveal_token` is the single exception, and it is a whole action so it cannot
happen as a side effect; it says that it invalidated the previous token.

**A secret is never a tool argument.** A *path* may be (`api_key_file`); the id
and secret come from the environment, because arguments are logged by
everything between the caller and the server.

**Nothing writes to a cluster, with one gated exception.** `kubectl apply` and
`helm install` are the session's own, run in the user's shell where the person
watching sees them. The exception is `opl_agent livetest`, which deploys because
that is all it does, and has its own variable so enabling image mirroring does
not also enable deploying.

## The gates

Two capabilities are off by default and refuse with the variable that enables
them. Both are read *when the action runs*, so setting one needs no client
restart.

| variable | what it allows |
|---|---|
| `BZM_OPL_ALLOW_DESTRUCTIVE=1` | `opl_location delete` — a location and every agent in it |
| `BZM_OPL_ENABLE_LIVETEST=1` | `opl_agent livetest` — deploys to a cluster and blocks for minutes |

Every tool also carries MCP annotations (`readOnlyHint`, `destructiveHint`), so
a client can tell which is which without parsing the description.

`opl_bundle images` with `mirror=` pushes to a registry and is *not* gated — it
is `destructiveHint: true` and left to the client's confirmation. Mirroring adds
images to a registry you named; deleting a location destroys agents with nothing
to restore from.

`opl_agent livetest` is the plain deploy-and-wait. The full rig — local
registry, mitmproxy, negative control, a real engine run — is the `bzm-opl-gen
livetest` command, because it needs a shell and 12–20 minutes. See
[live-test.md](live-test.md).

## The other BlazeMeter MCP servers

This server covers the **deployment**: locations, agents, manifests, preflight.
It does not run tests or manage virtual services. Two sibling servers do, and
where a session has them they are the right tool:

- `blazemeter_tests` / `blazemeter_execution` — create and run tests, read
  results. This is how you prove a location works: an agent reporting online is
  *not* the same as an engine that started and reported back.
- `virtual_services_*` — virtual services on a service-virtualization location,
  once its agent is deployed and its ingress is serving.

The `instructions` block names them and tells the session to say so and stop if
they are not present, rather than describe what they would have returned.

## Checking it works

```
bzm-opl-gen mcp        # then talk MCP at it; it will look like it has hung
```

That is a JSON-RPC channel on stdin/stdout, so there is nothing to read, and the
server prints nothing else to stdout. To see it answer, use your client's MCP
listing (in Claude Code, `/mcp`).
