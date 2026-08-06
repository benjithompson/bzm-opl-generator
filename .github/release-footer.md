
---

## Install

```
pipx install "bzm-opl-gen[ui]==VERSION_NUMBER"
```

Needs Python 3.10+ and nothing else. Drop the `==VERSION_NUMBER` for the latest,
or `pipx upgrade bzm-opl-gen` if you already have it. Drop `[ui]` for the CLI
alone — it has no dependencies. The web UI is committed prebuilt, so there is no
npm step either way.

Would rather install from git, or from the artifact? Either of:

```
pipx install "bzm-opl-gen[ui] @ git+https://github.com/benjithompson/bzm-opl-generator@VERSION"
pipx install './bzm_opl_gen-VERSION_NUMBER-py3-none-any.whl[ui]'
```

The wheel is in the Assets below; install it by its real filename, and not with
a `*` in place of the version, which neither the shell nor pipx expands.

## Get a deployable bundle

```
bzm-opl-gen ui          # http://127.0.0.1:8765
```

Connect with a BlazeMeter API key (Settings → API Keys) — or enter the harbor
id, ship id and AUTH_TOKEN by hand if you have no access to the account — then
pick the location, configure, preview and download the zip.

The same thing on the CLI, ending in a cluster:

```
bzm-opl-gen locations --api-key api-key.json --account-name "<ACCOUNT NAME>"
bzm-opl-gen facts     --api-key api-key.json --harbor-id <HARBOR_ID>
bzm-opl-gen generate  --api-key api-key.json --namespace my-project -o out/
bzm-opl-gen doctor    --facts facts.json --manifests out/ -n my-project
kubectl apply -n my-project -f out/
```

`out/README.md` is written for whoever receives the bundle. Add `--format helm`
for a chart instead of flat YAML, and `--private-registry <host>/<path>` for an
air-gapped cluster.

No BlazeMeter access at all? `bzm-opl-gen facts --manual --harbor-id H
--ship-id S --func-ids performance` builds the facts from the three values
BlazeMeter shows on the agent, and contacts nothing.

Full docs: [README](https://github.com/benjithompson/bzm-opl-generator#readme) ·
[options](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/options.md) ·
[Helm](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/helm.md) ·
[preflight](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/preflight.md) ·
[live test](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/live-test.md)
