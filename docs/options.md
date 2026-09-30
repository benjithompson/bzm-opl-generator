# Options and profiles

Every option below is a key in a `--profile` JSON file, and most are also a
`generate` flag (`--private-registry`, `--sv-ingress`, …). Four have no flag and
are reached through a profile: `registry_auth` and `run_as_user`, which the web
UI writes, and `engine_ephemeral_request_mb` / `engine_ephemeral_limit_mb`,
which are best set from what a real run used. `bzm_opl_gen/profiles/` holds
three starting profiles — `standard`, `private-registry`, `proxy-ca` — which are
*postures*, not platforms: the default works on OpenShift and vanilla Kubernetes
alike.

If someone has sent you a [cluster evidence
file](preflight.md#a-cluster-you-cannot-reach), `bzm-opl-gen suggest` says which
of these options that cluster decides and which it only narrows —
[what the cluster implies](preflight.md#what-the-cluster-implies-about-the-options-suggest).

> The tables below are generated from `bzm_opl_gen/options.py`, which also
> supplies the web UI's field help and the MCP tool schemas. Edit the registry
> and run `python -m bzm_opl_gen.options`; editing a table cell here fails the
> test suite.

<!-- BEGIN GENERATED OPTIONS TABLE -- python -m bzm_opl_gen.options -->

### Platform and output

| Option | Default | Meaning |
|---|---|---|
| `platform` | `openshift` | Which side chooses the pod UID. `openshift` sets no `runAsUser`, so the SCC assigns one from the namespace's range and engines inherit it; `k8s` pins `runAsUser` 1337, because plain Kubernetes assigns none and a restricted PodSecurity namespace refuses a pod running as root. The wrong one fails at admission, not at generate time. It is a posture, not a product: `openshift` also installs on any Kubernetes cluster whose namespaces assign UIDs -- say which product it is with `openshift_cluster`. |
| `openshift_cluster` | `false` | The product, where `platform` is the posture. Leave it `false` for the SCC-friendly posture on a cluster that is not OpenShift, and the bundle's commands are written with `kubectl` rather than `oc`, `sv_ingress: openshift` is refused (a plain API server serves no Route, so the agent would deploy and then stall), and `ca_openshift_inject` is not offered (nothing outside OpenShift fills the labeled ConfigMap). Ignored with `platform: k8s`. |
| `output_format` | `manifests` | `manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus a values overlay that renders the same objects -- see [Helm](helm.md). `docker` is a different platform: one agent as one container on a host with a docker daemon, as a `docker run` script in BlazeMeter's documented shape and an equivalent `compose.yaml` (use one or the other) -- see [Docker](docker.md). Most options are Kubernetes vocabulary and reach nothing there; the bundle's README names the ones you set. All three formats publish virtual services, each with its own options. |
| `namespace` | `blazemeter` | The namespace every generated object carries, and the one crane's Role and RoleBinding are scoped to. Crane creates engine pods here, so it is also where the tests run. The bundle does **not** contain the namespace object -- so a later `kubectl delete -f .` cannot take the namespace with it -- and its README creates it with a command that succeeds whether or not it already exists. `doctor -n` overrides it for a check without re-generating. |

### Credentials

| Option | Default | Meaning |
|---|---|---|
| `auth_token` | `<AUTH_TOKEN>` | The agent's `AUTH_TOKEN`, which identifies this deployment as that agent. Resolved in order, and only the second step calls BlazeMeter: `--auth-token` wins; `--rotate-token` (with `--api-key`) issues a **new** one; otherwise the token already in the output directory is reused if that bundle's `profile.json` names the same agent; otherwise the marker `<AUTH_TOKEN>` is written and the command says where to get a real one. Never written to `profile.json`. **Minting invalidates the previous token**, and an agent holding a stale one reports no auth error: crane answers `404`, logs `Sleeping for 300` and the pod sits `0/1 Running`. Re-apply the whole bundle, Secret included, after any rotation. The agent's install command in the BlazeMeter UI carries the same value, for accounts that refuse the token API. |
| `use_secret` | `true` | AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap (simplified). Proxy credentials follow it: with `use_secret` on, the credentialed proxy URLs live in the Secret too. |

### Private registry

| Option | Default | Meaning |
|---|---|---|
| `private_registry` | -- | Sets `DOCKER_REGISTRY`, builds `IMAGE_OVERRIDES` from the facts, and rewrites the crane image. Every image the location needs must be mirrored under this prefix: a missing one silently falls back to the public registry. Fill it with the bundle's own `bzm-opl-image-mirror.sh` -- on Kubernetes crane composes an engine's reference as `<registry>/<repo path>:<tag>`, so an image mirrored to any other path fails with ImagePullBackOff on the first test, after the agent already reports online. |
| `pull_secret` | -- | `imagePullSecrets` name for the crane image. The Secret itself is not generated -- it holds credentials, so create it in the namespace with `kubectl create secret docker-registry`. Crane passes the same name to the engine pods it spawns. |
| `registry_auth` | `false` | Emit commented `DOCKER_REGISTRY_USERNAME` / `DOCKER_REGISTRY_PASSWORD` entries, so the variable names are in place for you to fill in. Commented rather than set, so no credential is ever written to a generated file. `pull_secret` covers the crane image itself; this pair is what crane uses for the images *it* pulls. |

### Agent lifecycle

| Option | Default | Meaning |
|---|---|---|
| `auto_update` | -- (unset -> off) | `AUTO_KUBERNETES_UPDATE`: does crane rewrite its own Deployment when BlazeMeter releases a newer agent? **Off by default, unlike BlazeMeter's own manifest**: with it on, crane takes field ownership of its Deployment, so the next `helm upgrade` fails on a conflict and changing anything means uninstall + install ([Helm](helm.md#managing-the-release-with-helm)). With it off, keeping the agent current is your job -- re-generate and re-apply. (BlazeMeter's `AUTO_UPDATE` is the Docker-side switch and does nothing on a Kubernetes agent, so it is not emitted.) |

### Security and RBAC

| Option | Default | Meaning |
|---|---|---|
| `service_account_name` | `crane` | The account the agent runs as, and the one the RoleBinding (and ClusterRoleBinding) grants to, whether or not the bundle creates it. **Required**: left blank it becomes `<SERVICE_ACCOUNT_NAME>` rather than the namespace's `default` account, which would bind crane's Role to every pod in the namespace. See [the service account](#the-service-account). |
| `service_account_create` | `true` | Emit the ServiceAccount object. `--no-create-service-account` leaves it out for an account your platform team already owns; everything still references `service_account_name`, so it must exist before you apply. If it does not, nothing fails at apply time -- the Deployment is accepted and no pod is ever created. `doctor` checks for it, and `livetest` refuses a profile with this off. |
| `cluster_rbac` | `false` | Include the optional read-only nodes ClusterRole/Binding. Not required for performance tests -- it lets crane read node capacity to place engines, and cluster-scoped RBAC is what a platform team is most likely to refuse. Left off, the bundle is entirely namespace-scoped. |
| `run_as_user` | `1337` | The UID crane's pod runs as, on `platform: k8s` only. On OpenShift the SCC assigns a UID from the namespace's range and rejects a pinned one, so nothing is emitted there. Any non-root UID satisfies restricted PodSecurity. With `restrict_engines` on, this is also the UID:GID the engines inherit. |
| `restrict_engines` | `true` | Engines crane spawns drop all capabilities and inherit crane's UID:GID (`INHERIT_RUNNING_USER_AND_GROUP`, cap-drop JSON). Crane's own default is a privileged engine pod, which restricted PodSecurity, OpenShift SCC and GKE Autopilot all reject -- after the agent is online, so the run hangs at `BOOT_STARTING`. `--no-restrict-engines` only for an image that needs a capability; it removes the posture from every container crane creates, so check [Hardened engines](hardened-engines.md) first. |

### Networking

| Option | Default | Meaning |
|---|---|---|
| `service_type` | `CLUSTERIP` | `KUBERNETES_SERVICE_USE_TYPE`. NODEPORT is the BlazeMeter default but often disallowed. With `sv_ingress`, only `nginx` and `openshift` publish over NODEPORT -- [the other two are refused](service-virtualization.md#service_type-and-the-backend-you-chose). Changing it later does not change the Services crane already created, so `kubectl get svc` may not show what is configured. |
| `proxy` | -- | `HTTP(S)_PROXY` / `NO_PROXY`; optional `username`/`password` are URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth variables) and the credentialed URLs live in the Secret when `use_secret` is on. Keys: `http`, `https`, `no_proxy`, `username`, `password`. **JMeter ignores these for sampler traffic** -- the proxy an engine uses to reach the system under test has to be set in the test itself. |

### Service virtualization

Only meaningful for a location whose funcIds include `mockServices`. There are **two sets, one per platform**: the four `sv_ingress` options are Kubernetes' `KUBERNETES_WEB_EXPOSE_*`, and the three below them are the docker agent's `HOSTNAME_OVERRIDE` and `TLS_CERT`/`TLS_KEY`. Each format ignores the other's set. For a `mockServices` location generated as manifests or a chart, `sv_ingress` is **required** -- a backend, or `none` for performance testing only; see [Service virtualization](service-virtualization.md).

| Option | Default | Meaning |
|---|---|---|
| `sv_ingress` | -- | `nginx` \| `istio` \| `contour` \| `openshift` -- **required** for a `mockServices` location; `openshift` needs `platform: openshift`; `contour` and `istio` are refused with `service_type: NODEPORT`. Each backend grants a different set of resources in crane's Role, so this picks the RBAC as well as the objects. `none` means *performance only*: the location generates without an ingress, and virtual services deployed to it stall at `WAITING_FOR_DOMAIN`. Unset is not `none` -- it is an unanswered question, and such a location is refused until it is answered. |
| `sv_subdomain` | -- | Wildcard domain your ingress controller serves; required with `sv_ingress`. Every virtual service gets a host under it, and the endpoint BlazeMeter advertises is built from it -- so it has to resolve from wherever the tests run, not just inside the cluster. |
| `sv_tls_secret` | -- | Wildcard TLS secret; required with `sv_ingress`, **even for HTTP** -- crane always names it. Create it in the **agent's own namespace**: a Kubernetes Ingress resolves `tls.secretName` in its own namespace only, and BlazeMeter's page says `default` only because their walkthrough installs the agent there. An ingress naming a missing Secret still serves -- over the controller's own fake certificate on ingress-nginx -- so the failure shows up for whoever verifies TLS, not at deploy time. |
| `sv_istio_gateway` | -- | istio only, optional; unset means crane creates a Gateway per virtual service. Rejected with any other `sv_ingress`, since only crane's istio backend reads it. A Gateway whose selector matches no pod fails exactly like a wrong port would -- crane hardcodes `istio: ingressgateway`. |
| `sv_hostname` | -- | **Docker only** -- `HOSTNAME_OVERRIDE`, the docker agent's counterpart to the `sv_ingress` group. BlazeMeter builds endpoint URLs from this hostname and the port; without it they use this host's IP address. Any form BlazeMeter accepts, but it has to resolve to this host from wherever the clients are, and with `sv_tls_cert` set it is checked against that certificate at generate time. Ignored by the Kubernetes formats, whose agents return a DNS-based URL. |
| `sv_tls_cert` | -- | **Docker only** -- the X509 certificate, inline PEM, written into the bundle as `sv-tls.crt`, mounted at `/etc/ssl/certs/public.pem` and named by `TLS_CERT`. Content rather than a path, like `ca_bundle`, so a bundle can be generated for a host you cannot see; the script's `SV_TLS_CERT` still points it at a file the host already keeps. Optional: without the pair the endpoints are plain HTTP. `sv_hostname` is checked against this certificate's Subject Alternative Name and Common Name at generate time, and a mismatch is refused -- otherwise the agent looks healthy while every client rejects its endpoint. |
| `sv_tls_key` | -- | **Docker only** -- the private key for `sv_tls_cert`, inline PEM, written as `sv-tls.key` and mounted at `/etc/ssl/certs/privatekey.pem` for `TLS_KEY`. BlazeMeter require **PKCS#8 syntax** (`-----BEGIN PRIVATE KEY-----`); a PKCS#1 key (`-----BEGIN RSA PRIVATE KEY-----`) is refused, naming the conversion -- `openssl pkcs8 -topk8 -nocrypt`. A credential, so **not** written to `profile.json`: `generate --profile` on such a bundle needs `--auth-token` and `--sv-tls-key` again. |

### CA trust

Pick **exactly one** of the four modes -- inline PEM, a certificate file supplied later, an existing ConfigMap, or OpenShift injection. More than one is refused. All four mount at `/var/cm` and reach engines via `KUBERNETES_CA_BUNDLE_MOUNT`. Check the certificate before deploying with `bzm-opl-gen ca-check` -- see [CA trust](ca-trust.md).

| Option | Default | Meaning |
|---|---|---|
| `ca_bundle` | -- | Inline PEM -- the generator creates the ConfigMap. The simplest mode, and the one that goes stale: nothing rotates it for you. A large bundle can push the manifest past the 256KB cap on kubectl's last-applied-configuration annotation, so anything over 200KB applies `--server-side`. The PEM is linted at generate time: a server certificate, an expired CA or an intermediate without its root is warned, never refused. `--ca-bundle` also reads a DER `.cer` or a PKCS#7 `.p7b` and writes it as PEM. |
| `ca_bundle_slot` | `false` | The certificate is a **file**, named by `ca_cert_file`, and the bundle carries no PEM -- the convention BlazeMeter's own agent documentation and helm chart follow. The chart reads the file from the chart directory at install (`caBundle.file`), a manifests bundle's README leads with the `kubectl create configmap --from-file=<key>=<file>` line, and a docker bundle mounts the file beside its run script. Refused together with `ca_bundle`. |
| `ca_cert_file` | -- (unset -> <CA_CERT_FILE>) | The certificate's file name, and the only field the file mode asks for. It names the key inside the ConfigMap, the mounted file, the chart-directory file helm reads, and the `--from-file=` key. One certificate serves both of BlazeMeter's `request_ca_bundle` and `aws_ca_bundle`. Left blank it becomes `<CA_CERT_FILE>`, to be filled in once the file is known. With `ca_bundle` instead it defaults to `ca-bundle.crt`, since the bundle writes that file itself. |
| `ca_existing_configmap` | -- | Reference a platform-owned trust-bundle ConfigMap -- recommended, because they rotate it and an inline copy does not follow. The ConfigMap must already exist in the agent namespace, and the bundle's README prints the `create configmap` command for one that does not, keyed to match `ca_configmap_key`. |
| `ca_configmap_key` | -- (unset -> ca-bundle.crt) | The bundle file key within `ca_existing_configmap`. Unset means `ca-bundle.crt`, the convention OpenShift and most cert-manager setups follow. Set it when yours differs: the engines' mount path is built from it, and a wrong key mounts an empty file rather than failing. That is why the README's create command writes `--from-file=<key>=<path>` rather than the bare `--from-file=<path>` BlazeMeter document, which keys the entry on the file's own name. |
| `ca_openshift_inject` | `false` | OpenShift's `inject-trusted-cabundle` labeled ConfigMap -- the cluster injects the bundle and rotates it. The generator emits the empty labeled ConfigMap; the content arrives from the cluster operator, so on anything that is not OpenShift it stays empty and the agent trusts nothing extra. |

### Scheduling

| Option | Default | Meaning |
|---|---|---|
| `tolerations` | -- | A Kubernetes toleration list, applied to the crane pod **and** passed to the engines crane spawns: on a one-pool cluster, a taint that keeps crane off a pool keeps the engines off it too, and tolerating only one would schedule the agent and leave every test Pending. Set `engine_tolerations` to aim the engines at a different pool. JSON, e.g. `[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]`. |
| `node_selector` | -- | A label map applied to the crane pod and passed to the engines, for the same reason as `tolerations`. JSON, e.g. `{"pool":"loadtest"}`. `doctor` measures capacity against the nodes that match it, so a selector matching nothing is reported as no capacity. |
| `engine_node_selector` | -- | A label map applied to the engines **only**, overriding `node_selector` for them. This is the two-pool shape: crane is one small always-on pod, engines are large pods that exist only during a run. Unset means engines follow crane; an explicit `{}` means engines take no selector even though crane has one. **A dedicated pool does not by itself give engines their configured size**: the scheduler and autoscaler work on requests, which the bundle sets equal to the limits unless the location's overrideCPU/overrideMemory replace them, and a pool without a `maxPods` ceiling can still pack engines onto one node. The generated `nodepools.md` carries the per-provider recipe. |
| `engines_per_node` | -- (unset -> 1) | How many engines one node of the engine pool is meant to hold. It reaches no manifest: it sizes the generated `nodepools.md` (`maxPods` and the machine type) and is what `doctor`'s engine-packing check judges against. Unset means 1: engines are measuring instruments, and two on one node contend for CPU, NIC and cache, which shows up as latency the load generator added. Raising it is cheaper -- every node spends about a CPU and 2Gi on system pods -- provided the node is sized for that many engines at their **limits**, which the recipe does. A platform floor can override it: GKE refuses `--max-pods-per-node` below 8, and the recipe sizes for the larger number. |
| `engine_tolerations` | -- | A toleration list applied to the engines **only**, overriding `tolerations` for them. The companion to `engine_node_selector`: a taint keeps everything else off the engine pool, and this lets the engines past it. Unset means engines follow crane; an explicit `[]` means they tolerate nothing even though crane does. |

### Engine and agent sizing

All unset by default: crane has its own defaults and this generator only overrides them when asked. `bzm-opl-gen doctor` checks whatever you set against real node capacity.

| Option | Default | Meaning |
|---|---|---|
| `engine_cpu_limit` | -- (BlazeMeter documents 2) | `KUBERNETES_RESOURCES_LIMITS_CPU` -- the CPU limit crane stamps on every pod it spawns -- and `KUBERNETES_RESOURCES_DEFAULT_CPU`, its request, set to the same value. Unset, it derives from the location's `overrideCPU`, else 2; always written, so engines never run without a limit or on crane's small default request. A location's `overrideCPU`, if set, replaces the request. Worth lowering on an emulated arm64 runtime, where a 2-CPU engine stays Pending. |
| `engine_mem_limit` | -- (BlazeMeter documents 8Gi) | `KUBERNETES_RESOURCES_LIMITS_MEMORY` -- the memory limit crane stamps on every pod it spawns -- and `KUBERNETES_RESOURCES_DEFAULT_MEM`, its request, the same amount in MiB. Unset, it derives from the location's `overrideMemory` (MB, read as Mi), else 8Gi. A location's `overrideMemory`, if set, replaces the request. `livetest --run-test` prints what an engine actually used as `ENGINE SIZING:`, which is the number to size from. |
| `engine_ephemeral_request_mb` | -- | `KUBERNETES_REQUESTS_EPHEMERAL_STORAGE`, in MB. Matters most on GKE Autopilot, which sizes the node's boot disk from what the pod requests and gives an engine that requests nothing too little room for the artifacts a run produces. BlazeMeter documents roughly 60GB of disk and 40GB of `/tmp` per concurrent engine; requesting all of that on a shared cluster is usually wrong, so set it from what a real run used. |
| `engine_ephemeral_limit_mb` | -- | `KUBERNETES_LIMITS_EPHEMERAL_STORAGE`, in MB. The ceiling, not the reservation -- a pod that exceeds an ephemeral-storage limit is evicted mid-run, which surfaces as a test that stops rather than as a resource error, so leave headroom over `engine_ephemeral_request_mb`. |
| `crane_ephemeral_storage` | -- (1Gi) | Crane's own pod, e.g. `2Gi`. One value sets **both** the request and the limit: crane's disk use is its image plus logs, and a request below the limit on a cluster that sizes nodes from requests just moves the eviction somewhere harder to see. Unset uses `1Gi`. |

### Cluster checks

Objects that check the cluster rather than serve tests on it. Applying the bundle without them deploys exactly the same agent.

| Option | Default | Meaning |
|---|---|---|
| `crane_hook` | `false` | Adds [crane-hook](https://github.com/Blazemeter/crane-hook) to the bundle -- a one-shot Pod with its own read-only Role and RoleBinding that checks node capacity, egress to BlazeMeter and the registries, the RBAC the agent needs, and (for service virtualization) the ingress and its TLS secret. It exits 0 or 1; `kubectl logs cranehook` is the report, and you delete it when done. Under `--format helm` it becomes the chart's `helm test` hook, run by `helm test <release>`. With `private_registry` its image is added to the mirror script, since it is not in the location's image list. |

### Agent environment

For BlazeMeter agent variables that have no option above, without hand-editing a generated file that the next `generate` overwrites.

| Option | Default | Meaning |
|---|---|---|
| `extra_env` | -- | Agent environment variables with no option of their own -- `{"PREFERRED_INTERFACE": "eth1"}`. Carried by all three formats: ConfigMap entries for `manifests`, `extraEnv` in the values overlay for `helm`, `--env` flags for `docker`. They reach the **agent** only: crane builds the engines' environment from the `KUBERNETES_*` variables rather than passing its own down. Every name the generator writes itself is **refused**, naming the option that owns it, in every format -- set it there instead. The web UI lists BlazeMeter's documented variables that are left to set for this location; a variable not on that list is still accepted. |

<!-- END GENERATED OPTIONS TABLE -->

## Fields left blank

A required text option left empty resolves to a marker that names it: `<KEY>`,
the option's key in upper case, with a dotted key joined by an underscore.
`auth_token` gives `<AUTH_TOKEN>`, `proxy.https` gives `<PROXY_HTTPS>`. The
bundle's README opens with the list of fields carrying one, the marker beside
each and where the value comes from.

A marker is used instead of an empty string because each of these fields fails
quietly when empty — an unnamed service account becomes the namespace's
`default`, an empty AUTH_TOKEN looks like a slow boot, a blank subdomain stalls
a virtual service at `WAITING_FOR_DOMAIN`. No Kubernetes name may contain angle
brackets, so `kubectl apply` rejects the object and names the field.
`helm install` refuses one in the chart's own validation, and `bzm-opl-gen
livetest` refuses a bundle carrying one before it builds a cluster.

| when | fields |
|---|---|
| always | `namespace`, `service_account_name`, `auth_token` |
| before BlazeMeter has issued the ids | `harbor_id`, `ship_id` |
| once an SV backend is chosen | `sv_subdomain`, `sv_tls_secret` |
| once the group is switched on in the web UI | `private_registry`, `proxy.http`/`proxy.https`, `ca_existing_configmap`, `ca_bundle` |

**The ids.** `harbor_id` is a fact about the location and `ship_id` identifies
the agent; both may be left blank because a bundle is often needed *before* the
private location exists — the manifests are what a platform team approves.
`facts --manual` takes neither id, and the web UI's two boxes are optional. The
marker reaches the crane Deployment's labels and selector, so the API server
rejects that object (naming `metadata.labels`) while the rest of the bundle
applies. `ship_id` is recorded in `profile.json`; `harbor_id` is not, because a
profile records options and the location comes from facts.

**The web UI row.** A registry, a proxy and a CA are configured by *having* a
value, so on the command line blank and "not using one" are the same thing. Only
the web UI's switch tells them apart.

**Exceptions.** `--format docker` has no namespace and no ServiceAccount, so
neither is marked there (see [the docker bundle](docker.md)). A chart leaves
`authToken` empty rather than marked, because it is supplied at install time —
`helm install --set-string authToken=...` — and the values file is the file
people commit. The bundle README says so in its own sentence when no token was given,
since the token is not in its list of blank fields.

## The service account

`service_account_name` is required in both Kubernetes formats, including with
`service_account_create: false`; left blank it carries the marker above. It is
never resolved to the namespace's `default` ServiceAccount — which installs
cleanly and binds crane's Role to every other pod in the namespace.
`--format docker` has no ServiceAccount and ignores the option.

With `create` off nothing else changes: the Deployment's `serviceAccountName`
and both binding subjects name the account you gave. If it does not exist,
nothing fails at apply time — the Deployment is accepted and no pod is ever
created, with the reason as an event on the ReplicaSet. `bzm-opl-gen doctor`
checks for it.

## Image selection, and the generated profile

Images are selected from the location's enabled funcIds: performance engines
always ship; browser/grid (functionalGui), mock-service (mockServices) and
recorder (proxyRecorder) images only when that functionality is enabled on the
location. `images --all` lists everything.

`generate` also writes `out/profile.json` — the fully resolved options, minus
`auth_token` and `sv_tls_key`, so the file can be committed, diffed and handed
over. Replay it with `generate --profile out/profile.json`; `livetest
--local-proxy` reads it to re-render the manifests with the rig's proxy and CA.

## Where the AUTH_TOKEN comes from

`generate` never mints a token as a side effect. It resolves one in four steps,
says which it took, and only the second contacts BlazeMeter:

1. **`--auth-token <token>`** wins outright.
2. **`--rotate-token`** (with `--api-key`) issues a new one, after a warning:
   the endpoint **invalidates the previous token**, so any agent running on it
   stops working until the whole bundle is re-applied.
3. **The bundle already in `-o`** — the token in `out/bzm_secret.yaml` (or the
   ConfigMap, or the chart overlay) is reused, provided that directory's
   `profile.json` names the same `ship_id`. Regenerating a bundle therefore
   produces identical output.

   If the directory holds a bundle for a *different* agent — or one whose
   `profile.json` cannot say which — **the command refuses and writes nothing**,
   because generating there would overwrite a token BlazeMeter cannot return
   again. Pass `--auth-token`, or `--rotate-token` for a fresh one; neither
   reads the directory, so replacing it stays possible when you mean to.
4. **The marker** `<AUTH_TOKEN>`, with a message naming where a real one comes
   from: what `create-agent` printed, or an agent already deployed —
   `kubectl -n <ns> get secret blazemeter-secret -o
   jsonpath='{.data.AUTH_TOKEN}' | base64 -d`. That command is printed for you
   to run; nothing here reads your cluster.

`--api-key` on its own does not fetch a token: the only endpoint that returns
one issues a new one and revokes the old. A crane left with a revoked token
reports no auth error — it answers `404`, logs `Sleeping for 300`, and the pod
sits at `0/1 Running` looking like a slow boot.
