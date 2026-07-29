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
pipx install 'bzm-opl-gen[mcp]'
```

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
| `opl_location` | `list` · `whoami` · `create` · `create_ship` · `reveal_token` · `delete`\* |
| `opl_facts` | `gather` · `manual` |
| `opl_bundle` | `generate` · `read` · `options` · `images` |
| `opl_preflight` | `doctor` · `suggest` · `toolcheck` |
| `opl_agent` | `status` · `livetest`\* |

\* off unless an environment variable is set — see [The gates](#the-gates).

The reference pages under `docs/` are served as resources at
`bzm-opl://docs/<name>.md`, so a session can read [options.md](options.md) or
[preflight.md](preflight.md) rather than guessing at an option name.

## Three rules the server keeps

**The AUTH_TOKEN is never in a response.** `generate` writes the Secret to disk
and answers with file names and byte counts — not the YAML. A response goes into
a transcript, gets summarised, and is quoted back later; a credential that
*rotates every time it is fetched* must not travel that way. `opl_location
reveal_token` is the single exception, and it is a whole action precisely so it
cannot happen as a side effect of something else. It says what it did, because
what it did was invalidate the previous token.

**A secret is never a tool argument.** A *path* may be (`api_key_file`); the id
and secret come from the environment. Arguments pass through everything between
the caller and the server and get logged by things nobody is thinking about at
the time.

**Nothing writes to a cluster.** The cluster reads are reads. `kubectl apply` is
the session's own, run in the user's shell — which is also the only place the
person watching sees what is being applied to their cluster. The same goes for
`helm install`.

## The gates

Two capabilities are off by default and refuse with the variable that enables
them. Both are read *when the action runs*, not when the server starts, so
setting one does not mean restarting the client.

| variable | what it allows |
|---|---|
| `BZM_OPL_ALLOW_DESTRUCTIVE=1` | `opl_location delete` — a location and every ship in it |
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
