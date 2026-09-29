"""Pre-flight: can this cluster actually run the location's concurrency?

A bundle that applies cleanly says nothing about whether an engine can be
*scheduled*: slots=5 at 2 CPU / 8Gi needs 10 CPU and 40Gi schedulable, plus
quota, a LimitRange that does not fight the sizing, and admission that accepts
the engine pods crane spawns. Missing any of it, a test sits in "initializing"
with no error anywhere.

Every check is a pure function over already-fetched data, so the doctor is
testable offline; gather_cluster() and probe_egress() are the impure layer. The
data may equally come from an evidence file (cluster_from_evidence, the twin of
facts.manual()), in the same shape. evaluate() returns verdicts, run() prints.

FAIL = a test would not start. WARN = the numbers are wrong or it will bite
later, but a test still starts.

A cluster section is None when nobody could read it and []/{} when it was read
and empty; the two get opposite verdicts, and @reads keeps them apart.
"""

import collections
import functools
import json
import os

from . import kube, plan, verdict
# Aliased: every check takes a `facts` argument, and evaluate() an `evidence`.
from . import evidence as evidence_mod
from . import facts as facts_mod
from .ca_trust import CA_MODES
from .footprint import (API_BASE, CRANE_CPU_LIMIT, CRANE_CPU_REQUEST,
                        CRANE_MEM_LIMIT, CRANE_MEM_REQUEST, DEFAULT_THREADS_PER_ENGINE,
                        ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM,
                        ENGINE_DEFAULT_REQUEST_CPU, ENGINE_DEFAULT_REQUEST_MEM,
                        ENGINE_DISK_GB, ENGINE_TMP_GB, ENGINE_UPLOAD_HOSTS,
                        TYPICAL_SYSTEM_PODS, engine_requests)
from .bundle_options import (DEFAULT_OPTIONS, crane_scheduling,
                             engines_per_node, engine_scheduling, engine_size,
                             resolve_engine_limits, separate_pools,
                             service_account)
from .bundle_names import NODEPOOLS_FILE
from .service_virt import SV_INGRESS_BACKENDS, SV_INGRESS_NONE
from .bundle_env import proxy_env
from .quantity import (format_cpu, format_memory, human_memory, parse_cpu,
                       parse_memory)
from .verdict import FAIL, PASS, WARN, Check

GB = 10 ** 9                     # the docs quote decimal GB, not GiB

API_PROBE_URL = f"{API_BASE}/web/version"
# Engines upload to hosts crane never contacts, so an egress rule shaped around
# crane alone passes here and still fails a run.
ENGINE_PROBE_URLS = tuple(f"https://{h}/" for h in ENGINE_UPLOAD_HOSTS)
CURL_IMAGE = "curlimages/curl:8.11.1"

# What a FAIL costs, in the words the report ends on. The web UI shows the same
# sentence beside an imported file's name.
NO_TEST_WOULD_START = "a test would not start on this location as configured"


def has_failures(checks):
    return verdict.has_failures(checks)


def summary_line(checks):
    """The verdict list in one sentence; the consequence only where something
    FAILed, so a thin all-warnings read is not a rejection."""
    return verdict.summary_line(checks, NO_TEST_WOULD_START)


class MissingSection(LookupError):
    """A check was handed cluster data with no key at all for a section it reads.

    Both producers always carry every key (None for unread), so a missing key is
    a caller -- usually a fixture -- that has not said which it means. Raised
    rather than read as unread, which would be indistinguishable from an honest
    WARN."""


# What a check declares about a cluster section it reads. `name`/`unread` are
# the verdict for a null section; both None where that verdict is another
# check's or the section cannot express it (the key is still presence-checked).
# `when` is a predicate over the options gating the whole declaration, for a
# section read only when the question arises.
Section = collections.namedtuple("Section", "key name unread when")


def reads(key, name=None, unread=None, when=None):
    """Declare a cluster section a check reads, and what an unread one costs.

    The wrapper answers a null section from this declaration, so the body only
    ever sees a section that was read. It travels with the check, so a direct
    call (as in the tests) keeps the same contract as evaluate(). `unread` may
    be a callable over (facts, opts) -- never the cluster, which is what was not
    read. Stack it for a check that reads two sections.
    """
    def declare(check):
        section = Section(key, name, unread, when)
        if getattr(check, "sections", None) is not None:
            # Already wrapped below; source order is answer order.
            check.sections = (section,) + check.sections
            return check

        @functools.wraps(check)
        def declared(facts, opts, cluster):
            for s in declared.sections:
                verdict_ = _declared_verdict(s, declared, facts, opts, cluster)
                if verdict_ is not None:
                    return verdict_
            return check(facts, opts, cluster)

        declared.sections = (section,)
        return declared
    return declare


def _declared_verdict(section, check, facts, opts, cluster):
    """The verdict a declaration gives by itself, or None to run the body."""
    if section.when is not None and not section.when(opts):
        return None
    if section.key not in cluster:
        raise MissingSection(
            f"{check.__name__} reads the cluster section '{section.key}', and "
            f"this cluster data has no key for it. Absent is not a third "
            f"answer: pass '{section.key}': None for a section nobody could "
            f"read, or its contents for one that was read. Both "
            f"gather_cluster() and cluster_from_evidence() always carry every "
            f"section, so this is a caller -- in practice a fixture -- that "
            f"has not said which it means")
    if section.unread is None or cluster.get(section.key) is not None:
        return None
    detail = (section.unread(facts, opts) if callable(section.unread)
              else section.unread)
    return [Check(section.name, WARN, detail)]


def defers_to(*owners):
    """Declare the checks whose verdict this one leaves unrepeated (returns []
    for). _ordered() enforces at import that each owner runs first."""
    def declare(check):
        check.defers = tuple(owners)
        return check
    return declare


def _ordered(checks):
    """CHECKS, refusing an order in which a check runs before one it defers to:
    the verdict it stays quiet for would never be reported."""
    seen = []
    for check in checks:
        for owner in getattr(check, "defers", ()):
            if owner not in seen:
                raise RuntimeError(
                    f"{check.__name__} defers to {owner.__name__}, which does "
                    f"not run before it -- so the verdict it stays quiet for is "
                    f"never reported. Fix the order in CHECKS")
        seen.append(check)
    return tuple(checks)


def run_check(check, facts, opts, cluster):
    """One check's verdicts. The @reads contract lives on the check itself."""
    return check(facts, opts, cluster)


# -- location -----------------------------------------------------------------
#
# These judge the location's settings against the bundle's and read no cluster
# section, so they declare none.

def check_location(facts, opts, cluster):
    """The two fields BlazeMeter needs before it will hand a run to this location.

    Hand-entered facts have both None, like a real location with them unset;
    only the second is a misconfiguration, so facts.from_manual_entry() decides.
    A typed 0 is still a FAIL.
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
        # "Engines per agent": one cluster is one agent, so slots is what a
        # cluster is sized against.
        checks.append(Check("location slots", PASS,
                            f"{slots} engine(s) per agent"))
    tpe = facts.get("threads_per_engine")
    if tpe is None and typed_by_hand:
        checks.append(Check("location threadsPerEngine", WARN,
                            "unknown -- entered by hand, so there was no account "
                            "to read it from. Unset, every test start fails with "
                            "403 'Not enough available resources', so check it in "
                            "Settings -> Private Locations"))
    elif not tpe:
        # A location created via the API has this null (POST ignores it).
        checks.append(Check("location threadsPerEngine", FAIL,
                            "threadsPerEngine is unset -- every test start fails "
                            "with 403 'Not enough available resources'. Set it in "
                            "Settings -> Private Locations"))
    else:
        checks.append(Check("location threadsPerEngine", PASS, f"{tpe} threads"))
    return checks


@defers_to(check_location)
def check_threads_per_engine(facts, opts, cluster):
    """Threads per engine against what the engine is sized for, by
    plan.supported_vus -- the ratio the planner sizes from, so the two agree."""
    tpe = facts.get("threads_per_engine")
    if not tpe:
        return []
    cpu, mem = engine_size(opts)
    model = _sizing_model(facts, cpu, mem)
    if model and not model["engine"]:
        # Said rather than skipped: silence reads like a pass.
        return [Check("threadsPerEngine vs engine size", PASS,
                      f"not judged -- this location runs {model['runs']} and "
                      f"carries no taurus engine, so the per-engine ratio is not "
                      f"about it. threadsPerEngine is {tpe}, which is what the "
                      f"account stores")]
    supported = plan.supported_vus(cpu, mem)
    size = _engine_str(cpu, mem)
    if tpe > supported:
        return [Check("threadsPerEngine vs engine size", WARN,
                      f"{tpe} threads on a {size} engine; that size supports about "
                      f"{supported} ({DEFAULT_THREADS_PER_ENGINE} threads per "
                      f"{ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM}). Lower "
                      f"threadsPerEngine or raise the engine limits"
                      + _model_caveat(facts, model))]
    return [Check("threadsPerEngine vs engine size", PASS,
                  f"{tpe} threads on a {size} engine (supports ~{supported})"
                  + _model_caveat(facts, model))]


def _sizing_model(facts, cpu, mem):
    """The sizing model the location's funcIds put it in, with what one pod of
    the configured size holds; None where they name no model here."""
    models = plan.sizing_models_for(facts.get("func_ids")) or []
    if not models:
        return None
    fid = models[0]
    return {**plan.SIZING_MODELS[fid],
            "id": fid,
            "engine": facts_mod.runs_engine(fid),
            "per_pod": plan.per_pod_capacity(fid, cpu, mem)}


def _model_caveat(facts, model):
    """Whose ratio was applied, where it is not this location's own model.

    No funcIds at all (not read) and funcIds naming no model (tdm, delphix...)
    are different facts and get different sentences."""
    if model is None:
        if facts.get("func_ids") is None:
            return (". These facts carry no funcIds, so that is the performance "
                    "ratio applied to a location nothing here has read the "
                    "functionalities of")
        return (". Its funcIds name nothing this tool sizes, so that is the "
                "performance ratio applied to a location that may not be one")
    if model["id"] == plan.PERFORMANCE:
        return ""
    carries = (f", about {model['per_pod']} to an engine this size"
               if model["per_pod"] else "")
    return (f". That is the performance ratio: this location runs "
            f"{model['runs']}, which are sized in {model['unit']}{carries}, and "
            f"threadsPerEngine is BlazeMeter's own field rather than that figure")


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


def eligible_nodes(nodes, opts, placement=None):
    """Nodes a pod could land on: Ready, uncordoned, matching the nodeSelector,
    every NoSchedule/NoExecute taint tolerated.

    `placement` is (selector, tolerations); it defaults to the engines'
    (engine_scheduling), and a caller asking about crane passes
    crane_scheduling(opts) -- on split pools they are different node sets."""
    selector, tolerations = placement or engine_scheduling(opts)
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
    """(cpu_millicores, mem_bytes) the node advertises as schedulable -- an
    upper bound, not what is free."""
    alloc = node.get("status", {}).get("allocatable", {})
    return parse_cpu(alloc.get("cpu", "0")), parse_memory(alloc.get("memory", "0"))


def _engine_str(cpu, mem):
    return f"{format_cpu(cpu)} CPU / {format_memory(mem)}"


def _scope(opts, placement=None):
    """How the eligible-node set was narrowed, for a detail string."""
    selector, tolerations = placement or engine_scheduling(opts)
    bits = []
    if selector:
        bits.append(f"nodeSelector {json.dumps(selector)}")
    if tolerations:
        bits.append(f"{len(tolerations)} toleration(s)")
    return ", ".join(bits) or "no nodeSelector/tolerations"


# -- capacity -----------------------------------------------------------------

@reads("nodes", "crane pool",
       "the cluster's nodes could not be read, so whether the crane pool can "
       "hold crane is unverified",
       when=separate_pools)
def check_crane_pool(facts, opts, cluster):
    """The crane pool can hold crane (split pools only; otherwise
    check_capacity spends crane's share out of the one pool).

    A too-small crane pool shows as an agent going offline mid-run. The usual
    case: e2-medium reports 940m allocatable, below crane's 1 CPU limit.
    """
    if not separate_pools(opts):
        return []
    placement = crane_scheduling(opts)
    nodes = eligible_nodes(cluster["nodes"], opts, placement)
    want_cpu, want_mem = parse_cpu(CRANE_CPU_LIMIT), parse_memory(CRANE_MEM_LIMIT)
    want = _engine_str(want_cpu, want_mem)
    if not nodes:
        return [Check("crane pool", FAIL,
                      f"no Ready, schedulable node matches {_scope(opts, placement)} "
                      f"-- the crane pod has nowhere to run, and an agent that "
                      f"never starts is a location that never comes online")]
    fits = [n for n in nodes if _allocatable(n) >= (want_cpu, want_mem)]
    if fits:
        return [Check("crane pool", PASS,
                      f"{len(fits)}/{len(nodes)} crane-pool node(s) hold crane's "
                      f"{want} (allocatable, not free)")]
    best = max(nodes, key=lambda n: _allocatable(n))
    cpu, mem = _allocatable(best)
    return [Check("crane pool", WARN,
                  f"no crane-pool node has crane's full {want} allocatable; the "
                  f"largest is {best['metadata']['name']} with "
                  f"{format_cpu(cpu)} CPU / {human_memory(mem)}. Crane schedules "
                  f"anyway -- it requests only {CRANE_CPU_REQUEST}/"
                  f"{CRANE_MEM_REQUEST} -- but is throttled at its limit exactly "
                  f"when a run makes it busy, and an agent that stops "
                  f"heartbeating mid-run reads as a test that stopped")]


def _capacity_unread(facts, opts):
    slots = facts.get("slots") or 1
    want = _engine_str(*engine_size(opts))
    return (f"the cluster's nodes could not be read, so nothing here knows "
            f"whether slots={slots} x {want} can be scheduled. Needs a role "
            f"that can list nodes")


@reads("nodes", "capacity", _capacity_unread)
def check_capacity(facts, opts, cluster):
    """slots x engine size against the eligible nodes: one node must fit an
    engine (a pod cannot split across nodes), and all of them must fit slots."""
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    want = _engine_str(cpu, mem)
    nodes = eligible_nodes(cluster["nodes"], opts)
    if not nodes:
        if separate_pools(opts):
            # A dedicated engine pool at min-nodes 0 has no nodes between runs,
            # and `get nodes` cannot tell it from a pool never created (the
            # autoscaler status names groups without their labels). So a WARN.
            return [Check("capacity: eligible nodes", WARN,
                          f"no node currently matches {_scope(opts)}. With a "
                          f"dedicated engine pool that is expected between runs "
                          f"-- a pool at min-nodes 0 has none until a test asks "
                          f"for one -- but it looks the same as a pool that was "
                          f"never created, and nothing in `get nodes` tells them "
                          f"apart. Confirm the pool exists and can scale: "
                          f"`kubectl -n kube-system get cm "
                          f"cluster-autoscaler-status -o yaml` lists the node "
                          f"groups with their minSize/maxSize")]
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

    # Crane's pod is spent out of the engine nodes only when it can land there.
    crane_here = not separate_pools(opts) or _crane_on(nodes, opts)
    crane_cpu, crane_mem = parse_cpu(CRANE_CPU_LIMIT), parse_memory(CRANE_MEM_LIMIT)
    spent_cpu, spent_mem = (crane_cpu, crane_mem) if crane_here else (0, 0)
    tot_cpu = max(sum(c for c, _ in sizes.values()) - spent_cpu, 0)
    tot_mem = max(sum(m for _, m in sizes.values()) - spent_mem, 0)
    holds = min(tot_cpu // cpu, tot_mem // mem)
    after = (f" after crane's own {_engine_str(crane_cpu, crane_mem)}"
             if crane_here else " (crane is on its own pool and spends none of it)")
    total = (f"{len(nodes)} eligible node(s) leave {format_cpu(tot_cpu)} CPU / "
             f"{human_memory(tot_mem)}{after} -- an upper bound, other "
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


MB = 1024 ** 2

# The engine sizing model, from the one configuration BlazeMeter documents:
# 500 threads on 2 CPU / 8Gi with a 4096MB heap. 4096/500 = 8.192MB of heap a
# thread; 8Gi/4096MB = 2.0 container per heap. Both carry the vendor's safety
# margin, so everything derived here is a defensible upper bound, not a
# measured requirement. Record any revision's measurements here.
HEAP_MB_PER_THREAD = 8.192
CONTAINER_HEAP_RATIO = 2.0

# The floor, and in practice nearly the whole answer. Bisecting a real engine's
# limit (Docker agent, light script), verdict by Taurus exit code:
#
#   threads  limit   result
#     300    1024MB  never starts -- JVM cannot initialise
#     300    2560MB  starts, dies halfway                (31,130 samples)
#     300    3072MB  runs the whole test                 (61,348 samples)
#     300    4096MB  runs the whole test                 (61,139 samples)
#      50    2560MB  starts, dies partway                 (6,019 samples)
#
# 50 and 300 threads share the floor (2560 < floor <= 3072), so the need is
# mostly fixed JVM/Taurus/JMeter cost, and above the knee more memory buys
# nothing. A reading of what an engine *used* is never a floor for what it needs.
MIN_HEAP_MB = 256
MIN_CONTAINER_MB = 3072


def engine_heap_mb(threads):
    """The heap a JVM needs to carry `threads` of load, in MB."""
    return max(int(threads * HEAP_MB_PER_THREAD), MIN_HEAP_MB)


def engine_container_mb(heap_mb):
    """The container the heap has to live in, in MB: heap plus what the JVM
    needs outside it (metaspace, stacks, code cache, direct buffers, GC)."""
    return max(int(heap_mb * CONTAINER_HEAP_RATIO), MIN_CONTAINER_MB)


def check_engine_heap(facts, opts, cluster):
    """The location's JVM heap against the threads it carries, and against the
    container the bundle gives it.

    Heap vs threads: short OOMKills mid-run; far over reserves node capacity
    the JVM cannot address. Heap vs container: at or above the limit is an
    OOMKill reported as a stopped test. Heap and threads are location settings,
    the limit a bundle option. An unknown heap is a WARN, never a pass.
    """
    xmx = facts.get("engine_xmx_mb")
    threads = facts.get("threads_per_engine")
    cpu, mem = engine_size(opts)
    limit = format_memory(mem)
    model = _sizing_model(facts, cpu, mem)
    if model and not model["engine"]:
        # No taurus engine on this agent, so no JVM heap to judge.
        return [Check("engine heap", PASS,
                      f"not judged -- this location runs {model['runs']} and "
                      f"carries no taurus engine, so there is no JVM here for a "
                      f"heap to be wrong for")]
    if xmx is None:
        if facts_mod.from_manual_entry(facts):
            return [Check("engine heap", WARN,
                          f"the location's engineXmx is unknown (facts entered by "
                          f"hand), so nothing here can tell whether a JVM fits the "
                          f"{limit} limit. Read it from the location's Advanced "
                          f"settings in BlazeMeter")]
        return [Check("engine heap", WARN,
                      f"the location has no engineXmx set, so the engine JVM's "
                      f"heap against the {limit} limit is unverified")]

    heap = xmx * MB
    if heap >= mem:
        return [Check("engine heap", FAIL,
                      f"engineXmx={xmx}MB against a {limit} container limit: the "
                      f"heap is at or above the whole limit, so the JVM is "
                      f"OOMKilled once it fills -- mid-run, reported as a test "
                      f"that stopped rather than a resource error. Raise "
                      f"engine_mem_limit to at least "
                      f"{engine_container_mb(xmx)}MB, or lower the heap")]

    if not threads:
        # check_location has FAILed on it; the load comparison cannot be made.
        return [Check("engine heap", WARN,
                      f"engineXmx={xmx}MB fits the {limit} limit, but "
                      f"threadsPerEngine is unset, so whether the heap matches "
                      f"the load it must carry is unverified")]

    want_heap = engine_heap_mb(threads)
    want_container = engine_container_mb(xmx)
    pair = f"engineXmx={xmx}MB for {threads} threads"
    # A 1.5x band either way: one vendor data point does not support finer.
    if xmx < want_heap / 1.5:
        # WARN, not FAIL: the per-thread model measured flat between 50 and 300
        # threads, so this rests on a shape the data does not confirm.
        return [Check("engine heap", WARN,
                      f"{pair}: that load needs about {want_heap}MB of heap "
                      f"({HEAP_MB_PER_THREAD}MB a thread, from BlazeMeter's "
                      f"documented 500 threads on a 4096MB heap), so the JVM "
                      f"fills and is OOMKilled partway up the ramp -- reported "
                      f"as a test that stopped. Raise engineXmx to {want_heap}MB "
                      f"and engine_mem_limit to {engine_container_mb(want_heap)}MB. "
                      f"Treat the figure as indicative: it comes from a "
                      f"per-thread model that measured *flat* between 50 and 300 "
                      f"threads, so what this load actually needs above that "
                      f"range is unverified")]
    if xmx > want_heap * 1.5:
        return [Check("engine heap", WARN,
                      f"{pair}: that load needs only about {want_heap}MB of heap, "
                      f"so every engine pod reserves memory the JVM cannot "
                      f"address -- on a dedicated engine pool that is most of "
                      f"what it costs. Either lower engineXmx toward {want_heap}MB "
                      f"(and engine_mem_limit to {engine_container_mb(want_heap)}MB), "
                      f"or raise threadsPerEngine if the engines can carry more")]
    if mem < want_container * MB:
        return [Check("engine heap", WARN,
                      f"{pair}: the heap suits the load, but a {limit} container "
                      f"leaves under what the JVM needs outside it (thread "
                      f"stacks, metaspace, direct buffers). Raise "
                      f"engine_mem_limit to about {want_container}MB")]
    return [Check("engine heap", PASS,
                  f"{pair}: heap suits the load (~{want_heap}MB) and fits the "
                  f"{limit} container")]


def _crane_on(engine_nodes, opts):
    """Could crane also land on these (engine) nodes? Two separately configured
    pools may still both accept crane."""
    return bool(eligible_nodes(engine_nodes, opts, crane_scheduling(opts)))


def _pod_ceiling(node):
    """`allocatable.pods`, or None where a trimmed status omits it."""
    raw = node.get("status", {}).get("allocatable", {}).get("pods")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@defers_to(check_capacity)
@reads("nodes", "engine packing",
       "the cluster's nodes could not be read, so nothing here knows how many "
       "engines would share one")
def check_engine_packing(facts, opts, cluster):
    """How many engines the scheduler would put on one node, versus how many
    can actually run there.

    Scheduler and autoscaler place by requests, which come from the location's
    overrideCPU/overrideMemory (250m/256Mi when unset), not from these
    manifests. WARN, never FAIL: engines start, but throttle against each other
    and report the load generator's latency."""
    nodes = eligible_nodes(cluster["nodes"], opts)
    if not nodes:
        return []
    cpu, mem = engine_size(opts)
    req_cpu_s, req_mem_s = engine_requests(facts)
    req_cpu, req_mem = parse_cpu(req_cpu_s), parse_memory(req_mem_s)
    overridden = bool(facts.get("override_cpu") or facts.get("override_memory"))

    worst = None
    for n in nodes:
        alloc_cpu, alloc_mem = _allocatable(n)
        by_req = min(alloc_cpu // req_cpu, alloc_mem // req_mem)
        # Capped by maxPods less the pods every node runs -- an assumption,
        # named in the verdict; TYPICAL_SYSTEM_PODS is what the node-pool
        # recipe sizes maxPods from.
        ceiling = _pod_ceiling(n)
        if ceiling is not None:
            by_req = min(by_req, max(ceiling - TYPICAL_SYSTEM_PODS, 1))
        # Never counted above the pool's design, or a pool built to spec warns.
        runs = min(alloc_cpu // cpu, alloc_mem // mem, engines_per_node(opts))
        if by_req > max(runs, 1) and (worst is None or by_req > worst[1]):
            worst = (n["metadata"]["name"], by_req, runs, ceiling)

    want = _engine_str(cpu, mem)
    if not worst:
        return [Check("engine packing", PASS,
                      f"no eligible node would take more {want} engine(s) than "
                      f"it can run. Engines request {req_cpu_s}/{req_mem_s}"
                      + (" (the location's overrideCPU/overrideMemory)"
                         if overridden else
                         " (crane's default -- the location sets no "
                         "overrideCPU/overrideMemory)")
                      + f"; assuming ~{TYPICAL_SYSTEM_PODS} system pods a node "
                      f"against its allocatable.pods, which you can count with "
                      f"`kubectl get pods -A --field-selector "
                      f"spec.nodeName=<node>`")]

    name, packs, runs, ceiling = worst
    lever = (f"allocatable.pods={ceiling}, less ~{TYPICAL_SYSTEM_PODS} for "
             f"system pods" if ceiling is not None
             else "the node reports no pod ceiling")
    return [Check("engine packing", WARN,
                  f"node {name} would accept {packs} engine(s) but can only run "
                  f"{runs} at {want}: engines request {req_cpu_s}/{req_mem_s} and "
                  f"both the scheduler and the cluster autoscaler place on "
                  f"requests, not limits ({lever}). Engines sharing a node "
                  f"throttle against each other, so the run reports the load "
                  f"generator's latency rather than the system's. "
                  + ("Raise the location's overrideCPU/overrideMemory to match "
                     f"the engine limits ({format_cpu(cpu)} / "
                     f"{mem // (1024 ** 2)}MB) -- they set the pod's requests, "
                     "and matching them is what makes the scheduler place "
                     "engines truthfully."
                     if not overridden else
                     "The location's overrideCPU/overrideMemory are set but "
                     f"still below the limits; matching them ({format_cpu(cpu)} "
                     f"/ {mem // (1024 ** 2)}MB) closes this.")
                  + f" Failing that, cap the engine pool's maxPods at the pods "
                  f"a node of it actually runs plus one -- counted with "
                  f"`kubectl get pods -A --field-selector spec.nodeName=<node>`, "
                  f"not `get ds`, which counts nodeAffinity-gated variants that "
                  f"never land. No manifest can set maxPods, so it belongs on "
                  f"the node pool (see the generated {NODEPOOLS_FILE})")]


@reads("nodes", "node disk",
       f"the cluster's nodes could not be read, so the {ENGINE_DISK_GB}GB per "
       f"engine is unverified")
def check_disk(facts, opts, cluster):
    """Ephemeral storage per eligible node against the documented engine
    footprint. WARN: an engine that fills it is evicted mid-test."""
    slots = facts.get("slots") or 1
    nodes = eligible_nodes(cluster["nodes"], opts)
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


@reads("limitranges", "limitrange",
       "the namespace's LimitRanges could not be read, so whether one would "
       "reject the engine pod at admission is unverified")
def check_limitrange(facts, opts, cluster):
    """An existing LimitRange can reject the engine pod at admission -- max
    below its limits, min above the requests crane stamps, or a
    maxLimitRequestRatio tighter than their gap -- and its defaults reach every
    pod that declares no resources. None of it shows in the manifests."""
    limitranges = cluster["limitranges"]
    cpu, mem = engine_size(opts)
    if not limitranges:
        return [Check("limitrange", WARN,
                      f"no LimitRange in the namespace, so nothing caps what it "
                      f"may ask for. Separately, engine pods request "
                      f"{ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM} "
                      f"rather than {_engine_str(cpu, mem)} because crane sets "
                      f"that explicitly -- a LimitRange cannot override it")]

    # (field, parse, the engine's limit, how to show it, crane's stamped request)
    dims = (("cpu", parse_cpu, cpu, format_cpu, parse_cpu(ENGINE_DEFAULT_REQUEST_CPU)),
            ("memory", parse_memory, mem, format_memory,
             parse_memory(ENGINE_DEFAULT_REQUEST_MEM)))
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
                mn = (item.get("min") or {}).get(key)
                if mn and parse(mn) > stamped:
                    blocking.append(f"min {key} {mn} > the {show(stamped)} "
                                    f"crane requests")
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


# Quota keys comparable with an engine's claim; 'cpu'/'memory' are the API's
# aliases for requests.*.
_QUOTA_CPU = ("requests.cpu", "limits.cpu", "cpu")
_QUOTA_MEM = ("requests.memory", "limits.memory", "memory")


def _quota_unread(facts, opts):
    slots = facts.get("slots") or 1
    return (f"the namespace's ResourceQuotas could not be read, so whether one "
            f"has room for slots={slots} is unverified")


@reads("quotas", "resourcequota", _quota_unread)
# Read for its [] vs None: check_limitrange owns the unread verdict.
@reads("limitranges")
def check_resourcequota(facts, opts, cluster):
    """hard - used, per resource, against slots x engine (+1 pod for crane)."""
    quotas, limitranges = cluster["quotas"], cluster["limitranges"]
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
    if constrains and limitranges == []:      # read them, there are none
        # A cpu/memory quota rejects pods that declare no requests, and crane
        # sets none on the job pods it spawns.
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


@reads("namespace", "admission",
       "the namespace could not be read, so its PodSecurity / SCC posture is "
       "unverified -- unreadable is not absent, and creating the namespace is "
       "not what is missing here. The cluster evidence verdict carries the "
       "collector's own reason; re-collect with access to it to settle this")
def check_admission(facts, opts, cluster):
    """Will the namespace's admission posture accept the *engine* pods?

    Crane's own pod satisfies restricted PSA. The engines' security context
    comes from KUBERNETES_SECURITY_CONTEXT_CAP_JSON / INHERIT_RUNNING_USER_AND_GROUP
    (the restrict_engines option), and a refusal lands after the agent reads
    online. `{}` here is a namespace that does not exist yet.
    """
    namespace_obj = cluster["namespace"]
    platform = opts.get("platform") or "openshift"
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
        if opts.get("restrict_engines", True):
            return [Check("admission (PodSecurity)", PASS,
                          f"{PSA_ENFORCE}=restricted; engines drop all "
                          f"capabilities and inherit crane's UID:GID, so the "
                          f"pods crane spawns satisfy it too")]
        return [Check("admission (PodSecurity)", FAIL,
                      f"{PSA_ENFORCE}=restricted with restrict_engines off: "
                      f"crane passes, but the engine pods it spawns keep "
                      f"crane's own privileged default and are rejected after "
                      f"the agent is already online, so runs hang rather than "
                      f"fail. Drop --no-restrict-engines, or use "
                      f"enforce=baseline for this namespace")]
    if enforce:
        return [Check("admission (PodSecurity)", PASS,
                      f"{PSA_ENFORCE}={enforce} admits the engine pods")]
    return [Check("admission (PodSecurity)", WARN,
                  f"namespace has no {PSA_ENFORCE} label -- no enforcement is "
                  f"configured, so nothing here is checked at admission time "
                  f"(a cluster-wide default may still apply)")]


# -- service account ----------------------------------------------------------

def _brings_its_own_account(opts):
    return not opts.get("service_account_create", True)


def _account_unverified(facts, opts):
    return (f"could not read the ServiceAccounts in the namespace, so "
            f"'{service_account(opts)}' is unverified -- it must exist before "
            f"you apply, because nothing in this bundle creates it")


@reads("serviceaccounts", "service account", _account_unverified,
       when=_brings_its_own_account)
def check_service_account(facts, opts, cluster):
    """Does the ServiceAccount the bundle references exist? Asked only when the
    bundle does not create one: a wrong name fails silently, as a ReplicaSet
    event and an agent that never appears."""
    if opts.get("service_account_create", True):
        return []
    name = service_account(opts)
    accounts = cluster["serviceaccounts"]
    # Every existing namespace has `default`, so an empty read means missing or
    # filtered -- the same "unverified" as an unread one.
    if not accounts:
        return [Check("service account", WARN, _account_unverified(facts, opts))]
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

# Crane hardcodes `ingressClassName: nginx` on each virtual service's Ingress;
# BlazeMeter exposes no env for it. Equal to the `nginx` sv_ingress value only
# by coincidence, so kept separate.
CRANE_INGRESS_CLASS = "nginx"
OPENSHIFT_ROUTE_CONTROLLER = "openshift.io/ingress-to-route"


def _claims_an_ingress_class(opts):
    """Does this bundle publish an Ingress for a class to claim? Gates the
    IngressClass read: the other branches answer from the options alone."""
    backend = SV_INGRESS_BACKENDS.get(opts.get("sv_ingress"))
    return bool(backend and backend.via_ingress_class)


@reads("ingressclasses", "sv ingress class",
       f"IngressClasses could not be read, so the '{CRANE_INGRESS_CLASS}' class "
       f"crane requires is unverified",
       when=_claims_an_ingress_class)
def check_ingress_class(facts, opts, cluster):
    """Will anything claim the Ingress crane creates for a virtual service?

    With no IngressClass named `nginx` the published endpoint returns 503 while
    the virtual service is healthy in-cluster, and nothing in the deploy fails.
    OpenShift ships only `openshift-default`.
    """
    ingress = opts.get("sv_ingress")
    if not ingress or ingress == SV_INGRESS_NONE:
        return []
    backend = SV_INGRESS_BACKENDS.get(ingress)
    if backend is None:
        # Only a hand-written profile gets here; generate() rejects it.
        known = "', '".join(SV_INGRESS_BACKENDS)
        return [Check("sv ingress class", WARN,
                      f"unrecognised sv_ingress={ingress}; expected one of "
                      f"'{known}', so the ingress path is unverified")]
    if not backend.via_ingress_class:
        # Istio and Contour register no IngressClass (verified on 1.30 / v1.33).
        return [Check("sv ingress class", PASS,
                      f"sv_ingress={ingress} routes through the "
                      f"{backend.creates} crane creates, not an IngressClass")]

    by_name = {c.get("metadata", {}).get("name"): c
               for c in cluster["ingressclasses"]}
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
    # Crane's Ingress backend writes port 8080; this controller resolves it
    # against the Service's port, which is 80 under CLUSTERIP (no Route) and
    # 8080 under NODEPORT (fine).
    if (controller == OPENSHIFT_ROUTE_CONTROLLER
            and opts.get("service_type", "CLUSTERIP") == "CLUSTERIP"):
        detail += ("; note that this controller resolves the backend port "
                   "against the Service's port 80 and crane writes 8080, so it "
                   "reports IncompleteIngressToRouteRules and creates no Route "
                   "(upstream defect -- see README)")
    return [Check("sv ingress class", PASS, detail)]


# -- egress -------------------------------------------------------------------

def egress_targets(opts):
    """What has to be reachable from inside the namespace before a run works."""
    targets = [API_PROBE_URL, *ENGINE_PROBE_URLS]
    reg = opts.get("private_registry")
    if reg:
        targets.append(f"https://{reg.split('/')[0]}/v2/")
    return targets


# No unread verdict: {} (an evidence file cannot probe) and None (not probed)
# are the same answer, since egress_targets() is never empty.
@reads("probes")
def check_egress(facts, opts, cluster):
    """Pure verdict over {target: curl returncode, or None if unknown}."""
    probes = cluster["probes"]
    if not probes:
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
    """A kubectl List's `.items`, or None when the command failed ({} from kget).
    Never `.get("items", [])`, which would turn a denied read into a FAIL."""
    return document.get("items", []) if document else None


def _split_by_kind(document):
    """One `get limitrange,resourcequota,serviceaccount` -> the three lists, or
    three Nones: one command succeeds for all of them or none."""
    items = _items(document)
    if items is None:
        return None, None, None
    by_kind = {"LimitRange": [], "ResourceQuota": [], "ServiceAccount": []}
    for item in items:
        by_kind.setdefault(item.get("kind"), []).append(item)
    return by_kind["LimitRange"], by_kind["ResourceQuota"], by_kind["ServiceAccount"]


def gather_cluster(cli, namespace):
    """Everything the checks read, in as few API round trips as it takes."""
    limitranges, quotas, accounts = _split_by_kind(kube.kget(
        cli, namespace, "limitrange,resourcequota,serviceaccount"))
    # IngressClass has its own get: folded in with nodes, an API server that
    # does not serve it would fail the nodes read too.
    return {
        "nodes": _items(kube.kget(cli, None, "nodes")),
        "ingressclasses": _items(kube.kget(cli, None, "ingressclass")),
        "limitranges": limitranges,
        "quotas": quotas,
        "serviceaccounts": accounts,
        # kget_named: {} for "not created yet", None for "not allowed to look".
        "namespace": kube.kget_named(cli, None, "ns", namespace),
    }


# -- evidence file ------------------------------------------------------------

# An import: cluster data in gather_cluster()'s shape, the probes (none), and
# the verdicts about the file itself.
Evidence = collections.namedtuple("Evidence", "cluster probes checks")


def load_evidence(path):
    """Read an evidence file; ValueError, with what to do, for a bad one."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ValueError(
            f"no cluster evidence file at '{path}'. Have someone with access to "
            f"the cluster run {evidence_mod.SCRIPT} (read-only) and send back its "
            f"output:\n  ./{evidence_mod.SCRIPT} -n <namespace> > cluster-evidence.json")
    except json.JSONDecodeError as e:
        raise ValueError(f"'{path}' is not valid JSON ({e}). It should be the "
                         f"unedited output of {evidence_mod.SCRIPT}")
    except OSError as e:
        # A directory, an unreadable file, a dead symlink.
        raise ValueError(f"'{path}' could not be read ({e}). It should be the "
                         f"file {evidence_mod.SCRIPT} wrote on the customer's "
                         f"machine")


def cluster_from_evidence(doc, namespace=None):
    """Normalise an evidence file into what gather_cluster() returns, so no
    check can tell which way the data arrived. What the file says about itself
    comes back as Checks. Null sections stay null."""
    _validate_evidence(doc)
    raw = doc.get(evidence_mod.RAW) or {}
    limitranges, quotas, accounts = _split_by_kind(
        _section(raw, evidence_mod.SCOPED))
    cluster = {
        "nodes": _items(_section(raw, evidence_mod.NODES)),
        "ingressclasses": _items(_section(raw, evidence_mod.INGRESSCLASSES)),
        "limitranges": limitranges,
        "quotas": quotas,
        "serviceaccounts": accounts,
        # Null, not {}: {} would tell check_admission the namespace is missing.
        "namespace": _section(raw, evidence_mod.NAMESPACE),
    }
    # Probing needs a pod in the namespace, which a collector must not create;
    # {} is check_egress's "not probed".
    return Evidence(cluster, {}, _evidence_checks(doc, namespace))


def _section(raw, key):
    """One `raw` section as collected, or None; anything else is a ValueError
    (files come back by mail, sometimes trimmed)."""
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
                         f"Expected the output of {evidence_mod.SCRIPT} "
                         f"(schema {evidence_mod.SCHEMA})")
    schema = doc.get(evidence_mod.SCHEMA_FIELD)
    if not schema:
        raise ValueError(f"this file has no 'schema' field, so it is not cluster "
                         f"evidence -- expected {evidence_mod.SCHEMA}, the output of "
                         f"{evidence_mod.SCRIPT}. (Account facts go to --facts.)")
    if schema != evidence_mod.SCHEMA:
        raise ValueError(f"unrecognised cluster evidence: found schema "
                         f"'{schema}', expected '{evidence_mod.SCHEMA}'. Re-collect "
                         f"with the {evidence_mod.SCRIPT} shipped with this version "
                         f"rather than trusting a partial read of a shape this "
                         f"doctor does not know")


def _evidence_checks(doc, namespace):
    """One verdict about the file: where and when it was read, whether it
    describes this namespace, and what the collector was refused."""
    collected = doc.get(evidence_mod.COLLECTED_AT) or "an unrecorded time"
    doc_ns = doc.get(evidence_mod.NAMESPACE)
    parts = [f"cluster read by {evidence_mod.SCRIPT} at {collected} for namespace "
             f"{doc_ns or 'an unnamed namespace'}, not from a live cluster"]
    if describes_elsewhere(doc_ns, namespace):
        # Reported, not refused: the nodes are the same cluster's.
        parts.append(f"but this preflight is for '{namespace}', so the "
                     f"namespaced verdicts below describe '{doc_ns}' "
                     f"instead: re-collect with -n {namespace}")
    if doc.get(evidence_mod.NOTES):
        parts.append(_unread(doc[evidence_mod.NOTES]))
    return [Check("cluster evidence", WARN if len(parts) > 1 else PASS,
                  "; ".join(parts))]


def _unread(notes):
    """The collector's "<section>: <error>" notes: sections listed, distinct
    reasons given once (an unreachable cluster repeats one reason)."""
    reasons = []
    for note in notes:
        reason = note.partition(": ")[2].strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    why = " | ".join(reasons or notes)
    return (f"could not read {', '.join(unreadable_sections(notes))}, reported "
            f"below as unverified rather than as absent: {why[:300]}")


def unreadable_sections(notes):
    """Sections the collector recorded as unreadable, in the order written."""
    sections = []
    for note in notes or []:
        section = str(note).partition(": ")[0]
        if section and section not in sections:
            sections.append(section)
    return sections


def describes_elsewhere(doc_ns, namespace):
    """Does the file describe another namespace than the one preflighted? False
    where either side names none: nothing to compare, not agreement."""
    return bool(namespace and doc_ns and namespace != doc_ns)


def evidence_summary(doc, namespace=None):
    """What the file says about itself, as data for a header (the web UI); the
    same facts _evidence_checks() judges in prose."""
    doc = doc if isinstance(doc, dict) else {}
    doc_ns = doc.get(evidence_mod.NAMESPACE) or None
    return {"collected_at": doc.get(evidence_mod.COLLECTED_AT) or None,
            "namespace": doc_ns,
            "elsewhere": describes_elsewhere(doc_ns, namespace),
            "unreadable": unreadable_sections(doc.get(evidence_mod.NOTES))}


def _ca_configured(opts):
    """Does the bundle configure CA trust in any mode? `.get` over CA_MODES,
    not ca_trust.ca_cfg: a doctor reports rather than raises over a refused pair."""
    return any(opts.get(k) for k in CA_MODES)


def _rc_lines(output, targets):
    """Parse `<url> rc=<n>` lines; a target with no line is None (unknown)."""
    rcs = {t: None for t in targets}
    for line in output.splitlines():
        url, _, rc = line.strip().partition(" rc=")
        if rc.strip().lstrip("-").isdigit() and url in rcs:
            rcs[url] = int(rc.strip())
    return rcs


def _curl_script(targets, cacert=False, settle=0):
    """One shell running every probe, so a doctor costs one exec or one pod.

    Each probe is retried once: a fresh pod can lose its first DNS lookup
    (rc=6) before CoreDNS answers. `settle` delays the first probe until
    `kubectl run -i` has attached; output before that is dropped."""
    ca = ' --cacert "$REQUESTS_CA_BUNDLE"' if cacert else ""
    probe = (f"curl -s -o /dev/null --max-time 20{ca} %s || "
             f"{{ sleep 2; curl -s -o /dev/null --max-time 20{ca} %s; }}")
    lines = [f'{probe % (t, t)}; echo "{t} rc=$?"' for t in targets]
    return "; ".join(([f"sleep {settle}"] if settle else []) + lines)


def probe_egress(cli, namespace, opts):
    """curl each target from inside the cluster -> {target: returncode}.

    From the crane pod where there is one: only there are the profile's proxy
    and CA in force. A throwaway pod cannot verify a corporate CA, so with one
    configured the answer is None (WARN), never a FAIL."""
    targets = egress_targets(opts)
    if kube.kget(cli, namespace, "deploy", "crane"):
        out = kube.crane_exec(cli, namespace,
                              _curl_script(targets, _ca_configured(opts)))
        return _rc_lines(out, targets)
    if _ca_configured(opts):
        return {t: None for t in targets}
    return _oneshot_curl(cli, namespace, targets, opts)


def _oneshot_curl(cli, namespace, targets, opts):
    """Probe from one throwaway pod: one pull and schedule for all targets."""
    env = [arg for name, value in proxy_env(opts).items()
           for arg in ("--env", f"{name}={value}")]
    print(f"  probing egress from a throwaway {CURL_IMAGE} pod in {namespace} "
          f"(crane is not deployed yet)")
    out = kube.quiet(
        [cli, "-n", namespace, "run", f"bzm-doctor-{os.getpid()}", "--rm", "-i",
         "--restart=Never", "--image", CURL_IMAGE, *env, "--command", "--",
         "sh", "-c", _curl_script(targets, settle=2)])
    return _rc_lines(out.stdout, targets)


# Every check takes (facts, opts, cluster). What each reads is on the check
# (@reads) and what it leaves to an earlier one too (@defers_to, enforced by
# _ordered at import), so this is a reading order rather than a contract.
CHECKS = _ordered((check_location, check_threads_per_engine, check_engine_heap,
                   check_crane_pool, check_capacity, check_engine_packing,
                   check_disk, check_limitrange, check_resourcequota,
                   check_admission, check_service_account, check_ingress_class,
                   check_egress))


def resolve_namespace(namespace, opts):
    """The explicit namespace, else the bundle's, else the documented default."""
    return (namespace or (opts or {}).get("namespace")
            or DEFAULT_OPTIONS["namespace"])


def evaluate(facts, opts, namespace, cluster_data=None, probes=None, cli=None,
             extra_checks=(), evidence=None):
    """Every verdict as data, nothing printed.

    `extra_checks` lead the list: verdicts about where the cluster data came
    from, which qualify everything after them. `evidence` is those three parts
    as cluster_from_evidence() returns them; pass it or the parts, not both.
    Whatever is not supplied is read from the live cluster.
    """
    if evidence is not None:
        if cluster_data is not None or probes is not None or extra_checks:
            raise TypeError("pass evidence= or the three parts it carries "
                            "(cluster_data, probes, extra_checks), not both")
        cluster_data, probes, extra_checks = evidence
    opts = dict(opts or {})
    # As generate() does: unset engine limits derive from the location's
    # overrides, so every check judges the size the bundle will carry.
    opts.update(resolve_engine_limits(facts, opts))
    namespace = resolve_namespace(namespace, opts)
    if cluster_data is None or probes is None:
        cli = cli or kube.cli_tool()
    if cluster_data is None:
        cluster_data = gather_cluster(cli, namespace)
    if probes is None:
        probes = probe_egress(cli, namespace, opts)

    cluster = {**cluster_data, "probes": probes}
    return list(extra_checks) + [c for check in CHECKS
                                 for c in run_check(check, facts, opts, cluster)]


def run(facts, opts, namespace, cluster_data=None, probes=None, cli=None,
        extra_checks=(), evidence=None):
    """evaluate() and print the verdict list. Returns the Check list; the
    caller decides the exit code (see has_failures)."""
    checks = evaluate(facts, opts, namespace, cluster_data, probes, cli,
                      extra_checks, evidence)
    verdict.report(f"doctor: location {facts.get('harbor_name')} "
                   f"({facts.get('harbor_id')}), namespace "
                   f"{resolve_namespace(namespace, opts)}",
                   checks, NO_TEST_WOULD_START)
    return checks
