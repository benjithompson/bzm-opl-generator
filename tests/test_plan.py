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
from test_core import _imports  # noqa: E402


# -- what it is allowed to reach --------------------------------------------

def test_plan_reaches_nothing():
    """The requirement, asserted rather than described.

    "Reaches nothing" was prose in CLAUDE.md while core's equivalent rule was
    an AST assertion, and prose is what the fifth recurrence of a rule is made
    of. Anything that reads an account, a cluster or a file puts the *first*
    step behind a later one, for the one user who has none of them.

    `api` and `generate` are allowed and named: the planner takes constants
    from them -- the API host for the egress list, the engine footprint and
    node overhead doctor judges against -- and importing a constant is not
    reaching anything. `client`, `facts`, `doctor` and `livetest` are not.
    """
    imported = _imports(plan.__file__)
    reaching = imported & {"subprocess", "urllib", "http", "socket", "os",
                           "facts", "doctor", "livetest", "core", "kubectl",
                           "requests", "json"}
    assert not reaching, (
        f"plan imports {sorted(reaching)} -- it sizes a cluster for somebody "
        f"who has no cluster, no account and no evidence file, so every one of "
        f"those is a dependency that puts the first step behind a later one")


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
    assert p["location"]["override_memory"] == 16384


def test_override_memory_is_megabytes():
    """BlazeMeter's overrideMemory field is in MB, and a plan that reported it
    in bytes or GiB would be pasted into the UI as-is."""
    assert plan.capacity_plan(500)["location"]["override_memory"] == 8192


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


# -- three sizing models, one pod size ---------------------------------------
#
# Two of the three covered functionalities are not sized in virtual users at
# all, and the third has no measured figure to be sized with. What these defend
# is that each model keeps its own unit, and that the one with no figure says so
# rather than borrowing the performance one.

def test_each_model_is_asked_for_in_its_own_unit():
    units = {f: m["unit"] for f, m in plan.SIZING_MODELS.items()}
    assert units == {"performance": "virtual users",
                     "functionalGui": "browser instances",
                     "mockServices": "requests per second"}


def test_browser_instances_scale_with_the_pod_as_virtual_users_do():
    """4 is the account owner's figure for BlazeMeter's own engine size, not a
    constant -- the same thing 500 is, so it moves the same way."""
    base = (2000, 8 * 1024 ** 3)
    assert plan.per_pod_capacity("functionalGui", *base) == 4
    assert plan.per_pod_capacity("functionalGui", 1000, 4 * 1024 ** 3) == 2
    assert plan.per_pod_capacity("functionalGui", 4000, 16 * 1024 ** 3) == 8
    # Never zero, for supported_vus' reason: a small pod carries few browsers,
    # not none.
    assert plan.per_pod_capacity("functionalGui", 1, 1) == 1


def test_service_virtualization_has_no_figure_to_be_sized_with():
    """The point of the whole table. `vus_per_engine_assumed` carries supplied
    against defaulted; requests per second per core is in neither position,
    because nobody has measured it. None, so no arithmetic can reach past it."""
    assert plan.SIZING_MODELS["mockServices"]["baseline"] is None
    assert plan.per_pod_capacity("mockServices", 2000, 8 * 1024 ** 3) is None


def test_supported_vus_is_the_performance_model_under_its_own_name():
    """doctor calls this one, and the pairing test above is what holds the two
    together. It has to stay the same function, not a second copy of it."""
    for cpu, mem in ((2000, 8 * 1024 ** 3), (1000, 4 * 1024 ** 3), (1, 1)):
        assert plan.supported_vus(cpu, mem) \
            == plan.per_pod_capacity("performance", cpu, mem)


def test_a_gui_sizing_counts_browser_instances():
    p = plan.capacity_plan(
        sizings=[{"functionality": "functionalGui", "target": 20}])
    assert p["engines"] == 5                       # 20 browsers at 4 an engine
    assert p["driven_by"] == "functionalGui"
    row = _row(p, "functionalGui")
    assert (row["target"], row["per_pod"], row["pods"]) == (20, 4, 5)
    assert row["per_pod_source"] == "assumed"


def test_a_supplied_browser_figure_is_not_marked_assumed():
    p = plan.capacity_plan(sizings=[{"functionality": "functionalGui",
                                     "target": 20, "figure": 2}])
    assert p["engines"] == 10
    assert _row(p, "functionalGui")["per_pod_source"] == "supplied"


def test_the_largest_model_decides_the_pool_and_the_plan_names_it():
    """Where a location runs several, one of them is the one being sized for --
    and which one is the first thing a reader of the node count needs."""
    perf = plan.capacity_plan(5000, sizings=[
        {"functionality": "functionalGui", "target": 20}])
    assert (perf["engines"], perf["driven_by"]) == (10, "performance")

    gui = plan.capacity_plan(500, sizings=[
        {"functionality": "functionalGui", "target": 100}])
    assert (gui["engines"], gui["driven_by"]) == (25, "functionalGui")


def test_sizing_for_several_says_it_is_the_largest_and_not_the_sum():
    p = plan.capacity_plan(5000, sizings=[
        {"functionality": "functionalGui", "target": 20}])
    assert any("largest" in w and "not for all of them at once" in w
               for w in p["warnings"])
    # ...and one model alone has nothing to say about it.
    assert not any("largest" in w for w in plan.capacity_plan(5000)["warnings"])


def test_service_virtualization_is_carried_unsized_rather_than_defaulted():
    """It is stated, and it drives nothing: there is no ratio to turn requests
    per second into pods with, so borrowing the performance one would put a
    number nobody measured into a node count."""
    p = plan.capacity_plan(5000, sizings=[
        {"functionality": "mockServices", "target": 2000}])
    row = _row(p, "mockServices")
    assert row["target"] == 2000
    assert row["per_pod"] is None
    assert row["per_pod_source"] == "unmeasured"
    assert row["pods"] is None
    # The performance sizing is untouched by it.
    assert (p["engines"], p["driven_by"]) == (10, "performance")
    assert any("has not been measured" in w for w in p["warnings"])


def test_unmeasured_is_not_the_same_answer_as_assumed():
    """The rule this repo keeps everywhere else: could not read and there is
    nothing there must not share a representation. Three states, three
    values."""
    p = plan.capacity_plan(5000, vus_per_engine=250, sizings=[
        {"functionality": "functionalGui", "target": 20},
        {"functionality": "mockServices", "target": 2000}])
    assert [r["per_pod_source"] for r in p["sizings"]] \
        == ["supplied", "assumed", "unmeasured"]


def test_a_sizing_that_cannot_be_worked_out_is_refused_not_guessed():
    """Service virtualization on its own. A plan is not a plan without a pod
    count, and inventing one is the one thing this must never do -- so the
    refusal is the sentence explaining why, not a number."""
    with pytest.raises(ValueError) as e:
        plan.capacity_plan(sizings=[{"functionality": "mockServices",
                                     "target": 2000}])
    assert "requests per second" in str(e.value)
    assert "has not been measured" in str(e.value)


def test_no_figure_may_be_supplied_where_none_is_measured():
    """Not a gap left open for a caller to fill: a requests-per-second figure
    would size mock pods, and every number after the pod count in this plan --
    slots, the engines per node, the whole document -- is about engines."""
    with pytest.raises(ValueError, match="mockServices"):
        plan.capacity_plan(sizings=[{"functionality": "mockServices",
                                     "target": 2000, "figure": 250}])


def test_an_unknown_functionality_is_refused_by_name():
    with pytest.raises(ValueError, match="tdm"):
        plan.capacity_plan(sizings=[{"functionality": "tdm", "target": 5}])


def test_one_functionality_cannot_be_sized_twice():
    with pytest.raises(ValueError, match="performance"):
        plan.capacity_plan(5000, sizings=[{"functionality": "performance",
                                           "target": 100}])


def test_a_model_names_its_own_field_in_a_refusal():
    """"users must be at least 1" is no help to somebody who typed a browser
    count. Each model's field is the one the surfaces above call it."""
    with pytest.raises(ValueError, match="browsers"):
        plan.capacity_plan(sizings=[{"functionality": "functionalGui",
                                     "target": 0}])


def _row(p, functionality):
    return [r for r in p["sizings"] if r["functionality"] == functionality][0]


# -- the document, once there is more than one model to write it about -------

def _three():
    return plan.capacity_plan(5000, sizings=[
        {"functionality": "functionalGui", "target": 20},
        {"functionality": "mockServices", "target": 2000}])


def test_the_document_states_every_sizing_in_its_own_unit():
    """The reader provisions for all of it, so all of it has to be in the ask.
    A browser suite stated in virtual users is a number about somebody else's
    workload."""
    # Whitespace collapsed: the ask is one wrapped sentence whose length
    # follows how many models were sized, so a line break can fall anywhere in
    # it. What is asserted is the words.
    doc = " ".join(plan.plan_document(_three()).split())
    assert "performance tests of up to **5,000 virtual users**" in doc
    assert "browser tests of up to **20 browser instances**" in doc
    assert "virtual services of up to **2,000 requests per second**" in doc


def test_the_document_shows_each_model_s_own_division():
    doc = plan.plan_document(_three())
    assert "5,000 / 500, rounded up" in doc
    assert "20 / 4, rounded up" in doc


def test_the_document_names_the_sizing_the_pool_came_from():
    """Which workload the node count was reached from is the first thing a
    reader of it needs, and with three sizings on the page it is not obvious."""
    doc = plan.plan_document(_three())
    assert "the largest of these, from the performance sizing" in doc


def test_a_browser_only_request_asks_for_browser_testing():
    """The title is what the ticket is called, and "load testing" on a request
    for a Selenium grid is the first thing that gets it sent back."""
    doc = plan.plan_document(plan.capacity_plan(
        sizings=[{"functionality": "functionalGui", "target": 20}]))
    assert doc.splitlines()[0] == "# Infrastructure request: browser testing"
    assert "20 / 4, rounded up" in doc


def test_the_document_says_where_the_browser_figure_came_from():
    """Roughly 4, from the account owner. An assumption, and one this tool can
    measure even less than it can measure virtual users per engine."""
    doc = plan.plan_document(plan.capacity_plan(
        sizings=[{"functionality": "functionalGui", "target": 20}]))
    assert "4 browser instances per engine" in doc
    assert "account owner" in doc
    assert "not a measurement" in doc


def test_the_document_says_service_virtualization_is_not_sized_here():
    """Absent and stated, in the one place a platform team reads. A request
    that quietly dropped the mocks would be provisioned as if they were free."""
    doc = plan.plan_document(_three())
    assert "has not been measured" in doc
    assert "Worth knowing" in doc


def test_a_request_with_no_load_target_still_states_threads_per_engine():
    """It is a location setting, and a location that runs any test at all needs
    it. What changes is where the figure came from, which the row says."""
    doc = plan.plan_document(plan.capacity_plan(
        sizings=[{"functionality": "functionalGui", "target": 20}]))
    assert "threadsPerEngine" in doc
    assert "No load test was sized here" in doc


def test_a_one_model_document_is_the_one_model_document():
    """The common case stays the simple case: nothing about largest, nothing
    about other workloads, and the plural heading only when there is one."""
    doc = plan.plan_document(plan.capacity_plan(5000))
    assert "## The assumption in this plan" in doc
    assert "largest of these" not in doc
    assert plan.plan_document(_three()).count("## The assumptions in this plan") == 1


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


def test_a_fractional_engine_has_no_whole_core_request():
    """overrideCPU takes whole cores, so a 500m engine has none to state.

    None rather than a formatted "500m": the field cannot hold it, and the web
    UI used to find that out with a regex over the plan's own string. Unknown
    is the null; a number is always a number the field will take.
    """
    p = plan.capacity_plan(100, engine_cpu="500m", engine_mem="2Gi")
    assert p["location"]["override_cpu"] is None
    assert p["location"]["override_memory"] == 2048
    doc = plan.plan_document(p)
    assert "whole cores" in doc
    # Never the repr of a missing value in the cell somebody types from. (Not
    # `"None" not in doc` -- the prose says "None of that waits for the
    # cluster", which is how the first version of this assertion failed.)
    assert "`None`" not in doc

    whole = plan.capacity_plan(100, engine_cpu="2", engine_mem="8Gi")
    assert whole["location"]["override_cpu"] == 2


def test_the_slots_row_only_multiplies_when_there_is_something_to_multiply():
    """At one agent, "1 x 10 = 10" is arithmetic for its own sake -- and it
    invites the question of where the 1 came from, which is the one thing the
    planner cannot answer: how many agents a location ends up with is decided
    after the cluster exists and changes at will. The web planner therefore has
    no agents field, and this is what its document says.
    """
    one = plan.plan_document(plan.capacity_plan(5000))
    row = [l for l in one.splitlines() if "Engines per agent" in l][0]
    assert "1 x 10" not in row
    assert "Add agents to this location" in row

    many = plan.plan_document(plan.capacity_plan(5000, agents=4))
    row = [l for l in many.splitlines() if "Engines per agent" in l][0]
    assert "4 x 3 = 12 engines" in row
