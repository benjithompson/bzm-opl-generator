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
    node_cpu = format_cpu(cpu * per_node + NODE_OVERHEAD_CPU)
    node_mem = format_memory(mem * per_node + NODE_OVERHEAD_MEM)
    engines = f"{per_node} engine{'s' if per_node > 1 else ''}"

    sel_pairs = ",".join(f"{k}={v}" for k, v in eng_sel.items()) or "(none set)"
    crane_desc = (", ".join(f"{k}={v}" for k, v in crane_sel.items())
                  or "no selector -- crane lands on any node")

    lines = [
        f"# Node pools for {facts.get('harbor_name') or facts['harbor_id']}",
        "",
        "This bundle places crane and its engines on **different nodes**. The",
        "manifests carry the labels and tolerations; you create the pools.",
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
        "Crane is small and always running; engines are large and exist only",
        "during a run. Separate pools let the engine pool scale to zero between",
        "tests.",
        "",
        "## 1. Set the location's CPU/memory overrides",
        "",
        "The scheduler and cluster autoscaler place pods by their **requests**.",
        f"An engine's limits come from this bundle ({format_cpu(cpu)} / {format_memory(mem)}); its requests come",
        "from the location's `overrideCPU` and `overrideMemory` (Settings ->",
        f"Private Locations), default {ENGINE_DEFAULT_REQUEST_CPU}/{ENGINE_DEFAULT_REQUEST_MEM}. At the default, many engines",
        "fit on one node and slow each other down, skewing results.",
        "",
        f"Set **overrideCPU: {format_cpu(cpu)}** and **overrideMemory: {mem // (1024 ** 2)}** (MB) so that",
        "requests match limits and the autoscaler adds the right number of nodes.",
        "",
        "## 2. Cap engines per node with maxPods",
        "",
        f"As a backstop, set **`maxPods: {max_pods}`** on the engine pool: room for the",
        f"node's own system pods (about {TYPICAL_SYSTEM_PODS}) plus {engines}. Check the system",
        "pod count on a node like the ones in the pool:",
        "",
        "```",
        f"{cli(o)} get pods -A --field-selector spec.nodeName=<NODE> --no-headers | wc -l",
        "```",
        "",
        f"`maxPods` is that count plus {per_node}. Count pods, not DaemonSets: many",
        "DaemonSets place no pod on a given node. Too low a value leaves system",
        "pods Pending with `Too many pods`. The taint keeps other workloads off",
        "the pool, so the count stays stable.",
        "",
        "## 3. Size the engine node",
        "",
        f"A node holding {engines} needs {format_cpu(cpu * per_node)} CPU / {format_memory(mem * per_node)}",
        f"*allocatable*, which is roughly {format_cpu(NODE_OVERHEAD_CPU)} CPU and {format_memory(NODE_OVERHEAD_MEM)} less than capacity on",
        f"a managed node. Pick a machine with at least **{node_cpu} vCPU and {node_mem}**, and",
        f"confirm with `{cli(o)} get node <name> -o jsonpath='{{.status.allocatable}}'`.",
        "",
    ]
    if slots:
        lines += [
            f"This location has **slots={slots}**, so allow the pool at least",
            f"{slots} node(s) for a full-width test.",
            "",
        ]
    else:
        lines += [
            "Set the pool maximum to the widest test you intend to run (the",
            "location's `slots`).",
            "",
        ]

    lines += _nodepool_commands(o, eng_sel, taints, max_pods, slots)
    lines += [
        "## Check it worked",
        "",
        "While a test is running, confirm the engines are on the engine pool:",
        "",
        "```",
        f"{cli(o)} -n {o['namespace']} get pods -o wide",
        f"{cli(o)} -n {o['namespace']} get pod <engine-pod> \\",
        "  -o jsonpath='{.spec.nodeName}{\"\\n\"}{.spec.containers[*].resources}{\"\\n\"}'",
        "```",
        "",
        f"Expect `limits` of {format_cpu(cpu)}/{format_memory(mem)}, and `requests` matching the location's",
        "overrides. No node should hold more engines than the pool was sized for",
        f"({per_node}, or {_engines_per_node(max_pods, GKE_MIN_MAX_PODS)} on GKE); more means `maxPods` is not in effect.",
        "",
        "`bzm-opl-gen doctor` checks the cluster before you deploy.",
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
        "## 4. Create the engine pool",
        "",
        "On every platform the pool needs the **labels** the manifests select on,",
        f"the **taints** that keep other workloads off, **`maxPods: {max_pods}`**, and a",
        "**minimum of zero** nodes.",
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
            f"**GKE will not go below {GKE_MIN_MAX_PODS}** max pods per node, so this pool holds",
            f"**{gke_engines} engines a node**; the machine type above is sized for that.",
            "",
        ]
    out += [
        "`--max-pods-per-node` cannot be changed after the pool is created.",
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
        "Set the taints in the `eksctl` ClusterConfig (`taints:` under the node",
        "group), and `maxPods` in the launch template's bootstrap:",
        "",
        "```",
        f"--kubelet-extra-args '--max-pods={max_pods}'",
        "```",
        "",
        "Otherwise EKS derives maxPods from the instance type, which is far too high.",
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
        "Add a `MachineAutoscaler` (`minReplicas: 0`), and set maxPods with a",
        "`KubeletConfig` for that pool's machine config pool:",
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
        "Set the labels and taints per node:",
        "",
        "```",
    ]
    out += ([f"{cli(o)} label node <NODE> {labels.replace(',', ' ')}"] if labels
            else ["# no engine labels configured"])
    out += [f"{cli(o)} taint node <NODE> {t}" for t in taints]
    out += [
        "```",
        "",
        f"Set `maxPods: {max_pods}` in the kubelet config",
        "(`/var/lib/kubelet/config.yaml`) and restart the kubelet. Without a",
        "cluster autoscaler the nodes stay up between runs.",
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
