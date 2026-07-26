"""Render deployable manifests from templates + facts + customer options."""

import json
import os
from string import Template
from urllib.parse import quote

from .facts import select_images
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

DEFAULT_OPTIONS = {
    "platform": "openshift",        # openshift | k8s
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
    "sv_ingress": None,              # None | "nginx" | "istio"
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

# The funcIds BlazeMeter puts on a location that serves virtual services. Both
# need the same ingress wiring: mockServices runs the mocks, sv-bridge fronts
# them, and either alone is enough to make the ingress options mandatory.
SV_FUNC_IDS = ("mockServices", "sv-bridge")
SV_INGRESS_TYPES = ("nginx", "istio")


def _sv_cfg(facts, o):
    """Resolve the service-virtualization ingress options, or None.

    Every branch here failed on a real cluster first, so the errors name the
    fix rather than the rule:

    - A mockServices location generated without these options deploys happily
      and then hangs forever. Crane has no domain to hand the virtual service,
      so tracking sits at WAITING_FOR_DOMAIN with no error, the mock never
      initialises, and the pod looks healthy at 1/1. Refusing to generate is
      the only signal the customer gets in time.
    - The TLS secret is required even when the virtual service speaks plain
      HTTP; without it crane crash-loops on `ValidationError: TLS secret name
      is empty`.
    - NODEPORT makes crane resolve the address from the Node object, which is
      cluster-scoped and so ungrantable by a namespaced Role. The ingress path
      exists precisely to avoid needing that, so the combination is refused.
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
    """
    if not sv:
        return ""
    rules = [
        "  # Service virtualization: crane publishes one Ingress per virtual service.",
        "  - apiGroups: [networking.k8s.io]",
        "    resources: [ingresses]",
        "    verbs: [get, list, watch, create, update, patch, delete, deletecollection]",
    ]
    if sv["type"] == "istio":
        rules += [
            "  - apiGroups: [networking.istio.io]",
            "    resources: [gateways, virtualservices]",
            "    verbs: [get, list, watch, create, update, patch, delete, deletecollection]",
        ]
    return "\n".join(rules) + "\n"


def generate(facts, options):
    """Return {filename: content}. options overrides DEFAULT_OPTIONS."""
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

    engine_requests(o)  # a bad engine size is wrong with or without the LimitRange

    ca = _ca_cfg(o)
    sv = _sv_cfg(facts, o)
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


def sv_expose(mocks, o):
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
    if not o.get("sv_subdomain"):
        raise ValueError("sv_expose needs sv_subdomain -- the endpoint host is "
                         "<mock>-<port>-<namespace>.<subdomain>")
    ns, docs = o["namespace"], []
    for m in mocks:
        name, port = m["name"], m["port"]
        obj = f"bzm-sv-{name}"
        host = f"{name}-{port}-{ns}.{o['sv_subdomain']}"
        tls = ""
        if o.get("sv_tls_secret"):
            tls = ("  tls:\n"
                   f"    - hosts: [{host}]\n"
                   f"      secretName: {o['sv_tls_secret']}\n")
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
            f"  ingressClassName: {o.get('sv_ingress_class') or 'nginx'}\n"
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
        with open(os.path.join(outdir, name), "w") as f:
            f.write(content)
    return sorted(files)
