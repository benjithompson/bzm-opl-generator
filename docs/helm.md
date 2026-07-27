# Helm output format

`--format helm` emits the same deployment as a chart instead of flat manifests:

```
bzm-opl-gen generate --format helm --namespace my-project \
    --api-key api-key.json -o out/

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
not about what ends up in the cluster. `tests/helm_parity.py` renders 17 option
combinations both ways and requires them to agree; it runs as its own CI job
because it is the one check that needs the `helm` binary.

## Managing the release with Helm

Set `autoUpdate: false` in the overlay if you intend to run `helm upgrade`.
Left on (the default, matching the manifests), crane takes ownership of its own
Deployment within seconds of install — rewriting the image to the version
BlazeMeter currently ships and `.spec.strategy` from `Recreate` to
`RollingUpdate` — and Helm's server-side apply then fails the next upgrade on a
field-ownership conflict, half-applied. `--force-conflicts` does not rescue it,
because the chart never declares `strategy.rollingUpdate` and crane's copy
survives beside the forced `type: Recreate`. With auto-update on, changing
anything means uninstall + install.

Turning it off makes upgrades ordinary and leaves keeping the agent current to
you. Both behaviours were confirmed against a live cluster and a real agent.

Two things differ, both deliberate:

- **Service virtualization is not supported.** Publishing a virtual service
  needs an ingress backend, the RBAC for whichever one it is, and a wildcard TLS
  secret. `--format helm` refuses an SV location rather than emitting a chart
  that would deploy, report idle, and stall at `WAITING_FOR_DOMAIN`. Use
  `--format manifests`, or the upstream
  [Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane) chart.
- **`livetest` does not take a chart directory.** The rig applies YAML with
  kubectl and reads it back object by object; it exits with that message rather
  than globbing an empty top level. Re-generate as manifests to live-test, then
  ship whichever format you prefer — parity is what makes that safe.

The chart is also usable on its own, without generating anything — see
`bzm_opl_gen/templates/helm/README.md`. Standalone it floats the crane image tag
on `latest` and needs `imageOverrides` written by hand for a private registry;
generating fills both in from the account, which is the main reason to.
