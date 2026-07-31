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
it. BlazeMeter's own 500 is the documented pairing for a 2 CPU / 8Gi engine and
is what this assumes when nobody says otherwise, but a plan that quietly turns
an assumption into a node count is how an infrastructure request comes back
wrong by a factor of three. Every returned plan therefore carries
`threads_per_engine_assumed`, and the document leads with it.
"""

import math

from .api import API_BASE, DEFAULT_THREADS_PER_ENGINE
from .generate import (CRANE_CPU_LIMIT, CRANE_CPU_REQUEST, CRANE_MEM_LIMIT,
                       CRANE_MEM_REQUEST, CRANE_EPHEMERAL_STORAGE,
                       ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM,
                       ENGINE_DEFAULT_REQUEST_CPU, ENGINE_DEFAULT_REQUEST_MEM,
                       ENGINE_DISK_GB, ENGINE_TMP_GB, GKE_MIN_MAX_PODS,
                       NODE_OVERHEAD_CPU, NODE_OVERHEAD_MEM, PUBLIC_REGISTRY,
                       TYPICAL_SYSTEM_PODS, engine_size)
from .livetest import ENGINE_UPLOAD_HOSTS
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory

# The BlazeMeter API host the agent registers against. Derived from
# api.API_BASE so a base URL change reaches the firewall list too -- the egress
# rule is the one part of this document somebody else implements, and a wrong
# host there fails as an agent that never comes online, days later.
API_HOST = API_BASE.split("/")[2]

# The engine size BlazeMeter's 500 threads is quoted against. Anything else
# scales from it, on whichever of CPU and memory is the tighter ratio: an
# engine given twice the memory and the same CPU is not twice the engine.
BASELINE_THREADS = DEFAULT_THREADS_PER_ENGINE

DOCUMENT_FILE = "capacity-request.md"


def supported_threads(cpu_millis, mem_bytes):
    """Threads an engine of this size can carry, scaled from BlazeMeter's own
    pairing of BASELINE_THREADS threads with ENGINE_DEFAULT_CPU/_MEM.

    Linear on the smaller of the two ratios, and floored at 1: an engine sized
    below the baseline in either dimension carries proportionally less, and
    500 threads on a 1 CPU / 4Gi engine is not a smaller run, it is a run that
    OOM-kills or throttles halfway up the ramp.

    doctor.check_threads_per_engine judges a *configured* location against this;
    plan_capacity works out the engine count *from* it. Same ratio, one place --
    they were two sentences of arithmetic in two files for exactly one commit.
    """
    base_cpu, base_mem = parse_cpu(ENGINE_DEFAULT_CPU), parse_memory(ENGINE_DEFAULT_MEM)
    ratio = min(cpu_millis / base_cpu, mem_bytes / base_mem)
    return max(int(BASELINE_THREADS * ratio), 1)


def capacity_plan(users, threads_per_engine=None, engine_cpu=None,
                  engine_mem=None, engines_per_node=1):
    """What `users` concurrent users needs, as numbers.

    `threads_per_engine` unset means BlazeMeter's documented default, and the
    plan says which of the two it was -- see the module docstring for why that
    distinction is carried rather than resolved.

    Raises ValueError on anything that cannot be a plan. A target of zero users
    is not a small plan, it is a question nobody asked; an engine size that does
    not parse is the customer's typo, and answering it with a default would size
    a cluster from a number they did not mean.
    """
    users = _positive(users, "users")
    per_node = _positive(engines_per_node, "engines_per_node")
    assumed = threads_per_engine is None
    threads = (DEFAULT_THREADS_PER_ENGINE if assumed
               else _positive(threads_per_engine, "threads_per_engine"))

    # Reuse generate's own parse-and-default so an engine size means exactly
    # what it will mean in the bundle, error message included.
    cpu, mem = engine_size({"engine_cpu_limit": engine_cpu,
                            "engine_mem_limit": engine_mem})

    engines = math.ceil(users / threads)
    nodes = math.ceil(engines / per_node)
    supported = supported_threads(cpu, mem)

    # Capacity, not allocatable: what the infrastructure team buys is a machine,
    # and the kubelet's reservations come out of it before a pod sees any. This
    # is the number that belongs in the request; doctor later measures the
    # allocatable that results.
    node_cpu = cpu * per_node + NODE_OVERHEAD_CPU
    node_mem = mem * per_node + NODE_OVERHEAD_MEM

    return {
        "users": users,
        "threads_per_engine": threads,
        "threads_per_engine_assumed": assumed,
        "engines": engines,
        "engines_per_node": per_node,
        "nodes": nodes,
        "engine": {
            "cpu": format_cpu(cpu),
            "memory": format_memory(mem),
            "cpu_millis": cpu,
            "memory_bytes": mem,
            "disk_gb": ENGINE_DISK_GB,
            "tmp_gb": ENGINE_TMP_GB,
            "supported_threads": supported,
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
        "peak": {
            "cpu": format_cpu(node_cpu * nodes),
            "memory": format_memory(node_mem * nodes),
            "cpu_millis": node_cpu * nodes,
            "memory_bytes": node_mem * nodes,
            "disk_gb": ENGINE_DISK_GB * engines,
        },
        "crane": {
            "cpu_request": CRANE_CPU_REQUEST, "memory_request": CRANE_MEM_REQUEST,
            "cpu_limit": CRANE_CPU_LIMIT, "memory_limit": CRANE_MEM_LIMIT,
            "ephemeral_storage": CRANE_EPHEMERAL_STORAGE,
        },
        # What has to be set on the BlazeMeter side for the cluster above to be
        # the cluster that gets used. Two of these are the fields a location
        # leaves unset by default, which is the gap that has engines scheduled
        # at 250m and packed onto one node -- see requests_note() in generate.
        "location": {
            "slots": engines,
            "threads_per_engine": threads,
            "override_cpu": format_cpu(cpu),
            "override_memory_mb": mem // (1024 ** 2),
        },
        "egress": [API_HOST, *ENGINE_UPLOAD_HOSTS, PUBLIC_REGISTRY.split("/")[0]],
        "warnings": _warnings(threads, supported, cpu, mem, per_node, engines),
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


def _warnings(threads, supported, cpu, mem, per_node, engines):
    """Everything true of this plan that a node count cannot express.

    Warnings, never refusals: each describes a plan that runs and reports
    numbers somebody will act on, which is more dangerous than one that does
    not run at all.

    Plain sentences, with no backticks and no `--`: these are shown twice, in
    the document where Markdown renders them and in the web panel where it does
    not, and prose that reads as markup in one of the two places is the cost of
    writing them once.
    """
    out = []
    if threads > supported:
        out.append(
            f"{threads} threads on a {format_cpu(cpu)} CPU / {format_memory(mem)} "
            f"engine is more than that size carries, which is about {supported} "
            f"({BASELINE_THREADS} per {ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM}). "
            f"The engines will throttle or OOM part-way up the ramp, and the run "
            f"will report the load generator's latency rather than the system's. "
            f"Either raise the engine size or lower threads per engine; the "
            f"second needs more engines, so re-plan rather than only editing the "
            f"location.")
    if per_node > 1:
        out.append(
            f"{per_node} engines share a node here. That is legitimate and "
            f"cheaper, since a node spends about {format_cpu(NODE_OVERHEAD_CPU)} "
            f"CPU and {format_memory(NODE_OVERHEAD_MEM)} on itself before any "
            f"engine arrives. But engines are measuring instruments, and two on "
            f"one node contend for CPU, NIC and cache in ways that surface as "
            f"latency the load generator invented.")
    gke_engines = max(GKE_MIN_MAX_PODS - TYPICAL_SYSTEM_PODS, 1)
    if per_node < gke_engines and engines > 1:
        out.append(
            f"On GKE a node pool cannot be told to hold fewer than "
            f"{GKE_MIN_MAX_PODS} pods, so after about {TYPICAL_SYSTEM_PODS} "
            f"system pods there is room for {gke_engines} engines a node whatever "
            f"this plan says. What keeps them apart there is setting the "
            f"location's overrideCPU and overrideMemory, because the scheduler "
            f"places pods on requests; the nodepools.md in a generated bundle has "
            f"the rest.")
    return out


def plan_document(plan, name=None):
    """The plan as a document to hand to whoever provisions the cluster.

    Written for a reader who does not know what BlazeMeter is and is not going
    to install it: it says what to create, then what each number came from, so
    the first question back -- "why this size?" -- is answered in the ticket
    rather than in a meeting. The arithmetic is shown in full for the same
    reason. A capacity request that cannot be checked gets cut in half by
    somebody with a budget, and the half that goes is usually the node count.
    """
    p = plan
    eng, node, peak = p["engine"], p["node"], p["peak"]
    title = f" for {name}" if name else ""
    assumed = p["threads_per_engine_assumed"]

    lines = [
        f"# Infrastructure request: load testing{title}",
        "",
        f"To run a **{p['users']:,} concurrent user** performance test on our own "
        f"Kubernetes",
        "cluster, using a BlazeMeter private location (the load generators run",
        "here; only results leave).",
        "",
        "## What is being asked for",
        "",
        "| | |",
        "|---|---|",
        f"| Load-generator nodes | **{p['nodes']}** × {node['cpu']} vCPU / "
        f"{node['memory']} RAM / {node['disk_gb']}GB disk |",
        "| ...when idle | **0** — the pool exists only during a test, and should "
        "autoscale from zero |",
        f"| Agent node | 1 small node, always on ({p['crane']['cpu_limit']} vCPU / "
        f"{p['crane']['memory_limit']} for the agent pod) |",
        f"| Peak, all {p['nodes']} nodes up | {peak['cpu']} vCPU / {peak['memory']} "
        f"/ {peak['disk_gb']}GB |",
        "| Kubernetes | any current version; one namespace, and the ability to "
        "create Deployments and Pods in it |",
        f"| Outbound network | HTTPS to {', '.join('`' + h + '`' for h in p['egress'])} |",
        "| Inbound network | none — nothing needs to be reachable from outside |",
        "",
        "The load-generator nodes are the whole cost, and they only exist while a",
        "test runs. If the cluster autoscales, a node pool with **minimum 0 and",
        f"maximum {p['nodes']}** is the shape being asked for; if it does not, the",
        f"{p['nodes']} nodes have to be standing when a test is scheduled.",
        "",
        "## How that number was reached",
        "",
        "```",
        f"{p['users']:>7,} concurrent users",
        f"{p['threads_per_engine']:>7,} users per engine"
        + ("   (assumed -- see below)" if assumed else "   (supplied)"),
        f"{'-' * 7}",
        f"{p['engines']:>7} engines, run at the same time"
        f"   ({p['users']:,} / {p['threads_per_engine']:,}, rounded up)",
        f"{p['engines_per_node']:>7} engine(s) per node",
        f"{'-' * 7}",
        f"{p['nodes']:>7} nodes",
        "```",
        "",
        f"Each engine is one pod, sized **{eng['cpu']} CPU / {eng['memory']} / "
        f"{eng['disk_gb']}GB disk**",
        f"({eng['tmp_gb']}GB of that under `/tmp`, which is where a test's own data "
        f"goes). A node",
        f"holding {p['engines_per_node']} of them therefore needs "
        f"{node['cpu']} vCPU and {node['memory']} of **capacity** —"
        + (f" the engine's own"
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

    if p["warnings"]:
        lines += ["## Worth knowing", ""]
        for w in p["warnings"]:
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


def _assumption_section(p):
    """The users-per-engine assumption, stated where it cannot be read past.

    It is the one input the whole document multiplies by, and the only one
    nothing here can verify. A reader who takes the node count and skips this
    is the failure mode; a reader who disagrees with 500 and re-runs the plan
    is the success.
    """
    threads = p["threads_per_engine"]
    if not p["threads_per_engine_assumed"]:
        return [
            "## The assumption in this plan",
            "",
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
        "## The assumption in this plan",
        "",
        f"**{threads:,} users per engine is BlazeMeter's documented figure for an "
        f"engine of",
        f"this size, not a measurement of our test.** How many users one engine "
        f"really",
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
    return [
        "## The BlazeMeter side, so the cluster is actually used",
        "",
        "Four fields on the private location, under **Settings → Private "
        "Locations**:",
        "",
        "| field | value | why |",
        "|---|---|---|",
        f"| Slots | `{loc['slots']}` | engines this location may run at once — "
        f"below this the test cannot reach {p['users']:,} users |",
        f"| Threads per engine | `{loc['threads_per_engine']}` | unset, every test "
        f"start fails with 403 *Not enough available resources* |",
        f"| overrideCPU | `{loc['override_cpu']}` | the engine pod's CPU "
        f"**request** |",
        f"| overrideMemory | `{loc['override_memory_mb']}` | the memory "
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
    ]
