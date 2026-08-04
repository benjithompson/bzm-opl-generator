"""What each generate option *means*, in one place.

`generate.DEFAULT_OPTIONS` says what an option defaults to; nothing said what it
was for except prose in `docs/options.md`, written by hand, and ten of the
thirty-one keys had no row there at all. Every new consumer wanting a
description -- the UI's help text, an MCP tool schema -- had to either restate
one or ship without it, and a restated description is one that drifts.

So the description lives here and the doc is generated from it. The registry is
deliberately *not* the source of the default value: `DEFAULT_OPTIONS` stays that,
and `tests/test_options.py` holds the two key sets equal in both directions, so
a new option added to either side fails the suite rather than arriving
undocumented.

Two description fields, because the consumers want different lengths and one
field would have to fail one of them:

  `summary`  one line, at most 20 words. This is what goes in an MCP tool's
             JSON schema, where all thirty-six land in every session's context
             whether or not the option is used, and in the UI's field help.
  `doc`      the full argued paragraph, cross-links and all. This is the
             `docs/options.md` cell, read one at a time by someone who has
             already decided to care about that option.

`docs/options.md` is regenerated with:

    python -m bzm_opl_gen.options

which rewrites only the block between the generated-table markers; the prose
around it is still written by hand. The test asserts that regenerating is a
no-op, so an edit to a table cell in the doc fails rather than being silently
overwritten later.
"""

import os
import re
import sys

from . import generate as gen


class Option:
    """One `generate` option: its shape, and what it is for.

    `default` is not stored -- it is read from `generate.DEFAULT_OPTIONS` at
    render time so there is exactly one copy of it.
    """

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
        return gen.DEFAULT_OPTIONS[self.name]

    @property
    def secret(self):
        """Whether the value is a credential -- read from `generate`, not
        declared here, so profile.json's exclusion list and this one cannot
        disagree about which options are safe to echo."""
        return self.name in gen.SECRET_OPTIONS

    @property
    def nullable(self):
        """A `None` default is this generator's "not asked", and every consumer
        needs to be able to send it back."""
        return gen.DEFAULT_OPTIONS[self.name] is None


# Section headings for the generated table, in the order they are emitted. The
# intro is prose the table cannot carry -- a constraint *between* options, which
# is exactly what the old single flat table had nowhere to say, and why the CA
# options shared one row with `|` between them.
GROUPS = [
    ("Platform and output", None),
    ("Credentials", None),
    ("Private registry", None),
    ("Agent lifecycle", None),
    ("Security and RBAC", None),
    ("Networking", None),
    ("Service virtualization",
     "Only meaningful for a location whose funcIds include `mockServices`, and "
     "for such a location `sv_ingress` is **required** -- either a backend, or "
     "`none` to generate it for performance testing alone; see "
     "[Service virtualization](service-virtualization.md)."),
    ("CA trust",
     "Pick **exactly one** of the three modes -- inline PEM, an existing "
     "ConfigMap, or OpenShift injection. More than one is refused rather than "
     "resolved. All three mount at `/var/cm` and propagate to engines via "
     "`KUBERNETES_CA_BUNDLE_MOUNT`."),
    ("Scheduling", None),
    ("Engine and agent sizing",
     "All unset by default: crane has its own defaults and this generator only "
     "overrides them when asked. `bzm-opl-gen doctor` checks whatever you set "
     "against real node capacity."),
    ("Cluster checks",
     "Objects that check the cluster rather than serve tests on it. They are "
     "not part of the agent: applying the bundle without them deploys exactly "
     "the same agent."),
    ("Agent environment",
     "The escape hatch. BlazeMeter's agent-environment reference is much wider "
     "than the options above, and this is how the rest is reached without "
     "hand-editing a generated file that the next `generate` overwrites."),
]


OPTIONS = [
    # ---- Platform and output -------------------------------------------
    Option(
        "platform", "string", "Platform and output",
        choices=["openshift", "k8s"],
        summary="Target platform: openshift leaves the UID to the SCC, k8s pins runAsUser.",
        doc="`openshift` = SCC-friendly (no `runAsUser`, engines inherit the "
            "SCC-assigned UID); `k8s` = pinned `runAsUser` "
            f"{gen.DEFAULT_OPTIONS['run_as_user']}. The difference is which side "
            "chooses the UID: OpenShift's SCC assigns one from the namespace's "
            "range and rejects a pod that pins its own, while plain Kubernetes "
            "assigns nothing and a restricted PodSecurity namespace then refuses "
            "the pod for running as root. So neither setting is a superset of the "
            "other, and the wrong one fails at admission rather than at generate "
            "time. It is a posture, not a product: the OpenShift default installs "
            "on vanilla Kubernetes too wherever the namespace assigns UIDs."),
    Option(
        "output_format", "string", "Platform and output",
        choices=["manifests", "helm", "docker"],
        summary="Flat YAML for kubectl, the Helm chart plus a values overlay, or a docker run script.",
        doc="`manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus "
            "a values overlay -- see [Helm](helm.md). The same deployment "
            "expressed twice rather than two codebases, which `tests/helm_parity.py` "
            "is what holds it to. `docker` is the other platform entirely: one "
            "agent as one container on a host with a docker daemon, emitted as a "
            "`docker run` script in BlazeMeter's own documented shape -- see "
            "[Docker](docker.md). Most options here are Kubernetes vocabulary and "
            "reach nothing in it, and its README names the ones this bundle set. "
            "All three refuse a service-virtualization location except "
            "`manifests`, whose ingress is the only one carried."),
    Option(
        "namespace", "string", "Platform and output",
        summary="Namespace every generated object is placed in, and the one crane's Role covers.",
        doc="The namespace every generated object carries, and the one crane's "
            "Role and RoleBinding are scoped to. Crane creates engine pods here, "
            "so it is also where the tests run. The bundle does **not** create "
            "the namespace -- `kubectl create namespace` first, or `helm install "
            "--create-namespace`. `doctor -n` overrides it for a check without "
            "re-generating."),

    # ---- Credentials ---------------------------------------------------
    Option(
        "auth_token", "string", "Credentials",
        summary="The ship's AUTH_TOKEN. Never minted unless you ask; --rotate-token is the ask.",
        doc="The agent's `AUTH_TOKEN`, which is what identifies this deployment "
            "as that ship. Resolved in four steps, and only the second one calls "
            "BlazeMeter: `--auth-token` wins outright; `--rotate-token` (with "
            "`--api-key`) issues a **new** one; otherwise the token already "
            "written into the output directory is reused, provided that bundle's "
            "`profile.json` names the same ship; otherwise the placeholder stays "
            "and the command says where a real token comes from. It is the one "
            "option stripped from `out/profile.json`, and it stays stripped -- a "
            "profile is a file people commit and hand over. **Minting invalidates "
            "the previous token**, and an agent left holding a stale one does not "
            "report an auth error: crane answers `404`, logs `Sleeping for 300` "
            "and never starts its health service, so the pod sits `0/1 Running` "
            "and reads as a slow boot. Re-apply the whole bundle, Secret "
            "included, after any rotation. Supplying the token is also the way "
            "past an account that refuses the fetch outright -- some allow the "
            "token endpoint only from BlazeMeter's own gateway, and the agent's "
            "install command in the BlazeMeter UI carries the same value."),
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
            "rewrites the crane image. Every image the location needs must already "
            "be mirrored under this prefix -- a key missing from `IMAGE_OVERRIDES` "
            "does not fail, it silently falls back to the public registry, which is "
            "the failure `livetest --local-registry` exists to make loud."),
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
            "entries. Commented, not set: these are credentials, and a generator "
            "that wrote them would put them in a file people paste into tickets. "
            "The lines are there so the shape is right and someone editing the "
            "bundle does not have to guess the variable names. `pull_secret` is the "
            "better answer for the crane image itself; this pair is what crane uses "
            "for the images *it* pulls."),

    # ---- Agent lifecycle -----------------------------------------------
    Option(
        "auto_update", "boolean", "Agent lifecycle",
        default_note="unset -> off",
        summary="Let crane rewrite its own Deployment when BlazeMeter ships a newer agent.",
        doc="`AUTO_KUBERNETES_UPDATE`: does crane rewrite its own Deployment when "
            "BlazeMeter ships a newer agent? **Off, which is a deliberate departure "
            "from BlazeMeter's own Kubernetes manifest** -- theirs ships `'true'`, "
            "and with it on crane takes field ownership of its Deployment within "
            "seconds of install, so the next `helm upgrade` fails on a conflict "
            "`--force-conflicts` cannot resolve and changing anything means "
            "uninstall + install ([Helm](helm.md#managing-the-release-with-helm)). "
            "The cost of the default is that keeping the agent current is your job "
            "-- re-generate and re-apply -- and one far enough behind loses support. "
            "`--auto-update` hands that back to crane on those terms. (BlazeMeter's "
            "`AUTO_UPDATE` is the Docker-side switch and does nothing on a "
            "Kubernetes agent, so nothing here emits it.)"),

    # ---- Security and RBAC ---------------------------------------------
    Option(
        "service_account_name", "string", "Security and RBAC",
        summary="The account crane runs as and the RoleBinding grants to. Required, never empty.",
        doc="The account the agent runs as, and the one the RoleBinding (and "
            "ClusterRoleBinding) grants to. Used whether or not the bundle creates "
            "it, and **required** -- an empty one is refused rather than resolved to "
            "the namespace's `default` account, which would bind crane's Role to "
            "every pod in the namespace. See [the service account](#the-service-account)."),
    Option(
        "service_account_create", "boolean", "Security and RBAC",
        summary="Emit the ServiceAccount object; off assumes your platform team already owns it.",
        doc="Emit the ServiceAccount object. `--no-create-service-account` leaves "
            "it out for an account your platform team already owns; everything still "
            "references `service_account_name`, so it must exist before you apply. If "
            "it does not, nothing fails at apply time -- the Deployment is accepted "
            "and no pod is ever created. `doctor` checks for it, and `livetest` "
            "refuses a profile with this off, because the rig creates its own "
            "namespace and would wait out its whole timeout."),
    Option(
        "cluster_rbac", "boolean", "Security and RBAC",
        summary="Include the optional read-only nodes ClusterRole and binding.",
        doc="Include the optional read-only nodes ClusterRole/Binding. Not required "
            "for performance tests -- it lets crane read node capacity to place "
            "engines, which is a nicety, and cluster-scoped RBAC is the thing a "
            "platform team is most likely to refuse. Left off, the rest of the "
            "bundle is entirely namespace-scoped."),
    Option(
        "run_as_user", "integer", "Security and RBAC",
        summary="UID for the crane pod on platform k8s. Ignored on OpenShift, where the SCC assigns one.",
        doc="The UID crane's pod runs as, on `platform: k8s` only. On OpenShift "
            "the SCC assigns a UID from the namespace's range and a pinned one is "
            "rejected at admission, so nothing is emitted there. 1337 is arbitrary "
            "beyond being non-root, which is what restricted PodSecurity requires. "
            "With `restrict_engines` on, this is also the UID:GID the engines "
            "inherit."),
    Option(
        "restrict_engines", "boolean", "Security and RBAC",
        summary="Engines crane spawns drop all capabilities and inherit crane's UID:GID.",
        doc="Engines crane spawns drop all capabilities and inherit crane's UID:GID "
            "(`INHERIT_RUNNING_USER_AND_GROUP`, cap-drop JSON). Crane's own default "
            "is a privileged engine pod, which restricted PodSecurity, OpenShift SCC "
            "and GKE Autopilot all reject -- after the agent is online, so the run "
            "hangs at `BOOT_STARTING`. `--no-restrict-engines` only for an image that "
            "needs a capability -- and it removes the posture from every container "
            "crane creates, so see which images have run under it in "
            "[Hardened engines](hardened-engines.md) first."),

    # ---- Networking ----------------------------------------------------
    Option(
        "service_type", "string", "Networking",
        choices=["CLUSTERIP", "NODEPORT"],
        summary="How crane publishes the Services it owns. NODEPORT is BlazeMeter's default, often disallowed.",
        doc="`KUBERNETES_SERVICE_USE_TYPE`. NODEPORT is the BlazeMeter default but "
            "often disallowed. With `sv_ingress`, only `nginx` and `openshift` "
            "publish over NODEPORT -- [the other two are "
            "refused](service-virtualization.md#service_type-and-the-backend-you-chose). "
            "Changing it later does not restyle the Services crane already pooled, so "
            "`kubectl get svc` will not report what is configured."),
    Option(
        "proxy", "object", "Networking",
        summary="HTTP(S)_PROXY / NO_PROXY for the agent, with optional credentials.",
        doc="`HTTP(S)_PROXY` / `NO_PROXY`; optional `username`/`password` are "
            "URL-encoded into the proxy URL (BlazeMeter has no separate proxy-auth "
            "envs) and the credentialed URLs live in the Secret when `use_secret` is "
            "on. Keys: `http`, `https`, `no_proxy`, `username`, `password`. Note that "
            "**JMeter ignores these for sampler traffic** -- the proxy an engine uses "
            "to reach the system under test has to be set in the test itself."),

    # ---- Service virtualization ----------------------------------------
    Option(
        "sv_ingress", "string", "Service virtualization",
        choices=list(gen.SV_INGRESS_TYPES) + [gen.SV_INGRESS_NONE],
        summary="Which ingress the mock services are published through, or `none` for performance only.",
        doc="`nginx` | `istio` | `contour` | `openshift` -- **required** for a "
            "`mockServices` location; `openshift` needs `platform: openshift`; "
            "`contour` and `istio` are refused with `service_type: NODEPORT`. Each "
            "backend grants a different set of resources in crane's Role, so this "
            "picks the RBAC as well as the objects. `none` is the third state and "
            "means *performance only*: a location carrying `mockServices` "
            "generates without any of the above, and virtual services deployed to "
            "it stall at `WAITING_FOR_DOMAIN`. Unset is not that -- it is nobody "
            "having answered, which is what such a location is refused for."),
    Option(
        "sv_subdomain", "string", "Service virtualization",
        summary="Wildcard domain your ingress controller serves; the endpoint host suffix.",
        doc="Wildcard domain your ingress controller serves; required with "
            "`sv_ingress`. Every virtual service gets a host under it, and the "
            "endpoint BlazeMeter advertises is built from it -- so it has to resolve "
            "from wherever the tests run, not just inside the cluster."),
    Option(
        "sv_tls_secret", "string", "Service virtualization",
        summary="Wildcard TLS secret in the agent namespace. Required with sv_ingress, even for HTTP.",
        doc="Wildcard TLS secret in the agent namespace; required with "
            "`sv_ingress`, **even for HTTP** -- crane names it unconditionally, and "
            "an ingress referencing a Secret that is not there is accepted and then "
            "never serves."),
    Option(
        "sv_istio_gateway", "string", "Service virtualization",
        summary="Existing istio Gateway to attach to; unset means crane creates one per virtual service.",
        doc="istio only, optional; unset means crane creates a Gateway per virtual "
            "service. Rejected with any other `sv_ingress`, since only crane's istio "
            "backend reads it. A Gateway whose selector matches no pod fails exactly "
            "like a wrong port would -- crane hardcodes `istio: ingressgateway`."),

    # ---- CA trust ------------------------------------------------------
    Option(
        "ca_bundle", "string", "CA trust",
        summary="Inline PEM; the generator creates the ConfigMap holding it.",
        doc="Inline PEM -- the generator creates the ConfigMap. The simplest mode "
            "and the one that goes stale: nothing rotates it for you. Bundles are "
            "large enough that the manifest crosses the 256KB cap on kubectl's "
            "last-applied-configuration annotation, which is why anything over 200KB "
            "applies `--server-side`."),
    Option(
        "ca_existing_configmap", "string", "CA trust",
        summary="Reference a trust-bundle ConfigMap your platform team owns and rotates.",
        doc="Reference a platform-owned trust-bundle ConfigMap -- recommended, "
            "because they rotate it and an inline copy does not follow. The "
            "ConfigMap must already exist in the agent namespace."),
    Option(
        "ca_configmap_key", "string", "CA trust",
        default_note="unset -> ca-bundle.crt",
        summary="Which key within ca_existing_configmap holds the bundle. Defaults to ca-bundle.crt.",
        doc="The bundle file key within `ca_existing_configmap`. Unset means "
            "`ca-bundle.crt`, which is the convention both OpenShift and most "
            "cert-manager setups follow. Set it when yours does not -- the mount "
            "path engines are given is built from it, so a wrong key mounts an "
            "empty file rather than failing."),
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
            "to the engines crane spawns. Both by default, because on a one-pool "
            "cluster a taint that keeps crane off a node pool keeps the engines off "
            "it too, and a bundle that tolerated one but not the other schedules the "
            "agent and then leaves every test Pending. Set `engine_tolerations` to "
            "aim the engines at a different pool. JSON, e.g. "
            "`[{\"key\":\"lifecycle\",\"operator\":\"Equal\",\"value\":\"spot\",\"effect\":\"NoSchedule\"}]`."),
    Option(
        "node_selector", "object", "Scheduling",
        summary="Label selector pinning the crane pod and every engine to a node pool.",
        doc="A label map applied to the crane pod and passed to the engines, for "
            "the same reason as `tolerations`. JSON, e.g. `{\"pool\":\"loadtest\"}`. "
            "`doctor` measures capacity against the nodes that match it, so a "
            "selector matching nothing is reported as no capacity rather than as a "
            "typo."),
    Option(
        "engine_node_selector", "object", "Scheduling",
        summary="Label selector for engines only, overriding node_selector -- the dedicated engine pool.",
        doc="A label map applied to the engines **only**, overriding "
            "`node_selector` for them and leaving it to place the crane pod. This "
            "is the two-pool shape: crane is one small always-on pod, an engine is "
            "1-n large pods that exist only during a run, and a pool that suits "
            "one suits the other badly. Unset means engines follow crane, which is "
            "what every bundle did before this option. An explicit `{}` is "
            "different from unset and is worth having: it says engines take no "
            "selector even though crane has one, for a crane pinned to a tainted "
            "infra pool with engines free to land anywhere. **The dedicated pool "
            "does not by itself give engines the size they are configured for** -- "
            "engine *requests* come from the location (overrideCPU/overrideMemory) "
            "and default to 250m/256Mi when it sets neither, "
            "and both the scheduler and the cluster autoscaler work on requests, "
            "so a pool without a `maxPods` ceiling packs many engines onto one "
            "node. The generated `nodepools.md` carries the per-flavour recipe."),
    Option(
        "engines_per_node", "integer", "Scheduling",
        default_note="unset -> 1",
        summary="How many engines a node of the engine pool should hold. Sizes the node pool recipe.",
        doc="How many engines one node of the engine pool is meant to hold. It "
            "reaches no manifest -- it sizes the generated `nodepools.md` "
            "(`maxPods` and the machine type together) and is what `doctor`'s "
            "engine-packing check judges against. Unset means 1, the "
            "conservative answer: engines are measuring instruments, and two "
            "sharing a node contend for CPU, NIC and cache in ways that surface "
            "as latency the load generator invented rather than latency the "
            "system produced. Raising it is legitimate and cheaper -- every node "
            "spends about a CPU and 2Gi on system pods before an engine arrives, "
            "so one large node beats several small ones -- provided the node is "
            "sized for that many engines at their **limits**, which the recipe "
            "does for you. Note that a platform floor can override it: GKE "
            "refuses `--max-pods-per-node` below 8, which after ~6 system pods "
            "leaves room for 2 engines whatever this says, and the recipe sizes "
            "the node for the larger number rather than pretending otherwise."),
    Option(
        "engine_tolerations", "array", "Scheduling",
        summary="Toleration list for engines only, overriding tolerations -- lets the engine pool be tainted.",
        doc="A toleration list applied to the engines **only**, overriding "
            "`tolerations` for them. The companion to `engine_node_selector`: a "
            "taint on the engine pool is what keeps everything else in the cluster "
            "off nodes that exist to be empty between runs, and this is what lets "
            "the engines past it. Unset means engines follow crane; an explicit "
            "`[]` means they tolerate nothing even though crane does."),

    # ---- Sizing --------------------------------------------------------
    Option(
        "engine_cpu_limit", "string", "Engine and agent sizing",
        default_note="BlazeMeter documents 2",
        summary="KUBERNETES_RESOURCES_LIMITS_CPU -- the CPU limit crane stamps on every engine.",
        doc="`KUBERNETES_RESOURCES_LIMITS_CPU` -- the CPU limit crane stamps on "
            "every engine it spawns. Unset, it derives from the location's "
            "`overrideCPU` (the engine's *request*, so the two halves of one "
            "figure agree by construction), else BlazeMeter's documented default "
            "of 2 -- the env is always carried, because doctor certifies that "
            "figure and a ConfigMap without it ran engines with no limits at "
            "all. Worth lowering on an emulated arm64 runtime, where a 2-CPU "
            "engine stays Pending. This generator emits no LimitRange "
            "and will not: crane sets engine requests explicitly, so a "
            "`defaultRequest` never reaches them."),
    Option(
        "engine_mem_limit", "string", "Engine and agent sizing",
        default_note="BlazeMeter documents 8Gi",
        summary="KUBERNETES_RESOURCES_LIMITS_MEMORY -- the memory limit crane stamps on every engine.",
        doc="`KUBERNETES_RESOURCES_LIMITS_MEMORY` -- the memory limit crane stamps "
            "on every engine it spawns. Unset, it derives from the location's "
            "`overrideMemory` (MB, read as Mi), else the documented default of "
            "8Gi -- always carried, for the same reason as the CPU limit. "
            "`livetest --run-test` prints what an engine actually used as "
            "`ENGINE SIZING:`, which is the number to size from."),
    Option(
        "engine_ephemeral_request_mb", "integer", "Engine and agent sizing",
        summary="KUBERNETES_REQUESTS_EPHEMERAL_STORAGE in MB, per engine pod.",
        doc="`KUBERNETES_REQUESTS_EPHEMERAL_STORAGE`, in MB. Matters most on GKE "
            "Autopilot, which sizes the node's boot disk from what the pod requests "
            "and gives an engine that requests nothing a share too small for the "
            "artifacts a run produces. BlazeMeter documents roughly 60GB of disk and "
            "40GB of `/tmp` per concurrent engine; requesting the whole of that on a "
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
            "bundle -- a one-shot Pod, plus its own read-only Role and RoleBinding, "
            "that checks node capacity, egress to BlazeMeter and the registries, the "
            "RBAC the agent needs, and (for service virtualization) the ingress and "
            "its TLS secret. It exits 0 or 1 and stops; `kubectl logs cranehook` is "
            "the report, and it is yours to delete when you have read it. Off by "
            "default because it is a check rather than part of the agent. Under "
            "`--format helm` it becomes the chart's `helm test` hook, so `helm test "
            "<release>` runs it and nothing runs at install time. With "
            "`private_registry` its image is added to the mirror script -- it is not "
            "in the location's inventory, so an air-gapped bundle would otherwise "
            "carry the one object that cannot pull."),
    # ---- Agent environment ---------------------------------------------
    Option(
        "extra_env", "object", "Agent environment",
        summary="Extra agent environment variables, as NAME: value. Refuses any name the bundle already writes.",
        doc="Agent environment variables this generator has no option of its "
            "own for -- `{\"PREFERRED_INTERFACE\": \"eth1\"}`. BlazeMeter's "
            "agent-environment reference is far wider than the options above, "
            "and the alternative was editing the generated ConfigMap by hand, "
            "which the next `generate` silently reverts. Carried by all three "
            "formats: ConfigMap entries for `manifests`, `extraEnv` in the "
            "values overlay for `helm`, `--env` flags in the `docker` script. "
            "It reaches the **agent**: crane's pod reads it, and the engines "
            "crane spawns do not, because crane builds their environment from "
            "the `KUBERNETES_*` variables rather than passing its own down. "
            "Every name the generator writes for itself is **refused**, "
            "naming the option that owns it -- two values for one key is a "
            "duplicate ConfigMap entry, and which one wins is not the one the "
            "form that set it shows. The refused set is the union across "
            "formats, so a Kubernetes variable is refused in a docker bundle "
            "too: it reaches nothing there either, and accepting it would read "
            "as a setting that had been made. What is left to set is served as "
            "`/api/agent-env` -- BlazeMeter's documented reference minus every "
            "name an option above writes -- so the web UI offers the variables "
            "as a list with a control per type rather than asking for a name "
            "typed from memory."),
    Option(
        "crane_ephemeral_storage", "string", "Engine and agent sizing",
        default_note=gen.CRANE_EPHEMERAL_STORAGE,
        summary="Crane's own ephemeral-storage request and limit. One value sets both.",
        doc="Crane's own pod, e.g. `2Gi`. One value sets **both** the request and "
            "the limit, deliberately: crane's disk use is its image plus logs, and a "
            "request below the limit on a cluster that sizes nodes from requests just "
            "moves the eviction somewhere harder to see. Unset uses "
            f"`{gen.CRANE_EPHEMERAL_STORAGE}`."),
]

BY_NAME = {o.name: o for o in OPTIONS}

# The words the summary limit is enforced at. Long enough for a real sentence,
# short enough that all thirty-six together stay a small fraction of an MCP
# session's context -- which is the only reason the limit exists.
SUMMARY_MAX_WORDS = 20

DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "options.md")
BEGIN = "<!-- BEGIN GENERATED OPTIONS TABLE -- python -m bzm_opl_gen.options -->"
END = "<!-- END GENERATED OPTIONS TABLE -->"


def _cell(text):
    """Prose into one markdown table cell.

    Pipes are escaped rather than forbidden -- several options enumerate their
    choices with `a | b | c`, which reads better than the alternatives -- and
    the registry writes ordinary text without thinking about the table.
    """
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
