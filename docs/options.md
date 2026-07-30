# Options and profiles

Every option below is a `generate` flag (`--private-registry`, `--sv-ingress`, …)
or a key in a `--profile` JSON file. `bzm_opl_gen/profiles/` holds three scenario
presets — `standard`, `private-registry`, `proxy-ca` — which are *postures*, not
platforms: the default works on OpenShift and vanilla Kubernetes alike.

If someone has sent you a [cluster evidence
file](preflight.md#a-cluster-you-cannot-reach), `bzm-opl-gen suggest` says which
of these options that cluster decides and which it only narrows —
[what the cluster implies](preflight.md#what-the-cluster-implies-about-the-options-suggest).

> The tables below are generated from `bzm_opl_gen/options.py`, which is also
> where the UI's field help and the MCP tool schemas get their descriptions.
> Edit the registry and run `python -m bzm_opl_gen.options`; editing a table
> cell here fails the test suite instead.

<!-- BEGIN GENERATED OPTIONS TABLE -- python -m bzm_opl_gen.options -->

### Platform and output

| Option | Default | Meaning |
|---|---|---|
| `platform` | `openshift` | `openshift` = SCC-friendly (no `runAsUser`, engines inherit the SCC-assigned UID); `k8s` = pinned `runAsUser` 1337. The difference is which side chooses the UID: OpenShift's SCC assigns one from the namespace's range and rejects a pod that pins its own, while plain Kubernetes assigns nothing and a restricted PodSecurity namespace then refuses the pod for running as root. So neither setting is a superset of the other, and the wrong one fails at admission rather than at generate time. It is a posture, not a product: the OpenShift default installs on vanilla Kubernetes too wherever the namespace assigns UIDs. |
| `output_format` | `manifests` | `manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus a values overlay -- see [Helm](helm.md). The same deployment expressed twice rather than two codebases, which `tests/helm_parity.py` is what holds it to. Refused for a service-virtualization location, whose ingress the chart does not carry. |
| `namespace` | `blazemeter` | The namespace every generated object carries, and the one crane's Role and RoleBinding are scoped to. Crane creates engine pods here, so it is also where the tests run. The bundle does **not** create the namespace -- `kubectl create namespace` first, or `helm install --create-namespace`. `doctor -n` overrides it for a check without re-generating. |

### Credentials

| Option | Default | Meaning |
|---|---|---|
| `auth_token` | `<YOUR_AUTH_TOKEN>` | The agent's `AUTH_TOKEN`, which is what identifies this deployment as that ship. Resolved in four steps, and only the second one calls BlazeMeter: `--auth-token` wins outright; `--rotate-token` (with `--api-key`) issues a **new** one; otherwise the token already written into the output directory is reused, provided that bundle's `profile.json` names the same ship; otherwise the placeholder stays and the command says where a real token comes from. It is the one option stripped from `out/profile.json`, and it stays stripped -- a profile is a file people commit and hand over. **Minting invalidates the previous token**, and an agent left holding a stale one does not report an auth error: crane answers `404`, logs `Sleeping for 300` and never starts its health service, so the pod sits `0/1 Running` and reads as a slow boot. Re-apply the whole bundle, Secret included, after any rotation. Supplying the token is also the way past an account that refuses the fetch outright -- some allow the token endpoint only from BlazeMeter's own gateway, and the agent's install command in the BlazeMeter UI carries the same value. |
| `use_secret` | `true` | AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap (simplified). Proxy credentials follow it: with `use_secret` on, the credentialed proxy URLs live in the Secret too. |

### Private registry

| Option | Default | Meaning |
|---|---|---|
| `private_registry` | -- | Sets `DOCKER_REGISTRY`, builds `IMAGE_OVERRIDES` from the facts, and rewrites the crane image. Every image the location needs must already be mirrored under this prefix -- a key missing from `IMAGE_OVERRIDES` does not fail, it silently falls back to the public registry, which is the failure `livetest --local-registry` exists to make loud. |
| `pull_secret` | -- | `imagePullSecrets` name for the crane image. The Secret itself is not generated -- it holds credentials, so create it in the namespace with `kubectl create secret docker-registry`. Crane passes the same name to the engine pods it spawns. |
| `registry_auth` | `false` | Emit commented `DOCKER_REGISTRY_USERNAME` / `DOCKER_REGISTRY_PASSWORD` entries. Commented, not set: these are credentials, and a generator that wrote them would put them in a file people paste into tickets. The lines are there so the shape is right and someone editing the bundle does not have to guess the variable names. `pull_secret` is the better answer for the crane image itself; this pair is what crane uses for the images *it* pulls. |

### Agent lifecycle

| Option | Default | Meaning |
|---|---|---|
| `auto_update` | -- (unset -> off) | `AUTO_KUBERNETES_UPDATE`: does crane rewrite its own Deployment when BlazeMeter ships a newer agent? **Off, which is a deliberate departure from BlazeMeter's own Kubernetes manifest** -- theirs ships `'true'`, and with it on crane takes field ownership of its Deployment within seconds of install, so the next `helm upgrade` fails on a conflict `--force-conflicts` cannot resolve and changing anything means uninstall + install ([Helm](helm.md#managing-the-release-with-helm)). The cost of the default is that keeping the agent current is your job -- re-generate and re-apply -- and one far enough behind loses support. `--auto-update` hands that back to crane on those terms. (BlazeMeter's `AUTO_UPDATE` is the Docker-side switch and does nothing on a Kubernetes agent, so nothing here emits it.) |

### Security and RBAC

| Option | Default | Meaning |
|---|---|---|
| `service_account_name` | `crane` | The account the agent runs as, and the one the RoleBinding (and ClusterRoleBinding) grants to. Used whether or not the bundle creates it, and **required** -- an empty one is refused rather than resolved to the namespace's `default` account, which would bind crane's Role to every pod in the namespace. See [the service account](#the-service-account). |
| `service_account_create` | `true` | Emit the ServiceAccount object. `--no-create-service-account` leaves it out for an account your platform team already owns; everything still references `service_account_name`, so it must exist before you apply. If it does not, nothing fails at apply time -- the Deployment is accepted and no pod is ever created. `doctor` checks for it, and `livetest` refuses a profile with this off, because the rig creates its own namespace and would wait out its whole timeout. |
| `cluster_rbac` | `false` | Include the optional read-only nodes ClusterRole/Binding. Not required for performance tests -- it lets crane read node capacity to place engines, which is a nicety, and cluster-scoped RBAC is the thing a platform team is most likely to refuse. Left off, the rest of the bundle is entirely namespace-scoped. |
| `run_as_user` | `1337` | The UID crane's pod runs as, on `platform: k8s` only. On OpenShift the SCC assigns a UID from the namespace's range and a pinned one is rejected at admission, so nothing is emitted there. 1337 is arbitrary beyond being non-root, which is what restricted PodSecurity requires. With `restrict_engines` on, this is also the UID:GID the engines inherit. |
| `restrict_engines` | `true` | Engines crane spawns drop all capabilities and inherit crane's UID:GID (`INHERIT_RUNNING_USER_AND_GROUP`, cap-drop JSON). Crane's own default is a privileged engine pod, which restricted PodSecurity, OpenShift SCC and GKE Autopilot all reject -- after the agent is online, so the run hangs at `BOOT_STARTING`. `--no-restrict-engines` only for an image that needs a capability -- and it removes the posture from every container crane creates, so see which images have run under it in [Hardened engines](hardened-engines.md) first. |

### Networking

| Option | Default | Meaning |
|---|---|---|
| `service_type` | `CLUSTERIP` | `KUBERNETES_SERVICE_USE_TYPE`. NODEPORT is the BlazeMeter default but often disallowed. With `sv_ingress`, only `nginx` and `openshift` publish over NODEPORT -- [the other two are refused](service-virtualization.md#service_type-and-the-backend-you-chose). Changing it later does not restyle the Services crane already pooled, so `kubectl get svc` will not report what is configured. |
| `proxy` | -- | `HTTP(S)_PROXY` / `NO_PROXY`; optional `username`/`password` are URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth envs) and the credentialed URLs live in the Secret when `use_secret` is on. Keys: `http`, `https`, `no_proxy`, `username`, `password`. Note that **JMeter ignores these for sampler traffic** -- the proxy an engine uses to reach the system under test has to be set in the test itself. |

### Service virtualization

Only meaningful for a location whose funcIds include `mockServices`, and for such a location `sv_ingress` is **required**; see [Service virtualization](service-virtualization.md).

| Option | Default | Meaning |
|---|---|---|
| `sv_ingress` | -- | `nginx` \| `istio` \| `contour` \| `openshift` -- **required** for a `mockServices` location; `openshift` needs `platform: openshift`; `contour` and `istio` are refused with `service_type: NODEPORT`. Each backend grants a different set of resources in crane's Role, so this picks the RBAC as well as the objects. |
| `sv_subdomain` | -- | Wildcard domain your ingress controller serves; required with `sv_ingress`. Every virtual service gets a host under it, and the endpoint BlazeMeter advertises is built from it -- so it has to resolve from wherever the tests run, not just inside the cluster. |
| `sv_tls_secret` | -- | Wildcard TLS secret in the agent namespace; required with `sv_ingress`, **even for HTTP** -- crane names it unconditionally, and an ingress referencing a Secret that is not there is accepted and then never serves. |
| `sv_istio_gateway` | -- | istio only, optional; unset means crane creates a Gateway per virtual service. Rejected with any other `sv_ingress`, since only crane's istio backend reads it. A Gateway whose selector matches no pod fails exactly like a wrong port would -- crane hardcodes `istio: ingressgateway`. |

### CA trust

Pick **exactly one** of the three modes -- inline PEM, an existing ConfigMap, or OpenShift injection. More than one is refused rather than resolved. All three mount at `/var/cm` and propagate to engines via `KUBERNETES_CA_BUNDLE_MOUNT`.

| Option | Default | Meaning |
|---|---|---|
| `ca_bundle` | -- | Inline PEM -- the generator creates the ConfigMap. The simplest mode and the one that goes stale: nothing rotates it for you. Bundles are large enough that the manifest crosses the 256KB cap on kubectl's last-applied-configuration annotation, which is why anything over 200KB applies `--server-side`. |
| `ca_existing_configmap` | -- | Reference a platform-owned trust-bundle ConfigMap -- recommended, because they rotate it and an inline copy does not follow. The ConfigMap must already exist in the agent namespace. |
| `ca_configmap_key` | -- (unset -> ca-bundle.crt) | The bundle file key within `ca_existing_configmap`. Unset means `ca-bundle.crt`, which is the convention both OpenShift and most cert-manager setups follow. Set it when yours does not -- the mount path engines are given is built from it, so a wrong key mounts an empty file rather than failing. |
| `ca_openshift_inject` | `false` | OpenShift's `inject-trusted-cabundle` labeled ConfigMap -- the cluster injects the bundle and rotates it. The generator emits the empty labeled ConfigMap; the content arrives from the cluster operator, so on anything that is not OpenShift it stays empty and the agent trusts nothing extra. |

### Scheduling

| Option | Default | Meaning |
|---|---|---|
| `tolerations` | -- | A Kubernetes toleration list, applied to the crane pod **and** passed to the engines crane spawns. Both, because a taint that keeps crane off a node pool keeps the engines off it too, and a bundle that tolerated one but not the other schedules the agent and then leaves every test Pending. JSON, e.g. `[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]`. |
| `node_selector` | -- | A label map applied to the crane pod and passed to the engines, for the same reason as `tolerations`. JSON, e.g. `{"pool":"loadtest"}`. `doctor` measures capacity against the nodes that match it, so a selector matching nothing is reported as no capacity rather than as a typo. |

### Engine and agent sizing

All unset by default: crane has its own defaults and this generator only overrides them when asked. `bzm-opl-gen doctor` checks whatever you set against real node capacity.

| Option | Default | Meaning |
|---|---|---|
| `engine_cpu_limit` | -- (BlazeMeter documents 2) | `KUBERNETES_RESOURCES_LIMITS_CPU` -- the CPU limit crane stamps on every engine it spawns. Unset leaves crane's own default, which BlazeMeter documents as 2. Worth lowering on an emulated arm64 runtime, where a 2-CPU engine stays Pending. This generator emits no LimitRange and will not: crane sets engine requests explicitly, so a `defaultRequest` never reaches them. |
| `engine_mem_limit` | -- (BlazeMeter documents 8Gi) | `KUBERNETES_RESOURCES_LIMITS_MEMORY` -- the memory limit crane stamps on every engine it spawns. Unset leaves crane's own default, documented as 8Gi. `livetest --run-test` prints what an engine actually used as `ENGINE SIZING:`, which is the number to size from. |
| `engine_ephemeral_request_mb` | -- | `KUBERNETES_REQUESTS_EPHEMERAL_STORAGE`, in MB. Matters most on GKE Autopilot, which sizes the node's boot disk from what the pod requests and gives an engine that requests nothing a share too small for the artifacts a run produces. BlazeMeter documents roughly 60GB of disk and 40GB of `/tmp` per concurrent engine; requesting the whole of that on a shared cluster is usually wrong, so set it from what a real run used. |
| `engine_ephemeral_limit_mb` | -- | `KUBERNETES_LIMITS_EPHEMERAL_STORAGE`, in MB. The ceiling, not the reservation -- a pod that exceeds an ephemeral-storage limit is evicted mid-run, which surfaces as a test that stops rather than as a resource error, so leave headroom over `engine_ephemeral_request_mb`. |
| `crane_ephemeral_storage` | -- (1Gi) | Crane's own pod, e.g. `2Gi`. One value sets **both** the request and the limit, deliberately: crane's disk use is its image plus logs, and a request below the limit on a cluster that sizes nodes from requests just moves the eviction somewhere harder to see. Unset uses `1Gi`. |

<!-- END GENERATED OPTIONS TABLE -->

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
`auth_token`, which is left out so the file can be committed, diffed and handed
over. Replay it with `generate --profile out/profile.json`; `livetest
--local-proxy` reads it to re-render the manifests with the rig's proxy and CA.

## Where the AUTH_TOKEN comes from

`generate` never mints one as a side effect. It resolves the token in four
steps, says which one it took, and only the second reaches BlazeMeter:

1. **`--auth-token <token>`** wins outright — the value you already hold is
   never replaced.
2. **`--rotate-token`** (with `--api-key`) issues a new one. Warned before it
   happens, because it cannot be undone.
3. **The bundle already in `-o`** — the token in `out/bzm_secret.yaml` (or the
   ConfigMap, or the chart overlay) is read back and reused, provided that
   directory's `profile.json` names the same `ship_id`. This is what makes
   regenerating a bundle produce byte-identical output.

   If that directory holds a bundle for a *different* ship — or one whose
   `profile.json` cannot say which ship its token belongs to — **the command
   refuses and writes nothing.** Not because borrowing the token would be wrong,
   though it would: generating there at all would *overwrite* that bundle, and
   its AUTH_TOKEN cannot be read back from BlazeMeter afterwards, because the
   only endpoint that returns one issues a new one. The token would survive only
   inside an agent already running on it. Say what this bundle's credential is —
   `--auth-token`, or `--rotate-token` for a fresh one — and neither reads the
   directory at all, so replacing it stays available to anyone who means to.
4. **The placeholder**, `<YOUR_AUTH_TOKEN>` — with a message naming the two
   places a real one comes from: what `create-ship` printed, or an agent already
   deployed, `kubectl -n <ns> get secret blazemeter-secret -o
   jsonpath='{.data.AUTH_TOKEN}' | base64 -d`. That command is printed for you
   to run; nothing here reads your cluster.

> **Why `--api-key` alone does nothing here.** It used to fetch the token, and
> that endpoint **issues a new one and invalidates the previous one** — so
> regenerating a bundle merely to look at it revoked the credential of an agent
> already running from the last one. Silently: a crane left with a stale token
> does not report an auth error. It answers `404`, logs `Sleeping for 300`,
> never starts its health service, and the pod sits `0/1 Running` looking like a
> slow boot. That cost a live debugging session. `--api-key` is now the
> credential for `--rotate-token` and has no other effect on `generate`.
