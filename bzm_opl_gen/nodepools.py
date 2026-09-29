"""The node pool recipe (nodepools.md) for a bundle whose engines have their
own pool.

The manifests say which nodes an engine may use, not how many share one: the
scheduler and autoscaler place on requests, which come from the location's
overrideCPU/overrideMemory (250m/256Mi when unset). So the recipe leads with
the overrides and falls back to the pool's maxPods, a node pool property no
manifest can set.
"""

from .bundle_options import (cli, crane_scheduling, engine_scheduling,
                             engine_size, engines_per_node)
from .footprint import (CRANE_CPU_LIMIT, CRANE_MEM_LIMIT,
                        ENGINE_DEFAULT_REQUEST_CPU,
                        ENGINE_DEFAULT_REQUEST_MEM, GKE_MIN_MAX_PODS,
                        NODE_OVERHEAD_CPU, NODE_OVERHEAD_MEM,
                        TYPICAL_SYSTEM_PODS)
from .quantity import format_cpu, format_memory


def _engines_per_node(max_pods, floor=0):
    """Engines that fit on a node once `floor` raises maxPods above what was
    asked for; never below 1.
    """
    return max(max(max_pods, floor) - TYPICAL_SYSTEM_PODS, 1)


def _taints_from_tolerations(tolerations):
    """The taints a pool needs for these tolerations to admit engines. `Exists`
    gives a valueless taint, `key:effect`, which differs from `key=:effect`.
    """
    out = []
    for tol in tolerations:
        key = tol.get("key")
        if not key:
            continue                  # tolerates everything; no taint to derive
        effect = tol.get("effect") or "NoSchedule"
        if tol.get("operator") == "Exists":
            out.append(f"{key}:{effect}")
        else:
            out.append(f"{key}={tol.get('value', '')}:{effect}")
    return out


def nodepools_md(facts, o):
    """nodepools.md, for a bundle whose engines have their own pool."""
    cpu, mem = engine_size(o)
    eng_sel, eng_tol = engine_scheduling(o)
    crane_sel, _ = crane_scheduling(o)
    slots = facts.get("slots")
    taints = _taints_from_tolerations(eng_tol)
    per_node = engines_per_node(o)
    max_pods = TYPICAL_SYSTEM_PODS + per_node

    # Sized from the engine's limits, which is what it runs at.
    node_cpu = format_cpu(cpu + NODE_OVERHEAD_CPU)
    node_mem = format_memory(mem + NODE_OVERHEAD_MEM)

    sel_pairs = ",".join(f"{k}={v}" for k, v in eng_sel.items()) or "(none set)"
    crane_desc = (", ".join(f"{k}={v}" for k, v in crane_sel.items())
                  or "no selector -- crane lands on any node")

    lines = [
        f"# Node pools for {facts.get('harbor_name') or facts['harbor_id']}",
        "",
        "This bundle places crane and its engines on **different nodes**. The",
        "manifests carry the labels and tolerations; the pools themselves are",
        "yours to create, and this is what they need to be.",
        "",
        "| | crane pool | engine pool |",
        "|---|---|---|",
        "| holds | 1 pod, always | 0-n pods, only during a run |",
        f"| selector | {crane_desc} | `{sel_pairs}` |",
        f"| taints | none needed | {', '.join(f'`{t}`' for t in taints) or 'none set'} |",
        f"| per node | {CRANE_CPU_LIMIT} CPU / {CRANE_MEM_LIMIT} | "
        f"{format_cpu(cpu)} CPU / {format_memory(mem)} per engine |",
        "| autoscaling | fixed, 1-2 nodes | min 0, scales with the run |",
        "",
        "## Why two pools",
        "",
        "Crane is a small orchestrator that must not move: it holds the",
        f"location's registration, and it needs {CRANE_CPU_LIMIT} CPU / {CRANE_MEM_LIMIT} to do it.",
        f"An engine needs {format_cpu(cpu)} CPU / {format_memory(mem)} and exists only for the length",
        "of a run. Sharing one pool means either paying for engine-sized nodes",
        "around the clock, or letting crane sit on a node the autoscaler wants",
        "to remove -- so the pool never drains and the saving never arrives.",
        "",
        "## Set the location's CPU/memory overrides first",
        "",
        "**This is the fix. Everything below is a backstop for it.**",
        "",
        "The scheduler and the cluster autoscaler place pods by their",
        "**requests**, not their limits. An engine's limits come from this bundle",
        f"({format_cpu(cpu)} / {format_memory(mem)}); its *requests* come from the location, as",
        "`overrideCPU` and `overrideMemory` under Settings -> Private Locations.",
        "They are different fields, not rival settings for one field --",
        "confirmed on a live run, where a location at `overrideCPU: 1` /",
        "`overrideMemory: 4096` and a bundle at 2 CPU / 8Gi produced an engine pod",
        "with `requests {cpu: 1, memory: 4Gi}` and `limits {cpu: 2, memory: 8Gi}`.",
        "",
        f"Left unset -- as {ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM} -- an engine asks the scheduler for a",
        "fraction of what it will use, so the autoscaler adds **one** node and",
        "packs the whole run onto it. The engines then throttle against each",
        "other and the test reports the load generator's latency, not the",
        "system's.",
        "",
        f"So set them to match the limits this bundle asks for: **overrideCPU: {format_cpu(cpu)}**,",
        f"**overrideMemory: {mem // (1024 ** 2)}** (it is in MB). Then requests equal limits, the",
        "scheduler places engines truthfully, the autoscaler grows the pool by",
        "the right number of nodes, and none of the `maxPods` arithmetic below",
        "has to carry the weight on its own.",
        "",
        "A LimitRange still cannot do this: crane sets the requests explicitly",
        "either way, and `defaultRequest` only fills fields a pod leaves unset.",
        "",
        "## The backstop, when the overrides are not set",
        "",
        f"**`maxPods` on the engine pool is the ceiling that works.** At {max_pods} a node",
        "takes its own system pods and exactly one engine, so N engines force N",
        f"nodes regardless of what they requested. Measure before trusting {max_pods} --",
        "count the pods on a node of the pool, on a node of a pool with the same",
        "taints, or on any node if you have neither:",
        "",
        "```",
        f"{cli(o)} get pods -A --field-selector spec.nodeName=<NODE> --no-headers | wc -l",
        "```",
        "",
        f"**Do not count DaemonSet objects for this.** `{cli(o)} get ds -A | wc -l`",
        "reports 32 on a stock GKE cluster where 4 DaemonSet pods actually land:",
        "most are variants gated by nodeAffinity -- GPU plugins, Windows builds,",
        "metrics agents chosen by machine size -- and counting them sizes the pool",
        "eight times too loose.",
        "",
        "That count + 1 is your `maxPods`. Too low and the node's own agents never",
        "start, which looks like a broken node rather than a full one: a cluster",
        "left at `maxPods: 10` had six system pods stuck Pending on `Too many",
        "pods`, managed Prometheus among them.",
        "",
        "The taint is what makes the number predictable. Without it the pool also",
        "takes whatever Deployments the scheduler spreads there -- kube-dns,",
        "metrics-server, konnectivity-agent -- for another 5-6 slots a node that",
        "come and go.",
        "",
        "## Sizing the engine node",
        "",
        f"One engine per node means the machine must hold {format_cpu(cpu)} CPU / {format_memory(mem)}",
        "*allocatable*, and allocatable is what is left after the kubelet's",
        f"reservations -- roughly {format_cpu(NODE_OVERHEAD_CPU)} CPU and {format_memory(NODE_OVERHEAD_MEM)} on a managed node. So pick a",
        f"machine with at least **{node_cpu} vCPU and {node_mem}** of capacity, and confirm with",
        f"`{cli(o)} get node <name> -o jsonpath='{{.status.allocatable}}'` once one exists.",
        "",
    ]
    if slots:
        lines += [
            f"This location advertises **slots={slots}**, so size the pool's maximum",
            f"at {slots} node(s) to run a full-width test.",
            "",
        ]
    else:
        lines += [
            "The location's concurrency (`slots`) is not recorded in these facts;",
            "set the pool maximum to the widest test you intend to run.",
            "",
        ]

    lines += _nodepool_commands(o, eng_sel, taints, max_pods, slots)
    lines += [
        "## Checking it worked",
        "",
        "Run a test, then -- while it is running -- confirm the engine is on the",
        "engine pool and is the size you configured:",
        "",
        "```",
        f"{cli(o)} -n {o['namespace']} get pods -o wide",
        f"{cli(o)} -n {o['namespace']} get pod <engine-pod> \\",
        "  -o jsonpath='{.spec.nodeName}{\"\\n\"}{.spec.containers[*].resources}{\"\\n\"}'",
        "```",
        "",
        f"Expect `limits` of {format_cpu(cpu)}/{format_memory(mem)} and `requests` of "
        f"{ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM}.",
        "The mismatch is expected and is the reason for `maxPods`. What matters",
        "is that the node is one of the engine pool's, and that no more engines",
        "share it than the pool was sized for -- which is one per node where the",
        f"platform allows it and {_engines_per_node(max_pods, GKE_MIN_MAX_PODS)} on GKE, whose maxPods floor of "
        f"{GKE_MIN_MAX_PODS} does not",
        "go low enough. More than that means `maxPods` is not in effect.",
        "",
        "`bzm-opl-gen doctor` checks the same shape before you deploy.",
        "",
    ]
    return "\n".join(lines)


def _machine_for(o, engines):
    """The machine-size placeholder for a node holding `engines` engines at
    their limits.
    """
    cpu, mem = engine_size(o)
    return (f"<at least {format_cpu(cpu * engines + NODE_OVERHEAD_CPU)} vCPU / "
            f"{format_memory(mem * engines + NODE_OVERHEAD_MEM)}"
            + (f", holding {engines} engines>" if engines > 1 else ">"))


def _cmd(parts):
    """Shell lines joined with trailing backslashes. Empty parts are dropped
    first, so no conditional flag leaves a dangling continuation.
    """
    parts = [p for p in parts if p]
    return [p + (" \\" if i < len(parts) - 1 else "")
            for i, p in enumerate(parts)]


def _nodepool_commands(o, eng_sel, taints, max_pods, slots):
    """Per-distribution commands for the engine pool.

    Each sets labels, taints, maxPods and a zero minimum; where a distribution
    cannot set one on the create command, the recipe says so.
    """
    labels = ",".join(f"{k}={v}" for k, v in eng_sel.items())
    maximum = slots or 5
    machine = _machine_for(o, engines_per_node(o))
    out = [
        "## Creating the engine pool",
        "",
        "Four things matter and are the same everywhere: the **labels** the",
        "manifests select on, the **taints** that keep other workloads off, the",
        f"**`maxPods: {max_pods}`** ceiling ({engines_per_node(o)} engine(s) a node plus",
        f"~{TYPICAL_SYSTEM_PODS} system pods), and a **minimum of zero** so the pool",
        "drains between runs.",
        "",
    ]

    gke_max_pods = max(max_pods, GKE_MIN_MAX_PODS)
    gke_engines = _engines_per_node(max_pods, GKE_MIN_MAX_PODS)
    out += ["### GKE (Standard -- Autopilot cannot take user node pools)", "", "```"]
    out += _cmd([
        "gcloud container node-pools create bzm-engines",
        "  --cluster <CLUSTER> --region <REGION>",
        f"  --machine-type {_machine_for(o, gke_engines)}",
        f"  --node-labels {labels}" if labels else "",
        f"  --max-pods-per-node {gke_max_pods}",
        *[f"  --node-taints {t}" for t in taints],
        f"  --enable-autoscaling --min-nodes 0 --max-nodes {maximum}",
    ])
    out += ["```", ""]
    if gke_max_pods > max_pods:
        out += [
            f"**GKE will not go below {GKE_MIN_MAX_PODS}.** The API refuses anything lower",
            f"(\"Maximum pods per node must be at least {GKE_MIN_MAX_PODS} and at most 256\"), so the",
            f"{max_pods} this pool actually wants is not reachable and the floor leaves room",
            f"for **{gke_engines} engines a node**, not one.",
            "",
            "That is not a setting you can tighten, so size the node for those",
            f"{gke_engines} engines rather than for one -- the machine type above already",
            "is. The alternative is fewer system pods on the pool (dropping",
            "managed Prometheus or NodeLocalDNS from it), which buys one slot each",
            "and costs observability.",
            "",
        ]
    out += [
        "`--max-pods-per-node` cannot be changed after creation -- it sizes the",
        "node's alias IP range, so getting it wrong means replacing the pool.",
        "",
    ]

    out += ["### EKS (managed node group)", "", "```"]
    out += _cmd([
        "eksctl create nodegroup --cluster <CLUSTER> --name bzm-engines",
        f"  --node-type {machine}",
        f"  --nodes-min 0 --nodes-max {maximum}",
        f"  --node-labels {labels}" if labels else "",
    ])
    out += [
        "```",
        "",
        "**Two of the four are not on that command.** Taints go in the `eksctl`",
        "ClusterConfig (`taints:` under the node group), and `maxPods` comes from",
        "the launch template's bootstrap:",
        "",
        "```",
        f"--kubelet-extra-args '--max-pods={max_pods}'",
        "```",
        "",
        "EKS otherwise derives maxPods from the instance type's ENI limits, which",
        "is far higher than anything wanted here.",
        "",
    ]

    out += ["### AKS", "", "```"]
    out += _cmd([
        "az aks nodepool add --cluster-name <CLUSTER> --resource-group <RG>",
        "  --name bzmengines",
        f"  --node-vm-size {machine}",
        f"  --max-pods {max_pods}",
        f"  --enable-cluster-autoscaler --min-count 0 --max-count {maximum}",
        f"  --labels {labels}" if labels else "",
        f"  --node-taints {','.join(taints)}" if taints else "",
    ])
    out += ["```", ""]

    out += [
        "### OpenShift (MachineSet)",
        "",
        "Clone an existing MachineSet and set on the clone:",
        "",
        "```yaml",
        "spec:",
        "  replicas: 0                     # a MachineAutoscaler grows it",
        "  template:",
        "    spec:",
        "      metadata:",
        "        labels:",
    ]
    out += ([f"          {k}: \"{v}\"" for k, v in eng_sel.items()]
            or ["          # no engine labels configured"])
    if taints:
        out.append("      taints:")
        for block in _taint_yaml(taints):
            out += block
    out += [
        "```",
        "",
        "Pair it with a `MachineAutoscaler` (`minReplicas: 0`), and set maxPods",
        "through a `KubeletConfig` selecting that pool's machine config pool --",
        "it is not a MachineSet field:",
        "",
        "```yaml",
        "apiVersion: machineconfiguration.openshift.io/v1",
        "kind: KubeletConfig",
        "spec:",
        "  kubeletConfig:",
        f"    maxPods: {max_pods}",
        "```",
        "",
    ]

    out += [
        "### Anything else (kubeadm, Rancher, on-prem)",
        "",
        "No pool object, so the same four things are set per node:",
        "",
        "```",
    ]
    out += ([f"{cli(o)} label node <NODE> {labels.replace(',', ' ')}"] if labels
            else ["# no engine labels configured"])
    out += [f"{cli(o)} taint node <NODE> {t}" for t in taints]
    out += [
        "```",
        "",
        f"`maxPods: {max_pods}` goes in the kubelet config",
        "(`/var/lib/kubelet/config.yaml`) and needs a kubelet restart. With no",
        "cluster autoscaler the pool cannot scale to zero: cordon the nodes",
        "between runs, or accept that they idle.",
        "",
    ]
    return out


def _taint_yaml(taints):
    """`key=value:Effect` / `key:Effect` as MachineSet taint YAML; the
    valueless form has no `value:` key.
    """
    out = []
    for t in taints:
        head, _, effect = t.rpartition(":")
        key, sep, value = head.partition("=")
        block = [f"        - key: \"{key}\"", f"          effect: \"{effect}\""]
        if sep:
            block.insert(1, f"          value: \"{value}\"")
        out.append(block)
    return out
