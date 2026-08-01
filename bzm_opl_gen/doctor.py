"""Pre-flight: can this cluster actually run the location's concurrency?

`generate` renders manifests that apply cleanly; that says nothing about
whether an engine can be *scheduled*. A location with slots=5 and the
documented 2 CPU / 8Gi engine needs 10 CPU and 40Gi of schedulable capacity,
plus quota, plus a LimitRange that does not fight the sizing, plus admission
that accepts the engine pods crane spawns. When any of that is missing the
customer sees a test that never starts -- no manifest error, no crane error,
just a run stuck in "initializing".

Every check here is a pure function over already-fetched data (`Check` list),
so the whole doctor is testable offline; the only impure parts are
gather_cluster() / probe_egress(), which are thin. That data can equally come
from an evidence file collected on a cluster nobody here can reach
(cluster_from_evidence, the twin of facts.manual()), and no check can tell the
difference -- it is the same shape either way. evaluate() returns the verdicts,
run() prints them.

FAIL = a test would not start. WARN = the numbers are wrong or it will bite
later, but a test still starts.
"""

import collections
import functools
import json
import os
import subprocess

from . import livetest
from . import plan
# Aliased because every check takes a `facts` argument, which takes the name.
from . import facts as facts_mod
# Same reason: evaluate(), run() and half of core take an `evidence` argument.
# This is the module that states what is *in* one -- the section names, which
# used to be spelled out at each of the four places that read them.
from . import evidence as evidence_mod
from .api import (API_BASE, DEFAULT_THREADS_PER_ENGINE,
                  ENGINE_UPLOAD_HOSTS)
from .generate import (CRANE_CPU_LIMIT, CRANE_CPU_REQUEST, CRANE_MEM_LIMIT,
                       CRANE_MEM_REQUEST, DEFAULT_OPTIONS,
                       ENGINE_DEFAULT_CPU, ENGINE_DEFAULT_MEM, ENGINE_DISK_GB,
                       ENGINE_TMP_GB,
                       ENGINE_STAMPED_REQUEST_CPU, ENGINE_STAMPED_REQUEST_MEM,
                       NODEPOOLS_FILE, SV_INGRESS_BACKENDS, SV_INGRESS_NONE,
                       TYPICAL_SYSTEM_PODS, crane_scheduling, engine_requests,
                       engines_per_node,
                       engine_scheduling, engine_size, proxy_env,
                       separate_pools, service_account)
from .quantity import (format_cpu, format_memory, human_memory, parse_cpu,
                       parse_memory)

Check = collections.namedtuple("Check", "name status detail")
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

GB = 10 ** 9                     # the docs quote decimal GB, not GiB

API_PROBE_URL = f"{API_BASE}/web/version"
# Engines upload results and artifacts to hosts crane itself never contacts, so
# an egress rule shaped around crane alone passes here and still fails a run.
# Same hosts livetest looks for in the proxy log -- one list, not two.
ENGINE_PROBE_URLS = tuple(f"https://{h}/" for h in ENGINE_UPLOAD_HOSTS)
CURL_IMAGE = "curlimages/curl:8.11.1"


def has_failures(checks):
    return any(c.status == FAIL for c in checks)


# What a FAIL in that list costs, in the words the report ends on. A constant
# because two surfaces state it and one of them is not this process: the web UI
# puts the same sentence beside the imported file's name.
NO_TEST_WOULD_START = "a test would not start on this location as configured"


def summary_line(checks):
    """The verdict list in one sentence: the counts, and what a failure means.

    The consequence is stated only where something FAILed. An evidence file
    whose collector was refused half the cluster is all warnings, and ending
    that with "a test would not start" turns a thin read into a rejection of a
    cluster nobody has judged -- which is the same rule `has_failures` keeps,
    and it is kept once, here. The browser used to compose its own line from
    the same counts, including this rule.
    """
    counts = collections.Counter(c.status for c in checks)
    line = (f"{counts[PASS]} passed, {_plural(counts[WARN], 'warning')}, "
            + (_plural(counts[FAIL], "failure") if counts[FAIL]
               else "no failures"))
    return f"{line} — {NO_TEST_WOULD_START}" if counts[FAIL] else line


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _unread_section(cluster, key, name, detail):
    """The branch every check that reads a cluster section opens with.

    A section is None when nobody could look -- a denied `list nodes`, an API
    server that does not serve the kind, or an evidence file whose collector was
    refused it. It is never None for "we looked and there are none": that
    arrives as [] or {}, and the two get opposite verdicts. An unread section is
    a WARN and exits 0; an empty one can be the FAIL that "no eligible node" or
    "no IngressClass named nginx" is, and claiming either off a read we were
    denied is a claim nothing here can stand behind.

    Returns the verdict to hand straight back, or None to carry on. `detail`
    stays the caller's, because what an unread section costs is specific to the
    question being asked of it -- only the branch is shared.

    Every check reaches this through its own @reads declaration now, so the
    seam below is the only caller: a check that reads a new section gets the
    branch by naming its section, and cannot get it by remembering the rule.
    """
    if cluster.get(key) is None:
        return [Check(name, WARN, detail)]
    return None


class MissingSection(LookupError):
    """A check was handed cluster data with no key at all for a section it reads.

    Absent is not a third answer to "what is in this section". Both producers --
    gather_cluster() and cluster_from_evidence() -- carry every key always, null
    for a section nobody could read and a value for one that was read, so a
    mapping missing the key is a caller that has not said which of the two it
    means. `.get()` picks "unread" for it silently, and that WARN is
    indistinguishable from an honest one: it is how thirty-six partial test
    fixtures could each have been putting a question to a check that the check
    never answered, with a pinned count of WARNs as the only thing noticing.

    Loud, therefore, and at the call site rather than in the report.
    """


# What a check declares about a cluster section it reads.
#
# `name`/`unread` are the verdict to give when the section is null. Both may be
# None: that is a check whose unread case is not its own to report -- either
# another check already owns that verdict (check_resourcequota's second read of
# `limitranges`) or the section cannot express the difference in the first place
# (check_egress's probes). The key is still declared, because presence is
# checked for every declaration and reading a section undeclared is the thing
# this exists to stop.
#
# `when` is a predicate over the options, for a section only read when the
# question arises at all -- crane's own pool on a split bundle, an
# IngressClass for a virtual service. It gates the whole declaration: a section
# a check will not look at need not be there, and an unread one costs nothing.
Section = collections.namedtuple("Section", "key name unread when")


def reads(key, name=None, unread=None, when=None):
    """Declare a cluster section a check reads, and what an unread one costs.

    The rule this exists to make structural is the one broken most often here:
    an unread section (None -- denied, not served, trimmed out of an evidence
    file) and an empty one ([] or {}) are opposite answers, and a check body
    that forgets the difference turns a read somebody was refused into a FAIL
    about a cluster nobody described. A declared check never gets the chance:
    the wrapper answers the unread case from this declaration and the body is
    only ever called with a section that was actually read.

    The declaration travels with the check rather than being applied by the
    loop, so a direct call -- which is how most of the tests here reach a check
    -- is held to the same contract as evaluate(). A seam only the loop went
    through would leave every other caller free to ask a check a question it
    was never given.

    `unread` is the check's own sentence, not a generic one, because what an
    unread section costs is specific to the question being asked of it -- only
    the branch is shared. It may be a callable over (facts, opts) where the
    sentence names the location's own numbers ("slots=2 x 2 CPU / 8Gi"), which
    is what makes it actionable; never over the cluster, which is the thing that
    was not read.

    Stack the decorator for a check that reads two sections. Undeclared checks
    are run unchanged: a check that reads nothing from the cluster is right not
    to declare, and says so where it is defined.
    """
    def declare(check):
        section = Section(key, name, unread, when)
        if getattr(check, "sections", None) is not None:
            # Already wrapped by a decorator below this one; one wrapper is
            # enough, and source order is the order they are answered in.
            check.sections = (section,) + check.sections
            return check

        @functools.wraps(check)
        def declared(facts, opts, cluster):
            for s in declared.sections:
                verdict = _declared_verdict(s, declared, facts, opts, cluster)
                if verdict is not None:
                    return verdict
            return check(facts, opts, cluster)

        declared.sections = (section,)
        return declared
    return declare


def _declared_verdict(section, check, facts, opts, cluster):
    """The verdict a declaration answers with by itself, or None to run the body.

    Three outcomes, and the middle one is the point: a section the check does
    not read here (`when`), a section the cluster data has no key for at all
    (MissingSection -- see it), and a section that was read (carry on, possibly
    after the unread WARN).
    """
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
    if section.unread is None:
        return None                   # not this check's verdict to give
    detail = (section.unread(facts, opts) if callable(section.unread)
              else section.unread)
    return _unread_section(cluster, section.key, section.name, detail)


def defers_to(*owners):
    """Declare the checks whose verdicts this one returns [] rather than restate.

    Two checks here go quiet because an earlier one has already reported the
    thing they would have said -- threadsPerEngine unset, an eligible node set
    that is empty. That was true only by where they sat in CHECKS, which is a
    fact about a tuple rather than about either check; _ordered() below turns it
    into something that fails at import when the tuple is reshuffled.

    Not a dependency graph, and it should not grow into one: it records the two
    places a verdict is deliberately left to somebody else. A check that goes
    quiet because the question does not arise (no split pools, no virtual
    service, the bundle brings its own ServiceAccount) is not deferring to
    anything and declares nothing.
    """
    def declare(check):
        check.defers = tuple(owners)
        return check
    return declare


def _ordered(checks):
    """CHECKS, with every declared deference met by the order it is written in.

    Import-time, because a reordering that silences a check is invisible in the
    report: the verdict the quiet one was counting on simply never appears.
    """
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
    """One check's verdicts. A pass-through, and that is the finished shape.

    The declaration used to be read here, which made this the one place a check
    got what it was promised -- and left every caller that did not come through
    it, most of this project's tests among them, free to hand a check anything
    at all. Enforcing it on the check instead makes those two the same call, and
    leaves nothing for a loop to do. Kept as the name evaluate() runs a check
    under, so a caller running one on its own has the same thing to say.
    """
    return check(facts, opts, cluster)


# -- location -----------------------------------------------------------------
#
# The three checks in this section read no cluster section at all -- they judge
# the location's own settings against the bundle's -- so they declare nothing,
# and that is a decision rather than an omission. A @reads on any of them would
# be a claim about data none of them touches, and would make a cluster mapping
# a fixture has to carry to ask a question about an account.

def check_location(facts, opts, cluster):
    """The two fields BlazeMeter itself needs before it will hand a run to this
    location.

    Both come from the account, so both are None on manually-entered facts --
    the same None a real location with them unset produces, and only that second
    case is a misconfiguration. The value cannot tell them apart; the marker the
    facts already carry for how they arrived can, and it is read here rather
    than folded into the facts, so nothing that generates learns the difference.
    A typed 0 is still a FAIL: that is a value someone supplied.
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
        # "Engines per agent" in BlazeMeter's UI: a location's concurrency is
        # agents x slots. check_capacity measures one cluster, which is one
        # agent, so slots is the right number to size it against.
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
        # A location created via the API has this null (POST ignores it), and
        # every start then fails 403 "Not enough available resources" -- with no
        # hint that a scalar field is the reason.
        checks.append(Check("location threadsPerEngine", FAIL,
                            "threadsPerEngine is unset -- every test start fails "
                            "with 403 'Not enough available resources'. Set it in "
                            "Settings -> Private Locations"))
    else:
        checks.append(Check("location threadsPerEngine", PASS, f"{tpe} threads"))
    return checks


@defers_to(check_location)
def check_threads_per_engine(facts, opts, cluster):
    """Threads the location promises per engine vs what the engine is sized for.

    The ratio itself is plan.supported_vus: BlazeMeter's own default pairs
    500 threads with a 2 CPU / 8Gi engine, scaled linearly on whichever of the
    two dimensions is tighter. 500 threads on a 1 CPU / 4Gi engine is not a
    runnable location, it is one that OOM-kills or throttles halfway up the
    ramp. `plan` sizes a cluster *from* that ratio where this judges a location
    against it, and the two answering differently would be the planner
    recommending what the preflight then warns about.
    """
    tpe = facts.get("threads_per_engine")
    if not tpe:
        return []                     # check_location has already reported it
    cpu, mem = engine_size(opts)
    supported = plan.supported_vus(cpu, mem)
    size = _engine_str(cpu, mem)
    if tpe > supported:
        return [Check("threadsPerEngine vs engine size", WARN,
                      f"{tpe} threads on a {size} engine; that size supports about "
                      f"{supported} ({DEFAULT_THREADS_PER_ENGINE} threads per "
                      f"{ENGINE_DEFAULT_CPU} CPU / {ENGINE_DEFAULT_MEM}). Lower "
                      f"threadsPerEngine or raise the engine limits")]
    return [Check("threadsPerEngine vs engine size", PASS,
                  f"{tpe} threads on a {size} engine (supports ~{supported})")]


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
    """Nodes a pod could actually land on: Ready, uncordoned, matching the
    nodeSelector, and with every blocking taint tolerated.

    `placement` is a (selector, tolerations) pair -- engine_scheduling(opts) or
    crane_scheduling(opts). It defaults to the engines' placement because every
    caller here is asking about engines; crane's pod is one, theirs are the ones
    that fail to schedule. On a two-pool location the two answers are different
    sets of nodes, so a caller that means crane has to say so.

    PreferNoSchedule is a preference, not a rejection, so it does not exclude.
    """
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
    """(cpu_millicores, mem_bytes) the node advertises as schedulable. Not what
    is free -- that needs every pod's requests summed per node, a much bigger
    read than a preflight should do."""
    alloc = node.get("status", {}).get("allocatable", {})
    return parse_cpu(alloc.get("cpu", "0")), parse_memory(alloc.get("memory", "0"))


def _engine_str(cpu, mem):
    return f"{format_cpu(cpu)} CPU / {format_memory(mem)}"


def _scope(opts, placement=None):
    """How the eligible-node set was narrowed, for a detail string. Defaults to
    the engines' placement, matching eligible_nodes()."""
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
    """The crane pool can hold crane.

    Only asked when the pools are split, because otherwise check_capacity
    already spends crane's share out of the one set of nodes it measures. Split,
    nothing was checking the crane side at all: every capacity check here is
    about engines, and a crane pool too small to run crane fails in the way that
    is hardest to attribute -- the agent goes offline mid-run, and BlazeMeter
    reports a test that stopped.

    The case that motivated it is not exotic. `e2-medium` is the obvious choice
    for "small always-on node" and reports **940m** allocatable CPU: crane
    schedules on its 250m request and can never reach its 1 CPU limit, so it is
    throttled exactly when a run makes it busy.
    """
    # The `when` on the declaration above is this same condition: a bundle with
    # one pool asks nothing of the nodes here, so an unread `nodes` costs it
    # nothing either, and the section is not required to be present.
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
    """What an unread `nodes` costs the capacity question, in the location's own
    numbers -- which is what makes it something to act on rather than a note
    that a read failed. Composed from facts and options only: the cluster is the
    thing that was not read."""
    slots = facts.get("slots") or 1
    want = _engine_str(*engine_size(opts))
    return (f"the cluster's nodes could not be read, so nothing here knows "
            f"whether slots={slots} x {want} can be scheduled. Needs a role "
            f"that can list nodes")


@reads("nodes", "capacity", _capacity_unread)
def check_capacity(facts, opts, cluster):
    """slots x engine size vs what the eligible nodes can hold.

    Two checks, because they fail differently: a pod is not splittable across
    nodes, so 'the cluster has 40Gi free' does not mean an 8Gi engine fits
    anywhere.
    """
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    want = _engine_str(cpu, mem)
    nodes = eligible_nodes(cluster["nodes"], opts)
    if not nodes:
        if separate_pools(opts):
            # An engine pool aimed at its own nodes is *supposed* to sit at zero
            # between runs -- that is the saving the split exists for. From
            # `get nodes` alone an empty autoscaling pool and a pool that was
            # never created look identical, and they are opposite verdicts, so
            # this cannot be the FAIL it is on a single-pool cluster. Observed
            # on a correctly-built GKE pool at min-nodes 0, where the old FAIL
            # said "engines have nowhere to run" about a cluster that was right.
            #
            # The cluster-autoscaler does publish its node groups
            # (kube-system/cluster-autoscaler-status), but names them by
            # instance-group URL and carries none of their labels, so it cannot
            # settle which group answers this selector either. Hence a WARN that
            # says what to look at rather than a guess in either direction.
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

    # Crane's own pod is spent out of the engine pool only when it is *on* it.
    # With engines pointed at their own pool crane is somewhere else entirely,
    # and charging the engine pool for it understates the capacity by a whole
    # crane -- which on a small pool is the difference between a PASS and a FAIL
    # that sends someone resizing nodes they did not need to touch.
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

# The engine sizing model, calibrated entirely from the one configuration
# BlazeMeter documents: 500 threads on 2 CPU / 8Gi with a 4096MB heap.
#
#   HEAP_MB_PER_THREAD   4096 / 500  = 8.192
#   CONTAINER_HEAP_RATIO 8Gi  / 4096 = 2.0
#
# Neither number is invented, and that is the whole reason they are these two
# rather than a physically explicit heap + threads*stack + overhead model: the
# stack and overhead terms of that model would have been guesses wearing the
# costume of a measurement.
#
# Note what the documented 500 actually tracks. 500 * 8.192MB is exactly the
# 4096MB *heap*, not the 8Gi container -- so the heap is the unit of capacity
# and the container is derived from it, not the reverse. This check previously
# compared the heap against a flat 75% of the container limit, which fired on
# BlazeMeter's own default pairing (4096MB in 8Gi is exactly half) while saying
# nothing about a 4096MB heap on a 50-thread location, where it is ten times
# oversized. The threads are the signal; the ratio never was.
#
# What these constants are NOT: a measurement of what an engine needs.
#
# BlazeMeter's "2 CPU / 8Gi" is a system *requirement* in the dumbed-down sense
# -- a floor chosen so that things work consistently across every customer and
# every script, not a figure anyone optimised. The 2.0 ratio is round because it
# is somebody's rule of thumb. So both constants encode a safety margin of
# unknown size, and scaling them linearly carries that margin to every thread
# count rather than removing it.
#
# That makes this model safe by construction and no better: a recommendation it
# produces is exactly as conservative as BlazeMeter's own default, proportionally
# -- which is the right *default* to ship, because it cannot be less safe than
# what the vendor already tells people to run. It is also why the numbers here
# cannot deliver an optimised cluster on their own. Getting below the vendor's
# margin needs observed usage, which is what the calibration loop in #89 is for;
# until that exists, treat every value this produces as an upper bound that
# happens to be defensible, not as a measured requirement.
#
# Anyone revising these should record what they measured, on what, right here.
HEAP_MB_PER_THREAD = 8.192
CONTAINER_HEAP_RATIO = 2.0

# The floor, and it is not an edge case -- it is very nearly the whole answer.
#
# Measured by bisecting a real engine's container limit until it broke, on a
# Docker agent with a light script (small HTML, 1s think-time). Verdict is the
# Taurus exit code, because nothing else distinguishes the cases:
#
#   threads  limit   result
#     300    1024MB  never starts -- JVM cannot initialise
#     300    1536MB  starts, dies as the ramp completes   (1,139 samples)
#     300    2048MB  starts, dies as the ramp completes   (2,435 samples)
#     300    2560MB  starts, dies halfway                (31,130 samples)
#     300    3072MB  runs the whole test                 (61,348 samples)
#     300    4096MB  runs the whole test                 (61,139 samples)
#      50    1536MB  starts, dies partway                 (1,926 samples)
#      50    2560MB  starts, dies partway                 (6,019 samples)
#
# Two things fall out, and both contradict the model above.
#
# **The requirement is essentially fixed.** 50 threads and 300 threads have the
# same floor, 2560 < floor <= 3072. Six times the load, no measurable change:
# what costs the memory is the JVM, Taurus and JMeter existing at all, not the
# threads. So `threads * HEAP_MB_PER_THREAD` has the wrong shape -- it is a
# large constant with a small per-thread term, and this "floor" is doing nearly
# all the work across the range anyone runs. Restructuring it needs more than
# two thread counts on one script against one target; see #89.
#
# **Above the floor, more memory buys nothing.** 3072 and 4096 are
# indistinguishable (61,348 vs 61,139 samples). It is a knee, not a slope, so
# the right recommendation sits just above it and paying for headroom is waste.
#
# 3072 is the smallest measured pass. The previous 1536 was set from an engine
# *consuming* 1220MB, and consumption is not a requirement -- 1536 fails at both
# thread counts. That mistake has now been made three times in this file's
# history; a reading of what an engine used is never a floor for what it needs.
MIN_HEAP_MB = 256
MIN_CONTAINER_MB = 3072


def engine_heap_mb(threads):
    """The heap a JVM needs to carry `threads` of load, in MB."""
    return max(int(threads * HEAP_MB_PER_THREAD), MIN_HEAP_MB)


def engine_container_mb(heap_mb):
    """The container the heap has to live in, in MB -- heap plus what the JVM
    needs outside it (metaspace, thread stacks, code cache, direct buffers, and
    the GC's own structures)."""
    return max(int(heap_mb * CONTAINER_HEAP_RATIO), MIN_CONTAINER_MB)


def check_engine_heap(facts, opts, cluster):
    """The location's JVM heap against the threads it must carry, and against
    the container the bundle gives it.

    Two comparisons, because they fail differently and only one of them was
    being made before:

    * **heap vs threads.** The heap is what runs the load, and what it must
      hold scales with `threadsPerEngine`. A heap short of the threads OOMKills
      mid-run; a heap far over them reserves node capacity the JVM can never
      address, which on a dedicated autoscaling engine pool is most of what the
      pool costs. Both are invisible to a scheduler, so nothing else here sees
      them. This is the comparison that was missing: 4096MB is right for 500
      threads and ten times too much for 50, and a ratio against the container
      cannot tell those apart.
    * **heap vs container.** Whatever the heap is, the JVM needs room outside
      it. A heap at or above the whole limit is an OOMKill reported as a test
      that stopped rather than as a resource error.

    The heap and the threads are *location* settings and the limit is a bundle
    option, so this is where the two sources of truth for engine size meet.
    Unknown heap is a WARN naming where to look, never a pass: the default is
    4096MB on almost every location, and the one that has been retuned is
    exactly the one somebody is generating a bundle for.
    """
    xmx = facts.get("engine_xmx_mb")
    threads = facts.get("threads_per_engine")
    _, mem = engine_size(opts)
    limit = format_memory(mem)
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
    # Fits in its box at all? Independent of the threads, and the loudest way
    # to be wrong, so it is asked first.
    if heap >= mem:
        return [Check("engine heap", FAIL,
                      f"engineXmx={xmx}MB against a {limit} container limit: the "
                      f"heap is at or above the whole limit, so the JVM is "
                      f"OOMKilled once it fills -- mid-run, reported as a test "
                      f"that stopped rather than a resource error. Raise "
                      f"engine_mem_limit to at least "
                      f"{engine_container_mb(xmx)}MB, or lower the heap")]

    if not threads:
        # check_location has already FAILed on an unset threadsPerEngine; the
        # sizing comparison simply cannot be made, and saying so beats implying
        # the heap was checked against the load.
        return [Check("engine heap", WARN,
                      f"engineXmx={xmx}MB fits the {limit} limit, but "
                      f"threadsPerEngine is unset, so whether the heap matches "
                      f"the load it must carry is unverified")]

    want_heap = engine_heap_mb(threads)
    want_container = engine_container_mb(xmx)
    pair = f"engineXmx={xmx}MB for {threads} threads"
    # A factor either way before complaining: these constants come from a single
    # vendor data point, so a verdict on a 10% difference would be false
    # precision.
    if xmx < want_heap / 1.5:
        # WARN, not FAIL, and the wording says why: this rests on
        # HEAP_MB_PER_THREAD, whose *shape* the bisection refuted -- 50 and 300
        # threads measured the same requirement, so per-thread scaling is not
        # how this behaves in the range we have data for. Above that range it
        # may well be right, but "may well be" does not earn a non-zero exit.
        # The heap-exceeds-the-limit FAIL above is untouched: that one is
        # arithmetic, not a model.
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
                      f"range is unverified (#89)")]
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
    """Whether the crane pod could also land on the engine pool. Asked of the
    already-filtered engine nodes, so it answers about the overlap rather than
    about the cluster: two pools configured separately may still both accept
    crane, and then its share really is spent out of this set."""
    return bool(eligible_nodes(engine_nodes, opts, crane_scheduling(opts)))


def _pod_ceiling(node):
    """`allocatable.pods`, the kubelet's maxPods as the node reports it. Absent
    on a node whose status was trimmed, which is not zero -- None says so."""
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
    """How many engines the scheduler will put on one node, versus how many
    can actually run there.

    Both the scheduler and the cluster autoscaler place pods by *requests*, so
    a node with room for one engine's limits is offered as many as its requests
    divide into it. What sets those requests is the **location's** overrideCPU
    and overrideMemory, not this bundle and not anything in these manifests --
    confirmed live, where a location at 1 CPU / 4096MB against a bundle asking
    2 CPU / 8Gi produced requests {1, 4Gi} and limits {2, 8Gi} on the same pod.
    Unset, they default to 250m/256Mi, which is how nearly every location runs
    and why engines pack.

    So the fix this check points at is the location's overrides; the engine
    pool's maxPods is the backstop for when they are not set. `allocatable.pods`
    is how the node reports that ceiling.

    WARN, never FAIL: the engines do start, and a small test may never notice.
    What it costs is the validity of the numbers -- engines throttling against
    each other report the load generator's latency, not the system's.

    The `nodes` it reads are declared above, so an unread section is answered
    before this body runs and `[]` here is only ever "we looked and there are
    none".
    """
    nodes = eligible_nodes(cluster["nodes"], opts)
    if not nodes:
        return []            # check_capacity already FAILed on the empty set
    cpu, mem = engine_size(opts)
    # The requests the engine will really carry, from the location -- not the
    # default, which is only what an unset location produces.
    req_cpu_s, req_mem_s = engine_requests(facts)
    req_cpu, req_mem = parse_cpu(req_cpu_s), parse_memory(req_mem_s)
    overridden = bool(facts.get("override_cpu") or facts.get("override_memory"))

    worst = None
    for n in nodes:
        alloc_cpu, alloc_mem = _allocatable(n)
        # What the scheduler will accept, by the requests the pod carries.
        by_req = min(alloc_cpu // req_cpu, alloc_mem // req_mem)
        # ...capped by the node's own pod ceiling, less the DaemonSets that land
        # on every node. That subtraction is an assumption, not a measurement:
        # `allocatable.pods` counts all pods and nothing here collects
        # DaemonSets, so the number is named in the verdict for the reader to
        # correct rather than buried. TYPICAL_SYSTEM_PODS is the same constant
        # the generated recipe sizes maxPods from, so a pool built to that
        # recipe is judged by the arithmetic that produced it. It counts what
        # actually lands on a tainted node, which is not the DaemonSet count --
        # see the constant.
        ceiling = _pod_ceiling(n)
        if ceiling is not None:
            by_req = min(by_req, max(ceiling - TYPICAL_SYSTEM_PODS, 1))
        # What can actually run: the limits the engine was configured with,
        # but never counted above what the pool was *designed* to hold. A node
        # sized for 2 engines is not over-packed by taking 2, and judging it
        # against its raw capacity would WARN on a pool built exactly to spec.
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
                      f"spec.nodeName=<NODE>`")]

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
                  f"`kubectl get pods -A --field-selector spec.nodeName=<NODE>`, "
                  f"not `get ds`, which counts nodeAffinity-gated variants that "
                  f"never land. No manifest can set maxPods, so it belongs on "
                  f"the node pool (see the generated {NODEPOOLS_FILE})")]


@reads("nodes", "node disk",
       f"the cluster's nodes could not be read, so the {ENGINE_DISK_GB}GB per "
       f"engine is unverified")
def check_disk(facts, opts, cluster):
    """Ephemeral storage per eligible node against the documented engine
    footprint. WARN, not FAIL: a short run may never fill it -- but an engine
    that does gets evicted mid-test, which reads as a random failure."""
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
    """An existing LimitRange can reject the engine pod outright -- max below
    its limits, min above the requests crane stamps, or a maxLimitRequestRatio
    tighter than the gap between the two -- and can rewrite the resources of any
    pod in the namespace that declares none. Neither shows up in the manifests.

    The declaration above means `limitranges` here is a list that was read:
    empty is "the namespace caps nothing", which is this check's WARN, and never
    "we were refused it", which is the seam's."""
    limitranges = cluster["limitranges"]
    cpu, mem = engine_size(opts)
    if not limitranges:
        # Nothing caps the namespace, which is a note rather than a problem --
        # and separately the engines schedule small, which nothing here can
        # change. The generator used to offer a LimitRange for this and no
        # longer does: it could not fix the requests, and the defaults it did
        # apply landed on crane's helper pods.
        return [Check("limitrange", WARN,
                      f"no LimitRange in the namespace, so nothing caps what it "
                      f"may ask for. Separately, engine pods request "
                      f"{ENGINE_STAMPED_REQUEST_CPU}/{ENGINE_STAMPED_REQUEST_MEM} "
                      f"rather than {_engine_str(cpu, mem)} because crane sets "
                      f"that explicitly -- a LimitRange cannot override it")]

    # (field, parse, the engine's own value for it, how to show it)
    dims = (("cpu", parse_cpu, cpu, format_cpu, parse_cpu(ENGINE_STAMPED_REQUEST_CPU)),
            ("memory", parse_memory, mem, format_memory,
             parse_memory(ENGINE_STAMPED_REQUEST_MEM)))
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
                # min rejects from below just as max does from above, measured
                # against the requests crane stamps, not the engine's limits.
                mn = (item.get("min") or {}).get(key)
                if mn and parse(mn) > stamped:
                    blocking.append(f"min {key} {mn} > the {show(stamped)} "
                                    f"crane requests")
                # An engine's own limit/request ratio is large precisely because
                # crane requests so little of what it limits.
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


# Quota keys we can compare against an engine's claim. 'cpu'/'memory' are the
# API's aliases for the requests.* forms.
_QUOTA_CPU = ("requests.cpu", "limits.cpu", "cpu")
_QUOTA_MEM = ("requests.memory", "limits.memory", "memory")


def _quota_unread(facts, opts):
    slots = facts.get("slots") or 1
    return (f"the namespace's ResourceQuotas could not be read, so whether one "
            f"has room for slots={slots} is unverified")


@reads("quotas", "resourcequota", _quota_unread)
# The second read, and the reason it is declared without a verdict of its own:
# the last branch below distinguishes `limitranges == []` (read, the namespace
# has none) from null, and check_limitrange already reports the null. Declaring
# it says the section is read here, which is what stops a caller supplying
# quotas alone and getting an answer composed against a LimitRange list nobody
# looked at.
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
        # With a cpu/memory quota in force the API server rejects any pod that
        # does not declare that resource -- and crane sets no requests on the
        # engines it spawns, so something has to supply them.
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

    Our crane pod satisfies restricted PSA (runAsNonRoot, no privilege
    escalation, drop ALL, RuntimeDefault seccomp). The engines crane spawns are
    a different matter, and the one worth checking: their security context comes
    from KUBERNETES_SECURITY_CONTEXT_CAP_JSON and INHERIT_RUNNING_USER_AND_GROUP
    in the ConfigMap, not from anything in the Deployment, so a bundle can look
    entirely correct and still have crane create a privileged pod that
    admission refuses. That refusal lands after the agent is online and the
    location reads ready, which is why it is worth a preflight at all.

    Those envs are on by default on every platform now, so what this reads is
    the option, not the platform.
    """
    # Two different facts, and only one of them is answered by creating the
    # namespace. `{}` is a read that came back empty -- the live path's `get ns`
    # on a namespace that is not there yet, which is the ordinary preflight
    # case, and the only one that reaches this body. `None` is nobody having
    # looked, which today only an evidence file says: the collector records a
    # section it was refused as null, and telling its reader to create a
    # namespace they may well already have is advice about a problem they do not
    # have. That case is answered by the declaration above, before this runs.
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
        # This was a flat FAIL while the engine security envs were emitted only
        # for platform=openshift. They are on by default everywhere now, so the
        # verdict follows the option rather than the platform -- and turning
        # them off is exactly what puts this namespace back where it was.
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
    """Is the ServiceAccount the bundle references actually there?

    Only asked when the bundle does not create one: with
    `service_account_create` off, the Deployment and both binding subjects name
    an account somebody else owns, and if that name is wrong nothing errors --
    the Deployment applies, the ReplicaSet reports `serviceaccounts "x" not
    found` in an event, and the agent simply never appears. A preflight is the
    only place that is visible before someone waits on it.

    This is one of the two checks CLAUDE.md notes branch on falsiness rather
    than on null, and it still does, below -- but the two halves are now split
    where they belong. Null is the declaration's, like everywhere else. Empty
    stays this check's own judgement, and is the one section here where an empty
    read means the same thing as no read at all: see below.
    """
    if opts.get("service_account_create", True):
        return []                     # we create it; nothing to find
    name = service_account(opts)
    accounts = cluster["serviceaccounts"]
    # Every namespace that exists has at least `default`, so an empty list means
    # the namespace is missing or the read was filtered rather than the
    # namespace being genuinely accountless -- which is not a fact any cluster
    # produces. So this is the one place [] and null earn the same sentence, and
    # it is composed once for both rather than written out twice.
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

# Crane writes `ingressClassName: nginx` on the Ingress it creates per virtual
# service and BlazeMeter exposes no env to change it, so the name is ours to
# check, not to configure. It matches the `nginx` sv_ingress value only by
# coincidence -- keep the two apart so renaming either does not silently change
# which branch below runs.
CRANE_INGRESS_CLASS = "nginx"
OPENSHIFT_ROUTE_CONTROLLER = "openshift.io/ingress-to-route"


def _claims_an_ingress_class(opts):
    """Whether this bundle publishes an Ingress for something to claim at all.

    The three branches the body takes before it reaches the cluster -- no SV, a
    value generate() would have rejected, a backend that routes through its own
    CRD -- are all answers about the options, and none of them is improved by
    knowing whether IngressClasses could be read. So the declaration is gated on
    the one case that does read them.
    """
    backend = SV_INGRESS_BACKENDS.get(opts.get("sv_ingress"))
    return bool(backend and backend.via_ingress_class)


@reads("ingressclasses", "sv ingress class",
       f"IngressClasses could not be read, so the '{CRANE_INGRESS_CLASS}' class "
       f"crane requires is unverified",
       when=_claims_an_ingress_class)
def check_ingress_class(facts, opts, cluster):
    """Will anything claim the Ingress crane creates for a virtual service?

    With no IngressClass named `nginx` no controller adopts it, no route is
    created, and the endpoint BlazeMeter publishes returns 503 -- while the
    virtual service itself is healthy and serving in-cluster. Nothing in the
    deploy fails, so a preflight is the only place this is visible. On
    OpenShift the only shipped class is `openshift-default`, which makes that
    the default outcome there rather than an unlucky one.
    """
    ingress = opts.get("sv_ingress")
    if not ingress or ingress == SV_INGRESS_NONE:
        # Not an SV deployment -- either unconfigured, or configured for
        # performance alone -- so there is no Ingress to preflight. The two are
        # still different states everywhere it matters; here they genuinely
        # share an answer, because neither publishes an object.
        return []
    backend = SV_INGRESS_BACKENDS.get(ingress)
    if backend is None:
        # generate() rejects anything outside SV_INGRESS_TYPES, so this only
        # shows up for a hand-written profile. Say so rather than checking a
        # class name that such a deployment may never ask for.
        known = "', '".join(SV_INGRESS_BACKENDS)
        return [Check("sv ingress class", WARN,
                      f"unrecognised sv_ingress={ingress}; expected one of "
                      f"'{known}', so the ingress path is unverified")]
    if not backend.via_ingress_class:
        # Verified on Istio 1.30 and Contour v1.33: neither registers an
        # IngressClass, so treating "none found" as a failure here would fail
        # every correctly-installed cluster of both.
        return [Check("sv ingress class", PASS,
                      f"sv_ingress={ingress} routes through the "
                      f"{backend.creates} crane creates, not an IngressClass")]

    # An API server that does not serve the kind is the declaration's, above;
    # what reaches here is a list, and an empty one is the FAIL below.
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
    # Only under CLUSTERIP. Crane's Ingress backend writes a constant 8080 and
    # this controller resolves it against spec.ports[].port -- which is 80 under
    # CLUSTERIP (mismatch, no Route) and 8080 under NODEPORT (a match, so the
    # defect does not arise). Saying it unconditionally would tell a NODEPORT
    # customer their endpoint is broken when generate() just accepted it.
    if (controller == OPENSHIFT_ROUTE_CONTROLLER
            and opts.get("service_type", "CLUSTERIP") == "CLUSTERIP"):
        detail += (f"; note that this controller resolves the backend port "
                   f"against the Service's port 80 and crane writes 8080, so it "
                   f"reports IncompleteIngressToRouteRules and creates no Route "
                   f"(upstream defect -- see README)")
    return [Check("sv ingress class", PASS, detail)]


# -- egress -------------------------------------------------------------------

def egress_targets(opts):
    """What has to be reachable from inside the namespace before a run works."""
    targets = [API_PROBE_URL, *ENGINE_PROBE_URLS]
    reg = opts.get("private_registry")
    if reg:
        targets.append(f"https://{reg.split('/')[0]}/v2/")
    return targets


# Declared for presence, without an unread verdict of its own -- the other
# check CLAUDE.md notes branches on falsiness, and the branch stays here because
# probes is the one section where null and empty are genuinely the same answer.
# egress_targets() is never empty, so there is no "we probed and there was
# nothing to probe": {} is what an evidence file carries (a probe needs a pod in
# the namespace, which a collector must not create) and None is a caller that
# did not probe. Both are "not probed", and one sentence says so.
@reads("probes")
def check_egress(facts, opts, cluster):
    """Pure verdict over {target: curl returncode}; None = we could not probe."""
    probes = cluster["probes"]
    if not probes:
        # True of both ways in: no crane pod and no throwaway pod either, or an
        # evidence file, which cannot carry a probe -- it takes a pod in the
        # namespace to find out, and that is the one thing a collector script
        # must not create.
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
    """The `.items` of a kubectl List document, or None when the command failed.

    "served the kind, has none" and "could not ask" are different answers and
    the checks report them differently -- [] is a FAIL where we know nothing
    will claim crane's Ingress, None is a WARN because we did not look.
    `.get("items", [])` would collapse both to [], turning a namespace we were
    denied into a hard failure with a non-zero exit. kget reports a failed
    command as {}, which is the falsy case here.
    """
    return document.get("items", []) if document else None


def _split_by_kind(document):
    """One `get limitrange,resourcequota,serviceaccount` -> the three lists, or
    three Nones when the whole get failed. Nothing partial: the kinds come back
    from a single command, so it succeeded for all of them or none."""
    items = _items(document)
    if items is None:
        return None, None, None
    by_kind = {"LimitRange": [], "ResourceQuota": [], "ServiceAccount": []}
    for item in items:
        by_kind.setdefault(item.get("kind"), []).append(item)
    return by_kind["LimitRange"], by_kind["ResourceQuota"], by_kind["ServiceAccount"]


def gather_cluster(cli, namespace):
    """Everything the checks read, in as few API round trips as it takes.
    LimitRanges, ResourceQuotas and ServiceAccounts are all namespaced, so one
    `get` covers them; splitting the result by kind is cheaper than three."""
    limitranges, quotas, accounts = _split_by_kind(livetest.kget(
        cli, namespace, "limitrange,resourcequota,serviceaccount"))
    # IngressClass is cluster-scoped like nodes, but kept its own get: kget
    # reports a failed command as {}, so folding the kinds into one call would
    # lose the nodes too on a cluster whose API server does not serve it.
    return {
        "nodes": _items(livetest.kget(cli, None, "nodes")),
        "ingressclasses": _items(livetest.kget(cli, None, "ingressclass")),
        "limitranges": limitranges,
        "quotas": quotas,
        "serviceaccounts": accounts,
        "namespace": livetest.kget(cli, None, "ns", namespace),
    }


# -- evidence file ------------------------------------------------------------

# The file's own vocabulary, under the names every caller of this module already
# reads them by. What they say is stated with the rest of the document's shape,
# in `evidence`, so the collector and this reader cannot drift apart.
EVIDENCE_SCHEMA = evidence_mod.SCHEMA
EVIDENCE_SCRIPT = evidence_mod.SCRIPT

# What an import produces: cluster data in gather_cluster()'s shape, the probes
# it cannot supply, and the verdicts about the file itself.
Evidence = collections.namedtuple("Evidence", "cluster probes checks")


def load_evidence(path):
    """Read an evidence file. ValueError says what to do about a bad one --
    this is a file a customer mailed back, so every way it can be wrong is a
    message rather than a traceback."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ValueError(
            f"no cluster evidence file at '{path}'. Have someone with access to "
            f"the cluster run {EVIDENCE_SCRIPT} (read-only) and send back its "
            f"output:\n  ./{EVIDENCE_SCRIPT} -n <namespace> > cluster-evidence.json")
    except json.JSONDecodeError as e:
        raise ValueError(f"'{path}' is not valid JSON ({e}). It should be the "
                         f"unedited output of {EVIDENCE_SCRIPT}")
    except OSError as e:
        # A directory, an unreadable file, a dead symlink. Named rather than
        # raised: every caller of this turns a ValueError into a sentence
        # somebody can act on, and an IsADirectoryError traceback out of a
        # server is the one shape none of them expected.
        raise ValueError(f"'{path}' could not be read ({e}). It should be the "
                         f"file {EVIDENCE_SCRIPT} wrote on the customer's "
                         f"machine")


def cluster_from_evidence(doc, namespace=None):
    """Normalise an evidence file into what gather_cluster() returns.

    The cluster-side twin of `facts.manual()`, and the same rule applies: the
    result is exactly the shape the live path produces, so no check can tell
    which way the data arrived. Everything the file carries beyond that --
    when it was collected, which namespace for, what the script could not read
    -- comes back as Checks instead, because it qualifies the verdicts without
    being one of them.

    The `raw` sections are whole kubectl List documents, or null where the
    command failed; null stays null through here (see _items) so that "we did
    not look" cannot arrive looking like "there are none".
    """
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
        # Null stays null here too, and this section is the one where it costs
        # something to lose: `{}` is what the live path produces for a namespace
        # that is not there yet, and check_admission's answer to that is "create
        # it". A collector that was refused `get ns` said nothing of the kind.
        "namespace": _section(raw, evidence_mod.NAMESPACE),
    }
    # Egress needs something inside the namespace to curl from, so an evidence
    # file cannot carry it. {} is check_egress's "not probed" (WARN), which is
    # the honest answer -- never a PASS nobody stood behind.
    return Evidence(cluster, {}, _evidence_checks(doc, namespace))


def _section(raw, key):
    """One `raw` section: the kubectl document as collected, or None. Files come
    back by mail and are sometimes trimmed on the way, so anything else is named
    here rather than reaching a check as an AttributeError."""
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
                         f"Expected the output of {EVIDENCE_SCRIPT} "
                         f"(schema {EVIDENCE_SCHEMA})")
    schema = doc.get(evidence_mod.SCHEMA_FIELD)
    if not schema:
        raise ValueError(f"this file has no 'schema' field, so it is not cluster "
                         f"evidence -- expected {EVIDENCE_SCHEMA}, the output of "
                         f"{EVIDENCE_SCRIPT}. (Account facts go to --facts.)")
    if schema != EVIDENCE_SCHEMA:
        raise ValueError(f"unrecognised cluster evidence: found schema "
                         f"'{schema}', expected '{EVIDENCE_SCHEMA}'. Re-collect "
                         f"with the {EVIDENCE_SCRIPT} shipped with this version "
                         f"rather than trusting a partial read of a shape this "
                         f"doctor does not know")


def _evidence_checks(doc, namespace):
    """One verdict about the file itself: where these answers came from, how
    stale they are, whether they describe the namespace being preflighted, and
    anything the script was refused. Every verdict after it is only as good as
    this one, which is why it is a Check and not a printed aside."""
    collected = doc.get(evidence_mod.COLLECTED_AT) or "an unrecorded time"
    doc_ns = doc.get(evidence_mod.NAMESPACE)
    parts = [f"cluster read by {EVIDENCE_SCRIPT} at {collected} for namespace "
             f"{doc_ns or 'an unnamed namespace'}, not from a live cluster"]
    if describes_elsewhere(doc_ns, namespace):
        # Most of what follows is per-namespace -- LimitRanges, quotas,
        # ServiceAccounts, the PSA labels -- so evidence from another namespace
        # says little about this one. It still describes the same nodes, so this
        # reports rather than refuses.
        parts.append(f"but this preflight is for '{namespace}', so the "
                     f"namespaced verdicts below describe '{doc_ns}' "
                     f"instead: re-collect with -n {namespace}")
    if doc.get(evidence_mod.NOTES):
        parts.append(_unread(doc[evidence_mod.NOTES]))
    return [Check("cluster evidence", WARN if len(parts) > 1 else PASS,
                  "; ".join(parts))]


def _unread(notes):
    """The script's own errors, which are what explains every null below. It
    writes "<section>: <error>", and on a cluster nobody can reach that error is
    the same six times over -- so the sections are listed and the distinct
    reasons given once."""
    reasons = []
    for note in notes:
        reason = note.partition(": ")[2].strip()
        if reason and reason not in reasons:
            reasons.append(reason)
    why = " | ".join(reasons or notes)
    return (f"could not read {', '.join(unreadable_sections(notes))}, reported "
            f"below as unverified rather than as absent: {why[:300]}")


def unreadable_sections(notes):
    """Which sections the collector recorded as unreadable, in the order it
    wrote them. Names only -- the reasons are _unread's half."""
    sections = []
    for note in notes or []:
        section = str(note).partition(": ")[0]
        if section and section not in sections:
            sections.append(section)
    return sections


def describes_elsewhere(doc_ns, namespace):
    """Does this file describe a different namespace than the one being
    preflighted?

    A file that names none is not a mismatch: there is nothing to mismatch
    with, and a warning nobody can act on is one more line between the reader
    and the ones they can. Nor is a caller that named no namespace to compare
    against -- which is *not* the same as agreeing, and is why the summary
    keeps `namespace` beside this: false here means "nothing to report", and
    what the file recorded is still said in full.
    """
    return bool(namespace and doc_ns and namespace != doc_ns)


def evidence_summary(doc, namespace=None):
    """What the file says about itself, as data rather than as a sentence.

    The same facts _evidence_checks() states in prose, and it stays the one
    that judges them -- this is for a caller that renders a header instead of a
    verdict list. The web UI is that caller: three facts inside one verdict's
    prose, ten verdicts down a panel, is how a thin file passes for a clean
    bill of health (#53), and a browser re-deriving them by parsing that
    sentence would be a second opinion about the same file.

    `namespace` is the one being preflighted, and only the mismatch is about
    it. Comparing the two served fields instead was that second opinion in its
    shortest form -- the same comparison, one language away from the verdict
    that already makes it.
    """
    doc = doc if isinstance(doc, dict) else {}
    doc_ns = doc.get(evidence_mod.NAMESPACE) or None
    return {"collected_at": doc.get(evidence_mod.COLLECTED_AT) or None,
            "namespace": doc_ns,
            "elsewhere": describes_elsewhere(doc_ns, namespace),
            "unreadable": unreadable_sections(doc.get(evidence_mod.NOTES))}


def _ca_configured(opts):
    return bool(opts.get("ca_bundle") or opts.get("ca_existing_configmap")
                or opts.get("ca_openshift_inject"))


def _rc_lines(output, targets):
    """Parse the `<url> rc=<n>` lines one shell emitted for all the targets.
    A target with no line never ran -> None (unknown), never a FAIL."""
    rcs = {t: None for t in targets}
    for line in output.splitlines():
        url, _, rc = line.strip().partition(" rc=")
        if rc.strip().lstrip("-").isdigit() and url in rcs:
            rcs[url] = int(rc.strip())
    return rcs


def _curl_script(targets, cacert=False, settle=0):
    """One shell running every probe, so a doctor costs one exec (or one pod)
    rather than one per target -- each is a process spawn plus the API round
    trips to resolve and attach to a pod.

    Every probe is retried once: a freshly created pod can lose its first DNS
    lookup (curl rc=6) before CoreDNS answers for it, and a doctor that reports
    a FAIL it cannot reproduce is worse than one that says nothing. `settle`
    delays the first probe so `kubectl run -i` has finished attaching -- output
    written before that is simply dropped.
    """
    ca = ' --cacert "$REQUESTS_CA_BUNDLE"' if cacert else ""
    probe = (f"curl -s -o /dev/null --max-time 20{ca} %s || "
             f"{{ sleep 2; curl -s -o /dev/null --max-time 20{ca} %s; }}")
    lines = [f'{probe % (t, t)}; echo "{t} rc=$?"' for t in targets]
    return "; ".join(([f"sleep {settle}"] if settle else []) + lines)


def probe_egress(cli, namespace, opts):
    """curl each target from inside the cluster -> {target: returncode}.

    Preferably from the crane pod: it is the only place the profile's proxy env
    and CA bundle are actually in force, which is what the customer's egress
    depends on. A one-shot pod is the fallback, and cannot verify a corporate
    CA at all -- that reports None (WARN), never a FAIL we cannot stand behind.
    """
    targets = egress_targets(opts)
    if livetest.kget(cli, namespace, "deploy", "crane"):
        out = livetest._crane_exec(cli, namespace,
                                   _curl_script(targets, _ca_configured(opts)))
        return _rc_lines(out, targets)
    if _ca_configured(opts):
        return {t: None for t in targets}
    return _oneshot_curl(cli, namespace, targets, opts)


def _oneshot_curl(cli, namespace, targets, opts):
    """Probe from a single throwaway pod when crane is not deployed yet -- one
    image pull and schedule for all the targets, not one each."""
    env = [arg for name, value in proxy_env(opts).items()
           for arg in ("--env", f"{name}={value}")]
    print(f"  probing egress from a throwaway {CURL_IMAGE} pod in {namespace} "
          f"(crane is not deployed yet)")
    out = subprocess.run(
        [cli, "-n", namespace, "run", f"bzm-doctor-{os.getpid()}", "--rm", "-i",
         "--restart=Never", "--image", CURL_IMAGE, *env, "--command", "--",
         "sh", "-c", _curl_script(targets, settle=2)],
        capture_output=True, text=True)
    return _rc_lines(out.stdout, targets)


# Every check takes the same (facts, opts, cluster) so adding one is a single
# edit here, not a new argument order to remember.
#
# What each check reads is on the check (@reads), and what it leaves to an
# earlier one is too (@defers_to) -- _ordered() holds the tuple to the second at
# import. So this is a reading order rather than a contract: the three checks
# that declare nothing read nothing from the cluster, and every other one is
# handed only sections that were actually read.
CHECKS = _ordered((check_location, check_threads_per_engine, check_engine_heap,
                   check_crane_pool, check_capacity, check_engine_packing,
                   check_disk, check_limitrange, check_resourcequota,
                   check_admission, check_service_account, check_ingress_class,
                   check_egress))


def resolve_namespace(namespace, opts):
    """What the checks are asked about: the explicit namespace, else the one the
    bundle was generated for, else the documented default."""
    return (namespace or (opts or {}).get("namespace")
            or DEFAULT_OPTIONS["namespace"])


def evaluate(facts, opts, namespace, cluster_data=None, probes=None, cli=None,
             extra_checks=(), evidence=None):
    """Every verdict as data, and nothing printed.

    Split out of run() so a caller that is not a terminal -- the web UI -- gets
    the Check list without capturing stdout, and so importing cluster data can
    be tested against the live path by comparing whole lists.

    `extra_checks` are verdicts the caller reached before the cluster data
    existed at all: where it came from, whether it describes this namespace.
    They lead the list because they qualify everything after them.

    `evidence` is the three of them as the one thing they are -- what
    cluster_from_evidence() returns -- for the callers that have a file. The
    three parts stay spelled out for the live path and for tests that supply
    one and not the others, but a caller holding an Evidence should not have to
    take it apart and hope it lands back in the right slots.
    """
    if evidence is not None:
        # Two spellings of one thing, never both: layering extra_checks over an
        # imported file would silently drop one set or the other, and which is
        # not something a reader of the call site could tell.
        if cluster_data is not None or probes is not None or extra_checks:
            raise TypeError("pass evidence= or the three parts it carries "
                            "(cluster_data, probes, extra_checks), not both")
        cluster_data, probes, extra_checks = evidence
    opts = dict(opts or {})
    namespace = resolve_namespace(namespace, opts)
    if cluster_data is None or probes is None:
        cli = cli or livetest.cli_tool()
    if cluster_data is None:
        cluster_data = gather_cluster(cli, namespace)
    if probes is None:
        probes = probe_egress(cli, namespace, opts)

    cluster = {**cluster_data, "probes": probes}
    return list(extra_checks) + [c for check in CHECKS
                                 for c in run_check(check, facts, opts, cluster)]


def run(facts, opts, namespace, cluster_data=None, probes=None, cli=None,
        extra_checks=(), evidence=None):
    """Run every check and print the verdict list. Returns the Check list; the
    caller decides the exit code (see has_failures)."""
    checks = evaluate(facts, opts, namespace, cluster_data, probes, cli,
                      extra_checks, evidence)
    _report(checks, facts, resolve_namespace(namespace, opts))
    return checks


def _report(checks, facts, namespace):
    print(f"doctor: location {facts.get('harbor_name')} "
          f"({facts.get('harbor_id')}), namespace {namespace}")
    width = max((len(c.name) for c in checks), default=0)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
    print(summary_line(checks))
