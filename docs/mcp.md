# The MCP server

`bzm-opl-gen mcp` speaks [MCP](https://modelcontextprotocol.io) over stdio, so
an AI session can do the whole OPL deployment — find the location, read its real
image references, preflight a cluster, write the manifests — without a checkout
of this repo.

That last part is the design constraint. The session this is written for is an
SE sitting in a customer's directory with a cluster and a BlazeMeter account and
nothing else of ours. So the tool descriptions, the `instructions` block the
server returns at startup, and the reference docs it serves as resources are the
*entire* documentation. Anything a session needs to know that is not in one of
those three does not exist as far as it is concerned.

## Install and configure

```
pipx install "bzm-opl-gen[mcp]"
```

`[mcp]` rather than `[ui]`: the extras are independent, and `[ui,mcp]` installs
both. To track `main` or a tag PyPI has not seen, install from git instead —
`pipx install "bzm-opl-gen[mcp] @ git+https://github.com/benjithompson/bzm-opl-generator@v0.3.1"`
— and see the [README](../README.md#install) for the rest of that story,
including the release-wheel route if you would rather install an artifact.

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

If you would rather not have a key file, `BZM_API_KEY_ID` and
`BZM_API_KEY_SECRET` work instead. With neither, the server still starts and
everything that needs no account still works — `opl_bundle options`,
`opl_facts manual`, `opl_preflight` — and anything that does explains which
variable to set.

> There is deliberately no `.mcp.json` checked into this repo and no
> `--install` subcommand. Where the key lives is the user's decision, and a
> committed config either hardcodes a path that is wrong for everyone else or
> teaches people to put a credential in a file they then commit.

## The tools

Each dispatches on an `action`, which is the convention the sibling BlazeMeter
MCP servers already use — a session that has those does not have to learn a
second shape.

| tool | actions |
|---|---|
| `opl_location` | `list` · `show` · `whoami` · `create` · `create_agent` · `reveal_token` · `delete`\* |
| `opl_facts` | `gather` · `manual` |
| `opl_bundle` | `generate` · `read` · `options` · `images` |
| `opl_plan` | `capacity` |
| `opl_preflight` | `doctor` · `suggest` · `toolcheck` |
| `opl_agent` | `status` · `livetest`\* |

\* off unless an environment variable is set — see [The gates](#the-gates).

**One deployment inside a private location is an agent**, and that is the word
every action name, response key and sentence here uses. The exception is
`ship_id` — BlazeMeter's own field name for an agent's id, kept as the account
spells it so a session reading a response from this server and a response from
the account does not have to translate between them. `create_agent` was
`create_ship` until [#156](https://github.com/benjithompson/bzm-opl-generator/issues/156)
and the old spelling is still accepted, exactly as `bzm-opl-gen create-agent`
still answers to `create-ship`: a session reads the action list out of the tool
description at call time, but the prompt somebody saved does not update itself.

**`opl_plan capacity` is the one tool that reaches nothing** — no key, no
account, no cluster. It answers "what would we need to run this?", which is
routinely asked by a customer who cannot start the rest of the path because the
cluster is a ticket nobody has raised. Alongside the numbers it returns
`document`: a ready-to-send infrastructure request written for a platform team
that has never heard of BlazeMeter. Offer that document — it is the deliverable,
not a formatting of the numbers.

**Three sizings, each in its own unit**, and at least one of them: `users` for
virtual users, `browsers` for browser instances, `requests_per_second` for
service virtualization. Where several are given the largest pod count decides
the pool and `driven_by` names which — one agent applies a single CPU/memory
pair to every pod it creates, so these are three routes to a count of pods of
one size rather than three sizes.

Not every unit has a figure behind it, and each row says which it had.
`vus_per_engine` is what a load target multiplies by and the one thing
arithmetic cannot reach, since it depends on what the script does between
requests; unset, what an engine of that size is rated for is assumed and
`vus_per_engine_assumed` comes back `true`. `browsers_per_engine` works the same
way. A request rate has *neither*: how many requests per second one core of a
mock pod serves has never been measured here, nothing is assumed in its place,
and so that target is stated in the request document and sizes nothing — asked
for on its own it is a refusal rather than a plan with an invented number in it.
Pass the qualifier on rather than reporting a node count as measured.

Answer in BlazeMeter's own hierarchy — a location holds agents, an agent runs
engines, each engine drives virtual users — and note that neither the location
nor its agent needs a cluster to exist.
[capacity-planning.md](capacity-planning.md) has the rest.

**Listing locations is deliberately compact.** `opl_location list` gives one
line per location — its id, name, `funcIds`, slots, how many agents it has and
how many of those are reporting — and the first 50 of them. Real accounts hold
hundreds: one with 171 locations and 221 agents listed in full came to 84,779
characters, over a client's result ceiling, so the first step of the path never
completed. Narrow with `name_contains` (a case-insensitive substring of the
name) rather than raising `limit`, and use `show` for the agents of the one you
pick. Whatever the cap or the filter left out comes back as a count with a
sentence saying so — a listing that quietly stopped would read as the whole
account, and "that location does not exist" is a worse answer than a response
that was too big.

Two counts describe the agents, because one cannot carry both facts.
`agents_reporting` counts only the agents the payload vouches for, and
`agents_unknown` those it carried no heartbeat to judge by — so a location with
one live agent and one heartbeat-less record reports `1` and `1` rather than
hiding the live one. A `0` beside a non-zero `agents_unknown` means "none that we
could see", not "none alive", and where *nothing* is vouched for
`agents_reporting` is `null` rather than `0`. Don't redeploy on the strength of a
zero: `opl_agent status` is the authority on a single agent, and `show` gives
the per-agent detail (each with its own `reporting`) for the location you pick.

`opl_preflight doctor` and `suggest` take `evidence` as either the **path** of
the cluster-evidence JSON the customer sent — read here, on the machine running
the server — or as the parsed object, for a caller already holding one. A path
is neither a secret nor bulk, which is why `api_key_file` is one too; inlining a
real collector file means several KB of node lists and permission maps
travelling through the model to reach a check that only needed somewhere to read
them from. The two ways a path can be wrong are separate refusals, because the
remedies differ: one names the file that could not be read, the other names the
`schema` a file that *was* read does not carry.

The reference pages under `docs/` are served as resources at
`bzm-opl://docs/<name>.md`, so a session can read [options.md](options.md) or
[preflight.md](preflight.md) rather than guessing at an option name.

A bundle directory is also how work arrives from elsewhere: `bzm-opl-gen
generate -o <dir>` writes exactly the shape `opl_bundle generate` produces,
profile.json included, so a session pointed at a folder somebody else rendered
reads what was configured (`opl_bundle read` with `name=profile.json`) and
carries on rather than starting over. The filesystem is the shared state, which
is also why `livetest` consumes the same directory.

`options.output_format` takes `docker` as well as `manifests` and `helm`. That
one is not a cluster deployment at all — one agent as one container on a host,
emitted as a `docker run` script — so the two dozen Kubernetes options are
ignored rather than refused, and the `next` steps a generate comes back with are
the script rather than a `kubectl apply`. See [docker.md](docker.md) before
offering it.

## `generate` issues no credential unless you say `rotate_token`

The argument used to be `fetch_token` and used to default to *true*, which meant
generating a bundle — even just to look at one — POSTed for a new AUTH_TOKEN and
invalidated the previous one. The agent already running on it does not report an
error: crane answers a dead token with 404 on `/versions`, logs `Sleeping for
300` and never starts its health service, so the pod sits `0/1 Running` and reads
as a slow boot. That cost a live debugging session before anyone suspected the
credential.

The rename is the safeguard. A model reads JSON, not a terminal — there is no
prompt it can be stopped at and no output it can be warned by — so the argument
name has to be the warning, and the default has to be the branch that touches
nothing. `fetch_token` is now **refused** rather than ignored: a session working
from a cached description means to mint, and silently handing it a placeholder
bundle would be a worse answer than one round trip spent on the new word.

Every `generate` reports which of four ways its token arrived, as
`token_source: {branch, ship_id, message}`:

| branch | what happened |
|---|---|
| `given` | the `auth_token` option was already set — nothing was issued |
| `rotated` | `rotate_token: true` — a **new** token, and the previous one is dead |
| `reused` | `out_dir` already held a bundle for this agent; its token was read back, so the bytes are identical and the running agent is unaffected |
| `placeholder` | no token available — the bundle cannot be applied as it stands, and the message names both places a real one comes from |

A rotation is also in `warnings`, not only in `token_source`. A session scanning
for what went wrong would otherwise miss the one event in a generate that takes a
working agent down — and that is not hypothetical: a live rotation on this surface
answered `warnings: []` and named no agent at all.

The token itself is still never in the response, on any branch. The agent's own
`ship_id` is, because naming the agent whose credential was just replaced is the
point.

`opl_location create_agent` is where a new agent's first token comes from in
practice: create the agent, then `generate` once with `rotate_token: true`, and
every later regenerate into the same directory takes the `reused` branch.

## Three rules the server keeps

**The AUTH_TOKEN is never in a response.** `generate` writes the Secret to disk
and answers with file names and byte counts — not the YAML. A response goes into
a transcript, gets summarised, and is quoted back later; a credential that
*rotates every time it is issued* must not travel that way. `opl_location
reveal_token` is the single exception, and it is a whole action precisely so it
cannot happen as a side effect of something else. It says what it did, because
what it did was invalidate the previous token. `generate` with `rotate_token`
issues one too, and it also says so — but it names the *agent*, and puts the
value in the Secret on disk rather than in the answer.

**A secret is never a tool argument.** A *path* may be (`api_key_file`); the id
and secret come from the environment. Arguments pass through everything between
the caller and the server and get logged by things nobody is thinking about at
the time.

**Nothing writes to a cluster, with one gated exception.** The cluster reads are
reads. `kubectl apply` is the session's own, run in the user's shell — which is
also the only place the person watching sees what is being applied to their
cluster. The same goes for `helm install`.

The exception is `opl_agent livetest`, which deploys because deploying is the
whole of what it does. That is why it has its own variable rather than sharing
`BZM_OPL_ALLOW_DESTRUCTIVE` — enabling image mirroring should not quietly also
enable something that applies manifests.

## The gates

Two capabilities are off by default and refuse with the variable that enables
them. Both are read *when the action runs*, not when the server starts, so
setting one does not mean restarting the client.

| variable | what it allows |
|---|---|
| `BZM_OPL_ALLOW_DESTRUCTIVE=1` | `opl_location delete` — a location and every agent in it |
| `BZM_OPL_ENABLE_LIVETEST=1` | `opl_agent livetest` — deploys to a cluster and blocks for minutes |

Every tool also carries MCP annotations (`readOnlyHint`, `destructiveHint`), so
a client that asks before running something can tell which is which without
parsing the description.

`opl_bundle images` with `mirror=` pushes to a registry and is *not* gated —
it is `destructiveHint: true` and left to the client's confirmation. The
difference from `delete` is what the two do: mirroring adds images to a
registry you named, and the worst case is repositories nobody wanted; deleting
a location destroys an agent with nothing to restore from.

`opl_agent livetest` is the plain deploy-and-wait. The full rig — local
registry, mitmproxy, negative control, a real engine run — is the
`bzm-opl-gen livetest` command, because it needs a shell and 12–20 minutes.
See [live-test.md](live-test.md).

## The other BlazeMeter MCP servers

This server covers the **deployment**: locations, agents, manifests, preflight.
It does not run tests and does not manage virtual services. Two sibling servers
do, and where a session has them they are the right tool:

- `blazemeter_tests` / `blazemeter_execution` — create and run tests, read
  results. This is how you prove a location actually works: an agent reporting
  online is *not* the same as an engine that started and reported back.
- `virtual_services_*` — virtual services on a service-virtualization location,
  once its agent is deployed and its ingress is serving.

The `instructions` block names them and tells the session to say so and stop if
they are not present — explicitly forbidding it from describing what they would
have returned. A plausible test report for a run that never happened is
indistinguishable from a real one, which makes it the failure that gets caught
last.

## Checking it works

```
bzm-opl-gen mcp        # then talk MCP at it; it will look like it has hung
```

That is a JSON-RPC channel on stdin/stdout, so there is nothing to read. Nothing
in this server prints to stdout for that reason — one stray line makes the
session unparseable to the client. To see it answer, use your client's MCP
listing (in Claude Code, `/mcp`), or `tests/test_mcp.py`, which drives the real
server over the SDK's in-memory transport.
