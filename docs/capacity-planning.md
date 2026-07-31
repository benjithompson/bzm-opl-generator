# Capacity planning: `plan`

Everything else in this tool starts from something that exists — a location, an
agent, a cluster. `plan` starts from a number somebody has in a planning
meeting:

```
bzm-opl-gen plan --users 5000
```

```
5,000 virtual users at 500 per engine  (assumed -- what an engine this size is rated for)
  10 engines of 2 CPU / 8Gi / 60GB disk
  10 node(s) of 3 vCPU / 10Gi capacity, at 1 engine(s) each
  peak 30 vCPU / 100Gi across the pool; 0 between runs
  agent: 1 small always-on node (1 CPU / 2Gi)
  location: slots=10 (concurrent engines), threadsPerEngine=500 (virtual users per engine),
            overrideCPU=2, overrideMemory=8192
```

**The vocabulary, because two of those field names fight it.** A private
**location** holds **agents**; an agent runs **engines**; each engine drives some
number of **virtual users**. `slots` is the location's cap on concurrent
engines, and `threadsPerEngine` is virtual users per engine — those are the two
fields' names, not the terms to think in.

**No API key, no facts file, no cluster.** That is the point: the customer who
needs this most has none of them, because the cluster is a ticket they have not
raised yet. `doctor` asks "can this cluster run the location's concurrency?" and
needs both to exist; `plan` asks "what would have to exist?" and needs neither.

## The document

`-o DIR` writes `capacity-request.md` — the same numbers written for a platform
team that has never heard of BlazeMeter:

```
bzm-opl-gen plan --users 5000 -o ./plan
```

It carries what to provision (nodes, machine size, disk, egress hosts, and that
the pool should autoscale from zero), the arithmetic that produced it, the
assumption behind it, and the four BlazeMeter-side settings that decide whether
the cluster gets used at all. `--markdown` prints it instead of writing it.

Written to be *checked*, not just read: a capacity request that cannot be
questioned tends to get halved by whoever holds the budget, and the node count
is usually the half that goes.

## The one thing it cannot know

How many users **one engine** carries is a property of the script, not of the
engine. A chatty API test with no think time exhausts an engine far sooner than
a browsing journey does, and no arithmetic here reaches that.

So `--vus-per-engine` is the input everything multiplies by, and unset it
assumes **what an engine of the chosen size is rated for** — 500 for the
standard 2 CPU / 8Gi engine, scaled linearly on whichever of CPU and memory is
tighter for any other. It follows the engine size for a reason: a flat 500 on
the Small preset assumed load the engine cannot carry and then warned about the
figure the planner itself had picked, and on Large it asked for twice the nodes
needed. Every answer says which of the two it was (`vus_per_engine_assumed` in
the JSON, a line in the summary, a section in the document), because a plan that
quietly turns an assumption into a node count is how an infrastructure request
comes back wrong by a factor of three.

**The honest sequence is: plan, provision small, measure, re-plan.** Run the
real script against one engine, raise the load until that engine saturates, and
re-run `plan` with the number that comes out. That first step needs one node.

## Options

| flag | default | |
|---|---|---|
| `--users` | *required* | virtual users the test has to reach |
| `--vus-per-engine` | what the engine size is rated for | virtual users one engine carries (BlazeMeter's `threadsPerEngine`) — see above |
| `--engine-cpu-limit` / `--engine-mem-limit` | `2` / `8Gi` | the same two flags `generate` takes, so a plan and the bundle it leads to are one vocabulary |
| `--engines-per-node` | 1 | more is cheaper (a node spends ~1 CPU / 2Gi on itself) and they contend — see [`engines_per_node`](options.md) |
| `-o DIR` / `--markdown` / `--json` | – | write the document / print it / the whole plan as data |

The node size is **capacity**, not allocatable: what somebody buys is a machine,
and the kubelet's reservations come out of it before a pod sees any. That is
about 1 CPU and 2Gi on a managed node, and it is why one 2 CPU / 8Gi engine
wants a 3 vCPU / 10Gi machine.

## Carrying a plan forward

The plan's `location` block is what has to be set in BlazeMeter under
**Settings → Private Locations**:

| setting | from the plan |
|---|---|
| Concurrent engines (`slots`) | the engine count — below it the test cannot reach the target |
| Virtual users per engine (`threadsPerEngine`) | the figure the plan used; unset, every start fails 403 *Not enough available resources* |
| overrideCPU / overrideMemory | the engine's **requests**, matched to the limits the bundle sets |

The last two are the difference between the cluster being used and being wasted.
The bundle's `KUBERNETES_RESOURCES_LIMITS_*` set the engine's *limits*; these
two set its *requests*, and the scheduler and the autoscaler place pods on
requests. Left at their `250m`/`256Mi` default, every engine asks for a fraction
of what it uses, the autoscaler adds **one** node, and the whole run packs onto
it — against a pool that was sized, bought and approved for one engine each.
[preflight.md](preflight.md#engine-requests-where-they-come-from-and-why-no-limitrange)
and the `nodepools.md` in a generated bundle have the rest.

**None of this waits for the cluster.** A location and its agent are records in
BlazeMeter, not things running on your cluster: both can be created — with these
settings — while the infrastructure request is still being read, and an agent
that has never sent a heartbeat is the expected state until its manifests are
applied. So the setup either side of the wait can happen during it, and the day
the nodes exist the only step left is `kubectl apply`.

If the location already exists, size it from inside it: selecting it in step 1
of the web UI expands its settings, and **Calculate** there starts from what
that location already says rather than from a blank form
([web-ui.md](web-ui.md#changing-a-location-after-it-exists)). Re-planning after
a measured run ends there — the engine count and virtual users per engine
change, and neither is in a manifest. This page's own view is for the location
that does not exist yet.

In the web UI, **Plan capacity** in the header is the same calculator, and
*Use this plan* fills in the location's slots and threads per engine and the
bundle's engine size. It writes nothing to BlazeMeter: `overrideCPU` and
`overrideMemory` are fields of the account, and this tool does not set them for
you.

## Where else it is

- **Web UI** — the *Plan capacity* view, reachable before connecting anything
  ([web-ui.md](web-ui.md)).
- **MCP** — `opl_plan capacity {users, vus_per_engine?, engine_cpu?,
  engine_mem?, engines_per_node?}`, which returns the numbers and the document
  together ([mcp.md](mcp.md)).

## What it deliberately does not do

- **Estimate throughput, response times or data volume.** Requests per second is
  a property of the script and the system under test; this sizes the load
  generators only.
- **Model the ramp.** The plan is the plateau — engines are held for the whole
  run, so the peak is what has to be schedulable.
- **Account for what else is on the cluster.** The numbers are what the test
  needs; whether the cluster has it spare is `doctor`'s question, once there is
  a cluster to ask about.
- **Say what is being tested.** The request is for capacity to run load tests
  from this cluster. Naming an application invites the reply that it should be
  sized per application, which is a conversation about the test plan rather than
  about nodes.
