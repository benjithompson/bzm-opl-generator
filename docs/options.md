# Options and profiles

Every option below is a `generate` flag (`--private-registry`, `--sv-ingress`, …)
or a key in a `--profile` JSON file. `bzm_opl_gen/profiles/` holds three scenario
presets — `standard`, `private-registry`, `proxy-ca` — which are *postures*, not
platforms: the default works on OpenShift and vanilla Kubernetes alike.

If someone has sent you a [cluster evidence
file](preflight.md#a-cluster-you-cannot-reach), `bzm-opl-gen suggest` says which
of these options that cluster decides and which it only narrows —
[what the cluster implies](preflight.md#what-the-cluster-implies-about-the-options-suggest).

| Option | Default | Meaning |
|---|---|---|
| `platform` | `openshift` | `openshift` = SCC-friendly (no runAsUser, engines inherit the SCC-assigned UID); `k8s` = pinned runAsUser 1337 |
| `restrict_engines` | `true` | engines crane spawns drop all capabilities and inherit crane's UID:GID (INHERIT_RUNNING_USER_AND_GROUP, cap-drop JSON). Crane's own default is a privileged engine pod, which restricted PodSecurity, OpenShift SCC and GKE Autopilot all reject — after the agent is online, so the run hangs at `BOOT_STARTING`. `--no-restrict-engines` only for an image that needs a capability — and it removes the posture from every container crane creates, so see which images have run under it in [Hardened engines](hardened-engines.md) first |
| `output_format` | `manifests` | `manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus a values overlay — see [Helm](helm.md) |
| `use_secret` | `true` | AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap (simplified) |
| `private_registry` | – | sets DOCKER_REGISTRY, builds IMAGE_OVERRIDES from facts, rewrites crane image |
| `auto_update` | `false` | `AUTO_KUBERNETES_UPDATE`: does crane rewrite its own Deployment when BlazeMeter ships a newer agent? **Off, which is a deliberate departure from BlazeMeter's own Kubernetes manifest** — theirs ships `'true'`, and with it on crane takes field ownership of its Deployment within seconds of install, so the next `helm upgrade` fails on a conflict `--force-conflicts` cannot resolve and changing anything means uninstall + install ([Helm](helm.md#managing-the-release-with-helm)). The cost of the default is that keeping the agent current is your job — re-generate and re-apply — and one far enough behind loses support. `--auto-update` hands that back to crane on those terms. (BlazeMeter's `AUTO_UPDATE` is the Docker-side switch and does nothing on a Kubernetes agent, so nothing here emits it) |
| `pull_secret` | – | imagePullSecrets name for the crane image |
| `cluster_rbac` | `false` | include optional read-only nodes ClusterRole/Binding (not required for perf tests) |
| `service_account_name` | `crane` | the account the agent runs as, and the one the RoleBinding (and ClusterRoleBinding) grants to. Used whether or not the bundle creates it, and **required** — see below |
| `service_account_create` | `true` | emit the ServiceAccount object. `--no-create-service-account` leaves it out for an account your platform team already owns; everything still references `service_account_name`, so it must exist before you apply |
| `service_type` | `CLUSTERIP` | NODEPORT is the BlazeMeter default but often disallowed. With `sv_ingress`, only `nginx` and `openshift` publish over NODEPORT — [the other two are refused](service-virtualization.md#service_type-and-the-backend-you-chose) |
| `sv_ingress` | – | `nginx` \| `istio` \| `contour` \| `openshift` — **required** for a `mockServices` location; `openshift` needs `platform: openshift`; `contour` and `istio` are refused with `service_type: NODEPORT`; see [Service virtualization](service-virtualization.md) |
| `sv_subdomain` | – | wildcard domain your ingress controller serves; required with `sv_ingress` |
| `sv_tls_secret` | – | wildcard TLS secret in the agent namespace; required with `sv_ingress`, **even for HTTP** |
| `sv_istio_gateway` | – | istio only, optional; unset means crane creates a Gateway per virtual service. Rejected with any other `sv_ingress`, since only crane's istio backend reads it |
| `proxy` | – | HTTP(S)_PROXY / NO_PROXY; optional `username`/`password` are URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth envs) and the credentialed URLs live in the Secret when `use_secret` is on |
| `engine_cpu_limit` / `engine_mem_limit` | – (documented 2 / 8Gi) | `KUBERNETES_RESOURCES_LIMITS_CPU` / `_MEMORY` — the limits crane stamps on every engine it spawns |
| `ca_bundle` \| `ca_existing_configmap[:key]` \| `ca_openshift_inject` | – | CA trust, pick one: inline PEM (generator creates the ConfigMap), reference a platform-owned trust-bundle ConfigMap (recommended — they rotate it), or OpenShift's `inject-trusted-cabundle` labeled ConfigMap (cluster injects + rotates). All three mount at `/var/cm` and propagate to engines via `KUBERNETES_CA_BUNDLE_MOUNT` |

## The service account

`service_account_name` is required in both output formats, including with
`service_account_create: false`, and an empty one is refused rather than
resolved. The tempting fallback — and what most Helm charts scaffold — is the
namespace's `default` ServiceAccount: that installs cleanly, runs, and binds
crane's Role to the account every other pod in the namespace runs as. A blank
field should not be able to decide that.

With `create` off nothing else changes: the Deployment's `serviceAccountName`
and both binding subjects name the account you gave. If it is not there, nothing
fails at apply time — the Deployment is accepted and no pod is ever created, the
reason being an event on the ReplicaSet. `bzm-opl-gen doctor` checks for it.

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
