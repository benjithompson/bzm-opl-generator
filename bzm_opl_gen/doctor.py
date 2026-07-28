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
gather_cluster() / probe_egress(), which are thin. That data can equally come
from an evidence file collected on a cluster nobody here can reach
(cluster_from_evidence, the twin of facts.manual()), and no check can tell the
difference -- it is the same shape either way. evaluate() returns the verdicts,
run() prints them.

FAIL = a test would not start. WARN = the numbers are wrong or it will bite
later, but a test still starts.
"""

import collections
import json
import os
import subprocess

from . import livetest
# Aliased because every check takes a `facts` argument, which takes the name.
from . import facts as facts_mod
from .api import API_BASE, DEFAULT_THREADS_PER_ENGINE
from .generate import (CRANE_CPU_LIMIT, CRANE_MEM_LIMIT, DEFAULT_OPTIONS,
                       ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM, ENGINE_DISK_GB,
                       ENGINE_TMP_GB,
                       ENGINE_STAMPED_REQUEST_CPU, ENGINE_STAMPED_REQUEST_MEM,
                       SV_INGRESS_BACKENDS, engine_size, proxy_env,
                       service_account)
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


def _unread_section(cluster, key, name, detail):
    """The branch every check that reads a cluster section opens with.

    A section is None when nobody could look -- a denied `list nodes`, an API
    server that does not serve the kind, or an evidence file whose collector was
    refused it. It is never None for "we looked and there are none": that
    arrives as [] or {}, and the two get opposite verdicts. An unread section is
    a WARN and exits 0; an empty one can be the FAIL that "no eligible node" or
    "no IngressClass named nginx" is, and claiming either off a read we were
    denied is a claim nothing here can stand behind.

    Returns the verdict to hand straight back, or None to carry on. `detail`
    stays the caller's, because what an unread section costs is specific to the
    question being asked of it -- only the branch is shared, so a check that
    reads a new section gets it by naming its section rather than by its author
    remembering the rule.
    """
    if cluster.get(key) is None:
        return [Check(name, WARN, detail)]
    return None


# -- location -----------------------------------------------------------------

def check_location(facts, opts, cluster):
    """The two fields BlazeMeter itself needs before it will hand a run to this
    location.

    Both come from the account, so both are None on manually-entered facts --
    the same None a real location with them unset produces, and only that second
    case is a misconfiguration. The value cannot tell them apart; the marker the
    facts already carry for how they arrived can, and it is read here rather
    than folded into the facts, so nothing that generates learns the difference.
    A typed 0 is still a FAIL: that is a value someone supplied.
    """
    typed_by_hand = facts_mod.from_manual_entry(facts)
    checks = []
    slots = facts.get("slots")
    if slots is None and typed_by_hand:
        checks.append(Check("location slots", WARN,
                            "unknown -- these facts were entered by hand, and "
                            "slots is only readable from the account. Confirm it "
                            "is set in Settings -> Private Locations"))
    elif not slots:
        checks.append(Check("location slots", FAIL,
                            "the location advertises no slots -- BlazeMeter has "
                            "nowhere to place a run"))
    else:
        checks.append(Check("location slots", PASS,
                            f"{slots} concurrent engine(s)"))
    tpe = facts.get("threads_per_engine")
    if tpe is None and typed_by_hand:
        checks.append(Check("location threadsPerEngine", WARN,
                            "unknown -- entered by hand, so there was no account "
                            "to read it from. Unset, every test start fails with "
                            "403 'Not enough available resources', so check it in "
                            "Settings -> Private Locations"))
    elif not tpe:
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
        return []                     # check_location has already reported it
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
    unread = _unread_section(
        cluster, "nodes", "capacity",
        f"the cluster's nodes could not be read, so nothing here knows whether "
        f"slots={slots} x {want} can be scheduled. Needs a role that can list "
        f"nodes")
    if unread:
        return unread
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
    unread = _unread_section(
        cluster, "nodes", "node disk",
        f"the cluster's nodes could not be read, so the {ENGINE_DISK_GB}GB per "
        f"engine is unverified")
    if unread:
        return unread
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
    limitranges = cluster.get("limitranges")
    cpu, mem = engine_size(opts)
    unread = _unread_section(
        cluster, "limitranges", "limitrange",
        "the namespace's LimitRanges could not be read, so whether one would "
        "reject the engine pod at admission is unverified")
    if unread:
        return unread
    if not limitranges:
        # Nothing caps the namespace, which is a note rather than a problem --
        # and separately the engines schedule small, which nothing here can
        # change. The generator used to offer a LimitRange for this and no
        # longer does: it could not fix the requests, and the defaults it did
        # apply landed on crane's helper pods.
        return [Check("limitrange", WARN,
                      f"no LimitRange in the namespace, so nothing caps what it "
                      f"may ask for. Separately, engine pods request "
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
            checks.append(Check(f"limitrange {name} defaults", WARN,
                                f"LimitRange '{name}' sets {', '.join(conflicts)} "
                                f"against an engine of {_engine_str(cpu, mem)} "
                                f"-- those reach every pod in the namespace that "
                                f"declares no resources, including crane's "
                                f"per-run job pods"))
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
    quotas, limitranges = cluster.get("quotas"), cluster.get("limitranges")
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    unread = _unread_section(
        cluster, "quotas", "resourcequota",
        f"the namespace's ResourceQuotas could not be read, so whether one has "
        f"room for slots={slots} is unverified")
    if unread:
        return unread
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
    if constrains and limitranges == []:      # read them, there are none
        # With a cpu/memory quota in force the API server rejects any pod that
        # does not declare that resource -- and crane sets no requests on the
        # engines it spawns, so something has to supply them.
        checks.append(Check("quota defaults", WARN,
                            f"ResourceQuota '{constrains}' constrains "
                            f"cpu/memory, so every pod must declare requests and "
                            f"limits; crane sets none on the job pods it spawns. "
                            f"Add a LimitRange of your own to supply them -- sized "
                            f"for those pods, not for an engine"))
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
    namespace_obj = cluster.get("namespace")
    platform = opts.get("platform") or "openshift"
    # Two different facts, and only one of them is answered by creating the
    # namespace. `{}` is a read that came back empty -- the live path's `get ns`
    # on a namespace that is not there yet, which is the ordinary preflight
    # case. `None` is nobody having looked, which today only an evidence file
    # says: the collector records a section it was refused as null, and telling
    # its reader to create a namespace they may well already have is advice
    # about a problem they do not have.
    unread = _unread_section(
        cluster, "namespace", "admission",
        "the namespace could not be read, so its PodSecurity / SCC posture is "
        "unverified -- unreadable is not absent, and creating the namespace is "
        "not what is missing here. The cluster evidence verdict carries the "
        "collector's own reason; re-collect with access to it to settle this")
    if unread:
        return unread
    meta = namespace_obj.get("metadata") or {}
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


# -- service account ----------------------------------------------------------

def check_service_account(facts, opts, cluster):
    """Is the ServiceAccount the bundle references actually there?

    Only asked when the bundle does not create one: with
    `service_account_create` off, the Deployment and both binding subjects name
    an account somebody else owns, and if that name is wrong nothing errors --
    the Deployment applies, the ReplicaSet reports `serviceaccounts "x" not
    found` in an event, and the agent simply never appears. A preflight is the
    only place that is visible before someone waits on it.
    """
    if opts.get("service_account_create", True):
        return []                     # we create it; nothing to find
    name = service_account(opts)
    accounts = cluster.get("serviceaccounts")
    # Every namespace that exists has at least `default`, so an empty list means
    # the namespace is missing or unreadable rather than genuinely accountless
    # -- a different answer from "we looked and it is not there", and not one to
    # fail a preflight on.
    if not accounts:
        return [Check("service account", WARN,
                      f"could not read the ServiceAccounts in the namespace, so "
                      f"'{name}' is unverified -- it must exist before you apply, "
                      f"because nothing in this bundle creates it")]
    if name in {(sa.get("metadata") or {}).get("name") for sa in accounts}:
        return [Check("service account", PASS,
                      f"ServiceAccount '{name}' exists (not created by this "
                      f"bundle, as configured)")]
    return [Check("service account", FAIL,
                  f"ServiceAccount '{name}' does not exist in the namespace and "
                  f"this bundle does not create it. The Deployment applies "
                  f"cleanly and no pod is ever created -- the reason is an event "
                  f"on the ReplicaSet. Create it, correct the name, or "
                  f"re-generate without --no-create-service-account")]


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
    # Older cluster_data, or an API server that does not serve the kind.
    unread = _unread_section(
        cluster, "ingressclasses", "sv ingress class",
        f"IngressClasses could not be read, so the '{CRANE_INGRESS_CLASS}' "
        f"class crane requires is unverified")
    if unread:
        return unread
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
    # Only under CLUSTERIP. Crane's Ingress backend writes a constant 8080 and
    # this controller resolves it against spec.ports[].port -- which is 80 under
    # CLUSTERIP (mismatch, no Route) and 8080 under NODEPORT (a match, so the
    # defect does not arise). Saying it unconditionally would tell a NODEPORT
    # customer their endpoint is broken when generate() just accepted it.
    if (controller == OPENSHIFT_ROUTE_CONTROLLER
            and opts.get("service_type", "CLUSTERIP") == "CLUSTERIP"):
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
        # True of both ways in: no crane pod and no throwaway pod either, or an
        # evidence file, which cannot carry a probe -- it takes a pod in the
        # namespace to find out, and that is the one thing a collector script
        # must not create.
        return [Check("egress", WARN,
                      "egress was not probed from inside the cluster, so whether "
                      "the namespace can reach BlazeMeter is unverified -- the "
                      "agent will not come online without it")]
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

def _items(document):
    """The `.items` of a kubectl List document, or None when the command failed.

    "served the kind, has none" and "could not ask" are different answers and
    the checks report them differently -- [] is a FAIL where we know nothing
    will claim crane's Ingress, None is a WARN because we did not look.
    `.get("items", [])` would collapse both to [], turning a namespace we were
    denied into a hard failure with a non-zero exit. kget reports a failed
    command as {}, which is the falsy case here.
    """
    return document.get("items", []) if document else None


def _split_by_kind(document):
    """One `get limitrange,resourcequota,serviceaccount` -> the three lists, or
    three Nones when the whole get failed. Nothing partial: the kinds come back
    from a single command, so it succeeded for all of them or none."""
    items = _items(document)
    if items is None:
        return None, None, None
    by_kind = {"LimitRange": [], "ResourceQuota": [], "ServiceAccount": []}
    for item in items:
        by_kind.setdefault(item.get("kind"), []).append(item)
    return by_kind["LimitRange"], by_kind["ResourceQuota"], by_kind["ServiceAccount"]


def gather_cluster(cli, namespace):
    """Everything the checks read, in as few API round trips as it takes.
    LimitRanges, ResourceQuotas and ServiceAccounts are all namespaced, so one
    `get` covers them; splitting the result by kind is cheaper than three."""
    limitranges, quotas, accounts = _split_by_kind(livetest.kget(
        cli, namespace, "limitrange,resourcequota,serviceaccount"))
    # IngressClass is cluster-scoped like nodes, but kept its own get: kget
    # reports a failed command as {}, so folding the kinds into one call would
    # lose the nodes too on a cluster whose API server does not serve it.
    return {
        "nodes": _items(livetest.kget(cli, None, "nodes")),
        "ingressclasses": _items(livetest.kget(cli, None, "ingressclass")),
        "limitranges": limitranges,
        "quotas": quotas,
        "serviceaccounts": accounts,
        "namespace": livetest.kget(cli, None, "ns", namespace),
    }


# -- evidence file ------------------------------------------------------------

EVIDENCE_SCHEMA = "bzm-opl-cluster-evidence/1"
EVIDENCE_SCRIPT = "scripts/bzm-cluster-evidence.sh"

# What an import produces: cluster data in gather_cluster()'s shape, the probes
# it cannot supply, and the verdicts about the file itself.
Evidence = collections.namedtuple("Evidence", "cluster probes checks")


def load_evidence(path):
    """Read an evidence file. ValueError says what to do about a bad one --
    this is a file a customer mailed back, so every way it can be wrong is a
    message rather than a traceback."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ValueError(
            f"no cluster evidence file at '{path}'. Have someone with access to "
            f"the cluster run {EVIDENCE_SCRIPT} (read-only) and send back its "
            f"output:\n  ./{EVIDENCE_SCRIPT} -n <namespace> > cluster-evidence.json")
    except json.JSONDecodeError as e:
        raise ValueError(f"'{path}' is not valid JSON ({e}). It should be the "
                         f"unedited output of {EVIDENCE_SCRIPT}")


def cluster_from_evidence(doc, namespace=None):
    """Normalise an evidence file into what gather_cluster() returns.

    The cluster-side twin of `facts.manual()`, and the same rule applies: the
    result is exactly the shape the live path produces, so no check can tell
    which way the data arrived. Everything the file carries beyond that --
    when it was collected, which namespace for, what the script could not read
    -- comes back as Checks instead, because it qualifies the verdicts without
    being one of them.

    The `raw` sections are whole kubectl List documents, or null where the
    command failed; null stays null through here (see _items) so that "we did
    not look" cannot arrive looking like "there are none".
    """
    _validate_evidence(doc)
    raw = doc.get("raw") or {}
    limitranges, quotas, accounts = _split_by_kind(_section(raw, "scoped"))
    cluster = {
        "nodes": _items(_section(raw, "nodes")),
        "ingressclasses": _items(_section(raw, "ingressclasses")),
        "limitranges": limitranges,
        "quotas": quotas,
        "serviceaccounts": accounts,
        # Null stays null here too, and this section is the one where it costs
        # something to lose: `{}` is what the live path produces for a namespace
        # that is not there yet, and check_admission's answer to that is "create
        # it". A collector that was refused `get ns` said nothing of the kind.
        "namespace": _section(raw, "namespace"),
    }
    # Egress needs something inside the namespace to curl from, so an evidence
    # file cannot carry it. {} is check_egress's "not probed" (WARN), which is
    # the honest answer -- never a PASS nobody stood behind.
    return Evidence(cluster, {}, _evidence_checks(doc, namespace))


def _section(raw, key):
    """One `raw` section: the kubectl document as collected, or None. Files come
    back by mail and are sometimes trimmed on the way, so anything else is named
    here rather than reaching a check as an AttributeError."""
    document = raw.get(key)
    if document is not None and not isinstance(document, dict):
        raise ValueError(f"cluster evidence: raw.{key} should be the kubectl "
                         f"document as collected, or null for a section that "
                         f"could not be read; found a {type(document).__name__}")
    return document


def _validate_evidence(doc):
    if not isinstance(doc, dict):
        found = "a JSON array" if isinstance(doc, list) else type(doc).__name__
        raise ValueError(f"cluster evidence must be a JSON object; found {found}. "
                         f"Expected the output of {EVIDENCE_SCRIPT} "
                         f"(schema {EVIDENCE_SCHEMA})")
    schema = doc.get("schema")
    if not schema:
        raise ValueError(f"this file has no 'schema' field, so it is not cluster "
                         f"evidence -- expected {EVIDENCE_SCHEMA}, the output of "
                         f"{EVIDENCE_SCRIPT}. (Account facts go to --facts.)")
    if schema != EVIDENCE_SCHEMA:
        raise ValueError(f"unrecognised cluster evidence: found schema "
                         f"'{schema}', expected '{EVIDENCE_SCHEMA}'. Re-collect "
                         f"with the {EVIDENCE_SCRIPT} shipped with this version "
                         f"rather than trusting a partial read of a shape this "
                         f"doctor does not know")


def _evidence_checks(doc, namespace):
    """One verdict about the file itself: where these answers came from, how
    stale they are, whether they describe the namespace being preflighted, and
    anything the script was refused. Every verdict after it is only as good as
    this one, which is why it is a Check and not a printed aside."""
    collected = doc.get("collected_at") or "an unrecorded time"
    for_ns = doc.get("namespace") or "an unnamed namespace"
    parts = [f"cluster read by {EVIDENCE_SCRIPT} at {collected} for namespace "
             f"{for_ns}, not from a live cluster"]
    if namespace and doc.get("namespace") and namespace != doc["namespace"]:
        # Most of what follows is per-namespace -- LimitRanges, quotas,
        # ServiceAccounts, the PSA labels -- so evidence from another namespace
        # says little about this one. It still describes the same nodes, so this
        # reports rather than refuses.
        parts.append(f"but this preflight is for '{namespace}', so the "
                     f"namespaced verdicts below describe '{doc['namespace']}' "
                     f"instead: re-collect with -n {namespace}")
    if doc.get("notes"):
        parts.append(_unread(doc["notes"]))
    return [Check("cluster evidence", WARN if len(parts) > 1 else PASS,
                  "; ".join(parts))]


def _unread(notes):
    """The script's own errors, which are what explains every null below. It
    writes "<section>: <error>", and on a cluster nobody can reach that error is
    the same six times over -- so the sections are listed and the distinct
    reasons given once."""
    reasons = []
    for note in notes:
        reason = note.partition(": ")[2].strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    why = " | ".join(reasons or notes)
    return (f"could not read {', '.join(unreadable_sections(notes))}, reported "
            f"below as unverified rather than as absent: {why[:300]}")


def unreadable_sections(notes):
    """Which sections the collector recorded as unreadable, in the order it
    wrote them. Names only -- the reasons are _unread's half."""
    sections = []
    for note in notes or []:
        section = str(note).partition(": ")[0]
        if section and section not in sections:
            sections.append(section)
    return sections


def evidence_summary(doc):
    """What the file says about itself, as data rather than as a sentence.

    The same three facts _evidence_checks() states in prose, and it stays the
    one that judges them -- this is for a caller that renders a header instead
    of a verdict list. The web UI is that caller: three facts inside one
    verdict's prose, ten verdicts down a panel, is how a thin file passes for a
    clean bill of health (#53), and a browser re-deriving them by parsing that
    sentence would be a second opinion about the same file.
    """
    doc = doc if isinstance(doc, dict) else {}
    return {"collected_at": doc.get("collected_at") or None,
            "namespace": doc.get("namespace") or None,
            "unreadable": unreadable_sections(doc.get("notes"))}


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
          check_service_account, check_ingress_class, check_egress)


def resolve_namespace(namespace, opts):
    """What the checks are asked about: the explicit namespace, else the one the
    bundle was generated for, else the documented default."""
    return (namespace or (opts or {}).get("namespace")
            or DEFAULT_OPTIONS["namespace"])


def evaluate(facts, opts, namespace, cluster_data=None, probes=None, cli=None,
             extra_checks=(), evidence=None):
    """Every verdict as data, and nothing printed.

    Split out of run() so a caller that is not a terminal -- the web UI -- gets
    the Check list without capturing stdout, and so importing cluster data can
    be tested against the live path by comparing whole lists.

    `extra_checks` are verdicts the caller reached before the cluster data
    existed at all: where it came from, whether it describes this namespace.
    They lead the list because they qualify everything after them.

    `evidence` is the three of them as the one thing they are -- what
    cluster_from_evidence() returns -- for the callers that have a file. The
    three parts stay spelled out for the live path and for tests that supply
    one and not the others, but a caller holding an Evidence should not have to
    take it apart and hope it lands back in the right slots.
    """
    if evidence is not None:
        # Two spellings of one thing, never both: layering extra_checks over an
        # imported file would silently drop one set or the other, and which is
        # not something a reader of the call site could tell.
        if cluster_data is not None or probes is not None or extra_checks:
            raise TypeError("pass evidence= or the three parts it carries "
                            "(cluster_data, probes, extra_checks), not both")
        cluster_data, probes, extra_checks = evidence
    opts = dict(opts or {})
    namespace = resolve_namespace(namespace, opts)
    if cluster_data is None or probes is None:
        cli = cli or livetest.cli_tool()
    if cluster_data is None:
        cluster_data = gather_cluster(cli, namespace)
    if probes is None:
        probes = probe_egress(cli, namespace, opts)

    cluster = {**cluster_data, "probes": probes}
    return list(extra_checks) + [c for check in CHECKS
                                 for c in check(facts, opts, cluster)]


def run(facts, opts, namespace, cluster_data=None, probes=None, cli=None,
        extra_checks=(), evidence=None):
    """Run every check and print the verdict list. Returns the Check list; the
    caller decides the exit code (see has_failures)."""
    checks = evaluate(facts, opts, namespace, cluster_data, probes, cli,
                      extra_checks, evidence)
    _report(checks, facts, resolve_namespace(namespace, opts))
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
