# Service virtualization

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

## Which one to pick

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
of them; only `NODEPORT` actually depends on that lookup.

One value crane accepts is **not** offered here: `INGRESS`, which BlazeMeter's
env-var reference documents, creates no object at all and stalls at
`WAITING_FOR_DOMAIN`.

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

`service_type` stays `CLUSTERIP` here and the generator rejects `NODEPORT`
alongside `sv_ingress`. NODEPORT makes crane resolve its address from the
cluster-scoped **Node** object, which a namespaced Role cannot grant; denied, it
silently falls back to `127.0.0.1` and stalls. Using an ingress is what keeps
the whole deployment inside namespaced RBAC — no ClusterRole required.
