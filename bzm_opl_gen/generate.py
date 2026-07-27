"""Render deployable manifests from templates + facts + customer options."""

import collections
import json
import os
from string import Template
from urllib.parse import quote

from .facts import select_images
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

DEFAULT_OPTIONS = {
    "platform": "openshift",        # openshift | k8s
    # manifests -> flat YAML to kubectl apply. helm -> the chart in
    # templates/helm, plus a values overlay built from these same options -- the
    # same deployment expressed twice, not two codebases. tests/helm_parity.py
    # is what holds the two to that.
    "output_format": "manifests",   # manifests | helm
    "namespace": "blazemeter",
    "use_secret": True,              # False -> AUTH_TOKEN in ConfigMap (simplified)
    "auth_token": "<YOUR_AUTH_TOKEN>",
    "private_registry": None,        # e.g. registry.example.com/blazemeter
    "pull_secret": None,             # name of docker-registry secret for crane image
    "registry_auth": False,          # emit commented DOCKER_REGISTRY_USERNAME/PASSWORD
    "cluster_rbac": False,           # include optional ClusterRole/Binding files
    "service_type": "CLUSTERIP",    # CLUSTERIP | NODEPORT
    # Service virtualization ingress. Only meaningful for a location whose
    # funcIds include mockServices; see _sv_cfg for why all three are required
    # together and why NODEPORT is rejected alongside them.
    "sv_ingress": None,              # None, or one of SV_INGRESS_TYPES
    "sv_subdomain": None,            # e.g. apps.example.com -- endpoint host suffix
    "sv_tls_secret": None,           # wildcard TLS secret, in the agent namespace
    "sv_istio_gateway": None,        # optional; unset -> a Gateway per virtual service
    # {"http", "https", "no_proxy", "username", "password"} -- credentials are
    # embedded in the proxy URL (user:pass@host, per BlazeMeter docs) and the
    # URL moves into the Secret when use_secret is on.
    "proxy": None,
    "run_as_user": 1337,             # k8s platform only (openshift: SCC assigns)
    # Real-cluster scheduling / trust / sizing (all optional):
    "tolerations": None,             # k8s toleration list -> crane pod + engines
    "node_selector": None,           # {"label": "value"} -> crane pod + engines
    # CA trust -- pick ONE mode:
    "ca_bundle": None,               # inline PEM -> generator creates the ConfigMap
    "ca_existing_configmap": None,   # name of a ConfigMap the platform team owns/rotates
    "ca_configmap_key": None,        # bundle file key within it (default ca-bundle.crt)
    "ca_openshift_inject": False,    # labeled empty CM; OpenShift injects cluster trust
    "engine_cpu_limit": None,        # e.g. "2" -> KUBERNETES_RESOURCES_LIMITS_CPU
    "engine_mem_limit": None,        # e.g. "8Gi" -> KUBERNETES_RESOURCES_LIMITS_MEMORY
    "engine_ephemeral_request_mb": None,  # int MB -> KUBERNETES_REQUESTS_EPHEMERAL_STORAGE
    "engine_ephemeral_limit_mb": None,    # int MB -> KUBERNETES_LIMITS_EPHEMERAL_STORAGE
    # A namespace LimitRange: caps what any pod in the namespace may ask for,
    # and supplies requests/limits to the ones that declare none. It does NOT
    # fix the taurus engine -- crane sets that pod's requests explicitly, and
    # defaultRequest only fills fields a pod leaves unset (verified on a live
    # run: the engine pod comes back with no limit-ranger annotation). Opt-in,
    # because it applies to every pod in the namespace.
    "emit_limitrange": False,
    "engine_cpu_request": None,      # defaults to engine_cpu_limit
    "engine_mem_request": None,      # defaults to engine_mem_limit
}

# BlazeMeter's documented engine footprint -- the fallback when the customer
# has not pinned engine limits.
ENGINE_DEFAULT_CPU = "2"
ENGINE_DEFAULT_MEM = "8Gi"
# ...and on disk, per concurrent engine (the docs quote decimal GB).
ENGINE_DISK_GB = 60
ENGINE_TMP_GB = 40

# Crane's own container resources, substituted into templates/deployment.yaml so
# these are the single source. The limits matter beyond that pod: crane shares
# the namespace, so a LimitRange max below them would get the crane pod itself
# rejected by the LimitRanger, and doctor spends them out of node capacity.
# Values are the official helm-crane chart's resourcesCrane.
CRANE_CPU_REQUEST = "250m"
CRANE_MEM_REQUEST = "512Mi"
CRANE_CPU_LIMIT = "1"
CRANE_MEM_LIMIT = "2Gi"

# What crane stamps on the engine pods it spawns, explicitly. A LimitRange
# cannot override it -- defaultRequest only fills fields a pod leaves unset --
# so this is what the scheduler packs engines by, whatever their limits say.
ENGINE_STAMPED_REQUEST_CPU = "250m"
ENGINE_STAMPED_REQUEST_MEM = "256Mi"

LIMITRANGE_FILE = "bzm_limitrange.yaml"


def _quantity(o, key, default, parse):
    """Parse an engine quantity option, naming the option in the error --
    quantity's own message only carries the bad value. `default` is returned
    as-is when the option is unset, so a caller with an already-parsed fallback
    does not have to format it back into a string for re-parsing."""
    value = o.get(key)
    if not value:
        return parse(default) if isinstance(default, str) else default
    try:
        return parse(value)
    except ValueError as e:
        raise ValueError(f"{key}: {e}") from None


def engine_size(o):
    """(cpu_millicores, mem_bytes) one engine actually claims. doctor.py imports
    this to compare the claim against what a node can hold."""
    return (_quantity(o, "engine_cpu_limit", ENGINE_DEFAULT_CPU, parse_cpu),
            _quantity(o, "engine_mem_limit", ENGINE_DEFAULT_MEM, parse_memory))


def engine_requests(o):
    """(cpu_millicores, mem_bytes) engines should *request* -- their limits
    unless the customer deliberately overcommits."""
    cpu_limit, mem_limit = engine_size(o)
    cpu = _quantity(o, "engine_cpu_request", cpu_limit, parse_cpu)
    mem = _quantity(o, "engine_mem_request", mem_limit, parse_memory)
    # k8s rejects a pod whose request exceeds its limit outright.
    if cpu > cpu_limit:
        raise ValueError(f"engine_cpu_request ({o['engine_cpu_request']}) exceeds "
                         f"engine_cpu_limit ({format_cpu(cpu_limit)})")
    if mem > mem_limit:
        raise ValueError(f"engine_mem_request ({o['engine_mem_request']}) exceeds "
                         f"engine_mem_limit ({format_memory(mem_limit)})")
    return cpu, mem


def _limitrange_max(o):
    """The namespace ceiling: the engine size, but never below crane's own
    limits -- crane shares the namespace, and a max under them would have the
    LimitRanger reject the crane pod itself."""
    cpu_limit, mem_limit = engine_size(o)
    return (max(cpu_limit, parse_cpu(CRANE_CPU_LIMIT)),
            max(mem_limit, parse_memory(CRANE_MEM_LIMIT)))


def _limitrange_sub(o):
    """Substitutions for templates/limitrange.yaml."""
    cpu_limit, mem_limit = engine_size(o)
    cpu_req, mem_req = engine_requests(o)
    max_cpu, max_mem = _limitrange_max(o)
    return {"NAMESPACE": o["namespace"],
            "ENGINE_CPU_REQUEST": format_cpu(cpu_req),
            "ENGINE_MEM_REQUEST": format_memory(mem_req),
            "ENGINE_CPU_LIMIT": format_cpu(cpu_limit),
            "ENGINE_MEM_LIMIT": format_memory(mem_limit),
            "LIMITRANGE_MAX_CPU": format_cpu(max_cpu),
            "LIMITRANGE_MAX_MEM": format_memory(max_mem)}


CA_MOUNT_PATH = "/var/cm"
CA_FILENAME = "ca-bundle.crt"
CA_CONFIGMAP = "blazemeter-cacerts"

# The funcId BlazeMeter puts on a location that serves virtual services, and so
# the one that makes the ingress options mandatory. A tuple, not a string,
# because it is served to the UI as a set and was two entries until sv-bridge
# was retired -- a second one returning is a data change, not a code change.
SV_FUNC_IDS = ("mockServices",)


class SvBackend(collections.namedtuple(
        "SvBackend", "group resources creates via_ingress_class")):
    """One crane web-expose backend: the object it publishes, and so the single
    API group the Role has to grant.

    Crane selects one implementation from KUBERNETES_WEB_EXPOSE_TYPE and never
    touches the others, so granting any other group is permission that can only
    go unused. Verified live for istio, contour and openshift: with a Role
    carrying only its own group, crane still published its object and the
    virtual service served. nginx keeps `ingresses` because that is the group it
    writes.

    `via_ingress_class` records whether the published object is claimed by an
    IngressClass, which is what doctor preflights -- none of Istio 1.30, Contour
    v1.33 or the OpenShift router registers an IngressClass at all.
    """
    __slots__ = ()


SV_INGRESS_BACKENDS = {
    "nginx": SvBackend("networking.k8s.io", ["ingresses"], "Ingress", True),
    "istio": SvBackend("networking.istio.io", ["gateways", "virtualservices"],
                       "Gateway + VirtualService", False),
    "contour": SvBackend("projectcontour.io", ["httpproxies"],
                         "HTTPProxy", False),
    # routes/custom-host is not padding: OpenShift gates spec.host behind its
    # own create, and crane sets spec.host. Without it the create comes back 422
    # "you do not have permission to set the host field of the route", no Route
    # appears, and the virtual service stalls with the mock pod healthy at 1/1.
    # Proven by A/B on a live cluster -- and note `auth can-i create
    # routes/custom-host` answers yes either way, so it cannot be used to check.
    "openshift": SvBackend("route.openshift.io",
                           ["routes", "routes/custom-host"], "Route", False),
}
# Derived, not a second list to keep in step. Crane's binary names five
# implementations -- kubernetes_{base,contour,istio,nginx,openshift}_web_expose
# _service -- and all four real ones are backends above; `base` is their shared
# parent, not a value. The `INGRESS` value BlazeMeter's env reference documents
# is deliberately absent: it creates no object at all and stalls at
# WAITING_FOR_DOMAIN.
SV_INGRESS_TYPES = tuple(SV_INGRESS_BACKENDS)


def _sv_cfg(facts, o):
    """Resolve the service-virtualization ingress options, or None.

    Each rejection below carries its own reason, because every one of these
    combinations fails *silently* on a cluster: the manifests apply, the agent
    reports idle, the mock pod sits at 1/1, and the virtual service never
    becomes reachable. Refusing to generate is the only signal that arrives
    before someone has spent an afternoon on it, so the errors name the fix
    rather than restating the rule.
    """
    ingress = o["sv_ingress"]
    sv_funcs = [f for f in (facts.get("func_ids") or []) if f in SV_FUNC_IDS]
    if not ingress:
        if sv_funcs:
            raise ValueError(
                f"location advertises funcId(s) {', '.join(sv_funcs)} but no "
                "service-virtualization ingress was configured. Pass sv_ingress "
                f"({'|'.join(SV_INGRESS_TYPES)}) + sv_subdomain + sv_tls_secret, "
                "or virtual services will deploy and stall at WAITING_FOR_DOMAIN."
            )
        return None
    if ingress not in SV_INGRESS_TYPES:
        raise ValueError(f"sv_ingress must be one of {SV_INGRESS_TYPES}, got {ingress!r}")
    missing = [n for n, v in (("sv_subdomain", o["sv_subdomain"]),
                              ("sv_tls_secret", o["sv_tls_secret"])) if not v]
    if missing:
        raise ValueError(
            f"sv_ingress={ingress} also requires {' and '.join(missing)}. "
            "The TLS secret is mandatory even for HTTP virtual services -- crane "
            "refuses to start without it."
        )
    if o["service_type"] != "CLUSTERIP":
        raise ValueError(
            f"sv_ingress={ingress} requires service_type=CLUSTERIP, got "
            f"{o['service_type']}. NODEPORT makes crane read the cluster-scoped "
            "Node object to build an address, which a namespaced Role cannot grant."
        )
    if ingress == "openshift" and o["platform"] != "openshift":
        raise ValueError(
            f"sv_ingress=openshift requires platform=openshift, got "
            f"{o['platform']}. That backend publishes a route.openshift.io "
            "Route, which a plain Kubernetes API server does not serve -- the "
            "agent would deploy cleanly and then stall with nothing to create."
        )
    if o["sv_istio_gateway"] and ingress != "istio":
        raise ValueError(
            f"sv_istio_gateway is only meaningful with sv_ingress=istio, not "
            f"{ingress}. Crane reads KUBERNETES_ISTIO_GATEWAY_NAME in the istio "
            "backend alone, so setting it here would silently do nothing."
        )
    return {"type": ingress, "subdomain": o["sv_subdomain"],
            "tls_secret": o["sv_tls_secret"],
            "istio_gateway": o["sv_istio_gateway"]}


def _ca_cfg(o):
    """Resolve the CA-trust mode to {cm, key, mode} or None.

    Ownership model:
      existing -- the platform/security team owns and rotates a trust-bundle
                  ConfigMap in the namespace (e.g. written by trust-manager);
                  we only reference it.
      inline   -- the app team owns the PEM; the generator creates the ConfigMap.
      inject   -- OpenShift-only: we emit an empty ConfigMap labeled
                  config.openshift.io/inject-trusted-cabundle=true and the
                  cluster network operator injects ca-bundle.crt into it.
    """
    modes = [("inline", bool(o["ca_bundle"])),
             ("existing", bool(o["ca_existing_configmap"])),
             ("inject", bool(o["ca_openshift_inject"]))]
    active = [m for m, on in modes if on]
    if len(active) > 1:
        raise ValueError("choose one CA mode: ca_bundle (inline PEM) | "
                         "ca_existing_configmap | ca_openshift_inject")
    if not active:
        return None
    mode = active[0]
    if mode == "existing":
        return {"cm": o["ca_existing_configmap"],
                "key": o["ca_configmap_key"] or CA_FILENAME, "mode": mode}
    # inline + inject both use our own ConfigMap; inject's key is fixed to
    # ca-bundle.crt (the key OpenShift writes into labeled ConfigMaps).
    return {"cm": CA_CONFIGMAP, "key": CA_FILENAME, "mode": mode}


def proxy_url(url, p):
    """Embed credentials in the proxy URL (http://user:pass@host:port) --
    BlazeMeter has no separate proxy-auth env vars; the URL carries them."""
    user = p.get("username")
    if not url or not user:
        return url
    userinfo = quote(user, safe="")
    if p.get("password"):
        userinfo += ":" + quote(p["password"], safe="")
    scheme, sep, rest = url.partition("://")
    return f"{scheme}{sep}{userinfo}@{rest}" if sep else f"{userinfo}@{url}"


DEFAULT_NO_PROXY = "kubernetes.default,127.0.0.1,localhost"


def proxy_env(o):
    """The proxy environment a pod needs, as {NAME: value}, credentials already
    embedded in the URLs. One builder for the three places that need it: the
    ConfigMap, the Secret, and doctor's probe pod."""
    p = o.get("proxy") or {}
    env = {}
    for name, key in (("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https")):
        if p.get(key):
            env[name] = proxy_url(p[key], p)
    if p:
        env["NO_PROXY"] = p.get("no_proxy", DEFAULT_NO_PROXY)
    return env


def _proxy_has_creds(o):
    return bool(o["proxy"] and o["proxy"].get("username"))


def _tpl(name):
    with open(os.path.join(TEMPLATE_DIR, name)) as f:
        return Template(f.read())


def _image_overrides(facts, registry):
    """Build crane IMAGE_OVERRIDES JSON from account facts. Which images are
    included follows the location's enabled funcIds (facts.select_images)."""
    entries = {}
    for img in select_images(facts):
        name = img["repo"].rsplit("/", 1)[-1]
        entries[img["key"]] = f"{registry.rstrip('/')}/{name}:{img['tag']}"
    return entries


def _configmap(facts, o):
    lines = [
        "kind: ConfigMap",
        "apiVersion: v1",
        "metadata:",
        "  name: blazemeter-configmap",
        f"  namespace: {o['namespace']}",
        "data:",
        f"  HARBOR_ID: \"{facts['harbor_id']}\"",
        f"  SHIP_ID: \"{o['ship_id']}\"",
    ]
    if not o["use_secret"]:
        lines += [
            "  # Simplified: AUTH_TOKEN in ConfigMap. Hardened option: move to a Secret",
            "  # (regenerate with use_secret=true).",
            f"  AUTH_TOKEN: \"{o['auth_token']}\"",
        ]
    lines += ["  CONTAINER_MANAGER_TYPE: KUBERNETES"]
    if o["platform"] == "openshift":
        lines += [
            "  # OpenShift non-privileged path: engines inherit crane's SCC-assigned",
            "  # UID:GID and drop all capabilities, so spawned engine pods also pass",
            "  # the restricted-v2 SCC.",
            "  INHERIT_RUNNING_USER_AND_GROUP: 'true'",
            "  KUBERNETES_SECURITY_CONTEXT_CAP_JSON: '{\"drop\": [\"ALL\"]}'",
        ]
    lines += [
        f"  KUBERNETES_SERVICE_USE_TYPE: {o['service_type']}",
        "  RUN_HEALTH_WEB_SERVICE: 'true'",
    ]
    sv = _sv_cfg(facts, o)
    if sv:
        lines += [
            "  # Service virtualization ingress. The endpoint crane advertises is",
            "  # <virtual-service>-<port>-<namespace>.<subdomain>, so the subdomain",
            "  # must be the wildcard domain your ingress controller already serves.",
            f"  KUBERNETES_WEB_EXPOSE_TYPE: {sv['type'].upper()}",
            f"  KUBERNETES_WEB_EXPOSE_SUB_DOMAIN: {sv['subdomain']}",
            "  # Required even for HTTP virtual services -- crane validates it at",
            "  # startup and crash-loops when it is empty.",
            f"  KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME: {sv['tls_secret']}",
        ]
        if sv["type"] == "istio":
            lines.append(
                f"  KUBERNETES_ISTIO_GATEWAY_NAME: {sv['istio_gateway']}"
                if sv["istio_gateway"] else
                "  # KUBERNETES_ISTIO_GATEWAY_NAME unset: crane creates a"
                " Gateway per virtual service."
            )
    if o["private_registry"]:
        overrides = _image_overrides(facts, o["private_registry"])
        lines += [
            "  # Private registry: images resolved from the account's live agent",
            f"  # inventory ({facts.get('images_source', 'unknown')}).",
            f"  DOCKER_REGISTRY: {o['private_registry']}",
            f"  IMAGE_OVERRIDES: '{json.dumps(overrides)}'",
            "  # Auto-update pulls from BlazeMeter's public registry -- disabled for",
            "  # private-registry clusters. Upgrade = re-mirror + bump tags.",
            "  AUTO_KUBERNETES_UPDATE: 'false'",
        ]
        if o["registry_auth"]:
            lines += [
                "  # Crane-side auth for engine image pulls (or use cluster pull secrets):",
                "  # DOCKER_REGISTRY_USERNAME: <user>",
                "  # DOCKER_REGISTRY_PASSWORD: <password>",
                "  # DOCKER_REGISTRY_EMAIL: <email>",
            ]
    else:
        lines += [
            "  DOCKER_REGISTRY: gcr.io/verdant-bulwark-278",
            "  AUTO_KUBERNETES_UPDATE: 'true'",
        ]
    if o["proxy"]:
        p = o["proxy"]
        if _proxy_has_creds(o) and o["use_secret"]:
            lines.append("  # HTTP(S)_PROXY embed credentials -> kept in blazemeter-secret.")
        else:
            if _proxy_has_creds(o):
                lines.append("  # WARNING: proxy credentials below are plaintext -- anyone who can")
                lines.append("  # read ConfigMaps sees them. Regenerate with use_secret=true.")
            lines += [f"  {k}: \"{v}\"" for k, v in proxy_env(o).items()
                      if k != "NO_PROXY"]
        lines.append(f"  NO_PROXY: {proxy_env(o)['NO_PROXY']}")
    if o["tolerations"]:
        lines += [
            "  # Engines inherit the crane pod's tolerations via this env.",
            f"  KUBERNETES_TOLERATIONS_JSON: '{json.dumps(o['tolerations'])}'",
        ]
    if o["node_selector"]:
        lines.append(f"  KUBERNETES_NODE_SELECTOR_JSON: '{json.dumps(o['node_selector'])}'")
    if o["engine_cpu_limit"]:
        lines.append(f"  KUBERNETES_RESOURCES_LIMITS_CPU: \"{o['engine_cpu_limit']}\"")
    if o["engine_mem_limit"]:
        lines.append(f"  KUBERNETES_RESOURCES_LIMITS_MEMORY: \"{o['engine_mem_limit']}\"")
    if o["engine_ephemeral_request_mb"]:
        lines.append(f"  KUBERNETES_REQUESTS_EPHEMERAL_STORAGE: \"{o['engine_ephemeral_request_mb']}\"")
    if o["engine_ephemeral_limit_mb"]:
        lines.append(f"  KUBERNETES_LIMITS_EPHEMERAL_STORAGE: \"{o['engine_ephemeral_limit_mb']}\"")
    ca = _ca_cfg(o)
    if ca:
        ca_comment = {
            "inline": "  # Corporate CA bundle (generator-created ConfigMap).",
            "existing": f"  # CA bundle from existing ConfigMap '{ca['cm']}' -- the platform",
            "inject": "  # OpenShift cluster trust bundle (operator-injected ConfigMap).",
        }[ca["mode"]]
        lines.append(ca_comment)
        if ca["mode"] == "existing":
            lines.append("  # team owns and rotates it; these manifests only reference it.")
        path = f"{CA_MOUNT_PATH}/{ca['key']}"
        lines += [
            "  # Mounted into crane; engines get the same ConfigMap mounted via",
            "  # KUBERNETES_CA_BUNDLE_MOUNT (ENV=configmapName=fileKey).",
            f"  REQUESTS_CA_BUNDLE: {path}",
            f"  AWS_CA_BUNDLE: {path}",
            f"  KUBERNETES_CA_BUNDLE_MOUNT: \"REQUESTS_CA_BUNDLE={ca['cm']}={ca['key']}:AWS_CA_BUNDLE={ca['cm']}={ca['key']}\"",
        ]
    return "\n".join(lines) + "\n"


def _indent_yaml(obj, indent):
    """Render obj as indented YAML-compatible JSON block lines."""
    pad = " " * indent
    return "\n".join(pad + line for line in json.dumps(obj, indent=2).splitlines())


def _scheduling_block(o):
    """tolerations / nodeSelector for the crane pod itself (engines get the
    matching env vars in the ConfigMap)."""
    out = ""
    if o["tolerations"]:
        out += "      tolerations:\n" + _indent_yaml(o["tolerations"], 8) + "\n"
    if o["node_selector"]:
        out += "      nodeSelector:\n" + "\n".join(
            f"        {k}: \"{v}\"" for k, v in o["node_selector"].items()) + "\n"
    return out


def _ca_configmap(facts, o):
    ca = _ca_cfg(o)
    if ca["mode"] == "inject":
        return f"""kind: ConfigMap
apiVersion: v1
metadata:
  name: {CA_CONFIGMAP}
  namespace: {o['namespace']}
  labels:
    # OpenShift's network operator injects the cluster-wide trust bundle
    # (proxy/custom CAs configured at cluster level) as ca-bundle.crt.
    # Nobody hand-manages PEMs; rotation is the cluster's job.
    config.openshift.io/inject-trusted-cabundle: "true"
"""
    pem = "\n".join("    " + line for line in o["ca_bundle"].strip().splitlines())
    return f"""kind: ConfigMap
apiVersion: v1
metadata:
  name: {CA_CONFIGMAP}
  namespace: {o['namespace']}
data:
  {CA_FILENAME}: |
{pem}
"""


def _proxy_secret_block(o):
    if not (_proxy_has_creds(o) and o["use_secret"]):
        return ""
    lines = ["  # Proxy URLs embed credentials (user:pass@host) -> kept out of the ConfigMap."]
    lines += [f"  {k}: \"{v}\"" for k, v in proxy_env(o).items() if k != "NO_PROXY"]
    return "\n".join(lines) + "\n"


def _mirror_script(facts, o):
    refs = [facts["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in select_images(facts)
    ]
    reg = o["private_registry"].rstrip("/")
    lines = [
        "#!/usr/bin/env bash",
        "# Mirror the BlazeMeter images this private location needs into your",
        "# private registry. Engines are amd64-only; --platform matters on ARM hosts.",
        f"# Location: {facts.get('harbor_name')} ({facts['harbor_id']}), "
        f"source: {facts.get('images_source')}",
        "set -euo pipefail",
        "",
    ]
    for ref in refs:
        name = ref.rsplit("/", 1)[-1]
        lines += [
            f"docker pull --platform linux/amd64 {ref}",
            f"docker tag {ref} {reg}/{name}",
            f"docker push {reg}/{name}",
            "",
        ]
    return "\n".join(lines)


def _security_context(o):
    if o["platform"] == "openshift":
        return (
            "          securityContext:\n"
            "            # restricted-v2 SCC assigns an in-range UID; leave runAsUser\n"
            "            # unset for portability.\n"
            "            runAsNonRoot: true\n"
            "            allowPrivilegeEscalation: false\n"
            "            capabilities:\n"
            "              drop:\n"
            "                - ALL"
        )
    return (
        "          securityContext:\n"
        "            runAsNonRoot: true\n"
        f"            runAsUser: {o['run_as_user']}\n"
        f"            runAsGroup: {o['run_as_user']}\n"
        "            allowPrivilegeEscalation: false\n"
        "            capabilities:\n"
        "              drop:\n"
        "                - ALL"
    )


def _crane_image(facts, o):
    if not o["private_registry"]:
        return facts["crane_image"]
    tag = facts["crane_image"].rsplit(":", 1)[1]
    return f"{o['private_registry'].rstrip('/')}/crane:{tag}"


def _sv_rbac_block(sv):
    """Namespaced Role rules crane needs to publish a virtual service.

    Deliberately namespaced: this is the whole reason the ingress path is
    preferred over NODEPORT, which would need cluster-scoped node reads.

    Only the group the configured backend actually writes is granted. Crane
    picks one implementation per KUBERNETES_WEB_EXPOSE_TYPE and never touches
    the others, so granting `ingresses` on an istio or contour deployment is
    permission that can only ever go unused.
    """
    if not sv:
        return ""
    backend = SV_INGRESS_BACKENDS[sv["type"]]
    return "\n".join([
        f"  # Service virtualization: crane publishes one {backend.creates}"
        " per virtual service.",
        f"  - apiGroups: [{backend.group}]",
        f"    resources: [{', '.join(backend.resources)}]",
        "    verbs: [get, list, watch, create, update, patch, delete, deletecollection]",
    ]) + "\n"


# -- helm output --------------------------------------------------------------

OUTPUT_FORMATS = ("manifests", "helm")

# The chart is copied verbatim, and what the account supplies is emitted beside
# it as an overlay passed with `-f`. Deliberately an overlay rather than a
# rewritten helm/values.yaml: the chart's own values file holds defaults this
# module does not own -- crane's resources, the probe timings -- and generating
# a complete file would mean restating them here, where they would drift from
# the chart the first time either side changed. The overlay names only the keys
# that come from the customer's account, so `helm show values` stays the
# documentation and re-generating never touches the chart.
HELM_DIR = os.path.join(TEMPLATE_DIR, "helm")
CHART_DIR = "helm"
HELM_CHART_FILE = f"{CHART_DIR}/Chart.yaml"
HELM_VALUES_FILE = "bzm-opl-values.yaml"


def _helm_chart_files():
    """The static chart, keyed by output path."""
    out = {}
    for root, _, names in os.walk(HELM_DIR):
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(root, name), HELM_DIR)
            with open(os.path.join(root, name)) as f:
                out[f"{CHART_DIR}/{rel}"] = f.read()
    return out


def _yq(value):
    """A scalar as a double-quoted YAML string. Everything a value can hold here
    is an id, a quantity or a URL, and quoting all of them keeps `latest`,
    `1337` and `8Gi` from being read as the wrong type."""
    return json.dumps("" if value is None else str(value))


def _helm_values(facts, o):
    """The values overlay: the resolved options, in the chart's vocabulary.

    Written as text rather than dumped from a dict because it is a file the
    customer edits and re-reads -- the comments are the point, and pyyaml is a
    test-only dependency this package does not carry at runtime.
    """
    ca = _ca_cfg(o)
    image = _crane_image(facts, o)
    repo, _, tag = image.rpartition(":")
    lines = [
        "# BlazeMeter private location agent -- generated by bzm-opl-gen from",
        f"# location {facts.get('harbor_name')} ({facts['harbor_id']}).",
        "#",
        f"#   helm install crane ./{CHART_DIR} -n {o['namespace']} -f {HELM_VALUES_FILE}",
        "#",
        "# Only the keys that come from your account are here; everything else",
        "# keeps the chart's default. `helm show values ./helm` lists them all,",
        "# with the reasoning. Re-generating overwrites this file and leaves the",
        "# chart alone, so prefer re-generating to hand-editing.",
        "",
        f"harborId: {_yq(facts['harbor_id'])}",
        f"shipId: {_yq(o['ship_id'])}",
    ]
    if o["auth_token"] and o["auth_token"] != DEFAULT_OPTIONS["auth_token"]:
        lines += [
            "# The agent credential. Anyone holding it can register as this",
            "# agent -- pass it at install time (--set-string authToken=...) or",
            "# via existingSecret rather than committing this file.",
            f"authToken: {_yq(o['auth_token'])}",
        ]
    else:
        lines += [
            "# Not fetched: generate with --api-key, or pass it at install time",
            "# (helm install --set-string authToken=...).",
            'authToken: ""',
        ]
    lines += [
        "",
        f"platform: {o['platform']}",
        f"serviceType: {o['service_type']}",
        f"useSecret: {'true' if o['use_secret'] else 'false'}",
        'existingSecret: ""',
        "",
        "image:",
        # Pinned, not floated: this is the tag the account advertises for this
        # location right now. The chart's own default is `latest`, which is the
        # right default for a chart with no API access and the wrong one for a
        # bundle generated against a real account.
        "  # The tag this location currently advertises, pinned at generate time.",
        f"  repository: {_yq(repo)}",
        f"  tag: {_yq(tag)}",
        "  pullPolicy: Always",
        f"  pullSecret: {_yq(o['pull_secret'] or '')}",
        "",
    ]
    if o["private_registry"]:
        overrides = _image_overrides(facts, o["private_registry"])
        lines += [
            f"privateRegistry: {_yq(o['private_registry'])}",
            "# Resolved from the account's live agent inventory "
            f"({facts.get('images_source', 'unknown')}).",
            "# A key crane cannot find falls back to the public registry without",
            "# logging anything, so re-generate rather than editing this by hand.",
            "imageOverrides:",
        ] + [f"  {json.dumps(k)}: {_yq(v)}" for k, v in sorted(overrides.items())] + [
            f"registryAuth: {'true' if o['registry_auth'] else 'false'}",
        ]
    else:
        lines += [
            '# Empty -> BlazeMeter\'s public registry, and auto-update stays on.',
            'privateRegistry: ""',
            "imageOverrides: {}",
            "registryAuth: false",
        ]
    lines += [
        "",
        "# Left to the chart's default (on here, off with a private registry).",
        "# Set false if you will manage this release with `helm upgrade`: left on,",
        "# crane takes ownership of its own Deployment and the next upgrade fails",
        "# on a field-ownership conflict. See autoUpdate in the chart's values.yaml",
        "# for what that costs either way.",
        "autoUpdate:",
    ]
    lines += [
        "",
        f"clusterRbac: {'true' if o['cluster_rbac'] else 'false'}",
        "serviceAccount:",
        "  create: true",
        '  name: ""',
        "  annotations: {}",
        "",
    ]
    if o["platform"] == "openshift":
        lines += ["# Ignored on openshift: the restricted-v2 SCC assigns the UID.",
                  f"runAsUser: {o['run_as_user']}"]
    else:
        lines.append(f"runAsUser: {o['run_as_user']}")
    lines.append("")
    if o["node_selector"]:
        lines += ["nodeSelector:"] + [
            f"  {json.dumps(k)}: {_yq(v)}" for k, v in o["node_selector"].items()]
    else:
        lines.append("nodeSelector: {}")
    if o["tolerations"]:
        # Engines inherit these through KUBERNETES_TOLERATIONS_JSON, which the
        # chart derives from this same list. Spelled out as YAML rather than the
        # JSON block _indent_yaml would give: a toleration is the thing someone
        # most often hand-edits after generating, and `- key: ...` is what every
        # other example they will find looks like.
        lines.append("tolerations:")
        for tol in o["tolerations"]:
            items = list(tol.items())
            lines += [f"  {'- ' if i == 0 else '  '}{k}: {_yq(v)}"
                      for i, (k, v) in enumerate(items)]
    else:
        lines.append("tolerations: []")
    cpu_limit, mem_limit = engine_size(o)
    cpu_req, mem_req = engine_requests(o)
    lines += [
        "",
        "# What crane stamps on the engine pods it spawns. Empty -> BlazeMeter's",
        f"# documented default. This location asks for {format_cpu(cpu_limit)} CPU + "
        f"{format_memory(mem_limit)} per engine,",
        f"# plus ~{ENGINE_DISK_GB}GB disk ({ENGINE_TMP_GB}GB of it /tmp). Check a cluster with "
        "`bzm-opl-gen doctor`.",
        "engine:",
        f"  cpuLimit: {_yq(o['engine_cpu_limit'] or '')}",
        f"  memoryLimit: {_yq(o['engine_mem_limit'] or '')}",
        f"  cpuRequest: {_yq(o['engine_cpu_request'] or '')}",
        f"  memoryRequest: {_yq(o['engine_mem_request'] or '')}",
        f"  ephemeralRequestMb: {_yq(o['engine_ephemeral_request_mb'] or '')}",
        f"  ephemeralLimitMb: {_yq(o['engine_ephemeral_limit_mb'] or '')}",
        "",
        "# Namespace-wide, so it reaches every pod in the namespace and not just",
        "# this release. It does not change what crane requests for engine pods.",
        "limitRange:",
        f"  enabled: {'true' if o['emit_limitrange'] else 'false'}",
    ]
    if o["emit_limitrange"]:
        max_cpu, max_mem = _limitrange_max(o)
        # maxCpu/maxMemory are deliberately NOT pinned here. The chart derives
        # them from the engine size, raised to clear crane's own limits; pinning
        # the value computed at generate time makes the LimitRange
        # self-inconsistent the moment anyone raises the engine size, and the
        # API server rejects it ("default request value 6Gi is greater than max
        # value 4Gi"). Verified on a live cluster -- the upgrade fails, and the
        # release is left half-applied.
        lines += [
            f"  # max is derived: {format_cpu(max_cpu)} CPU / {format_memory(max_mem)} for this engine size,",
            f"  # never below crane's own limits ({CRANE_CPU_LIMIT} / {CRANE_MEM_LIMIT}) or the crane pod is",
            "  # rejected in its own namespace. Set maxCpu/maxMemory only to raise",
            "  # it further -- the chart refuses a value below what it derives.",
            f"  # requests default to the limits: {format_cpu(cpu_req)} / {format_memory(mem_req)}",
        ]
    lines.append("")
    if o["proxy"]:
        env = proxy_env(o)
        lines += ["proxy:", "  enabled: true"]
        if _proxy_has_creds(o):
            lines.append("  # Credentials are embedded in the URL -- BlazeMeter has no separate")
            lines.append("  # proxy-auth env vars. The chart routes a URL carrying them into the")
            lines.append("  # Secret rather than the ConfigMap.")
        lines += [
            f"  http: {_yq(env.get('HTTP_PROXY', ''))}",
            f"  https: {_yq(env.get('HTTPS_PROXY', ''))}",
            f"  noProxy: {_yq(env['NO_PROXY'])}",
        ]
    else:
        lines += ["proxy:", "  enabled: false", '  http: ""', '  https: ""',
                  f"  noProxy: {_yq(DEFAULT_NO_PROXY)}"]
    lines.append("")
    mode = {"inline": "inline", "existing": "existing",
            "inject": "openshiftInject"}[ca["mode"]] if ca else "none"
    lines += ["caBundle:", f"  mode: {mode}"]
    if ca and ca["mode"] == "inline":
        lines.append("  pem: |")
        lines += ["    " + line for line in o["ca_bundle"].strip().splitlines()]
    else:
        lines.append('  pem: ""')
    lines += [
        '  file: ""',
        f"  existingConfigMap: {_yq(ca['cm'] if ca and ca['mode'] == 'existing' else '')}",
        f"  key: {_yq(ca['key'] if ca else CA_FILENAME)}",
        f"  mountPath: {_yq(CA_MOUNT_PATH)}",
    ]
    return "\n".join(lines) + "\n"


def _helm_readme(facts, o):
    ns = o["namespace"]
    token = ""
    if not o["auth_token"] or o["auth_token"] == DEFAULT_OPTIONS["auth_token"]:
        token = (f" \\\n    --set-string authToken=<AUTH_TOKEN>"
                 f"   # not in {HELM_VALUES_FILE}: generate with --api-key to embed it")
    mirror = ""
    if o["private_registry"]:
        mirror = (
            f"\n## Mirror the images into `{o['private_registry']}` first\n\n"
            "Run the included `bzm-opl-image-mirror.sh` (docker pull/tag/push, forced\n"
            f"linux/amd64). `{HELM_VALUES_FILE}` already names every image by the key\n"
            "crane resolves it under, so nothing else has to be filled in.\n")
    limitrange = ""
    if o["emit_limitrange"]:
        max_cpu, max_mem = _limitrange_max(o)
        limitrange = (
            f"\nThe LimitRange caps `{ns}` at {format_cpu(max_cpu)} CPU / "
            f"{format_memory(max_mem)} per container and applies to **every** pod in\n"
            "the namespace, not just this release. It does not change what crane\n"
            "requests for engine pods -- see `helm/README.md` for why nothing can.\n")
    return f"""# BlazeMeter OPL -- generated Helm chart

- Location: **{facts.get('harbor_name')}** (`{facts['harbor_id']}`), features: {facts.get('func_ids')}
- Agent (ship): `{o['ship_id']}`
- Platform: {o['platform']}, namespace: `{ns}`
- Crane image: `{_crane_image(facts, o)}` (pinned to what the account advertises)

`{CHART_DIR}/` is the chart, identical for every customer. `{HELM_VALUES_FILE}` is the
part generated from your account, and it is an overlay: it names only the keys
your location needs and everything else keeps the chart's default. Re-generating
overwrites the overlay and leaves the chart untouched.

Run `helm show values ./{CHART_DIR}` for every key you can set, with the reasoning.
{mirror}
## Install

```
helm install crane ./{CHART_DIR} -n {ns} --create-namespace -f {HELM_VALUES_FILE}{token}
```

## Verify

```
helm -n {ns} status crane
kubectl -n {ns} rollout status deploy/crane
kubectl -n {ns} logs -l role=role-crane -f
```

Then confirm the agent shows **online** in BlazeMeter (Settings -> Private
Locations).

## Upgrade

**Set `autoUpdate: false` in `{HELM_VALUES_FILE}` if you intend to manage this
release with Helm.** Then upgrades are ordinary:

```
helm upgrade crane ./{CHART_DIR} -n {ns} -f {HELM_VALUES_FILE}
```

Left on -- the default, matching the manifests -- crane takes over its own
Deployment within seconds of install: it rewrites `.spec.template.spec.
containers[].image` to the version BlazeMeter currently ships, and
`.spec.strategy` from `Recreate` to `RollingUpdate`. Helm applies server-side, so
the next `helm upgrade` fails on a field-ownership conflict, half-applied.
`--force-conflicts` does not rescue it: Helm never declares
`strategy.rollingUpdate`, so crane's copy survives next to the forced
`type: Recreate` and the API server rejects the combination. With auto-update on,
changing anything means `helm uninstall` + `helm install`, not an upgrade.

All of the above was observed on a live cluster, not inferred from the docs.

The tradeoff for turning it off: the agent stops upgrading itself, so keeping it
current becomes your job -- re-generate to pick up a newer tag. An agent that
falls far enough behind stops being supported.

`harbor_id` and `ship_id` are part of the Deployment's selector and selectors
are immutable, so repointing this install at a *different* agent needs
`helm uninstall` + `helm install`, not an upgrade. Every other option upgrades
in place; the Deployment carries checksums of the ConfigMap and Secret, so a
configuration-only change still rolls the pod.
{limitrange}
Engines need **{format_cpu(engine_size(o)[0])} CPU + {format_memory(engine_size(o)[1])} RAM + {ENGINE_DISK_GB}GB disk ({ENGINE_TMP_GB}GB /tmp)** per concurrent engine.
Egress required to *.blazemeter.com and the image registry.
Check the target cluster against that with `bzm-opl-gen doctor`.

## Deploying these as plain manifests instead

Re-generate with `--format manifests` (or the toggle in `bzm-opl-gen ui`). The
two render the same objects -- same ConfigMap data, RBAC rules, LimitRange and
container spec -- so the choice is about how you want to install and upgrade,
not about what ends up in the cluster.
"""


PREVIEW_TAIL = ["bzm-opl-image-mirror.sh", "README.md"]


def preview_order(files):
    """The order generated files are listed in, for any caller showing them to a
    human. Lives here rather than in the server because it is a fact about what
    generate() emits: the manifest order is the order they must be applied in,
    and the chart's is most-generated-first, values.yaml being the only file in
    a chart bundle that is specific to the account.
    """
    if HELM_CHART_FILE in files:
        lead = [HELM_VALUES_FILE, "README.md", f"{CHART_DIR}/README.md",
                HELM_CHART_FILE, f"{CHART_DIR}/values.yaml"]
    else:
        lead = APPLY_ORDER + PREVIEW_TAIL
    return [n for n in lead if n in files] + sorted(set(files) - set(lead))


def generate(facts, options):
    """Return {filename: content}. options overrides DEFAULT_OPTIONS.

    Names may contain `/` -- the helm format emits a chart directory. write()
    creates the parent directories.
    """
    o = {**DEFAULT_OPTIONS, **options}
    if "ship_id" not in o:
        ships = facts.get("ships") or []
        if len(ships) == 1:
            o["ship_id"] = ships[0]["id"]
        else:
            raise ValueError(
                f"ship_id required: location has {len(ships)} ships "
                f"({[s['id'] for s in ships]})"
            )

    if o["output_format"] not in OUTPUT_FORMATS:
        raise ValueError(f"output_format must be one of {OUTPUT_FORMATS}, "
                         f"got {o['output_format']!r}")

    engine_requests(o)  # a bad engine size is wrong with or without the LimitRange

    ca = _ca_cfg(o)
    sv = _sv_cfg(facts, o)
    if sv and o["output_format"] == "helm":
        # Not a gap to fill in later: publishing a virtual service needs an
        # ingress backend, the RBAC for whichever one it is, and a wildcard TLS
        # secret, and the upstream Blazemeter/helm-crane chart already carries
        # all three. Emitting a chart that quietly dropped them would deploy,
        # report idle, and stall at WAITING_FOR_DOMAIN.
        raise ValueError(
            "output_format=helm covers performance testing only, and this "
            f"location is configured for service virtualization (sv_ingress="
            f"{sv['type']}). Generate it as --format manifests, or use the "
            "upstream Blazemeter/helm-crane chart, which supports both.")

    if o["output_format"] == "helm":
        out = _helm_chart_files()
        out[HELM_VALUES_FILE] = _helm_values(facts, o)
        if o["private_registry"]:
            out["bzm-opl-image-mirror.sh"] = _mirror_script(facts, o)
        out["README.md"] = _helm_readme(facts, o)
        out[PROFILE_FILE] = _profile_json(o)
        return out

    sub = {
        "SV_RBAC_BLOCK": _sv_rbac_block(sv),
        "NAMESPACE": o["namespace"],
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
        "PROXY_SECRET_BLOCK": _proxy_secret_block(o),
        "CRANE_IMAGE": _crane_image(facts, o),
        "PULL_SECRETS_BLOCK": (
            f"      imagePullSecrets:\n        - name: {o['pull_secret']}\n"
            if o["pull_secret"] else ""
        ),
        "SECURITY_CONTEXT_BLOCK": _security_context(o),
        "CRANE_CPU_REQUEST": CRANE_CPU_REQUEST,
        "CRANE_MEM_REQUEST": CRANE_MEM_REQUEST,
        "CRANE_CPU_LIMIT": CRANE_CPU_LIMIT,
        "CRANE_MEM_LIMIT": CRANE_MEM_LIMIT,
        "SECRET_REF_BLOCK": (
            "            - secretRef:\n                name: blazemeter-secret\n"
            if o["use_secret"] else ""
        ),
        "SCHEDULING_BLOCK": _scheduling_block(o),
        "VOLUME_MOUNTS_BLOCK": (
            "          volumeMounts:\n"
            f"            - name: cacerts\n"
            f"              mountPath: {CA_MOUNT_PATH}\n"
            f"              readOnly: true\n"
            if ca else ""
        ),
        "VOLUMES_BLOCK": (
            "      volumes:\n"
            f"        - name: cacerts\n"
            f"          configMap:\n"
            f"            name: {ca['cm']}\n"
            if ca else ""
        ),
    }

    out = {
        "bzm_serviceaccount.yaml": _tpl("serviceaccount.yaml").substitute(sub),
        "bzm_configmap.yaml": _configmap(facts, o),
        "bzm_role.yaml": _tpl("role.yaml").substitute(sub),
        "bzm_rolebinding.yaml": _tpl("rolebinding.yaml").substitute(sub),
        "bzm_deployment.yaml": _tpl("deployment.yaml").substitute(sub),
    }
    if o["emit_limitrange"]:
        out[LIMITRANGE_FILE] = _tpl("limitrange.yaml").substitute(_limitrange_sub(o))
    if o["use_secret"]:
        out["bzm_secret.yaml"] = _tpl("secret.yaml").substitute(sub)
    if o["cluster_rbac"]:
        out["bzm_clusterrole.yaml"] = _tpl("clusterrole.yaml").substitute(sub)
        out["bzm_clusterrolebinding.yaml"] = _tpl("clusterrolebinding.yaml").substitute(sub)
    if ca and ca["mode"] in ("inline", "inject"):
        out["bzm_cacerts.yaml"] = _ca_configmap(facts, o)
    if o["private_registry"]:
        out["bzm-opl-image-mirror.sh"] = _mirror_script(facts, o)
    out["README.md"] = _readme(facts, o, out)
    out[PROFILE_FILE] = _profile_json(o)
    return out


# -- exposing a deployed virtual service --------------------------------------

SV_EXPOSE_FILE = "bzm_sv_expose.yaml"
# Crane stamps these on the mock pod. Unlike the Services it creates -- whose
# names carry a per-deploy hash (crane-9f30e-..., crane-e69e2-...) -- these are
# derived from the virtual service's identity and survive every redeploy, which
# is what lets a Service of our own keep pointing at the right pod.
SV_POD_NAME_LABEL = "BZM_CONTAINER_NAME"
SV_POD_HARBOR_LABEL = "BZM_HARBOR_ID"
SV_POD_SHIP_LABEL = "BZM_SHIP_ID"

# The class we put on the Ingress *we* own, when the caller names none. It is
# `nginx` because that is the common cluster -- not because crane hardcodes the
# same string on its own Ingress, which is a different fact with a different
# owner (doctor.CRANE_INGRESS_CLASS). On OpenShift pass openshift-default.
SV_EXPOSE_DEFAULT_INGRESS_CLASS = "nginx"


class SvPublish(collections.namedtuple(
        "SvPublish", "subdomain tls_secret ingress_class")):
    """Where `sv-expose` publishes: the wildcard host, an optional TLS secret,
    and the IngressClass that should claim the Ingress."""
    __slots__ = ()


def sv_publish_cfg(o):
    """Resolve a profile into the three fields `sv_expose` actually needs.

    Deliberately not `_sv_cfg`. That validates what *crane* needs at generate
    time, where the TLS secret is mandatory even for HTTP because crane
    crash-loops without the name. This describes an Ingress we own and apply
    ourselves: TLS is genuinely optional, and `ingress_class` is an sv-expose
    argument that never reaches the agent at all.
    """
    subdomain = o.get("sv_subdomain")
    if not subdomain:
        raise ValueError(
            "sv-expose needs sv_subdomain -- the endpoint host is "
            "<mock>-<port>-<namespace>.<subdomain>. Generate the manifests with "
            "--sv-subdomain so the profile carries it, or pass --sv-subdomain "
            "here.")
    return SvPublish(subdomain, o.get("sv_tls_secret"),
                     o.get("sv_ingress_class") or SV_EXPOSE_DEFAULT_INGRESS_CLASS)


def sv_endpoint_host(name, port, namespace, subdomain):
    """The host BlazeMeter advertises for a deployed virtual service, or None
    when no wildcard domain is configured yet.

    One function because two callers are judged by this exact string: sv_expose
    puts it on the Ingress it emits, and the UI shows it to someone who is about
    to paste it into a browser. A second copy of the formula would let the route
    that exists and the host a human is told to try drift apart.
    """
    return f"{name}-{port}-{namespace}.{subdomain}" if subdomain else None


def sv_expose(mocks, namespace, publish):
    """Render a Service + Ingress per deployed virtual service.

    Crane publishes its own pair, but the Ingress is unusable: its backend says
    `port.number: 8080` while the Service it created exposes `port: 80`, and
    Kubernetes resolves a backend against `spec.ports[].port`. Nothing claims
    it, so the endpoint BlazeMeter advertises 503s while the mock serves
    happily inside the cluster.

    Rather than patch an object crane rewrites on every deploy, this emits a
    parallel pair that sidesteps the mismatch: `port == targetPort`, and a
    selector on the pod's identity labels rather than crane's hashed Service
    name. Because we own the Ingress, `ingress_class` can name whatever class
    the cluster actually has -- so OpenShift needs no `nginx` alias, and no
    policy engine or admission webhook is involved. Crane's own Ingress is left
    alone; it stays unclaimed and creates no competing route.

    `mocks` is a list of {"name", "port", "harbor", "ship"} -- see
    livetest.sv_mocks(), which reads them off the running pods. The ids come
    from the pod rather than the profile because profile.json carries no
    harbor_id, and the pod is the authority on what crane actually stamped.
    """
    ns, docs = namespace, []
    for m in mocks:
        name, port = m["name"], m["port"]
        obj = f"bzm-sv-{name}"
        host = sv_endpoint_host(name, port, ns, publish.subdomain)
        tls = ""
        if publish.tls_secret:
            tls = ("  tls:\n"
                   f"    - hosts: [{host}]\n"
                   f"      secretName: {publish.tls_secret}\n")
        docs.append(
            "apiVersion: v1\n"
            "kind: Service\n"
            f"metadata: {{ name: {obj}, namespace: {ns} }}\n"
            "spec:\n"
            "  selector:\n"
            f"    {SV_POD_NAME_LABEL}: {name}\n"
            f"    {SV_POD_HARBOR_LABEL}: \"{m['harbor']}\"\n"
            f"    {SV_POD_SHIP_LABEL}: \"{m['ship']}\"\n"
            "  ports:\n"
            # port == targetPort is the whole point: it is what makes the
            # Ingress backend below resolve.
            f"    - {{ name: http, port: {port}, targetPort: {port}, protocol: TCP }}\n"
            "---\n"
            "apiVersion: networking.k8s.io/v1\n"
            "kind: Ingress\n"
            f"metadata: {{ name: {obj}, namespace: {ns} }}\n"
            "spec:\n"
            f"  ingressClassName: {publish.ingress_class}\n"
            f"{tls}"
            "  rules:\n"
            f"    - host: {host}\n"
            "      http:\n"
            "        paths:\n"
            "          - path: /\n"
            "            pathType: Prefix\n"
            "            backend:\n"
            f"              service: {{ name: {obj}, port: {{ number: {port} }} }}\n")
    return "---\n".join(docs)


PROFILE_FILE = "profile.json"


def _profile_json(o):
    """The resolved options, replayable with `generate --profile`. AUTH_TOKEN is
    left out on purpose -- it is re-fetched from the API, so this file can be
    committed or handed over without leaking the agent credential."""
    return json.dumps({k: v for k, v in sorted(o.items()) if k != "auth_token"},
                      indent=2) + "\n"


def load_profile(outdir):
    """Read back the profile.json generate() wrote next to the manifests."""
    path = os.path.join(outdir, PROFILE_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found -- regenerate the manifests with a current "
            f"bzm-opl-gen so the options can be replayed")
    with open(path) as f:
        return json.load(f)


APPLY_ORDER = [
    "bzm_serviceaccount.yaml", "bzm_configmap.yaml",
    # Before the deployment: engines must not schedule before the defaults exist.
    LIMITRANGE_FILE,
    "bzm_secret.yaml",
    "bzm_cacerts.yaml", "bzm_role.yaml", "bzm_rolebinding.yaml",
    "bzm_clusterrole.yaml", "bzm_clusterrolebinding.yaml",
    "bzm_deployment.yaml",
]


def _readme(facts, o, files):
    cli = "oc" if o["platform"] == "openshift" else "kubectl"
    apply_lines = "\n".join(
        f"{cli} -n {o['namespace']} apply -f {f}" for f in APPLY_ORDER if f in files
    )
    # Client-side apply copies the object into the last-applied-configuration
    # annotation, which the API server caps at 256KB. Corporate bundles that
    # include the public roots run right into it.
    big_ca = ""
    if o["ca_bundle"] and len(o["ca_bundle"]) > 200_000:
        big_ca = (f"\n> The CA bundle is {len(o['ca_bundle']) // 1024}KB. Apply "
                  f"`bzm_cacerts.yaml` with `--server-side` -- client-side apply "
                  f"stores a copy in an annotation, which is capped at 256KB.\n")
    limitrange = ""
    if o["emit_limitrange"]:
        cpu_req, mem_req = engine_requests(o)
        max_cpu, max_mem = _limitrange_max(o)
        limitrange = f"""
## Engine sizing (`bzm_limitrange.yaml`)

Crane sets engine **limits** from `KUBERNETES_RESOURCES_LIMITS_CPU` /
`KUBERNETES_RESOURCES_LIMITS_MEMORY`. It also sets the engine's **requests**, to
{ENGINE_STAMPED_REQUEST_CPU} / {ENGINE_STAMPED_REQUEST_MEM} -- roughly an eighth of what the engine is allowed to use. The
scheduler packs nodes on requests, so on a busy node the run competes for CPU it
was never given, and the numbers the test reports are wrong rather than merely
slow.

**This file does not fix that**, and nothing in these manifests can: a
LimitRange's `defaultRequest` only fills in fields a pod leaves unset, and crane
sets the engine's requests explicitly. What this file does do:

- `max` **{format_cpu(max_cpu)} CPU / {format_memory(max_mem)}** is enforced at
  admission -- a pod above it is rejected, so nothing in the namespace can be
  sized past the engine (raised where needed to clear crane's own limits, or the
  crane pod would be rejected in its own namespace).
- `defaultRequest` **{format_cpu(cpu_req)} CPU / {format_memory(mem_req)}** and
  the matching `default` reach every pod in the namespace that declares no
  resources of its own -- including crane's per-run job pods, which otherwise
  schedule as best-effort.

It is namespace-wide, so other workloads in `{o['namespace']}` get those
defaults too. Give the private location its own namespace if that is a problem.

To size engines honestly today, give the location nodes it does not share, or
add a mutating admission policy that rewrites the engine pod's requests --
`bzm-opl-gen livetest --run-test` prints the live gap under `ENGINE SIZING:`.
"""
    mirror = ""
    if o["private_registry"]:
        imgs = [facts["crane_image"]] + [
            f"{i['repo']}:{i['tag']}" for i in select_images(facts)
        ]
        mirror = (
            "\n## Mirror these images into "
            f"`{o['private_registry']}` first\n\nRun the included "
            "`bzm-opl-image-mirror.sh` (docker pull/tag/push, forced linux/amd64), "
            "or mirror manually:\n\n```\n" + "\n".join(imgs) + "\n```\n"
        )
    return f"""# BlazeMeter OPL -- generated manifests

- Location: **{facts.get('harbor_name')}** (`{facts['harbor_id']}`), features: {facts.get('func_ids')}
- Agent (ship): `{o['ship_id']}`
- Platform: {o['platform']}, namespace: `{o['namespace']}`
- AUTH_TOKEN: {"in Secret (bzm_secret.yaml)" if o['use_secret'] else "in ConfigMap (simplified)"}
{mirror}
## Apply

```
{apply_lines}
```
{big_ca}
## Verify

```
{cli} -n {o['namespace']} rollout status deploy/crane
{cli} -n {o['namespace']} logs -l role=role-crane -f
```

Then confirm the agent shows **online** in BlazeMeter (Settings -> Private Locations),
or run the generator's live test: `bzm-opl-gen livetest ...`

Engines need **{format_cpu(engine_size(o)[0])} CPU + {format_memory(engine_size(o)[1])} RAM + {ENGINE_DISK_GB}GB disk ({ENGINE_TMP_GB}GB /tmp)** per concurrent engine.
Egress required to *.blazemeter.com and the image registry.
Check the target cluster against that with `bzm-opl-gen doctor`.
{limitrange}"""


def write(files, outdir):
    os.makedirs(outdir, exist_ok=True)
    for name, content in files.items():
        path = os.path.join(outdir, name)
        # The helm format keys files by chart-relative path, so a name can carry
        # directories that do not exist yet.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
    return sorted(files)
