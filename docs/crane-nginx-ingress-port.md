# Crane: the NGINX expose backend writes a spec-noncompliant Ingress port, and breaks on any strict controller

**Component:** crane agent, `kubernetes_nginx_web_expose_service`
**Version:** `gcr.io/verdant-bulwark-278/blazemeter/crane:3.7.55`
(`sha256:875ce4b04f24bfc82ace039b1b5a3b86e8345691f3daabe0f0326cafde753c25`)
**Feature:** Service Virtualization (`mockServices` / `sv-bridge` private location)

**Severity: controller-dependent — please read the next section before triaging.**
It works on `ingress-nginx` and fails hard on OpenShift. If you reproduce on
`ingress-nginx` only, you will see it pass and conclude there is no bug.

---

## Summary

Crane creates a Service and an Ingress per virtual service. The Ingress backend
references **port 8080**; the Service crane created in the same step publishes
**port 80** (with `targetPort: 8080`). The Kubernetes API defines an Ingress
backend's `port.number` as the Service's `spec.ports[].port`, so by spec that
reference resolves to nothing.

It nevertheless works today on `ingress-nginx`, which matches leniently and falls
back to `targetPort`. It fails completely on OpenShift's `ingress-to-route`, which
follows the spec: no Route is created and the endpoint BlazeMeter advertises returns
**503** while the mock pod is `1/1` and serving inside the cluster.

So crane is currently depending on an undocumented tolerance in one controller
implementation. The three other expose backends (`istio`, `contour`, `openshift`)
reference their ports correctly and work everywhere tested.

## Test matrix (all run for this report)

| controller | crane's `number: 8080` | proposed `number: 80` |
|---|---|---|
| `ingress-nginx` v1.14.3 (k8s 1.32) | **200** — tolerated | **200** |
| OpenShift `ingress-to-route` (OpenShift Local) | **503** — no Route created | **200** |

Control, same cluster and rig: a backend of `number: 9999` (matching nothing on the
Service) returned **503** on `ingress-nginx` — so the test genuinely distinguishes a
dangling backend from a working one, and the `8080` pass is real tolerance, not a
test artifact.

**Which clause grants the tolerance:** I re-ran against a Service whose port is named
`"http"` instead of `"8080"`, keeping `targetPort: 8080`. Backend `number: 8080` still
returned **200** — so `ingress-nginx` is matching on **targetPort**, not on the port
name. This is the documented-by-code behaviour in `serviceEndpoints`, which accepts a
match on `servicePort.Port`, `servicePort.TargetPort`, *or* `servicePort.Name`.

The practical reading: **the current manifest is valid only by accident.** Nothing in
the Ingress API promises a targetPort fallback, and the controller that doesn't offer
one already ships as the default on every OpenShift cluster.

---

## What crane creates

Service (abridged, as created by crane):

```yaml
spec:
  ports:
    - name: "8080"        # the *name* is the string 8080
      port: 80            # what an Ingress backend resolves against, per spec
      targetPort: 8080    # what ingress-nginx leniently falls back to
```

Ingress (abridged, as created by crane):

```yaml
spec:
  ingressClassName: nginx
  rules:
    - host: <vs>.<subdomain>
      http:
        paths:
          - backend:
              service:
                name: crane-<hash>-<harborId>
                port:
                  number: 8080     # spec says resolve against `port:` (80)
```

### Likely one-line cause

The Service port is *named* `"8080"`. `ServiceBackendPort` is a union of `name` and
`number`, and `port.name: "8080"` would resolve correctly and portably against this
Service. It looks like a name reference emitted into the `number` field — which would
explain a value that is simultaneously plausible-looking, spec-wrong, and accidentally
functional on the one controller that falls back to targetPort.

## Observed failure on OpenShift

The `openshift.io/ingress-to-route` controller names the problem rather than silently
503-ing:

```
IncompleteIngressToRouteRules: No valid target port for backend service ... at index 0
```

Patching the live Ingress to `port.number: 80` — changing nothing else — makes the
Route appear immediately and the endpoint serve: HTTP 302 → HTTPS, then 200 / 200 / 201
on real transactions and 404 on an unmatched path, verified through the router from a
separate host.

**The patch does not survive.** Verified on a freshly created object with no prior
edits: every virtual-service deploy recreates the Ingress with `8080`. There is no
customer-side fix.

## Why this is a defect and not a convention

Worth pre-empting, because the OpenShift backend writes the number 8080 too and looks
identical at a glance:

- A **Route**'s `spec.port.targetPort` resolves against the Service's **targetPort**.
  `8080` is correct there.
- An **Ingress** backend's `port.number` resolves against the Service's **port**.
  `8080` is wrong here.

Same number, opposite meaning. Crane is right in the Route and wrong in the Ingress by
the rules of the object it is writing in each case — a localised bug, not a consistent
misreading of its own Service.

| | `nginx` | `istio` | `contour` | `openshift` |
|---|---|---|---|---|
| creates | Ingress | Gateway + VirtualService | HTTPProxy | Route |
| backend port | **`8080` — spec-wrong** | omitted; Istio resolves it | `80` — correct | `8080` — correct for a Route |
| serves as deployed | **only on lenient controllers** | yes | yes | yes |

Istio 1.30.3 and Contour v1.33.5 verified end to end on minikube (k8s 1.32) with real
transactions returning `200`; Routes verified on OpenShift Local.

---

## Second defect on the same path: `ingressClassName: nginx` is hardcoded

Crane writes `ingressClassName: nginx` with no environment variable to change it — I
checked the agent environment-variable reference specifically.

On OpenShift the only IngressClass shipped is `openshift-default` (controller
`openshift.io/ingress-to-route`), so nothing claims crane's Ingress and the deploy
fails *before* the port bug is even reachable. The workaround is a cluster-admin
IngressClass named `nginx` aliased to `openshift.io/ingress-to-route` — cluster-scoped
privileges that SV customers frequently do not have, for a deployment that is otherwise
entirely namespaced.

Even on vanilla Kubernetes, "the class must be named exactly `nginx`" is a real
constraint — clusters commonly name theirs `nginx-internal` or `internal`, or run more
than one.

**Ask:** honour an env var (e.g. `KUBERNETES_INGRESS_CLASS`), defaulting to `nginx`.

Note these two defects compound: the customers most likely to hit the port bug are
exactly those on OpenShift, who must first be talked through a cluster-admin alias to
reach it.

## Third, smaller: the documented env-var values are wrong in both directions

The agent environment-variable reference lists `INGRESS | CONTOUR | ISTIO` for
`KUBERNETES_WEB_EXPOSE_TYPE`.

- **`INGRESS` is not a real value.** Set it and crane starts cleanly, creates no object
  at all, and the deploy stalls at `WAITING_FOR_DOMAIN` with no error. The working
  value is `NGINX` (as the SV install page and the Helm chart say).
- **Two working values are missing.** `strings` on the shipped binary yields five
  backends: `kubernetes_{base,contour,istio,nginx,openshift}_web_expose_service`.
  `OPENSHIFT` works and is undocumented — it is the right answer for OpenShift
  customers and sidesteps both defects above entirely.

A value that silently creates nothing deserves an explicit rejection at startup, not
just a docs fix.

### Related trap, in case it shares a code path

`KUBERNETES_SERVICES_BLOCKING_GET=true` appears in BlazeMeter's own SV manifest example
(an *Istio* example). Tested in isolation on the nginx path: with it set, crane stops
creating the Ingress entirely and instead calls `_get_final_ip` — the **NodePort** node
lookup — which hits `nodes "..." is forbidden ... at the cluster scope` under namespaced
RBAC, falls back to `127.0.0.1`, and stalls at `WAITING_FOR_DOMAIN`. Removing it
restored Ingress creation on the next deploy. If that flag is being copied into
nginx-path docs or examples, it shouldn't be.

---

## Reproduction

**Reproduce on OpenShift, or the bug hides.** On `ingress-nginx` the deploy succeeds
and looks correct.

1. Private location with `mockServices` / `sv-bridge` funcIds; agent deployed with
   `KUBERNETES_WEB_EXPOSE_TYPE=NGINX`, `KUBERNETES_SERVICE_TYPE=CLUSTERIP`, a subdomain
   and a TLS secret name.
2. OpenShift, plus the cluster-admin IngressClass alias described above (otherwise you
   stop at the second defect instead).
3. Deploy any virtual service.
4. `oc get ingress -n <ns> -o yaml` → backend `port.number: 8080`;
   `oc get svc -n <ns> -o yaml` → `port: 80`. `oc get events` shows
   `IncompleteIngressToRouteRules`.
5. The endpoint in the BlazeMeter UI → **503**. From inside the cluster, the Service
   answers normally.

**Controller-only repro, no BlazeMeter account needed** — this is what produced the
matrix above, and it runs in about five minutes on any cluster:

    kubectl apply -f docs/repro/nginx-ingress-port.yaml

It reconstructs crane's Service and Ingress shapes and curls three backends —
crane's `8080`, the proposed `80`, and a `9999` control that must 503 for the run to
mean anything. Header comments carry the invocation and how to read the result.
Point it at Traefik, HAProxy or anything else to settle whether that controller is
tolerant or strict.

## Requested fix

1. Emit the Ingress backend as `port.number: 80`, or `port.name: "8080"` matching the
   name crane already puts on the Service port. Both are portable; the matrix above
   shows `80` passing on **both** controllers, so this is not a trade-off between them.
   One line in `kubernetes_nginx_web_expose_service`.
2. Make the ingress class configurable, defaulting to `nginx`.
3. Correct the `KUBERNETES_WEB_EXPOSE_TYPE` documentation to the values the binary
   implements, and reject unknown values at startup instead of stalling.

Item 1 is a strict improvement even for customers on `ingress-nginx` today: it removes
a dependency on controller-specific leniency that no API guarantee backs.

## Current workaround (customer-side, and why it exists)

Because crane rewrites its Ingress on every deploy, patching it is useless. Our
generator instead applies a **parallel** Service + Ingress per mock, with
`port == targetPort` so the reference resolves, selecting the mock pod by its identity
labels (`BZM_CONTAINER_NAME` / `BZM_HARBOR_ID` / `BZM_SHIP_ID`) rather than by crane's
Service name, which carries a per-deploy hash. Crane's own Ingress is left in place,
unclaimed and inert.

It works, but it is an extra apply step and an extra object per virtual service that
exists only to route around this. Fixing item 1 removes the need for it.

## Verified vs. not

- **Verified:** the spec-noncompliant port; the 503 and absent Route on OpenShift; that
  `ingress-nginx` v1.14.3 tolerates it via targetPort fallback while a bogus port 503s;
  that `80` works on both controllers; that the fix is reverted on every deploy; that
  istio/contour/openshift are unaffected; the hardcoded ingress class; and
  `INGRESS` creating nothing.
- **Not verified:** controllers other than these two — Traefik, HAProxy, AWS LB
  Controller, Gateway-API implementations. Each is independently either lenient or
  strict, and there is no guarantee either way. That uncertainty is itself an argument
  for item 1.
