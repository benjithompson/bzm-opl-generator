# bzm-opl-gen

Generate — and **live-test** — BlazeMeter OPL (On-Premise/Private Location)
Kubernetes & OpenShift deployments for customers, driven by **facts from their
actual BlazeMeter account** instead of hand-edited templates.

```
        BlazeMeter API                customer parameters
   (harbor, ships, funcIds,        (namespace, registry, platform,
    live image inventory)           features, secret policy, ...)
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

Needs Python 3.10+ and access to this repo. Releases ship a prebuilt wheel —
the UI bundle is inside it, so there's no npm step.

```
brew install pipx gh && pipx ensurepath     # once, if you don't have them
gh auth login                               # once, if gh isn't set up

gh release download --repo benjithompson/bzm-opl-generator --pattern '*.whl'
pipx install './bzm_opl_gen-*.whl[ui]'
bzm-opl-gen ui                              # opens the web UI
```

`gh release download` with no tag takes the newest release; pass a tag like
`v0.2.0` to pin an older one. `gh` is what handles the authentication — this
repo is private, so a plain `pip install git+https://…` fails for anyone whose
git credentials aren't already set up for GitHub. No `gh`? Download the `.whl`
from the Releases page in a browser and `pipx install` it the same way.

**"release not found" or "repository not found" means you don't have access to
the repo** — GitHub reports private repos as missing rather than forbidden. Ask
for access; it isn't a typo in the command.

Upgrade with `pipx install --force` on the newer wheel. Drop `[ui]` if you only
want the CLI — it has no dependencies at all.

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
location you're generating for, and write access only for the commands that
create things (`create-location`, `create-ship`, `livetest`).

## Quick start

`bzm-opl-gen ui` walks the whole thing in a browser. The CLI equivalent:

```
# 0. find (or create) the location and agent
bzm-opl-gen locations --api-key api-key.json --account-name "<ACCOUNT NAME>"
bzm-opl-gen create-ship --api-key api-key.json --harbor-id <HARBOR_ID> \
    --name my-k8s-agent        # prints ship_id + AUTH_TOKEN

# 1. gather the location's facts from the account
bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>

# 2. generate manifests (--api-key fetches AUTH_TOKEN automatically)
bzm-opl-gen generate --namespace my-project --api-key api-key.json -o out/

# 3. preflight the target cluster before anyone waits on a stuck run
bzm-opl-gen doctor --facts facts.json --manifests out/ -n my-project

# 4. deploy
kubectl apply -n my-project -f out/
```

`out/README.md` is written for whoever receives the bundle and covers applying
it. `out/profile.json` is the resolved options, minus the token — replay it with
`generate --profile out/profile.json`.

Other things you'll reach for: `--format helm` for a chart instead of flat YAML
([docs/helm.md](docs/helm.md)), `--private-registry` plus `bzm-opl-gen images
--pull --mirror` for an air-gapped cluster, and `bzm-opl-gen livetest` to deploy
to a local cluster and wait for the agent to report online
([docs/live-test.md](docs/live-test.md)).

> **Re-generating against a live agent:** `--api-key` fetches the AUTH_TOKEN,
> and that endpoint **issues a new token and invalidates the previous one**. If
> an agent is already running for that ship, either re-apply the whole bundle
> (Secret included) or pass `--auth-token <existing>` instead.

### Without a BlazeMeter account

Two paths need no account at all. The sample facts file gets you to real
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
catalogue rather than a live agent's inventory (complete for performance, mock
services and the proxy recorder — **not** for GUI browser images, where only a
running agent says which of the 60+ version-pinned repos a location uses),
`doctor` has no concurrency numbers to check against, and the agent-status watch
needs an API key. The UI and CLI both say so when it applies.

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
| [docs/options.md](docs/options.md) | every `generate` option and profile key |
| [docs/web-ui.md](docs/web-ui.md) | what each step of `bzm-opl-gen ui` does, and why it binds locally |
| [docs/helm.md](docs/helm.md) | `--format helm`, and managing the release with `helm upgrade` |
| [docs/service-virtualization.md](docs/service-virtualization.md) | ingress backends for `mockServices`, which to pick, and `sv-expose` |
| [docs/preflight.md](docs/preflight.md) | `doctor`, `suggest`, `toolcheck`, and engine sizing |
| [docs/live-test.md](docs/live-test.md) | the live rig: local registry, proxy + CA, egress containment, real engine runs |
| [docs/hardened-engines.md](docs/hardened-engines.md) | the security context crane stamps on the pods it spawns, and which images have been observed running under it |
| [docs/crane-nginx-ingress-port.md](docs/crane-nginx-ingress-port.md) | write-up of crane's nginx Ingress port defect |
| [scripts/bzm-cluster-evidence.sh](scripts/bzm-cluster-evidence.sh) | read-only script a customer runs to send you their cluster's facts, which `doctor` and `suggest` then read with `--cluster-evidence` |
| [CONTRIBUTING.md](CONTRIBUTING.md) | setup, the two test layers, PR flow, cutting a release |
| [CLAUDE.md](CLAUDE.md) | live-rig internals and the environment trap behind each flag |

## Contributing

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests -q          # ~1s, no cluster, must end "N passed"
```

You need no BlazeMeter account to work on the generator — `examples/facts.example.json`
drives it. Everything lands on `main` through a PR. Full guide in
[CONTRIBUTING.md](CONTRIBUTING.md).

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
