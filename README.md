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

**Why account facts matter.** `funcIds: ["performance"]` tells the generator
that doduo, browser and mock-service images are dead weight, so they never ship.
A running agent reports the images it actually pulled, which is what makes the
mirror list and the `IMAGE_OVERRIDES` keys correct for a private registry — the
engine is `blazemeter/v4`, locally tagged `taurus-cloud`, and that is easy to
get wrong by hand. Ship id, crane version and heartbeat are read, not typed.

## Install

Needs Python 3.10+ and nothing else. The UI bundle is committed, so there is no
npm step whichever way you install.

```
pipx install "bzm-opl-gen[ui]"
bzm-opl-gen ui                              # opens the web UI
```

`brew install pipx && pipx ensurepath` first if you don't have pipx;
`uv tool install "bzm-opl-gen[ui]"` works identically. Upgrade with
`pipx upgrade bzm-opl-gen`, and pin with `"bzm-opl-gen[ui]==0.3.1"`.

`[ui]` is the web page; `[mcp]` is the MCP server for an AI session
([docs/mcp.md](docs/mcp.md)), and `[ui,mcp]` installs both. Neither extra changes
what the CLI does. Drop the extras entirely if you only want the CLI — it has no
dependencies at all.

<details>
<summary>Installing from git, or from a release wheel</summary>

To track `main`, or to run a tag PyPI has not seen:

```
pipx install "bzm-opl-gen[ui] @ git+https://github.com/benjithompson/bzm-opl-generator@v0.3.1"
```

Drop `@v0.3.1` to track `main`; add `--force` to reinstall over an existing copy.

Every release also attaches the built wheel. Download it from the Releases page,
or with `gh release download --repo benjithompson/bzm-opl-generator`, then
install the file by its real name:

```
pipx install './bzm_opl_gen-0.3.1-py3-none-any.whl[ui]'
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

Everything that talks to BlazeMeter takes `--api-key path/to/api-key.json` — a
BlazeMeter API key (Settings → API Keys) as JSON:

```
cp examples/api-key.example.json api-key.json   # then fill in id + secret
```

```json
{ "id": "<api key id>", "secret": "<api key secret>" }
```

`api-key*.json` is gitignored. The key needs read access to the account whose
location you're generating for, and write access only where something is created
or changed: `create-location`, `create-agent`, `delete-location`, `livetest`, and
`generate --rotate-token`, which mints a credential and kills the previous one.

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

Step 0 needs no account and no cluster, which is the case it exists for: the
answer is what you raise the request for the cluster with
([docs/capacity-planning.md](docs/capacity-planning.md)).

`out/README.md` is written for whoever receives the bundle and covers applying
it. `out/profile.json` is the resolved options, minus the token — replay it with
`generate --profile out/profile.json`.

Other things you'll reach for: `--format helm` for a chart instead of flat YAML
([docs/helm.md](docs/helm.md)), `--format docker` for a host that runs the agent
as a container ([docs/docker.md](docs/docker.md)), `--private-registry` plus `bzm-opl-gen images
--pull --mirror` for an air-gapped cluster, and `bzm-opl-gen livetest` to deploy
to a local cluster and wait for the agent to report online
([docs/live-test.md](docs/live-test.md)).

> **Re-generating against a live agent is safe, and that is new.** `generate`
> never issues an AUTH_TOKEN as a side effect: it takes `--auth-token`, or reads
> back the token already in `-o` (checking that bundle is for the same ship), or
> leaves the placeholder and says where a real one comes from. `--rotate-token`
> is the only thing that mints, and it warns first — the endpoint **issues a new
> token and invalidates the previous one**, so after a rotation the whole bundle
> has to be re-applied, Secret included, or that agent sits at `0/1 Running`.
> `--api-key` on its own no longer does anything to `generate`;
> [docs/options.md](docs/options.md) has the four-step resolution in full.

### Without a BlazeMeter account

Three paths need no account at all — and `plan` above needs no cluster either. The sample facts file gets you to real
manifests — edit it and watch which images land in `IMAGE_OVERRIDES`:

```
bzm-opl-gen generate --facts examples/facts.example.json --namespace demo -o out/
```

And for a customer whose account and cluster you cannot reach, the three values
BlazeMeter shows on an agent are enough to render every manifest (in the web UI,
switch step 1 to **Enter values manually**):

```
bzm-opl-gen facts --manual --harbor-id <HARBOR_ID> --ship-id <SHIP_ID> \
    --func-ids performance
bzm-opl-gen generate --auth-token <AUTH_TOKEN> --namespace their-ns -o out/
```

Nothing is validated and nothing is sent to BlazeMeter. What you give up: the
crane tag floats on `latest`, `IMAGE_OVERRIDES` comes from the built-in
catalogue rather than from the location itself (complete for performance, mock
services and the proxy recorder — **not** for GUI browser images, where the
account names one of 60+ version-pinned repos and nothing here can guess which),
`doctor` has no concurrency numbers to check against, and the agent-status watch
needs an API key. The UI and CLI both say so when it applies.

With an API key none of that applies, and it does not need a deployed agent
either: `bzm-opl-gen facts` reads the location's own image list, so the versions
are exact and a GUI location's browser image is named, for an agent that has
never been online.

The cluster is the same story, and has the same answer: have someone who *does*
have access run the read-only
[scripts/bzm-cluster-evidence.sh](scripts/bzm-cluster-evidence.sh), then
preflight from the file it produces —

```
./scripts/bzm-cluster-evidence.sh -n their-ns > cluster-evidence.json
bzm-opl-gen doctor   --facts facts.json --cluster-evidence cluster-evidence.json
bzm-opl-gen suggest                     --cluster-evidence cluster-evidence.json
```

`doctor` runs the same checks against the same data, with no kubeconfig here at
all. `suggest` answers the question that comes first — what that cluster implies
about the options you should generate with, each suggestion naming the evidence
behind it and whether it settles the option or only narrows it
([docs/preflight.md](docs/preflight.md#a-cluster-you-cannot-reach)).

## Documentation

| | |
|---|---|
| [docs/capacity-planning.md](docs/capacity-planning.md) | `plan` — how much infrastructure a load target needs, and the request document to ask for it with |
| [docs/mcp.md](docs/mcp.md) | `bzm-opl-gen mcp` — the MCP server, for an AI session with no checkout of this repo |
| [docs/options.md](docs/options.md) | every `generate` option and profile key |
| [docs/web-ui.md](docs/web-ui.md) | `bzm-opl-gen ui`: its two views, what each step does, and why it binds locally |
| [docs/helm.md](docs/helm.md) | `--format helm`, and managing the release with `helm upgrade` |
| [docs/docker.md](docs/docker.md) | `--format docker`: one agent as one container, and which options reach it |
| [docs/service-virtualization.md](docs/service-virtualization.md) | ingress backends for `mockServices`, which to pick, and `sv-expose` |
| [docs/preflight.md](docs/preflight.md) | `doctor`, `suggest`, `toolcheck`, and engine sizing |
| [docs/live-test.md](docs/live-test.md) | the live rig: local registry, proxy + CA, egress containment, real engine runs |
| [docs/hardened-engines.md](docs/hardened-engines.md) | the security context crane stamps on the pods it spawns, and which images have been observed running under it |
| [docs/crane-nginx-ingress-port.md](docs/crane-nginx-ingress-port.md) | write-up of crane's nginx Ingress port defect |
| [scripts/bzm-cluster-evidence.sh](scripts/bzm-cluster-evidence.sh) | read-only script a customer runs to send you their cluster's facts, which `doctor` and `suggest` then read with `--cluster-evidence` |
| [CONTEXT.md](CONTEXT.md) | the glossary — what *functionality*, *funcId*, *slot*, *bundle*, *sizing* and *agent* mean here, and which word wins where two were doing one job |
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, the test layers, PR flow, cutting a release |

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
don't report a security problem there — [SECURITY.md](SECURITY.md) says where
instead.

This is a community project, not a Perforce/BlazeMeter product: it generates
manifests *for* BlazeMeter private locations but carries no BlazeMeter support
commitment. For the platform itself, go to BlazeMeter support.

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for attribution; container images
the generated manifests reference are published by BlazeMeter under their own
terms and are not redistributed here.

## Not yet covered

- SV expose backends beyond the four implemented — and the behaviour of crane's
  nginx Ingress under controllers other than `ingress-nginx` and
  `ingress-to-route` (Traefik, HAProxy, AWS LB Controller are untested)
- External Secrets Operator / CSI secret-store variants
- Multi-engine runs (the rig validates one engine pod, on one node)
- Engine ephemeral-storage sizing under a real 40GB `/tmp` workload

References: [help.blazemeter.com — private locations](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-kubernetes.html),
[agent env variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html),
[system requirements](https://help.blazemeter.com/docs/guide/private-locations-system-requirements.html),
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane).
