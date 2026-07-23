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

## Quick start

```
# 1. gather facts from the customer's account
bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>

# 2. generate manifests
bzm-opl-gen generate --profile profiles/openshift-restricted.json \
    --namespace my-project --auth-token <TOKEN> -o out/

# private-registry variant
bzm-opl-gen generate --profile profiles/openshift-private-registry.json \
    --namespace my-project --private-registry registry.corp.com/bzm -o out/

# 3. mirror images if using a private registry
bzm-opl-gen images --facts facts.json --pull --mirror registry.corp.com/bzm

# 4. live-test: deploy + verify the agent reports online in BlazeMeter
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster kind          # disposable smoke cluster
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster current       # real cluster via active kubeconfig context
```

Run without installing: `python3 -m bzm_opl_gen ...` from the repo root.
No runtime dependencies (stdlib only); tests need `pip install -e .[test]`.

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
- Creating a new harbor/ship + token via API (today: existing location only)
- Engine resource override envs (`KUBERNETES_RESOURCES_*`)

References: [help.blazemeter.com — private locations](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-kubernetes.html),
[agent env variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html),
[system requirements](https://help.blazemeter.com/docs/guide/private-locations-system-requirements.html),
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane).
