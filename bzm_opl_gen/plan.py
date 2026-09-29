"""Sizing before there is anything to size: what a load target costs in nodes.

Arithmetic over numbers for somebody with no account and no cluster; the output
is a plan and a document for the infrastructure request. It reaches nothing
(tests/test_plan.py checks the import closure) and shares its constants with
doctor, so what it predicts is what doctor later measures.

Users per engine is a property of the test script that nothing here can
measure, so it is assumed from BlazeMeter's 500 threads per 2 CPU / 8Gi engine,
scaled to the engine size, and every plan carries `vus_per_engine_assumed`.
"""

import math
import textwrap

from .bundle_options import engine_size
from .footprint import (API_BASE, CRANE_CPU_LIMIT, CRANE_MEM_LIMIT,
                        DEFAULT_THREADS_PER_ENGINE, ENGINE_DEFAULT_CPU,
                        ENGINE_DEFAULT_MEM, ENGINE_DEFAULT_REQUEST_CPU,
                        ENGINE_DEFAULT_REQUEST_MEM, ENGINE_DISK_GB,
                        ENGINE_TMP_GB, ENGINE_UPLOAD_HOSTS,
                        GKE_MIN_MAX_PODS, NODE_OVERHEAD_CPU,
                        NODE_OVERHEAD_MEM, PUBLIC_REGISTRY,
                        TYPICAL_SYSTEM_PODS)
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory


# The API host the agent registers against, for the egress list.
API_HOST = API_BASE.split("/")[2]

# Virtual users on an ENGINE_DEFAULT_CPU/MEM engine. Other sizes scale on the
# tighter of the CPU and memory ratios.
BASELINE_VUS = DEFAULT_THREADS_PER_ENGINE

# Browser instances on the same engine: the account owner's rough estimate, not
# a measurement, and it scales the same way.
BASELINE_BROWSERS = 4
DOCUMENT_FILE = "capacity-request.md"
PERFORMANCE, GUI, SV = "performance", "functionalGui", "mockServices"

# What each covered functionality is sized in, and what one pod of the baseline
# size carries (`baseline`). Crane applies one limits pair to every pod it
# creates, so the models share a pod size and the largest pod count decides.
#
# `baseline: None` means no measured figure (service virtualization): that
# model sizes nothing, and is never filled in with another model's ratio.
# `target_field`/`figure_field` are the names surfaces and refusals use.
# `example_target` is a starting point, not a recommendation. `name`, `runs`
# and `asks` are this module's prose, not the account's display names, which
# would mean reaching an account.
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
        # No figure can be supplied: it would size mock pods against engines.
        "figure_field": None,
        "figure_unit": "requests per second per core",
        "baseline": None,
        "pod": "mock pod", "pods": "mock pods",
        "runs": "virtual services",
        "asks": "service virtualization",
    },
}


def sizing_models_for(func_ids):
    """The models describing a location carrying `func_ids`, in SIZING_MODELS
    order.

    None for funcIds nobody read, [] for funcIds no model covers (tdm, delphix,
    ...): a caller says opposite things about the two.
    """
    if func_ids is None:
        return None
    carried = set(func_ids)
    return [f for f in SIZING_MODELS if f in carried]


def per_pod_capacity(functionality, cpu_millis, mem_bytes):
    """What one pod of this size carries in the functionality's unit, or None
    without a measured baseline.

    Linear on the smaller of the CPU and memory ratios, floored at 1.
    """
    baseline = SIZING_MODELS[functionality]["baseline"]
    if baseline is None:
        return None
    base_cpu, base_mem = parse_cpu(ENGINE_DEFAULT_CPU), parse_memory(ENGINE_DEFAULT_MEM)
    ratio = min(cpu_millis / base_cpu, mem_bytes / base_mem)
    return max(int(baseline * ratio), 1)


def supported_vus(cpu_millis, mem_bytes):
    """Threads an engine of this size carries: the ratio
    doctor.check_threads_per_engine judges a location against.
    """
    return per_pod_capacity(PERFORMANCE, cpu_millis, mem_bytes)


def _unmeasured_note(row):
    """Why a model with no baseline sizes nothing: one wording for the plan's
    warning and for the refusal.
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
    """Whether a caller said anything; blank means absent."""
    return value is not None and str(value).strip() != ""


def _sizing_row(functionality, target, figure, cpu, mem):
    """One model's answer: its target, what a pod carries, how many pods.

    `per_pod_source` is supplied, assumed or unmeasured, and the third must
    never collapse into either of the others.
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
        # None, never zero: unsized is not zero pods.
        "pods": math.ceil(target / per_pod) if per_pod else None,
    }


def _sizing_rows(users, vus_per_engine, sizings, cpu, mem):
    """Every sizing requested, in model order. `users` is the performance
    model's target under its long-standing name.
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
        _positive(users, "users")
    order = list(SIZING_MODELS)
    return [_sizing_row(fid, target, figure, cpu, mem)
            for fid, target, figure in sorted(given,
                                              key=lambda g: order.index(g[0]))]


def sizings_from(values):
    """The `sizings` rows for a surface's flat fields, one per non-performance
    model.

    A row is built where the target or its figure was given: blank, absent and
    zero differ, and a 0 or a figure without a target belongs in a refusal
    naming the field.
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

    `users` sizes performance and `sizings` adds other models. The largest pod
    count decides the pool, and `driven_by` names it. A model with no figure
    drives nothing; sized alone it raises ValueError with _unmeasured_note.

    `slots` is engines per agent (BlazeMeter's "Engines per agent"), so the run
    is divided by `agents`, and nodes are per agent because an agent is a
    cluster. An unset `vus_per_engine` is assumed from the engine size. Raises
    ValueError on anything that cannot be a plan.
    """
    # Both default to one, here and nowhere else, so no caller defines blank
    # for itself.
    per_node = _positive(1 if engines_per_node is None else engines_per_node,
                         "engines_per_node")
    agents = _positive(1 if agents is None else agents, "agents")

    # The bundle's own parse-and-default, error messages included.
    cpu, mem = engine_size({"engine_cpu_limit": engine_cpu,
                            "engine_mem_limit": engine_mem})
    supported = supported_vus(cpu, mem)

    rows = _sizing_rows(users, vus_per_engine, sizings, cpu, mem)
    sized = [r for r in rows if r["pods"]]
    # Only a model with no baseline gets here: refuse rather than invent a pod
    # count.
    if not sized:
        raise ValueError(" ".join(
            [_unmeasured_note(r) for r in rows if r["pods"] is None]
            + ["Nothing else is sized here, so there is no pod count to build "
               "a plan from."]))
    # max() keeps the first of a tie, so a tie goes to the earlier model.
    driver = max(sized, key=lambda r: r["pods"])
    engines = driver["pods"]

    # threads_per_engine is a location setting every test needs; without a
    # performance sizing it is the engine's rating.
    perf = next((r for r in rows if r["functionality"] == PERFORMANCE), None)
    if perf is not None:
        vus, assumed = perf["per_pod"], perf["per_pod_source"] == "assumed"
    elif _given(vus_per_engine):
        vus, assumed = _positive(vus_per_engine, "vus_per_engine"), False
    else:
        vus, assumed = supported, True
    users = perf["target"] if perf is not None else None
    # Rounded up so the agents together always reach the target.
    per_agent = math.ceil(engines / agents)
    nodes_per_agent = math.ceil(per_agent / per_node)
    nodes = nodes_per_agent * agents

    # Capacity, not allocatable: what gets bought is a machine, and the kubelet
    # reserves from it.
    node_cpu = cpu * per_node + NODE_OVERHEAD_CPU
    node_mem = mem * per_node + NODE_OVERHEAD_MEM

    return {
        # None where no load test was sized; 0 would read as a sized zero.
        "users": users,
        "vus_per_engine": vus,
        "vus_per_engine_assumed": assumed,
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
        # One agent's cluster at full width; the pool idles at zero between
        # runs.
        "peak": {
            "cpu": format_cpu(node_cpu * nodes_per_agent),
            "memory": format_memory(node_mem * nodes_per_agent),
            "cpu_millis": node_cpu * nodes_per_agent,
            "memory_bytes": node_mem * nodes_per_agent,
            "disk_gb": ENGINE_DISK_GB * per_agent,
        },
        # The always-on agent; limits only.
        "crane": {
            "cpu_limit": CRANE_CPU_LIMIT, "memory_limit": CRANE_MEM_LIMIT,
        },
        # The four location settings, in core.LOCATION_SETTINGS' names and
        # units. The overrides set the engine requests; unset, engines schedule
        # at the default requests and pack onto one node.
        "location": {
            "slots": per_agent,
            "threads_per_engine": vus,
            # None where the engine is not whole cores, which the field cannot
            # express.
            "override_cpu": cpu // 1000 if cpu % 1000 == 0 else None,
            "override_memory": mem // (1024 ** 2),
        },
        "egress": [API_HOST, *ENGINE_UPLOAD_HOSTS, PUBLIC_REGISTRY.split("/")[0]],
        "warnings": _warnings(rows, driver, cpu, mem, per_node, supported),
    }


def _positive(value, name):
    """A whole number above zero, named in the refusal. Accepts "10" from a
    form; refuses 2.5 rather than truncating it.
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
    """What is true of this plan that a node count cannot express.

    Warnings, never refusals. Plain prose, no backticks or `--`: shown as
    Markdown in the document and as text in the panel.
    """
    out = []
    for r in rows:
        if r["per_pod_source"] == "unmeasured":
            out.append(_unmeasured_note(r))
    # Sized for the largest workload, not the sum.
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
    # `perf and ...`, not a 0 sentinel: an absent figure must not become a
    # number.
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
    """The plan as a document for whoever provisions the cluster.

    Written for a reader who will not install BlazeMeter: what to create, then
    where each number came from, arithmetic shown, so the request can be
    checked rather than halved. It names no application under test, and keeps
    BlazeMeter's vocabulary: a location holds agents, an agent runs engines, an
    engine drives virtual users.
    """
    p = plan
    eng, node, peak = p["engine"], p["node"], p["peak"]
    rows = p["sizings"]
    models = [SIZING_MODELS[r["functionality"]] for r in rows]

    lines = [
        f"# Infrastructure request: {_join(m['asks'] for m in models)}",
        "",
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

    # The unmeasured note is already an assumption paragraph here; do not
    # repeat it.
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
    """"a", "a and b", "a, b and c"."""
    parts = list(parts)
    if len(parts) < 3:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _wrap(text, width=78):
    """Prose wrapped to the document's width, for sentences whose length varies
    with the sizings.
    """
    return textwrap.wrap(text, width)


def _ask_phrase(row):
    """One sizing as the ask reads it, its unit beside its workload."""
    m = SIZING_MODELS[row["functionality"]]
    return f"{m['runs']} of up to **{row['target']:,} {row['unit']}**"


def _arithmetic_block(p):
    """Each model's division, then the pool's. A single model keeps its
    division on the pod-count line.
    """
    rows = p["sizings"]
    driver = next(r for r in rows if r["functionality"] == p["driven_by"])
    many = len(rows) > 1
    out = ["```"]
    for r in rows:
        out.append(f"{r['target']:>7,} {r['unit']}")
        # No divisor, so no division: a zero would read as needing nothing.
        if r["per_pod"] is None:
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
    """This engine against the baseline, in words ("half that size")."""
    r = row["per_pod"] / SIZING_MODELS[row["functionality"]]["baseline"]
    return {0.25: "a quarter of that size", 0.5: "half that size",
            2.0: "twice that size", 4.0: "four times that size"}.get(
        r, f"{r:g}x that size")


def _assumption_section(p):
    """Where each figure came from (supplied, assumed or unmeasured): the
    inputs nothing here can verify.
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
    """The browsers-per-engine assumption, weaker than the performance figure
    and said so.
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
        "measurement of our test.**"
        + (f" {BASELINE_VUS:,} is BlazeMeter's figure for a"
           if threads != BASELINE_VUS
           else " It is BlazeMeter's own figure for that size."),
        (f"{ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM} engine, and this one "
         f"is {_size_vs_baseline(row)}."
         if threads != BASELINE_VUS else ""),
        "How many users one engine really",
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
    """The four BlazeMeter location settings that must match the plan. Unset
    overrides schedule engines at the default requests and pack the run onto
    one node.
    """
    loc = p["location"]
    driver = next(r for r in p["sizings"] if r["functionality"] == p["driven_by"])
    reach = f"{driver['target']:,} {driver['unit']}"
    # threadsPerEngine must be set even when no load test was sized; the row
    # says where the figure came from.
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
        # The multiplication is shown only when there are several agents.
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
        # None: the engine is not whole cores.
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
        "so every engine asks for a fraction of what it uses, the autoscaler adds "
        "**one**",
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
