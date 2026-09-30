"""The generator's options: defaults, what each format ignores, and the readers
the renderers share.

doctor, plan and livetest pass option dicts that were never merged over
DEFAULT_OPTIONS, so readers use `o.get`.
"""

from .footprint import (ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM,
                        ENGINE_MIN_DERIVED_MEM_MB)
from .markers import is_placeholder
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory


# The formats a bundle can be rendered in.
OUTPUT_FORMATS = ("manifests", "helm", "docker")

DEFAULT_OPTIONS = {
    "platform": "openshift",        # openshift | k8s
    # The product, where `platform` is the UID posture (which also installs on
    # vanilla Kubernetes). It decides `oc` or `kubectl` in the bundle's
    # instructions, and whether a Route or an injected trust bundle is offered.
    "openshift_cluster": False,
    "output_format": "manifests",   # manifests | helm | docker
    "namespace": "blazemeter",
    "use_secret": True,              # False -> AUTH_TOKEN in ConfigMap (simplified)
    # The one option whose default is its marker: there is no sensible fallback
    # value.
    "auth_token": "<AUTH_TOKEN>",
    "private_registry": None,        # e.g. registry.example.com/blazemeter
    "pull_secret": None,             # name of docker-registry secret for crane image
    "registry_auth": False,          # emit commented DOCKER_REGISTRY_USERNAME/PASSWORD
    # AUTO_KUBERNETES_UPDATE (AUTO_UPDATE on docker). None takes the default,
    # which is off; see auto_update().
    "auto_update": None,             # None | True | False
    "cluster_rbac": False,           # include optional ClusterRole/Binding files
    # Named by the Deployment and the RBAC subjects either way; `create` only
    # decides whether the bundle emits the object.
    "service_account_name": "crane",
    "service_account_create": True,
    "service_type": "CLUSTERIP",    # CLUSTERIP | NODEPORT
    # Service virtualization on Kubernetes. None is unanswered, refused for a
    # mockServices location; SV_INGRESS_NONE answers performance only.
    "sv_ingress": None,              # None, SV_INGRESS_NONE, or an SV_INGRESS_TYPE
    "sv_subdomain": None,            # e.g. apps.example.com -- endpoint host suffix
    "sv_tls_secret": None,           # wildcard TLS secret, in the agent's own namespace
    "sv_istio_gateway": None,        # optional; unset -> a Gateway per virtual service
    # ...and on a docker host. The PEMs hold content rather than a path, so a
    # bundle can be generated for a host nobody here can see.
    "sv_hostname": None,             # HOSTNAME_OVERRIDE -- what the agent advertises
    "sv_tls_cert": None,             # inline PEM certificate -> sv-tls.crt, mounted
    "sv_tls_key": None,              # inline PKCS#8 PEM key -> sv-tls.key, mounted
    # {http, https, no_proxy, username, password}. Credentials are embedded in
    # the URL, which moves into the Secret when use_secret is on.
    "proxy": None,
    "run_as_user": 1337,             # k8s platform only (openshift: SCC assigns)
    # Engines drop all capabilities and inherit crane's UID:GID. Crane's own
    # default engine pod is privileged, which admission control refuses.
    "restrict_engines": True,
    "tolerations": None,             # k8s toleration list -> crane pod + engines
    "node_selector": None,           # {"label": "value"} -> crane pod + engines
    # Engines only, overriding the two above. None follows crane's placement;
    # an explicit {} or [] gives engines no selector or toleration.
    "engine_tolerations": None,      # k8s toleration list -> engines only
    "engine_node_selector": None,    # {"label": "value"} -> engines only
    # Sizes the node pool recipe and doctor's packing check; reaches no
    # manifest. None means 1.
    "engines_per_node": None,
    # CA trust: at most one mode (see ca_trust.CA_MODES).
    "ca_bundle": None,               # inline PEM -> generator creates the ConfigMap
    "ca_bundle_slot": False,         # the certificate is a *file*, named below
    "ca_existing_configmap": None,   # name of a ConfigMap the platform team owns/rotates
    "ca_configmap_key": None,        # bundle file key within it (default ca-bundle.crt)
    # The certificate's file name: the ConfigMap key, the mounted file and the
    # chart file are one file. Blank becomes the marker, except inline, where
    # the bundle writes the file itself and picks the name.
    "ca_cert_file": None,            # certificate file name; blank -> <CA_CERT_FILE>
    "ca_openshift_inject": False,    # labeled empty CM; OpenShift injects cluster trust
    "engine_cpu_limit": None,        # e.g. "2" -> KUBERNETES_RESOURCES_LIMITS_CPU
    "engine_mem_limit": None,        # e.g. "8Gi" -> KUBERNETES_RESOURCES_LIMITS_MEMORY
    "engine_ephemeral_request_mb": None,  # int MB -> KUBERNETES_REQUESTS_EPHEMERAL_STORAGE
    "engine_ephemeral_limit_mb": None,    # int MB -> KUBERNETES_LIMITS_EPHEMERAL_STORAGE
    "crane_ephemeral_storage": None,      # e.g. "2Gi"
    # {NAME: value} for agent variables with no option here; see
    # bundle_env.extra_env.
    "extra_env": None,
}

# What a cluster bundle cannot carry: the docker agent's way of publishing a
# virtual service. BlazeMeter publishes them with disjoint variables per
# platform, so each set is the other platform's ignored options.
SV_DOCKER_IGNORED = {
    "sv_hostname": "HOSTNAME_OVERRIDE is a docker variable; a Kubernetes agent "
                   "returns a DNS-based URL and needs no hostname override",
    "sv_tls_cert": "a Kubernetes agent serves its endpoints through the ingress, "
                   "which reads the certificate from sv_tls_secret",
    "sv_tls_key": "a Kubernetes agent serves its endpoints through the ingress, "
                  "which reads the key from sv_tls_secret",
}

# What each format cannot carry, as {format: {option: why}}. Every format has
# an entry, and `{}` means it ignores nothing. An ignored option is kept,
# written to profile.json and named in the README, never refused. Served as
# core.ignored_options(), which the configure page hides fields by.
IGNORED_BY_FORMAT = {
    "manifests": dict(SV_DOCKER_IGNORED),
    "helm": dict(SV_DOCKER_IGNORED),
    "docker": {
        "platform": "there is no OpenShift/Kubernetes distinction on a docker host",
        "openshift_cluster": "there is no cluster, so no oc and no Route",
        "namespace": "containers are not namespaced",
        "service_account_name": "there is no ServiceAccount to run as",
        "service_account_create": "there is no ServiceAccount to create",
        "cluster_rbac": "there is no RBAC",
        "service_type": "KUBERNETES_SERVICE_USE_TYPE is a Kubernetes variable",
        "pull_secret": "the host's own docker login is what authenticates a pull",
        "run_as_user": "the container runs as root (-u 0) because that is what "
                       "opens the docker socket it starts engines through",
        "restrict_engines": "engine security context is a pod field",
        "tolerations": "scheduling is a Kubernetes concern",
        "node_selector": "scheduling is a Kubernetes concern",
        "engine_tolerations": "scheduling is a Kubernetes concern",
        "engine_node_selector": "scheduling is a Kubernetes concern",
        "engine_cpu_limit": "KUBERNETES_RESOURCES_LIMITS_CPU is a Kubernetes variable",
        "engine_mem_limit": "KUBERNETES_RESOURCES_LIMITS_MEMORY is a Kubernetes variable",
        "engine_ephemeral_request_mb": "ephemeral storage is a pod field",
        "engine_ephemeral_limit_mb": "ephemeral storage is a pod field",
        "crane_ephemeral_storage": "ephemeral storage is a pod field",
        "ca_existing_configmap": "there is no ConfigMap; the bundle mounts a file",
        "ca_configmap_key": "there is no ConfigMap; the bundle mounts a file",
        "ca_openshift_inject": "nothing injects a trust bundle into a container",
        "engines_per_node": "there is one host, and it is this one",
        "sv_ingress": "KUBERNETES_WEB_EXPOSE_TYPE is a Kubernetes variable; a "
                      "docker agent publishes under sv_hostname instead",
        "sv_subdomain": "KUBERNETES_WEB_EXPOSE_SUB_DOMAIN is a Kubernetes "
                        "variable; the docker agent's host is sv_hostname",
        "sv_tls_secret": "there is no Secret to name; this bundle mounts "
                         "sv_tls_cert and sv_tls_key as files",
        "sv_istio_gateway": "istio is a Kubernetes service mesh",
        "registry_auth": "the stubs are ConfigMap lines; a docker host authenticates "
                         "with its own docker login",
    },
}


# Options this generator no longer has, as {option: sentence for whoever set
# it}. Set to anything but off, one is refused, never ignored. Off is what
# every bundle does now, so an older profile.json that records it replays.
RETIRED_OPTIONS = {
    "crane_hook": "crane_hook was removed: bundles no longer carry the "
                  "crane-hook check pod. Remove crane_hook from the options "
                  "or profile, and run bzm-opl-gen doctor against the target "
                  "namespace to check the cluster before you deploy.",
}


def without_retired(options):
    """`options` minus RETIRED_OPTIONS. ValueError, with the option's own
    sentence, for one set to anything but off (False, None or absent)."""
    for key, why in RETIRED_OPTIONS.items():
        if (options or {}).get(key) not in (None, False):
            raise ValueError(why)
    return {k: v for k, v in (options or {}).items()
            if k not in RETIRED_OPTIONS}


def ignored_options(o):
    """This bundle's entry of IGNORED_BY_FORMAT, as {option: why}.

    A format may not refuse what it says it ignores, so every validator over an
    option in the table asks this first. An unknown format ignores nothing,
    leaving generate() to name it.
    """
    fmt = o.get("output_format") or DEFAULT_OPTIONS["output_format"]
    return IGNORED_BY_FORMAT.get(fmt, {})


def _quantity(o, key, default, parse, ignored=False):
    """Parse an engine quantity option, naming the option in the error.

    `default` is returned when unset (parsed if it is a string); `ignored`
    reads as unset.
    """
    value = None if ignored else o.get(key)
    if not value:
        return parse(default) if isinstance(default, str) else default
    try:
        return parse(value)
    except ValueError as e:
        raise ValueError(f"{key}: {e}") from None


def engine_size(o):
    """(cpu_millicores, mem_bytes) one engine claims.

    A format that ignores the limits gets the defaults, never the values, so it
    neither refuses nor reports a size it does not carry.
    """
    ignored = ignored_options(o)
    return (_quantity(o, "engine_cpu_limit", ENGINE_DEFAULT_CPU, parse_cpu,
                      "engine_cpu_limit" in ignored),
            _quantity(o, "engine_mem_limit", ENGINE_DEFAULT_MEM, parse_memory,
                      "engine_mem_limit" in ignored))


def engine_request(o):
    """(cpu, memory MiB) crane requests for each engine: the engine limits, so
    the scheduler and autoscaler place engines by what they actually use.
    Emitted as KUBERNETES_RESOURCES_DEFAULT_CPU/_MEM (memory is an integer in
    MiB, as BlazeMeter's own chart writes it)."""
    cpu, mem = engine_size(o)
    return format_cpu(cpu), str(mem // (1024 ** 2))


def engine_request_quantities(o):
    """engine_request as Kubernetes quantities, for comparing and printing."""
    cpu, mib = engine_request(o)
    return cpu, f"{mib}Mi"


def resolve_engine_limits(facts, o):
    """The engine limits the location implies, as an options patch.

    overrideCPU/overrideMemory are the engine's requests, so a bundle carrying
    a different limit packs engines by the wrong size. An explicit option wins,
    then the overrides, then the default the emitters apply. generate() merges
    the patch into the options, so profile.json replays the derived value.
    overrideMemory is MB, read as Mi.
    """
    patch = {}
    if "engine_cpu_limit" in ignored_options(o):
        return patch
    cpu = facts.get("override_cpu")
    mem = facts.get("override_memory")
    if not o.get("engine_cpu_limit") and cpu:
        patch["engine_cpu_limit"] = format_cpu(int(round(float(cpu) * 1000)))
    if (not o.get("engine_mem_limit") and mem
            and int(mem) >= ENGINE_MIN_DERIVED_MEM_MB):
        patch["engine_mem_limit"] = format_memory(int(mem) * 1024 * 1024)
    return patch


def crane_scheduling(o):
    """(nodeSelector, tolerations) for the crane pod itself."""
    return o.get("node_selector") or {}, o.get("tolerations") or []


def engine_scheduling(o):
    """(nodeSelector, tolerations) crane stamps on the engines it spawns.

    A set `engine_*` option wins outright rather than merging: two pools carry
    different labels, so a union would match neither. None inherits crane's
    placement; {} or [] means none, so crane can sit on a tainted pool while
    engines land anywhere.
    """
    selector = o.get("engine_node_selector")
    if selector is None:
        selector = o.get("node_selector")
    tolerations = o.get("engine_tolerations")
    if tolerations is None:
        tolerations = o.get("tolerations")
    return selector or {}, tolerations or []


def separate_pools(o):
    """Whether engines are aimed at different nodes from crane."""
    return (o.get("engine_node_selector") is not None
            or o.get("engine_tolerations") is not None)


def engines_per_node(o):
    """How many engines a node of the engine pool is meant to hold (default 1).

    One keeps measuring instruments from contending for CPU, NIC and cache; a
    node big enough for several at their full limits is cheaper.
    """
    n = o.get("engines_per_node")
    if n is None:
        return 1
    n = int(n)
    if n < 1:
        raise ValueError(f"engines_per_node must be at least 1, got {n}")
    return n


def is_openshift(o):
    """Whether the target cluster is OpenShift itself.

    `platform: openshift` is the security posture, which installs on vanilla
    Kubernetes too; this is the product, which decides `oc` or `kubectl`, the
    Route backend and the injected trust bundle.
    """
    return o["platform"] == "openshift" and bool(o.get("openshift_cluster", False))


def cli(o):
    """The command-line tool the bundle's instructions are written for."""
    return "oc" if is_openshift(o) else "kubectl"


def service_account(o):
    """The ServiceAccount the Deployment and both binding subjects name.

    Required rather than falling back to the namespace's `default` account,
    which would bind crane's Role to every pod there. None for a format with no
    ServiceAccount.
    """
    if "service_account_name" in ignored_options(o):
        return None
    name = str(o.get("service_account_name") or "").strip()
    if not name:
        raise ValueError(
            "service_account_name is required -- it names the account the "
            "Deployment runs as and the one the RoleBinding grants to, whether "
            "or not service_account_create emits the ServiceAccount itself. "
            "Pass --service-account <name> (the default is 'crane')")
    return name


def auth_token(o):
    """The supplied AUTH_TOKEN, or None where it is blank or a marker.

    Whitespace or a control character is refused, never stripped: crane sends
    the value as written, BlazeMeter answers 404, and the agent crash-loops as
    if its token were revoked. The message never echoes the value.
    """
    token = o.get("auth_token")
    if not token or is_placeholder(token):
        return None
    token = str(token)
    lines = token.splitlines()
    if len(lines) > 1:
        problem = f"has {len(lines)} lines"
    elif any(c.isspace() for c in token):
        problem = "contains whitespace"
    elif not token.isprintable():
        problem = "contains a control character"
    else:
        return token
    raise ValueError(
        f"auth_token (--auth-token) {problem} -- an AUTH_TOKEN is one word "
        "with no spaces, line breaks or control characters. Nothing was "
        "stripped: pass the token alone. Written as given, BlazeMeter refuses "
        "it and the agent restarts as if its token were revoked. The value is "
        "not shown here.")


def auto_update(o):
    """Resolve `auto_update` to the boolean AUTO_KUBERNETES_UPDATE carries.

    Unset is off, unlike BlazeMeter's own manifest: with the updater on, crane
    takes field ownership of its Deployment's image and strategy, and the next
    `helm upgrade` fails on a conflict --force-conflicts cannot resolve. `.get`
    because older profile.json files lack the key; the chart's
    `bzm-opl.autoUpdate` resolves it the same way.
    """
    chosen = o.get("auto_update")
    if isinstance(chosen, bool):
        return chosen
    if chosen is not None:
        raise ValueError(
            f"auto_update must be true, false or unset, got {chosen!r} -- "
            "unset means off, the default that keeps `helm upgrade` working")
    return False
