# Preflight: `doctor` and `toolcheck`

Manifests that apply cleanly say nothing about whether an engine can be
*scheduled*. When it can't, the customer sees a run stuck in "initializing" —
no manifest error, no crane error. `doctor` reads the cluster and the location
together and answers the question the manifests can't:

```
bzm-opl-gen doctor --facts facts.json --manifests out/ -n my-project
# or gather the location's facts live:
bzm-opl-gen doctor --api-key api-key.json --harbor-id <HARBOR_ID> -n my-project
```

It measures against `out/profile.json` — engine size, nodeSelector/tolerations,
registry, proxy/CA — so it checks the deployment you actually generated.

| check | FAIL when | WARN when |
|---|---|---|
| location | `slots` or `threadsPerEngine` unset (every start 403s "Not enough available resources") | – |
| threadsPerEngine vs engine size | – | more threads than the size supports (500 threads is BlazeMeter's own pairing with 2 CPU / 8Gi) |
| capacity: per-node fit | no eligible node holds **one** engine — a pod cannot be split across nodes | – |
| capacity: aggregate | eligible nodes can't hold `slots ×` engine | the nodes could not be read at all |
| node disk | – | short of the documented 60GB (40GB `/tmp`) per engine — an engine that fills it is evicted mid-run |
| limitrange | an existing `max` below the engine size (LimitRanger rejects the pod at admission) | existing defaults conflict with the engine size; none exists and none is emitted; or they could not be read |
| resourcequota | `hard − used` can't fit `slots ×` engine, or `pods` can't fit slots + crane | a cpu/memory quota is in force with nothing supplying pod defaults, or the quotas could not be read |
| admission | `pod-security…/enforce=restricted` on `platform: k8s` — crane passes, but the engine pods it spawns get the security-context envs only on the openshift path | no PSA label; OpenShift namespace with no `sa.scc.uid-range` |
| service account | `service_account_create: false` and no ServiceAccount of that name in the namespace — the Deployment applies and no pod is ever created, the reason being an event on the ReplicaSet | the namespace's ServiceAccounts could not be read, so the name is unverified |
| sv ingress class | `sv_ingress: nginx` with no IngressClass named `nginx` — crane hardcodes that name, so nothing claims the Ingress and the published endpoint 503s while the virtual service is healthy ([details](service-virtualization.md#reaching-a-virtual-service-from-outside-sv-expose)) | the IngressClasses could not be read |
| egress | `a.blazemeter.com` (or the private registry) unreachable from the namespace | it could not be probed at all |

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
`doctor` would have read, and the importer normalises them into exactly what the
live path produces — nothing downstream knows which way the data arrived. The
namespace defaults to the one the evidence was collected for; preflighting a
different one is reported rather than quietly used, because LimitRanges, quotas,
ServiceAccounts and the PSA labels are all per-namespace.

Two differences, both reported rather than guessed:

- **Egress is unverified.** Probing it takes a pod inside the namespace running
  curl, which is the one thing a collector script must not create. WARN, never
  a PASS.
- **Anything the script could not read stays unknown.** It records a denied or
  failed `get` as `null` — distinct from the empty list a successful read of
  nothing returns — and every such section becomes a WARN ("we did not look"),
  not the FAIL an empty list can mean ("we looked, there are none"). A file
  collected by someone with very little access is still worth reading, and it
  exits 0 with warnings rather than raising a false alarm. The leading
  `cluster evidence` verdict says when it was collected, for which namespace,
  and what the script was refused.

A file whose `schema` is missing or unrecognised is refused by name — pointing
`--cluster-evidence` at `facts.json` is the likely mistake, and half-parsing it
would produce verdicts about a cluster nobody described.

The [web UI](web-ui.md) takes the same file under Download & verify and shows
the same verdicts against the configuration on screen, re-run as you edit it —
no API key and no kubecontext, the same "no access to anything" path manual
facts entry serves.

## Your machine (`toolcheck`)

`doctor` asks whether a cluster can run the location. `toolcheck` asks the
question that comes first when you're driving the live rig: does this
workstation have what `livetest` shells out to?

```
bzm-opl-gen toolcheck --cluster minikube --local-registry 5001 --local-proxy
```

It checks only what the flags you passed will actually use — kubectl/oc, the
docker daemon, kind/minikube, the host port `--local-registry` publishes on,
free space on the docker VM, and whether the pinned rig images are cached.
On arm64 it warns that BlazeMeter's amd64-only images run under emulation and
that engines need sizing down. Exits non-zero on anything that would stop the
run before it deploys.

## Engine requests: the gap, and why no LimitRange

The agent env reference exposes engine **limits** only —
`KUBERNETES_RESOURCES_LIMITS_CPU` / `_MEMORY`. Crane nonetheless sets the engine
pod's **requests**, to a fixed **250m / 256Mi**. The scheduler packs nodes on
requests, so it will put roughly eight engines where two fit — and a run that
competes for CPU it was never given reports numbers that are wrong, not merely
slow.

**A LimitRange cannot fix this, and neither can anything else in these
manifests.** `defaultRequest` only fills in fields a pod leaves unset, and crane
sets them explicitly. Verified on a live run: the engine pod comes back with
`requests 250m/256Mi`, `limits 1/4Gi` and **no `kubernetes.io/limit-ranger`
annotation** at all, while crane's per-run job pods — which declare nothing — do
carry one and get the defaults. `livetest --run-test` prints the live gap under
`ENGINE SIZING:`.

This generator used to emit one anyway, opt-in, for the namespace ceiling it
also gave. **It was removed.** Besides not closing the gap above, the defaults
it *did* apply landed on crane's own per-run job pods — reserving a full
engine's worth of CPU and memory for jobs that need neither, and so taking
capacity a real engine could then not get. It was namespace-wide as well, so it
reached workloads that had nothing to do with the location.

To size engines honestly, give the location nodes it does not share, or add a
mutating admission policy that rewrites the engine pod's requests.

`doctor` still reports what the target namespace already has, which is a
different question: an existing LimitRange whose `max`/`min`/
`maxLimitRequestRatio` would reject the engine or the crane pod, defaults that
would reach crane's job pods, or no LimitRange at all.
