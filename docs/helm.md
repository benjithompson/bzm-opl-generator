# Helm output format

`--format helm` emits the same deployment as a chart instead of flat manifests:

```
bzm-opl-gen generate --format helm --namespace my-project \
    --auth-token <token> -o out/

helm install crane ./out/helm -n my-project --create-namespace \
    -f out/bzm-opl-values.yaml
```

`out/helm/` is the chart, byte-identical for every customer.
`out/bzm-opl-values.yaml` is the overlay, and the only file generated from the
account. It is an overlay rather than a rewritten `helm/values.yaml` on purpose:
the chart's own values file holds defaults the generator does not own — crane's
resources, the probe timings — and writing a complete file would mean restating
them where they could drift. Re-generating replaces the overlay and leaves the
chart untouched. `helm show values ./out/helm` documents every key.

Both formats render **the same objects** — same ConfigMap data, RBAC rules,
container spec — so the choice is about how you install and upgrade,
not about what ends up in the cluster. `tests/helm_parity.py` renders 29 option
combinations both ways and requires them to agree; it runs as its own CI job
because it is the one check that needs the `helm` binary.

`extraEnv` in the overlay is the one place a value crosses it as arbitrary text
— agent variables this generator has no setting of its own for. It is rendered
into the ConfigMap last and can shadow nothing above it, because `extra_env`
refuses every name the chart writes before the overlay is written. A values file
written by hand has no such guard, and naming one there renders a ConfigMap with
a duplicate key.

## Managing the release with Helm

`helm upgrade` works, because `autoUpdate` is **off by default** — a departure
from BlazeMeter's own Kubernetes manifest, which ships it on.

Generate with `--auto-update` (or set `autoUpdate: true`) and you get theirs:
crane takes ownership of its own Deployment within seconds of install, rewriting
the image to the version BlazeMeter currently ships and `.spec.strategy` from
`Recreate` to `RollingUpdate`, and Helm's server-side apply then fails the next
upgrade on a field-ownership conflict, half-applied. `--force-conflicts` does not
rescue it, because the chart never declares `strategy.rollingUpdate` and crane's
copy survives beside the forced `type: Recreate`. With auto-update on, changing
anything means uninstall + install.

The default's cost is that keeping the agent current is your job — re-generate,
or bump `image.tag` and upgrade — and an agent far enough behind loses support.
Both behaviours were confirmed against a live cluster and a real agent.

`autoUpdate` here is BlazeMeter's `AUTO_KUBERNETES_UPDATE`. Its `AUTO_UPDATE` is
a different variable — the Docker-side switch, inert on a Kubernetes agent — so
neither this chart nor the manifests emit it. The [docker](docker.md) format
does, off the same option, and leaves it unset unless it was answered: there is
no Deployment there for a self-update to fight over.

Two things differ, both deliberate:

- **Service virtualization is not supported.** Publishing a virtual service
  needs an ingress backend, the RBAC for whichever one it is, and a wildcard TLS
  secret. `--format helm` refuses a bundle *configured* for service
  virtualization — an `sv_ingress` other than none — rather than emitting a chart
  that would deploy, report idle, and stall at `WAITING_FOR_DOMAIN`. The test is
  the configuration, not the location: a location that offers mocks but is being
  generated for performance alone ([declining the
  functionality](service-virtualization.md#not-using-it-on-a-location-that-offers-it))
  carries no `sv_*` options and the chart is available again. Use
  `--format manifests`, or the upstream
  [Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane) chart.
  Since [#182](https://github.com/benjithompson/bzm-opl-generator/issues/182)
  this is the **only** format that refuses one: `--format docker` publishes
  virtual services its own way, and the three options it does that with
  (`sv_hostname`, `sv_tls_cert`, `sv_tls_key`) are ignored here rather than
  refused, since a chart has nowhere to put them.
- **`livetest` does not take a chart directory.** The rig applies YAML with
  kubectl and reads it back object by object; it exits with that message rather
  than globbing an empty top level. Re-generate as manifests to live-test, then
  ship whichever format you prefer — parity is what makes that safe.

The chart is also usable on its own, without generating anything — see
`bzm_opl_gen/templates/helm/README.md`. Standalone it floats the crane image tag
on `latest` and needs `imageOverrides` written by hand for a private registry;
generating fills both in from the account, which is the main reason to.
