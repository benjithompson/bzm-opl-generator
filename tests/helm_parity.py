#!/usr/bin/env python3
"""Render every option combination both ways and require the same objects.

    python tests/helm_parity.py

Not a pytest module -- it shells out to `helm`, which the offline suite must not
depend on (a suite that skips when a binary is missing reports a clean pass
having tested nothing). Named so pytest does not collect it, and run as its own
CI job where helm is installed.

The point it defends: `--format helm` and `--format manifests` are two ways of
writing one deployment. Every judgement in templates/*.yaml -- the CA mount
being a directory, which proxy URLs reach the ConfigMap, the RBAC the agent
actually needs -- had to be restated in Go templates, and nothing but this check
would notice one of them being restated slightly differently.

Three ConfigMap values are compared as JSON rather than as bytes: they are JSON
documents in a string field, and Go's toJson sorts keys and omits the spaces
Python's json.dumps writes. Crane parses them, so the encoding is not the
contract; the structure is.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from bzm_opl_gen import generate as gen  # noqa: E402

from test_generate import FACTS  # noqa: E402

HELM = os.environ.get("HELM", "helm")

COMMON = {"ship_id": "bbb222", "namespace": "bzm-perf", "auth_token": "TOKEN"}

CASES = {
    "plain": {"platform": "k8s"},
    "openshift": {"platform": "openshift"},
    "engine-sized": {"platform": "k8s", "engine_cpu_limit": "2",
                     "engine_mem_limit": "8Gi"},
    "engine-small": {"platform": "k8s", "engine_cpu_limit": "500m",
                     "engine_mem_limit": "1Gi"},
    "token-in-configmap": {"platform": "k8s", "use_secret": False},
    "nodeport": {"platform": "k8s", "service_type": "NODEPORT", "cluster_rbac": True},
    # The pairing only one format used to accept. Manifests rendered it, the
    # chart refused it, and neither direction was covered here -- which is how
    # the disagreement survived a live run that proved the manifests right.
    "nodeport-namespaced-rbac": {"platform": "k8s", "service_type": "NODEPORT"},
    "private-registry": {"platform": "k8s", "private_registry": "reg.example.com/bzm"},
    "registry-auth": {"platform": "k8s", "private_registry": "reg.example.com/bzm",
                      "registry_auth": True, "pull_secret": "regcred"},
    # Both sides RESOLVE auto-update rather than pass it through -- the
    # ConfigMap carries a boolean and the option is a tri-state -- so each
    # format has its own copy of "unset follows the registry" and the two can
    # disagree in either direction. The unset case is covered by every other
    # entry here; these two are the ones where the customer overruled it.
    "auto-update-off": {"platform": "k8s", "auto_update": False},
    "auto-update-on-private-registry": {"platform": "k8s", "auto_update": True,
                                        "private_registry": "reg.example.com/bzm"},
    "proxy": {"platform": "k8s", "proxy": {"http": "http://px:3128"}},
    # Credentials must reach the Secret, not the ConfigMap...
    "proxy-creds": {"platform": "k8s", "proxy": {"http": "http://px:3128",
                    "https": "http://px:3128", "username": "u", "password": "p"}},
    # ...unless there is no Secret, where both formats warn instead.
    "proxy-creds-no-secret": {"platform": "k8s", "use_secret": False,
                              "proxy": {"http": "http://px:3128", "username": "u",
                                        "password": "p"}},
    "ca-inline": {"platform": "k8s", "ca_bundle":
                  "-----BEGIN CERTIFICATE-----\nMIIfake\n-----END CERTIFICATE-----"},
    "ca-existing": {"platform": "k8s", "ca_existing_configmap": "trust-bundle",
                    "ca_configmap_key": "tls-ca.pem"},
    "ca-openshift-inject": {"platform": "openshift", "ca_openshift_inject": True},
    "scheduling": {"platform": "k8s", "node_selector": {"workload": "perf"},
                   "tolerations": [{"key": "lifecycle", "operator": "Equal",
                                    "value": "spot", "effect": "NoSchedule"}]},
    # Two node pools: crane's placement is the podspec, the engines' is the
    # KUBERNETES_*_JSON env, and the chart has to keep them apart exactly as the
    # manifests do. A chart that derived the engine env from .Values.nodeSelector
    # -- which is what it used to do -- puts every engine back on crane's pool
    # and renders a Deployment that still looks right.
    "scheduling-split-pools": {
        "platform": "k8s", "node_selector": {"pool": "crane"},
        "engine_node_selector": {"pool": "bzm-engines"},
        "engine_tolerations": [{"key": "bzm.io/engines", "operator": "Equal",
                                "value": "true", "effect": "NoSchedule"}]},
    # The distinction the chart cannot re-derive: crane is tainted and pinned,
    # the engines are explicitly given neither. Rendered from a values file
    # alone, "empty" and "absent" have to already be resolved -- so an engine
    # env appearing here at all is the regression.
    "scheduling-engines-unpinned": {
        "platform": "k8s", "node_selector": {"pool": "infra"},
        "tolerations": [{"key": "infra", "operator": "Exists",
                         "effect": "NoSchedule"}],
        "engine_node_selector": {}, "engine_tolerations": []},
    "ephemeral": {"platform": "k8s", "engine_ephemeral_request_mb": 1024,
                  "engine_ephemeral_limit_mb": 61440},
    # Crane's own pod, which the engine case above does not touch. The chart
    # keeps its own copy of the default and the overlay only names an override,
    # so the two sides can disagree here without either looking wrong alone.
    "crane-ephemeral": {"platform": "k8s", "crane_ephemeral_storage": "4Gi"},
    # The escape hatch, not the default -- the default is covered by every other
    # case here. Off is the side that can drift: the chart gates on its own
    # value and the overlay only speaks when it is false.
    "unrestricted-engines": {"platform": "k8s", "restrict_engines": False},
    # The name has to reach the Deployment and both binding subjects, and
    # `create` has to remove the object from one format exactly when it removes
    # it from the other -- a chart still rendering it would adopt an account the
    # customer's platform team owns.
    "service-account-named": {"platform": "k8s", "cluster_rbac": True,
                              "service_account_name": "bzm-agent"},
    "service-account-existing": {"platform": "k8s", "cluster_rbac": True,
                                 "service_account_name": "platform-sa",
                                 "service_account_create": False},
    # crane-hook: three more objects on both sides, and every value in them is
    # one the bundle already decided -- the namespace, the account it runs as,
    # the registry its image comes from, the UID rule that differs by platform.
    "crane-hook": {"platform": "k8s", "crane_hook": True},
    "crane-hook-openshift": {"platform": "openshift", "crane_hook": True},
    "crane-hook-private-registry": {"platform": "k8s", "crane_hook": True,
                                    "private_registry": "reg.example.com/bzm",
                                    "service_account_name": "bzm-agent"},
    # Free-form agent env. The one place a *value* crosses the overlay as
    # arbitrary text, so the quoting is the thing at risk: an unquoted `8080` or
    # `true` is a ConfigMap value Kubernetes refuses, and the two sides quote in
    # different languages.
    "extra-env": {"platform": "k8s", "extra_env": {
        "PREFERRED_INTERFACE": "eth1", "DODUO_PORT": 8080,
        "KUBERNETES_USE_PRE_PULLING": True}},
}

JSON_ENVS = ("IMAGE_OVERRIDES", "KUBERNETES_TOLERATIONS_JSON",
             "KUBERNETES_NODE_SELECTOR_JSON")

POD_FIELDS = ("tolerations", "nodeSelector", "imagePullSecrets", "volumes",
              "serviceAccountName", "securityContext", "restartPolicy",
              "terminationGracePeriodSeconds")
CONTAINER_FIELDS = ("name", "image", "imagePullPolicy", "resources", "envFrom",
                    "securityContext", "volumeMounts", "livenessProbe",
                    "readinessProbe")


# crane-hook's objects, which are keyed by kind like everything else and would
# collide with the agent's Role, RoleBinding and (in helm) nothing at all. They
# are compared separately, by name, because they are not the deployment: they
# are a check that runs beside it, and on the chart side they are a `helm test`
# hook rather than an installed object.
HOOK_NAMES = ("bzm-cranehook", "bzm-cranehook-binding", "cranehook")


def _is_hook(d):
    return d and d.get("metadata", {}).get("name") in HOOK_NAMES


def _by_kind(docs):
    out = {}
    for d in docs:
        if d and not _is_hook(d):
            out[d["kind"]] = d
    return out


def _by_name(docs):
    return {d["metadata"]["name"]: d for d in docs if _is_hook(d)}


def _helm_docs(outdir, namespace):
    chart = os.path.join(outdir, gen.CHART_DIR)
    values = os.path.join(outdir, gen.HELM_VALUES_FILE)
    for cmd in ([HELM, "lint", "--strict", chart, "-f", values],
                [HELM, "template", "crane", chart, "-n", namespace, "-f", values]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError((r.stderr or r.stdout).strip())
    return list(yaml.safe_load_all(r.stdout))


def compare(name, opts):
    """Return a list of human-readable differences, empty when they agree."""
    outdir = tempfile.mkdtemp(prefix=f"bzm-parity-{name}-")
    try:
        gen.write(gen.generate(FACTS, {**opts, "output_format": "helm"}), outdir)
        helm_docs = _helm_docs(outdir, opts["namespace"])
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
    helm = _by_kind(helm_docs)

    # safe_load_all, not safe_load: a file may hold several documents --
    # bzm_cranehook.yaml holds three.
    flat = [d
            for n, c in gen.generate(
                FACTS, {**opts, "output_format": "manifests"}).items()
            if n.endswith(".yaml")
            for d in yaml.safe_load_all(c)]
    manifests = _by_kind(flat)

    diffs = _hook_diffs(_by_name(flat), _by_name(helm_docs))
    if set(manifests) != set(helm):
        diffs.append(f"object kinds: manifests={sorted(manifests)} "
                     f"helm={sorted(helm)}")
    for kind in sorted(set(manifests) & set(helm)):
        m, h = manifests[kind], helm[kind]
        if kind == "ConfigMap" and m["metadata"]["name"] == "blazemeter-configmap":
            for k in sorted(set(m["data"]) | set(h["data"])):
                mv, hv = m["data"].get(k), h["data"].get(k)
                if k in JSON_ENVS and mv is not None and hv is not None:
                    mv, hv = json.loads(mv), json.loads(hv)
                if mv != hv:
                    diffs.append(f"ConfigMap.{k}: {mv!r} != {hv!r}")
        elif kind == "Deployment":
            if m["spec"]["selector"] != h["spec"]["selector"]:
                diffs.append("Deployment selector differs (immutable in k8s -- "
                             "the two formats would not be upgradeable to each other)")
            mp, hp = (d["spec"]["template"]["spec"] for d in (m, h))
            for f in POD_FIELDS:
                if mp.get(f) != hp.get(f):
                    diffs.append(f"pod.{f}: {mp.get(f)!r} != {hp.get(f)!r}")
            for f in CONTAINER_FIELDS:
                if mp["containers"][0].get(f) != hp["containers"][0].get(f):
                    diffs.append(f"container.{f}: {mp['containers'][0].get(f)!r} "
                                 f"!= {hp['containers'][0].get(f)!r}")
        elif kind in ("RoleBinding", "ClusterRoleBinding"):
            # Not covered by the kind set alone: a binding that grants to the
            # wrong account renders fine and gives crane no permissions at all.
            if m["subjects"] != h["subjects"]:
                diffs.append(f"{kind}.subjects: {m['subjects']} != {h['subjects']}")
            # roleRef is only compared for the namespaced binding. The chart's
            # cluster-scoped names carry the namespace on purpose, so that two
            # locations in two namespaces do not collide over one
            # cluster-role-binding-crane -- see bzm-opl.clusterRoleName.
            if kind == "RoleBinding" and m["roleRef"] != h["roleRef"]:
                diffs.append(f"{kind}.roleRef: {m['roleRef']} != {h['roleRef']}")
        elif kind == "ServiceAccount":
            if m["metadata"]["name"] != h["metadata"]["name"]:
                diffs.append(f"ServiceAccount name: {m['metadata']['name']} != "
                             f"{h['metadata']['name']}")
        elif kind in ("Role", "ClusterRole"):
            if m["rules"] != h["rules"]:
                diffs.append(f"{kind}.rules: {m['rules']} != {h['rules']}")
        elif kind == "Secret" and m["stringData"] != h["stringData"]:
            diffs.append(f"Secret.stringData: {m['stringData']} != {h['stringData']}")
    return diffs


def _hook_diffs(flat, helm):
    """crane-hook, compared by name across the two formats.

    Not by kind: its Role and RoleBinding sit beside the agent's, and a
    kind-keyed map would silently compare one against the other. Its Pod exists
    on both sides but is a `helm test` hook in the chart, so the annotations are
    expected to differ and only what the hook *does* is compared -- the image it
    runs, the account it runs as, and the environment that tells it what to
    check. Everything there is a value the bundle decided twice.
    """
    if set(flat) != set(helm):
        return [f"crane-hook objects: manifests={sorted(flat)} helm={sorted(helm)}"]
    diffs = []
    for name in sorted(flat):
        m, h = flat[name], helm[name]
        if m["kind"] != h["kind"]:
            diffs.append(f"{name}.kind: {m['kind']} != {h['kind']}")
        elif m["kind"] == "Role" and m["rules"] != h["rules"]:
            diffs.append(f"{name}.rules: {m['rules']} != {h['rules']}")
        elif m["kind"] == "RoleBinding":
            for f in ("subjects", "roleRef"):
                if m[f] != h[f]:
                    diffs.append(f"{name}.{f}: {m[f]} != {h[f]}")
        elif m["kind"] == "Pod":
            mp, hp = m["spec"], h["spec"]
            for f in ("serviceAccountName", "restartPolicy", "volumes"):
                if mp.get(f) != hp.get(f):
                    diffs.append(f"{name}.{f}: {mp.get(f)!r} != {hp.get(f)!r}")
            mc, hc = mp["containers"][0], hp["containers"][0]
            for f in ("image", "securityContext", "resources", "volumeMounts"):
                if mc.get(f) != hc.get(f):
                    diffs.append(f"{name}.container.{f}: {mc.get(f)!r} != {hc.get(f)!r}")
            me = {e["name"]: e["value"] for e in mc["env"]}
            he = {e["name"]: e["value"] for e in hc["env"]}
            # The SV variables are the exception, and deliberately: --format
            # helm refuses a service-virtualization location outright, so the
            # chart has no ingress for the hook to be told about.
            me = {k: v for k, v in me.items() if not k.startswith("KUBERNETES_WEB_")}
            if me != he:
                diffs.append(f"{name}.env: {me} != {he}")
    return diffs


def overrides_stay_consistent():
    """A generated bundle must survive `--set` on top of it.

    The overlay is a file people edit and a base people override at install
    time, so nothing in the chart may depend on a value the overlay froze. This
    caught a real one: the overlay used to pin the LimitRange max computed at
    generate time, and `--set engine.memoryLimit=6Gi` then rendered `default`
    above `max`, which the API server rejects -- found by running a real
    `helm upgrade`, which failed with the ConfigMap already applied. The
    LimitRange is gone now; the check stays, because the next frozen value would
    fail the same way.
    """
    opts = {**COMMON, "platform": "k8s", "engine_cpu_limit": "1",
            "engine_mem_limit": "4Gi"}
    outdir = tempfile.mkdtemp(prefix="bzm-parity-override-")
    problems = []
    try:
        gen.write(gen.generate(FACTS, {**opts, "output_format": "helm"}), outdir)
        chart = os.path.join(outdir, gen.CHART_DIR)
        values = os.path.join(outdir, gen.HELM_VALUES_FILE)
        for cpu, mem in (("2", "6Gi"), ("4", "16Gi"), ("500m", "1Gi")):
            r = subprocess.run(
                [HELM, "template", "crane", chart, "-n", opts["namespace"],
                 "-f", values, "--set", f"engine.cpuLimit={cpu}",
                 "--set", f"engine.memoryLimit={mem}"],
                capture_output=True, text=True)
            if r.returncode:
                problems.append(f"--set engine={cpu}/{mem}: render failed: "
                                f"{(r.stderr or '').strip()[:200]}")
                continue
            docs = _by_kind(yaml.safe_load_all(r.stdout))
            cm = docs.get("ConfigMap", {}).get("data", {})
            if cm.get("KUBERNETES_RESOURCES_LIMITS_CPU") != cpu or \
                    cm.get("KUBERNETES_RESOURCES_LIMITS_MEMORY") != mem:
                problems.append(
                    f"--set engine={cpu}/{mem}: the override did not reach the "
                    f"ConfigMap (got {cm.get('KUBERNETES_RESOURCES_LIMITS_CPU')}"
                    f"/{cm.get('KUBERNETES_RESOURCES_LIMITS_MEMORY')})")
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
    return problems


def main():
    if not shutil.which(HELM):
        sys.exit(f"{HELM} not found -- install helm, or set HELM=/path/to/helm")
    failed = 0
    for name, extra in CASES.items():
        opts = {**COMMON, **extra}
        try:
            diffs = compare(name, opts)
        except RuntimeError as e:
            print(f"FAIL {name}: chart did not render\n     {e}")
            failed += 1
            continue
        if diffs:
            failed += 1
            print(f"FAIL {name}")
            for d in diffs:
                print(f"     {d}")
        else:
            print(f"ok   {name}")

    problems = overrides_stay_consistent()
    if problems:
        failed += 1
        print("FAIL overrides-on-a-generated-bundle")
        for p in problems:
            print(f"     {p}")
    else:
        print("ok   overrides-on-a-generated-bundle")

    print()
    if failed:
        sys.exit(f"{failed} check(s) failed")
    print(f"{len(CASES)} cases: helm and manifests render the same objects, "
          f"and a generated bundle survives --set")


if __name__ == "__main__":
    main()
