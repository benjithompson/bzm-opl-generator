"""Offline tests for the pre-flight doctor.

Every check is a pure verdict over already-fetched cluster JSON, so the whole
file runs with no cluster and no network: the fixtures below are what
`kubectl get nodes/limitrange/resourcequota/ns -o json` actually returns.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import doctor, facts as facts_mod  # noqa: E402


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
         "slots": 2, "threads_per_engine": 500}

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
    """The flagship path: harbor id, ship id and a token typed in, no account
    to read. Neither value could have been supplied and nothing is
    misconfigured, so neither is a failure."""
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
    """Unknown is `None` on manually-entered facts, and only that. A typed 0 is
    a value the customer did supply, and zero slots is the case BlazeMeter has
    nowhere to place a run -- exempting it would hide a real misconfiguration
    behind the marker."""
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
    """THE regression this model exists to fix. 500 threads on a 4096MB heap in
    an 8Gi container is the configuration BlazeMeter documents and ships, and
    the previous fixed-ratio check WARNed on it -- 4096 is exactly half of 8Gi.
    A preflight that flags the vendor's own default teaches people to ignore
    it."""
    c = _find(doctor.check_engine_heap(_heap_facts(4096), {"engine_mem_limit": "8Gi"},
                                       {}), "engine heap")
    assert c.status == doctor.PASS


def test_engine_heap_fails_when_the_heap_cannot_carry_the_threads():
    """The 1000-thread case, live on 24 locations in one real account: they
    declare double the documented threads and almost all still carry the
    default 4096MB heap, so they OOM partway up the ramp."""
    facts = dict(FACTS, engine_xmx_mb=4096, threads_per_engine=1000)
    c = _find(doctor.check_engine_heap(facts, {"engine_mem_limit": "8Gi"}, {}),
              "engine heap")
    assert c.status == doctor.FAIL
    assert "8192MB" in c.detail          # what 1000 threads actually need


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
    """threadsPerEngine unset is check_location's FAIL, not this one's. Saying
    the heap fits the load when there is no load to compare it to would be a
    verdict on a comparison that never happened."""
    facts = dict(FACTS, engine_xmx_mb=4096, threads_per_engine=None)
    c = _find(doctor.check_engine_heap(facts, {"engine_mem_limit": "8Gi"}, {}),
              "engine heap")
    assert c.status == doctor.WARN
    assert "unverified" in c.detail


def test_the_model_reproduces_the_documented_point_exactly():
    """A compatibility anchor, not a correctness one.

    It proves the model never contradicts what BlazeMeter tells people to run.
    It proves nothing about those numbers being right: 2 CPU / 8Gi is a floor
    chosen so things work consistently everywhere, not a measurement, so this
    pins a safety margin of unknown size rather than a requirement. Getting
    below it needs observed usage (#89), and until then every value the model
    produces is a defensible upper bound."""
    assert doctor.engine_heap_mb(500) == 4096
    assert doctor.engine_container_mb(4096) == 8192


def test_the_floor_is_the_measured_one_not_a_consumption_reading():
    """3072MB is the smallest container limit measured to survive a whole run,
    at BOTH 50 and 300 threads. The previous 1536 came from an engine
    *consuming* 1220MB, and it fails at both -- consumption is not a
    requirement. Pinned because that conflation has now been made three times."""
    assert doctor.MIN_CONTAINER_MB == 3072


def test_the_model_never_recommends_below_the_measured_floor():
    """The property that matters, across the whole range anyone runs. At 50
    threads the unfloored arithmetic gives 818MB, which died partway through a
    real run; the floor is what stops the tool recommending it."""
    for threads in (1, 10, 50, 100, 250, 300):
        got = doctor.engine_container_mb(doctor.engine_heap_mb(threads))
        assert got >= 3072, f"{threads} threads -> {got}MB, under the measured floor"


def test_the_low_thread_floor_keeps_the_container_startable():
    """Below ~50 threads the ratio alone produces a container no JVM starts in
    (10 threads -> 164MB). The floor covers that edge; at 50 threads, the common
    low value, it is not needed."""
    assert doctor.engine_container_mb(doctor.engine_heap_mb(10)) == doctor.MIN_CONTAINER_MB
    assert doctor.engine_heap_mb(50) > doctor.MIN_HEAP_MB


def test_unknown_heap_is_a_warn_not_a_pass():
    """4096 is the default on almost every location, so assuming it would pass
    the check nearly always -- and the location somebody is generating a bundle
    for is exactly the one that has been retuned."""
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
# Measured on a live GKE run: a location at overrideCPU=1 / overrideMemory=4096
# against a bundle asking for 2 CPU / 8Gi produced ONE pod carrying
# requests {cpu: 1, memory: 4Gi} and limits {cpu: 2, memory: 8Gi}. The two
# settings are not rivals for one field -- the bundle sets limits, the location
# sets requests. 250m/256Mi is only what an unset location defaults to.

def test_engine_requests_come_from_the_location_when_set():
    from bzm_opl_gen import generate as gen
    assert gen.engine_requests({"override_cpu": 1, "override_memory": 4096}) \
        == ("1", "4096Mi")


def test_engine_requests_fall_back_to_cranes_default():
    from bzm_opl_gen import generate as gen
    assert gen.engine_requests({}) == (gen.ENGINE_DEFAULT_REQUEST_CPU,
                                       gen.ENGINE_DEFAULT_REQUEST_MEM)


def test_packing_uses_the_locations_requests_not_the_default():
    """A location whose overrides match the engine limits packs correctly, and
    the check has to see that -- judging it by the 250m default would WARN on
    the very configuration that fixes the problem."""
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
    """The old advice was maxPods, which is a backstop. The direct fix is the
    location's overrides, and the verdict has to say so."""
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    node = _engine_node("e1", cpu="16", mem="64Gi", pods=110)
    c = _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [node]}),
              "engine packing")
    assert c.status == doctor.WARN
    assert "overrideCPU/overrideMemory" in c.detail
    assert "8192MB" in c.detail          # the value to set, in the field's unit


# -- check_crane_pool -------------------------------------------------------
#
# Split pools left crane unchecked: every other capacity check here is about
# engines. Numbers below are a real GKE e2-medium.

def _e2_medium(name="c1"):
    """940m allocatable CPU, ~2.73Gi memory -- measured, and the point is that
    940m is *below* crane's 1 CPU limit while being above its 250m request."""
    return _node(name, cpu="940m", mem="2866848Ki", labels=CRANE_POOL)


def test_crane_pool_warns_when_the_node_cannot_reach_cranes_limit():
    """The obvious "small always-on node" cannot actually run crane at its
    limit. It schedules on the request and is throttled when a run makes it
    busy -- and an agent that stops heartbeating mid-run reads as a test that
    stopped, which is the hardest failure here to attribute."""
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
    """A dedicated engine pool is *supposed* to sit at zero between runs -- that
    is the saving the split exists for. Observed on a correctly-built GKE pool
    at min-nodes 0, where FAIL claimed "engines have nowhere to run" about a
    cluster that was right. An empty autoscaling pool and a pool that was never
    created look identical in `get nodes`, and they are opposite verdicts."""
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
    from bzm_opl_gen.generate import crane_scheduling
    assert [n["metadata"]["name"] for n in
            doctor.eligible_nodes(nodes, SPLIT, crane_scheduling(SPLIT))] == ["c1"]


def test_capacity_does_not_spend_crane_out_of_a_pool_it_is_not_on():
    """Charging the engine pool for crane understates it by a whole crane. On a
    small dedicated pool that is the difference between PASS and a FAIL that
    sends someone resizing nodes they did not need to touch."""
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
# The check that exists because of the one thing the manifests cannot express:
# crane stamps engine requests at 250m/256Mi whatever the limits say, and the
# scheduler places on requests.

def test_engine_packing_warns_when_requests_let_engines_pile_onto_one_node():
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    # 16 CPU / 64Gi runs 8 engines but *accepts* 64 by requests.
    checks = doctor.check_engine_packing(FACTS, opts, {"nodes": [_engine_node("e1")]})
    c = _find(checks, "engine packing")
    assert c.status == doctor.WARN
    assert "250m" in c.detail and "maxPods" in c.detail
    # Never a FAIL: the engines do start, and the cost is the validity of the
    # numbers rather than the run.
    assert c.status != doctor.FAIL


def test_engine_packing_passes_when_maxpods_caps_the_node():
    """The lever that actually closes it, as the node reports it. A pool capped
    at the system pods a node of it actually runs, plus one, takes exactly one
    engine however little that engine asked for."""
    from bzm_opl_gen import generate as gen
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    # Derived, not the literal it used to be: the ceiling that admits exactly
    # one engine is a function of how many system pods land on the node, and a
    # test that hardcodes the sum silently stops testing the property when that
    # number is corrected -- which is exactly what happened when measurement
    # moved it from 8 to 6.
    caps_at_one = gen.TYPICAL_SYSTEM_PODS + 1
    checks = doctor.check_engine_packing(
        FACTS, opts, {"nodes": [_engine_node("e1", pods=caps_at_one)]})
    assert _find(checks, "engine packing").status == doctor.PASS


def test_engine_packing_allows_the_engines_the_pool_was_designed_for():
    """A node sized for 2 engines is not over-packed by taking 2. Judging it
    against raw capacity would WARN on a pool built exactly to spec -- and on
    GKE, whose maxPods floor of 8 forces 2 engines a node, that verdict would
    fire on every correctly-built pool there is."""
    from bzm_opl_gen import generate as gen
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi",
                engines_per_node=2)
    node = _engine_node("e1", cpu="8", mem="32Gi",
                        pods=gen.TYPICAL_SYSTEM_PODS + 2)
    assert _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [node]}),
                 "engine packing").status == doctor.PASS
    # ...and one designed for 2 but ceilinged for 4 is still over-packed.
    loose = _engine_node("e2", cpu="8", mem="32Gi",
                         pods=gen.TYPICAL_SYSTEM_PODS + 4)
    assert _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [loose]}),
                 "engine packing").status == doctor.WARN


def test_the_recipe_builds_a_pool_the_checker_passes():
    """The generated nodepools.md and this check must agree: a pool built to
    the recipe's maxPods, on a node sized as the recipe says, has to come back
    PASS. They share TYPICAL_SYSTEM_PODS precisely so the advice and the
    verdict cannot drift into contradicting each other."""
    from bzm_opl_gen import generate as gen
    opts = dict(SPLIT, engine_cpu_limit="2", engine_mem_limit="8Gi")
    recipe_max_pods = gen.TYPICAL_SYSTEM_PODS + 1
    # The recipe's node: one engine's limits plus the kubelet's reservations.
    node = _engine_node("e1", cpu="3", mem="10Gi", pods=recipe_max_pods)
    c = _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [node]}),
              "engine packing")
    assert c.status == doctor.PASS
    # ...and the same node without the ceiling is exactly what it warns about.
    loose = _engine_node("e2", cpu="3", mem="10Gi", pods=110)
    assert _find(doctor.check_engine_packing(FACTS, opts, {"nodes": [loose]}),
                 "engine packing").status == doctor.WARN


def test_engine_packing_is_silent_when_no_node_is_eligible():
    """check_capacity already FAILs on an empty eligible set; a second verdict
    saying the same thing is noise, and this one would have nothing to measure."""
    cluster = {"nodes": [_node("c1", labels=CRANE_POOL)]}
    assert doctor.check_engine_packing(FACTS, SPLIT, cluster) == []


def test_engine_packing_warns_rather_than_guesses_when_nodes_are_unread():
    """A denied `list nodes` is not an uncapped pool. Unreadable and empty must
    not share a verdict."""
    checks = doctor.check_engine_packing(FACTS, SPLIT, {"nodes": None})
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

def test_limitrange_absent_warns_about_cranes_defaults():
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": []})[0]
    assert c.status == doctor.WARN
    assert "250m" in c.detail and "256Mi" in c.detail


def test_limitrange_matching_passes():
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [LR_MATCHING]})) == {doctor.PASS}


def test_limitrange_max_below_engine_fails():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "2Gi"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "team-caps" in c.detail                      # name the object


def test_limitrange_min_above_the_stamped_request_fails():
    """min rejects from below exactly as max does from above, and it is measured
    against what crane actually requests (250m/256Mi), not the engine's limits --
    a namespace that insists on 1 CPU minimum rejects every engine pod."""
    lr = {"metadata": {"name": "floor"},
          "spec": {"limits": [{"type": "Container", "min": {"cpu": "1", "memory": "1Gi"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "min cpu" in c.detail and "250m" in c.detail


def test_limitrange_ratio_tighter_than_the_engines_own_gap_fails():
    """The engine limits 8Gi while requesting 256Mi -- a 32x ratio. Any
    maxLimitRequestRatio below that rejects it."""
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "4", "memory": "4"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "maxLimitRequestRatio" in c.detail


def test_limitrange_ratio_wide_enough_passes():
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "16", "memory": "64"}}]}}
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})) == {doctor.PASS}


def test_limitrange_absent_does_not_promise_a_fix_it_cannot_deliver():
    """Crane stamps the engine's requests explicitly, so no LimitRange can raise
    them -- the WARN must not tell a customer that emitting one would."""
    detail = doctor.check_limitrange(FACTS, {}, {"limitranges": []})[0].detail
    assert "cannot override" in detail


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
    assert _statuses(doctor.check_resourcequota(FACTS, {}, {"quotas": []})) == {doctor.PASS}


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
    """Used to be a FAIL, and correctly so: the engine security envs were
    emitted only for platform=openshift, so restricted PSA rejected the engine
    pods after crane was online. They are on by default everywhere now."""
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
    """Two different facts, and the advice for one is wrong for the other. `{}`
    is the live path's "asked, it is not there" -- the normal preflight case,
    answered by creating it. `None` is "nobody looked", which an evidence file
    says when the collector was refused the namespace; telling that reader to
    create the namespace sends them after something that is not missing.
    """
    absent = doctor.check_admission(FACTS, {"platform": "k8s"}, {"namespace": {}})[0]
    unread = doctor.check_admission(FACTS, {"platform": "k8s"},
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
    """Every namespace that exists has `default`, so an empty list means we
    could not look -- which is not evidence the account is missing."""
    c = doctor.check_service_account(FACTS, EXISTING_SA, {"serviceaccounts": []})[0]
    assert c.status == doctor.WARN
    c2 = doctor.check_service_account(FACTS, EXISTING_SA, {})[0]
    assert c2.status == doctor.WARN


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
    """`none` is a value, so the unrecognised-value WARN below would claim it
    otherwise -- telling someone who deliberately took the SV path off that
    their ingress path is unverified, about an ingress there is not."""
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
    """istio routes through a Gateway/VirtualService, contour through an
    HTTPProxy, openshift through a Route; none creates an Ingress, and none of
    those controllers registers an IngressClass -- so failing on 'none found'
    would fail every correct install of all three."""
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


@pytest.mark.parametrize("cluster", [{}, {"ingressclasses": None}])
def test_ingress_class_unreadable_warns_rather_than_fails(cluster):
    """cluster_data from an older caller has no such key -- 'we could not look',
    not 'the class is missing'."""
    checks = doctor.check_ingress_class(FACTS, SV_NGINX, cluster)
    assert _statuses(checks) == {doctor.WARN}


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
    monkeypatch.setattr(doctor.livetest, "kget",
                        lambda cli, ns, kind, name=None: {"x": 1} if deployed else {})
    seen = []
    monkeypatch.setattr(doctor.livetest, "_crane_exec",
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
    """A pod's first DNS lookup can fail before CoreDNS answers for it -- seen
    live, two of three hosts returning rc=6 and all three passing on a rerun.
    A doctor that FAILs on that is reporting something it cannot reproduce."""
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
    """LimitRanges, ResourceQuotas and ServiceAccounts come back from a single
    `get` -- one API round trip instead of three -- so the shape has to be split
    by kind."""
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

    monkeypatch.setattr(doctor.livetest, "kget", fake_kget)
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
    monkeypatch.setattr(doctor.livetest, "kget", lambda *a, **k: {})
    data = doctor.gather_cluster("kubectl", "ns1")
    # Every list is None rather than []: kget reports a failed command as {},
    # and "could not ask" has to stay distinguishable from "asked, none exist"
    # -- an [] here would fail the capacity check of anyone who is merely not
    # allowed to list nodes. The namespace stays {}, which is what
    # check_admission already reads as "not created yet".
    assert data == {"nodes": None, "ingressclasses": None, "limitranges": None,
                    "quotas": None, "serviceaccounts": None, "namespace": {}}


@pytest.mark.parametrize("served,expected,status", [
    ({"items": []}, [], doctor.FAIL),   # asked, cluster has none -> nothing claims it
    ({}, None, doctor.WARN),            # kget's failure shape -> we did not look
])
def test_gather_cluster_keeps_unreadable_ingressclasses_apart_from_empty(
        monkeypatch, served, expected, status):
    """The two answers reach check_ingress_class as [] and None and it grades
    them differently. Collapsing both to [] -- which `.get("items", [])` does --
    turns a cluster whose API server does not serve IngressClass into a hard
    FAIL with a non-zero exit, for something never actually checked."""
    monkeypatch.setattr(doctor.livetest, "kget",
                        lambda cli, ns, kind, name=None:
                        served if kind == "ingressclass" else {})
    data = doctor.gather_cluster("kubectl", "ns1")
    assert data["ingressclasses"] == expected
    assert _statuses(doctor.check_ingress_class(FACTS, SV_NGINX, data)) == {status}


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

    monkeypatch.setattr(doctor.livetest, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(doctor, "gather_cluster", fake_gather)
    monkeypatch.setattr(doctor, "probe_egress",
                        lambda cli, ns, opts: {doctor.API_PROBE_URL: 0})
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter")
    assert called["gather"] == ("kubectl", "blazemeter")
    assert not doctor.has_failures(checks)
