"""Pre-flight: can this cluster actually run the location's concurrency?

`generate` renders manifests that apply cleanly; that says nothing about
whether an engine can be *scheduled*. A location with slots=5 and the
documented 2 CPU / 8Gi engine needs 10 CPU and 40Gi of schedulable capacity,
plus quota, plus a LimitRange that does not fight the sizing, plus admission
that accepts the engine pods crane spawns. When any of that is missing the
customer sees a test that never starts -- no manifest error, no crane error,
just a run stuck in "initializing".

Every check here is a pure function over already-fetched data (`Check` list),
so the whole doctor is testable offline; the only impure parts are
gather_cluster() / probe_egress(), which are thin.

FAIL = a test would not start. WARN = the numbers are wrong or it will bite
later, but a test still starts.
"""

import collections
import json
import os
import subprocess

from . import livetest
from .api import API_BASE, DEFAULT_THREADS_PER_ENGINE
from .generate import (CRANE_CPU_LIMIT, CRANE_MEM_LIMIT, DEFAULT_OPTIONS,
                       ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM, ENGINE_DISK_GB,
                       ENGINE_TMP_GB,
                       ENGINE_STAMPED_REQUEST_CPU, ENGINE_STAMPED_REQUEST_MEM,
                       SV_INGRESS_BACKENDS, engine_size, proxy_env)
from .quantity import (format_cpu, format_memory, human_memory, parse_cpu,
                       parse_memory)

Check = collections.namedtuple("Check", "name status detail")
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

GB = 10 ** 9                     # the docs quote decimal GB, not GiB

API_PROBE_URL = f"{API_BASE}/web/version"
# Engines upload results and artifacts to hosts crane itself never contacts, so
# an egress rule shaped around crane alone passes here and still fails a run.
# Same hosts livetest looks for in the proxy log -- one list, not two.
ENGINE_PROBE_URLS = tuple(f"https://{h}/" for h in livetest.ENGINE_UPLOAD_HOSTS)
CURL_IMAGE = "curlimages/curl:8.11.1"


def has_failures(checks):
    return any(c.status == FAIL for c in checks)


# -- location -----------------------------------------------------------------

def check_location(facts, opts, cluster):
    """The two fields BlazeMeter itself needs before it will hand a run to this
    location."""
    checks = []
    slots = facts.get("slots")
    if not slots:
        checks.append(Check("location slots", FAIL,
                            "the location advertises no slots -- BlazeMeter has "
                            "nowhere to place a run"))
    else:
        checks.append(Check("location slots", PASS,
                            f"{slots} concurrent engine(s)"))
    tpe = facts.get("threads_per_engine")
    if not tpe:
        # A location created via the API has this null (POST ignores it), and
        # every start then fails 403 "Not enough available resources" -- with no
        # hint that a scalar field is the reason.
        checks.append(Check("location threadsPerEngine", FAIL,
                            "threadsPerEngine is unset -- every test start fails "
                            "with 403 'Not enough available resources'. Set it in "
                            "Settings -> Private Locations"))
    else:
        checks.append(Check("location threadsPerEngine", PASS, f"{tpe} threads"))
    return checks


def check_threads_per_engine(facts, opts, cluster):
    """Threads the location promises per engine vs what the engine is sized for.

    BlazeMeter's own default pairs 500 threads with a 2 CPU / 8Gi engine, so
    scale that linearly on whichever of the two ratios is smaller: 500 threads
    on a 1 CPU / 4Gi engine is not a runnable location, it is a location that
    OOM-kills or throttles halfway up the ramp.
    """
    tpe = facts.get("threads_per_engine")
    if not tpe:
        return []                     # check_location already FAILs on this
    cpu, mem = engine_size(opts)
    base_cpu, base_mem = parse_cpu(ENGINE_DEFAULT_CPU), parse_memory(ENGINE_DEFAULT_MEM)
    ratio = min(cpu / base_cpu, mem / base_mem)
    supported = int(DEFAULT_THREADS_PER_ENGINE * ratio)
    size = _engine_str(cpu, mem)
    if tpe > supported:
        return [Check("threadsPerEngine vs engine size", WARN,
                      f"{tpe} threads on a {size} engine; that size supports about "
                      f"{supported} ({DEFAULT_THREADS_PER_ENGINE} threads per "
                      f"{ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM}). Lower "
                      f"threadsPerEngine or raise the engine limits")]
    return [Check("threadsPerEngine vs engine size", PASS,
                  f"{tpe} threads on a {size} engine (supports ~{supported})")]


# -- node eligibility ---------------------------------------------------------

def _ready(node):
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in node.get("status", {}).get("conditions", []))


def _tolerates(toleration, taint):
    """k8s toleration semantics, enough of them to be honest about scheduling."""
    if toleration.get("effect") and toleration["effect"] != taint.get("effect"):
        return False
    key = toleration.get("key")
    if key and key != taint.get("key"):
        return False
    if toleration.get("operator", "Equal") == "Exists":
        return True
    if not key:
        return False                  # Equal with an empty key matches nothing
    return toleration.get("value", "") == taint.get("value", "")


def eligible_nodes(nodes, opts):
    """Nodes an engine could actually land on: Ready, uncordoned, matching the
    configured nodeSelector, and with every blocking taint tolerated.

    PreferNoSchedule is a preference, not a rejection, so it does not exclude.
    """
    selector = opts.get("node_selector") or {}
    tolerations = opts.get("tolerations") or []
    out = []
    for n in nodes:
        spec, meta = n.get("spec", {}), n.get("metadata", {})
        if spec.get("unschedulable") or not _ready(n):
            continue
        labels = meta.get("labels") or {}
        if any(labels.get(k) != v for k, v in selector.items()):
            continue
        blocking = [t for t in spec.get("taints", [])
                    if t.get("effect") in ("NoSchedule", "NoExecute")]
        if any(not any(_tolerates(tol, t) for tol in tolerations) for t in blocking):
            continue
        out.append(n)
    return out


def _allocatable(node):
    """(cpu_millicores, mem_bytes) the node advertises as schedulable. Not what
    is free -- that needs every pod's requests summed per node, a much bigger
    read than a preflight should do."""
    alloc = node.get("status", {}).get("allocatable", {})
    return parse_cpu(alloc.get("cpu", "0")), parse_memory(alloc.get("memory", "0"))


def _engine_str(cpu, mem):
    return f"{format_cpu(cpu)} CPU / {format_memory(mem)}"


def _scope(opts):
    bits = []
    if opts.get("node_selector"):
        bits.append(f"nodeSelector {json.dumps(opts['node_selector'])}")
    if opts.get("tolerations"):
        bits.append(f"{len(opts['tolerations'])} toleration(s)")
    return ", ".join(bits) or "no nodeSelector/tolerations"


# -- capacity -----------------------------------------------------------------

def check_capacity(facts, opts, cluster):
    """slots x engine size vs what the eligible nodes can hold.

    Two checks, because they fail differently: a pod is not splittable across
    nodes, so 'the cluster has 40Gi free' does not mean an 8Gi engine fits
    anywhere.
    """
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    want = _engine_str(cpu, mem)
    nodes = eligible_nodes(cluster.get("nodes") or [], opts)
    if not nodes:
        return [Check("capacity: eligible nodes", FAIL,
                      f"no Ready, schedulable node matches {_scope(opts)} -- "
                      f"engines have nowhere to run")]

    sizes = {n["metadata"]["name"]: _allocatable(n) for n in nodes}
    fits = [name for name, (c, m) in sizes.items() if c >= cpu and m >= mem]
    biggest = max(sizes.items(), key=lambda kv: (kv[1][1], kv[1][0]))
    checks = []
    if fits:
        checks.append(Check("capacity: per-node fit", PASS,
                            f"{len(fits)}/{len(nodes)} eligible node(s) hold one "
                            f"{want} engine (allocatable, not free)"))
    else:
        checks.append(Check("capacity: per-node fit", FAIL,
                            f"no eligible node has {want} allocatable (an upper "
                            f"bound -- other workloads already use part of it); "
                            f"the largest is {biggest[0]} with "
                            f"{format_cpu(biggest[1][0])} CPU / "
                            f"{human_memory(biggest[1][1])}. An engine is one pod; "
                            f"it cannot be split across nodes"))

    # Crane occupies the namespace alongside the engines; the quota check counts
    # its pod, so the capacity maths has to spend its share too.
    crane_cpu, crane_mem = parse_cpu(CRANE_CPU_LIMIT), parse_memory(CRANE_MEM_LIMIT)
    tot_cpu = max(sum(c for c, _ in sizes.values()) - crane_cpu, 0)
    tot_mem = max(sum(m for _, m in sizes.values()) - crane_mem, 0)
    holds = min(tot_cpu // cpu, tot_mem // mem)
    total = (f"{len(nodes)} eligible node(s) leave {format_cpu(tot_cpu)} CPU / "
             f"{human_memory(tot_mem)} after crane's own "
             f"{_engine_str(crane_cpu, crane_mem)} -- an upper bound, other "
             f"workloads already use part of the rest")
    if holds < slots:
        checks.append(Check("capacity: aggregate", FAIL,
                            f"slots={slots} needs {format_cpu(cpu * slots)} CPU / "
                            f"{format_memory(mem * slots)}; {total}, so the cluster "
                            f"holds {holds} engine(s) at most"))
    else:
        checks.append(Check("capacity: aggregate", PASS,
                            f"slots={slots} needs {format_cpu(cpu * slots)} CPU / "
                            f"{format_memory(mem * slots)}; {total} ({holds} engine(s))"))
    return checks


def check_disk(facts, opts, cluster):
    """Ephemeral storage per eligible node against the documented engine
    footprint. WARN, not FAIL: a short run may never fill it -- but an engine
    that does gets evicted mid-test, which reads as a random failure."""
    slots = facts.get("slots") or 1
    nodes = eligible_nodes(cluster.get("nodes") or [], opts)
    if not nodes:
        return [Check("node disk", WARN,
                      f"no eligible node to measure against the documented "
                      f"{ENGINE_DISK_GB}GB per engine")]
    per = {}
    for n in nodes:
        raw = n.get("status", {}).get("allocatable", {}).get("ephemeral-storage")
        per[n["metadata"]["name"]] = parse_memory(raw) if raw else 0
    holds = {name: b // (ENGINE_DISK_GB * GB) for name, b in per.items()}
    footprint = (f"{ENGINE_DISK_GB}GB per engine ({ENGINE_TMP_GB}GB of it /tmp)")
    if max(holds.values()) == 0:
        worst = max(per.items(), key=lambda kv: kv[1])
        return [Check("node disk", WARN,
                      f"no eligible node has {footprint}; the largest is "
                      f"{worst[0]} with {worst[1] // GB}GB allocatable ephemeral "
                      f"storage -- engines that fill it are evicted mid-run")]
    if sum(holds.values()) < slots:
        return [Check("node disk", WARN,
                      f"eligible nodes fit {sum(holds.values())} concurrent "
                      f"engine(s) at {footprint}, but the location advertises "
                      f"slots={slots}")]
    return [Check("node disk", PASS,
                  f"eligible nodes fit {sum(holds.values())} concurrent engine(s) "
                  f"at {footprint} (slots={slots})")]


# -- LimitRange / ResourceQuota ----------------------------------------------

_LR_TYPES = ("Container", "Pod")


def check_limitrange(facts, opts, cluster):
    """An existing LimitRange can reject the engine pod outright -- max below
    its limits, min above the requests crane stamps, or a maxLimitRequestRatio
    tighter than the gap between the two -- and can rewrite the resources of any
    pod in the namespace that declares none. Neither shows up in the manifests."""
    limitranges = cluster.get("limitranges") or []
    cpu, mem = engine_size(opts)
    if not limitranges:
        if opts.get("emit_limitrange"):
            return [Check("limitrange", PASS,
                          "none in the namespace; the generated "
                          "bzm_limitrange.yaml caps it at the engine size")]
        # No LimitRange means no ceiling on the namespace either -- and the
        # engines are already scheduling small (see the detail), which nothing
        # here can change.
        return [Check("limitrange", WARN,
                      f"no LimitRange in the namespace and the profile does not "
                      f"emit one, so nothing caps what the namespace may ask "
                      f"for. Separately, engine pods request "
                      f"{ENGINE_STAMPED_REQUEST_CPU}/{ENGINE_STAMPED_REQUEST_MEM} "
                      f"rather than {_engine_str(cpu, mem)} because crane sets "
                      f"that explicitly -- a LimitRange cannot override it")]

    # (field, parse, the engine's own value for it, how to show it)
    dims = (("cpu", parse_cpu, cpu, format_cpu, parse_cpu(ENGINE_STAMPED_REQUEST_CPU)),
            ("memory", parse_memory, mem, format_memory,
             parse_memory(ENGINE_STAMPED_REQUEST_MEM)))
    checks = []
    for lr in limitranges:
        name = lr.get("metadata", {}).get("name", "?")
        blocking, conflicts = [], []
        for item in lr.get("spec", {}).get("limits", []):
            if item.get("type") not in _LR_TYPES:
                continue
            for key, parse, limit, show, stamped in dims:
                mx = (item.get("max") or {}).get(key)
                if mx and parse(mx) < limit:
                    blocking.append(f"max {key} {mx} < engine {show(limit)}")
                # min rejects from below just as max does from above, measured
                # against the requests crane stamps, not the engine's limits.
                mn = (item.get("min") or {}).get(key)
                if mn and parse(mn) > stamped:
                    blocking.append(f"min {key} {mn} > the {show(stamped)} "
                                    f"crane requests")
                # An engine's own limit/request ratio is large precisely because
                # crane requests so little of what it limits.
                ratio = (item.get("maxLimitRequestRatio") or {}).get(key)
                if ratio and limit / stamped > float(ratio):
                    blocking.append(f"maxLimitRequestRatio {key} {ratio} < the "
                                    f"engine's own {limit / stamped:.0f}x "
                                    f"({show(stamped)} requested, "
                                    f"{show(limit)} limit)")
                for field in ("defaultRequest", "default"):
                    value = (item.get(field) or {}).get(key)
                    if value and parse(value) != limit:
                        conflicts.append(f"{field}.{key} {value}")
        if blocking:
            checks.append(Check(f"limitrange {name}", FAIL,
                                f"LimitRange '{name}' rejects the engine pod at "
                                f"admission: {'; '.join(blocking)} (crane itself "
                                f"needs {CRANE_CPU_LIMIT}/{CRANE_MEM_LIMIT})"))
        if conflicts:
            # Every LimitRange in the namespace is enforced, but when more than
            # one supplies defaults there is no defined winner -- so ours does
            # not simply take over from this one.
            checks.append(Check(f"limitrange {name} defaults", WARN,
                                f"LimitRange '{name}' sets {', '.join(conflicts)} "
                                f"against an engine of {_engine_str(cpu, mem)} "
                                f"-- adding our bzm_limitrange.yaml gives the "
                                f"namespace two sources of defaults with no "
                                f"defined winner; drop one"))
        if not blocking and not conflicts:
            checks.append(Check(f"limitrange {name}", PASS,
                                f"LimitRange '{name}' is compatible with a "
                                f"{_engine_str(cpu, mem)} engine"))
    return checks


# Quota keys we can compare against an engine's claim. 'cpu'/'memory' are the
# API's aliases for the requests.* forms.
_QUOTA_CPU = ("requests.cpu", "limits.cpu", "cpu")
_QUOTA_MEM = ("requests.memory", "limits.memory", "memory")


def check_resourcequota(facts, opts, cluster):
    """hard - used, per resource, against slots x engine (+1 pod for crane)."""
    quotas, limitranges = cluster.get("quotas") or [], cluster.get("limitranges")
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    if not quotas:
        return [Check("resourcequota", PASS, "no ResourceQuota in the namespace")]

    # (quota keys, parse, format free, format needed, what slots need)
    dimensions = ((_QUOTA_CPU, parse_cpu, format_cpu, format_cpu, cpu * slots),
                  (_QUOTA_MEM, parse_memory, human_memory, format_memory, mem * slots))
    checks, constrains = [], None
    for q in quotas:
        name = q.get("metadata", {}).get("name", "?")
        hard = q.get("status", {}).get("hard") or q.get("spec", {}).get("hard") or {}
        used = q.get("status", {}).get("used") or {}
        short = []
        for keys, parse, show_free, show_need, need in dimensions:
            for key in keys:
                if key not in hard:
                    continue
                constrains = constrains or name
                free = parse(hard[key]) - parse(used.get(key, "0"))
                if free < need:
                    short.append((key, f"{show_free(free)} free, "
                                       f"{show_need(need)} needed"))
        if "pods" in hard:
            free = int(hard["pods"]) - int(used.get("pods", 0))
            if free < slots + 1:            # slots engines + the crane pod
                short.append(("pods", f"{free} free, {slots + 1} needed "
                                      f"({slots} engine(s) + crane)"))
        checks += [Check(f"quota {name} {key}", FAIL,
                         f"ResourceQuota '{name}' cannot fit slots={slots}: "
                         f"{key} {detail}")
                   for key, detail in short]
        if not short:
            checks.append(Check(f"quota {name}", PASS,
                                f"ResourceQuota '{name}' has room for slots="
                                f"{slots} ({format_cpu(cpu * slots)} / "
                                f"{format_memory(mem * slots)}, {slots + 1} pods)"))
    if constrains and not limitranges and not opts.get("emit_limitrange"):
        # With a cpu/memory quota in force the API server rejects any pod that
        # does not declare that resource -- and crane sets no requests on the
        # engines it spawns, so something has to supply them.
        checks.append(Check("quota defaults", WARN,
                            f"ResourceQuota '{constrains}' constrains "
                            f"cpu/memory, so every pod must declare requests and "
                            f"limits; crane sets none on engine pods. Supply them "
                            f"with a LimitRange (regenerate with emit_limitrange)"))
    return checks


# -- admission ----------------------------------------------------------------

PSA_ENFORCE = "pod-security.kubernetes.io/enforce"
SCC_UID_RANGE = "openshift.io/sa.scc.uid-range"


def check_admission(facts, opts, cluster):
    """Will the namespace's admission posture accept the *engine* pods?

    Our crane pod satisfies restricted PSA (runAsNonRoot, no privilege
    escalation, drop ALL, RuntimeDefault seccomp). The engines crane spawns are
    a different matter: they only get KUBERNETES_SECURITY_CONTEXT_CAP_JSON and
    INHERIT_RUNNING_USER_AND_GROUP on the openshift path, so under restricted
    PSA on plain k8s they are rejected after crane is already happily online.
    """
    namespace_obj = cluster.get("namespace") or {}
    meta = namespace_obj.get("metadata") or {}
    platform = opts.get("platform") or "openshift"
    if not namespace_obj:
        return [Check("admission", WARN,
                      "the namespace does not exist yet -- its PodSecurity / SCC "
                      "posture cannot be read; re-run the doctor after creating it")]
    if platform == "openshift":
        if (meta.get("annotations") or {}).get(SCC_UID_RANGE):
            return [Check("admission (SCC)", PASS,
                          f"{SCC_UID_RANGE}="
                          f"{meta['annotations'][SCC_UID_RANGE]}; engines inherit "
                          f"crane's SCC-assigned UID")]
        return [Check("admission (SCC)", WARN,
                      f"namespace has no {SCC_UID_RANGE} annotation -- SCC has "
                      f"assigned no UID range, so INHERIT_RUNNING_USER_AND_GROUP "
                      f"has nothing to inherit and engine pods may be rejected")]
    enforce = (meta.get("labels") or {}).get(PSA_ENFORCE)
    if enforce == "restricted":
        return [Check("admission (PodSecurity)", FAIL,
                      f"{PSA_ENFORCE}=restricted with platform=k8s: crane passes, "
                      f"but the engine pods it spawns get no security context "
                      f"(the generator emits KUBERNETES_SECURITY_CONTEXT_CAP_JSON "
                      f"/ INHERIT_RUNNING_USER_AND_GROUP only for platform="
                      f"openshift), so runs fail after the agent is online. Use "
                      f"enforce=baseline for this namespace")]
    if enforce:
        return [Check("admission (PodSecurity)", PASS,
                      f"{PSA_ENFORCE}={enforce} admits the engine pods")]
    return [Check("admission (PodSecurity)", WARN,
                  f"namespace has no {PSA_ENFORCE} label -- no enforcement is "
                  f"configured, so nothing here is checked at admission time "
                  f"(a cluster-wide default may still apply)")]


# -- service virtualization ---------------------------------------------------

# Crane writes `ingressClassName: nginx` on the Ingress it creates per virtual
# service and BlazeMeter exposes no env to change it, so the name is ours to
# check, not to configure. It matches the `nginx` sv_ingress value only by
# coincidence -- keep the two apart so renaming either does not silently change
# which branch below runs.
CRANE_INGRESS_CLASS = "nginx"
OPENSHIFT_ROUTE_CONTROLLER = "openshift.io/ingress-to-route"


def check_ingress_class(facts, opts, cluster):
    """Will anything claim the Ingress crane creates for a virtual service?

    With no IngressClass named `nginx` no controller adopts it, no route is
    created, and the endpoint BlazeMeter publishes returns 503 -- while the
    virtual service itself is healthy and serving in-cluster. Nothing in the
    deploy fails, so a preflight is the only place this is visible. On
    OpenShift the only shipped class is `openshift-default`, which makes that
    the default outcome there rather than an unlucky one.
    """
    ingress = opts.get("sv_ingress")
    if not ingress:
        return []                     # not an SV deployment; nothing to say
    backend = SV_INGRESS_BACKENDS.get(ingress)
    if backend is None:
        # generate() rejects anything outside SV_INGRESS_TYPES, so this only
        # shows up for a hand-written profile. Say so rather than checking a
        # class name that such a deployment may never ask for.
        known = "', '".join(SV_INGRESS_BACKENDS)
        return [Check("sv ingress class", WARN,
                      f"unrecognised sv_ingress={ingress}; expected one of "
                      f"'{known}', so the ingress path is unverified")]
    if not backend.via_ingress_class:
        # Verified on Istio 1.30 and Contour v1.33: neither registers an
        # IngressClass, so treating "none found" as a failure here would fail
        # every correctly-installed cluster of both.
        return [Check("sv ingress class", PASS,
                      f"sv_ingress={ingress} routes through the "
                      f"{backend.creates} crane creates, not an IngressClass")]

    classes = cluster.get("ingressclasses")
    if classes is None:
        # Older cluster_data, or an API server that does not serve the kind.
        return [Check("sv ingress class", WARN,
                      f"IngressClasses could not be read, so the "
                      f"'{CRANE_INGRESS_CLASS}' class crane requires is "
                      f"unverified")]
    by_name = {c.get("metadata", {}).get("name"): c for c in classes}
    mine = by_name.get(CRANE_INGRESS_CLASS)
    if mine is None:
        existing = ", ".join(sorted(n for n in by_name if n)) or "none at all"
        return [Check("sv ingress class", FAIL,
                      f"no IngressClass named '{CRANE_INGRESS_CLASS}' -- crane "
                      f"hardcodes ingressClassName: {CRANE_INGRESS_CLASS} on the "
                      f"Ingress it creates per virtual service and BlazeMeter has "
                      f"no env to change it, so nothing claims it: the published "
                      f"endpoint returns 503 while the virtual service stays "
                      f"healthy and serving in-cluster. IngressClasses present: "
                      f"{existing}. Install an nginx ingress controller, or have "
                      f"a cluster-admin create an IngressClass named "
                      f"'{CRANE_INGRESS_CLASS}'")]
    controller = (mine.get("spec") or {}).get("controller") or "?"
    detail = (f"IngressClass '{CRANE_INGRESS_CLASS}' exists (controller "
              f"{controller}) to claim the Ingress crane creates")
    if controller == OPENSHIFT_ROUTE_CONTROLLER:
        # Verified live: crane's Ingress backend uses port.number 8080 while the
        # Service it created exposes port 80, and this controller resolves the
        # backend against spec.ports[].port -- so it logs
        # IncompleteIngressToRouteRules and creates no Route.
        detail += (f"; note that this controller resolves the backend port "
                   f"against the Service's port 80 and crane writes 8080, so it "
                   f"reports IncompleteIngressToRouteRules and creates no Route "
                   f"(upstream defect -- see README)")
    return [Check("sv ingress class", PASS, detail)]


# -- egress -------------------------------------------------------------------

def egress_targets(opts):
    """What has to be reachable from inside the namespace before a run works."""
    targets = [API_PROBE_URL, *ENGINE_PROBE_URLS]
    reg = opts.get("private_registry")
    if reg:
        targets.append(f"https://{reg.split('/')[0]}/v2/")
    return targets


def check_egress(facts, opts, cluster):
    """Pure verdict over {target: curl returncode}; None = we could not probe."""
    probes = cluster.get("probes")
    if not probes:
        return [Check("egress", WARN,
                      "egress could not be probed from inside the cluster -- "
                      "no crane pod and no way to run a one-shot probe pod")]
    checks = []
    for target, rc in probes.items():
        name = f"egress {target.split('/')[2]}"
        if rc == 0:
            checks.append(Check(name, PASS, f"{target} reachable from the namespace"))
        elif rc is None:
            checks.append(Check(name, WARN,
                                f"{target} could not be probed with the profile's "
                                f"proxy/CA honoured -- verdict unknown"))
        else:
            checks.append(Check(name, FAIL,
                                f"{target} unreachable (curl rc={rc}); if the "
                                f"cluster egresses through a proxy or a custom CA, "
                                f"the profile must configure both -- the agent "
                                f"will not come online without this"))
    return checks


# -- impure layer -------------------------------------------------------------

def gather_cluster(cli, namespace):
    """Everything the checks read, in as few API round trips as it takes.
    LimitRanges and ResourceQuotas are both namespaced, so one `get` covers
    them; splitting the result by kind is cheaper than a second call."""
    scoped = livetest.kget(cli, namespace, "limitrange,resourcequota").get("items", [])
    by_kind = {"LimitRange": [], "ResourceQuota": []}
    for item in scoped:
        by_kind.setdefault(item.get("kind"), []).append(item)
    # Cluster-scoped like nodes, but kept its own get: kget reports a failed
    # command as {}, so folding the kinds into one call would lose the nodes too
    # on a cluster whose API server does not serve IngressClass.
    #
    # "served the kind, has none" and "could not ask" are different answers and
    # check_ingress_class reports them differently -- [] is a FAIL because we
    # know nothing will claim crane's Ingress, None is a WARN because we did not
    # look. `.get("items", [])` would collapse both to [], turning an unreadable
    # cluster into a hard failure with a non-zero exit.
    ingressclasses = livetest.kget(cli, None, "ingressclass")
    return {
        "nodes": livetest.kget(cli, None, "nodes").get("items", []),
        "ingressclasses": ingressclasses.get("items") if ingressclasses else None,
        "limitranges": by_kind["LimitRange"],
        "quotas": by_kind["ResourceQuota"],
        "namespace": livetest.kget(cli, None, "ns", namespace),
    }


def _ca_configured(opts):
    return bool(opts.get("ca_bundle") or opts.get("ca_existing_configmap")
                or opts.get("ca_openshift_inject"))


def _rc_lines(output, targets):
    """Parse the `<url> rc=<n>` lines one shell emitted for all the targets.
    A target with no line never ran -> None (unknown), never a FAIL."""
    rcs = {t: None for t in targets}
    for line in output.splitlines():
        url, _, rc = line.strip().partition(" rc=")
        if rc.strip().lstrip("-").isdigit() and url in rcs:
            rcs[url] = int(rc.strip())
    return rcs


def _curl_script(targets, cacert=False, settle=0):
    """One shell running every probe, so a doctor costs one exec (or one pod)
    rather than one per target -- each is a process spawn plus the API round
    trips to resolve and attach to a pod.

    Every probe is retried once: a freshly created pod can lose its first DNS
    lookup (curl rc=6) before CoreDNS answers for it, and a doctor that reports
    a FAIL it cannot reproduce is worse than one that says nothing. `settle`
    delays the first probe so `kubectl run -i` has finished attaching -- output
    written before that is simply dropped.
    """
    ca = ' --cacert "$REQUESTS_CA_BUNDLE"' if cacert else ""
    probe = (f"curl -s -o /dev/null --max-time 20{ca} %s || "
             f"{{ sleep 2; curl -s -o /dev/null --max-time 20{ca} %s; }}")
    lines = [f'{probe % (t, t)}; echo "{t} rc=$?"' for t in targets]
    return "; ".join(([f"sleep {settle}"] if settle else []) + lines)


def probe_egress(cli, namespace, opts):
    """curl each target from inside the cluster -> {target: returncode}.

    Preferably from the crane pod: it is the only place the profile's proxy env
    and CA bundle are actually in force, which is what the customer's egress
    depends on. A one-shot pod is the fallback, and cannot verify a corporate
    CA at all -- that reports None (WARN), never a FAIL we cannot stand behind.
    """
    targets = egress_targets(opts)
    if livetest.kget(cli, namespace, "deploy", "crane"):
        out = livetest._crane_exec(cli, namespace,
                                   _curl_script(targets, _ca_configured(opts)))
        return _rc_lines(out, targets)
    if _ca_configured(opts):
        return {t: None for t in targets}
    return _oneshot_curl(cli, namespace, targets, opts)


def _oneshot_curl(cli, namespace, targets, opts):
    """Probe from a single throwaway pod when crane is not deployed yet -- one
    image pull and schedule for all the targets, not one each."""
    env = [arg for name, value in proxy_env(opts).items()
           for arg in ("--env", f"{name}={value}")]
    print(f"  probing egress from a throwaway {CURL_IMAGE} pod in {namespace} "
          f"(crane is not deployed yet)")
    out = subprocess.run(
        [cli, "-n", namespace, "run", f"bzm-doctor-{os.getpid()}", "--rm", "-i",
         "--restart=Never", "--image", CURL_IMAGE, *env, "--command", "--",
         "sh", "-c", _curl_script(targets, settle=2)],
        capture_output=True, text=True)
    return _rc_lines(out.stdout, targets)


# Every check takes the same (facts, opts, cluster) so adding one is a single
# edit here, not a new argument order to remember.
CHECKS = (check_location, check_threads_per_engine, check_capacity, check_disk,
          check_limitrange, check_resourcequota, check_admission,
          check_ingress_class, check_egress)


def run(facts, opts, namespace, cluster_data=None, probes=None, cli=None):
    """Run every check and print the verdict list. Returns the Check list; the
    caller decides the exit code (see has_failures)."""
    opts = dict(opts or {})
    namespace = (namespace or opts.get("namespace")
                 or DEFAULT_OPTIONS["namespace"])
    if cluster_data is None or probes is None:
        cli = cli or livetest.cli_tool()
    if cluster_data is None:
        cluster_data = gather_cluster(cli, namespace)
    if probes is None:
        probes = probe_egress(cli, namespace, opts)

    cluster = {**cluster_data, "probes": probes}
    checks = [c for check in CHECKS for c in check(facts, opts, cluster)]
    _report(checks, facts, namespace)
    return checks


def _report(checks, facts, namespace):
    print(f"doctor: location {facts.get('harbor_name')} "
          f"({facts.get('harbor_id')}), namespace {namespace}")
    width = max((len(c.name) for c in checks), default=0)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
    counts = collections.Counter(c.status for c in checks)
    print(f"{counts[PASS]} passed, {counts[WARN]} warning(s), "
          f"{counts[FAIL]} failure(s)"
          + ("" if not counts[FAIL] else
             " -- a test would not start on this location as configured"))
