# bzm-opl-gen

[![tests](https://github.com/benjithompson/bzm-opl-generator/actions/workflows/tests.yml/badge.svg)](https://github.com/benjithompson/bzm-opl-generator/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/bzm-opl-gen)](https://pypi.org/project/bzm-opl-gen/)
[![Python](https://img.shields.io/pypi/pyversions/bzm-opl-gen)](https://pypi.org/project/bzm-opl-gen/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Generate — and **live-test** — BlazeMeter OPL (On-Premise/Private Location)
Kubernetes & OpenShift deployments for customers, driven by **facts from their
actual BlazeMeter account** instead of hand-edited templates.

```
        BlazeMeter API                customer parameters
   (harbor, ships, funcIds,        (namespace, registry, platform,
    live image inventory)           functionality, secret policy, ...)
              \                        /
               v                      v
          facts.json  ---->  bzm-opl-gen generate  ---->  out/*.yaml + README
                                                             |
                                              bzm-opl-gen livetest
                                        (apply to kind / current cluster,
                                         poll API until agent is ONLINE)
```

**Why account facts matter.** `funcIds: ["performance"]` tells the generator that
browser and mock-service images are dead weight, so they never ship. The
location's own image list gives exact versions and the right `IMAGE_OVERRIDES`
keys for a private registry — the engine is `blazemeter/v4` tagged
`taurus-cloud`, which is easy to get wrong by hand.

## Install

Needs Python 3.10+ and nothing else — the UI ships prebuilt, so there is no npm
step.

```
pipx install "bzm-opl-gen[ui]"
bzm-opl-gen ui                              # opens the web UI
```

`uv tool install` works the same. Upgrade with `pipx upgrade bzm-opl-gen`, pin
with `"bzm-opl-gen[ui]==0.3.2"`.

`[ui]` is the web page, `[mcp]` the MCP server ([docs/mcp.md](docs/mcp.md)),
`[ui,mcp]` both. The bare CLI pulls in one package, `cryptography` — it reads
the certificate a docker agent serves its virtual services with, to check the
hostname against it.

<details>
<summary>Installing from git, or from a release wheel</summary>

To track `main`, or to run a tag PyPI has not seen:

```
pipx install "bzm-opl-gen[ui] @ git+https://github.com/benjithompson/bzm-opl-generator@v0.3.2"
```

Drop `@v0.3.2` to track `main`; add `--force` to reinstall over an existing copy.

Every release also attaches the built wheel. Download it from the Releases page,
or with `gh release download --repo benjithompson/bzm-opl-generator`, then
install the file by its real name:

```
pipx install './bzm_opl_gen-0.3.2-py3-none-any.whl[ui]'
```

Name the version rather than globbing it. Neither the shell (inside quotes) nor
pipx expands a `*` there, and the error is `Unable to parse package spec`.

</details>

### From a checkout

Working on it, or already cloned? Same page, no wheel:

```
python3 -m venv .venv && .venv/bin/pip install -e ".[ui]"
.venv/bin/bzm-opl-gen ui
```

`[dev]` instead of `[ui]` adds the test dependencies — see
[Contributing](#contributing).

### Credentials

Everything that talks to BlazeMeter takes `--api-key path/to/api-key.json` — an
API key (Settings → API Keys) as `{ "id": "...", "secret": "..." }`:

```
cp examples/api-key.example.json api-key.json   # then fill in id + secret
```

`api-key*.json` is gitignored. Read access is enough except where something is
created or changed: `create-location`, `create-agent`, `delete-location`,
`livetest`, and `generate --rotate-token`, which mints a credential and kills
the previous one.

## Quick start

`bzm-opl-gen ui` walks the whole thing in a browser. The CLI equivalent:

```
# 0. before any of it exists: how much cluster does the load target need?
bzm-opl-gen plan --users 5000 -o ./plan     # writes capacity-request.md

# 1. find (or create) the location and agent
bzm-opl-gen locations --api-key api-key.json --account-name "<ACCOUNT NAME>"
bzm-opl-gen create-agent --api-key api-key.json --harbor-id <HARBOR_ID> \
    --name my-k8s-agent        # prints ship_id + AUTH_TOKEN -- keep the token

# 2. gather the location's facts from the account
bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>

# 3. generate manifests. The token comes from you, not from the API: generate
#    never mints one, because minting revokes the token a running agent holds
bzm-opl-gen generate --namespace my-project --auth-token <AUTH_TOKEN> -o out/

# 4. preflight the target cluster before anyone waits on a stuck run
bzm-opl-gen doctor --facts facts.json --manifests out/ -n my-project

# 5. deploy
kubectl apply -n my-project -f out/
```

Step 0 needs no account and no cluster — that is the case it exists for, since
its answer is what you raise the cluster request *with*
([docs/capacity-planning.md](docs/capacity-planning.md)).

`out/README.md` is written for whoever receives the bundle; `out/profile.json`
is the resolved options minus the token, replayed with `generate --profile`.

Also: `--format helm` for a chart ([docs/helm.md](docs/helm.md)),
`--format docker` for one agent as one container
([docs/docker.md](docs/docker.md)), `--private-registry` plus `images --pull
--mirror` for an air-gapped cluster, and `livetest` to deploy to a local cluster
and wait for the agent to come online ([docs/live-test.md](docs/live-test.md)).

> **Re-generating against a live agent is safe.** `generate` never mints an
> AUTH_TOKEN as a side effect — it takes `--auth-token`, reads back the token
> already in `-o`, or leaves the placeholder. Only `--rotate-token` mints, and it
> warns first: the endpoint **invalidates the previous token**, so the whole
> bundle has to be re-applied or that agent sits at `0/1 Running`
> ([docs/options.md](docs/options.md)).

### Without a BlazeMeter account

Three paths need no account, and `plan` above needs no cluster. The sample facts
file gets you to real manifests — edit it and watch which images land in
`IMAGE_OVERRIDES`:

```
bzm-opl-gen generate --facts examples/facts.example.json --namespace demo -o out/
```

For a customer whose account you cannot reach, the three values BlazeMeter shows
on an agent render every manifest (in the web UI, step 1 → **Enter values
manually**):

```
bzm-opl-gen facts --manual --harbor-id <HARBOR_ID> --ship-id <SHIP_ID> \
    --func-ids performance
bzm-opl-gen generate --auth-token <AUTH_TOKEN> --namespace their-ns -o out/
```

Nothing is validated and nothing is sent to BlazeMeter. What you give up: the
crane tag floats on `latest`, `IMAGE_OVERRIDES` comes from the built-in catalogue
rather than the location — complete except for **GUI browser images**, where the
account names one of 60+ pinned repos and nothing here can guess which — and
`doctor` has no concurrency numbers. With a key none of that applies, and it
needs no deployed agent: `facts` reads the location's own image list.

The cluster has the same answer — someone with access runs the read-only
[scripts/bzm-cluster-evidence.sh](scripts/bzm-cluster-evidence.sh), and you
preflight from the file:

```
./scripts/bzm-cluster-evidence.sh -n their-ns > cluster-evidence.json
bzm-opl-gen doctor   --facts facts.json --cluster-evidence cluster-evidence.json
bzm-opl-gen suggest                     --cluster-evidence cluster-evidence.json
```

`suggest` answers what comes first: what that cluster implies about the options
to generate with, each suggestion naming its evidence and whether it settles the
option or only narrows it
([docs/preflight.md](docs/preflight.md#a-cluster-you-cannot-reach)).

## Documentation

| | |
|---|---|
| [docs/capacity-planning.md](docs/capacity-planning.md) | `plan` — sizing a cluster, and the request document to ask for it with |
| [docs/options.md](docs/options.md) | every `generate` option and profile key |
| [docs/web-ui.md](docs/web-ui.md) | `bzm-opl-gen ui` — the two views, each step, why it binds locally |
| [docs/mcp.md](docs/mcp.md) | the MCP server, for an AI session with no checkout |
| [docs/helm.md](docs/helm.md) | `--format helm`, and `helm upgrade` |
| [docs/docker.md](docs/docker.md) | `--format docker`, and which options reach it |
| [docs/service-virtualization.md](docs/service-virtualization.md) | ingress backends for `mockServices`, and `sv-expose` |
| [docs/preflight.md](docs/preflight.md) | `doctor`, `suggest`, `toolcheck`, engine sizing |
| [docs/live-test.md](docs/live-test.md) | the live rig: registry, proxy + CA, egress containment |
| [docs/hardened-engines.md](docs/hardened-engines.md) | the security context crane stamps on the pods it spawns |
| [docs/crane-nginx-ingress-port.md](docs/crane-nginx-ingress-port.md) | write-up of crane's nginx Ingress port defect |
| [scripts/bzm-cluster-evidence.sh](scripts/bzm-cluster-evidence.sh) | read-only script a customer runs, feeding `--cluster-evidence` |
| [CONTEXT.md](CONTEXT.md) | the glossary — *functionality*, *funcId*, *slot*, *bundle*, *sizing*, *agent* |
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, test layers, PR flow, cutting a release |

## Contributing

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests -q          # ~3s, no cluster, must end "N passed"
```

You need no BlazeMeter account to work on the generator — `examples/facts.example.json`
drives it. Everything lands on `main` through a PR. Full guide in
[CONTRIBUTING.md](CONTRIBUTING.md), and [CONTEXT.md](CONTEXT.md) is the glossary
to read before naming anything new.

## Support

Bugs and feature requests go to
[Issues](https://github.com/benjithompson/bzm-opl-generator/issues). Please
don't report a security problem there — a private-location AUTH_TOKEN is a
credential, so use
[a security advisory](https://github.com/benjithompson/bzm-opl-generator/security/advisories/new)
instead, which stays private until it is published.

This is an independent project, not a BlazeMeter product: it generates manifests
*for* BlazeMeter private locations but carries no BlazeMeter support commitment.
For the platform itself — the agent, crane, the API — go to BlazeMeter support.

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attribution; container images
the generated manifests reference are published by BlazeMeter under their own
terms and are not redistributed here.

## Not yet covered

- SV expose backends beyond the four implemented, and crane's nginx Ingress
  under controllers other than `ingress-nginx` and `ingress-to-route`
- External Secrets Operator / CSI secret-store variants
- Multi-engine runs (the rig validates one engine pod, on one node)
- Engine ephemeral-storage sizing under a real 40GB `/tmp` workload

References: [help.blazemeter.com — private locations](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-kubernetes.html),
[agent env variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html),
[system requirements](https://help.blazemeter.com/docs/guide/private-locations-system-requirements.html),
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane).
