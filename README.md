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
bzm-opl-gen generate --namespace my-project --api-key api-key.json -o out/

# private-registry scenario (profiles are scenario presets, not platforms —
# the default posture works on OpenShift and vanilla k8s alike)
bzm-opl-gen generate --profile bzm_opl_gen/profiles/private-registry.json \
    --namespace my-project --private-registry registry.corp.com/bzm -o out/

# 3. mirror images if using a private registry (pulls linux/amd64)
bzm-opl-gen images --facts facts.json --pull --mirror registry.corp.com/bzm

# 3b. or test the whole private-registry path locally: generate with
#     --private-registry host.minikube.internal:5001 then
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster minikube --local-registry 5001
#     (starts a registry:2 container, mirrors the location's images into it,
#      starts minikube trusting it, deploys, verifies agent online, tears down)

# 3c. …and the proxy + custom-CA path, on top of it. --local-proxy needs no
#     generate flags: it regenerates out/ from out/profile.json with the
#     proxy env + the proxy's own CA once the container is up.
bzm-opl-gen livetest --api-key api-key.json --namespace my-project \
    --cluster minikube --local-registry 5001 --local-proxy --contain-egress
#     (--contain-egress adds calico + a default-deny egress NetworkPolicy, so
#      the proxy is the only way out -- not merely the way that was taken)

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
| `proxy` | – | HTTP(S)_PROXY / NO_PROXY; optional `username`/`password` are URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth envs) and the credentialed URLs live in the Secret when `use_secret` is on |
| `ca_bundle` \| `ca_existing_configmap[:key]` \| `ca_openshift_inject` | – | CA trust, pick one: inline PEM (generator creates the ConfigMap), reference a platform-owned trust-bundle ConfigMap (recommended — they rotate it), or OpenShift's `inject-trusted-cabundle` labeled ConfigMap (cluster injects + rotates). All three mount at `/var/cm` and propagate to engines via `KUBERNETES_CA_BUNDLE_MOUNT` |

`generate` also writes `out/profile.json` — the fully resolved options, minus
`auth_token` (re-fetched from the API, so the file is safe to commit or hand
over). Replay it with `generate --profile out/profile.json`; `livetest
--local-proxy` reads it to re-render the manifests with the rig's proxy and CA.

Images are selected automatically from the location's enabled funcIds:
performance engines always ship; browser/grid (functionalGui), mock-service
(mockServices), SV bridge (sv-bridge), and recorder (proxyRecorder) images only
when that feature is enabled on the location. `images --all` lists everything.

## Live test

Success = the BlazeMeter API reports the ship with a **fresh heartbeat** and
idle/running state. That exercises the full chain: RBAC, SCC admission, image
pull, egress to `*.blazemeter.com`, and credentials. `--keep` skips teardown;
`--cluster kind` creates/deletes a disposable `bzm-opl-test` cluster (crane
comes online; engines won't fit laptop resources — use `--cluster current`
against a real cluster for full engine validation).

### Reproducing the hard customer environments locally

Two optional rigs turn a laptop into the awkward network a customer has, and
are torn down with the cluster.

| flag | container | what it proves |
|---|---|---|
| `--local-registry [PORT]` (5001) | `registry:2`, published on the host, pulled via `host.minikube.internal` | air-gapped pulls: `DOCKER_REGISTRY`, `IMAGE_OVERRIDES`, auto-update off |
| `--local-proxy` | `mitmproxy`, joined to the cluster's own docker network | proxy egress **and** custom CA trust |
| `--contain-egress` | calico + a default-deny egress NetworkPolicy | that the proxy is the **only** way out, not just the way that was taken |
| `--run-test TEST_ID` | a real BlazeMeter run on the location | what crane passes to the **engines** it spawns: image override, CA propagation, proxy env |

`--local-proxy` is deliberately hostile: mitmproxy terminates TLS with its own
CA, so `*.blazemeter.com` is unreachable unless the generated `REQUESTS_CA_BUNDLE`
/ CA ConfigMap actually lands in the crane process. The rig

1. starts the cluster, then mitmdump (authenticated by default —
   `--proxy-auth user:pass`, or `none` for an open proxy) **on the cluster's
   docker network**, addressed by container IP,
2. reads the mitm CA out of the container and appends it to the public roots,
3. CONNECTs through the proxy from inside the node and requires the attempt to
   appear in the proxy's *own* log before going further,
4. **regenerates** `out/` from `out/profile.json` with `proxy` + inline
   `ca_bundle` merged in (so the manifests under test are generator output, not
   a hand-patched Deployment),
5. deploys, waits for the agent to come online, and then requires
   `blazemeter.com` lines in the proxy log — online *without* them means the
   agent bypassed the proxy, which fails the test.

### What a pass actually proves

"Agent online" is a weak claim on its own — plenty of wrong configurations still
reach it. The run therefore also:

- **blackholes the public registries** on the node (`127.0.0.1 gcr.io`, plus a
  purge of cached copies) whenever `--local-registry` is on, so an image
  `IMAGE_OVERRIDES` forgot to rewrite is an ImagePullBackOff here rather than a
  silent fallback that only breaks in the customer's air-gapped cluster;
- **runs a negative control first** — the same deploy with the CA stripped,
  required to fail with `CERTIFICATE_VERIFY_FAILED` before the real run is
  trusted. A rig that cannot fail proves nothing. Skip with
  `--skip-negative-control` (saves ~2 min);
- **reads the deployed objects back** and checks the generator's promises:
  `AUTH_TOKEN` not in the ConfigMap, proxy credentials not readable there,
  `AUTO_KUBERNETES_UPDATE=false` under a private registry, `IMAGE_OVERRIDES`
  covering every image the location's funcIds need, every running image coming
  from the private registry, and the CA bundle actually present and parseable
  *inside the crane pod* (not merely mounted);
- **reads the proxy log** for what online-ness cannot show: any `407` (the
  embedded credentials were rejected) and any Kubernetes API traffic that
  `NO_PROXY` should have kept out. Lines the negative control produced are
  excluded from both checks.

Any of these failing turns a green run red, with the specific claim printed.

### Egress containment (`--contain-egress`)

Without it, the proxy log proves the agent *used* the proxy — not that it had
to. `--contain-egress` starts minikube with calico and applies a default-deny
egress NetworkPolicy to the namespace, opening only DNS, the Kubernetes API,
and the proxy. Then it probes from inside the crane pod: `a.blazemeter.com`
must be unreachable directly and reachable through the proxy.

```
egress contained: DNS + apiserver (10.96.0.1:443, 192.168.67.2:8443) + proxy 192.168.67.3:8080, everything else denied
  egress probes from the crane pod: direct rc=28, via proxy rc=0
```

Both halves matter: "direct fails" alone is equally consistent with a policy so
tight nothing works, which would pass a containment check while proving
nothing. Two details this needs to be real rather than decorative:

- **minikube's default CNI accepts NetworkPolicies and enforces none**, so the
  policy would be a silent no-op. `--cni` only applies at cluster creation, so
  if the running profile has no policy enforcer the rig recreates it (it is
  disposable by design).
- **The API rule names both the Service ClusterIP and the endpoint behind it**,
  because policy is evaluated after kube-proxy's DNAT — a rule listing only the
  ClusterIP does not match the packet that actually leaves, and crane loses the
  API access it needs to create engine pods.

Probes run `curl` inside the crane pod, not python: `/usr/local/bin/python3`
there is a crane-agent shim, not an interpreter.

### Engine validation (`--run-test TEST_ID`)

Crane coming online says nothing about the pods crane *creates*. `--run-test`
runs an existing BlazeMeter test on the location so an engine actually spawns,
then checks what crane handed it:

```
test 15783207 repointed at harbor-6a63a79dcc45dccca90bf440 (original locations saved for restore)
started test 15783207 -> master 82803809
  engine pod r-v4-6a63ce4e06112601331279-0-0-c-t6cd7 (Running, 10.244.0.9)
  master 82803809: BOOT_STARTING … TAURUS_ENGINE_READY … DATA_RECEIVED … ENDED
  proxy saw engine upload traffic: data.blazemeter.com=64, storage.blazemeter.com=22
restored the original locations on test 15783207
```

Checked: the engine image comes from the private registry (a *different*
`IMAGE_OVERRIDES` key than crane's own, so crane being right proves nothing
about it), the CA bundle propagated via `KUBERNETES_CA_BUNDLE_MOUNT`,
`HTTPS_PROXY` reached the engine env, the engine's own traffic appears in the
proxy log under its pod IP, and the run reached `ENDED` rather than dying.

The test's `executions[].locations` are repointed at `harbor-<id>` and restored
in a `finally`; the original is printed so it can be put back by hand if the
process is killed. Engines are sized down with `--engine-cpu` / `--engine-mem`
(default 1 / 4Gi) — the documented 2 CPU / 8Gi will not schedule on a laptop.
Note crane sets its own resource *requests* (250m / 256Mi) and only the limits
come from the generated envs.

Engines mount the bundle as a file (`/var/cm/ca-bundle.crt`, subPath) where
crane mounts the directory — the check accepts both.

Why the CONNECT probe, and why the cluster network rather than a published port: a host
port belongs to whatever already claimed it (an ssh tunnel, a stray Java
process), and the node then reaches *that* instead. The symptom is a plausible
lie — the agent gets `403 Forbidden` from a proxy that was never yours, while
your proxy's log stays empty.

Notes: the CA bundle is mitm CA + public roots, because replacing the trust
store outright is not what a corporate bundle does. Image pulls come from the
kubelet, which ignores the pod's proxy env — that's why the registry rig is
reachable directly. mitmproxy is pinned to `11.1.3`; 12+ dies with SIGILL on
Apple-silicon VMs. The CA ConfigMap is applied `--server-side`: a real bundle
overruns the 256KB cap on kubectl's last-applied-configuration annotation.

Not covered by either rig: proof that egress *cannot* leave except through the
proxy (needs `--cni=calico` + a default-deny egress NetworkPolicy), and CA
propagation into engine pods, which only a real test run on the location
exercises.

## Layout

```
bzm_opl_gen/
  api.py         BlazeMeter API client (stdlib)
  facts.py       account fact gathering + image classification
  generate.py    manifest rendering (templates/ + per-option assembly)
  livetest.py    deploy, poll-until-online, teardown
  cli.py         subcommands: facts | generate | images | livetest
  templates/     per-CRD best-practice templates
  profiles/      scenario presets (standard | private-registry | proxy-ca)
tests/           offline unit tests (fixture facts)
```

## Roadmap / not yet covered

- Istio/nginx service-virtualization ingress env sets
- External Secrets Operator / CSI secret-store variants
- Multi-engine runs (the rig validates one engine pod, on one node)
- Engine ephemeral-storage sizing under a real 40GB `/tmp` workload

References: [help.blazemeter.com — private locations](https://help.blazemeter.com/docs/guide/private-locations-install-blazemeter-agent-for-kubernetes.html),
[agent env variables](https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html),
[system requirements](https://help.blazemeter.com/docs/guide/private-locations-system-requirements.html),
[Blazemeter/helm-crane](https://github.com/Blazemeter/helm-crane).
