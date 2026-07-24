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

## Why account facts matter

- `funcIds: ["performance"]` → generator knows doduo / charmander browser
  images / mock-service images are dead weight and omits them.
- A running agent reports its **actual pulled images** → exact mirror list +
  correct `IMAGE_OVERRIDES` keys/tags for private registries (e.g. the engine
  is `blazemeter/v4`, locally tagged `taurus-cloud` — easy to get wrong by hand).
- Ship id, crane version, heartbeat — all read, not typed.

## Install

```
brew install pipx && pipx ensurepath        # once, if you don't have pipx
pipx install "bzm-opl-gen[ui] @ git+https://github.com/benjithompson/bzm-opl-generator"
bzm-opl-gen ui                              # opens the web UI
```

`pipx` puts `bzm-opl-gen` on your PATH globally, isolated from everything else.
Upgrade later with `pipx reinstall bzm-opl-gen`. Working on the code instead?
`pipx install -e ".[ui]"` from a checkout tracks your edits live.

## Quick start

```
# 0. find (or create) the location and agent
bzm-opl-gen locations --api-key api-key.json --account-name "SE Demo"
bzm-opl-gen create-ship --api-key api-key.json --harbor-id <HARBOR_ID> \
    --name my-k8s-agent        # prints ship_id + AUTH_TOKEN

# 1. gather facts from the customer's account
bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>

# 2. generate manifests (--api-key fetches AUTH_TOKEN automatically)
bzm-opl-gen generate --profile profiles/openshift-restricted.json \
    --namespace my-project --api-key api-key.json -o out/

# private-registry variant
bzm-opl-gen generate --profile profiles/openshift-private-registry.json \
    --namespace my-project --private-registry registry.corp.com/bzm -o out/

# 3. mirror images if using a private registry (pulls linux/amd64)
bzm-opl-gen images --facts facts.json --pull --mirror registry.corp.com/bzm

# 3b. or test the whole private-registry path locally: generate with
#     --private-registry host.minikube.internal:5001 then
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster minikube --local-registry 5001
#     (starts a registry:2 container, mirrors the location's images into it,
#      starts minikube trusting it, deploys, verifies agent online, tears down)

# 4. live-test: deploy + verify the agent reports online in BlazeMeter
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster kind          # disposable smoke cluster
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster current       # real cluster via active kubeconfig context
```

Run without installing: `python3 -m bzm_opl_gen ...` from the repo root.
No runtime dependencies for the CLI (stdlib only); tests need `pip install -e .[test]`.

## Web UI

```
pip install -e .[ui]
bzm-opl-gen ui          # opens http://127.0.0.1:8765
```

Single page: Connect (key stays local) → pick/create location & agent →
configure (presets, private registry, proxy/CA, tolerations/nodeSelector,
engine sizing) → live manifest preview → download zip (AUTH_TOKEN fetched on
download) → watch the agent flip online. Profile JSON import/export round-trips
with `generate --profile`. Frontend dev: `cd frontend && npm install && npm run
dev` (proxies /api to :8765); `npm run build` refreshes the shipped bundle in
`bzm_opl_gen/ui_dist/`.

## Options (flags or `--profile` JSON)

| Option | Default | Meaning |
|---|---|---|
| `platform` | `openshift` | `openshift` = SCC-friendly (no runAsUser, INHERIT_RUNNING_USER_AND_GROUP, cap-drop JSON); `k8s` = pinned runAsUser 1337 |
| `use_secret` | `true` | AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap (simplified) |
| `private_registry` | – | sets DOCKER_REGISTRY, builds IMAGE_OVERRIDES from facts, disables auto-update, rewrites crane image |
| `pull_secret` | – | imagePullSecrets name for the crane image |
| `cluster_rbac` | `false` | include optional read-only nodes ClusterRole/Binding (not required for perf tests) |
| `service_type` | `CLUSTERIP` | NODEPORT is the BlazeMeter default but often disallowed |
| `proxy` | – | HTTP(S)_PROXY / NO_PROXY env |
| `gui` | `false` | include GUI-functional/mock images in overrides |

## Live test

Success = the BlazeMeter API reports the ship with a **fresh heartbeat** and
idle/running state. That exercises the full chain: RBAC, SCC admission, image
pull, egress to `*.blazemeter.com`, and credentials. `--keep` skips teardown;
`--cluster kind` creates/deletes a disposable `bzm-opl-test` cluster (crane
comes online; engines won't fit laptop resources — use `--cluster current`
against a real cluster for full engine validation).

## Layout

```
bzm_opl_gen/
  api.py         BlazeMeter API client (stdlib)
  facts.py       account fact gathering + image classification
  generate.py    manifest rendering (templates/ + per-option assembly)
  livetest.py    deploy, poll-until-online, teardown
  cli.py         subcommands: facts | generate | images | livetest
  templates/     per-CRD best-practice templates
profiles/        ready-made option sets
tests/           offline unit tests (fixture facts)
```

## Roadmap / not yet covered

- CA-bundle mount + `KUBERNETES_CA_BUNDLE_MOUNT` (air-gapped TLS interception)
- Istio/nginx service-virtualization ingress env sets
- Tolerations / nodeSelector for crane + engines
- External Secrets Operator / CSI secret-store variants
- Engine resource override envs (`KUBERNETES_RESOURCES_*`)
- funcIds-driven image selection (auto `--gui` when location has functionalGui)
- `livetest --cluster minikube` target (today: current | kind)

References: [help.blazemeter.com — private locations](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-kubernetes.html),
[agent env variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html),
[system requirements](https://help.blazemeter.com/docs/guide/private-locations-system-requirements.html),
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane).
