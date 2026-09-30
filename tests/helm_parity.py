#!/usr/bin/env python3
"""Render every option combination as manifests and as a helm chart, and
require the same objects.

    python tests/helm_parity.py

Not a pytest module: it needs the helm binary, and runs as its own CI job.
JSON-valued ConfigMap entries are compared parsed, since Go and Python encode
them differently.
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
from bzm_opl_gen import bundle_names, service_virt  # noqa: E402

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
    # NODEPORT with namespaced RBAC only: both formats render it.
    "nodeport-namespaced-rbac": {"platform": "k8s", "service_type": "NODEPORT"},
    "private-registry": {"platform": "k8s", "private_registry": "reg.example.com/bzm"},
    "registry-auth": {"platform": "k8s", "private_registry": "reg.example.com/bzm",
                      "registry_auth": True, "pull_secret": "regcred"},
    # Auto-update set explicitly; unset is covered by every other case.
    "auto-update-off": {"platform": "k8s", "auto_update": False},
    "auto-update-on-private-registry": {"platform": "k8s", "auto_update": True,
                                        "private_registry": "reg.example.com/bzm"},
    "proxy": {"platform": "k8s", "proxy": {"http": "http://px:3128"}},
    # Requests follow the limits; neither quantity is a whole Gi.
    "engine-fractional": {"platform": "k8s", "engine_cpu_limit": "1500m",
                          "engine_mem_limit": "6144Mi"},
    # A leading `*` must be quoted, or YAML reads an alias.
    "proxy-wildcard-no-proxy": {"platform": "k8s", "proxy": {
        "http": "http://px:3128", "no_proxy": "*.corp.example,10.0.0.0/8"}},
    # Proxy credentials go to the Secret...
    "proxy-creds": {"platform": "k8s", "proxy": {"http": "http://px:3128",
                    "https": "http://px:3128", "username": "u", "password": "p"}},
    # ...or, with no Secret, to the ConfigMap.
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
    # Two node pools: crane's placement in the pod spec, the engines' in the
    # KUBERNETES_*_JSON env.
    "scheduling-split-pools": {
        "platform": "k8s", "node_selector": {"pool": "crane"},
        "engine_node_selector": {"pool": "bzm-engines"},
        "engine_tolerations": [{"key": "bzm.io/engines", "operator": "Equal",
                                "value": "true", "effect": "NoSchedule"}]},
    # Crane pinned and tainted, engines explicitly unpinned: no engine env.
    "scheduling-engines-unpinned": {
        "platform": "k8s", "node_selector": {"pool": "infra"},
        "tolerations": [{"key": "infra", "operator": "Exists",
                         "effect": "NoSchedule"}],
        "engine_node_selector": {}, "engine_tolerations": []},
    "ephemeral": {"platform": "k8s", "engine_ephemeral_request_mb": 1024,
                  "engine_ephemeral_limit_mb": 61440},
    # Crane's own ephemeral storage override.
    "crane-ephemeral": {"platform": "k8s", "crane_ephemeral_storage": "4Gi"},
    # restrict_engines off; on is covered by every other case.
    "unrestricted-engines": {"platform": "k8s", "restrict_engines": False},
    # The ServiceAccount name reaches the Deployment and both bindings, and
    # create: false removes the object in both formats.
    "service-account-named": {"platform": "k8s", "cluster_rbac": True,
                              "service_account_name": "bzm-agent"},
    "service-account-existing": {"platform": "k8s", "cluster_rbac": True,
                                 "service_account_name": "platform-sa",
                                 "service_account_create": False},
    # crane-hook: three more objects on both sides.
    "crane-hook": {"platform": "k8s", "crane_hook": True},
    "crane-hook-openshift": {"platform": "openshift", "crane_hook": True},
    "crane-hook-private-registry": {"platform": "k8s", "crane_hook": True,
                                    "private_registry": "reg.example.com/bzm",
                                    "service_account_name": "bzm-agent"},
    # Service virtualization: the KUBERNETES_WEB_EXPOSE_* env and the Role's
    # API group, per backend; `none` renders as a performance bundle.
    "sv-nginx": {"platform": "k8s", "sv_ingress": "nginx",
                 "sv_subdomain": "mocks.example.com",
                 "sv_tls_secret": "wildcard-mocks"},
    "sv-nginx-nodeport": {"platform": "k8s", "service_type": "NODEPORT",
                          "sv_ingress": "nginx",
                          "sv_subdomain": "mocks.example.com",
                          "sv_tls_secret": "wildcard-mocks"},
    "sv-istio": {"platform": "k8s", "sv_ingress": "istio",
                 "sv_subdomain": "mocks.example.com",
                 "sv_tls_secret": "wildcard-mocks"},
    "sv-istio-gateway": {"platform": "k8s", "sv_ingress": "istio",
                         "sv_subdomain": "mocks.example.com",
                         "sv_tls_secret": "wildcard-mocks",
                         "sv_istio_gateway": "shared-gateway"},
    "sv-contour": {"platform": "k8s", "sv_ingress": "contour",
                   "sv_subdomain": "mocks.example.com",
                   "sv_tls_secret": "wildcard-mocks"},
    # The chart reads only `platform`; the generator also `openshift_cluster`.
    "sv-openshift": {"platform": "openshift", "openshift_cluster": True,
                     "sv_ingress": "openshift",
                     "sv_subdomain": "apps.example.com",
                     "sv_tls_secret": "wildcard-mocks"},
    "sv-declined": {"platform": "k8s", "sv_ingress": service_virt.SV_INGRESS_NONE},
    "sv-nginx-crane-hook": {"platform": "k8s", "crane_hook": True,
                            "sv_ingress": "nginx",
                            "sv_subdomain": "mocks.example.com",
                            "sv_tls_secret": "wildcard-mocks"},
    # extra_env: numbers and booleans must be quoted on both sides.
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


# crane-hook's objects, compared by name so they do not collide with the
# agent's Role and RoleBinding.
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
    chart = os.path.join(outdir, bundle_names.CHART_DIR)
    values = os.path.join(outdir, bundle_names.HELM_VALUES_FILE)
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

    # bzm_cranehook.yaml holds several documents.
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
            if m["subjects"] != h["subjects"]:
                diffs.append(f"{kind}.subjects: {m['subjects']} != {h['subjects']}")
            # The chart's cluster-scoped names include the namespace.
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
    """crane-hook's objects, compared by name; the Pod by what it runs, not by
    its annotations (the chart's is a `helm test` hook)."""
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
            # securityContext: the pod's seccomp profile, which restricted
            # Pod Security admission requires of both.
            for f in ("serviceAccountName", "restartPolicy", "volumes",
                      "securityContext"):
                if mp.get(f) != hp.get(f):
                    diffs.append(f"{name}.{f}: {mp.get(f)!r} != {hp.get(f)!r}")
            mc, hc = mp["containers"][0], hp["containers"][0]
            for f in ("image", "securityContext", "resources", "volumeMounts"):
                if mc.get(f) != hc.get(f):
                    diffs.append(f"{name}.container.{f}: {mc.get(f)!r} != {hc.get(f)!r}")
            me = {e["name"]: e["value"] for e in mc["env"]}
            he = {e["name"]: e["value"] for e in hc["env"]}
            if me != he:
                diffs.append(f"{name}.env: {me} != {he}")
    return diffs


def overrides_stay_consistent():
    """Engine limits set with `--set` on top of a generated overlay reach the
    ConfigMap."""
    opts = {**COMMON, "platform": "k8s", "engine_cpu_limit": "1",
            "engine_mem_limit": "4Gi"}
    outdir = tempfile.mkdtemp(prefix="bzm-parity-override-")
    problems = []
    try:
        gen.write(gen.generate(FACTS, {**opts, "output_format": "helm"}), outdir)
        chart = os.path.join(outdir, bundle_names.CHART_DIR)
        values = os.path.join(outdir, bundle_names.HELM_VALUES_FILE)
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


# Service virtualization values the chart refuses (as service_virt.sv_cfg
# does), and a phrase each refusal must contain. Passed as --set, since the
# generator refuses them before writing an overlay.
SV_REFUSALS = {
    "contour-nodeport": (["--set", "sv.ingress=contour",
                          "--set", "serviceType=NODEPORT"],
                         "requires serviceType: CLUSTERIP"),
    "istio-nodeport": (["--set", "sv.ingress=istio",
                        "--set", "serviceType=NODEPORT"],
                       "requires serviceType: CLUSTERIP"),
    "route-on-plain-k8s": (["--set", "sv.ingress=openshift"],
                           "requires platform: openshift"),
    "gateway-no-istio": (["--set", "sv.ingress=nginx",
                          "--set", "sv.istioGateway=gw"],
                         "only meaningful with sv.ingress istio"),
    "unknown-backend": (["--set", "sv.ingress=traefik"],
                        "sv.ingress must be one of"),
    "no-subdomain": (["--set", "sv.ingress=nginx", "--set", "sv.subdomain="],
                     "also requires sv.subdomain and sv.tlsSecret"),
    "marker-left-in": (["--set", "sv.ingress=nginx",
                        "--set-string", "sv.subdomain=<SV_SUBDOMAIN>"],
                       "was left blank when this bundle was generated"),
}


def sv_refusals_still_refuse():
    """Every refused combination fails the render, naming the fix."""
    chart = os.path.join(os.path.dirname(__file__), "..", "bzm_opl_gen",
                         "templates", "helm")
    base = ["--set", "harborId=h", "--set", "shipId=s",
            "--set-string", "authToken=t",
            "--set", "sv.subdomain=apps.example.com",
            "--set", "sv.tlsSecret=wildcard-mocks"]
    problems = []
    for name, (args, expected) in SV_REFUSALS.items():
        r = subprocess.run([HELM, "template", "crane", chart, "-n", "bzm-perf"]
                           + base + args, capture_output=True, text=True)
        if not r.returncode:
            problems.append(f"{name}: the chart rendered it")
        elif expected not in (r.stderr or "") + (r.stdout or ""):
            problems.append(f"{name}: refused, but not for the stated reason "
                            f"-- wanted {expected!r}, got "
                            f"{(r.stderr or r.stdout).strip()[:160]!r}")
    # ...while nginx with NODEPORT renders.
    r = subprocess.run([HELM, "template", "crane", chart, "-n", "bzm-perf"]
                       + base + ["--set", "sv.ingress=nginx",
                                 "--set", "serviceType=NODEPORT"],
                       capture_output=True, text=True)
    if r.returncode:
        problems.append("nginx-nodeport: refused, and it is the pairing that "
                        f"works: {(r.stderr or r.stdout).strip()[:160]}")
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

    problems = sv_refusals_still_refuse()
    if problems:
        failed += 1
        print("FAIL service-virtualization-refusals")
        for p in problems:
            print(f"     {p}")
    else:
        print("ok   service-virtualization-refusals")

    print()
    if failed:
        sys.exit(f"{failed} check(s) failed")
    print(f"{len(CASES)} cases: helm and manifests render the same objects, "
          f"and a generated bundle survives --set")


if __name__ == "__main__":
    main()
