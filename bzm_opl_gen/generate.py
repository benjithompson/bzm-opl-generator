"""Render deployable manifests from templates + facts + customer options."""

import collections
import json
import os
import re
import shlex
from string import Template
from urllib.parse import quote

from .facts import image_refs, select_images
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
    # AUTO_KUBERNETES_UPDATE -- BlazeMeter's Kubernetes auto-updater, which
    # rewrites crane's own Deployment when a newer agent ships. Three states,
    # where None means "not asked, take the default" -- and the default is OFF;
    # see auto_update() for why this generator departs from BlazeMeter's own
    # manifest there. The chart's `autoUpdate` is the same tri-state with the
    # same default, which is why the overlay leaves it unset unless it was
    # chosen. (BlazeMeter's AUTO_UPDATE is a different variable for a different
    # mechanism; the docker format is where it is emitted, off this same
    # option -- see docker_env.)
    "auto_update": None,             # None | True | False
    "cluster_rbac": False,           # include optional ClusterRole/Binding files
    # The ServiceAccount crane runs as. The name is used either way -- what
    # `create` decides is only whether the bundle carries the ServiceAccount
    # object. Customers routinely have to run under an account their platform
    # team owns, and with create off the deployment and the RBAC subjects still
    # name it, because it is already there.
    "service_account_name": "crane",
    "service_account_create": True,
    "service_type": "CLUSTERIP",    # CLUSTERIP | NODEPORT
    # Service virtualization ingress. Only meaningful for a location whose
    # funcIds include mockServices; see _sv_cfg for why all three are required
    # together. service_type is not among them -- see the note there.
    # None is "not answered" and is refused for such a location; SV_INGRESS_NONE
    # is "answered: performance only".
    "sv_ingress": None,              # None, SV_INGRESS_NONE, or an SV_INGRESS_TYPE
    "sv_subdomain": None,            # e.g. apps.example.com -- endpoint host suffix
    "sv_tls_secret": None,           # wildcard TLS secret, in the agent namespace
    "sv_istio_gateway": None,        # optional; unset -> a Gateway per virtual service
    # {"http", "https", "no_proxy", "username", "password"} -- credentials are
    # embedded in the proxy URL (user:pass@host, per BlazeMeter docs) and the
    # URL moves into the Secret when use_secret is on.
    "proxy": None,
    "run_as_user": 1337,             # k8s platform only (openshift: SCC assigns)
    # Engines crane spawns drop all capabilities and inherit crane's UID:GID.
    # On by default on every platform -- crane's own default is privileged, and
    # a privileged engine is refused by anything that enforces admission. Off is
    # for an image that genuinely needs a capability; see _configmap.
    "restrict_engines": True,
    # Real-cluster scheduling / trust / sizing (all optional):
    "tolerations": None,             # k8s toleration list -> crane pod + engines
    "node_selector": None,           # {"label": "value"} -> crane pod + engines
    # Engines only, overriding the two above. Unset (None) means the engines
    # share the crane pod's placement -- the single-pool shape, and still the
    # default. An *empty* {} or [] is not the same as unset: it says the engines
    # take no selector/toleration even though crane has one, which is how you
    # keep crane on a tainted infra pool and let engines land anywhere.
    "engine_tolerations": None,      # k8s toleration list -> engines only
    "engine_node_selector": None,    # {"label": "value"} -> engines only
    # How many engines a node of the engine pool is meant to hold. Reaches no
    # manifest -- it sizes the node pool recipe and is what doctor judges
    # packing against. Unset means 1.
    "engines_per_node": None,
    # CA trust -- pick ONE mode:
    "ca_bundle": None,               # inline PEM -> generator creates the ConfigMap
    "ca_existing_configmap": None,   # name of a ConfigMap the platform team owns/rotates
    "ca_configmap_key": None,        # bundle file key within it (default ca-bundle.crt)
    "ca_openshift_inject": False,    # labeled empty CM; OpenShift injects cluster trust
    "engine_cpu_limit": None,        # e.g. "2" -> KUBERNETES_RESOURCES_LIMITS_CPU
    "engine_mem_limit": None,        # e.g. "8Gi" -> KUBERNETES_RESOURCES_LIMITS_MEMORY
    "engine_ephemeral_request_mb": None,  # int MB -> KUBERNETES_REQUESTS_EPHEMERAL_STORAGE
    "engine_ephemeral_limit_mb": None,    # int MB -> KUBERNETES_LIMITS_EPHEMERAL_STORAGE
    # Crane's own pod, unset -> CRANE_EPHEMERAL_STORAGE. One value, both fields;
    # see the constant for why they are not separately settable.
    "crane_ephemeral_storage": None,      # e.g. "2Gi"
    # github.com/Blazemeter/crane-hook: a one-shot Pod (plus its own read-only
    # Role and RoleBinding) that checks the cluster against what the agent needs
    # and exits 0 or 1. Off by default -- it is a check, not part of the agent,
    # and a bundle that quietly carried an extra Pod would surprise whoever
    # applies it.
    "crane_hook": False,
}

# BlazeMeter's documented engine footprint -- the fallback when the customer
# has not pinned engine limits.
ENGINE_DEFAULT_CPU = "2"
ENGINE_DEFAULT_MEM = "8Gi"
# ...and on disk, per concurrent engine (the docs quote decimal GB).
ENGINE_DISK_GB = 60
ENGINE_TMP_GB = 40

# Crane's own container resources, substituted into templates/deployment.yaml so
# these are the single source. They matter beyond that pod: doctor spends them
# out of node capacity, and a LimitRange the customer already has in the
# namespace has to clear them or the crane pod is rejected at admission.
# Values are the official helm-crane chart's resourcesCrane.
CRANE_CPU_REQUEST = "250m"
CRANE_MEM_REQUEST = "512Mi"
CRANE_CPU_LIMIT = "1"
CRANE_MEM_LIMIT = "2Gi"

# Ephemeral storage is one value, used for BOTH the request and the limit, and
# that is the whole point of it not being a pair like the two above.
#
# The original 100Mi request / 1Gi limit describes a pod that does not exist,
# and nothing on a working cluster ever said so. Crane sits at ~161MiB with
# 107MiB of that in /tmp within
# seconds of starting, so 100Mi never described crane on any platform -- it was
# a scheduling hint below steady-state usage, and elsewhere only the 1Gi limit
# kept the pod alive. On GKE Autopilot the limit is not the customer's to set:
# Autopilot rewrites it down to the request, so the pod came back 100Mi/100Mi
# and was evicted in ~12s -- `Pod ephemeral local storage usage exceeds the
# total limit of containers 100Mi` -- forever, since each replacement did the
# same. CPU and memory are NOT rewritten that way (250m/512Mi requests against
# 1/2Gi limits survive untouched in the same pod), so this is specific to
# ephemeral storage rather than a general requests==limits rule.
#
# Keeping one value means the number a customer names is the number that binds
# on every platform, instead of a gap that is headroom on one and a lie on
# another.
CRANE_EPHEMERAL_STORAGE = "1Gi"

# What crane puts on an engine pod's requests when the *location* says nothing.
#
# These were long believed to be hardcoded and unchangeable -- "crane stamps
# them, nothing can move them" -- and the LimitRange removal, the packing
# problem and the whole node-pool recipe were argued from that. It is wrong,
# and a live run settled it: with the location's overrideCPU=1 and
# overrideMemory=4096 against a bundle asking for 2 CPU / 8Gi limits, the engine
# pod came back
#
#     requests {cpu: 1, memory: 4Gi}   limits {cpu: 2, memory: 8Gi}
#
# So the two knobs are not rivals for one field -- they set *different* fields.
# The bundle's KUBERNETES_RESOURCES_LIMITS_CPU/_MEMORY set the limits; the
# location's overrideCPU/overrideMemory set the requests (overrideMemory in MB).
# These values are simply crane's default for a location that leaves both unset,
# which is 165 of 171 locations on a real account -- so the 250m/256Mi seen
# everywhere was the *default*, not a ceiling.
#
# What survives from the old reasoning: a LimitRange still cannot help, because
# crane sets requests explicitly either way and `defaultRequest` only fills
# fields a pod leaves unset. What does not survive: "nothing can change them".
# Setting the location's overrides to match the engine limits is the direct fix
# for engine packing, and it beats every node-pool trick -- see requests_note().
ENGINE_DEFAULT_REQUEST_CPU = "250m"
ENGINE_DEFAULT_REQUEST_MEM = "256Mi"

# Kept as aliases: `livetest` profiles written by older versions and any caller
# outside this repo still name them, and the values are unchanged -- only the
# claim about them was wrong.
ENGINE_STAMPED_REQUEST_CPU = ENGINE_DEFAULT_REQUEST_CPU
ENGINE_STAMPED_REQUEST_MEM = ENGINE_DEFAULT_REQUEST_MEM


def engine_requests(facts):
    """(cpu, memory) an engine pod will actually request, as strings, given what
    the location carries -- or the defaults when it carries nothing.

    `overrideMemory` is in MB. Its unit is the one thing here not to trust from
    the field alone: the same account holds 32, 4000 and 8196 for it, so a value
    that looks like GB probably is somebody's mistake rather than a different
    unit. Read it, report it, do not rescale it.
    """
    cpu = facts.get("override_cpu")
    mem = facts.get("override_memory")
    return (f"{cpu}" if cpu else ENGINE_DEFAULT_REQUEST_CPU,
            f"{mem}Mi" if mem else ENGINE_DEFAULT_REQUEST_MEM)


def _quantity(o, key, default, parse, ignored=False):
    """Parse an engine quantity option, naming the option in the error --
    quantity's own message only carries the bad value. `default` is returned
    as-is when the option is unset, so a caller with an already-parsed fallback
    does not have to format it back into a string for re-parsing.

    `ignored` is "this format has no such field", which reads as unset."""
    value = None if ignored else o.get(key)
    if not value:
        return parse(default) if isinstance(default, str) else default
    try:
        return parse(value)
    except ValueError as e:
        raise ValueError(f"{key}: {e}") from None


def ignored_options(o):
    """The options this bundle's format cannot carry, as {option: why}.

    `DOCKER_IGNORED` for a docker bundle and nothing for the other two. One
    reader for a rule that has to hold in three places, because remembered it
    does not: **a format may not refuse what it says it ignores.** An ignored
    option has no control on the configure page for that format, so a refusal
    over its value is a blocker with nothing on screen to clear -- an imported
    profile carrying an engine limit blocked the download exactly that way.

    Ignored is not discarded, and nothing here drops a value: it stays in the
    options, reaches `profile.json`, and the bundle's README names it (see
    `_docker_ignored`). What this decides is only what may be *read* -- and so
    what may be complained about.

    `o.get` rather than `o[...]`: doctor, plan and livetest all call the
    validators below with option dicts of their own that were never merged over
    the defaults, and for those "no format stated" means the Kubernetes one.
    """
    return DOCKER_IGNORED if o.get("output_format") == "docker" else {}


def engine_size(o):
    """(cpu_millicores, mem_bytes) one engine actually claims. doctor.py imports
    this to compare the claim against what a node can hold.

    A format that ignores the two limits gets the defaults, and never the
    values: reading them would refuse a malformed one (see ignored_options),
    and reporting them would have a docker bundle's README advertise an engine
    size its own footer says is not carried."""
    ignored = ignored_options(o)
    return (_quantity(o, "engine_cpu_limit", ENGINE_DEFAULT_CPU, parse_cpu,
                      "engine_cpu_limit" in ignored),
            _quantity(o, "engine_mem_limit", ENGINE_DEFAULT_MEM, parse_memory,
                      "engine_mem_limit" in ignored))


def resolve_engine_limits(facts, o):
    """The engine limits this location implies, as an options patch -- empty
    where there is nothing to derive.

    Requests and limits are one figure with two writers (#132), and the
    location is where it is set: overrideCPU/overrideMemory are the engine
    pod's *requests*, so a bundle that carried a different size than the
    location requests is the packing gap all over again. Resolution: an
    explicit option wins (the CLI, the livetest overlay, a replayed profile --
    all speak through options); then the location's overrides; then the
    documented default, which the emitters fall back to on their own.

    generate() merges the patch into the resolved options, so profile.json
    records the derived value and a replay against different facts -- the
    location was resized after the bundle was cut -- reproduces the bundle
    rather than re-deriving. doctor.evaluate applies the same patch, so the
    preflight certifies the size the bundle will actually carry.

    overrideMemory is MB read as Mi -- the planner's own equivalence
    (plan.capacity_plan emits 8192 for an 8Gi engine) -- and formatted the way
    every other manifest quantity is, so 4096 arrives as 4Gi and an odd 8196
    stays 8196Mi rather than being rounded to a lie. A format that ignores the
    two keys derives nothing: the value would reach no manifest and only add a
    README line about an option nobody set (see ignored_options)."""
    patch = {}
    if "engine_cpu_limit" in ignored_options(o):
        return patch
    cpu = facts.get("override_cpu")
    mem = facts.get("override_memory")
    if not o.get("engine_cpu_limit") and cpu:
        patch["engine_cpu_limit"] = format_cpu(int(round(float(cpu) * 1000)))
    if not o.get("engine_mem_limit") and mem:
        patch["engine_mem_limit"] = format_memory(int(mem) * 1024 * 1024)
    return patch


def crane_scheduling(o):
    """(nodeSelector, tolerations) for the crane pod itself."""
    return o.get("node_selector") or {}, o.get("tolerations") or []


def engine_scheduling(o):
    """(nodeSelector, tolerations) crane stamps on the engines it spawns.

    `engine_*` unset means the engines inherit the crane pod's placement, which
    is the one-pool shape and the behaviour every bundle generated before these
    options had. Set, it wins outright rather than merging: a two-pool cluster
    puts crane and the engines on nodes with *different* labels and taints, so
    a union of the two selectors would match neither pool.

    Unset and empty are deliberately different, and both are reachable. `None`
    is "engines go wherever crane goes"; `{}` / `[]` is "engines take no
    selector/toleration of their own", which is what a customer wants when
    crane sits on a tainted infra pool and the engines are free to land on the
    general one. Collapsing the two would make that second case unsayable.
    """
    selector = o.get("engine_node_selector")
    if selector is None:
        selector = o.get("node_selector")
    tolerations = o.get("engine_tolerations")
    if tolerations is None:
        tolerations = o.get("tolerations")
    return selector or {}, tolerations or []


def separate_pools(o):
    """Whether engines are aimed at different nodes from crane. Drives the node
    pool recipe in the bundle, and stops doctor spending crane's resources out
    of a pool crane is not on."""
    return (o.get("engine_node_selector") is not None
            or o.get("engine_tolerations") is not None)


# BlazeMeter's own registry: the default DOCKER_REGISTRY, and what the
# bundle READMEs name when no private registry is configured.
PUBLIC_REGISTRY = "gcr.io/verdant-bulwark-278"

# crane-hook's own image and RBAC names. The image lives in the same registry as
# everything else BlazeMeter ships, so a private-registry bundle mirrors it with
# the rest -- see _mirror_script. The Role name is ours rather than upstream's
# `test-hookrole`: the bundle already namespaces every object it owns, and a
# name that says which tool made it is what stops `kubectl delete role
# test-hookrole` in six months' time being a guess.
HOOK_IMAGE_REPO = "cranehook"
HOOK_IMAGE_TAG = "latest"
HOOK_ROLE_NAME = "bzm-cranehook"
HOOK_FILE = "bzm_cranehook.yaml"

CA_MOUNT_PATH = "/var/cm"
CA_FILENAME = "ca-bundle.crt"
CA_CONFIGMAP = "blazemeter-cacerts"

# The funcId BlazeMeter puts on a location that serves virtual services, and so
# the one that makes the ingress options mandatory. A tuple, not a string,
# because it is served to the UI as a set and was two entries until sv-bridge
# was retired -- a second one returning is a data change, not a code change.
SV_FUNC_IDS = ("mockServices",)


class SvBackend(collections.namedtuple(
        "SvBackend", "group resources creates via_ingress_class nodeport_ok")):
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

    `nodeport_ok` records whether the backend survives service_type=NODEPORT,
    and it is per-backend because crane fills the port field two different ways.
    nginx and openshift write a *constant* -- 8080 -- which stays valid: an
    Ingress backend resolves against the Service's port, which NODEPORT moves
    from 80 to 8080, and a Route resolves against targetPort, which never moves.
    contour and istio instead derive the port from the Service and take its
    **nodePort**, which is a number no client ever reaches the ingress on. All
    four were deployed live to settle it (#60); see docs/service-virtualization.md
    for what each one did.

    One case under `istio` is refused without having been measured, and
    deliberately: with sv_istio_gateway set crane reuses a Gateway the customer
    already owns instead of creating one, and the Gateway is the object that
    carried the bad port -- its VirtualService names no port at all. That
    variant may well work. It is refused with the rest because a rule a customer
    can predict ("istio does not do NODEPORT") is worth more here than the
    narrowest possible one ("istio does not do NODEPORT unless you also set a
    gateway name"), and CLUSTERIP costs them nothing. #63 settles it if the
    narrower rule ever earns its keep.
    """
    __slots__ = ()


SV_INGRESS_BACKENDS = {
    "nginx": SvBackend("networking.k8s.io", ["ingresses"], "Ingress", True,
                       nodeport_ok=True),
    "istio": SvBackend("networking.istio.io", ["gateways", "virtualservices"],
                       "Gateway + VirtualService", False, nodeport_ok=False),
    "contour": SvBackend("projectcontour.io", ["httpproxies"],
                         "HTTPProxy", False, nodeport_ok=False),
    # routes/custom-host is not padding: OpenShift gates spec.host behind its
    # own create, and crane sets spec.host. Without it the create comes back 422
    # "you do not have permission to set the host field of the route", no Route
    # appears, and the virtual service stalls with the mock pod healthy at 1/1.
    # Proven by A/B on a live cluster -- and note `auth can-i create
    # routes/custom-host` answers yes either way, so it cannot be used to check.
    "openshift": SvBackend("route.openshift.io",
                           ["routes", "routes/custom-host"], "Route", False,
                           nodeport_ok=True),
}
# Derived, not a second list to keep in step. Crane's binary names five
# implementations -- kubernetes_{base,contour,istio,nginx,openshift}_web_expose
# _service -- and all four real ones are backends above; `base` is their shared
# parent, not a value. The `INGRESS` value BlazeMeter's env reference documents
# is deliberately absent: it creates no object at all and stalls at
# WAITING_FOR_DOMAIN.
SV_INGRESS_TYPES = tuple(SV_INGRESS_BACKENDS)

# The third state of `sv_ingress`, and the reason it is a value rather than a
# separate flag: unset means "nobody has answered yet", which is what makes the
# refusal below right for a mockServices location, and this means "answered: no
# ingress, deliberately". Collapsing the two -- the shape this generator refuses
# everywhere else -- is what made an SV-capable location impossible to configure
# for performance alone, which plenty of accounts want: a location often carries
# both funcIds because somebody enabled them together, and the customer runs
# tests on it and no virtual services at all.
#
# What it buys is only the refusal. Nothing else changes: no SV RBAC, no
# KUBERNETES_WEB_EXPOSE_* env, and the mock images the location's funcIds select
# are still in the ConfigMap, because those are read off the location and not
# off this option. A virtual service deployed to a location generated this way
# still stalls at WAITING_FOR_DOMAIN -- that is the trade being declared, not a
# problem being fixed.
SV_INGRESS_NONE = "none"
assert SV_INGRESS_NONE not in SV_INGRESS_BACKENDS  # a backend may not claim it


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
    if ingress == SV_INGRESS_NONE:
        # Declared, so not refused. See SV_INGRESS_NONE for what is and is not
        # being said by it.
        return None
    if not ingress:
        if sv_funcs:
            raise ValueError(
                f"location advertises funcId(s) {', '.join(sv_funcs)} but no "
                "service-virtualization ingress was configured. Pass sv_ingress "
                f"({'|'.join(SV_INGRESS_TYPES)}) + sv_subdomain + sv_tls_secret, "
                "or virtual services will deploy and stall at WAITING_FOR_DOMAIN. "
                f"To generate this location for performance testing alone, pass "
                f"sv_ingress={SV_INGRESS_NONE}: the bundle is then the same as a "
                "non-SV location's, and virtual services deployed to it stall."
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
    # Per-backend, because the answer differs per backend and was measured for
    # each (#60). Not about node reads -- crane's is denied under NODEPORT on
    # every backend and it publishes anyway. It is about the port crane writes
    # into the object: see SvBackend.nodeport_ok.
    if o["service_type"] != "CLUSTERIP" and not SV_INGRESS_BACKENDS[ingress].nodeport_ok:
        raise ValueError(
            f"sv_ingress={ingress} requires service_type=CLUSTERIP, got "
            f"{o['service_type']}. Crane fills this backend's port field from "
            f"the Service's nodePort, which nothing reaches the ingress on: the "
            f"{SV_INGRESS_BACKENDS[ingress].creates} is written, the mock runs "
            "1/1, BlazeMeter advertises the endpoint, and the endpoint does not "
            "serve. Measured live -- contour reports `unresolved service "
            "reference` and answers 503; istio's gateway ends up listening on "
            "the nodePort alone and nothing answers at all. Fix: use "
            "service_type=CLUSTERIP, which is the default and changes nothing "
            f"else about a {ingress} deployment. (sv_ingress=nginx and "
            "openshift do work on NODEPORT -- they write a constant port -- but "
            "switching backend to get there means switching ingress controller, "
            "which is the bigger change of the two.)")
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


# The options that configure CA trust, and what each mode is called where
# somebody picks one. Exactly one of them may hold a value (see _ca_cfg), and a
# caller offering to write one has to know which of the others already does --
# the web UI's suggestion panel is that caller -- so the set and the words for
# it are stated here, beside the refusal, rather than restated wherever a mode
# is offered.
CA_MODES = {
    "ca_existing_configmap": "an existing ConfigMap",
    "ca_bundle": "an inline PEM",
    "ca_openshift_inject": "OpenShift injection",
}


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
    # A format that carries no ConfigMap has only the inline PEM, and the two
    # modes that name one are not competing modes there -- they are fields it
    # does not have (see ignored_options). Counted, a bundle configured for
    # Kubernetes and then switched to docker refuses "choose one CA mode" over
    # a ConfigMap name the page for that format never showed.
    ignored = ignored_options(o)
    active = [k for k in CA_MODES if o[k] and k not in ignored]
    if len(active) > 1:
        raise ValueError("choose one CA mode: ca_bundle (inline PEM) | "
                         "ca_existing_configmap | ca_openshift_inject")
    if not active:
        return None
    if active[0] == "ca_existing_configmap":
        return {"cm": o["ca_existing_configmap"],
                "key": o["ca_configmap_key"] or CA_FILENAME, "mode": "existing"}
    # inline + inject both use our own ConfigMap; inject's key is fixed to
    # ca-bundle.crt (the key OpenShift writes into labeled ConfigMaps).
    return {"cm": CA_CONFIGMAP, "key": CA_FILENAME,
            "mode": "inline" if active[0] == "ca_bundle" else "inject"}


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


def proxy_env(o, no_proxy=DEFAULT_NO_PROXY):
    """The proxy environment a pod needs, as {NAME: value}, credentials already
    embedded in the URLs. One builder for the four places that need it: the
    ConfigMap, the Secret, doctor's probe pod, and the docker command.

    `no_proxy` is what an unanswered `no_proxy` falls back to, and the docker
    format passes its own: the default names `kubernetes.default`, which is the
    cluster's API service and nothing a Docker host can resolve."""
    p = o.get("proxy") or {}
    env = {}
    for name, key in (("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https")):
        if p.get(key):
            env[name] = proxy_url(p[key], p)
    if p:
        env["NO_PROXY"] = p.get("no_proxy", no_proxy)
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
    if o["restrict_engines"]:
        # Crane's own default engine pod is privileged, and nothing in these two
        # keys is OpenShift-specific -- they were only ever emitted there because
        # that is where a rejection was noticed first. Any cluster that enforces
        # admission rejects the privileged engine: restricted PSA on plain k8s,
        # and GKE Autopilot, whose Warden denies the Job outright
        # (`[denied by autogke-disallow-privilege]`). The failure is the
        # expensive kind -- crane is online and healthy, the location looks
        # ready, and the run hangs at BOOT_STARTING until it times out, because
        # the pod that was refused is one crane creates and no manifest here
        # names.
        #
        # What has to tolerate this is images, not clusters: a shape the
        # strictest cluster admits is admitted everywhere by construction, and
        # once admitted the container's identity comes from the spec. So the
        # evidence is per image, and docs/hardened-engines.md is where it is
        # kept -- which images have run under this, what was read from inside
        # them, and the one thing that does vary by platform, namely whether an
        # SCC assigns the UID crane passes down or run_as_user pins it.
        lines += [
            "  # Engines inherit crane's UID:GID and drop all capabilities, so the",
            "  # pods crane spawns pass restricted PodSecurity, OpenShift's",
            "  # restricted-v2 SCC and GKE Autopilot's Warden. Turn off only for an",
            "  # image that genuinely needs a capability (--no-restrict-engines).",
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
        ]
        if o["registry_auth"]:
            lines += [
                "  # Crane-side auth for engine image pulls (or use cluster pull secrets):",
                "  # DOCKER_REGISTRY_USERNAME: <user>",
                "  # DOCKER_REGISTRY_PASSWORD: <password>",
                "  # DOCKER_REGISTRY_EMAIL: <email>",
            ]
    else:
        lines.append(f"  DOCKER_REGISTRY: {PUBLIC_REGISTRY}")
    # Emitted after the registry either way: what a self-update would pull from
    # is the registry above, so the two read together. See auto_update() for
    # why off is the default here and true in BlazeMeter's own manifest.
    if auto_update(o):
        lines += [
            "  # Auto-update ON (--auto-update). Crane rewrites this Deployment --",
            "  # its image, and .spec.strategy to RollingUpdate -- when BlazeMeter",
            "  # ships a newer agent. It takes field ownership doing so, which is",
            "  # what makes a later `helm upgrade` conflict; with kubectl, expect",
            "  # the image you apply to be replaced by whatever is current."
            + ("\n  # The newer tag has to be in your registry before crane looks"
               "\n  # for it." if o["private_registry"] else ""),
            "  AUTO_KUBERNETES_UPDATE: 'true'",
        ]
    else:
        lines += [
            "  # Crane leaves this Deployment alone, so the image above is the",
            "  # version that runs until you re-generate and re-apply. Keeping the",
            "  # agent current is your job -- one far enough behind loses support.",
            "  # --auto-update hands that back to crane, at the cost of it owning",
            "  # fields Helm and kubectl then fight it for.",
            "  AUTO_KUBERNETES_UPDATE: 'false'",
        ]
    if o["proxy"]:
        if _proxy_has_creds(o) and o["use_secret"]:
            lines.append("  # HTTP(S)_PROXY embed credentials -> kept in blazemeter-secret.")
        else:
            if _proxy_has_creds(o):
                lines.append("  # WARNING: proxy credentials below are plaintext -- anyone who can")
                lines.append("  # read ConfigMaps sees them. Regenerate with use_secret=true.")
            lines += [f"  {k}: \"{v}\"" for k, v in proxy_env(o).items()
                      if k != "NO_PROXY"]
        lines.append(f"  NO_PROXY: {proxy_env(o)['NO_PROXY']}")
    eng_sel, eng_tol = engine_scheduling(o)
    split = separate_pools(o)
    if eng_tol:
        lines += [
            "  # Tolerations crane stamps on the engines it spawns." if split else
            "  # Engines inherit the crane pod's tolerations via this env.",
            f"  KUBERNETES_TOLERATIONS_JSON: '{json.dumps(eng_tol)}'",
        ]
    if eng_sel:
        if split:
            lines.append("  # Engines are pinned to their own node pool, separate from crane's.")
        lines.append(f"  KUBERNETES_NODE_SELECTOR_JSON: '{json.dumps(eng_sel)}'")
    # Always emitted, defaults included. doctor and the planner certify
    # engine_size(), which falls back to ENGINE_DEFAULT_CPU/MEM -- a ConfigMap
    # that omitted these when the options were unset shipped engines with no
    # limits at all while the preflight vouched for 2/8Gi, and a live run had
    # one OOMKilled 4s after start (#132). Unset means the documented default,
    # never "whatever crane does with no env".
    lines.append(f"  KUBERNETES_RESOURCES_LIMITS_CPU: "
                 f"\"{o['engine_cpu_limit'] or ENGINE_DEFAULT_CPU}\"")
    lines.append(f"  KUBERNETES_RESOURCES_LIMITS_MEMORY: "
                 f"\"{o['engine_mem_limit'] or ENGINE_DEFAULT_MEM}\"")
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
    """tolerations / nodeSelector for the crane pod itself. Engines are placed
    by the KUBERNETES_*_JSON env in the ConfigMap instead -- crane stamps those
    on the pods it spawns -- so the two pools are reached by two different
    mechanisms, and engine_scheduling() is what decides whether they differ."""
    sel, tol = crane_scheduling(o)
    out = ""
    if tol:
        out += "      tolerations:\n" + _indent_yaml(tol, 8) + "\n"
    if sel:
        out += "      nodeSelector:\n" + "\n".join(
            f"        {k}: \"{v}\"" for k, v in sel.items()) + "\n"
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


# -- node pool recipe ---------------------------------------------------------

NODEPOOLS_FILE = "nodepools.md"

# What a node spends on itself before a pod sees any of it: the kubelet's
# kube-reserved/system-reserved plus the eviction threshold. Every managed
# distribution reserves on a sliding scale and none of them agree, so this is a
# working allowance for sizing advice, not a number to compute a manifest from
# -- which is why nothing outside this file uses it.
NODE_OVERHEAD_CPU = 1000        # millicores
NODE_OVERHEAD_MEM = 2 * (1024 ** 3)

# System pods that land on a node in a *tainted* pool, counted into maxPods
# because a ceiling that forgets them stops the node's own agents from starting
# and the node never becomes useful.
#
# Measured, not assumed, and the three ways of counting disagree wildly -- on a
# GKE 1.35 REGULAR cluster: `kubectl get ds -A` reports 32; 31 of those tolerate
# any taint; and exactly 4 DaemonSet pods actually land on a node (fluentbit-gke,
# gke-metrics-agent, node-local-dns, pdcsi-node), plus kube-proxy as a static
# pod and a managed-prometheus collector. The 32 are mostly *variants* gated by
# nodeAffinity -- GPU plugins, Windows builds, metrics agents selected by
# machine size -- so counting DaemonSet objects overestimates by 8x.
#
# A taint is what makes this predictable at all: it repels the Deployments
# (kube-dns, metrics-server, konnectivity-agent, l7-default-backend) that
# otherwise take another 5-6 slots per node on an untainted pool.
#
# So this is a starting point for arithmetic, and the recipe tells the reader to
# count pods on a real node of their own pool rather than trust it.
TYPICAL_SYSTEM_PODS = 6

# GKE refuses --max-pods-per-node below 8: "Maximum pods per node must be at
# least 8 and at most 256". Verified against the API, not read off a doc page.
#
# It is the reason this file cannot simply promise one engine per node. With
# TYPICAL_SYSTEM_PODS system pods already on the node, the floor leaves
# 8 - 6 = 2 engine slots, and no node pool setting closes that further: crane
# exposes no anti-affinity, and the engine's requests are too small to bind.
# So on GKE the honest instruction is "size the node for the engines the floor
# still permits", which is what _engines_per_node() works out.
GKE_MIN_MAX_PODS = 8


def engines_per_node(o):
    """How many engines a node of the engine pool is meant to hold.

    One by default, which is the conservative answer: engines are measuring
    instruments, and two sharing a node contend for CPU, NIC and cache in ways
    that show up as latency the load generator invented rather than latency the
    system produced. But it is a choice, not a law -- a node big enough to run
    several at their full limits satisfies the real rule, and costs less,
    because every node spends about a CPU and 2Gi on system pods before any
    engine arrives.
    """
    n = o.get("engines_per_node")
    if n is None:
        return 1
    n = int(n)
    if n < 1:
        raise ValueError(f"engines_per_node must be at least 1, got {n}")
    return n


def _engines_per_node(max_pods, floor=0):
    """Engines that can still share a node once `floor` raises maxPods above
    what we asked for. Never below 1 -- a floor that leaves no room at all is a
    pool that cannot run an engine, which is a different (and louder) problem
    than one that packs them."""
    return max(max(max_pods, floor) - TYPICAL_SYSTEM_PODS, 1)


def _taints_from_tolerations(tolerations):
    """The taints a pool must carry for these tolerations to be the thing that
    gets past it. `Exists` with no value gives a valueless taint, which is
    written `key:effect` -- `key=:effect` is a taint with an empty *value* and
    is not the same object."""
    out = []
    for tol in tolerations:
        key = tol.get("key")
        if not key:
            continue                  # tolerates everything; no taint to derive
        effect = tol.get("effect") or "NoSchedule"
        if tol.get("operator") == "Exists":
            out.append(f"{key}:{effect}")
        else:
            out.append(f"{key}={tol.get('value', '')}:{effect}")
    return out


def _nodepools_md(facts, o):
    """The node pool shape the split scheduling options describe, as commands.

    This file exists because the interesting half of a two-pool location is not
    in the manifests at all. The manifests can say *which* nodes an engine may
    land on; they cannot say *how many* engines land on one, because crane
    engine pod's requests come from the *location* (overrideCPU /
    overrideMemory), defaulting to 250m/256Mi when it sets neither -- and both
    the scheduler and the cluster autoscaler decide on requests. So a dedicated
    pool whose location leaves them unset still packs engines onto the first
    node it scales up, and the run reports numbers from engines that were never
    given the CPU they were configured for. The recipe therefore leads with the
    overrides, and falls back to the pool's own maxPods, which is a node pool
    property on every distribution and a manifest property on none -- hence a
    recipe rather than another template.
    """
    cpu, mem = engine_size(o)
    eng_sel, eng_tol = engine_scheduling(o)
    crane_sel, crane_tol = crane_scheduling(o)
    slots = facts.get("slots")
    taints = _taints_from_tolerations(eng_tol)
    per_node = engines_per_node(o)
    max_pods = TYPICAL_SYSTEM_PODS + per_node

    # An engine's *limits* are what has to fit, since that is what it runs at
    # once it is on the node -- the requests it was scheduled by are the lie
    # this whole file is about.
    node_cpu = format_cpu(cpu + NODE_OVERHEAD_CPU)
    node_mem = format_memory(mem + NODE_OVERHEAD_MEM)

    sel_pairs = ",".join(f"{k}={v}" for k, v in eng_sel.items()) or "(none set)"
    crane_desc = (", ".join(f"{k}={v}" for k, v in crane_sel.items())
                  or "no selector -- crane lands on any node")

    lines = [
        f"# Node pools for {facts.get('harbor_name') or facts['harbor_id']}",
        "",
        "This bundle places crane and its engines on **different nodes**. The",
        "manifests carry the labels and tolerations; the pools themselves are",
        "yours to create, and this is what they need to be.",
        "",
        "| | crane pool | engine pool |",
        "|---|---|---|",
        "| holds | 1 pod, always | 0-n pods, only during a run |",
        f"| selector | {crane_desc} | `{sel_pairs}` |",
        f"| taints | none needed | {', '.join(f'`{t}`' for t in taints) or 'none set'} |",
        f"| per node | {CRANE_CPU_LIMIT} CPU / {CRANE_MEM_LIMIT} | "
        f"{format_cpu(cpu)} CPU / {format_memory(mem)} per engine |",
        "| autoscaling | fixed, 1-2 nodes | min 0, scales with the run |",
        "",
        "## Why two pools",
        "",
        "Crane is a small orchestrator that must not move: it holds the",
        f"location's registration, and it needs {CRANE_CPU_LIMIT} CPU / {CRANE_MEM_LIMIT} to do it.",
        f"An engine needs {format_cpu(cpu)} CPU / {format_memory(mem)} and exists only for the length",
        "of a run. Sharing one pool means either paying for engine-sized nodes",
        "around the clock, or letting crane sit on a node the autoscaler wants",
        "to remove -- so the pool never drains and the saving never arrives.",
        "",
        "## Set the location's CPU/memory overrides first",
        "",
        "**This is the fix. Everything below is a backstop for it.**",
        "",
        "The scheduler and the cluster autoscaler place pods by their",
        "**requests**, not their limits. An engine's limits come from this bundle",
        f"({format_cpu(cpu)} / {format_memory(mem)}); its *requests* come from the location, as",
        "`overrideCPU` and `overrideMemory` under Settings -> Private Locations.",
        "They are different fields, not rival settings for one field --",
        "confirmed on a live run, where a location at `overrideCPU: 1` /",
        "`overrideMemory: 4096` and a bundle at 2 CPU / 8Gi produced an engine pod",
        "with `requests {cpu: 1, memory: 4Gi}` and `limits {cpu: 2, memory: 8Gi}`.",
        "",
        f"Left unset -- as {ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM} -- an engine asks the scheduler for a",
        "fraction of what it will use, so the autoscaler adds **one** node and",
        "packs the whole run onto it. The engines then throttle against each",
        "other and the test reports the load generator's latency, not the",
        "system's.",
        "",
        f"So set them to match the limits this bundle asks for: **overrideCPU: {format_cpu(cpu)}**,",
        f"**overrideMemory: {mem // (1024 ** 2)}** (it is in MB). Then requests equal limits, the",
        "scheduler places engines truthfully, the autoscaler grows the pool by",
        "the right number of nodes, and none of the `maxPods` arithmetic below",
        "has to carry the weight on its own.",
        "",
        "A LimitRange still cannot do this: crane sets the requests explicitly",
        "either way, and `defaultRequest` only fills fields a pod leaves unset.",
        "",
        "## The backstop, when the overrides are not set",
        "",
        f"**`maxPods` on the engine pool is the ceiling that works.** At {max_pods} a node",
        "takes its own system pods and exactly one engine, so N engines force N",
        f"nodes regardless of what they requested. Measure before trusting {max_pods} --",
        "count the pods on a node of the pool, on a node of a pool with the same",
        "taints, or on any node if you have neither:",
        "",
        "```",
        "kubectl get pods -A --field-selector spec.nodeName=<NODE> --no-headers | wc -l",
        "```",
        "",
        "**Do not count DaemonSet objects for this.** `kubectl get ds -A | wc -l`",
        "reports 32 on a stock GKE cluster where 4 DaemonSet pods actually land:",
        "most are variants gated by nodeAffinity -- GPU plugins, Windows builds,",
        "metrics agents chosen by machine size -- and counting them sizes the pool",
        "eight times too loose.",
        "",
        "That count + 1 is your `maxPods`. Too low and the node's own agents never",
        "start, which looks like a broken node rather than a full one: a cluster",
        "left at `maxPods: 10` had six system pods stuck Pending on `Too many",
        "pods`, managed Prometheus among them.",
        "",
        "The taint is what makes the number predictable. Without it the pool also",
        "takes whatever Deployments the scheduler spreads there -- kube-dns,",
        "metrics-server, konnectivity-agent -- for another 5-6 slots a node that",
        "come and go.",
        "",
        "## Sizing the engine node",
        "",
        f"One engine per node means the machine must hold {format_cpu(cpu)} CPU / {format_memory(mem)}",
        "*allocatable*, and allocatable is what is left after the kubelet's",
        f"reservations -- roughly {format_cpu(NODE_OVERHEAD_CPU)} CPU and {format_memory(NODE_OVERHEAD_MEM)} on a managed node. So pick a",
        f"machine with at least **{node_cpu} vCPU and {node_mem}** of capacity, and confirm with",
        "`kubectl get node <name> -o jsonpath='{.status.allocatable}'` once one exists.",
        "",
    ]
    if slots:
        lines += [
            f"This location advertises **slots={slots}**, so size the pool's maximum",
            f"at {slots} node(s) to run a full-width test.",
            "",
        ]
    else:
        lines += [
            "The location's concurrency (`slots`) is not recorded in these facts;",
            "set the pool maximum to the widest test you intend to run.",
            "",
        ]

    lines += _nodepool_commands(o, eng_sel, taints, max_pods, slots)
    lines += [
        "## Checking it worked",
        "",
        "Run a test, then -- while it is running -- confirm the engine is on the",
        "engine pool and is the size you configured:",
        "",
        "```",
        f"kubectl -n {o['namespace']} get pods -o wide",
        f"kubectl -n {o['namespace']} get pod <engine-pod> \\",
        "  -o jsonpath='{.spec.nodeName}{\"\\n\"}{.spec.containers[*].resources}{\"\\n\"}'",
        "```",
        "",
        f"Expect `limits` of {format_cpu(cpu)}/{format_memory(mem)} and `requests` of "
        f"{ENGINE_STAMPED_REQUEST_CPU}/{ENGINE_STAMPED_REQUEST_MEM}.",
        "The mismatch is expected and is the reason for `maxPods`. What matters",
        "is that the node is one of the engine pool's, and that no more engines",
        "share it than the pool was sized for -- which is one per node where the",
        f"platform allows it and {_engines_per_node(max_pods, GKE_MIN_MAX_PODS)} on GKE, whose maxPods floor of "
        f"{GKE_MIN_MAX_PODS} does not",
        "go low enough. More than that means `maxPods` is not in effect.",
        "",
        "`bzm-opl-gen doctor` checks the same shape before you deploy.",
        "",
    ]
    return "\n".join(lines)


def _machine_for(o, engines):
    """The machine-size placeholder for a node expected to hold `engines` of
    them at once.

    Sized from the engine's *limits*, because that is what it runs at once it is
    on the node -- the requests it was scheduled by are the gap this whole file
    is about. Where a platform floor forces more than one engine onto a node,
    this is what stops the recipe recommending a node for one and a maxPods for
    several.
    """
    cpu, mem = engine_size(o)
    return (f"<at least {format_cpu(cpu * engines + NODE_OVERHEAD_CPU)} vCPU / "
            f"{format_memory(mem * engines + NODE_OVERHEAD_MEM)}"
            + (f", holding {engines} engines>" if engines > 1 else ">"))


def _cmd(parts):
    """Shell command lines joined with trailing backslashes. Built from a list
    with the empty entries already dropped, because the alternative -- a
    conditional line that carries its own `\\` -- emits a dangling continuation
    the moment its condition is false, and a broken command in a recipe is
    worse than a missing flag."""
    parts = [p for p in parts if p]
    return [p + (" \\" if i < len(parts) - 1 else "")
            for i, p in enumerate(parts)]


def _nodepool_commands(o, eng_sel, taints, max_pods, slots):
    """Per-distribution commands for the engine pool. Every one sets the same
    four things -- labels, taints, maxPods, min zero -- and they are spelled out
    in full rather than given as a table of flag names because the flag easiest
    to leave out (maxPods) is the one the whole shape depends on.

    Where a distribution cannot set one of the four on the create command
    (EKS's maxPods, OpenShift's anything), that is said in prose rather than
    quietly omitted: a recipe that looks complete and is not is how a pool ends
    up scaling correctly and packing eight engines onto the first node.
    """
    labels = ",".join(f"{k}={v}" for k, v in eng_sel.items())
    maximum = slots or 5
    machine = _machine_for(o, engines_per_node(o))
    out = [
        "## Creating the engine pool",
        "",
        "Four things matter and are the same everywhere: the **labels** the",
        "manifests select on, the **taints** that keep other workloads off, the",
        f"**`maxPods: {max_pods}`** ceiling ({engines_per_node(o)} engine(s) a node plus",
        f"~{TYPICAL_SYSTEM_PODS} system pods), and a **minimum of zero** so the pool",
        "drains between runs.",
        "",
    ]

    gke_max_pods = max(max_pods, GKE_MIN_MAX_PODS)
    gke_engines = _engines_per_node(max_pods, GKE_MIN_MAX_PODS)
    out += ["### GKE (Standard -- Autopilot cannot take user node pools)", "", "```"]
    out += _cmd([
        "gcloud container node-pools create bzm-engines",
        "  --cluster <CLUSTER> --region <REGION>",
        f"  --machine-type {_machine_for(o, gke_engines)}",
        f"  --node-labels {labels}" if labels else "",
        f"  --max-pods-per-node {gke_max_pods}",
        *[f"  --node-taints {t}" for t in taints],
        f"  --enable-autoscaling --min-nodes 0 --max-nodes {maximum}",
    ])
    out += ["```", ""]
    if gke_max_pods > max_pods:
        out += [
            f"**GKE will not go below {GKE_MIN_MAX_PODS}.** The API refuses anything lower",
            f"(\"Maximum pods per node must be at least {GKE_MIN_MAX_PODS} and at most 256\"), so the",
            f"{max_pods} this pool actually wants is not reachable and the floor leaves room",
            f"for **{gke_engines} engines a node**, not one.",
            "",
            "That is not a setting you can tighten, so size the node for those",
            f"{gke_engines} engines rather than for one -- the machine type above already",
            "is. The alternative is fewer system pods on the pool (dropping",
            "managed Prometheus or NodeLocalDNS from it), which buys one slot each",
            "and costs observability.",
            "",
        ]
    out += [
        "`--max-pods-per-node` cannot be changed after creation -- it sizes the",
        "node's alias IP range, so getting it wrong means replacing the pool.",
        "",
    ]

    out += ["### EKS (managed node group)", "", "```"]
    out += _cmd([
        "eksctl create nodegroup --cluster <CLUSTER> --name bzm-engines",
        f"  --node-type {machine}",
        f"  --nodes-min 0 --nodes-max {maximum}",
        f"  --node-labels {labels}" if labels else "",
    ])
    out += [
        "```",
        "",
        "**Two of the four are not on that command.** Taints go in the `eksctl`",
        "ClusterConfig (`taints:` under the node group), and `maxPods` comes from",
        "the launch template's bootstrap:",
        "",
        "```",
        f"--kubelet-extra-args '--max-pods={max_pods}'",
        "```",
        "",
        "EKS otherwise derives maxPods from the instance type's ENI limits, which",
        "is far higher than anything wanted here.",
        "",
    ]

    out += ["### AKS", "", "```"]
    out += _cmd([
        "az aks nodepool add --cluster-name <CLUSTER> --resource-group <RG>",
        "  --name bzmengines",
        f"  --node-vm-size {machine}",
        f"  --max-pods {max_pods}",
        f"  --enable-cluster-autoscaler --min-count 0 --max-count {maximum}",
        f"  --labels {labels}" if labels else "",
        f"  --node-taints {','.join(taints)}" if taints else "",
    ])
    out += ["```", ""]

    out += [
        "### OpenShift (MachineSet)",
        "",
        "Clone an existing MachineSet and set on the clone:",
        "",
        "```yaml",
        "spec:",
        "  replicas: 0                     # a MachineAutoscaler grows it",
        "  template:",
        "    spec:",
        "      metadata:",
        "        labels:",
    ]
    out += ([f"          {k}: \"{v}\"" for k, v in eng_sel.items()]
            or ["          # no engine labels configured"])
    if taints:
        out.append("      taints:")
        for block in _taint_yaml(taints):
            out += block
    out += [
        "```",
        "",
        "Pair it with a `MachineAutoscaler` (`minReplicas: 0`), and set maxPods",
        "through a `KubeletConfig` selecting that pool's machine config pool --",
        "it is not a MachineSet field:",
        "",
        "```yaml",
        "apiVersion: machineconfiguration.openshift.io/v1",
        "kind: KubeletConfig",
        "spec:",
        "  kubeletConfig:",
        f"    maxPods: {max_pods}",
        "```",
        "",
    ]

    out += [
        "### Anything else (kubeadm, Rancher, on-prem)",
        "",
        "No pool object, so the same four things are set per node:",
        "",
        "```",
    ]
    out += ([f"kubectl label node <NODE> {labels.replace(',', ' ')}"] if labels
            else ["# no engine labels configured"])
    out += [f"kubectl taint node <NODE> {t}" for t in taints]
    out += [
        "```",
        "",
        f"`maxPods: {max_pods}` goes in the kubelet config",
        "(`/var/lib/kubelet/config.yaml`) and needs a kubelet restart. With no",
        "cluster autoscaler the pool cannot scale to zero: cordon the nodes",
        "between runs, or accept that they idle.",
        "",
    ]
    return out


def _taint_yaml(taints):
    """`key=value:Effect` / `key:Effect` back into MachineSet taint YAML. The
    valueless form is a real and different taint, so it renders without a
    `value:` key rather than with an empty one."""
    out = []
    for t in taints:
        head, _, effect = t.rpartition(":")
        key, sep, value = head.partition("=")
        block = [f"        - key: \"{key}\"", f"          effect: \"{effect}\""]
        if sep:
            block.insert(1, f"          value: \"{value}\"")
        out.append(block)
    return out



# -- shared README fragments --------------------------------------------------
#
# The two bundle READMEs differ in how you deploy and what can bite, but they
# state the same things about *what this is*, *how to tell it worked* and *what
# it costs to run*. Those are product facts; duplicated, they drift.

PUBLIC_REGISTRY_LABEL = f"{PUBLIC_REGISTRY} (BlazeMeter public)"


def _bundle_table(facts, o, extra=()):
    rows = [
        ("Location", f"`{facts['harbor_id']}`"),
        ("Agent", f"`{o['ship_id']}`"),
    ]
    # Namespace and platform are Kubernetes answers. A docker bundle carries
    # them in profile.json because a profile is every option, but stating them
    # at the top of its README would advertise two settings the same page then
    # says are not applied.
    if o["output_format"] != "docker":
        rows += [("Namespace", f"`{o['namespace']}`"),
                 ("Platform", o["platform"])]
    else:
        rows.append(("Container", f"`{docker_container_name(o['ship_id'])}`"))
    rows.append(("Images from",
                 f"`{o['private_registry'] or PUBLIC_REGISTRY_LABEL}`"))
    rows += list(extra)
    head = f"# BlazeMeter agent -- {facts.get('harbor_name') or facts['harbor_id']}\n\n"
    return head + "| | |\n|---|---|\n" + "".join(
        f"| {k} | {v} |\n" for k, v in rows)


def _verify_block(o):
    cli = "oc" if o["platform"] == "openshift" else "kubectl"
    return f"""## Check it worked

```
{cli} -n {o['namespace']} rollout status deploy/crane
{cli} -n {o['namespace']} logs -l role=role-crane -f
```

The agent should show **online** in BlazeMeter under Settings -> Private
Locations within a minute or so.
"""


def _sizing_bullet(o):
    reg = o["private_registry"]
    return (f"- Each concurrent engine needs **{format_cpu(engine_size(o)[0])} CPU + "
            f"{format_memory(engine_size(o)[1])} RAM + {ENGINE_DISK_GB}GB disk** "
            f"({ENGINE_TMP_GB}GB of it /tmp),\n  and egress to `*.blazemeter.com`"
            + (f" and `{reg}`." if reg else "."))


def _location_bullet(facts):
    """What the location must be set to, in the file the person applying this
    bundle actually reads.

    The bundle deploys an agent; it cannot set the location, and neither figure
    is in the manifests. Left unset, the agent comes online, looks healthy, and
    every test start fails with 403 *Not enough available resources* -- the
    most-documented failure in this project, and the handover said nothing
    about it. `capacity-request.md` states them, but that is a different
    artefact for a different reader and it is not in here.

    **This deliberately does not ask how the facts arrived.** `facts.manual()`
    leaves both None because there was no account to ask, and `gather()` returns
    the same None for a location that genuinely has neither -- the distinction
    that `from_manual_entry` exists for. But nothing that generates may read
    that marker (its own docstring says so, and the manifests being identical
    either way is the property manual() preserves), so the way out is not to
    branch: when the figures are unknown this says *check them*, which is true
    however they came to be unknown, and claims nothing about a location nobody
    looked up. Only when both are known does it state them.

    One line per branch, because `test_readme_is_short_and_actionable` caps the
    file at 45 and is right to: this is a handover somebody skims while holding
    a cluster, not documentation.
    """
    slots, tpe = facts.get("slots"), facts.get("threads_per_engine")
    if not slots or not tpe:
        return ("\n- **Check this location's `slots` and `threadsPerEngine`** "
                "(Settings -> Private Locations): unset, the agent comes online "
                "and looks healthy, and every test start fails with 403 *Not "
                "enough available resources*.")
    return (f"\n- This location runs **{slots} engine(s) per agent at {tpe:,} "
            f"virtual users each** (`slots` / `threadsPerEngine`); its total is "
            f"that times the agents in it.")


def _sa_bullet(o):
    """Named in the handover only when the bundle does not create it: the pod
    stays Pending with `serviceaccount not found` on its ReplicaSet, which is an
    event on an object nobody thinks to look at."""
    if o["service_account_create"]:
        return ""
    return (f"\n- ServiceAccount **`{service_account(o)}`** must already exist in "
            f"`{o['namespace']}` -- this bundle\n  references it and does not "
            f"create it. Nothing fails at apply time if it is\n  missing; the "
            f"agent pod is simply never created.")


def _deploy_steps(o, verb):
    """Whatever has to happen before the apply/install, numbered as one list.

    Both prerequisites are optional and independent, so neither can number
    itself: a bundle with a private registry *and* split node pools used to get
    two steps both called "1." and two called "2.". The numbering lives here,
    once, and the verb is the last step whatever precedes it.

    Node pools come before the mirror because they are the slower of the two to
    get wrong -- a pool that does not exist fails silently and late (every
    object applies, crane comes online, the location reports healthy, and then
    every test sits Pending against a nodeSelector no node carries), where a
    missing image fails at the first pull.
    """
    steps = []
    if separate_pools(o):
        steps.append(
            f"**{{n}}. Create the node pools** -- see [{NODEPOOLS_FILE}]"
            f"({NODEPOOLS_FILE}). This bundle pins engines to\nnodes that must "
            f"exist first; without them the agent comes online and every test "
            f"stays\nPending.\n\n")
    if o["private_registry"]:
        steps.append(
            "**{n}. Mirror the images** (needs push access to the registry; "
            "the pull side needs none):\n\n"
            "```\n./bzm-opl-image-mirror.sh\n```\n\n")
    if not steps:
        return ""
    body = "".join(s.format(n=i) for i, s in enumerate(steps, 1))
    return f"{body}**{len(steps) + 1}. {verb}**\n\n"


def _mirror_script(facts, o):
    """Pull BlazeMeter's images and push them into the customer's registry.

    Handed to someone who may have neither this tool nor a BlazeMeter account,
    so it stands alone: the source needs no credentials (BlazeMeter's gcr.io
    project is anonymously pullable), and the destination needs whatever the
    customer's registry needs, which the script cannot know. The emitted
    comments say both -- they are what the person running it reads.

    Ordering carries the only cleverness: crane is mirrored first because it is
    ~86MB against the engine's ~3.5GB, so a registry that rejects the push costs
    one small image rather than the whole transfer. An earlier version pushed a
    synthetic `FROM scratch` probe instead; it was dropped because `docker rmi`
    is local-only, so every run left the probe tag behind in the customer's
    registry -- and zero-layer images are rejected outright by some registries,
    which would abort a mirror that would otherwise have worked.
    """
    refs = image_refs(facts)
    # The hook's image is not in the location's inventory -- it is not an image
    # the agent runs -- so an air-gapped bundle that includes the check has to
    # be told to mirror it too, or the Pod is the one object in the bundle that
    # cannot pull.
    if o["crane_hook"]:
        refs = refs + [f"{PUBLIC_REGISTRY}/{HOOK_IMAGE_REPO}:{HOOK_IMAGE_TAG}"]
    reg = o["private_registry"].rstrip("/")
    host = reg.split("/")[0]
    # The registry is free-form input from a CLI flag or a text field, and this
    # is a script somebody runs, so every interpolation reaching a *command*
    # is quoted at the point it is built -- the `mirror` calls below. The rest
    # of the uses here are comments and `echo` text, which run nothing.
    lines = [
        "#!/usr/bin/env bash",
        f"# Mirror the images this BlazeMeter private location needs into {reg}.",
        "#",
        f"# Location: {facts.get('harbor_name')} ({facts['harbor_id']})",
        f"# Images from: {facts.get('images_source')}",
        "#",
        "# Pulling needs no credentials -- BlazeMeter's registry is public, and",
        "# nothing here uses a BlazeMeter API key.",
        "# Pushing uses whatever your Docker client is already logged in as:",
        f"#     docker login {host}",
        "#",
        "# Engines are amd64-only, hence --platform on ARM hosts.",
        "set -euo pipefail",
        "",
        'command -v docker >/dev/null || { echo "docker not found on PATH" >&2; exit 1; }',
        "",
        "# crane is mirrored first and is much the smallest, so a registry that",
        "# refuses the push costs one small image, not the whole transfer.",
        "mirror() {",
        '  echo "--> $2"',
        '  docker pull --platform linux/amd64 "$1"',
        '  docker tag "$1" "$2"',
        '  if ! docker push "$2"; then',
        "    echo >&2",
        f'    echo "push to {reg} failed (the real error is above)." >&2',
        f'    echo "if it is an authentication error:  docker login {host}" >&2',
        "    exit 1",
        "  fi",
        "}",
        "",
    ]
    for ref in refs:
        name = ref.rsplit("/", 1)[-1]
        lines.append(f"mirror {shlex.quote(ref)} {shlex.quote(f'{reg}/{name}')}")
    lines += ["", f'echo "done -- {len(refs)} images in {reg}"']
    return "\n".join(lines) + "\n"


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


# Every field name a generated bundle carries the AUTH_TOKEN under, and the
# only place that list exists. The Secret and the simplified ConfigMap use the
# environment variable's own name; the chart's values overlay uses the value
# name helm templates read. Two consumers derive from this -- reading a token
# back out of an existing bundle (below) and redacting one on the way to a
# caller (core.redact_tokens) -- and they had drifted apart already: the reader
# knew only AUTH_TOKEN, so a regenerated chart bundle never found its own
# token, and a redactor that knew only the same one would have handed the chart
# overlay over intact.
TOKEN_FIELDS = ("AUTH_TOKEN", "authToken")

# The files those fields are written into, in the order a bundle is read.
TOKEN_FILES = ("bzm_secret.yaml", "bzm_configmap.yaml", "bzm-opl-values.yaml")

# The Secret holding it, named in templates/secret.yaml and defaulted by the
# chart's `bzm-opl.secretName`. Here as a constant because a bundle with no
# token has to be able to say where one is recoverable from -- `kubectl get
# secret <this> -o jsonpath=...` on an agent already deployed -- and that
# sentence must name the object this generator actually emits.
SECRET_NAME = "blazemeter-secret"

AUTH_TOKEN_RE = re.compile(
    r'^\s*(?:' + "|".join(TOKEN_FIELDS) + r'):\s*"?([^"\s]+)"?\s*$', re.M)


def existing_auth_token(output_dir):
    """The AUTH_TOKEN already written into the bundle at `output_dir`, or None.

    Read back rather than re-fetched because fetching *mints*. The call behind
    it is a POST to /private-locations/{h}/ships/{s}/docker-command, which
    issues a fresh token and invalidates the one before it -- so regenerating a
    bundle merely to look at it stops the agent already running from the last
    one. Silently, too: crane answers a dead token with 404 on /versions, logs
    `Sleeping for 300`, and never starts its health web service, so the pod sits
    `0/1 Running` looking like a slow boot rather than a revoked credential.

    Both files are checked because which one carries the token is use_secret's
    decision, and a bundle regenerated with the flag flipped should still find
    the token its predecessor wrote.
    """
    for name in TOKEN_FILES:
        try:
            with open(os.path.join(output_dir, name)) as fh:
                m = AUTH_TOKEN_RE.search(fh.read())
        except OSError:
            continue
        if m and m.group(1) != DEFAULT_OPTIONS["auth_token"]:
            return m.group(1)
    return None


def service_account(o):
    """The ServiceAccount name every reference uses -- the Deployment's
    serviceAccountName and both binding subjects -- whether or not this bundle
    creates the account itself.

    Required, never resolved from an empty field. The obvious alternative, and
    what `helm create` scaffolds, is to fall back to the namespace's `default`
    account when nothing creates one: that deploys, works, and quietly binds
    crane's Role to the account every other pod in the namespace runs as. An
    empty text field should not be able to decide that, so every format that
    has an account refuses instead. doctor.py imports this to check the account
    is really there.

    `None` where the format has none -- a docker bundle runs a container, not a
    pod. Refusing an empty name there would refuse a value the same file says
    is ignored (see ignored_options), over a field that format's page does not
    show.
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


def auto_update(o):
    """Resolve `auto_update` to the boolean AUTO_KUBERNETES_UPDATE carries.

    That variable, not BlazeMeter's `AUTO_UPDATE`: the latter is documented as
    the Docker-side switch and does nothing on a Kubernetes agent, so this
    generator never emits it. Named here because the two are one word apart and
    picking the wrong one produces a bundle whose setting is simply ignored.

    **Unset is off, and that is a deliberate departure from BlazeMeter.** Their
    manual Kubernetes manifest ships `AUTO_KUBERNETES_UPDATE: 'true'`, and this
    generator copied it until the cost was measured on a live cluster: with the
    updater on, crane takes ownership of its own Deployment within seconds of
    install as field manager `OpenAPI-Generator`, rewriting the container image
    and `.spec.strategy` from Recreate to RollingUpdate. Helm applies
    server-side, so the *next* `helm upgrade` fails on a field-ownership
    conflict, having already applied the ConfigMap -- and `--force-conflicts`
    does not rescue it, because forcing `type: Recreate` back leaves crane's
    `strategy.rollingUpdate` beside it and the API server rejects the pair. The
    fix was a value the customer had to know to set before installing, in a
    bundle that did not say so until they read the README after the failure.

    So the bundle now ships the configuration whose upgrades work, and
    `--auto-update` is how you ask for the other one. What off costs is real
    and is stated wherever it is emitted: the agent stops upgrading itself, and
    one far enough behind loses BlazeMeter support.

    It does NOT follow the registry any more. It used to -- off with a mirror,
    on without -- which meant the trap was sprung only for customers pulling
    from the public registry, i.e. most of them. A private registry is now one
    reason among others to leave the default alone rather than a special case
    in the resolution.

    `.get`, not `[]`: livetest judges a profile.json written by an older
    version of this tool, where the key is simply absent. Mirrors the chart's
    `bzm-opl.autoUpdate` helper -- both sides resolve it, so helm parity
    compares the same string.
    """
    chosen = o.get("auto_update")
    if isinstance(chosen, bool):
        return chosen
    if chosen is not None:
        raise ValueError(
            f"auto_update must be true, false or unset, got {chosen!r} -- "
            "unset means off, the default that keeps `helm upgrade` working")
    return False


def _crane_image(facts, o):
    if not o["private_registry"]:
        return facts["crane_image"]
    tag = facts["crane_image"].rsplit(":", 1)[1]
    return f"{o['private_registry'].rstrip('/')}/crane:{tag}"


def _hook_sub(o, sv):
    """The crane-hook-only half of the substitution map.

    Split out rather than folded into `sub` for one reason: every key in `sub`
    is used by a template that is always emitted, and a key that exists only for
    an optional file is how the always-emitted ones acquire a value nobody can
    trace. The SV variables are the interesting part -- the hook checks the
    ingress and its TLS secret only when there is one to check, and passing
    empty strings would have it check for an ingress named "".
    """
    registry = o["private_registry"] or PUBLIC_REGISTRY
    sv_env = ""
    if sv:
        sv_env = (
            f"        - name: KUBERNETES_WEB_EXPOSE_TYPE\n"
            f"          value: {sv['type'].upper()}\n"
            f"        - name: KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME\n"
            f"          value: {sv['tls_secret']}\n")
    return {
        "HOOK_ROLE": HOOK_ROLE_NAME,
        "REGISTRY": registry,
        "HOOK_IMAGE": f"{registry.rstrip('/')}/{HOOK_IMAGE_REPO}:{HOOK_IMAGE_TAG}",
        # Same rule as the agent: OpenShift's SCC assigns the UID and a pinned
        # one is refused, so it is pinned only on the platform that wants it.
        "HOOK_UID_BLOCK": (
            "" if o["platform"] == "openshift" else
            f"        runAsUser: {o['run_as_user']}\n"
            f"        runAsGroup: {o['run_as_user']}\n"),
        "HOOK_SV_ENV": sv_env,
    }


def _sv_rbac_block(sv):
    """Namespaced Role rules crane needs to publish a virtual service.

    Deliberately namespaced: keeping the whole deployment inside a namespaced
    Role is the reason the ingress path is preferred. Not because NODEPORT needs
    cluster-scoped node reads -- it does not, for a performance location (#49)
    or a virtual service (#60). A virtual service does provoke a denied node
    read, from the Service pool, and publishes its endpoint anyway.

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

OUTPUT_FORMATS = ("manifests", "helm", "docker")

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
    ]
    if not o["restrict_engines"]:
        # Only when off. On is the chart's default too, and the overlay names
        # what was chosen rather than restating what was not.
        lines += [
            "# Crane's default engine pod is privileged; restricted PodSecurity,",
            "# OpenShift SCC and GKE Autopilot all refuse it.",
            "restrictEngines: false",
        ]
    lines += [
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
            "# Empty -> BlazeMeter's public registry.",
            'privateRegistry: ""',
            "imageOverrides: {}",
            "registryAuth: false",
        ]
    if o["crane_hook"]:
        # Only when asked for: the chart's default is off, and an overlay that
        # restated every default would stop being the record of what was chosen.
        lines += [
            "",
            "# github.com/Blazemeter/crane-hook, as this chart's `helm test`",
            "# hook: `helm test <release>` runs the cluster check and nothing",
            "# runs it at install time.",
            "craneHook:",
            "  enabled: true",
        ]
    if o["auto_update"] is None:
        lines += [
            "",
            "# Left to the chart's default, which is off: crane leaves its own",
            "# Deployment to Helm, so `helm upgrade` works and keeping the agent",
            "# current is your job. See autoUpdate in the chart's values.yaml for",
            "# what turning it on costs.",
            "autoUpdate:",
        ]
    else:
        # Stated rather than left to the chart, even where it agrees with the
        # chart's default: this overlay is the record of what was asked for,
        # and `--auto-update` in particular is a decision someone should find
        # in the file rather than infer from its absence.
        lines += [
            "",
            ("# On by request: crane rewrites its own Deployment's image when "
             "BlazeMeter\n# ships a newer agent, so `helm upgrade` needs "
             "--force-conflicts -- and that\n# does not always rescue it. See "
             "autoUpdate in the chart's values.yaml."
             if o["auto_update"] else
             "# Off, which is also the chart's default: crane leaves its own "
             "Deployment\n# to Helm, and keeping the agent current is your job."),
            f"autoUpdate: {'true' if o['auto_update'] else 'false'}",
        ]
    lines += [
        "",
        f"clusterRbac: {'true' if o['cluster_rbac'] else 'false'}",
        "# The account crane runs as. The name is used either way: with create",
        "# false the ServiceAccount object is not rendered, and the Deployment",
        "# and the binding subjects name the one already in the namespace.",
        "serviceAccount:",
        f"  create: {'true' if o['service_account_create'] else 'false'}",
        f"  name: {_yq(service_account(o))}",
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
        # Spelled out as YAML rather than the JSON block _indent_yaml would
        # give: a toleration is the thing someone most often hand-edits after
        # generating, and `- key: ...` is what every other example they will
        # find looks like.
        lines.append("tolerations:")
        for tol in o["tolerations"]:
            items = list(tol.items())
            lines += [f"  {'- ' if i == 0 else '  '}{k}: {_yq(v)}"
                      for i, (k, v) in enumerate(items)]
    else:
        lines.append("tolerations: []")

    # Engine placement is a separate pair, not a derivation of the two above:
    # the chart cannot tell "unset, so follow crane" from "explicitly empty"
    # once the values file is written, so generate.py resolves it here and the
    # chart just renders what it is given. engineNodeSelector/engineTolerations
    # are therefore always the *effective* engine placement.
    eng_sel, eng_tol = engine_scheduling(o)
    lines.append("")
    if separate_pools(o):
        lines.append("# Engines run on their own node pool, separate from crane's above.")
    else:
        lines.append("# Engines follow the crane pod's placement (one-pool shape).")
    if eng_sel:
        lines += ["engineNodeSelector:"] + [
            f"  {json.dumps(k)}: {_yq(v)}" for k, v in eng_sel.items()]
    else:
        lines.append("engineNodeSelector: {}")
    if eng_tol:
        lines.append("engineTolerations:")
        for tol in eng_tol:
            items = list(tol.items())
            lines += [f"  {'- ' if i == 0 else '  '}{k}: {_yq(v)}"
                      for i, (k, v) in enumerate(items)]
    else:
        lines.append("engineTolerations: []")
    cpu_limit, mem_limit = engine_size(o)
    lines += [
        "",
        "# The limits crane stamps on the engine pods it spawns. Empty ->",
        f"# BlazeMeter's documented default. This location asks for {format_cpu(cpu_limit)} CPU +",
        f"# {format_memory(mem_limit)} per engine, plus ~{ENGINE_DISK_GB}GB disk ({ENGINE_TMP_GB}GB of it /tmp).",
        "# Check a cluster against that with `bzm-opl-gen doctor`.",
        "#",
        "# Engine *requests* are not settable from here -- they come from the",
        "# location's overrideCPU/overrideMemory (Settings -> Private Locations),",
        f"# defaulting to {ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM}. The scheduler packs nodes on those,",
        "# so set them to match these limits or engines pack far tighter than",
        "# they run.",
        "engine:",
        f"  cpuLimit: {_yq(o['engine_cpu_limit'] or '')}",
        f"  memoryLimit: {_yq(o['engine_mem_limit'] or '')}",
        f"  ephemeralRequestMb: {_yq(o['engine_ephemeral_request_mb'] or '')}",
        f"  ephemeralLimitMb: {_yq(o['engine_ephemeral_limit_mb'] or '')}",
    ]
    if o["crane_ephemeral_storage"]:
        # Both fields, always together -- the chart's default is already a
        # matched pair, and overriding only one reintroduces the gap that
        # Autopilot collapses. See CRANE_EPHEMERAL_STORAGE.
        lines += [
            "",
            "# Crane's own pod. Request and limit are one value on purpose:",
            "# GKE Autopilot rewrites the limit down to the request.",
            "crane:",
            "  resources:",
            "    requests:",
            f"      ephemeral-storage: {_yq(o['crane_ephemeral_storage'])}",
            "    limits:",
            f"      ephemeral-storage: {_yq(o['crane_ephemeral_storage'])}",
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


def _upgrade_bullet(o):
    """What `helm upgrade` does to this particular overlay.

    Two different instructions, and handing someone the wrong one wastes the
    upgrade. The default bundle upgrades normally; only one that asked for
    auto-update needs telling that it cannot, and telling it *before* the
    upgrade that would otherwise half-apply."""
    if auto_update(o):
        return (f"- **Upgrading:** this bundle asked for auto-update, so crane "
                f"owns its own\n  Deployment and `helm upgrade` fails on a "
                f"conflict. Change things by\n  reinstalling, or set "
                f"`autoUpdate: false` in `{HELM_VALUES_FILE}` and reinstall "
                f"once.")
    return ("- **Upgrading:** `helm upgrade` works as it should -- auto-update "
            "is off, so\n  Helm is the only writer of the Deployment. Keeping "
            "the agent current is\n  your job: bump `image.tag` and upgrade "
            "(an agent far behind loses support).")


def _helm_readme(facts, o):
    """The page someone hands a customer: instructions, not reasoning. The
    chart's own helm/README.md carries the why."""
    ns = o["namespace"]
    token = ""
    if not o["auth_token"] or o["auth_token"] == DEFAULT_OPTIONS["auth_token"]:
        token = (" \\\n    --set-string authToken=<AUTH_TOKEN>"
                 "   # not in the values file;\n    # re-generate with "
                 "--auth-token <token> to embed it")
    return f"""{_bundle_table(facts, o)}
## Deploy

{_deploy_steps(o, "Install")}```
helm install crane ./{CHART_DIR} -n {ns} --create-namespace -f {HELM_VALUES_FILE}{token}
```

{_verify_block(o)}
## Worth knowing

{_sizing_bullet(o)}{_location_bullet(facts)}{_sa_bullet(o)}
{_upgrade_bullet(o)}
- `{HELM_VALUES_FILE}` holds everything specific to you; `{CHART_DIR}/` is the same
  chart for everyone. `helm show values ./{CHART_DIR}` lists every option.
"""


# -- docker output ------------------------------------------------------------
#
# One agent, one container, on a host with a docker daemon -- BlazeMeter's other
# way of running a private location, and the one their own UI hands you from the
# Docker Command tab. The command is built here rather than fetched from
# `/private-locations/{harbor}/ships/{ship}/docker-command` (api.docker_command,
# which this repo already calls to mint a token) for the same reason every other
# format is built here: generate() reaches nothing, so a bundle can be produced
# for an account nobody here can log in to -- facts.manual() is the whole point.
# The shape is BlazeMeter's, from their Docker installation page and their
# agent-environment-variables reference; what this adds is the bundle's own
# settings folded in, which is the part their generated command cannot know.
#
# Most of this generator's options are Kubernetes vocabulary and reach nothing
# here -- there is no namespace, no ServiceAccount, no toleration on a docker
# host. That is stated in the README rather than refused, and stated per bundle:
# only the options actually set away from their default are listed, so the note
# says what *this* bundle asked for and did not get.

DOCKER_RUN_FILE = "bzm-opl-agent.sh"
DOCKER_ENV_FILE = "bzm-opl-agent.env"
DOCKER_CA_FILE = "ca-bundle.crt"
# Where BlazeMeter's Docker documentation says the trust bundle has to land, and
# what the two variables below have to point at. Not ours to choose: the
# container's own CA store is at this path and crane reads it from there.
DOCKER_CA_PATH = "/etc/ssl/certs/ca-certificates.crt"
# NO_PROXY for a docker host. The Kubernetes default names `kubernetes.default`,
# which is the cluster's API service and resolves to nothing here; 127.0.0.1 and
# localhost are required by BlazeMeter's proxy documentation, or transaction
# based virtual services break against their own local calls.
DOCKER_NO_PROXY = "127.0.0.1,localhost"
# The fixed part of the command, in BlazeMeter's own order and spelling. The
# socket is how crane starts engines -- it is a container manager, and on this
# platform the manager is the host's docker daemon; /tmp is shared so an engine
# can hand artifacts back; --net=host is what lets the agent advertise a
# reachable address.
DOCKER_MOUNTS = ["/var/run/docker.sock:/var/run/docker.sock", "/tmp:/tmp"]
# The host ports crane may give the engines it starts, as BlazeMeter's own
# generated command sets it. A literal rather than an option: it is one range on
# one host, the script is editable text, and an option for it would be a
# Kubernetes-shaped answer to a question only this format has.
DOCKER_PORT_RANGE = "6000-7000"
DOCKER_WORKDIR = "/usr/src/app/"
DOCKER_ENTRYPOINT = "python agent/agent.py"

# Options whose value a docker bundle cannot carry, with what each one is for.
# Every one of them is Kubernetes vocabulary; the docker agent's equivalents are
# either the daemon's own configuration or nothing at all.
#
# Served, as core.docker_ignored(): the configure page hides what a docker
# bundle cannot carry, and a second copy of this table in TypeScript is exactly
# the drift the SV funcId list already cost once. So a key added here stops
# being offered there, and nothing has to remember to remove it.
DOCKER_IGNORED = {
    "platform": "there is no OpenShift/Kubernetes distinction on a docker host",
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
    # The last two were found by hiding this table's keys on the configure
    # page: both were still offered there, and both reach nothing here.
    "crane_hook": "crane-hook is a Pod, and there is no cluster to run it in",
    "registry_auth": "the stubs are ConfigMap lines; a docker host authenticates "
                     "with its own docker login",
}


def docker_container_name(ship_id):
    """What BlazeMeter's own command calls the container, so an agent installed
    from this bundle is the one their documentation talks about."""
    return f"bzm-crane-{ship_id}"


def docker_env(facts, o):
    """Every environment variable the container needs, in the order they are
    written, as {NAME: value}.

    Split from the command itself because `use_secret` decides where each one is
    written rather than whether it exists -- see docker_split_env."""
    env = {
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
        # Stated rather than left to the default, exactly as the Kubernetes
        # ConfigMap states KUBERNETES: a file that says which manager it is for
        # can be read on its own.
        "CONTAINER_MANAGER_TYPE": "DOCKER",
        # Where engine images come from. Always set, like the ConfigMap's, so
        # the file names its registry rather than implying one.
        "DOCKER_REGISTRY": o["private_registry"] or PUBLIC_REGISTRY,
        # The ports crane hands its engines. `--net=host` below means they are
        # the *host's* ports, so this is a range that has to be free on the
        # machine -- edit it in the script if something else there wants it.
        # BlazeMeter's own generated command carries it; built from their docs,
        # this did not, and a variable their command always sets is not one to
        # leave to a default nobody here can see.
        "DOCKER_PORT_RANGE": DOCKER_PORT_RANGE,
    }
    # AUTO_UPDATE, not AUTO_KUBERNETES_UPDATE: a different variable for a
    # different mechanism, and this is the format where it belongs. Only emitted
    # when the option was answered -- unlike the Kubernetes path, which forces
    # it off. What it does here is pull a newer crane image for a container the
    # operator started; there is no Deployment for it to fight over, which is
    # the specific hazard auto_update() departs from BlazeMeter's default for.
    if o["auto_update"] is not None:
        env["AUTO_UPDATE"] = "true" if o["auto_update"] else "false"
    env.update(proxy_env(o, no_proxy=DOCKER_NO_PROXY))
    if _ca_cfg(o):
        # Both, per BlazeMeter's CA page: crane's own HTTP client reads the
        # first and boto the second, and a bundle trusted by one and not the
        # other fails in whichever half was missed.
        env["REQUESTS_CA_BUNDLE"] = DOCKER_CA_PATH
        env["AWS_CA_BUNDLE"] = DOCKER_CA_PATH
    return env


def docker_split_env(facts, o):
    """The environment, split into (command, env_file).

    `use_secret` means the same thing here as it does for Kubernetes -- the
    credential lives apart from the configuration -- and docker's own mechanism
    for it is --env-file. The difference it makes is real rather than cosmetic:
    a value passed with --env is in the host's process list for anyone running
    `ps`, and in the shell history of whoever ran it.

    With it off, everything is inline, which is the shape BlazeMeter's own
    generated command has."""
    env = docker_env(facts, o)
    if not o["use_secret"]:
        return env, {}
    secret = {"AUTH_TOKEN": env.pop("AUTH_TOKEN")}
    if _proxy_has_creds(o):
        # The proxy URL carries user:password (see proxy_url), so it is a
        # credential too -- the same rule the Kubernetes Secret follows.
        for name in ("HTTP_PROXY", "HTTPS_PROXY"):
            if name in env:
                secret[name] = env.pop(name)
    return env, secret


def _docker_run_lines(facts, o):
    """The `docker run` invocation, one argument per line."""
    cmd, secret = docker_split_env(facts, o)
    lines = ["docker run -d \\",
             '  --name "$NAME" \\',
             # on-failure rather than always: a crane that exits cleanly has
             # been told to stop, and BlazeMeter's own command says on-failure.
             "  --restart on-failure \\",
             # As root, because the socket below is how this agent starts
             # engines and on a stock host it is root:docker 0660. The crane
             # image runs as a non-root user, so without this the container
             # comes up, reaches the daemon, and dies on
             # `PermissionError: [Errno 13]` out of docker/transport/unixconn --
             # an error about a Python socket that says nothing about the uid
             # that could not open it. BlazeMeter's own generated command
             # carries `-u 0`; this generator was built from their *docs*, which
             # do not mention it, and that is how it came to be missing.
             #
             # Not `run_as_user`: that option is a pod securityContext field
             # (see DOCKER_IGNORED), and this is not a preference -- an agent
             # that cannot open the socket cannot do the one thing it is for.
             "  -u 0 \\"]
    if secret:
        lines.append('  --env-file "$ENV_FILE" \\')
    lines += [f"  --env {k}={_sh_value(v)} \\" for k, v in cmd.items()]
    lines += [f"  -v {m} \\" for m in DOCKER_MOUNTS]
    if _ca_cfg(o):
        lines.append(f'  -v "$CA_BUNDLE":{DOCKER_CA_PATH}:ro \\')
    lines += [f"  -w {DOCKER_WORKDIR} \\",
              # The agent has to advertise an address the engines it starts can
              # reach, and on this platform they are containers on the same
              # host. Bridge networking gives it a private address it cannot
              # hand out.
              "  --net=host \\",
              f"  {_crane_image(facts, o)} {DOCKER_ENTRYPOINT}"]
    return "\n".join(lines)


def _sh_value(value):
    """A value as one shell word. Quoted only where it has to be, so the common
    case reads like BlazeMeter's own command -- a proxy URL with a password in
    it is the case that needs it, and special characters there are exactly what
    their documentation warns about."""
    text = str(value)
    if text and all(c.isalnum() or c in "_-.:/" for c in text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def _docker_ignored(o):
    """The options this bundle set that a docker agent cannot carry, as
    (name, why) pairs.

    Compared against the defaults rather than listed wholesale: a note that
    named every one of them every time would be read as boilerplate, and the one
    line that matters -- "you asked for a node selector and it is not here" --
    would be in the middle of it."""
    return [(k, why) for k, why in sorted(DOCKER_IGNORED.items())
            if o.get(k) != DEFAULT_OPTIONS[k]]


def _docker_run_sh(facts, o):
    ca = _ca_cfg(o)
    name = docker_container_name(o["ship_id"])
    # Both sibling files are resolved against the script rather than against the
    # working directory: this is a file people copy onto a host and run from
    # wherever they happen to be, and a relative --env-file that resolves to
    # nothing fails inside docker with a message about the file, not about the
    # directory. CA_BUNDLE stays overridable -- a host may already have the
    # trust bundle the platform team maintains.
    ca_line = (f'CA_BUNDLE="${{CA_BUNDLE:-$DIR/{DOCKER_CA_FILE}}}"\n') if ca else ""
    ca_check = ('''
if [ ! -f "$CA_BUNDLE" ]; then
  echo "trust bundle not found: $CA_BUNDLE" >&2
  echo "set CA_BUNDLE=/path/to/your/bundle.crt, or put it beside this script" >&2
  exit 1
fi
''') if ca else ""
    env_check = (f'''
if [ ! -f "$ENV_FILE" ]; then
  echo "{DOCKER_ENV_FILE} not found beside this script -- it holds the AUTH_TOKEN" >&2
  exit 1
fi
''') if o["use_secret"] else ""
    env_line = f'ENV_FILE="$DIR/{DOCKER_ENV_FILE}"\n' if o["use_secret"] else ""
    return f'''#!/bin/sh
# The BlazeMeter agent for private location {facts.get("harbor_name") or facts["harbor_id"]},
# as one container on this host. Generated by bzm-opl-gen; see README.md.
#
# Run it on the machine that is to be the private location. Needs a docker
# daemon and permission to reach its socket -- crane starts the engines as
# sibling containers through it, which is why the socket is mounted.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
NAME={name}
{env_line}{ca_line}
if docker ps -a --format '{{{{.Names}}}}' | grep -qx "$NAME"; then
  # Not removed automatically: the container of this name may be the agent that
  # is currently serving this location, and taking it away is a decision rather
  # than a step.
  echo "$NAME already exists." >&2
  echo "Remove it first if it is the old agent: docker rm -f $NAME" >&2
  exit 1
fi
{env_check}{ca_check}
{_docker_run_lines(facts, o)}

echo "started $NAME -- follow it with: docker logs -f $NAME"
'''


def _docker_env_file(facts, o):
    """The credential half, for --env-file. Docker parses this itself: one
    NAME=value per line, no quoting and no shell -- a quoted value arrives with
    the quotes in it."""
    _, secret = docker_split_env(facts, o)
    if not secret:
        return None
    return ("# Read by docker --env-file, not by a shell: no quotes, no export.\n"
            "# Anyone holding this token can register as this agent. chmod 600.\n"
            + "".join(f"{k}={v}\n" for k, v in secret.items()))


def _docker_readme(facts, o):
    ignored = _docker_ignored(o)
    token = ""
    if not o["auth_token"] or o["auth_token"] == DEFAULT_OPTIONS["auth_token"]:
        where = DOCKER_ENV_FILE if o["use_secret"] else DOCKER_RUN_FILE
        token = (f"\n> **The AUTH_TOKEN in `{where}` is a placeholder.** Replace it "
                 f"with the agent's own, from BlazeMeter's Docker Command tab "
                 f"(Settings -> Private Locations -> this agent), or re-generate "
                 f"with `--auth-token`.\n")
    ignored_block = ""
    if ignored:
        rows = "\n".join(f"| `{k}` | {why} |" for k, why in ignored)
        ignored_block = f"""
## Set here, but not carried

These were configured for this bundle and a docker agent has nowhere to put
them. Nothing is silently applied -- if you need them, the Kubernetes formats
are where they mean something.

| option | why |
|---|---|
{rows}
"""
    ca = _ca_cfg(o)
    ca_block = ""
    if ca:
        ca_block = f"""
- **Trust.** `{DOCKER_CA_FILE}` is mounted at `{DOCKER_CA_PATH}`, which is where
  `REQUESTS_CA_BUNDLE` and `AWS_CA_BUNDLE` point. It replaces the container's CA
  store rather than adding to it, so it has to be a full bundle -- your CA *and*
  the public roots -- or the agent stops trusting BlazeMeter itself.
"""
    return f"""{_bundle_table(facts, o)}
## Run it
{token}
```
./{DOCKER_RUN_FILE}
```

The agent should report online in BlazeMeter within a minute or two; the first
run pulls crane and can take considerably longer. Watch it with
`docker logs -f {docker_container_name(o["ship_id"])}`.

## Worth knowing

{_sizing_bullet(o)}{_location_bullet(facts)}
- **The socket is the point.** Crane starts engines as containers on this host
  through `/var/run/docker.sock`, so whoever runs the script needs access to it.
  That is effectively root on the machine -- BlazeMeter's own instructions say
  the same.
- **One agent per host.** The container is named after the agent
  (`{docker_container_name(o["ship_id"])}`), and the script refuses rather than
  replacing an existing one, because that container may be the agent currently
  serving this location.
- **Engines run beside it, not inside it.** Size the host for the whole
  location, not for crane: every engine is another container here.{ca_block}
- BlazeMeter's Docker Command tab generates the same command without this
  bundle's settings. This one is that command with them folded in; the identity
  (`HARBOR_ID`, `SHIP_ID`) is the same either way.
{ignored_block}"""


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
    elif DOCKER_RUN_FILE in files:
        # The command first: it is the bundle, and the other three files are
        # about it.
        lead = [DOCKER_RUN_FILE, DOCKER_ENV_FILE, DOCKER_CA_FILE] + PREVIEW_TAIL
    else:
        lead = APPLY_ORDER + PREVIEW_TAIL
    return [n for n in lead if n in files] + sorted(set(files) - set(lead))


def generate(facts, options):
    """Return {filename: content}. options overrides DEFAULT_OPTIONS.

    Names may contain `/` -- the helm format emits a chart directory. write()
    creates the parent directories.
    """
    o = {**DEFAULT_OPTIONS, **options}
    # Into o itself, not read at emit time: everything downstream -- the
    # ConfigMap, the helm overlay, the READMEs, profile.json -- then speaks
    # one value, and the profile records it as the resolved option it is.
    o.update(resolve_engine_limits(facts, o))
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

    # Each of these three refuses a bad value here rather than at apply time --
    # a malformed engine quantity, an unnamed service account, an auto_update
    # that is neither a bool nor unset. Each also asks ignored_options() first,
    # so none of them refuses over a field this format has no such thing as.
    engine_size(o)
    sa = service_account(o)
    auto_update(o)

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

    if sv and o["output_format"] == "docker":
        # The same refusal as helm's above, for the same reason: a docker agent
        # can serve virtual services, but it publishes them with
        # HOSTNAME_OVERRIDE and a TLS_CERT/TLS_KEY pair, and this generator has
        # no options for that shape -- every sv_* option here is a
        # KUBERNETES_WEB_EXPOSE_* one. A command that quietly dropped them would
        # install, report idle, and never publish anything.
        raise ValueError(
            "output_format=docker covers performance testing only, and this "
            f"location is configured for service virtualization (sv_ingress="
            f"{sv['type']}). Generate it as --format manifests, or install the "
            "docker agent from BlazeMeter's own Docker Command tab and set "
            "HOSTNAME_OVERRIDE, TLS_CERT and TLS_KEY by hand.")

    if o["output_format"] == "docker":
        out = {DOCKER_RUN_FILE: _docker_run_sh(facts, o)}
        env_file = _docker_env_file(facts, o)
        if env_file:
            out[DOCKER_ENV_FILE] = env_file
        if o["ca_bundle"]:
            # The inline PEM, as the file the command mounts. The other two CA
            # modes name a ConfigMap, which is why they are in DOCKER_IGNORED:
            # there is nothing here to read one out of.
            out[DOCKER_CA_FILE] = o["ca_bundle"]
        if o["private_registry"]:
            out["bzm-opl-image-mirror.sh"] = _mirror_script(facts, o)
        out["README.md"] = _docker_readme(facts, o)
        out[PROFILE_FILE] = _profile_json(o)
        return out

    if o["output_format"] == "helm":
        out = _helm_chart_files()
        out[HELM_VALUES_FILE] = _helm_values(facts, o)
        if o["private_registry"]:
            out["bzm-opl-image-mirror.sh"] = _mirror_script(facts, o)
        if separate_pools(o):
            out[NODEPOOLS_FILE] = _nodepools_md(facts, o)
        out["README.md"] = _helm_readme(facts, o)
        out[PROFILE_FILE] = _profile_json(o)
        return out

    sub = {
        "SV_RBAC_BLOCK": _sv_rbac_block(sv),
        "NAMESPACE": o["namespace"],
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
        "SERVICE_ACCOUNT": sa,
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
        "CRANE_EPHEMERAL_STORAGE": (o["crane_ephemeral_storage"]
                                    or CRANE_EPHEMERAL_STORAGE),
        "SECRET_REF_BLOCK": (
            f"            - secretRef:\n                name: {SECRET_NAME}\n"
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
        "bzm_configmap.yaml": _configmap(facts, o),
        "bzm_role.yaml": _tpl("role.yaml").substitute(sub),
        "bzm_rolebinding.yaml": _tpl("rolebinding.yaml").substitute(sub),
        "bzm_deployment.yaml": _tpl("deployment.yaml").substitute(sub),
    }
    if o["service_account_create"]:
        # Off means the account already exists and is somebody else's object --
        # emitting it anyway would make `kubectl apply` take ownership of a
        # ServiceAccount the platform team maintains, annotations and all.
        out["bzm_serviceaccount.yaml"] = _tpl("serviceaccount.yaml").substitute(sub)
    if o["use_secret"]:
        out["bzm_secret.yaml"] = _tpl("secret.yaml").substitute(sub)
    if o["cluster_rbac"]:
        out["bzm_clusterrole.yaml"] = _tpl("clusterrole.yaml").substitute(sub)
        out["bzm_clusterrolebinding.yaml"] = _tpl("clusterrolebinding.yaml").substitute(sub)
    if ca and ca["mode"] in ("inline", "inject"):
        out["bzm_cacerts.yaml"] = _ca_configmap(facts, o)
    if o["crane_hook"]:
        out[HOOK_FILE] = _tpl("cranehook.yaml").substitute(
            sub, **_hook_sub(o, sv))
    if o["private_registry"]:
        out["bzm-opl-image-mirror.sh"] = _mirror_script(facts, o)
    if separate_pools(o):
        # Only when the engines are actually aimed elsewhere. A one-pool bundle
        # gaining a file about node pools it does not have would be one more
        # thing to read before finding the part that applies.
        out[NODEPOOLS_FILE] = _nodepools_md(facts, o)
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

    Crane publishes its own pair, but under CLUSTERIP the Ingress is unusable:
    its backend says `port.number: 8080` while the Service it created exposes
    `port: 80`, and Kubernetes resolves a backend against `spec.ports[].port`.
    Nothing claims it, so the endpoint BlazeMeter advertises 503s while the mock
    serves happily inside the cluster. (Under NODEPORT the Service publishes
    8080 and crane's constant matches, so the mismatch does not arise -- this
    command is then unnecessary, though harmless: it selects on pod labels and
    is indifferent to the Service crane made.)

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

# Options whose value is a credential. Everything that hands options to someone
# else reads this set rather than naming auth_token: profile.json leaves them
# out, and the MCP layer refuses to echo them back. One name to add to if a
# second credential ever becomes an option -- the failure mode of forgetting is
# a token in a file people paste into tickets.
SECRET_OPTIONS = frozenset({"auth_token"})


def _profile_json(o):
    """The resolved options, replayable with `generate --profile`. AUTH_TOKEN is
    left out on purpose, and stays out: #64's wording offered to source the token
    from "--auth-token, or a profile", and that half is superseded. A profile is
    the file people commit, diff, paste into tickets and hand to a colleague, and
    a credential written into one reaches everybody who ever sees it, for as long
    as the file exists. The token in the *bundle beside it* is what a regenerate
    reads back instead -- see core.resolve_auth_token."""
    return json.dumps(
        {k: v for k, v in sorted(o.items()) if k not in SECRET_OPTIONS},
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
    "bzm_serviceaccount.yaml", "bzm_configmap.yaml", "bzm_secret.yaml",
    "bzm_cacerts.yaml", "bzm_role.yaml", "bzm_rolebinding.yaml",
    "bzm_clusterrole.yaml", "bzm_clusterrolebinding.yaml",
    "bzm_deployment.yaml",
]


def _readme(facts, o, files):
    """Same brief as _helm_readme. The engine request gap and the LimitRange
    history are real but belong in the project README, not in a handover."""
    ns, cli = o["namespace"], "oc" if o["platform"] == "openshift" else "kubectl"
    apply_lines = "\n".join(
        f"{cli} -n {ns} apply -f {f}" for f in APPLY_ORDER if f in files)
    # Client-side apply copies the object into the last-applied-configuration
    # annotation, which the API server caps at 256KB.
    big_ca = ""
    if o["ca_bundle"] and len(o["ca_bundle"]) > 200_000:
        big_ca = (f"\n- The CA bundle is {len(o['ca_bundle']) // 1024}KB, so apply "
                  f"`bzm_cacerts.yaml` with `--server-side` -- client-side apply "
                  f"stores a copy in an annotation capped at 256KB.")
    token_row = [("AUTH_TOKEN", "in bzm_secret.yaml" if o["use_secret"]
                  else "in bzm_configmap.yaml (plain text)")]
    # On the resolved value, not on an explicit false: off is the default now,
    # so the common bundle is the one that needs telling. Whoever receives it
    # is the person who has to notice the agent ageing, and nothing else in the
    # bundle says the agent will not do it for them.
    pinned = ""
    if not auto_update(o):
        pinned = (f"\n- Auto-update is **off**, so the agent stays on "
                  f"`{_crane_image(facts, o).rsplit(':', 1)[1]}` until you "
                  f"re-generate\n  and re-apply. An agent far enough behind "
                  f"loses BlazeMeter support.")
    return f"""{_bundle_table(facts, o, token_row)}
## Deploy

{_deploy_steps(o, "Apply")}```
{apply_lines}
```

{_verify_block(o)}
## Worth knowing

{_sizing_bullet(o)}{_location_bullet(facts)}{_sa_bullet(o)}
- Engine *requests* come from the location, not this bundle: `overrideCPU` and
  `overrideMemory` under Settings -> Private Locations, defaulting to
  {ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM}. The scheduler places pods on requests, so unless you set
  them to match the limits above, a run competes for CPU it never reserved.{pinned}{big_ca}
- `bzm-opl-gen doctor` checks a cluster against all of the above before you apply.
"""


def write(files, outdir):
    os.makedirs(outdir, exist_ok=True)
    for name, content in files.items():
        path = os.path.join(outdir, name)
        # The helm format keys files by chart-relative path, so a name can carry
        # directories that do not exist yet.
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        # A bundle whose mirror script needs chmod first is a bundle with an
        # undocumented step. Set here rather than by whoever happens to be
        # writing, because this is the function that knows a .sh was emitted --
        # the zip download had the bit and `generate -o out` did not.
        if name.endswith(".sh"):
            os.chmod(path, os.stat(path).st_mode | 0o111)
    return sorted(files)
