# Service virtualization

**Two publishing shapes, one per platform.** A virtual service is only useful
once something outside the cluster — or outside the host — can reach it, and the
agent arranges that differently on Kubernetes and on Docker. As BlazeMeter put
it: *"Kubernetes agents automatically return DNS-based URLs. As an end-user, you
do not have to set a hostname override for a Kubernetes agent."*

| | Kubernetes (`--format manifests`, `--format helm`) | Docker (`--format docker`) |
|---|---|---|
| how endpoints are published | crane creates an ingress object per virtual service | crane serves them itself, under a name you give it |
| the options | [`--sv-ingress`, `--sv-subdomain`, `--sv-tls-secret`, `--sv-istio-gateway`](#kubernetes-an-ingress-per-virtual-service) | [`--sv-hostname`, `--sv-tls-cert`, `--sv-tls-key`](#docker-a-hostname-and-a-certificate) |
| the variables | `KUBERNETES_WEB_EXPOSE_*` | `HOSTNAME_OVERRIDE`, `TLS_CERT`, `TLS_KEY` |
| what you provide | an ingress controller, a wildcard DNS record, a wildcard TLS Secret | a DNS record for the hostname, and a certificate covering it |

Each set is ignored by the other platform's formats: the web UI shows one of
them, and a profile generated for the other platform keeps its values in
`profile.json` while the bundle's README names what it could not apply.

## Kubernetes: an ingress per virtual service

A location whose funcIds include `mockServices` needs an ingress before any
virtual service will work, and the generator refuses to render without an answer.
Otherwise the failure is invisible: the manifests apply, the agent goes `idle`,
the mock pod runs `1/1` — and every deploy hangs at `WAITING_FOR_DOMAIN` with no
error, because crane has no domain to hand the service.

```
bzm-opl-gen generate --facts facts.json --auth-token <token> \
    --namespace my-sv --sv-ingress nginx \
    --sv-subdomain apps.example.com --sv-tls-secret wildcard-credential
```

**Mandatory** — all three together:

| what | why |
|---|---|
| `--sv-ingress nginx\|istio\|contour\|openshift` | one at a time; the controller must already be installed (`openshift` uses the cluster router) |
| `--sv-subdomain` | endpoints become `<service>-<port>-<namespace>.<subdomain>` |
| `--sv-tls-secret` | crane validates it at startup and crash-loops on `TLS secret name is empty` — required even for plain HTTP, and even on istio, which never reads it (see below) |

**Optional:** `--sv-istio-gateway` reuses one Gateway instead of creating one
per virtual service. It is rejected with any other `--sv-ingress`, because only
crane's istio backend reads it.

**Provided by you, not generated** — a wildcard TLS secret for `*.<subdomain>`
in the **agent's own namespace**, and with `--sv-istio-gateway`, that Gateway.

```
kubectl -n <agent-namespace> create secret tls <name> --cert=<file> --key=<file>
```

Not `default`, which is what [Bring your own certificate][byoc] says — see
[Which namespace the TLS secret goes in](#which-namespace-the-tls-secret-goes-in).

[byoc]: https://help.blazemeter.com/docs/guide/private-locations-optional-installation-step-bring-your-own-certificate-mock-services.html

## Docker: a hostname and a certificate

A docker agent serves the endpoints itself. It needs a name to advertise them
under and, optionally, a certificate to serve them with:

```
bzm-opl-gen generate --facts facts.json --format docker \
    --auth-token <token> --sv-hostname mocks.example.com \
    --sv-tls-cert cert.pem --sv-tls-key key.pk8.pem
```

| what | why |
|---|---|
| `--sv-hostname` | `HOSTNAME_OVERRIDE`. BlazeMeter builds endpoint URLs from *"the combination of hostname and port"*; without it they use this host's IP address, which may be routable from nowhere. It must resolve to the host from wherever the clients are — a DNS record you own. |
| `--sv-tls-cert` | the X509 certificate, PEM. **Optional** — without the pair the endpoints are plain HTTP. |
| `--sv-tls-key` | its private key, PEM with **PKCS#8** syntax. |

The files you name are read and written into the bundle as `sv-tls.crt` and
`sv-tls.key`, mounted at BlazeMeter's paths `/etc/ssl/certs/public.pem` and
`/etc/ssl/certs/privatekey.pem`, which `TLS_CERT` and `TLS_KEY` name. Both stay
overridable at run time: set `SV_TLS_CERT` / `SV_TLS_KEY` (or `CA_BUNDLE`) to a
path the host already keeps, and the script and compose file both use it.

**Two things are checked at generate time**, because both fail silently on a
running agent — it reports online, publishes the endpoint, and every client
rejects it:

- **The key must be PKCS#8.** A PKCS#1 key (`-----BEGIN RSA PRIVATE KEY-----`,
  the usual `openssl genrsa` output) is refused with the conversion:
  `openssl pkcs8 -topk8 -nocrypt -in key.pem -out key.pk8.pem`. So is an
  encrypted key — the agent cannot be given a passphrase.
- **The hostname must match the certificate** — a DNS name in its Subject
  Alternative Name or its Common Name; wildcards cover one label. A mismatch is
  refused, naming what the certificate does carry.

Nothing else is checked: not expiry, the signer, the chain, or whether the key
belongs to the certificate. If the certificate cannot be parsed, the hostname is
**not checked**, and the bundle's README says so.

`--sv-hostname` has no format rules beyond that. Left blank in the web UI it
becomes `<SV_HOSTNAME>`, which the script and compose file both refuse before
starting a container.

**The key is not written to `profile.json`** (the certificate is). So
`generate --profile` on a docker bundle serving HTTPS needs `--auth-token`
**and** `--sv-tls-key`; without the key the bundle writes `<SV_TLS_KEY>` into
`sv-tls.key` and names it at the top of its README.

### What a live run showed

A docker bundle installed with `docker compose up -d`, with a real virtual
service deployed onto it, served `200` and the transaction's body over TLS with
exactly the certificate the bundle supplied, `404` on an unmatched path, and a
verification failure under any other hostname. What that run established:

- **Crane does not pull.** It asks the docker daemon to *create* the container;
  if the image is not already on the host it retries for about ninety seconds
  and the deploy ends `FAILED`, with `Failed to find a deployed container` in
  BlazeMeter and `No such image` only in `docker logs`. **Pre-pull the mock
  images on any host that will serve virtual services.**
- **The image a docker agent runs is `:latest`, not the pinned tag.**
  BlazeMeter's deploy command names `blazemeter/service-mock:latest` even where
  the location pins a version, and `DOCKER_REGISTRY` is prefixed onto that name.
  `<registry>/blazemeter/service-mock:latest` is the tag to pre-pull.
- **`DOCKER_PORT_RANGE` does not apply to virtual services.** The agent reports
  `10000-32000` whatever that variable says; it governs engines only. Do not
  size a firewall rule from it.
- **The mock is a bridge container with a published port**; only crane runs
  with `--net=host`. Open the `10000-32000` range on the host.
- **Crane re-mounts its own bind mounts onto the mock**, which is how the
  mock receives `TLS_CERT`/`TLS_KEY`. That is why the pair must be real files
  on the host.
- **`HOSTNAME_OVERRIDE` stays crane-side**, composing the advertised endpoint
  (hostname plus published port). Without it the endpoint was a docker bridge
  IP.

Not established by that run: whether BlazeMeter itself rejects a PKCS#1 key (the
generator refuses one first), and how the container behaves without `-u 0`.

## The same thing as a chart

`--format helm` publishes virtual services the way `--format manifests` does.
The four options become four values:

```
helm install crane ./helm -n my-sv -f bzm-opl-values.yaml
```

```yaml
sv:
  ingress: nginx          # nginx | istio | contour | openshift
  subdomain: apps.example.com
  tlsSecret: wildcard-credential
  istioGateway: ""        # istio only
```

The chart writes the same `KUBERNETES_WEB_EXPOSE_*`, grants the same API group
in its Role, and refuses the same combinations — a backend that cannot work on
`NODEPORT`, an OpenShift Route on a plain Kubernetes API server, a gateway name
only istio reads — because a chart can also be installed by hand.

On both formats the wildcard DNS record and the TLS Secret in the agent's
namespace are yours to create.

## Not using it on a location that offers it

Locations often carry `mockServices` beside `performance` and only ever run
tests. `--sv-ingress none` says so:

```
bzm-opl-gen generate --facts facts.json --auth-token <token> \
    --namespace blazemeter --sv-ingress none
```

The bundle is then the performance one — no ingress, no SV RBAC, no TLS secret,
no `KUBERNETES_WEB_EXPOSE_*` — in either Kubernetes format. A virtual service
deployed to this location will stall at `WAITING_FOR_DOMAIN`. The images are
unchanged: the mock image is still in `IMAGE_OVERRIDES`, because which images
the agent runs is a fact about the location.

Leaving `sv_ingress` unset is *not* the same: it is refused for such a location,
so the question is answered before anyone spends an afternoon on a mock pod that
never serves. In the web UI, turning off the **Service virtualization** group is
the same decision.

On docker, `--sv-ingress` does not apply. A docker bundle for a `mockServices`
location publishes under `--sv-hostname` if set, and under the host's IP address
if not; neither is refused.

## Which one to pick

**Prefer anything but `nginx`** on the default `service_type: CLUSTERIP`, which
this section assumes. Only crane's `nginx` implementation writes a port
reference that is wrong by the Ingress spec; `ingress-nginx` tolerates it, but a
controller that follows the spec does not. On OpenShift, use `openshift`.
`NODEPORT` changes the picture — see [its own
section](#service_type-and-the-backend-you-chose).

| | `nginx` | `istio` | `contour` | `openshift` |
|---|---|---|---|---|
| crane creates | `networking.k8s.io` Ingress | `networking.istio.io` Gateway + VirtualService | `projectcontour.io` HTTPProxy | `route.openshift.io` Route |
| backend port | `8080` — **spec-wrong**, the Service publishes `80` | omitted; Istio resolves it | `80` — correct | `8080` — correct *for a Route* |
| endpoint serves as-is | **depends on the controller** — see below | **yes** | **yes** | **yes** |
| needs an `IngressClass` | yes, named `nginx` | no | no | no |
| `--sv-tls-secret` | referenced; must exist in the agent namespace | **never referenced** | referenced; must exist in the agent namespace | not referenced (`edge/Allow`) |
| Role grants | `ingresses` | `gateways`, `virtualservices` | `httpproxies` | `routes`, `routes/custom-host` |
| requires | – | – | – | an OpenShift cluster: `--platform openshift --openshift` |

**Why nginx's row is a "depends".** Crane's Ingress backend says
`port.number: 8080` while the Service it created publishes `port: 80`
(`targetPort: 8080`). By spec `port.number` is the Service's
`spec.ports[].port`, so the reference resolves to nothing — but `ingress-nginx`
also accepts a `targetPort` match. Measured:

| controller | crane's `8080` | a bogus `9999` (control) |
|---|---|---|
| `ingress-nginx` v1.14.3, k8s 1.32 | **200** — tolerated | 503 |
| OpenShift `ingress-to-route` | **503**, no Route created | 503 |

So on stock `ingress-nginx` the endpoint works and
[`sv-expose`](#reaching-a-virtual-service-from-outside-sv-expose) is not needed;
on a strict controller it returns 503 while the mock sits healthy at `1/1`.
Other controllers are untested. To check one without BlazeMeter or crane,
`kubectl apply -f docs/repro/nginx-ingress-port.yaml`; the full write-up is
[crane-nginx-ingress-port.md](crane-nginx-ingress-port.md).

The `openshift` port is not the same bug: a Route's `spec.port.targetPort`
resolves against the Service's *targetPort*, where an Ingress backend resolves
against `spec.ports[].port`.

`routes/custom-host` is required: crane sets `spec.host`, and without that grant
the create fails with `422 spec.host: Forbidden`, no Route appears, and the
virtual service stalls. `oc auth can-i create routes/custom-host` answers **yes**
whether or not the grant is present, so only a deploy tells.

Only the API group the chosen backend writes is granted.

The TLS secret is unused on istio because crane writes the `:443` server as
`tls.mode: PASSTHROUGH` with no `credentialName`, so an **HTTPS** virtual
service on istio terminates TLS in the mock pod itself. Crane still refuses to
start without the secret's *name*. Contour is the opposite: its HTTPProxy
carries `tls.secretName`, and Contour validates it.

One value crane accepts is **not** offered: `INGRESS`, in BlazeMeter's variable
reference, creates no object and stalls at `WAITING_FOR_DOMAIN`.

All three non-nginx paths were verified end to end with namespaced RBAC only:
Istio 1.30.3 and Contour v1.33.5 on minikube (k8s 1.32), and Routes on OpenShift
Local. A `nodes ... is forbidden` warning in the crane log is expected and
harmless on all of them (see
`bzm_opl_gen/templates/clusterrole.yaml` for the optional grant).

### Which namespace the TLS secret goes in

The **agent's own**, although BlazeMeter's [Bring your own certificate][byoc]
page says `default`. Crane creates its Ingress in the namespace it runs in, and
an Ingress resolves `tls.secretName` in its own namespace — that is the
Kubernetes API, not a controller's choice. Measured on crane 3.7.56 and
ingress-nginx v1.11.3:

| where `wildcard-credential` was | certificate the endpoint served |
|---|---|
| nowhere | `CN=Kubernetes Ingress Controller Fake Certificate` |
| `default` only — BlazeMeter's step, verbatim | the fake certificate |
| the agent's namespace | **ours**, verified |

with the controller naming where it looked:

```
Error getting SSL certificate "bzm-agent185/wildcard-credential": local SSL
certificate bzm-agent185/wildcard-credential was not found. Using default
certificate
```

**A missing secret does not stop the endpoint serving**: `curl -k` gets `200`,
the deploy reports `FINISHED`, and only a client that verifies TLS finds out.
The bundle's README names the secret and the namespace for that reason.

BlazeMeter's "unless ingress configuration is modified" refers to the
controller-wide `--default-ssl-certificate=<namespace>/<name>` flag, which
serves one certificate for every host whose own secret is missing. It takes any
namespace and is a cluster-wide decision for whoever runs the controller.

`kubectl apply -f docs/repro/sv-tls-secret-namespace.yaml` puts the question to
another controller without BlazeMeter or crane.

## `service_type` and the backend you chose

With `service_type: NODEPORT`, two backends work and two are refused. Measured
on crane 3.7.55 with a namespaced Role only:

| backend | port crane writes | on `NODEPORT` |
|---|---|---|
| `nginx` | `port.number: 8080` — a constant | **works** |
| `openshift` | `port.targetPort: 8080` — a constant | **works** |
| `contour` | the Service's **nodePort** (`30598`) | **fails** — refused |
| `istio` | Gateway `port.number:` the **nodePort** (`32430`) | **fails** — refused |

**The two that work write a constant.** `NODEPORT` moves the Service's `port`
from `80` to `8080`, so nginx's reference becomes exactly right; a Route
resolves against `targetPort`, which is `8080` either way.

**The two that fail take the nodePort**, which nothing reaches the ingress on.
Both fail silently — object written, mock `1/1`, endpoint advertised, nothing
serving. Contour marks the HTTPProxy `invalid` (`unresolved service reference`)
and returns 503 (reproduced without crane by
[`docs/repro/contour-nodeport-port.yaml`](repro/contour-nodeport-port.yaml)).
Istio's gateway listens on the nodePort and nothing on 80 or 443. Istio with
`--sv-istio-gateway` (your own Gateway) is refused as well, untested.

Crane's node read is denied under `NODEPORT` on all four backends, including the
two that work: it logs `nodes "<node>" is forbidden` and `Setting default ip
127.0.0.1`. That address belongs to crane's pool of pre-created Services, which
the ingress path never consults, so it is harmless here. The warning appears only
once a virtual service exists.

Switching an existing agent between service types does not retype the Services
crane already created, so `kubectl get svc` does not reliably show the
configured type. Do not delete them by hand — crane holds them in a pool, and
removing one desynchronises the agent. Stop the virtual service and let crane
rebuild.

`CLUSTERIP` remains the default and the smaller ask of a cluster.

## Reaching a virtual service from outside: `sv-expose`

**A narrow fallback.** Every backend other than `nginx` routes correctly on its
own, and `nginx` works on `ingress-nginx` — so most clusters never need this.
It is for crane's Ingress claimed by a controller strict enough to reject its
port reference. On OpenShift, `--sv-ingress openshift` is the better answer.

Rather than patch objects crane rewrites on every deploy, emit a parallel pair
that works, once the virtual services are deployed:

```
bzm-opl-gen sv-expose --manifests out/ -n my-sv --ingress-class openshift-default
kubectl apply -n my-sv -f bzm_sv_expose.yaml
```

`--ingress-class` is an `sv-expose` flag, not a `generate` option; pass it on
each run. The rest (`sv_subdomain`, `sv_tls_secret`, `namespace`) is read from
`profile.json`.

It reads the deployed mocks off their pods and writes one Service + Ingress per
mock:

- `port == targetPort`, so the backend reference resolves;
- the Service selects the pod's **identity labels** (`BZM_CONTAINER_NAME`,
  `BZM_HARBOR_ID`, `BZM_SHIP_ID`) rather than crane's per-deploy Service name,
  so the pair keeps working across redeploys;
- the host matches the endpoint BlazeMeter publishes, so the UI link works;
- `--ingress-class` names whatever class the cluster has — on OpenShift,
  `openshift-default`, with no cluster-scoped objects needed.

Crane's own Ingress is left alone. Re-run `sv-expose` after adding a virtual
service. It works the same under either `service_type`.

`doctor` still checks for the `nginx` IngressClass that crane's own Ingress
needs, and reports a **FAIL** (non-zero exit) when it is missing, because it
cannot know you intend to use `sv-expose`. In CI, gate on the other checks or
use a non-nginx `sv_ingress`.
