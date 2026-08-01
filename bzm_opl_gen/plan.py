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

DOCUMENT_FILE = "capacity-request.md"


def supported_vus(cpu_millis, mem_bytes):
    """Threads an engine of this size can carry, scaled from BlazeMeter's own
    pairing of BASELINE_VUS threads with ENGINE_DEFAULT_CPU/_MEM.

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
    return max(int(BASELINE_VUS * ratio), 1)


def capacity_plan(users, vus_per_engine=None, engine_cpu=None,
                  engine_mem=None, engines_per_node=None, agents=None):
    """What `users` concurrent users needs, as numbers.

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
    users = _positive(users, "users")
    per_node = _positive(1 if engines_per_node is None else engines_per_node,
                         "engines_per_node")
    agents = _positive(1 if agents is None else agents, "agents")

    # Reuse generate's own parse-and-default so an engine size means exactly
    # what it will mean in the bundle, error message included. Before the
    # threads default, because that is now derived from it.
    cpu, mem = engine_size({"engine_cpu_limit": engine_cpu,
                            "engine_mem_limit": engine_mem})
    supported = supported_vus(cpu, mem)

    assumed = vus_per_engine is None
    vus = (supported if assumed
           else _positive(vus_per_engine, "vus_per_engine"))

    engines = math.ceil(users / vus)
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
        "users": users,
        "vus_per_engine": vus,
        "vus_per_engine_assumed": assumed,
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
        "warnings": _warnings(vus, supported, cpu, mem, per_node, engines),
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


def _warnings(vus, supported, cpu, mem, per_node, engines):
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
    if vus > supported:
        out.append(
            f"{vus} virtual users on a {format_cpu(cpu)} CPU / "
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
    assumed = p["vus_per_engine_assumed"]

    lines = [
        "# Infrastructure request: load testing",
        "",
        f"To run performance tests of up to **{p['users']:,} virtual users** from "
        f"our own",
        "Kubernetes cluster, using a BlazeMeter private location — the load",
        "generators run here, and only results leave.",
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
        "drives a share of the virtual users:",
        "",
        "```",
        f"{p['users']:>7,} virtual users",
        f"{p['vus_per_engine']:>7,} virtual users per engine"
        + ("   (assumed -- see below)" if assumed else "   (supplied)"),
        f"{'-' * 7}",
        f"{p['engines']:>7} engines, running at the same time"
        f"   ({p['users']:,} / {p['vus_per_engine']:,}, rounded up)",
        f"{p['agents']:>7} agent(s) to run them",
        f"{'-' * 7}",
        f"{p['engines_per_agent']:>7} engines per agent"
        + ("   (the location's `slots`)" if p["agents"] == 1
           else f"   ({p['engines']} / {p['agents']}, rounded up -- the "
                f"location's `slots`)"),
        f"{p['engines_per_node']:>7} engine(s) per node",
        f"{'-' * 7}",
        f"{p['nodes_per_agent']:>7} nodes per agent"
        + ("" if p["agents"] == 1 else f", {p['nodes']} in total"),
        "```",
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


def _size_vs_baseline(p):
    """This engine against the baseline one, as words rather than a decimal.

    "half" and "twice" are what a reader checks the arithmetic with; "0.5x"
    invites them to wonder what was rounded.
    """
    r = p["vus_per_engine"] / BASELINE_VUS
    return {0.25: "a quarter of that size", 0.5: "half that size",
            2.0: "twice that size", 4.0: "four times that size"}.get(
        r, f"{r:g}x that size")


def _assumption_section(p):
    """The users-per-engine assumption, stated where it cannot be read past.

    It is the one input the whole document multiplies by, and the only one
    nothing here can verify. A reader who takes the node count and skips this
    is the failure mode; a reader who disagrees with the figure and re-runs the
    plan is the success.
    """
    threads = p["vus_per_engine"]
    if not p["vus_per_engine_assumed"]:
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
        f"**{threads:,} users per engine is what an engine of this size is rated "
        f"for, not a",
        f"measurement of our test.**"
        + (f" {BASELINE_VUS:,} is BlazeMeter's figure for a"
           if p["vus_per_engine"] != BASELINE_VUS
           else " It is BlazeMeter's own figure for that size."),
        (f"{ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM} engine, and this one "
         f"is {_size_vs_baseline(p)}."
         if p["vus_per_engine"] != BASELINE_VUS else ""),
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
        f"| Engines per agent (`slots`) | `{loc['slots']}` | what **one** agent "
        f"may run at once, so this location's total is "
        f"{p['agents']} x {loc['slots']} = {p['agents'] * loc['slots']} engines "
        f"— below that the test cannot reach {p['users']:,} virtual users |",
        f"| Virtual users per engine (`threadsPerEngine`) | "
        f"`{loc['threads_per_engine']}` | unset, every test start fails with 403 "
        f"*Not enough available resources* |",
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
