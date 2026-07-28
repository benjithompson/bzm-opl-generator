# Preflight: `doctor`, `suggest` and `toolcheck`

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

## What the cluster implies about the options (`suggest`)

`doctor` asks whether a deployment would survive a cluster. The same evidence
answers the question that comes first — how the bundle should have been
configured — and `suggest` writes that reasoning down instead of leaving it in
whoever read the file:

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
  among the rest — narrowing to a single survivor is still not choosing it.

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

- **The command applies nothing.** The suggestions are printed (or emitted as
  JSON); passing them to `generate` stays a decision somebody makes. The web UI
  can apply one, and only ever one you click — see
  [the web UI](web-ui.md#applying-what-the-cluster-implies).
- **Nothing is suggested from evidence the collector could not read.** A `null`
  section is skipped as it is everywhere else, but the boolean maps need more
  than that: `auth can-i` and `api-resources` both report failure as *no*, so a
  machine with no kubeconfig produces a file that reads as a plain-Kubernetes
  cluster where nothing may be created. `versions.serverVersion` is present only
  when a server actually answered, and without it `suggest` returns nothing and
  says why. (`doctor` still reads such a file usefully — a warning about what
  could not be seen is worth having; a configuration guessed from it is not.)

Cluster-scoped permissions say nothing about `service_type`, and `suggest` will
not claim otherwise. Crane resolves its advertised address from its own network
interfaces rather than from the Node object, and NODEPORT has run green against
a cluster where the agent had namespaced RBAC only.


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
