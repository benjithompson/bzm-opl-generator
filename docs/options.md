# Options and profiles

Every option below is a `generate` flag (`--private-registry`, `--sv-ingress`, …)
or a key in a `--profile` JSON file. `bzm_opl_gen/profiles/` holds three scenario
presets — `standard`, `private-registry`, `proxy-ca` — which are *postures*, not
platforms: the default works on OpenShift and vanilla Kubernetes alike.

| Option | Default | Meaning |
|---|---|---|
| `platform` | `openshift` | `openshift` = SCC-friendly (no runAsUser, INHERIT_RUNNING_USER_AND_GROUP, cap-drop JSON); `k8s` = pinned runAsUser 1337 |
| `output_format` | `manifests` | `manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus a values overlay — see [Helm](helm.md) |
| `use_secret` | `true` | AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap (simplified) |
| `private_registry` | – | sets DOCKER_REGISTRY, builds IMAGE_OVERRIDES from facts, disables auto-update, rewrites crane image |
| `pull_secret` | – | imagePullSecrets name for the crane image |
| `cluster_rbac` | `false` | include optional read-only nodes ClusterRole/Binding (not required for perf tests) |
| `service_type` | `CLUSTERIP` | NODEPORT is the BlazeMeter default but often disallowed |
| `sv_ingress` | – | `nginx` \| `istio` \| `contour` \| `openshift` — **required** for a `mockServices` location; `openshift` needs `platform: openshift`; see [Service virtualization](service-virtualization.md) |
| `sv_subdomain` | – | wildcard domain your ingress controller serves; required with `sv_ingress` |
| `sv_tls_secret` | – | wildcard TLS secret in the agent namespace; required with `sv_ingress`, **even for HTTP** |
| `sv_istio_gateway` | – | istio only, optional; unset means crane creates a Gateway per virtual service. Rejected with any other `sv_ingress`, since only crane's istio backend reads it |
| `proxy` | – | HTTP(S)_PROXY / NO_PROXY; optional `username`/`password` are URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth envs) and the credentialed URLs live in the Secret when `use_secret` is on |
| `engine_cpu_limit` / `engine_mem_limit` | – (documented 2 / 8Gi) | `KUBERNETES_RESOURCES_LIMITS_CPU` / `_MEMORY` — the limits crane stamps on every engine it spawns |
| `ca_bundle` \| `ca_existing_configmap[:key]` \| `ca_openshift_inject` | – | CA trust, pick one: inline PEM (generator creates the ConfigMap), reference a platform-owned trust-bundle ConfigMap (recommended — they rotate it), or OpenShift's `inject-trusted-cabundle` labeled ConfigMap (cluster injects + rotates). All three mount at `/var/cm` and propagate to engines via `KUBERNETES_CA_BUNDLE_MOUNT` |

## Image selection, and the generated profile

Images are selected automatically from the location's enabled funcIds:
performance engines always ship; browser/grid (functionalGui), mock-service
(mockServices), and recorder (proxyRecorder) images only
when that feature is enabled on the location. `images --all` lists everything.

`generate` also writes `out/profile.json` — the fully resolved options, minus
`auth_token` (re-fetched from the API, so the file is safe to commit or hand
over). Replay it with `generate --profile out/profile.json`; `livetest
--local-proxy` reads it to re-render the manifests with the rig's proxy and CA.

> **Re-generating against a live agent:** `--api-key` fetches the AUTH_TOKEN,
> and that endpoint **issues a new token and invalidates the previous one**. If
> an agent is already running for that ship, either re-apply the whole bundle
> (Secret included) or pass `--auth-token <existing>` instead. A crane left with
> a stale token does not report an auth error — it logs `404` on
> `/ships/<id>/status` and sits at `0/1`, which reads like a deleted ship.
