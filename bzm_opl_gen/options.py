"""What each generate option means, in one place.

`bundle_options.DEFAULT_OPTIONS` holds the default value; this registry holds
what the option is for, in two lengths: `summary` (at most 20 words; it lands in
every MCP session's schema and in the UI's field help) and `doc` (the
`docs/options.md` cell). tests/test_options.py holds the two key sets equal.

The table block in docs/options.md is generated from here:

    python -m bzm_opl_gen.options

which rewrites only the text between the markers; editing inside them fails
the test.
"""

import os
import re
import sys

from . import generate as gen
from . import bundle_options, footprint, service_virt


class Option:
    """One `generate` option: its shape, and what it is for. The default is
    read from `bundle_options.DEFAULT_OPTIONS`, never stored here."""

    def __init__(self, name, type, group, summary, doc,
                 choices=None, default_note=None):
        self.name = name
        self.type = type                  # JSON-schema type name
        self.group = group
        self.summary = summary
        self.doc = doc
        self.choices = tuple(choices) if choices else None
        self.default_note = default_note  # parenthetical beside the default

    @property
    def default(self):
        return bundle_options.DEFAULT_OPTIONS[self.name]

    @property
    def secret(self):
        """Whether the value is a credential (generate.SECRET_OPTIONS)."""
        return self.name in gen.SECRET_OPTIONS

    @property
    def nullable(self):
        """A `None` default means "not asked", which a caller must be able to
        send back."""
        return bundle_options.DEFAULT_OPTIONS[self.name] is None


# Section headings for the generated table, in order, each with an optional
# intro for what a row cannot say: a constraint between options.
GROUPS = [
    ("Platform and output", None),
    ("Credentials", None),
    ("Private registry", None),
    ("Agent lifecycle", None),
    ("Security and RBAC", None),
    ("Networking", None),
    ("Service virtualization",
     "Only meaningful for a location whose funcIds include `mockServices`. There "
     "are **two sets, one per platform**: the four `sv_ingress` options are "
     "Kubernetes' `KUBERNETES_WEB_EXPOSE_*`, and the three below them are the "
     "docker agent's `HOSTNAME_OVERRIDE` and `TLS_CERT`/`TLS_KEY`. Each format "
     "ignores the other's set. For a `mockServices` location generated as "
     "manifests or a chart, `sv_ingress` is **required** -- a backend, or `none` "
     "for performance testing only; see "
     "[Service virtualization](service-virtualization.md)."),
    ("CA trust",
     "Pick **exactly one** of the four modes -- inline PEM, a certificate file "
     "supplied later, an existing ConfigMap, or OpenShift injection. More than "
     "one is refused. All four mount at `/var/cm` and reach engines via "
     "`KUBERNETES_CA_BUNDLE_MOUNT`. Check the certificate before deploying "
     "with `bzm-opl-gen ca-check` -- see [CA trust](ca-trust.md)."),
    ("Scheduling", None),
    ("Engine and agent sizing",
     "All unset by default: crane has its own defaults and this generator only "
     "overrides them when asked. `bzm-opl-gen doctor` checks whatever you set "
     "against real node capacity."),
    ("Cluster checks",
     "Objects that check the cluster rather than serve tests on it. Applying "
     "the bundle without them deploys exactly the same agent."),
    ("Agent environment",
     "For BlazeMeter agent variables that have no option above, without "
     "hand-editing a generated file that the next `generate` overwrites."),
]


OPTIONS = [
    # ---- Platform and output -------------------------------------------
    Option(
        "platform", "string", "Platform and output",
        choices=["openshift", "k8s"],
        summary="Target platform: openshift leaves the UID to the SCC, k8s pins runAsUser.",
        doc="Which side chooses the pod UID. `openshift` sets no `runAsUser`, so "
            "the SCC assigns one from the namespace's range and engines inherit "
            "it; `k8s` pins `runAsUser` "
            f"{bundle_options.DEFAULT_OPTIONS['run_as_user']}, because plain "
            "Kubernetes assigns none and a restricted PodSecurity namespace "
            "refuses a pod running as root. The wrong one fails at admission, "
            "not at generate time. It is a posture, not a product: `openshift` "
            "also installs on any Kubernetes cluster whose namespaces assign "
            "UIDs -- say which product it is with `openshift_cluster`."),
    Option(
        "openshift_cluster", "boolean", "Platform and output",
        summary="Is the target cluster OpenShift? Decides oc vs kubectl, Routes and trust injection.",
        doc="The product, where `platform` is the posture. Leave it `false` for "
            "the SCC-friendly posture on a cluster that is not OpenShift, and "
            "the bundle's commands are written with `kubectl` rather than `oc`, "
            "`sv_ingress: openshift` is refused (a plain API server serves no "
            "Route, so the agent would deploy and then stall), and "
            "`ca_openshift_inject` is not offered (nothing outside OpenShift "
            "fills the labeled ConfigMap). Ignored with `platform: k8s`."),
    Option(
        "output_format", "string", "Platform and output",
        choices=["manifests", "helm", "docker"],
        summary="Flat YAML for kubectl, the Helm chart plus a values overlay, or a docker bundle.",
        doc="`manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus "
            "a values overlay that renders the same objects -- see "
            "[Helm](helm.md). `docker` is a different platform: one agent as one "
            "container on a host with a docker daemon, as a `docker run` script "
            "in BlazeMeter's documented shape and an equivalent `compose.yaml` "
            "(use one or the other) -- see [Docker](docker.md). Most options "
            "are Kubernetes vocabulary and reach nothing there; the bundle's "
            "README names the ones you set. All three formats publish virtual "
            "services, each with its own options."),
    Option(
        "namespace", "string", "Platform and output",
        summary="Namespace every generated object is placed in, and the one crane's Role covers.",
        doc="The namespace every generated object carries, and the one crane's "
            "Role and RoleBinding are scoped to. Crane creates engine pods here, "
            "so it is also where the tests run. The bundle does **not** contain "
            "the namespace object -- so a later `kubectl delete -f .` cannot "
            "take the namespace with it -- and its README creates it with a "
            "command that succeeds whether or not it already exists. `doctor -n` "
            "overrides it for a check without re-generating."),

    # ---- Credentials ---------------------------------------------------
    Option(
        "auth_token", "string", "Credentials",
        summary="The agent's AUTH_TOKEN. Never minted unless you ask; --rotate-token is the ask.",
        doc="The agent's `AUTH_TOKEN`, which identifies this deployment as that "
            "agent. Resolved in order, and only the second step calls "
            "BlazeMeter: `--auth-token` wins; `--rotate-token` (with "
            "`--api-key`) issues a **new** one; otherwise the token already in "
            "the output directory is reused if that bundle's `profile.json` "
            "names the same agent; otherwise the marker `<AUTH_TOKEN>` is "
            "written and the command says where to get a real one. Never "
            "written to `profile.json`. **Minting invalidates the previous "
            "token**, and an agent holding a stale one reports no auth error: "
            "crane answers `404`, logs `Sleeping for 300` and the pod sits "
            "`0/1 Running`. Re-apply the whole bundle, Secret included, after "
            "any rotation. The agent's install command in the BlazeMeter UI "
            "carries the same value, for accounts that refuse the token API."),
    Option(
        "use_secret", "boolean", "Credentials",
        summary="Put AUTH_TOKEN in a Secret; off puts it in the ConfigMap instead.",
        doc="AUTH_TOKEN in a Secret; `--no-secret` puts it in the ConfigMap "
            "(simplified). Proxy credentials follow it: with `use_secret` on, the "
            "credentialed proxy URLs live in the Secret too."),

    # ---- Private registry ----------------------------------------------
    Option(
        "private_registry", "string", "Private registry",
        summary="Registry prefix to pull every image from, e.g. registry.example.com/blazemeter.",
        doc="Sets `DOCKER_REGISTRY`, builds `IMAGE_OVERRIDES` from the facts, and "
            "rewrites the crane image. Every image the location needs must be "
            "mirrored under this prefix: a missing one silently falls back to "
            "the public registry. Fill it with the bundle's own "
            "`bzm-opl-image-mirror.sh` -- on Kubernetes crane composes an "
            "engine's reference as `<registry>/<repo path>:<tag>`, so an image "
            "mirrored to any other path fails with ImagePullBackOff on the "
            "first test, after the agent already reports online."),
    Option(
        "pull_secret", "string", "Private registry",
        summary="Name of an existing docker-registry Secret used to pull the crane image.",
        doc="`imagePullSecrets` name for the crane image. The Secret itself is not "
            "generated -- it holds credentials, so create it in the namespace with "
            "`kubectl create secret docker-registry`. Crane passes the same name to "
            "the engine pods it spawns."),
    Option(
        "registry_auth", "boolean", "Private registry",
        summary="Emit commented-out DOCKER_REGISTRY_USERNAME/PASSWORD lines for crane to fill in.",
        doc="Emit commented `DOCKER_REGISTRY_USERNAME` / `DOCKER_REGISTRY_PASSWORD` "
            "entries, so the variable names are in place for you to fill in. "
            "Commented rather than set, so no credential is ever written to a "
            "generated file. `pull_secret` covers the crane image itself; this "
            "pair is what crane uses for the images *it* pulls."),

    # ---- Agent lifecycle -----------------------------------------------
    Option(
        "auto_update", "boolean", "Agent lifecycle",
        default_note="unset -> off",
        summary="Let crane rewrite its own Deployment when BlazeMeter ships a newer agent.",
        doc="`AUTO_KUBERNETES_UPDATE`: does crane rewrite its own Deployment when "
            "BlazeMeter releases a newer agent? **Off by default, unlike "
            "BlazeMeter's own manifest**: with it on, crane takes field ownership "
            "of its Deployment, so the next `helm upgrade` fails on a conflict "
            "and changing anything means uninstall + install "
            "([Helm](helm.md#managing-the-release-with-helm)). With it off, "
            "keeping the agent current is your job -- re-generate and re-apply. "
            "(BlazeMeter's `AUTO_UPDATE` is the Docker-side switch and does "
            "nothing on a Kubernetes agent, so it is not emitted.)"),

    # ---- Security and RBAC ---------------------------------------------
    Option(
        "service_account_name", "string", "Security and RBAC",
        summary="The account crane runs as and the RoleBinding grants to. Required, never empty.",
        doc="The account the agent runs as, and the one the RoleBinding (and "
            "ClusterRoleBinding) grants to, whether or not the bundle creates it. "
            "**Required**: left blank it becomes `<SERVICE_ACCOUNT_NAME>` rather "
            "than the namespace's `default` account, which would bind crane's "
            "Role to every pod in the namespace. See "
            "[the service account](#the-service-account)."),
    Option(
        "service_account_create", "boolean", "Security and RBAC",
        summary="Emit the ServiceAccount object; off assumes your platform team already owns it.",
        doc="Emit the ServiceAccount object. `--no-create-service-account` leaves "
            "it out for an account your platform team already owns; everything "
            "still references `service_account_name`, so it must exist before "
            "you apply. If it does not, nothing fails at apply time -- the "
            "Deployment is accepted and no pod is ever created. `doctor` checks "
            "for it, and `livetest` refuses a profile with this off."),
    Option(
        "cluster_rbac", "boolean", "Security and RBAC",
        summary="Include the optional read-only nodes ClusterRole and binding.",
        doc="Include the optional read-only nodes ClusterRole/Binding. Not required "
            "for performance tests -- it lets crane read node capacity to place "
            "engines, and cluster-scoped RBAC is what a platform team is most "
            "likely to refuse. Left off, the bundle is entirely namespace-scoped."),
    Option(
        "run_as_user", "integer", "Security and RBAC",
        summary="UID for the crane pod on platform k8s. Ignored on OpenShift, where the SCC assigns one.",
        doc="The UID crane's pod runs as, on `platform: k8s` only. On OpenShift "
            "the SCC assigns a UID from the namespace's range and rejects a "
            "pinned one, so nothing is emitted there. Any non-root UID satisfies "
            "restricted PodSecurity. With `restrict_engines` on, this is also "
            "the UID:GID the engines inherit."),
    Option(
        "restrict_engines", "boolean", "Security and RBAC",
        summary="Engines crane spawns drop all capabilities and inherit crane's UID:GID.",
        doc="Engines crane spawns drop all capabilities and inherit crane's UID:GID "
            "(`INHERIT_RUNNING_USER_AND_GROUP`, cap-drop JSON). Crane's own default "
            "is a privileged engine pod, which restricted PodSecurity, OpenShift SCC "
            "and GKE Autopilot all reject -- after the agent is online, so the run "
            "hangs at `BOOT_STARTING`. `--no-restrict-engines` only for an image that "
            "needs a capability; it removes the posture from every container "
            "crane creates, so check [Hardened engines](hardened-engines.md) "
            "first."),

    # ---- Networking ----------------------------------------------------
    Option(
        "service_type", "string", "Networking",
        choices=["CLUSTERIP", "NODEPORT"],
        summary="How crane publishes the Services it owns. NODEPORT is BlazeMeter's default, often disallowed.",
        doc="`KUBERNETES_SERVICE_USE_TYPE`. NODEPORT is the BlazeMeter default but "
            "often disallowed. With `sv_ingress`, only `nginx` and `openshift` "
            "publish over NODEPORT -- [the other two are "
            "refused](service-virtualization.md#service_type-and-the-backend-you-chose). "
            "Changing it later does not change the Services crane already "
            "created, so `kubectl get svc` may not show what is configured."),
    Option(
        "proxy", "object", "Networking",
        summary="HTTP(S)_PROXY / NO_PROXY for the agent, with optional credentials.",
        doc="`HTTP(S)_PROXY` / `NO_PROXY`; optional `username`/`password` are "
            "URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth "
            "variables) and the credentialed URLs live in the Secret when "
            "`use_secret` is on. Keys: `http`, `https`, `no_proxy`, `username`, "
            "`password`. **JMeter ignores these for sampler traffic** -- the "
            "proxy an engine uses to reach the system under test has to be set "
            "in the test itself."),

    # ---- Service virtualization ----------------------------------------
    Option(
        "sv_ingress", "string", "Service virtualization",
        choices=list(service_virt.SV_INGRESS_TYPES) + [service_virt.SV_INGRESS_NONE],
        summary="Which ingress the mock services are published through, or `none` for performance only.",
        doc="`nginx` | `istio` | `contour` | `openshift` -- **required** for a "
            "`mockServices` location; `openshift` needs `platform: openshift`; "
            "`contour` and `istio` are refused with `service_type: NODEPORT`. Each "
            "backend grants a different set of resources in crane's Role, so this "
            "picks the RBAC as well as the objects. `none` means *performance "
            "only*: the location generates without an ingress, and virtual "
            "services deployed to it stall at `WAITING_FOR_DOMAIN`. Unset is "
            "not `none` -- it is an unanswered question, and such a location is "
            "refused until it is answered."),
    Option(
        "sv_subdomain", "string", "Service virtualization",
        summary="Wildcard domain your ingress controller serves; the endpoint host suffix.",
        doc="Wildcard domain your ingress controller serves; required with "
            "`sv_ingress`. Every virtual service gets a host under it, and the "
            "endpoint BlazeMeter advertises is built from it -- so it has to resolve "
            "from wherever the tests run, not just inside the cluster."),
    Option(
        "sv_tls_secret", "string", "Service virtualization",
        summary="Wildcard TLS secret in the agent's own namespace, not default. Required with sv_ingress, even for HTTP.",
        doc="Wildcard TLS secret; required with `sv_ingress`, **even for HTTP** -- "
            "crane always names it. Create it in the **agent's own namespace**: "
            "a Kubernetes Ingress resolves `tls.secretName` in its own namespace "
            "only, and BlazeMeter's page says `default` only because their "
            "walkthrough installs the agent there. An ingress naming a missing "
            "Secret still serves -- over the controller's own fake certificate "
            "on ingress-nginx -- so the failure shows up for whoever verifies "
            "TLS, not at deploy time."),
    Option(
        "sv_istio_gateway", "string", "Service virtualization",
        summary="Existing istio Gateway to attach to; unset means crane creates one per virtual service.",
        doc="istio only, optional; unset means crane creates a Gateway per virtual "
            "service. Rejected with any other `sv_ingress`, since only crane's istio "
            "backend reads it. A Gateway whose selector matches no pod fails exactly "
            "like a wrong port would -- crane hardcodes `istio: ingressgateway`."),

    Option(
        "sv_hostname", "string", "Service virtualization",
        summary="Docker only: the hostname this agent advertises its virtual services under.",
        doc="**Docker only** -- `HOSTNAME_OVERRIDE`, the docker agent's "
            "counterpart to the `sv_ingress` group. BlazeMeter builds endpoint "
            "URLs from this hostname and the port; without it they use this "
            "host's IP address. Any form BlazeMeter accepts, but it has to "
            "resolve to this host from wherever the clients are, and with "
            "`sv_tls_cert` set it is checked against that certificate at "
            "generate time. Ignored by the Kubernetes formats, whose agents "
            "return a DNS-based URL."),
    Option(
        "sv_tls_cert", "string", "Service virtualization",
        summary="Docker only: inline PEM certificate the agent serves its virtual services with.",
        doc="**Docker only** -- the X509 certificate, inline PEM, written into "
            "the bundle as `sv-tls.crt`, mounted at `/etc/ssl/certs/public.pem` "
            "and named by `TLS_CERT`. Content rather than a path, like "
            "`ca_bundle`, so a bundle can be generated for a host you cannot "
            "see; the script's `SV_TLS_CERT` still points it at a file the host "
            "already keeps. Optional: without the pair the endpoints are plain "
            "HTTP. `sv_hostname` is checked against this certificate's Subject "
            "Alternative Name and Common Name at generate time, and a mismatch "
            "is refused -- otherwise the agent looks healthy while every client "
            "rejects its endpoint."),
    Option(
        "sv_tls_key", "string", "Service virtualization",
        summary="Docker only: inline PEM private key for sv_tls_cert. PKCS#8 syntax, and never in profile.json.",
        doc="**Docker only** -- the private key for `sv_tls_cert`, inline PEM, "
            "written as `sv-tls.key` and mounted at "
            "`/etc/ssl/certs/privatekey.pem` for `TLS_KEY`. BlazeMeter require "
            "**PKCS#8 syntax** (`-----BEGIN PRIVATE KEY-----`); a PKCS#1 key "
            "(`-----BEGIN RSA PRIVATE KEY-----`) is refused, naming the "
            "conversion -- `openssl pkcs8 -topk8 -nocrypt`. A credential, so "
            "**not** written to `profile.json`: `generate --profile` on such a "
            "bundle needs `--auth-token` and `--sv-tls-key` again."),

    # ---- CA trust ------------------------------------------------------
    Option(
        "ca_bundle", "string", "CA trust",
        summary="Inline PEM; the generator creates the ConfigMap holding it.",
        doc="Inline PEM -- the generator creates the ConfigMap. The simplest mode, "
            "and the one that goes stale: nothing rotates it for you. A large "
            "bundle can push the manifest past the 256KB cap on kubectl's "
            "last-applied-configuration annotation, so anything over 200KB "
            "applies `--server-side`. The PEM is linted at generate time: a "
            "server certificate, an expired CA or an intermediate without its "
            "root is warned, never refused. `--ca-bundle` also reads a DER "
            "`.cer` or a PKCS#7 `.p7b` and writes it as PEM."),
    Option(
        "ca_bundle_slot", "boolean", "CA trust",
        summary="The certificate is a file you supply; name it with ca_cert_file.",
        doc="The certificate is a **file**, named by `ca_cert_file`, and the "
            "bundle carries no PEM -- the convention BlazeMeter's own agent "
            "documentation and helm chart follow. The chart reads the file from "
            "the chart directory at install (`caBundle.file`), a manifests "
            "bundle's README leads with the `kubectl create configmap "
            "--from-file=<key>=<file>` line, and a docker bundle mounts the "
            "file beside its run script. Refused together with `ca_bundle`."),
    Option(
        "ca_cert_file", "string", "CA trust",
        default_note="unset -> <CA_CERT_FILE>",
        summary="The certificate's file name, used everywhere the file appears.",
        doc="The certificate's file name, and the only field the file mode asks "
            "for. It names the key inside the ConfigMap, the mounted file, the "
            "chart-directory file helm reads, and the `--from-file=` key. One "
            "certificate serves both of BlazeMeter's `request_ca_bundle` and "
            "`aws_ca_bundle`. Left blank it becomes `<CA_CERT_FILE>`, to be "
            "filled in once the file is known. With `ca_bundle` instead it "
            "defaults to `ca-bundle.crt`, since the bundle writes that file "
            "itself."),
    Option(
        "ca_existing_configmap", "string", "CA trust",
        summary="Reference a trust-bundle ConfigMap your platform team owns and rotates.",
        doc="Reference a platform-owned trust-bundle ConfigMap -- recommended, "
            "because they rotate it and an inline copy does not follow. The "
            "ConfigMap must already exist in the agent namespace, and the "
            "bundle's README prints the `create configmap` command for one that "
            "does not, keyed to match `ca_configmap_key`."),
    Option(
        "ca_configmap_key", "string", "CA trust",
        default_note="unset -> ca-bundle.crt",
        summary="Which key within ca_existing_configmap holds the bundle. Defaults to ca-bundle.crt.",
        doc="The bundle file key within `ca_existing_configmap`. Unset means "
            "`ca-bundle.crt`, the convention OpenShift and most cert-manager "
            "setups follow. Set it when yours differs: the engines' mount path "
            "is built from it, and a wrong key mounts an empty file rather than "
            "failing. That is why the README's create command writes "
            "`--from-file=<key>=<path>` rather than the bare `--from-file=<path>` "
            "BlazeMeter document, which keys the entry on the file's own name."),
    Option(
        "ca_openshift_inject", "boolean", "CA trust",
        summary="Emit a labeled empty ConfigMap; OpenShift injects and rotates the cluster trust bundle.",
        doc="OpenShift's `inject-trusted-cabundle` labeled ConfigMap -- the cluster "
            "injects the bundle and rotates it. The generator emits the empty labeled "
            "ConfigMap; the content arrives from the cluster operator, so on anything "
            "that is not OpenShift it stays empty and the agent trusts nothing extra."),

    # ---- Scheduling ----------------------------------------------------
    Option(
        "tolerations", "array", "Scheduling",
        summary="Kubernetes toleration list, applied to the crane pod and to every engine.",
        doc="A Kubernetes toleration list, applied to the crane pod **and** passed "
            "to the engines crane spawns: on a one-pool cluster, a taint that "
            "keeps crane off a pool keeps the engines off it too, and tolerating "
            "only one would schedule the agent and leave every test Pending. "
            "Set `engine_tolerations` to aim the engines at a different pool. "
            "JSON, e.g. "
            "`[{\"key\":\"lifecycle\",\"operator\":\"Equal\",\"value\":\"spot\",\"effect\":\"NoSchedule\"}]`."),
    Option(
        "node_selector", "object", "Scheduling",
        summary="Label selector pinning the crane pod and every engine to a node pool.",
        doc="A label map applied to the crane pod and passed to the engines, for "
            "the same reason as `tolerations`. JSON, e.g. `{\"pool\":\"loadtest\"}`. "
            "`doctor` measures capacity against the nodes that match it, so a "
            "selector matching nothing is reported as no capacity."),
    Option(
        "engine_node_selector", "object", "Scheduling",
        summary="Label selector for engines only, overriding node_selector -- the dedicated engine pool.",
        doc="A label map applied to the engines **only**, overriding "
            "`node_selector` for them. This is the two-pool shape: crane is one "
            "small always-on pod, engines are large pods that exist only during "
            "a run. Unset means engines follow crane; an explicit `{}` means "
            "engines take no selector even though crane has one. **A dedicated "
            "pool does not by itself give engines their configured size**: "
            "the scheduler and autoscaler work on requests, which the bundle "
            "sets equal to the limits unless the location's overrideCPU/"
            "overrideMemory replace them, and a pool without a `maxPods` "
            "ceiling can still pack engines onto one node. The generated "
            "`nodepools.md` carries the per-provider recipe."),
    Option(
        "engines_per_node", "integer", "Scheduling",
        default_note="unset -> 1",
        summary="How many engines a node of the engine pool should hold. Sizes the node pool recipe.",
        doc="How many engines one node of the engine pool is meant to hold. It "
            "reaches no manifest: it sizes the generated `nodepools.md` "
            "(`maxPods` and the machine type) and is what `doctor`'s "
            "engine-packing check judges against. Unset means 1: engines are "
            "measuring instruments, and two on one node contend for CPU, NIC and "
            "cache, which shows up as latency the load generator added. Raising "
            "it is cheaper -- every node spends about a CPU and 2Gi on system "
            "pods -- provided the node is sized for that many engines at their "
            "**limits**, which the recipe does. A platform floor can override "
            "it: GKE refuses `--max-pods-per-node` below 8, and the recipe sizes "
            "for the larger number."),
    Option(
        "engine_tolerations", "array", "Scheduling",
        summary="Toleration list for engines only, overriding tolerations -- lets the engine pool be tainted.",
        doc="A toleration list applied to the engines **only**, overriding "
            "`tolerations` for them. The companion to `engine_node_selector`: a "
            "taint keeps everything else off the engine pool, and this lets the "
            "engines past it. Unset means engines follow crane; an explicit "
            "`[]` means they tolerate nothing even though crane does."),

    # ---- Sizing --------------------------------------------------------
    Option(
        "engine_cpu_limit", "string", "Engine and agent sizing",
        default_note="BlazeMeter documents 2",
        summary="Engine CPU limit and request (KUBERNETES_RESOURCES_LIMITS_CPU / _DEFAULT_CPU).",
        doc="`KUBERNETES_RESOURCES_LIMITS_CPU` -- the CPU limit crane stamps on "
            "every pod it spawns -- and `KUBERNETES_RESOURCES_DEFAULT_CPU`, its "
            "request, set to the same value. Unset, it derives from the "
            "location's `overrideCPU`, else 2; always written, so engines never "
            "run without a limit or on crane's small default request. A "
            "location's `overrideCPU`, if set, replaces the request. Worth "
            "lowering on an emulated arm64 runtime, where a 2-CPU engine stays "
            "Pending."),
    Option(
        "engine_mem_limit", "string", "Engine and agent sizing",
        default_note="BlazeMeter documents 8Gi",
        summary="Engine memory limit and request (KUBERNETES_RESOURCES_LIMITS_MEMORY / _DEFAULT_MEM).",
        doc="`KUBERNETES_RESOURCES_LIMITS_MEMORY` -- the memory limit crane stamps "
            "on every pod it spawns -- and `KUBERNETES_RESOURCES_DEFAULT_MEM`, "
            "its request, the same amount in MiB. Unset, it derives from the "
            "location's `overrideMemory` (MB, read as Mi), else 8Gi. A "
            "location's `overrideMemory`, if set, replaces the request. "
            "`livetest --run-test` prints what an engine actually used as "
            "`ENGINE SIZING:`, which is the number to size from."),
    Option(
        "engine_ephemeral_request_mb", "integer", "Engine and agent sizing",
        summary="KUBERNETES_REQUESTS_EPHEMERAL_STORAGE in MB, per engine pod.",
        doc="`KUBERNETES_REQUESTS_EPHEMERAL_STORAGE`, in MB. Matters most on GKE "
            "Autopilot, which sizes the node's boot disk from what the pod requests "
            "and gives an engine that requests nothing too little room for the "
            "artifacts a run produces. BlazeMeter documents roughly 60GB of disk and "
            "40GB of `/tmp` per concurrent engine; requesting all of that on a "
            "shared cluster is usually wrong, so set it from what a real run used."),
    Option(
        "engine_ephemeral_limit_mb", "integer", "Engine and agent sizing",
        summary="KUBERNETES_LIMITS_EPHEMERAL_STORAGE in MB, per engine pod.",
        doc="`KUBERNETES_LIMITS_EPHEMERAL_STORAGE`, in MB. The ceiling, not the "
            "reservation -- a pod that exceeds an ephemeral-storage limit is evicted "
            "mid-run, which surfaces as a test that stops rather than as a resource "
            "error, so leave headroom over `engine_ephemeral_request_mb`."),
    # ---- Cluster checks ------------------------------------------------
    Option(
        "crane_hook", "boolean", "Cluster checks",
        summary="Add crane-hook: a one-shot Pod that checks the cluster before the agent runs.",
        doc="Adds [crane-hook](https://github.com/Blazemeter/crane-hook) to the "
            "bundle -- a one-shot Pod with its own read-only Role and "
            "RoleBinding that checks node capacity, egress to BlazeMeter and the "
            "registries, the RBAC the agent needs, and (for service "
            "virtualization) the ingress and its TLS secret. It exits 0 or 1; "
            "`kubectl logs cranehook` is the report, and you delete it when "
            "done. Under `--format helm` it becomes the chart's `helm test` "
            "hook, run by `helm test <release>`. With `private_registry` its "
            "image is added to the mirror script, since it is not in the "
            "location's image list."),
    # ---- Agent environment ---------------------------------------------
    Option(
        "extra_env", "object", "Agent environment",
        summary="Extra agent environment variables, as NAME: value. Refuses any name the bundle already writes.",
        doc="Agent environment variables with no option of their own -- "
            "`{\"PREFERRED_INTERFACE\": \"eth1\"}`. Carried by all three "
            "formats: ConfigMap entries for `manifests`, `extraEnv` in the "
            "values overlay for `helm`, `--env` flags for `docker`. They reach "
            "the **agent** only: crane builds the engines' environment from the "
            "`KUBERNETES_*` variables rather than passing its own down. Every "
            "name the generator writes itself is **refused**, naming the option "
            "that owns it, in every format -- set it there instead. The web UI "
            "lists BlazeMeter's documented variables that are left to set for "
            "this location; a variable not on that list is still accepted."),
    Option(
        "crane_ephemeral_storage", "string", "Engine and agent sizing",
        default_note=footprint.CRANE_EPHEMERAL_STORAGE,
        summary="Crane's own ephemeral-storage request and limit. One value sets both.",
        doc="Crane's own pod, e.g. `2Gi`. One value sets **both** the request and "
            "the limit: crane's disk use is its image plus logs, and a request "
            "below the limit on a cluster that sizes nodes from requests just "
            "moves the eviction somewhere harder to see. Unset uses "
            f"`{footprint.CRANE_EPHEMERAL_STORAGE}`."),
]

BY_NAME = {o.name: o for o in OPTIONS}

# Every summary lands in every MCP session's context, hence the cap.
SUMMARY_MAX_WORDS = 20

DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "options.md")
BEGIN = "<!-- BEGIN GENERATED OPTIONS TABLE -- python -m bzm_opl_gen.options -->"
END = "<!-- END GENERATED OPTIONS TABLE -->"


def _cell(text):
    """Prose into one markdown table cell, pipes escaped."""
    return re.sub(r"\s+", " ", text).strip().replace("|", "\\|")


def _default_cell(opt):
    value = opt.default
    if value is None:
        shown = "--"
    elif value is True:
        shown = "`true`"
    elif value is False:
        shown = "`false`"
    else:
        shown = f"`{value}`"
    return f"{shown} ({opt.default_note})" if opt.default_note else shown


def render_table():
    """The generated block of docs/options.md, between the markers."""
    out = [BEGIN, ""]
    for group, intro in GROUPS:
        members = [o for o in OPTIONS if o.group == group]
        if not members:
            continue
        out.append(f"### {group}")
        out.append("")
        if intro:
            out.append(_cell(intro))
            out.append("")
        out.append("| Option | Default | Meaning |")
        out.append("|---|---|---|")
        for o in members:
            out.append(f"| `{o.name}` | {_default_cell(o)} | {_cell(o.doc)} |")
        out.append("")
    out.append(END)
    return "\n".join(out)


def sync_doc(path=DOC_PATH):
    """Rewrite the generated block in place. Returns True if the file changed."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    start, stop = text.find(BEGIN), text.find(END)
    if start < 0 or stop < 0:
        raise SystemExit(f"{path}: generated-table markers not found")
    updated = text[:start] + render_table() + text[stop + len(END):]
    if updated == text:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return True


def main(argv=None):
    path = (argv or sys.argv[1:] or [DOC_PATH])[0]
    changed = sync_doc(path)
    print(f"{path}: {'rewritten' if changed else 'already up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
