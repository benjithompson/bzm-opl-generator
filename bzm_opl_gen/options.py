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
             JSON schema, where all thirty-one land in every session's context
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

    def json_schema(self):
        """The property entry for an MCP tool's input schema.

        `summary`, not `doc`, on purpose -- see the module docstring.
        """
        types = [self.type] + (["null"] if self.nullable else [])
        prop = {"type": types if len(types) > 1 else self.type,
                "description": self.summary}
        if self.choices:
            prop["enum"] = list(self.choices) + ([None] if self.nullable else [])
        return prop


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
     "for such a location `sv_ingress` is **required**; see "
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
        choices=["manifests", "helm"],
        summary="Emit flat YAML to kubectl apply, or the Helm chart plus a values overlay.",
        doc="`manifests` = flat YAML to `kubectl apply`; `helm` = the chart plus "
            "a values overlay -- see [Helm](helm.md). The same deployment "
            "expressed twice rather than two codebases, which `tests/helm_parity.py` "
            "is what holds it to. Refused for a service-virtualization location, "
            "whose ingress the chart does not carry."),
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
        summary="The ship's AUTH_TOKEN. Fetched from the API when an API key is given.",
        doc="The agent's `AUTH_TOKEN`, which is what identifies this deployment "
            "as that ship. Left as the placeholder unless `--api-key` fetches it "
            "or `--auth-token` supplies it, and it is the one option stripped from "
            "`out/profile.json`. **Fetching issues a new token and invalidates the "
            "previous one** -- for an agent already running, either re-apply the "
            "whole bundle including the Secret, or pass the existing token. A crane "
            "left holding a stale one logs `404` on `/ships/<id>/status` and sits at "
            "`0/1`, which reads like a deleted ship."),
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
        choices=list(gen.SV_INGRESS_TYPES),
        summary="Which ingress the mock services are published through. Required for a mockServices location.",
        doc="`nginx` | `istio` | `contour` | `openshift` -- **required** for a "
            "`mockServices` location; `openshift` needs `platform: openshift`; "
            "`contour` and `istio` are refused with `service_type: NODEPORT`. Each "
            "backend grants a different set of resources in crane's Role, so this "
            "picks the RBAC as well as the objects."),
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
            "to the engines crane spawns. Both, because a taint that keeps crane off "
            "a node pool keeps the engines off it too, and a bundle that tolerated "
            "one but not the other schedules the agent and then leaves every test "
            "Pending. JSON, e.g. "
            "`[{\"key\":\"lifecycle\",\"operator\":\"Equal\",\"value\":\"spot\",\"effect\":\"NoSchedule\"}]`."),
    Option(
        "node_selector", "object", "Scheduling",
        summary="Label selector pinning the crane pod and every engine to a node pool.",
        doc="A label map applied to the crane pod and passed to the engines, for "
            "the same reason as `tolerations`. JSON, e.g. `{\"pool\":\"loadtest\"}`. "
            "`doctor` measures capacity against the nodes that match it, so a "
            "selector matching nothing is reported as no capacity rather than as a "
            "typo."),

    # ---- Sizing --------------------------------------------------------
    Option(
        "engine_cpu_limit", "string", "Engine and agent sizing",
        default_note="BlazeMeter documents 2",
        summary="KUBERNETES_RESOURCES_LIMITS_CPU -- the CPU limit crane stamps on every engine.",
        doc="`KUBERNETES_RESOURCES_LIMITS_CPU` -- the CPU limit crane stamps on "
            "every engine it spawns. Unset leaves crane's own default, which "
            "BlazeMeter documents as 2. Worth lowering on an emulated arm64 runtime, "
            "where a 2-CPU engine stays Pending. This generator emits no LimitRange "
            "and will not: crane sets engine requests explicitly, so a "
            "`defaultRequest` never reaches them."),
    Option(
        "engine_mem_limit", "string", "Engine and agent sizing",
        default_note="BlazeMeter documents 8Gi",
        summary="KUBERNETES_RESOURCES_LIMITS_MEMORY -- the memory limit crane stamps on every engine.",
        doc="`KUBERNETES_RESOURCES_LIMITS_MEMORY` -- the memory limit crane stamps "
            "on every engine it spawns. Unset leaves crane's own default, documented "
            "as 8Gi. `livetest --run-test` prints what an engine actually used as "
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
# short enough that all thirty-one together stay a small fraction of an MCP
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
