"""Offline tests for the capacity planner.

The planner is the one module here that reaches nothing at all -- no account,
no cluster, no options file -- so these are pure arithmetic and pure prose.
What they mostly defend is the honesty of the document: an infrastructure
request is acted on by somebody who cannot check it, so the assumption behind
the node count has to survive every refactor that touches the wording.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import doctor, plan  # noqa: E402


# -- the arithmetic ---------------------------------------------------------

def test_engines_are_the_target_over_threads_rounded_up():
    assert plan.capacity_plan(5000)["engines"] == 10
    # 4,501 users does not fit in nine engines, and a plan that floors it is a
    # plan that runs out of capacity at the top of the ramp.
    assert plan.capacity_plan(4501)["engines"] == 10
    assert plan.capacity_plan(1)["engines"] == 1


def test_slots_is_engines_per_agent_not_the_whole_run():
    """BlazeMeter's `slots` is "Engines per agent" in its own UI -- "the number
    of engines/tests that can run on one agent" -- so a location's concurrency
    is agents x slots.

    This was wrong here first: `slots` was set to the whole engine count, which
    on a two-agent location is twice the run and twice the cluster. Real
    accounts lean on the multiplication -- one has 17 agents at slots=1.
    """
    one = plan.capacity_plan(5000)
    assert one["engines"] == 10
    assert one["location"]["slots"] == 10        # one agent: the same number

    four = plan.capacity_plan(5000, agents=4)
    assert four["engines"] == 10                 # the run has not changed
    assert four["location"]["slots"] == 3        # 10 over 4, rounded up
    assert four["engines_per_agent"] == 3


def test_engines_per_agent_rounds_up_so_the_target_is_reachable():
    """3 agents x 3 engines is 9, and the run needs 10. Rounding up gives 12
    available for 10 used; rounding down gives a test that cannot start."""
    p = plan.capacity_plan(5000, agents=3)
    assert p["engines_per_agent"] == 4
    assert p["engines_per_agent"] * p["agents"] >= p["engines"]


def test_nodes_are_per_agent_because_an_agent_is_a_cluster():
    """The infrastructure request is for one cluster. Sizing it from the
    location's total would build every agent's share into each of them."""
    p = plan.capacity_plan(5000, agents=2)
    assert p["engines_per_agent"] == 5
    assert p["nodes_per_agent"] == 5
    assert p["nodes"] == 10


def test_more_agents_do_not_change_the_total_nodes_needed():
    """The work is the same; it is spread. What changes is how much of it any
    one cluster has to hold."""
    for agents in (1, 2, 5):
        p = plan.capacity_plan(5000, agents=agents)
        assert p["nodes"] >= p["engines"], agents
        assert p["nodes_per_agent"] * agents == p["nodes"]


def test_nodes_divide_by_engines_per_node_and_round_up():
    assert plan.capacity_plan(5000)["nodes"] == 10
    assert plan.capacity_plan(5000, engines_per_node=4)["nodes"] == 3
    assert plan.capacity_plan(5000, engines_per_node=10)["nodes"] == 1


def test_node_is_sized_for_the_engines_plus_kubelet_overhead():
    """Capacity, not allocatable: the machine somebody buys has to hold the
    engines *and* what the node spends on itself."""
    p = plan.capacity_plan(1000, engines_per_node=2)
    assert p["node"]["cpu_millis"] == 2 * 2000 + 1000      # 2 engines + overhead
    assert p["node"]["memory_bytes"] == 2 * 8 * 1024 ** 3 + 2 * 1024 ** 3
    assert p["node"]["cpu"] == "5"
    assert p["node"]["memory"] == "18Gi"


def test_peak_is_the_pool_at_full_width():
    p = plan.capacity_plan(5000)
    assert p["peak"]["cpu_millis"] == p["node"]["cpu_millis"] * p["nodes"]
    assert p["peak"]["disk_gb"] == 60 * p["engines"]


def test_engine_size_is_taken_from_the_bundle_options():
    p = plan.capacity_plan(1000, vus_per_engine=250,
                           engine_cpu="4", engine_mem="16Gi")
    assert p["engine"]["cpu"] == "4"
    assert p["engine"]["memory"] == "16Gi"
    assert p["engines"] == 4
    assert p["location"]["override_memory_mb"] == 16384


def test_override_memory_is_megabytes():
    """BlazeMeter's overrideMemory field is in MB, and a plan that reported it
    in bytes or GiB would be pasted into the UI as-is."""
    assert plan.capacity_plan(500)["location"]["override_memory_mb"] == 8192


# -- supported threads, shared with doctor ----------------------------------

def test_supported_vus_scales_on_the_tighter_dimension():
    assert plan.supported_vus(2000, 8 * 1024 ** 3) == 500       # the baseline
    assert plan.supported_vus(1000, 4 * 1024 ** 3) == 250       # half of both
    # Twice the memory and the same CPU is not twice the engine.
    assert plan.supported_vus(2000, 16 * 1024 ** 3) == 500
    assert plan.supported_vus(1000, 16 * 1024 ** 3) == 250


def test_supported_vus_never_reaches_zero():
    """A tiny engine carries few threads, not none -- zero would divide the
    plan by nothing and read as 'this engine cannot run'."""
    assert plan.supported_vus(1, 1) == 1


def test_doctor_judges_against_the_same_ratio_the_planner_sizes_from():
    """The planner and the preflight must not disagree: a plan doctor then
    WARNs about is worse than either alone. This is the one assertion that
    would catch the ratio being restated in one of them."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    supported = plan.supported_vus(1000, 4 * 1024 ** 3)
    checks = doctor.check_threads_per_engine(
        {"threads_per_engine": supported}, opts, {})
    assert [c.status for c in checks] == ["PASS"]
    over = doctor.check_threads_per_engine(
        {"threads_per_engine": supported + 1}, opts, {})
    assert [c.status for c in over] == ["WARN"]
    assert str(supported) in over[0].detail


# -- what the plan refuses ---------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"users": 0},
    {"users": 100, "agents": 0},
    {"users": -5},
    {"users": 100, "vus_per_engine": 0},
    {"users": 100, "engines_per_node": 0},
    {"users": "many"},
    {"users": 100, "engines_per_node": 2.5},
])
def test_refuses_what_cannot_be_a_plan(kwargs):
    with pytest.raises(ValueError):
        plan.capacity_plan(**kwargs)


def test_refusal_names_the_field():
    with pytest.raises(ValueError, match="vus_per_engine"):
        plan.capacity_plan(100, vus_per_engine=0)


def test_a_bad_engine_size_is_refused_not_defaulted():
    """Sizing a cluster from a default the customer did not ask for is worse
    than saying the value did not parse."""
    with pytest.raises(ValueError, match="engine_mem_limit"):
        plan.capacity_plan(100, engine_mem="8 gigs")


def test_string_counts_from_a_form_are_accepted():
    """These arrive from a browser form and an argparse int alike."""
    assert plan.capacity_plan("5000")["engines"] == 10


# -- the assumption ----------------------------------------------------------

def test_default_threads_are_marked_as_assumed():
    p = plan.capacity_plan(5000)
    assert p["vus_per_engine"] == 500
    assert p["vus_per_engine_assumed"] is True


def test_the_assumed_figure_follows_the_engine_size():
    """500 is BlazeMeter's number for *its* engine, not a constant. Carried onto
    another size it is wrong in both directions -- and the small case is the
    worse one, because the planner then warns about the figure it chose
    itself."""
    small = plan.capacity_plan(10000, engine_cpu="1", engine_mem="4Gi")
    standard = plan.capacity_plan(10000)
    large = plan.capacity_plan(10000, engine_cpu="4", engine_mem="16Gi")
    assert [p["vus_per_engine"] for p in (small, standard, large)] \
        == [250, 500, 1000]
    assert [p["engines"] for p in (small, standard, large)] == [40, 20, 10]
    assert all(p["vus_per_engine_assumed"] for p in (small, standard, large))


def test_an_assumed_figure_never_warns_about_itself():
    """The over-threading warning is about a *supplied* figure the engine cannot
    carry. Firing it against the planner's own default made the tool look broken
    on the small engine preset."""
    for cpu, mem in (("1", "4Gi"), ("2", "8Gi"), ("4", "16Gi")):
        p = plan.capacity_plan(10000, engine_cpu=cpu, engine_mem=mem)
        assert not any("throttle" in w for w in p["warnings"]), (cpu, mem)


def test_a_supplied_figure_is_still_judged_against_the_engine():
    p = plan.capacity_plan(10000, vus_per_engine=500,
                           engine_cpu="1", engine_mem="4Gi")
    assert p["vus_per_engine"] == 500
    assert any("throttle" in w for w in p["warnings"])


def test_the_document_says_where_a_scaled_figure_came_from():
    """A reader who knows BlazeMeter's 500 has to be able to see why this plan
    says something else."""
    doc = plan.plan_document(plan.capacity_plan(10000, engine_cpu="4",
                                                engine_mem="16Gi"))
    assert "1,000 virtual users per engine" in doc
    assert "500 is BlazeMeter's figure for a" in doc
    assert "twice that size" in doc


def test_a_supplied_figure_is_not_marked_assumed():
    p = plan.capacity_plan(5000, vus_per_engine=500)
    assert p["vus_per_engine"] == 500
    assert p["vus_per_engine_assumed"] is False


def test_document_says_the_vus_per_engine_figure_is_an_assumption():
    """The whole document multiplies by this number and nothing here can
    verify it. A reader who takes the node count and never learns that is the
    failure this planner has to avoid."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "not a\nmeasurement of our test" in doc
    assert "confirm" in doc.lower()
    assert "one real run" in doc


def test_document_does_not_claim_a_supplied_figure_is_blazemeter_s():
    doc = plan.plan_document(plan.capacity_plan(5000, vus_per_engine=120))
    assert "supplied rather than measured" in doc
    assert "is rated for" not in doc


# -- the document ------------------------------------------------------------

def test_document_leads_with_what_to_provision():
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "# Infrastructure request" in doc
    assert "**10** × 3 vCPU / 10Gi RAM" in doc
    # The reader is not a BlazeMeter user; the ask has to be in their units.
    assert "autoscale from zero" in doc
    assert "Inbound network | none" in doc


def test_document_names_the_egress_hosts_a_firewall_rule_needs():
    doc = plan.plan_document(plan.capacity_plan(1000))
    for host in ("a.blazemeter.com", "data.blazemeter.com",
                 "storage.blazemeter.com"):
        assert host in doc


def test_document_shows_the_arithmetic():
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "5,000 / 500, rounded up" in doc


def test_document_says_nothing_about_what_is_being_tested():
    """The request is for capacity to run load tests from this cluster. Naming
    an application invites the reply that it should be sized per application,
    which is a conversation about the test plan rather than about nodes."""
    doc = plan.plan_document(plan.capacity_plan(100))
    assert doc.splitlines()[0] == "# Infrastructure request: load testing"


def test_document_states_the_override_fields_and_why():
    """The two fields that decide whether the cluster in this request is the
    cluster that gets used."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "overrideCPU" in doc and "overrideMemory" in doc
    assert "250m" in doc and "256Mi" in doc
    assert "scheduler places pods on requests" in doc


def test_document_warns_when_the_engine_is_too_small_for_the_threads():
    p = plan.capacity_plan(5000, vus_per_engine=500,
                           engine_cpu="1", engine_mem="4Gi")
    assert p["warnings"]
    assert "throttle" in " ".join(p["warnings"])
    assert "Worth knowing" in plan.plan_document(p)


def test_no_over_threading_warning_when_the_engine_is_big_enough():
    p = plan.capacity_plan(5000, vus_per_engine=500)
    assert not any("throttle" in w for w in p["warnings"])


def test_sharing_a_node_is_warned_about_but_allowed():
    p = plan.capacity_plan(5000, engines_per_node=4)
    assert p["nodes"] == 3
    assert any("contend" in w for w in p["warnings"])


def test_warnings_are_prose_in_both_places_they_are_shown():
    """They go into the Markdown document and into the web panel, which renders
    neither backticks nor `--`. One wording has to read correctly in both."""
    plans = [plan.capacity_plan(5000, vus_per_engine=500,
                                engine_cpu="1", engine_mem="4Gi"),
             plan.capacity_plan(5000, engines_per_node=4),
             plan.capacity_plan(5000)]
    for w in [w for p in plans for w in p["warnings"]]:
        assert "`" not in w, w
        assert "--" not in w, w


def test_document_says_the_blazemeter_side_does_not_wait_for_the_cluster():
    """A location and its agent are records in BlazeMeter, not things running on
    the cluster: both can be created while the infrastructure request is still
    being read, and an agent that has never sent a heartbeat is the expected
    state before a deployment rather than a fault. Saying so is the difference
    between the wait being dead time and being setup time."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "None of that waits for the cluster" in doc
    assert "never" in doc and "heartbeat" in doc


def test_document_shows_the_division_across_agents():
    """A reader who knows the run needs 20 engines has to be able to see why
    the location is being set to 7."""
    doc = plan.plan_document(plan.capacity_plan(10000, vus_per_engine=500,
                                                agents=3))
    assert "3 agent(s) to run them" in doc
    assert "7 engines per agent" in doc
    assert "20 / 3, rounded up" in doc
    assert "each of 3 clusters" in doc


def test_document_does_not_mention_agents_when_there_is_one():
    """The common case stays the simple case."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "clusters" not in doc
    assert "the location's `slots`" in doc
