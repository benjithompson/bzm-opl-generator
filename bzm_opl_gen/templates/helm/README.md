# bzm-opl — BlazeMeter private location agent (Helm)

A Helm packaging of the manifests `bzm-opl-gen generate` emits, scoped to
**performance testing**. Same objects, same defaults, same reasoning — expressed
as a chart so a platform team can install and upgrade it the way it installs
everything else.

Service virtualization (mock services) is deliberately out of scope: it needs an
ingress backend, its own RBAC and a wildcard TLS secret. Use the upstream
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane) chart for a
location that also serves virtual services.

## Which one should I use?

| | `bzm-opl-gen generate` | this chart |
|---|---|---|
| Where values come from | the account, over the API | `values.yaml` |
| Private-registry `IMAGE_OVERRIDES` | derived from the live agent inventory | you supply the map |
| Crane image tag | pinned to what the account advertises | `latest`, or pin it yourself |
| Upgrades | re-generate, re-apply | `helm upgrade` |
| Live-testable | `bzm-opl-gen livetest` | — |

They render the same objects. Verified: for the same options, the ConfigMap
data, Role rules and container spec come out identical, the
crane image tag being the one thing a chart cannot know.

The practical path is to use both — generate once against the real account to
get the facts and the exact image map, then carry those into `values.yaml`.

## Install

The three account facts are the only required values:

```
helm install crane ./helm -n blazemeter --create-namespace \
  --set harborId=<HARBOR_ID> \
  --set shipId=<SHIP_ID> \
  --set-string authToken=<AUTH_TOKEN>
```

Get the ids with `bzm-opl-gen locations --api-key api-key.json --account-name
"<ACCOUNT>"`, or from the private location's page in the BlazeMeter UI. Generate
the token on the location itself.

Keep the token out of git. Either pass it with `--set-string` at install time, or
create the Secret yourself and name it:

```
kubectl -n blazemeter create secret generic bzm-auth --from-literal=AUTH_TOKEN=<TOKEN>
helm install crane ./helm -n blazemeter \
  --set harborId=<H> --set shipId=<S> --set existingSecret=bzm-auth
```

Then confirm the agent shows **online** in BlazeMeter under Settings → Private
Locations.

## Common configurations

**OpenShift** — `--set platform=openshift`. `runAsUser` is dropped (the
restricted-v2 SCC assigns an in-range UID) and engines are told to inherit
crane's UID:GID so the pods it spawns also pass the SCC.

**Private / air-gapped registry** — set `privateRegistry` *and* `imageOverrides`.
Both, always: crane resolves engine images per key, and a key it cannot find
falls back to the public registry without logging anything, so a partial map
looks like it works until the cluster is genuinely sealed. The chart refuses to
render one without the other. Generate the map rather than writing it:

```
bzm-opl-gen generate --api-key api-key.json --harbor-id <H> \
    --private-registry registry.example.com/blazemeter --out out
# copy IMAGE_OVERRIDES out of out/bzm_configmap.yaml, and run the emitted
# out/bzm-opl-image-mirror.sh to mirror the images first
```

Setting `privateRegistry` also turns `AUTO_KUBERNETES_UPDATE` off — upgrading a
sealed location is re-mirror plus bump tags, not a silent pull.

**Corporate CA** — four modes, pick one:

```
# a ConfigMap the platform team owns and rotates (trust-manager, etc.)
--set caBundle.mode=existing --set caBundle.existingConfigMap=trust-bundle

# your own PEM
--set caBundle.mode=inline --set-file caBundle.pem=/path/to/ca.crt

# OpenShift injects the cluster-wide bundle
--set caBundle.mode=openshiftInject --set platform=openshift
```

**Proxy** — `--set proxy.enabled=true --set-string proxy.http=http://px:3128`.
BlazeMeter has no separate proxy-auth env vars, so credentials go in the URL
(`http://user:pass@host:port`); a URL carrying credentials is routed into the
Secret rather than the ConfigMap automatically.

> JMeter ignores `HTTP(S)_PROXY` for sampler traffic — a library convention, not
> a JVM one. Engine → system-under-test goes direct regardless of what is set
> here; the proxy has to go in the test itself. Results still upload.

## Engine sizing

Engines are what actually consume the cluster; crane itself is a small
orchestrator. Per **concurrent engine**: 2 CPU + 8Gi RAM (the default) plus
~60GB disk, 40GB of it `/tmp`. `bzm-opl-gen doctor` checks a cluster against
that.

`engine.cpuLimit` / `engine.memoryLimit` are what crane stamps as the engine
pod's **limits**. Crane sets that pod's **requests** separately and explicitly,
to 250m/256Mi — roughly an eighth of what the engine is allowed to use. The
scheduler packs nodes on requests, so on a busy node a run competes for CPU it
was never given and the numbers the test reports are wrong rather than merely
slow.

**Nothing in this chart can close that gap**, and there is no
`engine.cpuRequest` to set. A LimitRange's `defaultRequest` only fills fields a
pod leaves unset, and crane sets the engine's requests explicitly — verified on a
live run, where the engine pod comes back with no `limit-ranger` annotation at
all.

This chart used to ship a LimitRange anyway, for the namespace ceiling it also
gave. It was removed. It could not do the thing it was added for, and the
defaults it *did* apply landed on crane's own per-run helper pods — which
declare no resources and so received a full engine's worth of CPU and memory,
reserving capacity that a real engine then could not get.

To size engines honestly: give the location nodes it does not share, or add a
mutating admission policy that rewrites the engine pod's requests.
`bzm-opl-gen livetest --run-test` prints the live gap under `ENGINE SIZING:`.

If the namespace already has a LimitRange of its own, its `max` must clear both
the engine size and crane's own limits (1 CPU / 2Gi) or the respective pod is
rejected at admission. `bzm-opl-gen doctor` checks an existing one for exactly
that.

## Upgrading, and crane's self-update

**Set `autoUpdate: false` if you manage this release with Helm.** Then
`helm upgrade` behaves normally, and a configuration-only change still rolls the
pod (the Deployment carries checksums of the ConfigMap and Secret).

Left on — the default — crane takes ownership of its own Deployment within
seconds of install, as field manager `OpenAPI-Generator`. It rewrites the
container image to the version BlazeMeter currently ships, and `.spec.strategy`
from `Recreate` to `RollingUpdate{maxSurge: 1}`. Helm applies server-side, so the
next `helm upgrade` fails on a field-ownership conflict having already applied
the ConfigMap.

`--force-conflicts` does not rescue it. This chart never declares
`strategy.rollingUpdate`, so crane's copy survives beside the forced
`type: Recreate` and the API server rejects the pair:

```
Deployment.apps "crane" is invalid: spec.strategy.rollingUpdate: Forbidden:
may not be specified when strategy `type` is 'Recreate'
```

With auto-update on, changing anything is `helm uninstall` + `helm install`.

All of this was observed on a live cluster. The cost of turning it off is that
keeping the agent current becomes your job — re-generate, or bump `image.tag` —
and an agent that falls far enough behind loses support.

## Cluster RBAC

Off by default, and it should stay off for performance testing. Crane uses
cluster-scoped node reads for capacity awareness only — denied, it logs a
`forbidden: nodes` warning and proceeds.

`serviceType: NODEPORT` is **not** an exception, though this chart used to
refuse the pairing on the theory that it was. Crane resolves its advertised
address from its own network interfaces rather than from the `Node` object, and
creates the NodePort Service through the namespaced Role, which already grants
`services`. A performance location deployed with `NODEPORT`, namespaced RBAC
only and no ClusterRole in the cluster came online, spawned a real engine, and
ran the test to completion — with nothing forbidden anywhere in the crane log.

Cluster-scoped object names carry the namespace
(`cluster-role-binding-crane-<ns>`) so two locations in two namespaces do not
collide.

## Names

Object names default to the fixed ones `bzm-opl-gen generate` emits — deployment
`crane`, `blazemeter-configmap`, `blazemeter-secret`, `role-crane` — rather than
release-derived ones, so a location can move between the generated manifests and
this chart without renaming anything, and the `-l role=role-crane` selectors in
BlazeMeter's docs keep matching. `fullnameOverride` changes that if you want
release-scoped names.

`harbor_id` and `ship_id` are part of the Deployment's selector, and selectors
are immutable: repointing an install at a different agent needs
`helm uninstall` + `helm install`, not `helm upgrade`.

## What it renders

| Object | When |
|---|---|
| ServiceAccount `serviceAccount.name` (default `crane`) | `serviceAccount.create` (default on) |
| ConfigMap `blazemeter-configmap` | always |
| Secret `blazemeter-secret` | `useSecret` and no `existingSecret` |
| Role/RoleBinding `role-crane` | always |
| Deployment `crane` | always |
| ConfigMap `blazemeter-cacerts` | `caBundle.mode` inline or openshiftInject |
| ClusterRole/Binding | `clusterRbac` |

Whichever name `serviceAccount.name` holds is what the Deployment runs as and
what both binding subjects grant to, created here or not. With
`serviceAccount.create: false` the name is **required** — an empty one would
otherwise resolve to the namespace's `default` account and hand crane's Role to
every other pod in the namespace, so the chart refuses to render instead.

The Deployment carries `checksum/config` and `checksum/secret` annotations, so a
`helm upgrade` that only changes configuration still rolls the pod — crane reads
its environment once, at startup.

## Verify a render before installing

```
helm lint ./helm --set harborId=h --set shipId=s --set-string authToken=t
helm template crane ./helm -n blazemeter \
  --set harborId=h --set shipId=s --set-string authToken=t
```

The chart validates its own values and fails the render with a message naming
the fix. Every rejected combination is one that fails *silently* on a cluster —
manifests apply, pod runs, agent never comes online — so refusing to render is
the only signal that arrives before someone has spent an afternoon on it.
