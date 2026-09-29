# Preflight: `doctor`, `suggest` and `toolcheck`

Sizing a cluster you do not have yet is a different command —
[`plan`](capacity-planning.md) turns a load target into engines, nodes and a
machine size, and needs neither an account nor a cluster. What follows assumes
both exist. After the bundle is applied, [`triage`](triage.md) reads what went
wrong in the namespace.

Manifests that apply cleanly say nothing about whether an engine can be
*scheduled*. When it can't, the customer sees a run stuck in "initializing" —
no manifest error, no crane error. `doctor` reads the cluster and the location
together and answers the question the manifests can't:

```
bzm-opl-gen doctor --facts facts.json --manifests out/ -n my-project
# or gather the location's facts live:
bzm-opl-gen doctor --api-key api-key.json --harbor-id <harbor-id> -n my-project
```

It measures against `out/profile.json` — engine size, nodeSelector/tolerations,
registry, proxy/CA — so it checks the deployment you generated.

Every check below is named exactly as `doctor` prints it.

| check | FAIL when | WARN when |
|---|---|---|
| cluster evidence *(evidence files only)* | – | some section of the file could not be read; the verdict says when it was collected, for which namespace, and what the script was refused |
| location slots | `slots` unset (every start 403s "Not enough available resources") | facts entered by hand: not readable without the account, so reported unknown rather than failed |
| location threadsPerEngine | `threadsPerEngine` unset (same 403) | facts entered by hand, as above |
| threadsPerEngine vs engine size | – | more threads than the size supports (500 threads is BlazeMeter's own pairing with 2 CPU / 8Gi) |
| engine heap | `engineXmx` at or above the container limit (OOMKill mid-run, reported as a test that stopped), or a heap too small for `threadsPerEngine` to fill the ramp | a heap far larger than the threads need — every engine pod reserves memory the JVM cannot address; or `engineXmx`/`threadsPerEngine` unknown, so the comparison could not be made |
| crane pool | split pools and no Ready node matches crane's own selector — the agent has nowhere to run | no crane-pool node holds crane's full 1 CPU / 2Gi, so it schedules on its 250m request and throttles when busy |
| engine packing | – | a node would accept more engines than it can run at their limits — engines sharing a node throttle against each other and the run reports the load generator's latency |
| capacity: eligible nodes | no Ready, schedulable node matches the engines' node selector — engines have nowhere to run | none matches *and* a dedicated engine pool is configured: expected between runs (a pool at min-nodes 0 has none until a test asks), but indistinguishable from a pool that was never created |
| capacity: per-node fit | no eligible node holds **one** engine — a pod cannot be split across nodes | – |
| capacity: aggregate | eligible nodes can't hold `slots ×` engine | the nodes could not be read at all; or a dedicated engine pool currently has no nodes, which is expected between runs and indistinguishable from a pool that was never created |
| node disk | – | short of the documented 60GB (40GB `/tmp`) per engine — an engine that fills it is evicted mid-run |
| limitrange | an existing `max` below the engine size (LimitRanger rejects the pod at admission) | existing defaults conflict with the engine size; none exists and none is emitted; or they could not be read |
| resourcequota | `hard − used` can't fit `slots ×` engine, or `pods` can't fit slots + crane | the quotas could not be read |
| quota defaults | – | a cpu/memory ResourceQuota is in force and there is no LimitRange: every pod must then declare requests and limits, and crane sets none on the job pods it spawns |
| admission (PodSecurity) | `pod-security…/enforce=restricted` **with `restrict_engines` off** — crane passes, but the engine pods it spawns keep crane's privileged default and are rejected after the agent is already online, so runs hang rather than fail | no PSA label at all (nothing is enforced, so nothing was proved) |
| admission (SCC) | – | OpenShift namespace with no `sa.scc.uid-range` |
| service account | `service_account_create: false` and no ServiceAccount of that name in the namespace — the Deployment applies and no pod is ever created, the reason being an event on the ReplicaSet | the namespace's ServiceAccounts could not be read, so the name is unverified |
| sv ingress class | `sv_ingress: nginx` with no IngressClass named `nginx` — crane hardcodes that name, so nothing claims the Ingress and the published endpoint 503s while the virtual service is healthy ([details](service-virtualization.md#reaching-a-virtual-service-from-outside-sv-expose)) | the IngressClasses could not be read |
| egress *(one check per target)* | any of `a.blazemeter.com`, `data.blazemeter.com`, `storage.blazemeter.com` — plus the private registry when one is set — unreachable from the namespace | that target could not be probed with the profile's proxy/CA honoured; or nothing was probed at all (an evidence file cannot carry a probe: it takes a pod in the namespace, and a collector must not create one) |

Exit status is non-zero on any FAIL. Egress is probed from the crane pod when
it is deployed — the only place the profile's proxy env and CA bundle are
actually in force — and from a one-shot curl pod otherwise; a probe that cannot
honour a configured CA reports *unknown*, never a FAIL.

Capacity is measured against node **allocatable**, which is an upper bound:
other workloads already hold part of it. A doctor pass means "nothing here
stops a test", not "there is headroom".

## A cluster you cannot reach

The cluster-side twin of `facts --manual`. Have someone with access run the
read-only [collector script](../scripts/bzm-cluster-evidence.sh) — it needs no
cluster-admin, creates nothing, and reads no secret value — and preflight the
file it sends back:

```
# on their machine, pointed at the cluster
./bzm-cluster-evidence.sh -n their-ns > cluster-evidence.json

# on yours, with no kubeconfig at all
bzm-opl-gen doctor --facts facts.json --manifests out/ \
    --cluster-evidence cluster-evidence.json
```

Same checks, same verdicts: the file carries the `kubectl get` documents
`doctor` would have read. The namespace defaults to the one the evidence was
collected for; preflighting a different one is reported, because LimitRanges,
quotas, ServiceAccounts and the PSA labels are all per-namespace.

Three differences, all reported rather than guessed:

- **Egress is unverified.** Probing it takes a pod inside the namespace running
  curl, which a collector script must not create. WARN, never a PASS.
- **With `facts --manual` on the other side, so are the location's numbers.**
  `slots` and `threadsPerEngine` live in the account, so hand-entered facts
  carry neither. Both are reported unknown, naming Settings → Private
  Locations; a location that really has them unset still FAILs.
- **Anything the script could not read stays unknown.** A denied or failed
  `get` is recorded as `null` — distinct from the empty list a successful read
  of nothing returns — and becomes a WARN ("we did not look"), never the FAIL an
  empty list can mean ("we looked, there are none"). A file collected with very
  little access still exits 0 with warnings. The leading `cluster evidence`
  verdict says when it was collected, for which namespace, and what the script
  was refused. A namespace the collector could not read leaves the admission
  posture *unverified*, which is not the same as a namespace that does not
  exist yet.

A file whose `schema` is missing or unrecognised is refused by name — pointing
`--cluster-evidence` at `facts.json` is the likely mistake.

Over the [MCP server](mcp.md), `opl_preflight doctor` and `suggest` take
`evidence` as the **path** of the file the customer sent — read on the machine
running the server — or as the parsed object. A path that is missing or does not
parse, and a document with no recognised `schema`, are separate refusals because
they have separate remedies.

## What the cluster implies about the options (`suggest`)

`doctor` asks whether a deployment would survive a cluster. The same evidence
answers the question that comes first — how the bundle should be configured:

```
bzm-opl-gen suggest --cluster-evidence cluster-evidence.json
bzm-opl-gen suggest --cluster-evidence cluster-evidence.json --json   # as data
```

Every suggestion names the evidence it came from and how strongly it holds:

- **DECISIVE** — the evidence settles it, and you can pass the value straight to
  `generate`. The namespace already holds the ServiceAccount the bundle would
  create, so `service_account_create` is `false`.
- **SUGGESTIVE** — the evidence narrows the choice without making it, so what
  comes back is a shortlist. The cluster serves `projectcontour.io` and not
  `networking.istio.io`, which rules `sv_ingress` values *out* without picking
  among the rest.

| option | read from | strength |
|---|---|---|
| `platform` | `api_groups.openshift_security` — served by OpenShift and nothing else | decisive either way |
| `service_account_create` | a ServiceAccount named `crane` already in the namespace, or `permissions.namespaced` refusing to create one | decisive (`false`) |
| `service_account_name` | the namespace's other ServiceAccounts (never `default`) | suggestive |
| `sv_ingress` | `api_groups` for istio/contour/openshift, and an IngressClass named `nginx` — the name crane hardcodes | suggestive, with what is ruled out and why |
| `sv_subdomain` | `openshift.ingress_config` `spec.domain` | suggestive — that is the *default* router's wildcard |
| `pull_secret` | `inventory.secrets` of type `kubernetes.io/dockerconfigjson` | decisive at exactly one, suggestive above that |
| `ca_existing_configmap` | `inventory.configmaps` named like a trust bundle | suggestive, always — only names are collected, never contents |
| `proxy` | `openshift.proxy_config` (`status` first: it is the effective one) | decisive |
| `ca_openshift_inject` | the cluster proxy's `trustedCA` — egress is TLS-intercepted | suggestive |
| `cluster_rbac` | `permissions.cluster_scoped` refusing ClusterRoles | decisive (`false`) |

Two things it deliberately does not do:

- **It applies nothing.** The suggestions are printed (or emitted as JSON);
  passing them to `generate` is your decision.
- **Nothing is suggested from evidence the collector could not read.** A `null`
  section is skipped. `auth can-i` and `api-resources` report failure as *no*,
  so a file collected with no kubeconfig would read as a plain-Kubernetes
  cluster where nothing may be created; `versions.serverVersion` is present only
  when a server answered, and without it `suggest` returns nothing and says why.
  (`doctor` still reads such a file and warns about what it could not see.)

Cluster-scoped permissions say nothing about `service_type`: NODEPORT works
with namespaced RBAC only.

## Your machine (`toolcheck`)

`toolcheck` asks whether this workstation has what `livetest` shells out to:

```
bzm-opl-gen toolcheck --cluster minikube --local-registry 5001 --local-proxy
```

It checks only what the flags you passed will use — kubectl/oc, the docker
daemon, kind/minikube, the host port `--local-registry` publishes on, free space
on the docker VM, and whether the pinned rig images are cached. On arm64 it
warns that BlazeMeter's amd64-only images run under emulation and that engines
need sizing down. Exits non-zero on anything that would stop the run before it
deploys.

## Engine requests: where they come from, and why no LimitRange

The bundle sets both halves on the crane agent, and crane stamps them on every
engine it creates:

| | variable | default |
|---|---|---|
| limits | `KUBERNETES_RESOURCES_LIMITS_CPU` / `_MEMORY` | `2` / `8Gi` |
| requests | `KUBERNETES_RESOURCES_DEFAULT_CPU` / `_MEM` | equal to the limits (`2` / `8192`, memory in MiB) |

These are the variables BlazeMeter's own Helm chart uses for executor requests
and limits. Requests equal to limits matter because the scheduler and the
cluster autoscaler place pods by requests: with crane's own default of
**250m / 256Mi**, an engine that will use 2 CPU asks for a fraction of one,
several land where two fit, and a run competing for CPU it was never given
reports wrong numbers, not merely slow ones.

**A location's `overrideCPU` / `overrideMemory` replace the requests.** A
location at `overrideCPU: 1` / `overrideMemory: 4096` with a 2 CPU / 8Gi bundle
produces `requests {cpu: 1, memory: 4Gi}` against `limits {cpu: 2, memory:
8Gi}`. Leave them unset, or set them to the engine size. `overrideMemory` is in
MB; a value that looks like GB is probably a typo.

**A LimitRange cannot do it.** `defaultRequest` only fills fields a pod leaves
unset, and crane sets engine requests explicitly. A LimitRange would reach only
crane's per-run job pods, which declare nothing — reserving a full engine's
worth of CPU and memory for jobs that need neither, and taking capacity a real
engine then cannot get. So this generator emits no LimitRange.

`livetest --run-test` prints the live gap under `ENGINE SIZING:` and the JVM
heap against the limit under `ENGINE HEAP:`.

## Two node pools, and the ceiling that makes them work

Giving engines their own nodes is what `engine_node_selector` and
`engine_tolerations` are for. They place the engines *only* — `node_selector`
and `tolerations` keep placing the crane pod — so crane stays on a small
always-on pool while engines land on a tainted pool that scales to zero between
runs. Crane's placement is the Deployment's podspec; the engines' is the
`KUBERNETES_NODE_SELECTOR_JSON` / `KUBERNETES_TOLERATIONS_JSON` env that crane
stamps onto what it spawns. Leaving the engine options unset keeps the one-pool
shape.

Unset and empty differ: unset means "engines go wherever crane goes"; `{}` or
`[]` means "engines take no selector or toleration of their own", for crane
pinned to a tainted infra pool with engines free to land anywhere.

**Requests equal to limits are what make a dedicated pool scale correctly.** A
cluster autoscaler scales on requests just as the scheduler places on them, so
the bundle's requests grow the pool by the number of nodes the run needs. A
location whose `overrideCPU`/`overrideMemory` are set lower brings back the
packing: an empty pool grows by *one* node and the run lands on it.

The backstop is the pool's own `maxPods`,
sized to the pods a node of that pool actually runs plus the engines you intend
it to hold (`engines_per_node`). Measure it with `kubectl get pods -A
--field-selector spec.nodeName=<node>` — **not** with `kubectl get ds -A`, which
counts nodeAffinity-gated variants that never land (32 objects against 4 real
pods on a stock GKE cluster). It is a node pool property on every distribution
and a manifest property on none, which is why a bundle with split pools also
carries a generated `nodepools.md` with the per-provider commands.

**GKE will not take a `maxPods` below 8**, so after ~6 system pods the floor
leaves room for 2 engines a node. On GKE the backstop cannot deliver
one-engine-per-node, and the recipe sizes the node for the engines the floor
permits — another reason to set the location's overrides instead.

`doctor`'s **engine packing** check reads this back: it compares how many
engines a node would *accept* (by requests, capped by `allocatable.pods`)
against how many it can *run* (by limits), and WARNs when the first exceeds the
second. It assumes ~8 DaemonSet pods a node and says so in the verdict, because
nothing here collects DaemonSets — count yours to confirm. WARN, never FAIL:
the engines do start, and what is lost is the validity of the numbers.

`doctor` also reports what the target namespace already has: an existing
LimitRange whose `max`/`min`/`maxLimitRequestRatio` would reject the engine or
the crane pod, defaults that would reach crane's job pods, or no LimitRange at
all.
