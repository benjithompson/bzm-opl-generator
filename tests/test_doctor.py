"""Offline tests for the pre-flight doctor: every check is a pure verdict over
fixtures shaped like real `kubectl get -o json` output."""

import ast
import inspect
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import (doctor, evidence, facts as facts_mod, kube)  # noqa: E402
from bzm_opl_gen import bundle_options, ca_trust, footprint  # noqa: E402


# -- fixtures ---------------------------------------------------------------

def _node(name="n1", cpu="4", mem="6088480Ki", disk="17734596Ki", ready=True,
          labels=None, taints=None, unschedulable=False):
    """A node as the API server reports it. Defaults are a real minikube node:
    4 CPU, ~5.8Gi allocatable memory, ~17GB ephemeral storage."""
    node = {
        "metadata": {"name": name, "labels": labels or {}},
        "spec": {},
        "status": {
            "allocatable": {"cpu": cpu, "memory": mem, "ephemeral-storage": disk},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }
    if taints:
        node["spec"]["taints"] = taints
    if unschedulable:
        node["spec"]["unschedulable"] = True
    return node


def _big(name="big1"):
    """A node that comfortably holds engines at the documented 2 CPU / 8Gi."""
    return _node(name, cpu="16", mem="64Gi", disk="500Gi")


FACTS = {"harbor_id": "aaa111", "harbor_name": "Test Location",
         "func_ids": ["performance"], "slots": 2, "threads_per_engine": 500}

LR_MATCHING = {
    "metadata": {"name": "blazemeter-engine-sizing"},
    "spec": {"limits": [{"type": "Container",
                         "defaultRequest": {"cpu": "2", "memory": "8Gi"},
                         "default": {"cpu": "2", "memory": "8Gi"},
                         "max": {"cpu": "2", "memory": "8Gi"}}]},
}

NS_BASELINE = {"metadata": {"name": "blazemeter",
                            "labels": {"pod-security.kubernetes.io/enforce": "baseline"}}}


def _find(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert hits, f"no check matching {needle!r} in {[c.name for c in checks]}"
    return hits[0]


# Location overrides below the 2 CPU / 8Gi engine: they replace the bundle's
# requests, which is the only way engines request less than their limits.
LOW_OVERRIDES = {**FACTS, "override_cpu": 1, "override_memory": 1024}


def _statuses(checks):
    return {c.status for c in checks}


# -- facts ------------------------------------------------------------------

class _FakeClient:
    def __init__(self, harbor):
        self._h = harbor

    def private_location(self, harbor_id):
        return self._h


def test_facts_gather_reads_threads_per_engine():
    """Sizing checks are meaningless without it, and it is the field a fresh
    location has unset."""
    f = facts_mod.gather(_FakeClient(
        {"id": "aaa111", "name": "loc", "funcIds": ["performance"],
         "slots": 3, "threadsPerEngine": 500, "ships": []}), "aaa111")
    assert f["slots"] == 3
    assert f["threads_per_engine"] == 500


def test_facts_gather_threads_per_engine_absent_is_none():
    f = facts_mod.gather(_FakeClient(
        {"id": "aaa111", "name": "loc", "funcIds": ["performance"],
         "slots": 1, "ships": []}), "aaa111")
    assert f["threads_per_engine"] is None


# -- check_location ---------------------------------------------------------

def test_location_ok():
    assert _statuses(doctor.check_location(FACTS, {}, {})) == {doctor.PASS}


def test_location_missing_threads_per_engine_fails():
    c = _find(doctor.check_location({**FACTS, "threads_per_engine": None}, {}, {}),
              "threadsPerEngine")
    assert c.status == doctor.FAIL
    assert "403" in c.detail            # what the customer actually sees


@pytest.mark.parametrize("slots", [None, 0])
def test_location_without_slots_fails(slots):
    c = _find(doctor.check_location({**FACTS, "slots": slots}, {}, {}), "slots")
    assert c.status == doctor.FAIL


# Both fields are read from the account, so "there was no account to ask"
# (manual facts) and "the location has it unset" (gather() against a real
# location) both arrive as None -- and only the second is the 403-at-start
# failure. The value cannot tell them apart; how the facts arrived can.

def test_manually_entered_location_reports_the_two_fields_unknown():
    """Hand-entered facts leave slots and threadsPerEngine unknown: WARN, not
    FAIL."""
    checks = doctor.check_location(facts_mod.manual("aaa111", "bbb222"), {}, {})
    assert _statuses(checks) == {doctor.WARN}
    assert not doctor.has_failures(checks)
    for c in checks:
        assert "unknown" in c.detail
        # Still says where to look: unknown is not "no longer your problem".
        assert "Private Locations" in c.detail


def test_gathered_facts_with_the_same_nulls_still_fail():
    """The distinction is the marker, not the value -- identical None/None read
    off a real location stays the FAIL it has always been."""
    gathered = {**FACTS, "slots": None, "threads_per_engine": None,
                "images_source": "live agent inventory"}
    checks = doctor.check_location(gathered, {}, {})
    assert _statuses(checks) == {doctor.FAIL}
    assert "403" in _find(checks, "threadsPerEngine").detail


def test_manual_facts_with_the_values_filled_in_are_checked_normally():
    """Nothing is exempted by the marker: a manual facts file the customer
    completed from the BlazeMeter UI gets the verdicts a gathered one would."""
    filled = {**facts_mod.manual("aaa111", "bbb222"),
              "slots": 2, "threads_per_engine": 500}
    assert _statuses(doctor.check_location(filled, {}, {})) == {doctor.PASS}


def test_manual_facts_with_a_slot_count_of_zero_still_fail():
    """A typed 0 is a supplied value, so zero slots still FAILs on hand-entered
    facts."""
    zero = {**facts_mod.manual("aaa111", "bbb222"),
            "slots": 0, "threads_per_engine": 500}
    assert _find(doctor.check_location(zero, {}, {}), "slots").status == doctor.FAIL


# -- check_threads_per_engine ----------------------------------------------

@pytest.mark.parametrize("threads,opts,status", [
    (500, {}, doctor.PASS),                                            # the documented pairing
    (250, {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}, doctor.PASS),
    (500, {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}, doctor.WARN),
    (1000, {}, doctor.WARN),
    (500, {"engine_cpu_limit": "4", "engine_mem_limit": "16Gi"}, doctor.PASS),
    # Memory is the binding constraint here, not CPU.
    (500, {"engine_cpu_limit": "4", "engine_mem_limit": "2Gi"}, doctor.WARN),
])
def test_threads_per_engine_verdicts(threads, opts, status):
    checks = doctor.check_threads_per_engine({**FACTS, "threads_per_engine": threads}, opts, {})
    assert [c.status for c in checks] == [status]


def test_threads_per_engine_names_the_arithmetic():
    c = doctor.check_threads_per_engine(
        {**FACTS, "threads_per_engine": 500},
        {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}, {})[0]
    assert "250" in c.detail and "500" in c.detail


def test_threads_per_engine_silent_when_unset():
    """check_location already FAILs on this; don't say it twice."""
    assert doctor.check_threads_per_engine({**FACTS, "threads_per_engine": None}, {}, {}) == []


def test_the_ratio_is_named_as_performances_over_a_location_that_runs_browsers():
    """On a GUI location the verdict stands and names the ratio as
    performance's, beside the location's own unit."""
    c = doctor.check_threads_per_engine(
        {**FACTS, "func_ids": ["functionalGui"], "threads_per_engine": 500}, {}, {})[0]
    assert c.status == doctor.PASS
    assert "browser instances" in c.detail
    assert "virtual users" not in c.detail


def test_a_location_with_no_engine_is_not_judged_against_the_engine_ratio():
    """An SV-only location gets a stated "not judged" PASS rather than the
    engine ratio."""
    c = doctor.check_threads_per_engine(
        {**FACTS, "func_ids": ["mockServices"], "threads_per_engine": 500}, {}, {})[0]
    assert c.status == doctor.PASS
    assert "no taurus engine" in c.detail
    # It reports what the account stores and stops there -- no supported figure,
    # because there is none to compare against.
    assert "500" in c.detail and "supports" not in c.detail


def test_funcids_this_tool_does_not_size_keep_the_ratio_and_say_whose_it_is():
    """Unsized funcIds (tdm, delphix) keep the performance ratio with a caveat."""
    over = doctor.check_threads_per_engine(
        {**FACTS, "func_ids": ["tdm"], "threads_per_engine": 1000}, {}, {})[0]
    assert over.status == doctor.WARN         # the arithmetic is unchanged
    assert "performance" in over.detail


def test_unread_funcids_and_funcids_that_size_nothing_read_differently():
    """No funcIds (unread) and funcIds naming no model get different caveats."""
    unread = doctor.check_threads_per_engine(
        {k: v for k, v in FACTS.items() if k != "func_ids"}, {}, {})[0]
    read = doctor.check_threads_per_engine({**FACTS, "func_ids": ["tdm"]}, {}, {})[0]
    assert unread.status == read.status == doctor.PASS
    assert "carry no funcIds" in unread.detail
    assert "name nothing this tool sizes" in read.detail
    assert unread.detail != read.detail


def test_the_engine_heap_is_not_reported_for_an_agent_with_no_jvm():
    """`the location has no engineXmx set` over a service-virtualization
    location is a WARN about a JVM that does not exist there."""
    checks = doctor.check_engine_heap(
        {**FACTS, "func_ids": ["mockServices"], "engine_xmx_mb": None},
        {"engine_mem_limit": "8Gi"}, {})
    assert [c.status for c in checks] == [doctor.PASS]
    assert "no taurus engine" in checks[0].detail


# -- eligible_nodes ---------------------------------------------------------

@pytest.mark.parametrize("node,opts,eligible", [
    (_node(), {}, True),
    (_node(unschedulable=True), {}, False),                # cordoned
    (_node(ready=False), {}, False),
    (_node(taints=[{"key": "lifecycle", "value": "spot", "effect": "NoSchedule"}]),
     {}, False),
    # A taint we tolerate does not exclude the node.
    (_node(taints=[{"key": "lifecycle", "value": "spot", "effect": "NoSchedule"}]),
     {"tolerations": [{"key": "lifecycle", "operator": "Equal", "value": "spot",
                       "effect": "NoSchedule"}]}, True),
    (_node(taints=[{"key": "lifecycle", "value": "spot", "effect": "NoSchedule"}]),
     {"tolerations": [{"operator": "Exists"}]}, True),
    # PreferNoSchedule is a hint, not a rejection.
    (_node(taints=[{"key": "x", "effect": "PreferNoSchedule"}]), {}, True),
    (_node(labels={"pool": "loadtest"}), {"node_selector": {"pool": "loadtest"}}, True),
    (_node(labels={"pool": "web"}), {"node_selector": {"pool": "loadtest"}}, False),
    (_node(), {"node_selector": {"pool": "loadtest"}}, False),
])
def test_eligible_nodes(node, opts, eligible):
    assert bool(doctor.eligible_nodes([node], opts)) is eligible


# -- check_engine_heap ------------------------------------------------------
#
# The container limit is a bundle option and the JVM heap is a location setting,
# so this is the only check comparing the two sources of truth for engine size.
# Both failures it catches are invisible to a scheduler.

def _heap_facts(xmx):
    return dict(FACTS, engine_xmx_mb=xmx)


def test_engine_heap_fails_when_the_jvm_can_fill_the_whole_limit():
    """OOMKilled mid-run, which BlazeMeter reports as a test that stopped
    rather than as a resource error -- so nothing downstream says 'memory'."""
    c = _find(doctor.check_engine_heap(_heap_facts(8192), {"engine_mem_limit": "8Gi"},
                                       {}), "engine heap")
    assert c.status == doctor.FAIL
    assert "OOMKilled" in c.detail


def test_the_vendor_default_pairing_passes():
    """500 threads on a 4096MB heap in an 8Gi container passes."""
    c = _find(doctor.check_engine_heap(_heap_facts(4096), {"engine_mem_limit": "8Gi"},
                                       {}), "engine heap")
    assert c.status == doctor.PASS


def test_a_heap_short_of_its_threads_warns_rather_than_fails():
    """A heap short of its threads WARNs (the per-thread model is unconfirmed),
    and says so."""
    facts = dict(FACTS, engine_xmx_mb=4096, threads_per_engine=1000)
    c = _find(doctor.check_engine_heap(facts, {"engine_mem_limit": "8Gi"}, {}),
              "engine heap")
    assert c.status == doctor.WARN
    assert "8192MB" in c.detail          # still names a figure to aim at
    assert "unverified" in c.detail      # ...and admits what backs it


def test_engine_heap_warns_when_the_heap_dwarfs_the_threads():
    """The 50-thread case, live on 55 locations: the default heap is ten times
    what that load needs, and every engine pod reserves the difference."""
    facts = dict(FACTS, engine_xmx_mb=4096, threads_per_engine=50)
    c = _find(doctor.check_engine_heap(facts, {"engine_mem_limit": "8Gi"}, {}),
              "engine heap")
    assert c.status == doctor.WARN
    assert "cannot address" in c.detail


def test_engine_heap_warns_when_the_container_is_short_for_the_heap():
    """Heap suits the load, but the box does not suit the heap -- the JVM still
    needs stacks, metaspace and direct buffers outside it."""
    facts = dict(FACTS, engine_xmx_mb=6144, threads_per_engine=750)
    c = _find(doctor.check_engine_heap(facts, {"engine_mem_limit": "8Gi"}, {}),
              "engine heap")
    assert c.status == doctor.WARN
    assert "outside it" in c.detail


def test_engine_heap_is_not_judged_against_load_when_threads_are_unset():
    """Unset threadsPerEngine is WARNed as unverified, not judged."""
    facts = dict(FACTS, engine_xmx_mb=4096, threads_per_engine=None)
    c = _find(doctor.check_engine_heap(facts, {"engine_mem_limit": "8Gi"}, {}),
              "engine heap")
    assert c.status == doctor.WARN
    assert "unverified" in c.detail


def test_the_model_reproduces_the_documented_point_exactly():
    """The model reproduces BlazeMeter's documented 500 threads / 4096MB / 8Gi
    exactly."""
    assert doctor.engine_heap_mb(500) == 4096
    assert doctor.engine_container_mb(4096) == 8192


def test_the_floor_is_the_measured_one_not_a_consumption_reading():
    """The container floor is 3072MB, the smallest limit measured to survive a
    run."""
    assert doctor.MIN_CONTAINER_MB == 3072


def test_the_model_never_recommends_below_the_measured_floor():
    """No thread count yields a container below the measured floor."""
    for threads in (1, 10, 50, 100, 250, 300):
        got = doctor.engine_container_mb(doctor.engine_heap_mb(threads))
        assert got >= 3072, f"{threads} threads -> {got}MB, under the measured floor"


def test_the_low_thread_floor_keeps_the_container_startable():
    """Very low thread counts still get a startable container."""
    assert doctor.engine_container_mb(doctor.engine_heap_mb(10)) == doctor.MIN_CONTAINER_MB
    assert doctor.engine_heap_mb(50) > doctor.MIN_HEAP_MB


def test_unknown_heap_is_a_warn_not_a_pass():
    """An unknown heap WARNs rather than assuming the 4096MB default."""
    c = _find(doctor.check_engine_heap(_heap_facts(None), {"engine_mem_limit": "8Gi"},
                                       {}), "engine heap")
    assert c.status == doctor.WARN


# -- two node pools ---------------------------------------------------------
#
# With engines aimed at their own pool, "eligible" means eligible *for an
# engine*. Every capacity number here is about engines, so reading crane's
# placement instead silently measures the wrong nodes.

CRANE_POOL = {"pool": "crane"}
ENGINE_POOL = {"pool": "bzm-engines"}
ENGINE_TAINT = [{"key": "bzm.io/engines", "value": "true", "effect": "NoSchedule"}]
ENGINE_TOL = [{"key": "bzm.io/engines", "operator": "Equal", "value": "true",
               "effect": "NoSchedule"}]
SPLIT = {"node_selector": CRANE_POOL, "engine_node_selector": ENGINE_POOL,
         "engine_tolerations": ENGINE_TOL}


def _engine_node(name="e1", cpu="16", mem="64Gi", pods=None):
    """A node in the dedicated engine pool: labelled, tainted, and big."""
    n = _node(name, cpu=cpu, mem=mem, disk="500Gi", labels=ENGINE_POOL,
              taints=ENGINE_TAINT)
    if pods is not None:
        n["status"]["allocatable"]["pods"] = str(pods)
    return n


# -- engine requests come from the location ---------------------------------
#
# The bundle sets limits and the location's overrideCPU/overrideMemory set
# requests (measured: 1/4096 against a 2 CPU / 8Gi bundle gave requests {1, 4Gi},
# limits {2, 8Gi}); 250m/256Mi is only the unset default.

def test_engine_requests_come_from_the_location_when_set():
    assert footprint.engine_requests({"override_cpu": 1, "override_memory": 4096}) \
        == ("1", "4096Mi")


def test_engine_requests_fall_back_to_cranes_default():
    assert footprint.engine_requests({}) == (footprint.ENGINE_DEFAULT_REQUEST_CPU,
                                       footprint.ENGINE_DEFAULT_REQUEST_MEM)


def test_packing_uses_the_locations_requests_not_the_default():
    """Packing is judged on the location's override requests, not crane's
    default."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    facts = dict(FACTS, override_cpu=2, override_memory=8192)
    # 3 CPU / 12Gi holds exactly one such engine once the requests are honest --
    # and note maxPods is wide open at 32, which is the point: with truthful
    # requests the pod ceiling stops being the thing doing the work.
    node = _engine_node("e1", cpu="3", mem="12Gi", pods=32)
    c = _find(doctor.check_engine_packing(facts, opts, {"nodes": [node]}),
              "engine packing")
    assert c.status == doctor.PASS
    assert "overrideCPU" in c.detail


def test_packing_names_the_location_overrides_as_the_fix():
    """Location overrides below the limits replace the bundle's requests; the
    verdict names them and the value that closes the gap."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    node = _engine_node("e1", cpu="16", mem="64Gi", pods=110)
    c = _find(doctor.check_engine_packing(LOW_OVERRIDES, opts, {"nodes": [node]}),
              "engine packing")
    assert c.status == doctor.WARN
    assert "overrideCPU/overrideMemory" in c.detail
    assert "8192MB" in c.detail          # the value to set, in the field's unit


def test_packing_passes_on_the_bundles_own_requests():
    """With no location overrides the bundle's requests equal the limits, so a
    node sized for one engine accepts one, whatever its pod ceiling."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    node = _engine_node("e1", cpu="3", mem="10Gi", pods=110)
    c = _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [node]}),
              "engine packing")
    assert c.status == doctor.PASS
    assert "KUBERNETES_RESOURCES_DEFAULT" in c.detail


# -- check_crane_pool -------------------------------------------------------
#
# Crane's own pool on a split bundle. Numbers below are a real GKE e2-medium.

def _e2_medium(name="c1"):
    """An e2-medium node: 940m allocatable CPU (below crane's 1 CPU limit,
    above its request), ~2.73Gi."""
    return _node(name, cpu="940m", mem="2866848Ki", labels=CRANE_POOL)


def test_crane_pool_warns_when_the_node_cannot_reach_cranes_limit():
    """A crane pool whose largest node is below crane's limit WARNs."""
    cluster = {"nodes": [_e2_medium(), _engine_node("e1")]}
    c = _find(doctor.check_crane_pool(FACTS, SPLIT, cluster), "crane pool")
    assert c.status == doctor.WARN
    assert "940m" in c.detail and "throttled" in c.detail


def test_crane_pool_passes_on_a_node_that_holds_cranes_limit():
    node = _node("c1", cpu="2", mem="4Gi", labels=CRANE_POOL)
    c = _find(doctor.check_crane_pool(FACTS, SPLIT, {"nodes": [node]}), "crane pool")
    assert c.status == doctor.PASS


def test_crane_pool_fails_when_nothing_matches_cranes_selector():
    """A crane pool that does not exist is a location that never comes online --
    distinct from the engine pool being empty, which check_capacity reports."""
    cluster = {"nodes": [_engine_node("e1")]}
    c = _find(doctor.check_crane_pool(FACTS, SPLIT, cluster), "crane pool")
    assert c.status == doctor.FAIL


def test_crane_pool_is_not_checked_on_a_one_pool_bundle():
    """check_capacity already spends crane's share out of the nodes it
    measures, so a second verdict would be double-counting it."""
    assert doctor.check_crane_pool(FACTS, {}, {"nodes": [_big("a")]}) == []


def test_an_empty_engine_pool_is_a_warn_not_a_failure():
    """A dedicated engine pool with no nodes WARNs (it may be scaled to zero),
    not FAILs."""
    cluster = {"nodes": [_node("c1", labels=CRANE_POOL)]}      # crane only, no engine nodes
    checks = doctor.check_capacity(FACTS, SPLIT, cluster)
    c = _find(checks, "eligible nodes")
    assert c.status == doctor.WARN
    assert not doctor.has_failures(checks)
    assert "cluster-autoscaler-status" in c.detail      # says what to look at


def test_an_empty_single_pool_cluster_is_still_a_failure():
    """Nothing was aimed anywhere, so there is no autoscaling pool to be waiting
    on -- an empty match really does mean engines have nowhere to run."""
    opts = {"node_selector": {"pool": "nope"}}
    c = _find(doctor.check_capacity(FACTS, opts, {"nodes": [_big("a")]}),
              "eligible nodes")
    assert c.status == doctor.FAIL


def test_eligible_nodes_follows_the_engine_pool_not_cranes():
    """The crane pool is small and untainted; the engine pool is tainted and
    labelled differently. An engine belongs on exactly one of them."""
    crane_node = _node("c1", labels=CRANE_POOL)
    engine_node = _engine_node("e1")
    nodes = [crane_node, engine_node]
    assert [n["metadata"]["name"] for n in doctor.eligible_nodes(nodes, SPLIT)] == ["e1"]
    # ...and crane's own placement still resolves to crane's node when asked for.
    from bzm_opl_gen.bundle_options import crane_scheduling
    assert [n["metadata"]["name"] for n in
            doctor.eligible_nodes(nodes, SPLIT, crane_scheduling(SPLIT))] == ["c1"]


def test_capacity_does_not_spend_crane_out_of_a_pool_it_is_not_on():
    """Crane is not charged to an engine pool it cannot land on."""
    cluster = {"nodes": [_node("c1", labels=CRANE_POOL), _engine_node("e1")]}
    agg = _find(doctor.check_capacity(FACTS, SPLIT, cluster), "aggregate")
    assert agg.status == doctor.PASS
    assert "own pool" in agg.detail          # and it says why it did not charge it
    # Same nodes, one pool: crane shares them, so its share is spent.
    shared = {"nodes": [_big("a")]}
    agg_shared = _find(doctor.check_capacity(FACTS, {}, shared), "aggregate")
    assert "after crane's own" in agg_shared.detail


# -- check_engine_packing ---------------------------------------------------
#
# Crane stamps engine requests from the location (250m/256Mi unset) whatever
# the limits say, and the scheduler places on requests.

def test_engine_packing_warns_when_requests_let_engines_pile_onto_one_node():
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    # 16 CPU / 64Gi runs 8 engines but *accepts* 16 by the location's requests.
    checks = doctor.check_engine_packing(LOW_OVERRIDES, opts,
                                         {"nodes": [_engine_node("e1")]})
    c = _find(checks, "engine packing")
    assert c.status == doctor.WARN
    assert "1/1024Mi" in c.detail and "maxPods" in c.detail
    # Never a FAIL: the engines do start, and the cost is the validity of the
    # numbers rather than the run.
    assert c.status != doctor.FAIL


def test_engine_packing_passes_when_maxpods_caps_the_node():
    """allocatable.pods capped at system pods plus one passes."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    # Derived from TYPICAL_SYSTEM_PODS, so the test follows a corrected count.
    caps_at_one = footprint.TYPICAL_SYSTEM_PODS + 1
    checks = doctor.check_engine_packing(
        FACTS, opts, {"nodes": [_engine_node("e1", pods=caps_at_one)]})
    assert _find(checks, "engine packing").status == doctor.PASS


def test_engine_packing_allows_the_engines_the_pool_was_designed_for():
    """A node taking exactly the engines the pool was designed for is not over-
    packed."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi",
                engines_per_node=2)
    node = _engine_node("e1", cpu="8", mem="32Gi",
                        pods=footprint.TYPICAL_SYSTEM_PODS + 2)
    assert _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [node]}),
                 "engine packing").status == doctor.PASS
    # ...and one designed for 2 but ceilinged for 4 is still over-packed.
    loose = _engine_node("e2", cpu="8", mem="32Gi",
                         pods=footprint.TYPICAL_SYSTEM_PODS + 4)
    assert _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [loose]}),
                 "engine packing").status == doctor.WARN


def test_the_recipe_builds_a_pool_the_checker_passes():
    """A pool built to the generated nodepools recipe passes the packing check."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    recipe_max_pods = footprint.TYPICAL_SYSTEM_PODS + 1
    # The recipe's node: one engine's limits plus the kubelet's reservations.
    node = _engine_node("e1", cpu="3", mem="10Gi", pods=recipe_max_pods)
    c = _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [node]}),
              "engine packing")
    assert c.status == doctor.PASS
    # ...and without the ceiling, under low location overrides, it warns.
    loose = _engine_node("e2", cpu="3", mem="10Gi", pods=110)
    assert _find(doctor.check_engine_packing(LOW_OVERRIDES, opts, {"nodes": [loose]}),
                 "engine packing").status == doctor.WARN


def test_engine_packing_is_silent_when_no_node_is_eligible():
    """check_capacity already FAILs on an empty eligible set; a second verdict
    saying the same thing is noise, and this one would have nothing to measure."""
    cluster = {"nodes": [_node("c1", labels=CRANE_POOL)]}
    assert doctor.check_engine_packing(FACTS, SPLIT, cluster) == []


def test_engine_packing_warns_rather_than_guesses_when_nodes_are_unread():
    """Unread nodes WARN through the declared section, not a verdict on an
    empty pool."""
    checks = doctor.run_check(doctor.check_engine_packing, FACTS, SPLIT,
                              {"nodes": None})
    c = _find(checks, "engine packing")
    assert c.status == doctor.WARN
    assert "could not be read" in c.detail


# -- check_capacity ---------------------------------------------------------

def test_capacity_ok_on_a_real_cluster():
    checks = doctor.check_capacity(FACTS, {}, {"nodes": [_big("a"), _big("b")]})
    assert _statuses(checks) == {doctor.PASS}
    # allocatable is an upper bound, not free space -- say so.
    assert any("allocatable" in c.detail for c in checks)


def test_capacity_per_node_fit_fails_when_no_node_holds_one_engine():
    """A pod is not splittable: three 5.8Gi nodes cannot run one 8Gi engine."""
    checks = doctor.check_capacity(FACTS, {}, {"nodes": [_node("n1"), _node("n2"), _node("n3")]})
    fit = _find(checks, "per-node")
    assert fit.status == doctor.FAIL
    assert "8Gi" in fit.detail


def test_capacity_aggregate_fails_and_counts_engines():
    nodes = [_node("n1", cpu="4", mem="8Gi", disk="500Gi"),
             _node("n2", cpu="4", mem="8Gi", disk="500Gi")]
    checks = doctor.check_capacity({**FACTS, "slots": 5}, {}, {"nodes": nodes})
    assert _find(checks, "per-node").status == doctor.PASS
    agg = _find(checks, "aggregate")
    assert agg.status == doctor.FAIL
    # 16Gi across the two nodes, less crane's own 2Gi -- one 8Gi engine, not two.
    assert "1 engine" in agg.detail
    assert "crane" in agg.detail


def test_capacity_uses_the_configured_engine_size():
    """Sized down for a laptop, one engine fits the same node that cannot hold
    a documented 2 CPU / 8Gi one."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, opts, {"nodes": [_node()]}),
                 "per-node").status == doctor.PASS
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, {}, {"nodes": [_node()]}),
                 "per-node").status == doctor.FAIL


def test_capacity_aggregate_spends_cranes_own_share():
    """Crane runs in the same namespace: a 5.8Gi node cannot hold both crane
    (2Gi) and a 4Gi engine, even though the engine alone fits."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    agg = _find(doctor.check_capacity({**FACTS, "slots": 1}, opts, {"nodes": [_node()]}),
                "aggregate")
    assert agg.status == doctor.FAIL
    # Two of those nodes leave room once crane is paid for.
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, opts, {"nodes": [_node("n1"), _node("n2")]}),
                 "aggregate").status == doctor.PASS


def test_capacity_fails_when_the_selector_matches_nothing():
    checks = doctor.check_capacity(FACTS, {"node_selector": {"pool": "loadtest"}}, {"nodes": [_big("a")]})
    assert doctor.FAIL in _statuses(checks)
    assert any("pool" in c.detail for c in checks)


def test_capacity_says_allocatable_is_an_upper_bound():
    """The verdict must not read as 'there is room' -- allocatable counts what
    other workloads already hold."""
    checks = doctor.check_capacity(FACTS, {}, {"nodes": [_big("a")]})
    assert any("upper bound" in c.detail for c in checks)
    assert all("not free" in c.detail or "upper bound" in c.detail
               for c in checks)


# -- check_disk -------------------------------------------------------------

def test_disk_warns_on_a_laptop_node():
    c = doctor.check_disk(FACTS, {}, {"nodes": [_node()]})[0]
    assert c.status == doctor.WARN
    assert "60" in c.detail and "40" in c.detail       # total and /tmp


def test_disk_ok_on_a_real_node():
    assert doctor.check_disk(FACTS, {}, {"nodes": [_big("a")]})[0].status == doctor.PASS


def test_disk_warns_when_the_cluster_cannot_hold_every_slot():
    nodes = [_node("n1", disk="100G"), _node("n2", disk="100G")]
    c = doctor.check_disk({**FACTS, "slots": 5}, {}, {"nodes": nodes})[0]
    assert c.status == doctor.WARN
    assert "2" in c.detail                              # one engine per node


def test_disk_ignores_ineligible_nodes():
    nodes = [_big("a"), _node("cordoned", disk="1Gi", unschedulable=True)]
    assert doctor.check_disk(FACTS, {}, {"nodes": nodes})[0].status == doctor.PASS


# -- check_limitrange -------------------------------------------------------

def test_limitrange_absent_warns_that_nothing_caps_the_namespace():
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": []})[0]
    assert c.status == doctor.WARN
    assert "nothing caps" in c.detail


def test_limitrange_matching_passes():
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [LR_MATCHING]})) == {doctor.PASS}


def test_limitrange_max_below_engine_fails():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "2Gi"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "team-caps" in c.detail                      # name the object


def test_limitrange_min_above_the_request_fails():
    """A LimitRange min above the request crane sets (here the location's
    overrides) FAILs."""
    lr = {"metadata": {"name": "floor"},
          "spec": {"limits": [{"type": "Container",
                               "min": {"cpu": "1500m", "memory": "2Gi"}}]}}
    c = doctor.check_limitrange(LOW_OVERRIDES, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "min cpu" in c.detail


def test_limitrange_ratio_tighter_than_the_engines_own_gap_fails():
    """Location overrides of 1 / 1024MB against 2 / 8Gi limits are a 2x and 8x
    ratio; a maxLimitRequestRatio below that rejects the engine."""
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "1.5", "memory": "4"}}]}}
    c = doctor.check_limitrange(LOW_OVERRIDES, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "maxLimitRequestRatio" in c.detail


def test_limitrange_ratio_of_one_passes_on_the_bundles_requests():
    """The bundle's requests equal its limits, so even a 1:1 ratio admits it."""
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "1", "memory": "1"}}]}}
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})) == {doctor.PASS}


def test_limitrange_ratio_wide_enough_passes():
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "16", "memory": "64"}}]}}
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})) == {doctor.PASS}


def test_limitrange_conflicting_defaults_warn():
    lr = {"metadata": {"name": "platform-defaults"},
          "spec": {"limits": [{"type": "Container",
                               "defaultRequest": {"cpu": "500m", "memory": "1Gi"},
                               "default": {"cpu": "1", "memory": "2Gi"}}]}}
    checks = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})
    assert doctor.WARN in _statuses(checks)
    assert any("platform-defaults" in c.detail for c in checks)


def test_limitrange_max_measured_against_the_configured_engine():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "4Gi"},
                               "default": {"cpu": "1", "memory": "4Gi"},
                               "defaultRequest": {"cpu": "1", "memory": "4Gi"}}]}}
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    assert _statuses(doctor.check_limitrange(FACTS, opts, {"limitranges": [lr]})) == {doctor.PASS}


# -- check_resourcequota ----------------------------------------------------

def _quota(name="team-quota", hard=None, used=None):
    return {"metadata": {"name": name},
            "status": {"hard": hard or {}, "used": used or {}}}


def test_resourcequota_absent_passes():
    """No ResourceQuota passes, with both declared sections supplied."""
    checks = doctor.check_resourcequota(FACTS, {},
                                        {"quotas": [], "limitranges": []})
    assert _statuses(checks) == {doctor.PASS}


def test_resourcequota_with_room_passes():
    q = _quota(hard={"limits.cpu": "20", "limits.memory": "80Gi", "pods": "50"},
               used={"limits.cpu": "2", "limits.memory": "4Gi", "pods": "3"})
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    assert _statuses(checks) == {doctor.PASS}


def test_resourcequota_too_small_for_the_concurrency_fails():
    """slots=2 needs 4 CPU / 16Gi of quota headroom."""
    q = _quota(hard={"limits.cpu": "4", "limits.memory": "8Gi"},
               used={"limits.cpu": "1", "limits.memory": "2Gi"})
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    fails = [c for c in checks if c.status == doctor.FAIL]
    assert fails and all("team-quota" in c.detail for c in fails)
    assert any("limits.memory" in c.detail for c in fails)


def test_resourcequota_pod_count_includes_crane():
    q = _quota(hard={"pods": "2"}, used={"pods": "0"})      # 2 engines + crane = 3
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    assert any(c.status == doctor.FAIL and "pods" in c.detail for c in checks)


def test_resourcequota_counts_the_cpu_alias_as_requests():
    q = _quota(hard={"cpu": "3"}, used={"cpu": "0"})        # alias of requests.cpu
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    assert any(c.status == doctor.FAIL for c in checks)


def test_resourcequota_without_a_limitrange_warns_about_explicit_requests():
    """With a cpu/memory quota, k8s rejects any pod that does not declare that
    resource -- and crane sets no requests on the engines it spawns."""
    q = _quota(hard={"requests.cpu": "40", "requests.memory": "160Gi"},
               used={"requests.cpu": "0", "requests.memory": "0"})
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": []})
    warn = [c for c in checks if c.status == doctor.WARN]
    assert warn and any("LimitRange" in c.detail for c in warn)


# -- check_admission --------------------------------------------------------

NS_RESTRICTED = {"metadata": {"labels":
                 {"pod-security.kubernetes.io/enforce": "restricted"}}}


def test_admission_k8s_restricted_passes_now_that_engines_drop_privileges():
    """Restricted PSA passes with restrict_engines on."""
    c = doctor.check_admission(FACTS, {"platform": "k8s"},
                               {"namespace": NS_RESTRICTED})[0]
    assert c.status == doctor.PASS


def test_admission_k8s_restricted_still_fails_with_engine_restriction_off():
    """The verdict follows the option, not the platform -- turning the envs off
    is what puts this namespace back where it was."""
    c = doctor.check_admission(FACTS,
                               {"platform": "k8s", "restrict_engines": False},
                               {"namespace": NS_RESTRICTED})[0]
    assert c.status == doctor.FAIL
    assert "engine" in c.detail


def test_admission_k8s_baseline_passes():
    assert doctor.check_admission(FACTS, {"platform": "k8s"}, {"namespace": NS_BASELINE})[0].status == doctor.PASS


def test_admission_k8s_unlabelled_warns():
    c = doctor.check_admission(FACTS, {"platform": "k8s"},
                               {"namespace": {"metadata": {"labels": {}}}})[0]
    assert c.status == doctor.WARN


def test_admission_tells_an_absent_namespace_from_an_unread_one():
    """{} (not created yet) and None (not readable) get different advice."""
    absent = doctor.run_check(doctor.check_admission, FACTS, {"platform": "k8s"},
                              {"namespace": {}})[0]
    unread = doctor.run_check(doctor.check_admission, FACTS, {"platform": "k8s"},
                              {"namespace": None})[0]
    assert absent.status == unread.status == doctor.WARN
    assert "does not exist yet" in absent.detail
    # The claim that cannot be made about a namespace nobody could read.
    assert "does not exist" not in unread.detail
    assert "could not be read" in unread.detail


def test_admission_openshift_needs_a_uid_range():
    ns = {"metadata": {"annotations": {"openshift.io/sa.scc.uid-range": "1000700000/10000"}}}
    assert doctor.check_admission(FACTS, {"platform": "openshift"}, {"namespace": ns})[0].status == doctor.PASS
    c = doctor.check_admission(FACTS, {"platform": "openshift"},
                               {"namespace": {"metadata": {}}})[0]
    assert c.status == doctor.WARN
    assert "INHERIT_RUNNING_USER_AND_GROUP" in c.detail


# -- check_service_account ---------------------------------------------------
# The live counterpart is a run that never produces a pod: the Deployment
# applies, the ReplicaSet records `serviceaccounts "x" not found`, and nothing
# else says anything. Cheap to catch here, expensive to catch there.

EXISTING_SA = {"service_account_name": "platform-sa",
               "service_account_create": False}


def _sa(*names):
    return [{"metadata": {"name": n}} for n in ("default",) + names]


def test_service_account_silent_when_the_bundle_creates_it():
    """The default bundle brings its own, so there is nothing to look up --
    and no verdict, rather than one that is trivially true."""
    assert doctor.check_service_account(FACTS, {}, {"serviceaccounts": _sa()}) == []


def test_existing_service_account_found_passes():
    c = doctor.check_service_account(
        FACTS, EXISTING_SA, {"serviceaccounts": _sa("platform-sa")})[0]
    assert c.status == doctor.PASS
    assert "platform-sa" in c.detail


def test_existing_service_account_missing_fails():
    c = doctor.check_service_account(
        FACTS, EXISTING_SA, {"serviceaccounts": _sa("something-else")})[0]
    assert c.status == doctor.FAIL
    assert "platform-sa" in c.detail
    # The failure mode is the point: nothing errors at apply time.
    assert "no pod is ever created" in c.detail


def test_unreadable_namespace_warns_rather_than_failing():
    """Null and empty ServiceAccount lists give the same unverified WARN."""
    empty = doctor.check_service_account(FACTS, EXISTING_SA,
                                         {"serviceaccounts": []})[0]
    unread = doctor.check_service_account(FACTS, EXISTING_SA,
                                          {"serviceaccounts": None})[0]
    assert empty.status == unread.status == doctor.WARN
    assert empty.detail == unread.detail
    assert "platform-sa" in empty.detail


# -- check_ingress_class ----------------------------------------------------

SV_NGINX = {"sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
            "sv_tls_secret": "wildcard-credential"}


def _ingressclass(name, controller="k8s.io/ingress-nginx"):
    return {"metadata": {"name": name}, "spec": {"controller": controller}}


def test_ingress_class_silent_without_service_virtualization():
    """A performance-only location creates no Ingress; don't judge the cluster
    on something it never uses."""
    assert doctor.check_ingress_class(FACTS, {}, {"ingressclasses": []}) == []


def test_ingress_class_silent_when_the_ingress_was_declined():
    """sv_ingress=none produces no verdict."""
    assert doctor.check_ingress_class(
        FACTS, {"sv_ingress": doctor.SV_INGRESS_NONE},
        {"ingressclasses": []}) == []


def test_ingress_class_present_passes():
    checks = doctor.check_ingress_class(
        FACTS, SV_NGINX, {"ingressclasses": [_ingressclass("nginx"),
                                             _ingressclass("traefik")]})
    assert _statuses(checks) == {doctor.PASS}


def test_ingress_class_missing_fails_and_names_what_does_exist():
    """The OpenShift default: one class, called something else. Nothing in the
    deploy fails -- the endpoint just 503s -- so the detail has to explain it."""
    cluster = {"ingressclasses": [
        _ingressclass("openshift-default", "openshift.io/ingress-to-route")]}
    c = doctor.check_ingress_class(FACTS, SV_NGINX, cluster)[0]
    assert c.status == doctor.FAIL
    assert "openshift-default" in c.detail       # what the cluster has instead
    assert "503" in c.detail
    assert "hardcodes" in c.detail               # not a generator option


def test_ingress_class_none_at_all_fails():
    c = doctor.check_ingress_class(FACTS, SV_NGINX, {"ingressclasses": []})[0]
    assert c.status == doctor.FAIL


@pytest.mark.parametrize("ingress", ["istio", "contour", "openshift"])
def test_ingress_class_crd_based_types_are_never_a_failure(ingress):
    """istio, contour and openshift route without an IngressClass and PASS."""
    checks = doctor.check_ingress_class(
        FACTS, {**SV_NGINX, "sv_ingress": ingress}, {"ingressclasses": []})
    assert _statuses(checks) == {doctor.PASS}
    assert ingress in checks[0].detail


def test_ingress_class_unrecognised_value_warns_rather_than_fails():
    """A hand-written profile can carry a value generate() would have rejected;
    checking nginx's class name against it would be a misleading FAIL."""
    checks = doctor.check_ingress_class(
        FACTS, {**SV_NGINX, "sv_ingress": "traefik"}, {"ingressclasses": []})
    assert _statuses(checks) == {doctor.WARN}
    assert "traefik" in checks[0].detail


def test_ingress_class_unreadable_warns_rather_than_fails():
    """An API server that does not serve the kind -- 'we could not look', not
    'the class is missing'."""
    checks = doctor.check_ingress_class(FACTS, SV_NGINX,
                                        {"ingressclasses": None})
    assert _statuses(checks) == {doctor.WARN}


def test_ingress_class_with_no_such_key_at_all_is_a_fixture_error():
    """Cluster data with no ingressclasses key raises MissingSection."""
    with pytest.raises(doctor.MissingSection):
        doctor.check_ingress_class(FACTS, SV_NGINX, {})


def test_ingress_class_openshift_alias_says_the_route_still_will_not_be_made():
    """Aliasing the name to the ingress-to-route controller satisfies the class
    lookup but not the port mismatch behind it; a bare PASS would mislead."""
    cluster = {"ingressclasses": [
        _ingressclass("nginx", "openshift.io/ingress-to-route")]}
    c = doctor.check_ingress_class(FACTS, SV_NGINX, cluster)[0]
    assert c.status == doctor.PASS
    assert "IncompleteIngressToRouteRules" in c.detail


# -- egress -----------------------------------------------------------------

def test_egress_targets_include_the_private_registry():
    targets = doctor.egress_targets({"private_registry": "reg.corp:5001/blazemeter"})
    assert any("a.blazemeter.com" in t for t in targets)
    assert any("reg.corp:5001" in t for t in targets)
    # Engines upload to hosts crane never touches -- an egress rule shaped
    # around crane alone would pass a crane-only probe and still break runs.
    assert any("data.blazemeter.com" in t for t in targets)
    assert any("storage.blazemeter.com" in t for t in targets)
    assert len(doctor.egress_targets({})) == len(doctor.egress_targets(
        {"private_registry": "reg.corp:5001/bzm"})) - 1


@pytest.mark.parametrize("rc,status,marker", [
    (0, doctor.PASS, None),
    (6, doctor.FAIL, "proxy"),          # DNS: reminder that a proxy/CA must be honoured
    (28, doctor.FAIL, "proxy"),
    (None, doctor.WARN, None),          # could not probe -- never a false FAIL
])
def test_egress_verdicts(rc, status, marker):
    c = doctor.check_egress(FACTS, {}, {"probes": {doctor.API_PROBE_URL: rc}})[0]
    assert c.status == status
    assert doctor.API_PROBE_URL in c.detail
    if marker:
        assert marker in c.detail


def test_egress_without_probes_warns():
    assert doctor.check_egress(FACTS, {}, {"probes": None})[0].status == doctor.WARN


def _crane(monkeypatch, deployed, output=""):
    monkeypatch.setattr(kube, "kget",
                        lambda cli, ns, kind, name=None: {"x": 1} if deployed else {})
    monkeypatch.setattr(kube, "kget_named",
                        lambda cli, ns, kind, name=None: {"x": 1} if deployed else {})
    seen = []
    monkeypatch.setattr(kube, "crane_exec",
                        lambda cli, ns, sh: seen.append(sh) or output)
    return seen


def test_probe_egress_uses_one_exec_for_every_target(monkeypatch):
    """One shell for all the probes: each exec is a spawn plus the round trips
    to resolve deploy -> pod, and the 20s timeouts would stack serially."""
    targets = doctor.egress_targets({"ca_bundle": "PEM"})
    seen = _crane(monkeypatch, True,
                  "\n".join(f"{t} rc=0" for t in targets))
    probes = doctor.probe_egress("kubectl", "ns1", {"ca_bundle": "PEM"})
    assert probes == {t: 0 for t in targets}
    assert len(seen) == 1
    # The CA the profile configures has to be the one curl verifies against.
    assert '--cacert "$REQUESTS_CA_BUNDLE"' in seen[0]


def test_curl_script_retries_each_probe_once():
    """Each probe is retried once (a fresh pod's first DNS lookup can fail)."""
    script = doctor._curl_script(["https://x/"])
    assert script.count("curl") == 2
    assert "sleep 2" in script
    # The one-shot pod also has to wait for `kubectl run -i` to attach, or the
    # first lines are written to nobody.
    assert doctor._curl_script(["https://x/"], settle=2).startswith("sleep 2")


def test_probe_egress_reports_a_target_with_no_rc_line_as_unknown(monkeypatch):
    """The exec never ran, or one curl produced nothing: 'we could not look',
    not 'BlazeMeter is unreachable'."""
    _crane(monkeypatch, True, "")
    assert set(doctor.probe_egress("kubectl", "ns1", {}).values()) == {None}


def test_probe_egress_mixes_known_and_unknown_targets(monkeypatch):
    targets = doctor.egress_targets({})
    _crane(monkeypatch, True, f"{targets[0]} rc=7")
    probes = doctor.probe_egress("kubectl", "ns1", {})
    assert probes[targets[0]] == 7
    assert all(probes[t] is None for t in targets[1:])


def test_probe_egress_cannot_honour_a_ca_without_crane(monkeypatch):
    """A bare curl pod has no trust bundle; report 'unknown', not 'broken'."""
    _crane(monkeypatch, False)
    probes = doctor.probe_egress("kubectl", "ns1", {"ca_bundle": "PEM"})
    assert set(probes.values()) == {None}


@pytest.mark.parametrize("mode", sorted(ca_trust.CA_MODES))
def test_a_bundle_with_any_ca_mode_probes_with_that_ca(monkeypatch, mode):
    """Every CA mode makes the crane-pod probe pass --cacert."""
    opts = {mode: True if bundle_options.DEFAULT_OPTIONS[mode] is False else "x"}
    targets = doctor.egress_targets(opts)
    seen = _crane(monkeypatch, True, "\n".join(f"{t} rc=0" for t in targets))
    doctor.probe_egress("kubectl", "ns1", opts)
    assert '--cacert "$REQUESTS_CA_BUNDLE"' in seen[0]
    # And with crane absent, the answer is 'unknown' rather than a one-shot
    # pod's verdict, which has no trust bundle to reach that CA with.
    _crane(monkeypatch, False)
    assert set(doctor.probe_egress("kubectl", "ns1", opts).values()) == {None}


def test_probe_egress_falls_back_to_one_shot_pod(monkeypatch):
    """One throwaway pod for all targets -- not one image pull and schedule
    per URL."""
    _crane(monkeypatch, False)
    pods = []
    monkeypatch.setattr(doctor, "_oneshot_curl",
                        lambda cli, ns, targets, opts: pods.append(targets)
                        or {t: 7 for t in targets})
    assert set(doctor.probe_egress("kubectl", "ns1", {}).values()) == {7}
    assert len(pods) == 1


# -- gather_cluster ---------------------------------------------------------

QUOTA_ITEM = {"kind": "ResourceQuota", "metadata": {"name": "q"},
              "status": {"hard": {}, "used": {}}}


SA_ITEM = {"kind": "ServiceAccount", "metadata": {"name": "crane"}}


def test_gather_cluster_splits_one_namespaced_get_by_kind(monkeypatch):
    """One namespaced get is split into LimitRanges, ResourceQuotas and
    ServiceAccounts."""
    calls = []

    def fake_kget(cli, namespace, kind, name=None):
        calls.append((namespace, kind, name))
        if kind == "nodes":
            return {"items": [_big("a")]}
        if kind == "ingressclass":
            return {"items": [_ingressclass("nginx")]}
        if kind == "ns":
            return NS_BASELINE
        return {"items": [dict(LR_MATCHING, kind="LimitRange"), QUOTA_ITEM,
                          SA_ITEM]}

    monkeypatch.setattr(kube, "kget", fake_kget)
    monkeypatch.setattr(kube, "kget_named", fake_kget)
    data = doctor.gather_cluster("kubectl", "ns1")
    assert [n["metadata"]["name"] for n in data["nodes"]] == ["a"]
    assert data["limitranges"] == [dict(LR_MATCHING, kind="LimitRange")]
    assert data["quotas"] == [QUOTA_ITEM]
    assert data["serviceaccounts"] == [SA_ITEM]
    assert data["namespace"] == NS_BASELINE
    assert ("ns1", "limitrange,resourcequota,serviceaccount", None) in calls
    # IngressClass is cluster-scoped, so it is read like nodes are.
    assert ("ingressclass" in [kind for _, kind, _ in calls])
    assert [c["metadata"]["name"] for c in data["ingressclasses"]] == ["nginx"]


def test_gather_cluster_survives_a_missing_namespace(monkeypatch):
    """`get ns` fails on a namespace that does not exist yet -- that is the
    normal pre-flight case, not a crash."""
    monkeypatch.setattr(kube, "kget", lambda *a, **k: {})
    monkeypatch.setattr(kube, "kget_named", lambda *a, **k: {})
    data = doctor.gather_cluster("kubectl", "ns1")
    # A failed get ({} from kget) is None, not [], so a denied list is unread
    # rather than empty. The namespace stays {}: "not created yet".
    assert data == {"nodes": None, "ingressclasses": None, "limitranges": None,
                    "quotas": None, "serviceaccounts": None, "namespace": {}}


def test_a_namespace_nobody_may_read_is_unread_not_absent(monkeypatch):
    """A refused `get ns` is None (unread), not {} (absent)."""
    monkeypatch.setattr(kube, "kget", lambda *a, **k: {})
    monkeypatch.setattr(kube, "kget_named", lambda *a, **k: None)
    data = doctor.gather_cluster("kubectl", "ns1")
    assert data["namespace"] is None
    [check] = doctor.run_check(doctor.check_admission, FACTS, {}, data)
    assert check.status == doctor.WARN
    assert "does not exist" not in check.detail
    assert "could not be read" in check.detail


@pytest.mark.parametrize("served,expected,status", [
    ({"items": []}, [], doctor.FAIL),   # asked, cluster has none -> nothing claims it
    ({}, None, doctor.WARN),            # kget's failure shape -> we did not look
])
def test_gather_cluster_keeps_unreadable_ingressclasses_apart_from_empty(
        monkeypatch, served, expected, status):
    """An unserved IngressClass read is None, not []."""
    monkeypatch.setattr(kube, "kget",
                        lambda cli, ns, kind, name=None:
                        served if kind == "ingressclass" else {})
    monkeypatch.setattr(kube, "kget_named",
                        lambda cli, ns, kind, name=None:
                        served if kind == "ingressclass" else {})
    data = doctor.gather_cluster("kubectl", "ns1")
    assert data["ingressclasses"] == expected
    assert _statuses(doctor.check_ingress_class(FACTS, SV_NGINX, data)) == {status}


# -- a check declares the sections it reads ----------------------------------
#
# "Could not read" (None) and "there is nothing there" ([]/{}) never share a
# verdict: a declared check's body only sees sections that were read, and a
# section with no key at all raises MissingSection.

UNREAD_ALL = {"nodes": None, "ingressclasses": None, "limitranges": None,
              "quotas": None, "serviceaccounts": None, "namespace": None}
EMPTY_ALL = {"nodes": [], "ingressclasses": [], "limitranges": [],
             "quotas": [], "serviceaccounts": [], "namespace": {}}

# Every check that reads a cluster section, with the sections it declares and
# the options that make it read them. The three not here -- check_location,
# check_threads_per_engine, check_engine_heap -- judge the location's settings
# against the bundle's and read no cluster section at all.
DECLARING = {
    doctor.check_crane_pool: ("nodes",),
    doctor.check_capacity: ("nodes",),
    doctor.check_engine_packing: ("nodes",),
    doctor.check_disk: ("nodes",),
    doctor.check_limitrange: ("limitranges",),
    doctor.check_resourcequota: ("quotas", "limitranges"),
    doctor.check_admission: ("namespace",),
    doctor.check_service_account: ("serviceaccounts",),
    doctor.check_ingress_class: ("ingressclasses",),
    doctor.check_egress: ("probes",),
}
FACTS_ONLY = (doctor.check_location, doctor.check_threads_per_engine,
              doctor.check_engine_heap)
# Options under which every declaration above is live at once: split pools, a
# virtual service behind nginx, and a ServiceAccount the bundle does not create.
ALL_ASKED = {"platform": "k8s", **SPLIT, **SV_NGINX, **EXISTING_SA}


def _by_name(check):
    return check.__name__


@pytest.mark.parametrize("check, keys", sorted(DECLARING.items(),
                                               key=lambda kv: _by_name(kv[0])),
                         ids=lambda v: v if isinstance(v, tuple) else _by_name(v))
def test_a_check_declares_the_sections_it_reads_as_data(check, keys):
    """The sections a check reads are readable off the check."""
    assert tuple(s.key for s in check.sections) == keys


def test_the_checks_that_declare_nothing_read_nothing():
    """Undeclared has to mean "reads no cluster section", not "not migrated
    yet" -- otherwise the absence of a declaration says nothing at all."""
    for check in FACTS_ONLY:
        assert not hasattr(check, "sections"), check.__name__
        assert not _sections_read(check), check.__name__


def _sections_read(check):
    """Every cluster section a check's source actually reaches for."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(check)))
    return {n.slice.value for n in ast.walk(tree)
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
            and n.value.id == "cluster" and isinstance(n.slice, ast.Constant)}


@pytest.mark.parametrize("check", doctor.CHECKS, ids=_by_name)
def test_every_section_a_check_reads_is_one_it_declares(check):
    """Every cluster[...] a check body reads is a section it declares."""
    declared = tuple(s.key for s in getattr(check, "sections", ()))
    assert _sections_read(check) == set(declared)


@pytest.mark.parametrize("check", doctor.CHECKS, ids=_by_name)
def test_no_check_reaches_a_cluster_section_with_get(check):
    """No check reads a section with cluster.get()."""
    assert "cluster.get(" not in inspect.getsource(check), check.__name__


def test_a_section_missing_from_the_cluster_data_is_refused_by_name():
    """A missing section key raises MissingSection naming the check and key."""
    with pytest.raises(doctor.MissingSection) as e:
        doctor.check_limitrange(FACTS, {}, {})
    assert "check_limitrange" in str(e.value)
    assert "'limitranges'" in str(e.value)
    # ...and it says what the two answers are spelled, because the fix is one of
    # them rather than adding a key with whatever value is to hand.
    assert "None" in str(e.value)


def test_the_declaration_travels_with_the_check_not_with_the_loop():
    """Calling a declared check directly applies its declaration."""
    direct = doctor.check_admission(FACTS, {"platform": "k8s"},
                                    {"namespace": None})
    through = doctor.run_check(doctor.check_admission, FACTS,
                               {"platform": "k8s"}, {"namespace": None})
    assert direct == through
    assert [c.status for c in direct] == [doctor.WARN]


def test_a_declared_checks_body_is_never_handed_an_unread_section():
    """The whole point of the seam: the body cannot mishandle what it never
    receives."""
    @doctor.reads("nodes", "explosive", "the nodes could not be read")
    def check_explosive(facts, opts, cluster):
        raise AssertionError("the body ran on an unread section")

    checks = doctor.run_check(check_explosive, FACTS, {}, {"nodes": None})
    assert [(c.name, c.status) for c in checks] == [("explosive", doctor.WARN)]
    assert checks[0].detail == "the nodes could not be read"


def test_a_declared_check_runs_its_body_when_the_section_is_merely_empty():
    """[] is "we looked and there are none", which is the body's to judge --
    and can be a FAIL. Only None is the seam's."""
    @doctor.reads("nodes", "picky", "the nodes could not be read")
    def check_picky(facts, opts, cluster):
        return [doctor.Check("picky", doctor.FAIL, f"{cluster['nodes']!r}")]

    checks = doctor.run_check(check_picky, FACTS, {}, {"nodes": []})
    assert [(c.status, c.detail) for c in checks] == [(doctor.FAIL, "[]")]


def test_an_unread_sentence_may_be_composed_from_the_facts_and_the_options():
    """An unread sentence can be built from facts and options."""
    @doctor.reads("nodes", "composed",
                  lambda facts, opts: f"slots={facts['slots']} unverified")
    def check_composed(facts, opts, cluster):
        raise AssertionError("the body ran on an unread section")

    c = doctor.run_check(check_composed, FACTS, {}, {"nodes": None})[0]
    assert c.detail == "slots=2 unverified"


def test_a_section_read_only_when_the_question_arises_is_gated_on_that():
    """A `when`-gated section is neither required nor WARNed when the question
    does not arise."""
    assert doctor.check_crane_pool(FACTS, {}, {}) == []
    assert doctor.check_crane_pool(FACTS, {}, {"nodes": None}) == []
    # Split, the same unread nodes are exactly what it cannot answer without.
    c = doctor.check_crane_pool(FACTS, SPLIT, {"nodes": None})[0]
    assert c.status == doctor.WARN and "crane pool" == c.name
    with pytest.raises(doctor.MissingSection):
        doctor.check_crane_pool(FACTS, SPLIT, {})


def test_an_undeclared_check_is_run_unchanged():
    """A check that reads no cluster section is passed straight through."""
    def check_facts_only(facts, opts, cluster):
        return [doctor.Check("old", doctor.WARN, str(facts["slots"]))]

    checks = doctor.run_check(check_facts_only, FACTS, {}, {})
    assert [(c.name, c.detail) for c in checks] == [("old", "2")]


@pytest.mark.parametrize("carrier", ["evidence", "live"])
def test_every_declared_section_is_one_the_cluster_data_actually_carries(
        carrier, monkeypatch):
    """Both producers carry every declared section except probes."""
    if carrier == "evidence":
        carried = set(doctor.cluster_from_evidence(
            {"schema": evidence.SCHEMA}).cluster)
    else:
        monkeypatch.setattr(kube, "kget", lambda *a, **k: {})
        monkeypatch.setattr(kube, "kget_named", lambda *a, **k: {})
        carried = set(doctor.gather_cluster("kubectl", "ns1"))
    for check, keys in DECLARING.items():
        for key in keys:
            assert key in carried or key == "probes", check.__name__


def test_evaluate_derives_engine_limits_from_the_location(monkeypatch):
    """evaluate() resolves unset engine limits from the location's overrides,
    as generate() does."""
    seen = {}
    monkeypatch.setattr(doctor, "run_check",
                        lambda check, facts, opts, cluster: seen.update(opts) or [])
    facts = {**FACTS, "override_cpu": 1, "override_memory": 4096}
    doctor.evaluate(facts, {}, "blazemeter", cluster_data=UNREAD_ALL, probes={})
    assert seen["engine_cpu_limit"] == "1"
    assert seen["engine_mem_limit"] == "4Gi"
    # An explicit option still wins -- a replayed profile speaks through opts.
    seen.clear()
    doctor.evaluate(facts, {"engine_cpu_limit": "2", "engine_mem_limit": "8Gi"},
                    "blazemeter", cluster_data=UNREAD_ALL, probes={})
    assert seen["engine_cpu_limit"] == "2"
    assert seen["engine_mem_limit"] == "8Gi"


def test_evaluate_carries_every_section_every_check_declares(monkeypatch):
    """evaluate() supplies every declared section, including probes."""
    passed = {}
    monkeypatch.setattr(doctor, "run_check",
                        lambda check, facts, opts, cluster: passed.update(cluster) or [])
    doctor.evaluate(FACTS, {}, "blazemeter", cluster_data=UNREAD_ALL, probes={})
    for check, keys in DECLARING.items():
        for key in keys:
            assert key in passed, check.__name__


def test_evaluate_opens_the_unread_branch_for_every_declared_check():
    """All-null cluster data gives each declared check its own WARN and no
    FAIL."""
    checks = doctor.evaluate(FACTS, ALL_ASKED, "blazemeter",
                             cluster_data=UNREAD_ALL, probes={})
    for check in DECLARING:
        for section in check.sections:
            if section.unread is None:
                continue                  # the verdict is somebody else's
            detail = (section.unread(FACTS, ALL_ASKED)
                      if callable(section.unread) else section.unread)
            c = _find(checks, section.name)
            assert c.status == doctor.WARN
            assert c.detail == detail
    assert not doctor.has_failures(checks)


# -- what a check leaves to an earlier one -----------------------------------

def test_a_check_that_goes_quiet_declares_what_it_is_leaving_to():
    """Checks that return [] for an earlier verdict declare that check."""
    assert doctor.check_threads_per_engine.defers == (doctor.check_location,)
    assert doctor.check_engine_packing.defers == (doctor.check_capacity,)


def test_the_order_of_checks_meets_every_declared_deference():
    """_ordered() refuses an order where a check precedes one it defers to."""
    seen = []
    for check in doctor.CHECKS:
        for owner in getattr(check, "defers", ()):
            assert owner in seen, check.__name__
        seen.append(check)


def test_an_order_that_breaks_a_deference_is_refused():
    """The tuple cannot be reshuffled quietly. Same list, two checks swapped
    past the one they defer to."""
    shuffled = [c for c in doctor.CHECKS if c is not doctor.check_capacity]
    shuffled.append(doctor.check_capacity)
    with pytest.raises(RuntimeError) as e:
        doctor._ordered(shuffled)
    assert "check_engine_packing" in str(e.value)
    assert "check_capacity" in str(e.value)


def test_a_wholly_unread_cluster_warns_and_exits_zero():
    """A denied read is never a failure: nothing here can stand behind a
    verdict about a cluster it was not allowed to look at."""
    checks = doctor.evaluate(FACTS, {"platform": "k8s"}, "blazemeter",
                             cluster_data=UNREAD_ALL, probes={})
    assert not doctor.has_failures(checks)
    assert doctor.FAIL not in _statuses(checks)


def test_a_wholly_empty_cluster_can_still_fail():
    """The other half of the same distinction, and the reason the seam cannot
    simply treat a falsy section as unread."""
    checks = doctor.evaluate(FACTS, {**SV_NGINX, "platform": "k8s"},
                             "blazemeter", cluster_data=EMPTY_ALL,
                             probes={doctor.API_PROBE_URL: 7})
    assert doctor.has_failures(checks)


# Every check with an unread sentence of its own, less check_egress (probes are
# not cluster data) and check_service_account (empty and unread deliberately
# share a sentence).
SAYS_WHEN_IT_DID_NOT_LOOK = tuple(
    c for c in DECLARING
    if c not in (doctor.check_egress, doctor.check_service_account))


@pytest.mark.parametrize("check", SAYS_WHEN_IT_DID_NOT_LOOK, ids=_by_name)
def test_a_declared_check_says_something_different_when_it_did_look(check):
    """Unread and empty reach the reader as different sentences, not just as
    different statuses."""
    unread = doctor.run_check(check, FACTS, ALL_ASKED, UNREAD_ALL)
    looked = doctor.run_check(check, FACTS, ALL_ASKED, EMPTY_ALL)
    assert [c.detail for c in unread] != [c.detail for c in looked]
    assert all(c.status == doctor.WARN for c in unread)


# -- run() ------------------------------------------------------------------

HEALTHY = {"nodes": [_big("a"), _big("b")], "limitranges": [LR_MATCHING],
           "quotas": [], "namespace": NS_BASELINE}


def test_run_healthy_cluster_has_no_failures(capsys):
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter",
                        cluster_data=HEALTHY, probes={doctor.API_PROBE_URL: 0})
    assert not doctor.has_failures(checks)
    assert doctor.FAIL not in _statuses(checks)
    out = capsys.readouterr().out
    assert "PASS" in out and "location slots" in out


def test_run_broken_cluster_fails(capsys):
    broken = {"nodes": [_node("n1"), _node("n2")],
              "limitranges": [],
              "quotas": [_quota(hard={"pods": "1"}, used={"pods": "0"})],
              "namespace": {"metadata": {"labels":
                            {"pod-security.kubernetes.io/enforce": "restricted"}}}}
    checks = doctor.run({**FACTS, "threads_per_engine": None},
                        {"platform": "k8s"}, "blazemeter",
                        cluster_data=broken, probes={doctor.API_PROBE_URL: 28})
    assert doctor.has_failures(checks)
    out = capsys.readouterr().out
    for marker in ("threadsPerEngine", "per-node", "pods", "admission", "egress"):
        assert marker in out
    assert "FAIL" in out


def test_run_gathers_when_nothing_is_injected(monkeypatch):
    called = {}

    def fake_gather(cli, ns):
        called["gather"] = (cli, ns)
        return HEALTHY

    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(doctor, "gather_cluster", fake_gather)
    monkeypatch.setattr(doctor, "probe_egress",
                        lambda cli, ns, opts: {doctor.API_PROBE_URL: 0})
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter")
    assert called["gather"] == ("kubectl", "blazemeter")
    assert not doctor.has_failures(checks)


# -- the verdict list in one sentence ----------------------------------------
#
# Printed under doctor's report and shown by the web UI beside an imported file.

def _checks(**counts):
    return [doctor.Check(f"{status} {i}", status, "")
            for status, n in counts.items() for i in range(n)]


def test_the_summary_counts_every_status_including_the_empty_ones():
    line = doctor.summary_line(_checks(PASS=3, WARN=2))
    assert "3 passed" in line and "2 warnings" in line and "no failures" in line


def test_a_count_of_one_is_not_pluralised():
    assert "1 warning," in doctor.summary_line(_checks(PASS=0, WARN=1))
    assert "1 failure" in doctor.summary_line(_checks(FAIL=1))


def test_the_consequence_is_stated_only_where_something_failed():
    """The summary names the consequence only when something FAILed."""
    assert doctor.NO_TEST_WOULD_START not in doctor.summary_line(_checks(WARN=4))
    assert doctor.NO_TEST_WOULD_START in doctor.summary_line(_checks(WARN=4, FAIL=1))


def test_the_report_prints_the_sentence_rather_than_a_second_one(capsys):
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter",
                        cluster_data=HEALTHY, probes={doctor.API_PROBE_URL: 0})
    assert doctor.summary_line(checks) in capsys.readouterr().out
