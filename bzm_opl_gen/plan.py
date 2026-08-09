"""Sizing before there is anything to size: what a load target costs in nodes.

Every other module here starts from something that already exists -- a
location, an agent, a cluster, an evidence file. This one starts from a number
somebody has in a planning meeting and nothing else, because that is the order
the work actually happens in. "We need to test 5,000 concurrent users" arrives
months before the namespace does, and the cluster it will run on is a ticket
somebody has to raise with a team that has never heard of BlazeMeter. A planner
that needs the cluster cannot help with the request *for* the cluster, which is
the moment the numbers are needed and the moment nothing is deployed.

So: arithmetic over numbers. No account, no facts, no kubectl, no options
dict. The output is a plan and a document to paste into that ticket.

The direction is the point. `doctor` asks "can this cluster run the location's
concurrency?" and needs both to exist; this asks "what would have to exist?"
and needs neither. They meet in the middle -- what this predicts is what
doctor later measures -- and the two must not drift, so the engine footprint,
the node overhead and the threads-per-engine ratio are read from the same
constants doctor judges against rather than restated here.

What this cannot know, and says so rather than implying otherwise: how many
users *one engine* really carries. That is a property of the script -- of what
each thread does between requests -- and no amount of arithmetic here reaches
it. What it assumes instead is the engine's own capacity, scaled from
BlazeMeter's documented pairing of 500 threads with a 2 CPU / 8Gi engine, so
the assumption moves when the engine size does. A plan that quietly turns that
assumption into a node count is how an infrastructure request comes back wrong
by a factor of three, so every returned plan carries
`vus_per_engine_assumed` and the document leads with it.
"""

import math
import textwrap

from .api import (API_BASE, DEFAULT_THREADS_PER_ENGINE,
                  ENGINE_UPLOAD_HOSTS)
from .generate import (CRANE_CPU_LIMIT, CRANE_MEM_LIMIT,
                       ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM,
                       ENGINE_DEFAULT_REQUEST_CPU, ENGINE_DEFAULT_REQUEST_MEM,
                       ENGINE_DISK_GB, ENGINE_TMP_GB, GKE_MIN_MAX_PODS,
                       NODE_OVERHEAD_CPU, NODE_OVERHEAD_MEM, PUBLIC_REGISTRY,
                       TYPICAL_SYSTEM_PODS, engine_size)
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory

# The BlazeMeter API host the agent registers against. Derived from
# api.API_BASE so a base URL change reaches the firewall list too -- the egress
# rule is the one part of this document somebody else implements, and a wrong
# host there fails as an agent that never comes online, days later.
API_HOST = API_BASE.split("/")[2]

# The engine size BlazeMeter's 500 threads is quoted against. Anything else
# scales from it, on whichever of CPU and memory is the tighter ratio: an
# engine given twice the memory and the same CPU is not twice the engine.
BASELINE_VUS = DEFAULT_THREADS_PER_ENGINE

# Browser instances one baseline engine carries, from the account owner and
# "roughly" is how it was given. Nothing here measures it either, so it is an
# assumption in exactly the way BASELINE_VUS is -- and, being the pod's capacity
# rather than a constant of the workload, it scales with the pod for the same
# reason 500 does.
BASELINE_BROWSERS = 4

DOCUMENT_FILE = "capacity-request.md"

PERFORMANCE, GUI, SV = "performance", "functionalGui", "mockServices"

# -- the three sizing models --------------------------------------------------
#
# Each answers one question -- how much of *its own unit* does one pod of the
# chosen size carry? -- because two of the three covered functionalities are
# not sized in virtual users at all, and a planner that speaks only virtual
# users answers a GUI-functional customer with a figure about somebody else's
# workload.
#
# One pod size across all of them, and that is crane's doing rather than a
# simplification here: BlazeMeter's own reference defines
# KUBERNETES_RESOURCES_LIMITS_CPU as the CPU limit for "resources created by
# agent" -- every pod it creates, engines and browser pods and mock pods alike
# -- and there is no second pair. So three models cannot mean three sets of
# limits. They mean three routes to how many pods of the one size are needed,
# and the largest of them decides.
#
# `baseline` is what one pod of ENGINE_DEFAULT_CPU / ENGINE_DEFAULT_MEM carries,
# and **None where no such figure exists**. That third state is why this is a
# table rather than two constants: `vus_per_engine_assumed` already carries the
# difference between a figure supplied and one defaulted, and service
# virtualization is in neither position -- requests per second per core has not
# been measured. It ships absent and stated. Do not fill it in with the
# performance ratio and do not average one out of somebody's mock set: the whole
# reason there is a None here is that a plan cannot quietly turn one into a node
# count.
#
# `target_field` and `figure_field` are the names every surface above calls
# these by -- the CLI flag, the JSON key, the label on the card -- so a refusal
# names the field somebody typed into rather than the model it belongs to.
#
# `example_target` is a starting point and never a recommendation: nothing here
# knows what a customer runs. It exists so a surface offering saved sizings has
# one per model before anybody has typed a number, and so all three units have
# been seen once -- which the page cannot supply for itself, because a figure
# invented in TypeScript for a model it was only just told about is the one
# thing this whole table is arranged to prevent.
#
# `name`, `runs` and `asks` are this module's own prose and not the account's
# display names: a planner that reached core.FUNCTIONALITIES for a word in a
# sentence would reach an account vocabulary, and reaching nothing is the
# requirement here. core.sizing_models() joins BlazeMeter's label on where a
# surface has one.
SIZING_MODELS = {
    PERFORMANCE: {
        "name": "performance",
        "unit": "virtual users",
        "target_field": "users",
        "example_target": 5000,
        "figure_field": "vus_per_engine",
        "figure_unit": "virtual users per engine",
        "baseline": BASELINE_VUS,
        "pod": "engine", "pods": "engines",
        "runs": "performance tests",
        "asks": "load testing",
    },
    GUI: {
        "name": "GUI functional",
        "unit": "browser instances",
        "target_field": "browsers",
        "example_target": 20,
        "figure_field": "browsers_per_engine",
        "figure_unit": "browser instances per engine",
        "baseline": BASELINE_BROWSERS,
        "pod": "engine", "pods": "engines",
        "runs": "browser tests",
        "asks": "browser testing",
    },
    SV: {
        "name": "service virtualization",
        "unit": "requests per second",
        "target_field": "requests_per_second",
        "example_target": 2000,
        # No figure field, and that is the absence rather than an omission: see
        # _unmeasured_note. A caller offering one would be sizing mock pods,
        # which every number after the pod count here is not about.
        "figure_field": None,
        "figure_unit": "requests per second per core",
        "baseline": None,
        "pod": "mock pod", "pods": "mock pods",
        "runs": "virtual services",
        "asks": "service virtualization",
    },
}


def sizing_models_for(func_ids):
    """The models that describe a location carrying `func_ids`, in
    SIZING_MODELS order.

    This module sizes a target for somebody with no location, and every other
    surface has one -- so this is the join between the two, and the reason it is
    here rather than beside either caller. The bundle README and `doctor` both
    hold a location's funcIds and had no way to turn them into a unit, so both
    printed performance's whatever the location ran: `500 virtual users each`
    over an agent that runs browsers (#165). A fourth model becomes a row above
    and neither of them needs an edit.

    A functionality *is* a funcId (#149), so this is a filter rather than a
    translation, and the order is this table's rather than the location's: it is
    the tie-break the configure step already uses for a location carrying
    several, so the same location reads the same way on screen and in its
    bundle.

    **Three answers, and the middle one is why this returns a list.** `None` is
    funcIds nobody read -- facts written before they were recorded -- and `[]`
    is funcIds that were read and that no model here covers, which real accounts
    genuinely have (tdm, dataPublisher, delphix). Both leave a caller with no
    unit to print, and what a caller may say about the location is opposite in
    the two cases, so the value carries which rather than the caller
    remembering.
    """
    if func_ids is None:
        return None
    carried = set(func_ids)
    return [f for f in SIZING_MODELS if f in carried]


def per_pod_capacity(functionality, cpu_millis, mem_bytes):
    """What one pod of this size carries, in that functionality's own unit --
    or None where there is no measured figure to scale.

    Linear on the smaller of the two ratios, and floored at 1: a pod sized below
    the baseline in either dimension carries proportionally less, and 500
    threads on a 1 CPU / 4Gi engine is not a smaller run, it is a run that
    OOM-kills or throttles halfway up the ramp.

    None is not zero and not one. A model with no baseline cannot be sized at
    all, and every caller has to decide what to do about that rather than
    multiply by it.
    """
    baseline = SIZING_MODELS[functionality]["baseline"]
    if baseline is None:
        return None
    base_cpu, base_mem = parse_cpu(ENGINE_DEFAULT_CPU), parse_memory(ENGINE_DEFAULT_MEM)
    ratio = min(cpu_millis / base_cpu, mem_bytes / base_mem)
    return max(int(baseline * ratio), 1)


def supported_vus(cpu_millis, mem_bytes):
    """Threads an engine of this size can carry: the performance model, under
    the name doctor calls it by.

    doctor.check_threads_per_engine judges a *configured* location against this;
    capacity_plan works out the engine count *from* it. Same ratio, one place --
    they were two sentences of arithmetic in two files for exactly one commit,
    and tests/test_plan.py asserts the pair still agree.
    """
    return per_pod_capacity(PERFORMANCE, cpu_millis, mem_bytes)


def _unmeasured_note(row):
    """Why a model with no baseline sizes nothing, in the words both surfaces
    that have to say it use.

    One sentence set, two callers: the warning a plan carries when service
    virtualization is sized beside something else, and the refusal when it is
    sized alone. They were going to be two wordings of one fact, and the
    refusal is the one somebody reads first.

    Plain prose, like every other warning here -- no backticks and no `--`.
    """
    m = SIZING_MODELS[row["functionality"]]
    return (
        f"{row['target']:,} {m['unit']} is what the virtual services here have "
        f"to serve, and this plan does not size for it. How many {m['unit']} "
        f"one core of a {m['pod']} carries has not been measured, in the way "
        f"that virtual users per engine is a property of the script rather "
        f"than of the engine, and nothing in this tool reaches it. Nothing is "
        f"assumed in its place, because a figure invented here would arrive as "
        f"a node count somebody buys. To size it, deploy one {m['pod']} at the "
        f"pod size above, drive it until it saturates, and multiply.")


def _given(value):
    """Whether a caller said anything. Blank is what a browser posts for a
    field nobody filled in, and it means the same as absent."""
    return value is not None and str(value).strip() != ""


def _sizing_row(functionality, target, figure, cpu, mem):
    """One model's answer: its target, what a pod carries, and how many pods.

    `per_pod_source` is three-valued on purpose and must stay that way.
    "supplied" and "assumed" are the distinction `vus_per_engine_assumed`
    already carried; "unmeasured" is the one that had nowhere to go, and
    collapsing it into either is how a figure nobody has becomes a figure
    somebody defaulted.
    """
    m = SIZING_MODELS[functionality]
    target = _positive(target, m["target_field"])
    rated = per_pod_capacity(functionality, cpu, mem)
    if _given(figure):
        if not m["figure_field"]:
            raise ValueError(
                f"{functionality} takes no {m['figure_unit']} figure: none has "
                f"been measured, and one supplied here would size "
                f"{m['pods']} against engines")
        per_pod, source = _positive(figure, m["figure_field"]), "supplied"
    elif rated is not None:
        per_pod, source = rated, "assumed"
    else:
        per_pod, source = None, "unmeasured"
    return {
        "functionality": functionality,
        "unit": m["unit"],
        "target": target,
        "per_pod": per_pod,
        "per_pod_unit": m["figure_unit"],
        "per_pod_source": source,
        "rated": rated,
        "pod": m["pod"],
        "pods_label": m["pods"],
        # None, never zero: a model with no figure has not been sized at 0 pods,
        # it has not been sized.
        "pods": math.ceil(target / per_pod) if per_pod else None,
    }


def _sizing_rows(users, vus_per_engine, sizings, cpu, mem):
    """Everything being sized, in model order.

    `users` is the performance model's shorthand and not a second way of saying
    it: every caller that sizes one thing has only ever had a load target, and
    the CLI flag, the JSON key and doctor's own vocabulary are all built on that
    name. It is folded in here so there is one list downstream.
    """
    given = []
    if _given(users):
        given.append((PERFORMANCE, users, vus_per_engine))
    for s in sizings or []:
        fid = s.get("functionality")
        if fid not in SIZING_MODELS:
            raise ValueError(
                f"{fid!r} has no sizing model; there is one for "
                f"{', '.join(SIZING_MODELS)}")
        if any(f == fid for f, _, _ in given):
            raise ValueError(f"{fid} is sized twice, and two targets for one "
                             f"functionality is two plans")
        given.append((fid, s.get("target"), s.get("figure")))
    if not given:
        # Named `users` because that is the field a caller with one sizing has,
        # and _positive is what says so.
        _positive(users, "users")
    order = list(SIZING_MODELS)
    return [_sizing_row(fid, target, figure, cpu, mem)
            for fid, target, figure in sorted(given,
                                              key=lambda g: order.index(g[0]))]


def sizings_from(values):
    """The `sizings` rows a surface's flat fields name, one per model.

    Every surface that collects a sizing collects it as one field per model,
    named by that model's own `target_field`/`figure_field`: those are the
    words the CLI flag, the JSON key and this module's refusals all use, so a
    caller told `browsers_per_engine must be a whole number` can find what to
    change. Turning those fields into rows is the same walk each time, and the
    command wrote it out by hand -- three flag names and two model constants,
    so a fourth model reached the MCP tool and the route and not the command.

    Performance is left out: `users` is capacity_plan's own argument, and every
    caller with a single sizing has always had it under that name.

    A row is built where the target **or** its figure was given, rather than
    where the target is truthy. Blank, absent and zero are three things: `0` is
    a target somebody typed, and belongs in a refusal naming the field rather
    than being read as a flag nobody passed; and a figure supplied with no
    target is a question, which the planner answers by name. Dropped, both were
    silence.
    """
    rows = []
    for fid, m in SIZING_MODELS.items():
        if fid == PERFORMANCE:
            continue
        target = values.get(m["target_field"])
        figure = values.get(m["figure_field"]) if m["figure_field"] else None
        if not _given(target) and not _given(figure):
            continue
        rows.append({"functionality": fid, "target": target, "figure": figure})
    return rows


def capacity_plan(users=None, vus_per_engine=None, engine_cpu=None,
                  engine_mem=None, engines_per_node=None, agents=None,
                  sizings=None):
    """What a sizing needs, as numbers.

    **Three models, one pod size.** `users` is the performance model's target
    and the shorthand every caller with one sizing uses; `sizings` is the
    general form, one row per functionality, each asked for in its own unit
    (SIZING_MODELS). Where several are sized the **largest** decides the pool
    and `driven_by` names it, because crane applies one limits pair to every pod
    it creates and so the sizes cannot be set apart -- and because a reader of a
    node count needs to know which workload it was reached from before they
    need anything else in this dict.

    Largest of the ones that *can* be worked out. Service virtualization has no
    measured requests-per-second-per-core figure, so its row carries a target,
    `per_pod: None` and `pods: None`, and it drives nothing: the comparison
    would be a mock pod against an engine, which is only legitimate because they
    are the same size, and it is not made here at all because there is no figure
    to reach a mock-pod count with. Sized alone it is a ValueError carrying
    _unmeasured_note, not a plan with a number nobody measured in it.

    **`slots` is per agent, not per location**, and this used to have it wrong.
    BlazeMeter calls the field "Engines per agent" in its own UI -- "the number
    of engines/tests that can run on one agent" -- so a location's total
    concurrency is `agents x slots`, and real accounts lean on that: one here
    has 17 agents at slots=1. Told a location needs 20 engines, the old answer
    said `slots: 20`, which on a two-agent location is forty engines and twice
    the cluster. `agents` is therefore an input, and `slots` an output divided
    by it.

    `vus_per_engine` unset is assumed **from the engine size**, not from a
    fixed 500: 500 is BlazeMeter's figure for its 2 CPU / 8Gi engine, and a
    plan that carried it onto any other size is wrong in both directions. On a
    1 CPU / 4Gi engine it assumed 500 threads the engine cannot carry -- and
    then warned about the very number it had chosen, which reads as the user's
    mistake. On 4 CPU / 16Gi it assumed half what the engine holds and asked
    for twice the nodes. Either way the plan says the figure was assumed; what
    is assumed is now the engine's own.

    Raises ValueError on anything that cannot be a plan. A target of zero users
    is not a small plan, it is a question nobody asked; an engine size that does
    not parse is the customer's typo, and answering it with a default would size
    a cluster from a number they did not mean.
    """
    # None means "not given", and the default for both is one -- one engine to
    # a node so they do not contend, one agent because a location can be run by
    # one. Defaulted here and nowhere else: every caller passes a form field or
    # a flag that may be blank, and each of them defaulting for itself is how
    # two callers come to disagree about what blank means.
    per_node = _positive(1 if engines_per_node is None else engines_per_node,
                         "engines_per_node")
    agents = _positive(1 if agents is None else agents, "agents")

    # Reuse generate's own parse-and-default so an engine size means exactly
    # what it will mean in the bundle, error message included. Before every
    # model, because what a pod carries is derived from it.
    cpu, mem = engine_size({"engine_cpu_limit": engine_cpu,
                            "engine_mem_limit": engine_mem})
    supported = supported_vus(cpu, mem)

    rows = _sizing_rows(users, vus_per_engine, sizings, cpu, mem)
    sized = [r for r in rows if r["pods"]]
    if not sized:
        # Only a model with no baseline can get here, and there is exactly one.
        # A refusal rather than a plan of one pod: the number would be this
        # tool's own invention arriving as a cluster somebody buys.
        raise ValueError(" ".join(
            [_unmeasured_note(r) for r in rows if r["pods"] is None]
            + ["Nothing else is sized here, so there is no pod count to build "
               "a plan from."]))
    # max() keeps the first of a tie, and the rows are in SIZING_MODELS order,
    # so two models needing the same pool are always attributed the same way.
    driver = max(sized, key=lambda r: r["pods"])
    engines = driver["pods"]

    # The performance row's figures, which stay top-level whether or not a load
    # target was given: `threads_per_engine` is a location setting that has to
    # be right on any location that runs a test, and doctor judges it against
    # the same ratio. Absent a performance sizing it is what an engine of this
    # size is rated for, which is what it has always defaulted to.
    perf = next((r for r in rows if r["functionality"] == PERFORMANCE), None)
    if perf is not None:
        vus, assumed = perf["per_pod"], perf["per_pod_source"] == "assumed"
    elif _given(vus_per_engine):
        vus, assumed = _positive(vus_per_engine, "vus_per_engine"), False
    else:
        vus, assumed = supported, True
    users = perf["target"] if perf is not None else None
    # Engines one agent runs -- the location's `slots`. Rounded up, so the
    # agents together can always reach the target: 10 engines over 3 agents is
    # 4 each, which is 12 available and 10 used, not 9 and a test that cannot
    # start.
    per_agent = math.ceil(engines / agents)
    # Nodes are per agent, because an agent is one cluster: sizing the pool from
    # the location's total would build one cluster big enough for every agent's
    # share of the run.
    nodes_per_agent = math.ceil(per_agent / per_node)
    nodes = nodes_per_agent * agents

    # Capacity, not allocatable: what the infrastructure team buys is a machine,
    # and the kubelet's reservations come out of it before a pod sees any. This
    # is the number that belongs in the request; doctor later measures the
    # allocatable that results.
    node_cpu = cpu * per_node + NODE_OVERHEAD_CPU
    node_mem = mem * per_node + NODE_OVERHEAD_MEM

    return {
        # The performance target, and None where no load test was sized -- a
        # plan for browser tests has no virtual users, and 0 would read as one
        # that has none of them on purpose.
        "users": users,
        "vus_per_engine": vus,
        "vus_per_engine_assumed": assumed,
        # Every model asked for, in model order, each in its own unit; and which
        # of them the pod count came from.
        "sizings": rows,
        "driven_by": driver["functionality"],
        "engines": engines,
        "agents": agents,
        "engines_per_agent": per_agent,
        "engines_per_node": per_node,
        "nodes_per_agent": nodes_per_agent,
        "nodes": nodes,
        "engine": {
            "cpu": format_cpu(cpu),
            "memory": format_memory(mem),
            "cpu_millis": cpu,
            "memory_bytes": mem,
            "disk_gb": ENGINE_DISK_GB,
            "tmp_gb": ENGINE_TMP_GB,
            "supported_vus": supported,
        },
        "node": {
            "cpu": format_cpu(node_cpu),
            "memory": format_memory(node_mem),
            "cpu_millis": node_cpu,
            "memory_bytes": node_mem,
            "disk_gb": ENGINE_DISK_GB * per_node,
        },
        # The engine pool at full width. It is not a standing cost -- the pool
        # is meant to idle at zero between runs -- and the document says so
        # where an infrastructure team would otherwise price it as one.
        # One agent's cluster at full width -- the thing a single infrastructure
        # request is for. With several agents each cluster carries its own share
        # rather than the whole run, which is the saving multiple agents buy.
        "peak": {
            "cpu": format_cpu(node_cpu * nodes_per_agent),
            "memory": format_memory(node_mem * nodes_per_agent),
            "cpu_millis": node_cpu * nodes_per_agent,
            "memory_bytes": node_mem * nodes_per_agent,
            "disk_gb": ENGINE_DISK_GB * per_agent,
        },
        # The agent itself, which is the always-on part of the request. Only the
        # limits: nothing states crane's requests, and a figure nobody reads is
        # a figure nobody notices going wrong.
        "crane": {
            "cpu_limit": CRANE_CPU_LIMIT, "memory_limit": CRANE_MEM_LIMIT,
        },
        # What has to be set on the BlazeMeter side for the cluster above to be
        # the cluster that gets used. Two of these are the fields a location
        # leaves unset by default, which is the gap that has engines scheduled
        # at 250m and packed onto one node -- see requests_note() in generate.
        # core.LOCATION_SETTINGS' own four names, in the units its PATCH takes.
        # This block used to have a vocabulary of its own -- `override_memory_mb`
        # and a *formatted* `override_cpu` -- and every consumer paid for it: the
        # web UI renamed one field and stringified the other before it could put
        # them in the settings form, and then guarded with a regex because a
        # number field cannot hold "250m".
        "location": {
            # "Engines per agent" in BlazeMeter's own UI. See the docstring:
            # this is the whole run divided by the agents that will serve it.
            "slots": per_agent,
            "threads_per_engine": vus,
            # None, not a formatted string, when the engine is not a whole
            # number of cores: overrideCPU has been whole cores on every
            # account this has been read on, so a 500m engine has no request
            # to state -- and "cannot say" is the one thing a formatted
            # quantity could not carry.
            "override_cpu": cpu // 1000 if cpu % 1000 == 0 else None,
            "override_memory": mem // (1024 ** 2),
        },
        "egress": [API_HOST, *ENGINE_UPLOAD_HOSTS, PUBLIC_REGISTRY.split("/")[0]],
        "warnings": _warnings(rows, driver, cpu, mem, per_node, supported),
    }


def _positive(value, name):
    """A count that has to be a whole number above zero, named in the refusal.

    int() rather than a type check: these arrive from a JSON body, a form field
    and an argparse int, and "10" from a browser is the same plan as 10 from a
    terminal. A float that is not whole is refused rather than truncated --
    2.5 engines per node is a misunderstanding, and rounding it silently sizes
    a cluster to the wrong half of it.
    """
    try:
        n = int(value)
        if n != float(value):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number, got {value!r}") from None
    if n < 1:
        raise ValueError(f"{name} must be at least 1, got {n}")
    return n


def _warnings(rows, driver, cpu, mem, per_node, supported):
    """Everything true of this plan that a node count cannot express.

    `rows` and `driver` say all of it: the models that were sized are the rows
    with a pod count, and the pod count this cluster was built from is the
    driver's own. They were passed in beside it, which is two callers' worth of
    chances to hand this one plan's warnings another plan's numbers.
    `supported` is what an engine of this size carries, which the caller has
    already worked out.

    Warnings, never refusals: each describes a plan that runs and reports
    numbers somebody will act on, which is more dangerous than one that does
    not run at all.

    Plain sentences, with no backticks and no `--`: these are shown twice, in
    the document where Markdown renders them and in the web panel where it does
    not, and prose that reads as markup in one of the two places is the cost of
    writing them once.
    """
    out = []
    # The models that were asked for and could not be answered. First, because
    # the reader's next question is what happened to the workload they typed a
    # target for and cannot find in the arithmetic.
    for r in rows:
        if r["per_pod_source"] == "unmeasured":
            out.append(_unmeasured_note(r))
    # Largest, not sum, and the difference is a cluster. Both models peaking at
    # once is a plan this one is not; saying so is cheaper than a customer
    # discovering it when the browser suite and the load test are scheduled
    # together.
    if sum(1 for r in rows if r["pods"]) > 1:
        m = SIZING_MODELS[driver["functionality"]]
        out.append(
            f"This cluster is sized for the largest of the workloads above on "
            f"its own, which is {driver['pods']} {driver['pods_label']} for "
            f"{m['runs']}, and not for all of them at once. Crane gives every "
            f"pod it creates the same limits, so the sizes cannot be told "
            f"apart, but the counts can: if these workloads are expected to "
            f"run at the same time, add their pod counts together and size for "
            f"the total instead.")
    gui = next((r for r in rows if r["functionality"] == GUI), None)
    if gui and gui["per_pod_source"] == "supplied" \
            and gui["per_pod"] > gui["rated"]:
        out.append(
            f"{gui['per_pod']} browser instances on a {format_cpu(cpu)} CPU / "
            f"{format_memory(mem)} engine is more than that size is assumed to "
            f"carry, which is about {gui['rated']}. That assumption is the "
            f"account owner's rather than a measurement, so a higher figure may "
            f"well be right, but a browser that runs out of memory fails the "
            f"test it was running rather than reporting a slow one.")
    # `if perf and ...`, never a 0 standing in for "no performance sizing":
    # this is the module whose whole subject is that a figure nobody has must
    # not arrive as a number, and a sentinel here is that mistake in the
    # comparison that decides whether a warning is printed at all.
    perf = next((r for r in rows if r["functionality"] == PERFORMANCE), None)
    if perf and perf["per_pod"] > supported:
        out.append(
            f"{perf['per_pod']} virtual users on a {format_cpu(cpu)} CPU / "
            f"{format_memory(mem)} engine is more than that size carries, which is "
            f"about {supported} ({BASELINE_VUS} per {ENGINE_DEFAULT_CPU} CPU / "
            f"{ENGINE_DEFAULT_MEM}). The engines will throttle or OOM part-way up "
            f"the ramp, and the run will report the load generator's latency rather "
            f"than the system's. Either raise the engine size or lower the virtual "
            f"users per engine; the second needs more engines, so re-plan rather "
            f"than only editing the location.")
    if per_node > 1:
        out.append(
            f"{per_node} engines share a node here. That is legitimate and "
            f"cheaper, since a node spends about {format_cpu(NODE_OVERHEAD_CPU)} "
            f"CPU and {format_memory(NODE_OVERHEAD_MEM)} on itself before any "
            f"engine arrives. But engines are measuring instruments, and two on "
            f"one node contend for CPU, NIC and cache in ways that surface as "
            f"latency the load generator invented.")
    gke_engines = max(GKE_MIN_MAX_PODS - TYPICAL_SYSTEM_PODS, 1)
    if per_node < gke_engines and driver["pods"] > 1:
        out.append(
            f"On GKE a node pool cannot be told to hold fewer than "
            f"{GKE_MIN_MAX_PODS} pods, so after about {TYPICAL_SYSTEM_PODS} "
            f"system pods there is room for {gke_engines} engines a node whatever "
            f"this plan says. What keeps them apart there is setting the "
            f"location's overrideCPU and overrideMemory, because the scheduler "
            f"places pods on requests; the nodepools.md in a generated bundle has "
            f"the rest.")
    return out


def plan_document(plan):
    """The plan as a document to hand to whoever provisions the cluster.

    Written for a reader who does not know what BlazeMeter is and is not going
    to install it: it says what to create, then what each number came from, so
    the first question back -- "why this size?" -- is answered in the ticket
    rather than in a meeting. The arithmetic is shown in full for the same
    reason. A capacity request that cannot be checked gets cut in half by
    somebody with a budget, and the half that goes is usually the node count.

    Deliberately says nothing about *what* is being tested. The request is for
    capacity to run load tests from this cluster, and naming an application
    invites the reply that this should be sized per application -- which is a
    conversation about the test plan, not about nodes.

    One vocabulary throughout, and it is BlazeMeter's own: a private **location**
    holds **agents**, an agent runs **engines**, and each engine drives some
    number of **virtual users**. "Slots" and "threads" appear once each, in the
    settings table, because those are what the fields are called -- not as terms
    this document explains anything in.
    """
    p = plan
    eng, node, peak = p["engine"], p["node"], p["peak"]
    rows = p["sizings"]
    models = [SIZING_MODELS[r["functionality"]] for r in rows]

    lines = [
        # What the ticket is called, and it follows what was sized: "load
        # testing" on a request for a Selenium grid is the first thing that
        # gets one sent back.
        f"# Infrastructure request: {_join(m['asks'] for m in models)}",
        "",
        # Each sizing paired with its own unit rather than two lists the reader
        # has to line up: "performance tests and browser tests of up to 5,000
        # virtual users and 20 browser instances" is a sentence nobody should
        # have to parse. Wrapped rather than hand-broken, because how long it
        # is now depends on how many models were sized.
        *_wrap("To run " + _join(_ask_phrase(r) for r in rows)
               + " from our own Kubernetes cluster, using a BlazeMeter private "
                 "location — the load generators run here, and only results "
                 "leave."),
        "",
        "## What is being asked for",
        "",
        "| | |",
        "|---|---|",
        f"| Load-generator nodes | **{p['nodes_per_agent']}** × {node['cpu']} vCPU "
        f"/ {node['memory']} RAM / {node['disk_gb']}GB disk"
        + ("" if p["agents"] == 1
           else f", in **each of {p['agents']} clusters** ({p['nodes']} in all)")
        + " |",
        "| ...when idle | **0** — they exist only while a test runs, and should "
        "autoscale from zero |",
        f"| Agent node | 1 small node, always on ({p['crane']['cpu_limit']} vCPU / "
        f"{p['crane']['memory_limit']} for the agent pod) |",
        f"| Peak, per cluster | {peak['cpu']} vCPU / {peak['memory']} "
        f"/ {peak['disk_gb']}GB |",
        "| Kubernetes | any current version; one namespace, and the ability to "
        "create Deployments and Pods in it |",
        f"| Outbound network | HTTPS to {', '.join('`' + h + '`' for h in p['egress'])} |",
        "| Inbound network | none — nothing needs to be reachable from outside |",
        "",
        "The load-generator nodes are the whole cost, and they only exist while a",
        "test runs. If the cluster autoscales, a node pool with **minimum 0 and",
        f"maximum {p['nodes_per_agent']}** is the shape being asked for; if it "
        f"does not, the",
        f"{p['nodes_per_agent']} nodes have to be standing when a test is "
        f"scheduled.",
        "",
        "## How that number was reached",
        "",
        "BlazeMeter runs a test from **engines** — one pod each — and each engine",
        "carries a share of the work:",
        "",
    ]
    lines += _arithmetic_block(p)
    lines += [
        "",
        f"Each engine is one pod, sized **{eng['cpu']} CPU / {eng['memory']} / "
        f"{eng['disk_gb']}GB disk**",
        f"({eng['tmp_gb']}GB of that under `/tmp`, which is where a test's own data "
        f"goes). A node",
        f"holding {p['engines_per_node']} of them therefore needs "
        f"{node['cpu']} vCPU and {node['memory']} of **capacity** —"
        + (" the engine's own"
           if p["engines_per_node"] == 1
           else f" {p['engines_per_node']} ×"),
        f"{eng['cpu']} CPU / {eng['memory']}, plus about "
        f"{format_cpu(NODE_OVERHEAD_CPU)} CPU / {format_memory(NODE_OVERHEAD_MEM)} "
        f"the node spends on",
        "Kubernetes itself before any pod is scheduled.",
        "",
    ]

    lines += _assumption_section(p)
    lines += _blazemeter_section(p)

    # Everything the warnings say that the document has not already said. The
    # unmeasured note is a warning *and* an assumption paragraph, because the
    # web panel has no assumptions section and the document has no warning list
    # the reader meets first -- one wording, and the surface that has already
    # shown it does not show it twice.
    stated = {_unmeasured_note(r) for r in rows
              if r["per_pod_source"] == "unmeasured"}
    worth = [w for w in p["warnings"] if w not in stated]
    if worth:
        lines += ["## Worth knowing", ""]
        for w in worth:
            lines += [f"- {w}", ""]

    lines += [
        "## Once the cluster exists",
        "",
        "Before anything is deployed, the cluster can be checked against this",
        "plan — capacity, quotas, admission policy and outbound access — by",
        "running a read-only collector on it and passing the result to",
        "`bzm-opl-gen doctor`. That turns every number above into a PASS or a",
        "FAIL against the real thing, which is the point at which this document",
        "stops being an estimate.",
        "",
    ]
    return "\n".join(lines)


def _join(parts):
    """"a", "a and b", "a, b and c". Every sentence in this document that lists
    the sizings uses it, so the three cases are decided once."""
    parts = list(parts)
    if len(parts) < 3:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _wrap(text, width=78):
    """Prose wrapped to the width the rest of this document is hand-wrapped to.

    For the sentences whose length depends on how many models were sized: a
    hand-broken line is right for exactly one number of sizings.
    """
    return textwrap.wrap(text, width)


def _ask_phrase(row):
    """One sizing as the ask reads it, its unit beside its own workload.

    Paired here rather than as two lists, because "performance tests and browser
    tests of up to 5,000 virtual users and 20 browser instances" asks the reader
    to line them up themselves.
    """
    m = SIZING_MODELS[row["functionality"]]
    return f"{m['runs']} of up to **{row['target']:,} {row['unit']}**"


def _arithmetic_block(p):
    """Every model's own division, then the one the pool came from.

    One model is the ordinary case and stays the ordinary shape -- its division
    is on the line that carries the pod count, exactly as it always was.
    With several, each gets its own block first and the pool line says which one
    it took, because a reader who cannot see where a node count came from
    cannot check it, and this document exists to be checked.
    """
    rows = p["sizings"]
    driver = next(r for r in rows if r["functionality"] == p["driven_by"])
    many = len(rows) > 1
    out = ["```"]
    for r in rows:
        out.append(f"{r['target']:>7,} {r['unit']}")
        if r["per_pod"] is None:
            # Stated and left there: no divisor, so no division and no pod
            # count. A zero on this line would read as a workload that needs
            # nothing.
            out += [f"{'':>7} no measured figure for {r['per_pod_unit']} "
                    f"-- see below", ""]
            continue
        out.append(
            f"{r['per_pod']:>7,} {r['per_pod_unit']}"
            + ("   (assumed -- see below)"
               if r["per_pod_source"] == "assumed" else "   (supplied)"))
        out.append("-" * 7)
        if many:
            out += [f"{r['pods']:>7} {r['pods_label']}"
                    f"   ({r['target']:,} / {r['per_pod']:,}, rounded up)", ""]
    if many:
        out.append(
            f"{p['engines']:>7} {driver['pods_label']}, running at the same time"
            f"   (the largest of these, from the "
            f"{SIZING_MODELS[driver['functionality']]['name']} sizing)")
    else:
        out.append(
            f"{p['engines']:>7} {driver['pods_label']}, running at the same time"
            f"   ({driver['target']:,} / {driver['per_pod']:,}, rounded up)")
    out += [
        f"{p['agents']:>7} agent(s) to run them",
        "-" * 7,
        f"{p['engines_per_agent']:>7} engines per agent"
        + ("   (the location's `slots`)" if p["agents"] == 1
           else f"   ({p['engines']} / {p['agents']}, rounded up -- the "
                f"location's `slots`)"),
        f"{p['engines_per_node']:>7} engine(s) per node",
        "-" * 7,
        f"{p['nodes_per_agent']:>7} nodes per agent"
        + ("" if p["agents"] == 1 else f", {p['nodes']} in total"),
        "```",
    ]
    return out


def _size_vs_baseline(row):
    """This engine against the baseline one, as words rather than a decimal.

    "half" and "twice" are what a reader checks the arithmetic with; "0.5x"
    invites them to wonder what was rounded.
    """
    r = row["per_pod"] / SIZING_MODELS[row["functionality"]]["baseline"]
    return {0.25: "a quarter of that size", 0.5: "half that size",
            2.0: "twice that size", 4.0: "four times that size"}.get(
        r, f"{r:g}x that size")


def _assumption_section(p):
    """Where each model's figure came from, stated where it cannot be read past.

    These are the inputs the whole document multiplies by and the only ones
    nothing here can verify. A reader who takes the node count and skips this is
    the failure mode; a reader who disagrees with a figure and re-runs the plan
    is the success.

    One paragraph per sizing, and three shapes rather than two: supplied,
    assumed, and -- for service virtualization -- no figure at all. The third is
    the one this section exists to keep visible, because a workload that is in
    the ask and not in the arithmetic is exactly what a platform team would
    otherwise provision as free.
    """
    rows = p["sizings"]
    out = ["## The assumption in this plan" if len(rows) == 1
           else "## The assumptions in this plan", ""]
    for r in rows:
        out += _assumption_for(r, p)
    return out


def _assumption_for(row, p):
    if row["per_pod_source"] == "unmeasured":
        return _wrap(f"**There is no figure here for {row['per_pod_unit']}.** "
                     + _unmeasured_note(row)) + [""]
    if row["functionality"] == GUI:
        return _browser_assumption(row)
    return _vus_assumption(row, p)


def _browser_assumption(row):
    """Roughly 4, from the account owner.

    Weaker than the performance figure and said so: 500 is BlazeMeter's own
    published pairing, and this is one person's estimate of a workload that
    varies with the page under test more than a script does.
    """
    if row["per_pod_source"] == "supplied":
        return [
            f"**{row['per_pod']:,} browser instances per engine was supplied "
            f"rather than measured",
            "here.** The engine count above is that number divided out, so if it "
            "turns out",
            "to be wrong the node count moves with it.",
            "",
        ]
    return [
        f"**{row['per_pod']:,} browser instances per engine is an estimate from "
        f"the account owner,",
        "not a measurement of our suite.** How many browsers one engine carries",
        "depends on what the pages under test do — a single-page application "
        "holding a",
        "large DOM open costs far more than a form submission — and nothing here "
        "reaches",
        "that. Run the real suite against one engine, raise the parallel count "
        "until it",
        "saturates, and re-plan with the number that comes out.",
        "",
    ]


def _vus_assumption(row, p):
    threads = row["per_pod"]
    if row["per_pod_source"] == "supplied":
        return [
            f"**{threads:,} users per engine was supplied rather than measured "
            f"here.** Everything",
            "above is that number multiplied out, so if it turns out to be wrong "
            "the node",
            "count moves with it. It is worth confirming against a real run "
            "before the",
            "hardware is bought.",
            "",
        ]
    return [
        f"**{threads:,} users per engine is what an engine of this size is rated "
        f"for, not a",
        f"measurement of our test.**"
        + (f" {BASELINE_VUS:,} is BlazeMeter's figure for a"
           if threads != BASELINE_VUS
           else " It is BlazeMeter's own figure for that size."),
        (f"{ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM} engine, and this one "
         f"is {_size_vs_baseline(row)}."
         if threads != BASELINE_VUS else ""),
        f"How many users one engine really",
        "carries depends on what the script does between requests — a chatty API "
        "test",
        "with no think time exhausts an engine far sooner than a browsing journey "
        "does,",
        "and no arithmetic reaches that.",
        "",
        "So the honest form of this request is: **provision for this plan, then "
        "confirm",
        "it with one real run.** Run the actual script against a single engine, "
        "raise the",
        "load until that engine saturates (CPU at its limit, or response times "
        "rising",
        "with no change at the system under test), and re-plan with the number "
        "that",
        "comes out. Doing that first needs one node, not "
        f"{p['nodes']}.",
        "",
    ]


def _blazemeter_section(p):
    """The four fields on the BlazeMeter side that have to agree with the plan.

    Here because two of them are the difference between the cluster above being
    used and being wasted: a location that leaves overrideCPU/overrideMemory
    unset has its engines scheduled at
    ENGINE_DEFAULT_REQUEST_CPU/ENGINE_DEFAULT_REQUEST_MEM, so the autoscaler
    adds one node and packs the entire run onto it -- against a node pool that
    was sized, bought and approved for one engine each.
    """
    loc = p["location"]
    # The pool was sized from one of the models, so "cannot reach X" is that
    # model's own target and unit: on a browser-driven plan the reader is
    # checking `slots` against a browser count, not against virtual users.
    driver = next(r for r in p["sizings"] if r["functionality"] == p["driven_by"])
    reach = f"{driver['target']:,} {driver['unit']}"
    # Where no load test was sized, `threadsPerEngine` still has to be set --
    # a location that runs any test at all fails to start one without it -- but
    # the figure came from the engine's rating rather than from anything asked
    # for here, and a row that did not say so would read as a number somebody
    # chose.
    perf = any(r["functionality"] == PERFORMANCE for r in p["sizings"])
    return [
        "## The BlazeMeter side, so the cluster is actually used",
        "",
        "A private **location** holds **agents**; an agent runs **engines**; each",
        "engine drives **virtual users**. This cluster is where one location's",
        "agent runs, and four of its settings have to match the plan above",
        "(**Settings → Private Locations**):",
        "",
        "| setting | value | why |",
        "|---|---|---|",
        # Named "Engines per agent" because that is what BlazeMeter's own UI
        # calls it and what it means: the row used to read "Concurrent engines
        # ... how many engines may run at once", which is the location's total
        # only when there is one agent -- and this plan divides by agents.
        # The multiplication only earns its place when there is something to
        # multiply. At one agent "1 x 10 = 10" reads as arithmetic for its own
        # sake and invites the question of where the 1 came from -- which is
        # exactly the question the planner cannot answer, since how many agents
        # a location ends up with is decided later and changes at will.
        (f"| Engines per agent (`slots`) | `{loc['slots']}` | what **one** agent "
         f"may run at once. Add agents to this location and its total is "
         f"agents x this — below `{loc['slots']}` a single agent cannot reach "
         f"{reach} |" if p["agents"] == 1 else
         f"| Engines per agent (`slots`) | `{loc['slots']}` | what **one** agent "
         f"may run at once, so this location's total is "
         f"{p['agents']} x {loc['slots']} = {p['agents'] * loc['slots']} engines "
         f"— below that the test cannot reach {reach} |"),
        f"| Virtual users per engine (`threadsPerEngine`) | "
        f"`{loc['threads_per_engine']}` | unset, every test start fails with 403 "
        f"*Not enough available resources*"
        + ("" if perf else ". No load test was sized here, so this is what an "
                          "engine of the size above is rated for")
        + " |",
        # None means this engine is not a whole number of cores, which the
        # field cannot express -- said as that rather than printed as "None".
        (f"| overrideCPU | `{loc['override_cpu']}` | the engine pod's CPU "
         f"**request**, in whole cores |" if loc["override_cpu"] is not None else
         f"| overrideCPU | — | this engine is {p['engine']['cpu']}, and the "
         f"field takes whole cores — round the engine size up, or set the "
         f"request in BlazeMeter by hand |"),
        f"| overrideMemory | `{loc['override_memory']}` | the memory "
        f"**request**, in MB |",
        "",
        "The last two matter more than they look. The engine's *limits* come from "
        "the",
        "generated manifests; its *requests* come from these two fields, and the "
        "Kubernetes",
        f"scheduler places pods on requests. Left unset they default to "
        f"`{ENGINE_DEFAULT_REQUEST_CPU}` / "
        f"`{ENGINE_DEFAULT_REQUEST_MEM}`,",
        f"so every engine asks for a fraction of what it uses, the autoscaler adds "
        f"**one**",
        f"node instead of {p['nodes']}, and the whole run lands on it — on the "
        f"cluster this",
        "document was written to justify.",
        "",
        "**None of that waits for the cluster.** A private location and its agent "
        "are",
        "records in BlazeMeter, not things running here: the location can be "
        "created with",
        "these four values, and its agent added, while this request is still being "
        "read.",
        "The agent simply reports nothing until it is deployed — an agent that has "
        "never",
        "sent a heartbeat is the expected state before then, not a fault. So the "
        "work",
        "either side of the wait can be done during it, and the day the nodes exist "
        "the",
        "only step left is applying the manifests.",
        "",
    ]
