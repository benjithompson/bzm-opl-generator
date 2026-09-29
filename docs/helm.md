# Helm output format

`--format helm` emits the same deployment as a chart instead of flat manifests:

```
bzm-opl-gen generate --format helm --namespace my-project \
    --auth-token <token> -o out/

helm install crane ./out/helm -n my-project --create-namespace \
    -f out/bzm-opl-values.yaml
```

`out/helm/` is the chart, identical for every customer. `out/bzm-opl-values.yaml`
is the overlay, and the only file generated from the account. It is an overlay
rather than a rewritten `helm/values.yaml` so the chart's own defaults — crane's
resources, the probe timings — stay in one place. Re-generating replaces the
overlay and leaves the chart untouched. `helm show values ./out/helm` documents
every key.

Both formats render **the same objects** — same ConfigMap data, RBAC rules,
container spec — so the choice is about how you install and upgrade, not about
what ends up in the cluster.

`extraEnv` in the overlay carries agent variables that have no option of their
own. It is rendered into the ConfigMap last; `extra_env` refuses every name the
chart already writes, so it cannot shadow anything. A values file written by
hand has no such guard, and naming one there renders a ConfigMap with a
duplicate key.

## Managing the release with Helm

`helm upgrade` works because `autoUpdate` is **off by default** — unlike
BlazeMeter's own Kubernetes manifest, which ships it on.

With `--auto-update` (or `autoUpdate: true`), crane takes ownership of its own
Deployment within seconds of install, rewriting the image to BlazeMeter's
current version and `.spec.strategy` from `Recreate` to `RollingUpdate`. Helm's
server-side apply then fails the next upgrade on a field-ownership conflict,
half-applied, and `--force-conflicts` does not recover it. With auto-update on,
changing anything means uninstall + install.

With it off, keeping the agent current is your job — re-generate, or bump
`image.tag` and upgrade — and an agent far enough behind loses support.

`autoUpdate` is BlazeMeter's `AUTO_KUBERNETES_UPDATE`. `AUTO_UPDATE` is the
Docker-side switch and does nothing on a Kubernetes agent, so neither this chart
nor the manifests emit it; the [docker](docker.md) format does.

## Service virtualization

`--sv-ingress` and the three options under it become the chart's `sv.ingress`,
`sv.subdomain`, `sv.tlsSecret` and `sv.istioGateway`; the ConfigMap gets the
same `KUBERNETES_WEB_EXPOSE_*`, and the Role grants the API group the chosen
backend publishes into. The chart refuses the same combinations the generator
does, because a chart can also be installed by hand: `istio` and `contour` under
`serviceType: NODEPORT`, an OpenShift Route on a plain Kubernetes API server, a
gateway name only istio reads. See
[service virtualization](service-virtualization.md#the-same-thing-as-a-chart).

The docker format's three SV options (`sv_hostname`, `sv_tls_cert`,
`sv_tls_key`) are ignored here, since a chart has nowhere to put them.

## Differences from manifests

- **`livetest` does not take a chart directory.** The rig applies YAML with
  kubectl and exits with that message. Re-generate as manifests to live-test,
  then ship whichever format you prefer — they render the same objects.

The chart is also usable on its own, without generating anything — see
`bzm_opl_gen/templates/helm/README.md`. Standalone it floats the crane image tag
on `latest` and needs `imageOverrides` written by hand for a private registry;
generating fills both in from the account.
