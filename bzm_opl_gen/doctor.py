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
gather_cluster() / probe_egress(), which are thin.

FAIL = a test would not start. WARN = the numbers are wrong or it will bite
later, but a test still starts.
"""

import collections
import json
import os
import subprocess

from . import livetest
from .api import DEFAULT_THREADS_PER_ENGINE
from .generate import (CRANE_CPU_LIMIT, CRANE_MEM_LIMIT, ENGINE_DEFAULT_CPU,
                       ENGINE_DEFAULT_MEM, engine_size, proxy_url)
from .quantity import (format_cpu, format_memory, human_memory, parse_cpu,
                       parse_memory)

Check = collections.namedtuple("Check", "name status detail")
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# BlazeMeter's documented engine footprint on disk, per concurrent engine.
ENGINE_DISK_GB = 60
ENGINE_TMP_GB = 40
GB = 10 ** 9                     # the docs quote decimal GB, not GiB

# What crane stamps on the engine pods it spawns, explicitly -- an eighth of a
# real engine, and not something a LimitRange can override (defaultRequest only
# fills fields a pod leaves unset). Confirmed on a live run.
ENGINE_STAMPED_REQUEST_CPU = "250m"
ENGINE_STAMPED_REQUEST_MEM = "256Mi"

API_PROBE_URL = "https://a.blazemeter.com/api/v4/web/version"
# Engines upload results and artifacts to hosts crane itself never contacts, so
# an egress rule shaped around crane alone passes here and still fails a run.
ENGINE_PROBE_URLS = ("https://data.blazemeter.com/", "https://storage.blazemeter.com/")
CURL_IMAGE = "curlimages/curl:8.11.1"


def has_failures(checks):
    return any(c.status == FAIL for c in checks)


# -- location -----------------------------------------------------------------

def check_location(facts):
    """The two fields BlazeMeter itself needs before it will hand a run to this
    location."""
    checks = []
    slots = facts.get("slots")
    if not slots:
        checks.append(Check("location slots", FAIL,
                            "the location advertises no slots -- BlazeMeter has "
                            "nowhere to place a run"))
    else:
        checks.append(Check("location slots", PASS,
                            f"{slots} concurrent engine(s)"))
    tpe = facts.get("threads_per_engine")
    if not tpe:
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


def check_threads_per_engine(facts, opts):
    """Threads the location promises per engine vs what the engine is sized for.

    BlazeMeter's own default pairs 500 threads with a 2 CPU / 8Gi engine, so
    scale that linearly on whichever of the two ratios is smaller: 500 threads
    on a 1 CPU / 4Gi engine is not a runnable location, it is a location that
    OOM-kills or throttles halfway up the ramp.
    """
    tpe = facts.get("threads_per_engine")
    if not tpe:
        return []                     # check_location already FAILs on this
    cpu, mem = engine_size(opts)
    base_cpu, base_mem = parse_cpu(ENGINE_DEFAULT_CPU), parse_memory(ENGINE_DEFAULT_MEM)
    ratio = min(cpu / base_cpu, mem / base_mem)
    supported = int(DEFAULT_THREADS_PER_ENGINE * ratio)
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


def eligible_nodes(nodes, opts):
    """Nodes an engine could actually land on: Ready, uncordoned, matching the
    configured nodeSelector, and with every blocking taint tolerated.

    PreferNoSchedule is a preference, not a rejection, so it does not exclude.
    """
    selector = opts.get("node_selector") or {}
    tolerations = opts.get("tolerations") or []
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


def _scope(opts):
    bits = []
    if opts.get("node_selector"):
        bits.append(f"nodeSelector {json.dumps(opts['node_selector'])}")
    if opts.get("tolerations"):
        bits.append(f"{len(opts['tolerations'])} toleration(s)")
    return ", ".join(bits) or "no nodeSelector/tolerations"


# -- capacity -----------------------------------------------------------------

def check_capacity(facts, opts, nodes):
    """slots x engine size vs what the eligible nodes can hold.

    Two checks, because they fail differently: a pod is not splittable across
    nodes, so 'the cluster has 40Gi free' does not mean an 8Gi engine fits
    anywhere.
    """
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    want = _engine_str(cpu, mem)
    nodes = eligible_nodes(nodes, opts)
    if not nodes:
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

    # Crane occupies the namespace alongside the engines; the quota check counts
    # its pod, so the capacity maths has to spend its share too.
    crane_cpu, crane_mem = parse_cpu(CRANE_CPU_LIMIT), parse_memory(CRANE_MEM_LIMIT)
    tot_cpu = max(sum(c for c, _ in sizes.values()) - crane_cpu, 0)
    tot_mem = max(sum(m for _, m in sizes.values()) - crane_mem, 0)
    holds = min(tot_cpu // cpu, tot_mem // mem)
    total = (f"{len(nodes)} eligible node(s) leave {format_cpu(tot_cpu)} CPU / "
             f"{human_memory(tot_mem)} after crane's own "
             f"{_engine_str(crane_cpu, crane_mem)} -- an upper bound, other "
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


def check_disk(facts, nodes, opts=None):
    """Ephemeral storage per eligible node against the documented engine
    footprint. WARN, not FAIL: a short run may never fill it -- but an engine
    that does gets evicted mid-test, which reads as a random failure."""
    opts = opts or {}
    slots = facts.get("slots") or 1
    nodes = eligible_nodes(nodes, opts)
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


def check_limitrange(opts, limitranges):
    """An existing LimitRange can reject the engine pod outright -- max below
    its limits, min above the requests crane stamps, or a maxLimitRequestRatio
    tighter than the gap between the two -- and can rewrite the resources of any
    pod in the namespace that declares none. Neither shows up in the manifests."""
    cpu, mem = engine_size(opts)
    if not limitranges:
        if opts.get("emit_limitrange"):
            return [Check("limitrange", PASS,
                          "none in the namespace; the generated "
                          "bzm_limitrange.yaml caps it at the engine size")]
        # No LimitRange means no ceiling on the namespace either -- and the
        # engines are already scheduling small (see the detail), which nothing
        # here can change.
        return [Check("limitrange", WARN,
                      f"no LimitRange in the namespace and the profile does not "
                      f"emit one, so nothing caps what the namespace may ask "
                      f"for. Separately, engine pods request "
                      f"{ENGINE_STAMPED_REQUEST_CPU}/{ENGINE_STAMPED_REQUEST_MEM} "
                      f"rather than {_engine_str(cpu, mem)} because crane sets "
                      f"that explicitly -- a LimitRange cannot override it")]

    checks = []
    for lr in limitranges:
        name = lr.get("metadata", {}).get("name", "?")
        blocking, conflicts = [], []
        for item in lr.get("spec", {}).get("limits", []):
            if item.get("type") not in _LR_TYPES:
                continue
            mx = item.get("max") or {}
            if mx.get("cpu") and parse_cpu(mx["cpu"]) < cpu:
                blocking.append(f"max cpu {mx['cpu']} < engine {format_cpu(cpu)}")
            if mx.get("memory") and parse_memory(mx["memory"]) < mem:
                blocking.append(f"max memory {mx['memory']} < engine "
                                f"{format_memory(mem)}")
            # min rejects from below just as max does from above, and the engine
            # requests what crane stamps (250m/256Mi), not its limits.
            mn = item.get("min") or {}
            if mn.get("cpu") and parse_cpu(mn["cpu"]) > parse_cpu(ENGINE_STAMPED_REQUEST_CPU):
                blocking.append(f"min cpu {mn['cpu']} > the "
                                f"{ENGINE_STAMPED_REQUEST_CPU} crane requests")
            if mn.get("memory") and parse_memory(mn["memory"]) > parse_memory(ENGINE_STAMPED_REQUEST_MEM):
                blocking.append(f"min memory {mn['memory']} > the "
                                f"{ENGINE_STAMPED_REQUEST_MEM} crane requests")
            # An engine's own limit/request ratio is large precisely because
            # crane requests so little of what it limits.
            ratio = item.get("maxLimitRequestRatio") or {}
            for key, stamped, limit in (
                    ("cpu", ENGINE_STAMPED_REQUEST_CPU, format_cpu(cpu)),
                    ("memory", ENGINE_STAMPED_REQUEST_MEM, format_memory(mem))):
                parse = parse_cpu if key == "cpu" else parse_memory
                engine_ratio = parse(limit) / parse(stamped)
                if ratio.get(key) and engine_ratio > float(ratio[key]):
                    blocking.append(f"maxLimitRequestRatio {key} {ratio[key]} < the "
                                    f"engine's own {engine_ratio:.0f}x "
                                    f"({stamped} requested, {limit} limit)")
            for field in ("defaultRequest", "default"):
                d = item.get(field) or {}
                if d.get("cpu") and parse_cpu(d["cpu"]) != cpu:
                    conflicts.append(f"{field}.cpu {d['cpu']}")
                if d.get("memory") and parse_memory(d["memory"]) != mem:
                    conflicts.append(f"{field}.memory {d['memory']}")
        if blocking:
            checks.append(Check(f"limitrange {name}", FAIL,
                                f"LimitRange '{name}' rejects the engine pod at "
                                f"admission: {'; '.join(blocking)} (crane itself "
                                f"needs {CRANE_CPU_LIMIT}/{CRANE_MEM_LIMIT})"))
        if conflicts:
            # Every LimitRange in the namespace is enforced, but when more than
            # one supplies defaults there is no defined winner -- so ours does
            # not simply take over from this one.
            checks.append(Check(f"limitrange {name} defaults", WARN,
                                f"LimitRange '{name}' sets {', '.join(conflicts)} "
                                f"against an engine of {_engine_str(cpu, mem)} "
                                f"-- adding our bzm_limitrange.yaml gives the "
                                f"namespace two sources of defaults with no "
                                f"defined winner; drop one"))
        if not blocking and not conflicts:
            checks.append(Check(f"limitrange {name}", PASS,
                                f"LimitRange '{name}' is compatible with a "
                                f"{_engine_str(cpu, mem)} engine"))
    return checks


# Quota keys we can compare against an engine's claim. 'cpu'/'memory' are the
# API's aliases for the requests.* forms.
_QUOTA_CPU = ("requests.cpu", "limits.cpu", "cpu")
_QUOTA_MEM = ("requests.memory", "limits.memory", "memory")


def check_resourcequota(facts, opts, quotas, limitranges=()):
    """hard - used, per resource, against slots x engine (+1 pod for crane)."""
    cpu, mem = engine_size(opts)
    slots = facts.get("slots") or 1
    if not quotas:
        return [Check("resourcequota", PASS, "no ResourceQuota in the namespace")]

    checks, constrains_compute = [], []
    for q in quotas:
        name = q.get("metadata", {}).get("name", "?")
        hard = q.get("status", {}).get("hard") or q.get("spec", {}).get("hard") or {}
        used = q.get("status", {}).get("used") or {}
        short = []
        for key in _QUOTA_CPU:
            if key in hard:
                constrains_compute.append(name)
                free = parse_cpu(hard[key]) - parse_cpu(used.get(key, "0"))
                if free < cpu * slots:
                    short.append((key, f"{format_cpu(free)} free, "
                                       f"{format_cpu(cpu * slots)} needed"))
        for key in _QUOTA_MEM:
            if key in hard:
                constrains_compute.append(name)
                free = parse_memory(hard[key]) - parse_memory(used.get(key, "0"))
                if free < mem * slots:
                    short.append((key, f"{human_memory(free)} free, "
                                       f"{format_memory(mem * slots)} needed"))
        if "pods" in hard:
            free = int(hard["pods"]) - int(used.get("pods", 0))
            if free < slots + 1:            # slots engines + the crane pod
                short.append(("pods", f"{free} free, {slots + 1} needed "
                                      f"({slots} engine(s) + crane)"))
        for key, detail in short:
            checks.append(Check(f"quota {name} {key}", FAIL,
                                f"ResourceQuota '{name}' cannot fit slots={slots}: "
                                f"{key} {detail}"))
        if not short:
            checks.append(Check(f"quota {name}", PASS,
                                f"ResourceQuota '{name}' has room for slots="
                                f"{slots} ({format_cpu(cpu * slots)} / "
                                f"{format_memory(mem * slots)}, {slots + 1} pods)"))
    if constrains_compute and not limitranges and not opts.get("emit_limitrange"):
        # With a cpu/memory quota in force the API server rejects any pod that
        # does not declare that resource -- and crane sets no requests on the
        # engines it spawns, so something has to supply them.
        checks.append(Check("quota defaults", WARN,
                            f"ResourceQuota '{constrains_compute[0]}' constrains "
                            f"cpu/memory, so every pod must declare requests and "
                            f"limits; crane sets none on engine pods. Supply them "
                            f"with a LimitRange (regenerate with emit_limitrange)"))
    return checks


# -- admission ----------------------------------------------------------------

PSA_ENFORCE = "pod-security.kubernetes.io/enforce"
SCC_UID_RANGE = "openshift.io/sa.scc.uid-range"


def check_admission(opts, namespace_obj):
    """Will the namespace's admission posture accept the *engine* pods?

    Our crane pod satisfies restricted PSA (runAsNonRoot, no privilege
    escalation, drop ALL, RuntimeDefault seccomp). The engines crane spawns are
    a different matter: they only get KUBERNETES_SECURITY_CONTEXT_CAP_JSON and
    INHERIT_RUNNING_USER_AND_GROUP on the openshift path, so under restricted
    PSA on plain k8s they are rejected after crane is already happily online.
    """
    meta = (namespace_obj or {}).get("metadata") or {}
    platform = opts.get("platform") or "openshift"
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
        return [Check("admission (PodSecurity)", FAIL,
                      f"{PSA_ENFORCE}=restricted with platform=k8s: crane passes, "
                      f"but the engine pods it spawns get no security context "
                      f"(the generator emits KUBERNETES_SECURITY_CONTEXT_CAP_JSON "
                      f"/ INHERIT_RUNNING_USER_AND_GROUP only for platform="
                      f"openshift), so runs fail after the agent is online. Use "
                      f"enforce=baseline for this namespace")]
    if enforce:
        return [Check("admission (PodSecurity)", PASS,
                      f"{PSA_ENFORCE}={enforce} admits the engine pods")]
    return [Check("admission (PodSecurity)", WARN,
                  f"namespace has no {PSA_ENFORCE} label -- no enforcement is "
                  f"configured, so nothing here is checked at admission time "
                  f"(a cluster-wide default may still apply)")]


# -- egress -------------------------------------------------------------------

def egress_targets(opts):
    """What has to be reachable from inside the namespace before a run works."""
    targets = [API_PROBE_URL, *ENGINE_PROBE_URLS]
    reg = opts.get("private_registry")
    if reg:
        targets.append(f"https://{reg.split('/')[0]}/v2/")
    return targets


def check_egress(probes):
    """Pure verdict over {target: curl returncode}; None = we could not probe."""
    if not probes:
        return [Check("egress", WARN,
                      "egress could not be probed from inside the cluster -- "
                      "no crane pod and no way to run a one-shot probe pod")]
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

def _kjson(cmd):
    """kubectl/oc -o json -> parsed object, {} when the object is not there.
    A namespace that does not exist yet is the normal pre-flight case."""
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return {}
    return json.loads(out.stdout)


def gather_cluster(cli, namespace):
    return {
        "nodes": _kjson([cli, "get", "nodes", "-o", "json"]).get("items", []),
        "limitranges": _kjson([cli, "-n", namespace, "get", "limitrange",
                               "-o", "json"]).get("items", []),
        "quotas": _kjson([cli, "-n", namespace, "get", "resourcequota",
                          "-o", "json"]).get("items", []),
        "namespace": _kjson([cli, "get", "ns", namespace, "-o", "json"]),
    }


def _crane_deployed(cli, namespace):
    out = subprocess.run([cli, "-n", namespace, "get", "deploy", "crane",
                          "-o", "name"], capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def _ca_configured(opts):
    return bool(opts.get("ca_bundle") or opts.get("ca_existing_configmap")
                or opts.get("ca_openshift_inject"))


def probe_egress(cli, namespace, opts):
    """curl each target from inside the cluster -> {target: returncode}.

    Preferably from the crane pod: it is the only place the profile's proxy env
    and CA bundle are actually in force, which is what the customer's egress
    depends on. A one-shot pod is the fallback, and cannot verify a corporate
    CA at all -- that reports None (WARN), never a FAIL we cannot stand behind.
    """
    targets = egress_targets(opts)
    if _crane_deployed(cli, namespace):
        ca = ' --cacert "$REQUESTS_CA_BUNDLE"' if _ca_configured(opts) else ""
        # crane_curl's -1 means the exec never produced an rc line at all --
        # unknown, not unreachable.
        rcs = {t: livetest.crane_curl(
            cli, namespace, f"-s -o /dev/null --max-time 20{ca} {t}")
            for t in targets}
        return {t: (None if rc < 0 else rc) for t, rc in rcs.items()}
    if _ca_configured(opts):
        return {t: None for t in targets}
    return {t: _oneshot_curl(cli, namespace,
                             f"-s -o /dev/null --max-time 20 {t}", opts)
            for t in targets}


def _oneshot_curl(cli, namespace, args, opts):
    """Probe from a throwaway pod when crane is not deployed yet. Returns None
    if the pod itself never ran -- unknown, not unreachable."""
    env = []
    p = opts.get("proxy") or {}
    for name, key in (("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https"),
                      ("NO_PROXY", "no_proxy")):
        if p.get(key):
            value = proxy_url(p[key], p) if key != "no_proxy" else p[key]
            env += ["--env", f"{name}={value}"]
    out = subprocess.run(
        [cli, "-n", namespace, "run", f"bzm-doctor-{os.getpid()}", "--rm", "-i",
         "--restart=Never", "--image", CURL_IMAGE, *env, "--command", "--",
         "sh", "-c", f"curl {args}; echo rc=$?"],
        capture_output=True, text=True)
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("rc="):
            return int(line[3:])
    return None


def run(facts, opts, namespace, cluster_data=None, probes=None, cli=None):
    """Run every check and print the verdict list. Returns the Check list; the
    caller decides the exit code (see has_failures)."""
    opts = dict(opts or {})
    namespace = namespace or opts.get("namespace") or "blazemeter"
    if cluster_data is None or probes is None:
        cli = cli or livetest.cli_tool()
    if cluster_data is None:
        cluster_data = gather_cluster(cli, namespace)
    if probes is None:
        probes = probe_egress(cli, namespace, opts)

    nodes = cluster_data.get("nodes") or []
    limitranges = cluster_data.get("limitranges") or []
    checks = (
        check_location(facts)
        + check_threads_per_engine(facts, opts)
        + check_capacity(facts, opts, nodes)
        + check_disk(facts, nodes, opts)
        + check_limitrange(opts, limitranges)
        + check_resourcequota(facts, opts, cluster_data.get("quotas") or [],
                              limitranges)
        + check_admission(opts, cluster_data.get("namespace") or {})
        + check_egress(probes)
    )
    _report(checks, facts, namespace)
    return checks


def _report(checks, facts, namespace):
    print(f"doctor: location {facts.get('harbor_name')} "
          f"({facts.get('harbor_id')}), namespace {namespace}")
    width = max((len(c.name) for c in checks), default=0)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
    counts = collections.Counter(c.status for c in checks)
    print(f"{counts[PASS]} passed, {counts[WARN]} warning(s), "
          f"{counts[FAIL]} failure(s)"
          + ("" if not counts[FAIL] else
             " -- a test would not start on this location as configured"))
