# bzm-opl-gen

Generate — and **live-test** — BlazeMeter OPL (On-Premise/Private Location)
Kubernetes & OpenShift deployments for customers, driven by **facts from their
actual BlazeMeter account** instead of hand-edited templates.

```
        BlazeMeter API                customer parameters
   (harbor, ships, funcIds,        (namespace, registry, platform,
    live image inventory)           features, secret policy, ...)
              \                        /
               v                      v
          facts.json  ---->  bzm-opl-gen generate  ---->  out/*.yaml + README
                                                             |
                                              bzm-opl-gen livetest
                                        (apply to kind / current cluster,
                                         poll API until agent is ONLINE)
```

## Why account facts matter

- `funcIds: ["performance"]` → generator knows doduo / charmander browser
  images / mock-service images are dead weight and omits them.
- A running agent reports its **actual pulled images** → exact mirror list +
  correct `IMAGE_OVERRIDES` keys/tags for private registries (e.g. the engine
  is `blazemeter/v4`, locally tagged `taurus-cloud` — easy to get wrong by hand).
- Ship id, crane version, heartbeat — all read, not typed.

## Install

```
brew install pipx && pipx ensurepath        # once, if you don't have pipx
pipx install "bzm-opl-gen[ui] @ git+https://github.com/benjithompson/bzm-opl-generator"
bzm-opl-gen ui                              # opens the web UI
```

`pipx` puts `bzm-opl-gen` on your PATH globally, isolated from everything else.
Upgrade later with `pipx reinstall bzm-opl-gen`. Working on the code instead?
`pipx install -e ".[ui]"` from a checkout tracks your edits live.

### Credentials

Everything that talks to BlazeMeter takes `--api-key path/to/api-key.json` — a
BlazeMeter API key (Settings → API Keys) as JSON:

```
cp examples/api-key.example.json api-key.json   # then fill in id + secret
```

```json
{ "id": "<api key id>", "secret": "<api key secret>" }
```

`api-key*.json` is gitignored. The key needs read access to the account whose
location you're generating for, and write access only for the commands that
create things (`create-location`, `create-ship`, `livetest`).

## Try it without an account

The generator itself only needs a facts file, so a checked-in sample gets you
to real manifests with no BlazeMeter access at all:

```
bzm-opl-gen generate --facts examples/facts.example.json --namespace demo -o out/
```

Edit `examples/facts.example.json` to see the account facts drive the output —
drop `"performance"` from `func_ids`, or add `"functionalGui"`, and watch which
images land in `IMAGE_OVERRIDES`.

## Quick start

```
# 0. find (or create) the location and agent
bzm-opl-gen locations --api-key api-key.json --account-name "SE Demo"
bzm-opl-gen create-ship --api-key api-key.json --harbor-id <HARBOR_ID> \
    --name my-k8s-agent        # prints ship_id + AUTH_TOKEN

# 1. gather facts from the customer's account
bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>

# 2. generate manifests (--api-key fetches AUTH_TOKEN automatically)
bzm-opl-gen generate --namespace my-project --api-key api-key.json -o out/

# private-registry scenario (profiles are scenario presets, not platforms —
# the default posture works on OpenShift and vanilla k8s alike)
bzm-opl-gen generate --profile bzm_opl_gen/profiles/private-registry.json \
    --namespace my-project --private-registry registry.corp.com/bzm -o out/

# 2b. preflight the target cluster against what the location advertises
bzm-opl-gen doctor --facts facts.json --manifests out/ -n my-project
#     (capacity, quota/LimitRange, disk, PSA/SCC, egress — exits non-zero on
#      anything that would stop a test from starting)

# 3. mirror images if using a private registry (pulls linux/amd64)
bzm-opl-gen images --facts facts.json --pull --mirror registry.corp.com/bzm

# 3b. or test the whole private-registry path locally: generate with
#     --private-registry host.minikube.internal:5001 then
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster minikube --local-registry 5001
#     (starts a registry:2 container, mirrors the location's images into it,
#      starts minikube trusting it, deploys, verifies agent online, tears down)

# 3c. …and the proxy + custom-CA path, on top of it. --local-proxy needs no
#     generate flags: it regenerates out/ from out/profile.json with the
#     proxy env + the proxy's own CA once the container is up.
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster minikube --local-registry 5001 --local-proxy --contain-egress
#     (--contain-egress adds calico + a default-deny egress NetworkPolicy, so
#      the proxy is the only way out -- not merely the way that was taken)

# 4. live-test: deploy + verify the agent reports online in BlazeMeter
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster kind          # disposable smoke cluster
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster current       # real cluster via active kubeconfig context
```

Run without installing: `python3 -m bzm_opl_gen ...` from the repo root.
No runtime dependencies for the CLI (stdlib only).

## Working on the code

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests -q          # ~1s, no cluster, must end "N passed"
```

`[dev]` is `[test]` + `[ui]`. Install one of those alone and
`tests/test_server.py` import-skips itself, so the run still says *passed*
while testing none of the HTTP layer — CI asserts the optional deps are
importable for exactly that reason.

The offline suite fakes every cluster and API response the live rig exercises,
so add an offline counterpart whenever you add a live check. Anything beyond
that — the live rig, the account it runs against, and the environment traps
behind each flag — is in [CLAUDE.md](CLAUDE.md).

## Web UI

```
pip install -e .[ui]
bzm-opl-gen ui          # opens http://127.0.0.1:8765
```

Single page: Connect (key stays local) → pick/create location & agent →
configure (presets, private registry, proxy/CA, tolerations/nodeSelector,
engine sizing) → live manifest preview → download zip (AUTH_TOKEN fetched on
download) → watch the agent flip online. Profile JSON import/export round-trips
with `generate --profile`. Frontend dev: `cd frontend && npm install && npm run
dev` (proxies /api to :8765); `npm run build` refreshes the shipped bundle in
`bzm_opl_gen/ui_dist/`.

## Options (flags or `--profile` JSON)

| Option | Default | Meaning |
|---|---|---|
| `platform` | `openshift` | `openshift` = SCC-friendly (no runAsUser, INHERIT_RUNNING_USER_AND_GROUP, cap-drop JSON); `k8s` = pinned runAsUser 1337 |
| `use_secret` | `true` | AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap (simplified) |
| `private_registry` | – | sets DOCKER_REGISTRY, builds IMAGE_OVERRIDES from facts, disables auto-update, rewrites crane image |
| `pull_secret` | – | imagePullSecrets name for the crane image |
| `cluster_rbac` | `false` | include optional read-only nodes ClusterRole/Binding (not required for perf tests) |
| `service_type` | `CLUSTERIP` | NODEPORT is the BlazeMeter default but often disallowed |
| `sv_ingress` | – | `nginx` \| `istio` \| `contour` \| `openshift` — **required** for a `mockServices` location; `openshift` needs `platform: openshift`; see [Service virtualization](#service-virtualization) |
| `sv_subdomain` | – | wildcard domain your ingress controller serves; required with `sv_ingress` |
| `sv_tls_secret` | – | wildcard TLS secret in the agent namespace; required with `sv_ingress`, **even for HTTP** |
| `sv_istio_gateway` | – | istio only, optional; unset means crane creates a Gateway per virtual service. Rejected with any other `sv_ingress`, since only crane's istio backend reads it |
| `proxy` | – | HTTP(S)_PROXY / NO_PROXY; optional `username`/`password` are URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth envs) and the credentialed URLs live in the Secret when `use_secret` is on |
| `engine_cpu_limit` / `engine_mem_limit` | – (documented 2 / 8Gi) | `KUBERNETES_RESOURCES_LIMITS_CPU` / `_MEMORY` — the limits crane stamps on every engine it spawns |
| `emit_limitrange` | `false` | emit `bzm_limitrange.yaml`: a namespace `max` at the engine size plus defaults for pods that declare no resources. It does **not** change the taurus engine — see below |
| `engine_cpu_request` / `engine_mem_request` | – (= the limits) | override that `defaultRequest`; must not exceed the limits |
| `ca_bundle` \| `ca_existing_configmap[:key]` \| `ca_openshift_inject` | – | CA trust, pick one: inline PEM (generator creates the ConfigMap), reference a platform-owned trust-bundle ConfigMap (recommended — they rotate it), or OpenShift's `inject-trusted-cabundle` labeled ConfigMap (cluster injects + rotates). All three mount at `/var/cm` and propagate to engines via `KUBERNETES_CA_BUNDLE_MOUNT` |

## Service virtualization

A location whose funcIds include `mockServices` needs an ingress before any
virtual service will work. The generator refuses to render without one, because
the failure is otherwise invisible: the manifests apply cleanly, the agent goes
`idle`, the mock pod runs `1/1` — and every deploy hangs at
`WAITING_FOR_DOMAIN` forever with no error, because crane has no domain to hand
the service.

```
bzm-opl-gen generate --facts facts.json --api-key api-key.json \
    --namespace my-sv --sv-ingress nginx \
    --sv-subdomain apps.example.com --sv-tls-secret wildcard-credential
```

**Mandatory** — all three together:

| what | why |
|---|---|
| `--sv-ingress nginx\|istio\|contour\|openshift` | one at a time; the controller must already be installed (`openshift` needs no install — the cluster router is already there) |
| `--sv-subdomain` | endpoints become `<service>-<port>-<namespace>.<subdomain>` |
| `--sv-tls-secret` | crane validates it at startup and crash-loops on `TLS secret name is empty` — required even when the virtual service speaks plain HTTP, and even on istio, where nothing ever reads it (see below) |

**Optional:** `--sv-istio-gateway` reuses one Gateway instead of creating one
per virtual service. It is rejected with any other `--sv-ingress`, because only
crane's istio backend reads it — setting it elsewhere would silently do nothing.

**Provided by you, not generated** — the agent namespace needs a wildcard TLS
secret for `*.<subdomain>`, and with `--sv-istio-gateway` that Gateway must
already exist (the generator names it, it does not create it).

### Which one to pick

**Prefer anything but `nginx`.** Crane ships a separate expose implementation
per type, and only the `nginx` one writes a port reference that is wrong by the
Ingress spec. It happens to work on `ingress-nginx`, which forgives it — but it
is working on tolerance no API guarantees, and it fails outright on a controller
that follows the spec. On OpenShift, use `openshift`.

| | `nginx` | `istio` | `contour` | `openshift` |
|---|---|---|---|---|
| crane creates | `networking.k8s.io` Ingress | `networking.istio.io` Gateway + VirtualService | `projectcontour.io` HTTPProxy | `route.openshift.io` Route |
| backend port | `8080` — **spec-wrong**, the Service publishes `80` | omitted; Istio resolves it | `80` — correct | `8080` — correct *for a Route* |
| endpoint serves as-is | **depends on the controller** — see below | **yes** | **yes** | **yes** |
| needs an `IngressClass` | yes, named `nginx` | no — none of these controllers registers one at all | no | no |
| `--sv-tls-secret` | referenced | **never referenced** | referenced; must exist in the agent namespace | not referenced (`edge/Allow`) |
| Role grants | `ingresses` | `gateways`, `virtualservices` | `httpproxies` | `routes`, `routes/custom-host` |
| requires | – | – | – | `--platform openshift` |

**Why nginx's row is a "depends".** Crane's Ingress backend says
`port.number: 8080` while the Service crane created publishes `port: 80`
(`targetPort: 8080`). The Kubernetes API defines `port.number` as the Service's
`spec.ports[].port`, so by spec that reference resolves to nothing — but
`ingress-nginx` matches leniently, accepting `Port`, `TargetPort` *or* `Name`,
and the `targetPort` clause rescues it. Measured on a real controller:

| controller | crane's `8080` | a bogus `9999` (control) |
|---|---|---|
| `ingress-nginx` v1.14.3, k8s 1.32 | **200** — tolerated | 503 |
| OpenShift `ingress-to-route` | **503**, no Route created | 503 |

So on a stock `ingress-nginx` cluster the endpoint works and
[`sv-expose`](#reaching-a-virtual-service-from-outside-sv-expose) is **not**
needed. On a strict controller it 503s while the mock sits healthy at `1/1`.
Controllers other than these two are untested and may go either way, which is
the reason to prefer another backend rather than to rely on the tolerance.

To settle a controller you have not tested — before telling anyone whether they
are affected — `kubectl apply -f docs/repro/nginx-ingress-port.yaml` reproduces
the shapes without BlazeMeter or crane. The full write-up, suitable for filing
against crane, is [docs/crane-nginx-ingress-port.md](docs/crane-nginx-ingress-port.md).

The `openshift` port deserves a note, because it looks like the nginx bug and is
not. A **Route**'s `spec.port.targetPort` resolves against the Service's
*targetPort*; an **Ingress** backend resolves against `spec.ports[].port`. Same
number, opposite meaning — crane is correct in both places by the rules of the
object it is writing, which is what makes the nginx case a real defect rather
than a consistent misunderstanding.

`routes/custom-host` in that Role is not padding. Crane sets `spec.host`, and
OpenShift gates that field behind its own create: with `routes` alone the create
comes back `422 spec.host: Forbidden: you do not have permission to set the host
field of the route`, no Route appears, and the virtual service stalls while the
mock pod sits healthy at `1/1`. Worth knowing that `oc auth can-i create
routes/custom-host` answers **yes** whether or not the grant is present, so it
cannot be used to check this — only a deploy tells the truth.

Only the API group the chosen backend actually writes is granted — crane picks
one implementation and never touches the others, so anything else would be
permission that can only go unused.

The TLS secret is inert on istio because crane writes the `:443` server as
`tls.mode: PASSTHROUGH` with no `credentialName`. Nothing loads a certificate,
so the secret does not need to exist in `istio-system` and does not need to be
valid — but crane still refuses to start without the *name*, so you must pass
it. It also means an **HTTPS** virtual service on istio terminates TLS in the
mock pod itself, not at the gateway. Contour is the opposite: its HTTPProxy
carries `tls.secretName`, and Contour validates it.

All three working paths were verified end to end with namespaced RBAC only and
real transactions returning `200` at the host BlazeMeter advertises: Istio 1.30.3
and Contour v1.33.5 on minikube (k8s 1.32), and Routes on OpenShift Local. The
`nodes ... is forbidden` warning in the crane log is expected and harmless on all
of them; only `NODEPORT` actually depends on that lookup.

One value crane accepts is **not** offered here: `INGRESS`, which BlazeMeter's
env-var reference documents, creates no object at all and stalls at
`WAITING_FOR_DOMAIN`.

### Reaching a virtual service from outside: `sv-expose`

**A fallback, and a narrow one.** Every backend other than `nginx` routes
correctly on its own, and `nginx` itself works on `ingress-nginx` (see [Which one
to pick](#which-one-to-pick)) — so most clusters never need this. What is left
is the case where crane's Ingress is claimed by a controller strict enough to
reject its port reference. On OpenShift, which is the strict controller in
practice, `--sv-ingress openshift` is the better answer and this command is a
last resort.

Where it does apply, the cause is that crane's backend says
`port.number: 8080` while the Service crane created exposes `port: 80`, and the
Ingress spec resolves a backend against `spec.ports[].port`. A strict controller
builds no route from it, so the endpoint BlazeMeter advertises returns **503** —
while the mock is healthy and serving inside the cluster. On OpenShift there is a
second, earlier reason: crane writes `ingressClassName: nginx` with no env to
change it, and the only class shipped is `openshift-default`, so nothing claims
the Ingress at all.

Rather than patch objects crane rewrites on every deploy, emit a parallel pair
that works, once the virtual services are deployed:

```
bzm-opl-gen sv-expose --manifests out/ -n my-sv --ingress-class openshift-default
kubectl apply -n my-sv -f bzm_sv_expose.yaml
```

It reads the deployed mocks off their pods rather than from the API — the
virtual-service API is on a separate host (`mock.blazemeter.com/api/v1`, not
`a.blazemeter.com/api/v4`) and does not report the harbor/ship labels crane
actually stamped. It writes one Service + Ingress per mock:

- `port == targetPort`, so the backend reference resolves and the mismatch
  never arises;
- the Service selects the pod's **identity labels** (`BZM_CONTAINER_NAME`,
  `BZM_HARBOR_ID`, `BZM_SHIP_ID`) rather than crane's Service name, which
  carries a per-deploy hash — so the pair keeps working across redeploys
  without being reapplied;
- the host matches the endpoint BlazeMeter publishes, so the UI link keeps
  working;
- because you own this Ingress, `--ingress-class` names whatever class the
  cluster actually has. On OpenShift that means `openshift-default` and **no
  cluster-admin IngressClass alias is needed** — nothing here is cluster-scoped,
  and no policy engine or admission webhook is involved.

Crane's own Ingress is left alone; it stays unclaimed and creates no competing
route. Re-run `sv-expose` after adding a virtual service; existing pairs are
unaffected.

`doctor` still preflights the `nginx` IngressClass (see
[Preflight](#preflight-doctor)), which is what crane's own Ingress needs. If you
publish with `sv-expose` and its `--ingress-class`, treat that **FAIL** as
advisory — but note it is a real FAIL with a non-zero exit, because `doctor`
reads only the profile and has no way to know you intend to run `sv-expose`. If
that matters in CI, gate on the other checks or use a non-nginx `sv_ingress`.

`service_type` stays `CLUSTERIP` here and the generator rejects `NODEPORT`
alongside `sv_ingress`. NODEPORT makes crane resolve its address from the
cluster-scoped **Node** object, which a namespaced Role cannot grant; denied, it
silently falls back to `127.0.0.1` and stalls. Using an ingress is what keeps
the whole deployment inside namespaced RBAC — no ClusterRole required.

`generate` also writes `out/profile.json` — the fully resolved options, minus
`auth_token` (re-fetched from the API, so the file is safe to commit or hand
over). Replay it with `generate --profile out/profile.json`; `livetest
--local-proxy` reads it to re-render the manifests with the rig's proxy and CA.

> **Re-generating against a live agent:** `--api-key` fetches the AUTH_TOKEN,
> and that endpoint **issues a new token and invalidates the previous one**. If
> an agent is already running for that ship, either re-apply the whole bundle
> (Secret included) or pass `--auth-token <existing>` instead. A crane left with
> a stale token does not report an auth error — it logs `404` on
> `/ships/<id>/status` and sits at `0/1`, which reads like a deleted ship.

Images are selected automatically from the location's enabled funcIds:
performance engines always ship; browser/grid (functionalGui), mock-service
(mockServices), SV bridge (sv-bridge), and recorder (proxyRecorder) images only
when that feature is enabled on the location. `images --all` lists everything.

### Engine requests: the gap, and what a LimitRange can and cannot do

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

`generate --limitrange` is still worth emitting for what it does do:

- `max` is enforced at admission, so nothing in the namespace can be sized past
  the engine. It is raised to cover crane's own limits (1 CPU / 2Gi) when the
  engines are configured smaller — a `max` below them would have the LimitRanger
  reject the crane pod in its own namespace.
- `defaultRequest` / `default` reach every pod in the namespace that declares no
  resources, including crane's per-run job pods, which otherwise schedule as
  best-effort.

It is namespace-wide, so other workloads get those defaults too — give the
private location its own namespace if that matters. To size engines honestly
today, give the location nodes it does not share, or add a mutating admission
policy that rewrites the engine pod's requests.

`doctor` reports what the target namespace already has: an existing LimitRange
whose `max`/`min`/`maxLimitRequestRatio` would reject the engine, defaults that
would collide with ours, or no LimitRange at all.

## Preflight (`doctor`)

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
| capacity: aggregate | eligible nodes can't hold `slots ×` engine | – |
| node disk | – | short of the documented 60GB (40GB `/tmp`) per engine — an engine that fills it is evicted mid-run |
| limitrange | an existing `max` below the engine size (LimitRanger rejects the pod at admission) | existing defaults conflict with the engine size, or none exists and none is emitted |
| resourcequota | `hard − used` can't fit `slots ×` engine, or `pods` can't fit slots + crane | a cpu/memory quota is in force with nothing supplying pod defaults |
| admission | `pod-security…/enforce=restricted` on `platform: k8s` — crane passes, but the engine pods it spawns get the security-context envs only on the openshift path | no PSA label; OpenShift namespace with no `sa.scc.uid-range` |
| sv ingress class | `sv_ingress: nginx` with no IngressClass named `nginx` — crane hardcodes that name, so nothing claims the Ingress and the published endpoint 503s while the virtual service is healthy ([details](#reaching-a-virtual-service-from-outside-sv-expose)) | the IngressClasses could not be read |
| egress | `a.blazemeter.com` (or the private registry) unreachable from the namespace | it could not be probed at all |

Exit status is non-zero on any FAIL. Egress is probed from the crane pod when
it is deployed — the only place the profile's proxy env and CA bundle are
actually in force — and from a one-shot curl pod otherwise; a probe that cannot
honour a configured CA reports *unknown*, never a FAIL.

Capacity is measured against node **allocatable**, which is an upper bound:
other workloads already hold part of it. A doctor pass means "nothing here
stops a test", not "there is headroom".

## Live test

Success = the BlazeMeter API reports the ship with a **fresh heartbeat** and
idle/running state. That exercises the full chain: RBAC, SCC admission, image
pull, egress to `*.blazemeter.com`, and credentials. `--keep` skips teardown;
`--cluster kind` creates/deletes a disposable `bzm-opl-test` cluster (crane
comes online; engines won't fit laptop resources — use `--cluster current`
against a real cluster for full engine validation).

### Reproducing the hard customer environments locally

Two optional rigs turn a laptop into the awkward network a customer has, and
are torn down with the cluster.

| flag | container | what it proves |
|---|---|---|
| `--local-registry [PORT]` (5001) | `registry:2`, published on the host, pulled via `host.minikube.internal` | air-gapped pulls: `DOCKER_REGISTRY`, `IMAGE_OVERRIDES`, auto-update off |
| `--local-proxy` | `mitmproxy`, joined to the cluster's own docker network | proxy egress **and** custom CA trust |
| `--contain-egress` | calico + a default-deny egress NetworkPolicy | that the proxy is the **only** way out, not just the way that was taken |
| `--run-test TEST_ID` | a real BlazeMeter run on the location | what crane passes to the **engines** it spawns: image override, CA propagation, proxy env |

`--local-proxy` is deliberately hostile: mitmproxy terminates TLS with its own
CA, so `*.blazemeter.com` is unreachable unless the generated `REQUESTS_CA_BUNDLE`
/ CA ConfigMap actually lands in the crane process. The rig

1. starts the cluster, then mitmdump (authenticated by default —
   `--proxy-auth user:pass`, or `none` for an open proxy) **on the cluster's
   docker network**, addressed by container IP,
2. reads the mitm CA out of the container and appends it to the public roots,
3. CONNECTs through the proxy from inside the node and requires the attempt to
   appear in the proxy's *own* log before going further,
4. **regenerates** `out/` from `out/profile.json` with `proxy` + inline
   `ca_bundle` merged in (so the manifests under test are generator output, not
   a hand-patched Deployment),
5. deploys, waits for the agent to come online, and then requires
   `blazemeter.com` lines in the proxy log — online *without* them means the
   agent bypassed the proxy, which fails the test.

### What a pass actually proves

"Agent online" is a weak claim on its own — plenty of wrong configurations still
reach it. The run therefore also:

- **blackholes the public registries** on the node (`127.0.0.1 gcr.io`, plus a
  purge of cached copies) whenever `--local-registry` is on, so an image
  `IMAGE_OVERRIDES` forgot to rewrite is an ImagePullBackOff here rather than a
  silent fallback that only breaks in the customer's air-gapped cluster;
- **runs a negative control first** — the same deploy with the CA stripped,
  required to fail with `CERTIFICATE_VERIFY_FAILED` before the real run is
  trusted. A rig that cannot fail proves nothing. Skip with
  `--skip-negative-control` (saves ~2 min);
- **reads the deployed objects back** and checks the generator's promises:
  `AUTH_TOKEN` not in the ConfigMap, proxy credentials not readable there,
  `AUTO_KUBERNETES_UPDATE=false` under a private registry, `IMAGE_OVERRIDES`
  covering every image the location's funcIds need, every running image coming
  from the private registry, and the CA bundle actually present and parseable
  *inside the crane pod* (not merely mounted);
- **reads the proxy log** for what online-ness cannot show: any `407` (the
  embedded credentials were rejected) and any Kubernetes API traffic that
  `NO_PROXY` should have kept out. Lines the negative control produced are
  excluded from both checks.

Any of these failing turns a green run red, with the specific claim printed.

### Egress containment (`--contain-egress`)

Without it, the proxy log proves the agent *used* the proxy — not that it had
to. `--contain-egress` starts minikube with calico and applies a default-deny
egress NetworkPolicy to the namespace, opening only DNS, the Kubernetes API,
and the proxy. Then it probes from inside the crane pod: `a.blazemeter.com`
must be unreachable directly and reachable through the proxy.

```
egress contained: DNS + apiserver (10.96.0.1:443, 192.168.67.2:8443) + proxy 192.168.67.3:8080, everything else denied
  egress probes from the crane pod: direct rc=28, via proxy rc=0
```

Both halves matter: "direct fails" alone is equally consistent with a policy so
tight nothing works, which would pass a containment check while proving
nothing. Two details this needs to be real rather than decorative:

- **minikube's default CNI accepts NetworkPolicies and enforces none**, so the
  policy would be a silent no-op. `--cni` only applies at cluster creation, so
  if the running profile has no policy enforcer the rig recreates it (it is
  disposable by design).
- **The API rule names both the Service ClusterIP and the endpoint behind it**,
  because policy is evaluated after kube-proxy's DNAT — a rule listing only the
  ClusterIP does not match the packet that actually leaves, and crane loses the
  API access it needs to create engine pods.

Probes run `curl` inside the crane pod, not python: `/usr/local/bin/python3`
there is a crane-agent shim, not an interpreter.

### Engine validation (`--run-test TEST_ID`)

Crane coming online says nothing about the pods crane *creates*. `--run-test`
runs an existing BlazeMeter test on the location so an engine actually spawns,
then checks what crane handed it:

```
test 15783207 repointed at harbor-6a63a79dcc45dccca90bf440 (original locations saved for restore)
started test 15783207 -> master 82803809
  engine pod r-v4-6a63ce4e06112601331279-0-0-c-t6cd7 (Running, 10.244.0.9)
  master 82803809: BOOT_STARTING … TAURUS_ENGINE_READY … DATA_RECEIVED … ENDED
  proxy saw engine upload traffic: data.blazemeter.com=64, storage.blazemeter.com=22
restored the original locations on test 15783207
```

Checked: the engine image comes from the private registry (a *different*
`IMAGE_OVERRIDES` key than crane's own, so crane being right proves nothing
about it), the CA bundle propagated via `KUBERNETES_CA_BUNDLE_MOUNT`,
`HTTPS_PROXY` reached the engine env, the engine's own traffic appears in the
proxy log under its pod IP, and the run reached `ENDED` rather than dying.

The test's `executions[].locations` are repointed at `harbor-<id>` and restored
in a `finally`; the original is printed so it can be put back by hand if the
process is killed. Engines are sized down with `--engine-cpu` / `--engine-mem`
(default 1 / 4Gi) — the documented 2 CPU / 8Gi will not schedule on a laptop.
Note crane sets its own resource *requests* (250m / 256Mi) and only the limits
come from the generated envs.

Engines mount the bundle as a file (`/var/cm/ca-bundle.crt`, subPath) where
crane mounts the directory — the check accepts both.

**Use a script that makes real requests.** A dummy-sampler script still reports
hundreds of samples with plausible response times, so the run summary cannot
tell load generation from none — and none of the engine's egress gets exercised.
`api.create_smoke_test()` builds a 1-VU/1-min Taurus test against a real URL for
exactly this. Note its location goes in the uploaded YAML: for a taurus-script
test, `PATCH /tests/{id}` silently drops `executions`, because the script *is*
the load configuration (`point_test_at_location` returns `None` for such tests
rather than pretending the repoint worked).

**Engines do not proxy their sampler traffic.** With real requests the rig shows
engine→BlazeMeter going through the proxy (`data.blazemeter.com`,
`storage.blazemeter.com`) while engine→SUT does not appear there at all: JMeter
ignores `HTTP(S)_PROXY`, which is an env-var convention of HTTP libraries, not
of the JVM. The manifests cannot fix this — a customer whose SUT is only
reachable through the corporate proxy has to put the proxy in the *test*
(taurus `modules.jmeter.properties` with `http.proxyHost`/`http.proxyPort`, or
JMeter's `-H`/`-P`). Worth saying out loud in a customer conversation, because
crane coming online and results uploading look like proof that "the proxy
works".

Why the CONNECT probe, and why the cluster network rather than a published port: a host
port belongs to whatever already claimed it (an ssh tunnel, a stray Java
process), and the node then reaches *that* instead. The symptom is a plausible
lie — the agent gets `403 Forbidden` from a proxy that was never yours, while
your proxy's log stays empty.

Notes: the CA bundle is mitm CA + public roots, because replacing the trust
store outright is not what a corporate bundle does. Image pulls come from the
kubelet, which ignores the pod's proxy env — that's why the registry rig is
reachable directly. mitmproxy is pinned to `11.1.3`; 12+ dies with SIGILL on
Apple-silicon VMs. The CA ConfigMap is applied `--server-side`: a real bundle
overruns the 256KB cap on kubectl's last-applied-configuration annotation.

Not covered by either rig: proof that egress *cannot* leave except through the
proxy (needs `--cni=calico` + a default-deny egress NetworkPolicy), and CA
propagation into engine pods, which only a real test run on the location
exercises.

## Layout

```
bzm_opl_gen/
  api.py         BlazeMeter API client (stdlib)
  facts.py       account fact gathering + image classification
  generate.py    manifest rendering (templates/ + per-option assembly)
  doctor.py      preflight checks (pure verdicts over fetched cluster JSON)
  quantity.py    k8s CPU/memory quantities as numbers (sizing arithmetic)
  livetest.py    deploy, poll-until-online, teardown
  cli.py         subcommands: facts | generate | doctor | images | livetest
  templates/     per-CRD best-practice templates
  profiles/      scenario presets (standard | private-registry | proxy-ca)
tests/           offline unit tests (fixture facts)
examples/        sample facts + api-key placeholder (the no-account path)
```

## Roadmap / not yet covered

- SV expose backends beyond the four implemented — and the behaviour of crane's
  nginx Ingress under controllers other than `ingress-nginx` and
  `ingress-to-route` (Traefik, HAProxy, AWS LB Controller are untested)
- External Secrets Operator / CSI secret-store variants
- Multi-engine runs (the rig validates one engine pod, on one node)
- Engine ephemeral-storage sizing under a real 40GB `/tmp` workload

References: [help.blazemeter.com — private locations](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-kubernetes.html),
[agent env variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html),
[system requirements](https://help.blazemeter.com/docs/guide/private-locations-system-requirements.html),
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane).
