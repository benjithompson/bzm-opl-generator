# Service virtualization

A location whose funcIds include `mockServices` needs an ingress before any
virtual service will work. The generator refuses to render without one, because
the failure is otherwise invisible: the manifests apply cleanly, the agent goes
`idle`, the mock pod runs `1/1` — and every deploy hangs at
`WAITING_FOR_DOMAIN` forever with no error, because crane has no domain to hand
the service.

```
bzm-opl-gen generate --facts facts.json --auth-token <AUTH_TOKEN> \
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

## Not using it on a location that offers it

Accounts routinely have locations carrying `mockServices` alongside
`performance` because somebody enabled both when the location was created, and
then run nothing but tests on them. `--sv-ingress none` is that, said out loud:

```
bzm-opl-gen generate --facts facts.json --auth-token <AUTH_TOKEN> \
    --namespace blazemeter --sv-ingress none
```

The bundle is then the performance one — no ingress, no SV RBAC, no TLS secret,
and no `KUBERNETES_WEB_EXPOSE_*` in the ConfigMap — and `--format helm` is
available again, since there is nothing left for the chart to be missing. What
you give up is what the refusal was protecting: deploy a virtual service to
this location and it will stall at `WAITING_FOR_DOMAIN`, exactly as described
above. Nothing else changes, including the images — which image set the agent
runs is a fact about the location, so the mock image is still in
`IMAGE_OVERRIDES`.

Unset is *not* this. An `sv_ingress` nobody has answered is still refused for
such a location: the whole value of the refusal is that it arrives before an
afternoon has gone into a healthy-looking mock pod that never serves, and it
would be worth nothing if the way past it were to say nothing. In the web UI
the switch on the **Service virtualization** group is the same decision — it
now turns off on such a location, and the row says what was given up rather
than going quiet.

## Which one to pick

**Prefer anything but `nginx`** — on the default `service_type: CLUSTERIP`,
which is what this section assumes throughout. Crane ships a separate expose
implementation per type, and only the `nginx` one writes a port reference that
is wrong by the Ingress spec. It happens to work on `ingress-nginx`, which
forgives it — but it is working on tolerance no API guarantees, and it fails
outright on a controller that follows the spec. On OpenShift, use `openshift`.

`NODEPORT` inverts this, which is why it has [its own
section](#service_type-and-the-backend-you-chose): it makes the `nginx`
reference correct and stops `contour` and `istio` working at all. The table
below is the CLUSTERIP picture.

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
against crane, is [crane-nginx-ingress-port.md](crane-nginx-ingress-port.md).

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
of them, and on `nginx` under `NODEPORT` (see below). Nothing a performance
location does depends on that lookup — it is capacity awareness (see
`bzm_opl_gen/templates/clusterrole.yaml`).

One value crane accepts is **not** offered here: `INGRESS`, which BlazeMeter's
env-var reference documents, creates no object at all and stalls at
`WAITING_FOR_DOMAIN`.

## `service_type` and the backend you chose

`NODEPORT` alongside `sv_ingress` was refused outright, on the reasoning that
NODEPORT forces a cluster-scoped Node read a namespaced Role cannot grant. That
reasoning is wrong — and the refusal was still half right, for a different
reason nobody had looked for. All four backends were deployed live on
2026-07-28 to settle it, crane 3.7.55 and service-mock 6.0.29.6 throughout, RBAC
a namespaced Role and RoleBinding with no ClusterRoleBinding naming the account.

| backend | port crane writes | on `NODEPORT` |
|---|---|---|
| `nginx` | `port.number: 8080` — a constant | **works** |
| `openshift` | `port.targetPort: 8080` — a constant | **works** |
| `contour` | the Service's **nodePort** (`30598`) | **fails** |
| `istio` | Gateway `port.number:` the **nodePort** (`32430`) | **fails** |

The generator refuses the two that fail, and `--service-type NODEPORT` is
accepted with `nginx` and `openshift`.

One istio configuration is refused without having been measured, on purpose.
With `--sv-istio-gateway` set crane reuses a Gateway you already own instead of
creating one, and the Gateway is the object that carried the bad port — the
VirtualService names no port at all. That combination may work. It is refused
with the rest because "istio does not do NODEPORT" is a rule you can predict
from the backend alone, and `CLUSTERIP` costs you nothing: it is the default,
it is the more widely permitted of the two under cluster policy, and it changes
nothing else about an istio deployment. [#63](https://github.com/benjithompson/bzm-opl-generator/issues/63)
settles it if anyone needs the narrower rule.

**The two that work do so because crane writes a constant.** `8080` is the
mock's container port. An Ingress backend resolves against the Service's
`port`, which `NODEPORT` moves from `80` to `8080` — so crane's reference, which
is *wrong* under `CLUSTERIP` and tolerated only by lenient controllers (see
[Which one to pick](#which-one-to-pick)), becomes exactly right. A Route
resolves against `targetPort`, which is `8080` either way. Neither was designed
for this; both survive it.

Measured: `nginx` on minikube (kicbase v0.0.46, k8s 1.32, ingress-nginx
v1.11.3), `openshift` on OpenShift 4.22.1. Deployment `FINISHED`, virtual
service `RUNNING`, BlazeMeter published `http://<vs>-8080-<ns>.<subdomain>` —
no `WAITING_FOR_DOMAIN` — and all three transactions answered there:
`GET /health` → `200`, `GET /api/v1/orders/1001` → `200`,
`POST /api/v1/orders` → `201`; unmatched path `404`, unknown host `404`
(ingress-nginx) / `503` (the router). The nginx case was reproduced across a
stop and a second deploy.

**The two that fail derive the port from the Service and take its nodePort**,
which is not a port anything reaches the ingress on. Both fail *silently* in the
way this page keeps warning about — object written, mock `1/1`, endpoint
advertised, nothing serving:

- **contour** wrote `services: [{name: crane-b3696-…, port: 30598}]`. Contour
  rejected it — `unresolved service reference: port "30598" on service … not
  matched`, HTTPProxy `invalid` — and the endpoint returned **503** while the
  mock answered `200` on its nodePort directly. Confirmed without crane by
  [`docs/repro/contour-nodeport-port.yaml`](repro/contour-nodeport-port.yaml):
  the same `port: 80` reference is valid against a `CLUSTERIP` Service and
  invalid against a `NODEPORT` one.
- **istio** wrote a Gateway server on `port.number: 32430`. Istio accepted it,
  and the ingress gateway's envoy came up listening on `15021`, `15090` and
  `32430` — **nothing on 80 or 443**, which is where the published host resolves.
  Both ports refused the connection outright.

This is why the refusal is per backend rather than per service type, and why it
could not have been settled by reasoning about node reads: crane's node read is
denied under `NODEPORT` on all four, including both that work.

Crane's node read **is** denied, exactly as the old rationale said:
`_get_final_ip` logs `nodes "<node>" is forbidden ... at the cluster scope` and
then `Setting default ip 127.0.0.1`. What the rationale got wrong is the
consequence. That address belongs to crane's *Service pool* — it pre-creates
NodePort Services and binds one to a mock by setting its selector at deploy time
— and the web-expose path never consults it. The endpoint comes from
`KUBERNETES_WEB_EXPOSE_SUB_DOMAIN`, which needs no cluster-scoped read at all.

Worth knowing when that warning is expected, because it is easy to read as a
symptom: it is the *pool* that reads Node, so it appears only once a virtual
service exists. The same agent on the same `NODEPORT` config logged it **0**
times across ten hours idle, and once per status update from the moment the mock
deployed. A performance location that never deploys a mock never reaches this
code at all — which is why #49 could report a clean log for `NODEPORT` and this
run a denied read, with neither contradicting the other.

If you switch an existing agent between the two service types, note that crane
does **not** retype the pool Services it already created — it keeps them and
adds new ones of the current type, binding whichever it picks. Observed on the
OpenShift agent above: after switching back to `CLUSTERIP` the virtual service
served correctly from a Service still typed `NodePort`. Harmless, but it means
`kubectl get svc` is not a reliable reading of the configured service type, and
the stale members hold node ports until something removes them. They are
crane-managed: deleting one by hand while the pool holds it desynchronises the
agent, and the virtual service then deploys a pod with no Service and no
endpoint. Stop the virtual service and let crane rebuild instead.

None of this is a reason to switch. `NODEPORT` does make crane's `nginx`
reference correct by spec, but it fixes that on one backend, costs a node port
per virtual service, and the three others either never had the problem or are
refused. `CLUSTERIP` remains the default and the smaller ask of a cluster.

What is still untested is `NODEPORT` on an SV location with **no** ingress —
where the pool's address is all there is to publish, so the `127.0.0.1` fallback
would plausibly be the endpoint. Nobody has run it: the generator refuses an SV
location without an ingress whatever the service type, and gives
`WAITING_FOR_DOMAIN` as the reason, which is a different claim from this one.

## Reaching a virtual service from outside: `sv-expose`

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

`sv_ingress_class` is read from the bundle's `profile.json`, so a profile
carrying it lets a later `sv-expose --manifests out/` pick the class up without
repeating the flag. It is not a `generate` option — nothing about it reaches the
agent, and a bundle generated without it is byte-identical to before.

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
[Preflight](preflight.md)), which is what crane's own Ingress needs. If you
publish with `sv-expose` and its `--ingress-class`, treat that **FAIL** as
advisory — but note it is a real FAIL with a non-zero exit, because `doctor`
reads only the profile and has no way to know you intend to run `sv-expose`. If
that matters in CI, gate on the other checks or use a non-nginx `sv_ingress`.

`sv-expose` is indifferent to `service_type`: it selects the mock's pod by the
identity labels crane stamps, not through crane's Service, so it works the same
whether that Service is `CLUSTERIP` or `NODEPORT`. Either way the whole
deployment stays inside namespaced RBAC — no ClusterRole required (see
[`service_type` and the backend you chose](#service_type-and-the-backend-you-chose)).
