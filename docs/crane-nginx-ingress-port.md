# crane: the NGINX expose backend writes a spec-invalid Ingress port

**Fix:** in `kubernetes_nginx_web_expose_service`, emit the Ingress backend as
`port.number: 80` (or `port.name: "8080"`). It currently emits `port.number: 8080`.

**Version:** crane `3.7.55` (`sha256:875ce4b04f24bfc82ace039b1b5a3b86e8345691f3daabe0f0326cafde753c25`),
Service Virtualization path (`mockServices` / `sv-bridge`).

> **Reproducing on `ingress-nginx` will show this passing.** That controller tolerates
> the bad value; OpenShift's `ingress-to-route` does not. Test there, or use the
> account-free repro below.

## The bug

Crane creates both objects, and they don't agree:

```yaml
# Service (crane)              # Ingress backend (crane)
ports:                         port:
  - name: "8080"                 number: 8080   # resolves against `port:` = 80
    port: 80
    targetPort: 8080
```

`ServiceBackendPort.number` is defined as the Service's `spec.ports[].port` — 80.
`8080` is the targetPort, so by spec the backend resolves to nothing.

Likely a name reference emitted into the number field: the Service port is *named*
`"8080"`, so `port.name: "8080"` would have been correct and portable.

## Evidence

| controller | crane's `8080` | fix `80` | control `9999` |
|---|---|---|---|
| `ingress-nginx` v1.14.3 (k8s 1.32) | 200 | 200 | 503 |
| OpenShift `ingress-to-route` | **503** | 200 | 503 |

The `9999` control matches nothing on the Service and must fail; it's what proves a
`200` is real tolerance and not a test that never reached the controller.

**Why nginx passes:** its `serviceEndpoints` accepts a match on `Port` **or
`TargetPort` or `Name`**. Confirmed the targetPort clause is the one doing it — with
the port renamed `"http"`, backend `8080` still returned 200.

**Why OpenShift fails:** `IncompleteIngressToRouteRules: No valid target port for
backend service ... at index 0`. No Route, endpoint 503s, mock pod healthy at `1/1`.
Patching the live Ingress to `80` makes the Route appear immediately and serve — then
every redeploy overwrites it back to `8080`, so there is no customer-side fix.

Not the same as the `openshift` backend, which also writes `8080`: a **Route**'s
`targetPort` resolves against the Service's *targetPort*, so it's correct there and
wrong in an Ingress.

## Repro — 5 minutes, no BlazeMeter account

```
kubectl apply -f nginx-ingress-port.yaml
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 18080:80 &
for h in crane fixed bogus; do
  printf '%-6s %s\n' "$h" "$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Host: $h.test" http://127.0.0.1:18080/)"
done
```

Rebuilds crane's object shapes against any controller. `bogus` must be 503 or the run
is invalid; `crane` is the answer — 200 tolerant, 503 strict.

## Two more on the same path

1. **`ingressClassName: nginx` is hardcoded**, with no env to change it. OpenShift
   ships only `openshift-default`, so nothing claims the Ingress and customers need a
   *cluster-admin* IngressClass alias for an otherwise fully namespaced deployment.
   Clusters also commonly name theirs `nginx-internal` or run more than one.
   **Ask:** `KUBERNETES_INGRESS_CLASS`, defaulting to `nginx`.

2. **`KUBERNETES_WEB_EXPOSE_TYPE` is documented wrong in both directions.** The
   env-var reference lists `INGRESS | CONTOUR | ISTIO`. `INGRESS` isn't real — crane
   starts clean, creates nothing, and stalls at `WAITING_FOR_DOMAIN` (worth rejecting
   at startup). `OPENSHIFT` works and is undocumented; it's the right answer on
   OpenShift and sidesteps both issues above. `strings` on the binary shows five:
   `kubernetes_{base,contour,istio,nginx,openshift}_web_expose_service`.

Related: **don't set `KUBERNETES_SERVICES_BLOCKING_GET=true` on the nginx path** — it
appears in BlazeMeter's own SV example, which is *Istio*. On nginx it makes crane skip
the Ingress entirely and fall into the NodePort node lookup, which 403s under
namespaced RBAC and stalls at `WAITING_FOR_DOMAIN`.

## Scope

Verified: everything above. Untested: Traefik, HAProxy, AWS LB Controller — each may
be lenient or strict. `80` passed on both controllers tested, so the fix is portable
regardless.
