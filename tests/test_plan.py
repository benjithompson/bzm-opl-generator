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


def test_slots_is_the_engine_count():
    """The location field and the plan's engine count are the same number --
    they are separate keys because one is advice about BlazeMeter and one is
    the input to the node arithmetic, not because they can differ."""
    p = plan.capacity_plan(5000)
    assert p["location"]["slots"] == p["engines"] == 10


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
    p = plan.capacity_plan(1000, threads_per_engine=250,
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

def test_supported_threads_scales_on_the_tighter_dimension():
    assert plan.supported_threads(2000, 8 * 1024 ** 3) == 500       # the baseline
    assert plan.supported_threads(1000, 4 * 1024 ** 3) == 250       # half of both
    # Twice the memory and the same CPU is not twice the engine.
    assert plan.supported_threads(2000, 16 * 1024 ** 3) == 500
    assert plan.supported_threads(1000, 16 * 1024 ** 3) == 250


def test_supported_threads_never_reaches_zero():
    """A tiny engine carries few threads, not none -- zero would divide the
    plan by nothing and read as 'this engine cannot run'."""
    assert plan.supported_threads(1, 1) == 1


def test_doctor_judges_against_the_same_ratio_the_planner_sizes_from():
    """The planner and the preflight must not disagree: a plan doctor then
    WARNs about is worse than either alone. This is the one assertion that
    would catch the ratio being restated in one of them."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    supported = plan.supported_threads(1000, 4 * 1024 ** 3)
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
    {"users": -5},
    {"users": 100, "threads_per_engine": 0},
    {"users": 100, "engines_per_node": 0},
    {"users": "many"},
    {"users": 100, "engines_per_node": 2.5},
])
def test_refuses_what_cannot_be_a_plan(kwargs):
    with pytest.raises(ValueError):
        plan.capacity_plan(**kwargs)


def test_refusal_names_the_field():
    with pytest.raises(ValueError, match="threads_per_engine"):
        plan.capacity_plan(100, threads_per_engine=0)


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
    assert p["threads_per_engine"] == 500
    assert p["threads_per_engine_assumed"] is True


def test_a_supplied_figure_is_not_marked_assumed():
    p = plan.capacity_plan(5000, threads_per_engine=500)
    assert p["threads_per_engine"] == 500
    assert p["threads_per_engine_assumed"] is False


def test_document_says_the_users_per_engine_figure_is_an_assumption():
    """The whole document multiplies by this number and nothing here can
    verify it. A reader who takes the node count and never learns that is the
    failure this planner has to avoid."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "not a measurement" in doc
    assert "confirm" in doc.lower()
    assert "one real run" in doc


def test_document_does_not_claim_a_supplied_figure_is_blazemeter_s():
    doc = plan.plan_document(plan.capacity_plan(5000, threads_per_engine=120))
    assert "supplied rather than measured" in doc
    assert "BlazeMeter's documented figure" not in doc


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


def test_document_carries_the_name_when_given_one():
    assert "for Checkout API" in plan.plan_document(
        plan.capacity_plan(100), name="Checkout API")
    assert "for " not in plan.plan_document(plan.capacity_plan(100)).split("\n")[0]


def test_document_states_the_override_fields_and_why():
    """The two fields that decide whether the cluster in this request is the
    cluster that gets used."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "overrideCPU" in doc and "overrideMemory" in doc
    assert "250m" in doc and "256Mi" in doc
    assert "scheduler places pods on requests" in doc


def test_document_warns_when_the_engine_is_too_small_for_the_threads():
    p = plan.capacity_plan(5000, threads_per_engine=500,
                           engine_cpu="1", engine_mem="4Gi")
    assert p["warnings"]
    assert "throttle" in " ".join(p["warnings"])
    assert "Worth knowing" in plan.plan_document(p)


def test_no_over_threading_warning_when_the_engine_is_big_enough():
    p = plan.capacity_plan(5000, threads_per_engine=500)
    assert not any("throttle" in w for w in p["warnings"])


def test_sharing_a_node_is_warned_about_but_allowed():
    p = plan.capacity_plan(5000, engines_per_node=4)
    assert p["nodes"] == 3
    assert any("contend" in w for w in p["warnings"])


def test_warnings_are_prose_in_both_places_they_are_shown():
    """They go into the Markdown document and into the web panel, which renders
    neither backticks nor `--`. One wording has to read correctly in both."""
    plans = [plan.capacity_plan(5000, threads_per_engine=500,
                                engine_cpu="1", engine_mem="4Gi"),
             plan.capacity_plan(5000, engines_per_node=4),
             plan.capacity_plan(5000)]
    for w in [w for p in plans for w in p["warnings"]]:
        assert "`" not in w, w
        assert "--" not in w, w
