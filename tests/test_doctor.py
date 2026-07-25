"""Offline tests for the pre-flight doctor.

Every check is a pure verdict over already-fetched cluster JSON, so the whole
file runs with no cluster and no network: the fixtures below are what
`kubectl get nodes/limitrange/resourcequota/ns -o json` actually returns.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import doctor, facts as facts_mod  # noqa: E402


# -- fixtures ---------------------------------------------------------------

def _node(name="n1", cpu="4", mem="6088480Ki", disk="17734596Ki", ready=True,
          labels=None, taints=None, unschedulable=False):
    """A node as the API server reports it. Defaults are a real minikube node:
    4 CPU, ~5.8Gi allocatable memory, ~17GB ephemeral storage."""
    node = {
        "metadata": {"name": name, "labels": labels or {}},
        "spec": {},
        "status": {
            "allocatable": {"cpu": cpu, "memory": mem, "ephemeral-storage": disk},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }
    if taints:
        node["spec"]["taints"] = taints
    if unschedulable:
        node["spec"]["unschedulable"] = True
    return node


def _big(name="big1"):
    """A node that comfortably holds engines at the documented 2 CPU / 8Gi."""
    return _node(name, cpu="16", mem="64Gi", disk="500Gi")


FACTS = {"harbor_id": "aaa111", "harbor_name": "Test Location",
         "slots": 2, "threads_per_engine": 500}

LR_MATCHING = {
    "metadata": {"name": "blazemeter-engine-sizing"},
    "spec": {"limits": [{"type": "Container",
                         "defaultRequest": {"cpu": "2", "memory": "8Gi"},
                         "default": {"cpu": "2", "memory": "8Gi"},
                         "max": {"cpu": "2", "memory": "8Gi"}}]},
}

NS_BASELINE = {"metadata": {"name": "blazemeter",
                            "labels": {"pod-security.kubernetes.io/enforce": "baseline"}}}


def _find(checks, needle):
    hits = [c for c in checks if needle in c.name]
    assert hits, f"no check matching {needle!r} in {[c.name for c in checks]}"
    return hits[0]


def _statuses(checks):
    return {c.status for c in checks}


# -- facts ------------------------------------------------------------------

class _FakeClient:
    def __init__(self, harbor):
        self._h = harbor

    def private_location(self, harbor_id):
        return self._h


def test_facts_gather_reads_threads_per_engine():
    """Sizing checks are meaningless without it, and it is the field a fresh
    location has unset."""
    f = facts_mod.gather(_FakeClient(
        {"id": "aaa111", "name": "loc", "funcIds": ["performance"],
         "slots": 3, "threadsPerEngine": 500, "ships": []}), "aaa111")
    assert f["slots"] == 3
    assert f["threads_per_engine"] == 500


def test_facts_gather_threads_per_engine_absent_is_none():
    f = facts_mod.gather(_FakeClient(
        {"id": "aaa111", "name": "loc", "funcIds": ["performance"],
         "slots": 1, "ships": []}), "aaa111")
    assert f["threads_per_engine"] is None


# -- check_location ---------------------------------------------------------

def test_location_ok():
    assert _statuses(doctor.check_location(FACTS)) == {doctor.PASS}


def test_location_missing_threads_per_engine_fails():
    c = _find(doctor.check_location({**FACTS, "threads_per_engine": None}),
              "threadsPerEngine")
    assert c.status == doctor.FAIL
    assert "403" in c.detail            # what the customer actually sees


@pytest.mark.parametrize("slots", [None, 0])
def test_location_without_slots_fails(slots):
    c = _find(doctor.check_location({**FACTS, "slots": slots}), "slots")
    assert c.status == doctor.FAIL


# -- check_threads_per_engine ----------------------------------------------

@pytest.mark.parametrize("threads,opts,status", [
    (500, {}, doctor.PASS),                                            # the documented pairing
    (250, {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}, doctor.PASS),
    (500, {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}, doctor.WARN),
    (1000, {}, doctor.WARN),
    (500, {"engine_cpu_limit": "4", "engine_mem_limit": "16Gi"}, doctor.PASS),
    # Memory is the binding constraint here, not CPU.
    (500, {"engine_cpu_limit": "4", "engine_mem_limit": "2Gi"}, doctor.WARN),
])
def test_threads_per_engine_verdicts(threads, opts, status):
    checks = doctor.check_threads_per_engine({**FACTS, "threads_per_engine": threads},
                                             opts)
    assert [c.status for c in checks] == [status]


def test_threads_per_engine_names_the_arithmetic():
    c = doctor.check_threads_per_engine(
        {**FACTS, "threads_per_engine": 500},
        {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"})[0]
    assert "250" in c.detail and "500" in c.detail


def test_threads_per_engine_silent_when_unset():
    """check_location already FAILs on this; don't say it twice."""
    assert doctor.check_threads_per_engine({**FACTS, "threads_per_engine": None},
                                           {}) == []


# -- eligible_nodes ---------------------------------------------------------

@pytest.mark.parametrize("node,opts,eligible", [
    (_node(), {}, True),
    (_node(unschedulable=True), {}, False),                # cordoned
    (_node(ready=False), {}, False),
    (_node(taints=[{"key": "lifecycle", "value": "spot", "effect": "NoSchedule"}]),
     {}, False),
    # A taint we tolerate does not exclude the node.
    (_node(taints=[{"key": "lifecycle", "value": "spot", "effect": "NoSchedule"}]),
     {"tolerations": [{"key": "lifecycle", "operator": "Equal", "value": "spot",
                       "effect": "NoSchedule"}]}, True),
    (_node(taints=[{"key": "lifecycle", "value": "spot", "effect": "NoSchedule"}]),
     {"tolerations": [{"operator": "Exists"}]}, True),
    # PreferNoSchedule is a hint, not a rejection.
    (_node(taints=[{"key": "x", "effect": "PreferNoSchedule"}]), {}, True),
    (_node(labels={"pool": "loadtest"}), {"node_selector": {"pool": "loadtest"}}, True),
    (_node(labels={"pool": "web"}), {"node_selector": {"pool": "loadtest"}}, False),
    (_node(), {"node_selector": {"pool": "loadtest"}}, False),
])
def test_eligible_nodes(node, opts, eligible):
    assert bool(doctor.eligible_nodes([node], opts)) is eligible


# -- check_capacity ---------------------------------------------------------

def test_capacity_ok_on_a_real_cluster():
    checks = doctor.check_capacity(FACTS, {}, [_big("a"), _big("b")])
    assert _statuses(checks) == {doctor.PASS}
    # allocatable is an upper bound, not free space -- say so.
    assert any("allocatable" in c.detail for c in checks)


def test_capacity_per_node_fit_fails_when_no_node_holds_one_engine():
    """A pod is not splittable: three 5.8Gi nodes cannot run one 8Gi engine."""
    checks = doctor.check_capacity(FACTS, {}, [_node("n1"), _node("n2"), _node("n3")])
    fit = _find(checks, "per-node")
    assert fit.status == doctor.FAIL
    assert "8Gi" in fit.detail


def test_capacity_aggregate_fails_and_counts_engines():
    nodes = [_node("n1", cpu="4", mem="8Gi", disk="500Gi"),
             _node("n2", cpu="4", mem="8Gi", disk="500Gi")]
    checks = doctor.check_capacity({**FACTS, "slots": 5}, {}, nodes)
    assert _find(checks, "per-node").status == doctor.PASS
    agg = _find(checks, "aggregate")
    assert agg.status == doctor.FAIL
    assert "2 engine" in agg.detail          # 16Gi total / 8Gi each


def test_capacity_uses_the_configured_engine_size():
    """Sized down for a laptop, one engine fits the same node that cannot hold
    a documented 2 CPU / 8Gi one."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    assert _statuses(doctor.check_capacity({**FACTS, "slots": 1}, opts,
                                           [_node()])) == {doctor.PASS}
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, {}, [_node()]),
                 "per-node").status == doctor.FAIL


def test_capacity_fails_when_the_selector_matches_nothing():
    checks = doctor.check_capacity(FACTS, {"node_selector": {"pool": "loadtest"}},
                                   [_big("a")])
    assert doctor.FAIL in _statuses(checks)
    assert any("pool" in c.detail for c in checks)


def test_capacity_can_subtract_already_allocated():
    """Optional: allocatable minus what is already requested on the node."""
    nodes = [_big("a")]
    checks = doctor.check_capacity({**FACTS, "slots": 1}, {}, nodes,
                                   allocated={"a": ("15500m", "60Gi")})
    assert doctor.FAIL in _statuses(checks)


# -- check_disk -------------------------------------------------------------

def test_disk_warns_on_a_laptop_node():
    c = doctor.check_disk(FACTS, [_node()])[0]
    assert c.status == doctor.WARN
    assert "60" in c.detail and "40" in c.detail       # total and /tmp


def test_disk_ok_on_a_real_node():
    assert doctor.check_disk(FACTS, [_big("a")])[0].status == doctor.PASS


def test_disk_warns_when_the_cluster_cannot_hold_every_slot():
    nodes = [_node("n1", disk="100G"), _node("n2", disk="100G")]
    c = doctor.check_disk({**FACTS, "slots": 5}, nodes)[0]
    assert c.status == doctor.WARN
    assert "2" in c.detail                              # one engine per node


def test_disk_ignores_ineligible_nodes():
    nodes = [_big("a"), _node("cordoned", disk="1Gi", unschedulable=True)]
    assert doctor.check_disk(FACTS, nodes, {})[0].status == doctor.PASS


# -- check_limitrange -------------------------------------------------------

def test_limitrange_absent_warns_about_cranes_defaults():
    c = doctor.check_limitrange({}, [])[0]
    assert c.status == doctor.WARN
    assert "250m" in c.detail and "256Mi" in c.detail


def test_limitrange_absent_is_fine_when_we_emit_one():
    assert doctor.check_limitrange({"emit_limitrange": True}, [])[0].status == doctor.PASS


def test_limitrange_matching_passes():
    assert _statuses(doctor.check_limitrange({}, [LR_MATCHING])) == {doctor.PASS}


def test_limitrange_max_below_engine_fails():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "2Gi"}}]}}
    c = doctor.check_limitrange({}, [lr])[0]
    assert c.status == doctor.FAIL
    assert "team-caps" in c.detail                      # name the object


def test_limitrange_conflicting_defaults_warn():
    lr = {"metadata": {"name": "platform-defaults"},
          "spec": {"limits": [{"type": "Container",
                               "defaultRequest": {"cpu": "500m", "memory": "1Gi"},
                               "default": {"cpu": "1", "memory": "2Gi"}}]}}
    checks = doctor.check_limitrange({}, [lr])
    assert doctor.WARN in _statuses(checks)
    assert any("platform-defaults" in c.detail for c in checks)


def test_limitrange_max_measured_against_the_configured_engine():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "4Gi"},
                               "default": {"cpu": "1", "memory": "4Gi"},
                               "defaultRequest": {"cpu": "1", "memory": "4Gi"}}]}}
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    assert _statuses(doctor.check_limitrange(opts, [lr])) == {doctor.PASS}


# -- check_resourcequota ----------------------------------------------------

def _quota(name="team-quota", hard=None, used=None):
    return {"metadata": {"name": name},
            "status": {"hard": hard or {}, "used": used or {}}}


def test_resourcequota_absent_passes():
    assert _statuses(doctor.check_resourcequota(FACTS, {}, [])) == {doctor.PASS}


def test_resourcequota_with_room_passes():
    q = _quota(hard={"limits.cpu": "20", "limits.memory": "80Gi", "pods": "50"},
               used={"limits.cpu": "2", "limits.memory": "4Gi", "pods": "3"})
    checks = doctor.check_resourcequota(FACTS, {}, [q], [LR_MATCHING])
    assert _statuses(checks) == {doctor.PASS}


def test_resourcequota_too_small_for_the_concurrency_fails():
    """slots=2 needs 4 CPU / 16Gi of quota headroom."""
    q = _quota(hard={"limits.cpu": "4", "limits.memory": "8Gi"},
               used={"limits.cpu": "1", "limits.memory": "2Gi"})
    checks = doctor.check_resourcequota(FACTS, {}, [q], [LR_MATCHING])
    fails = [c for c in checks if c.status == doctor.FAIL]
    assert fails and all("team-quota" in c.detail for c in fails)
    assert any("limits.memory" in c.detail for c in fails)


def test_resourcequota_pod_count_includes_crane():
    q = _quota(hard={"pods": "2"}, used={"pods": "0"})      # 2 engines + crane = 3
    checks = doctor.check_resourcequota(FACTS, {}, [q], [LR_MATCHING])
    assert any(c.status == doctor.FAIL and "pods" in c.detail for c in checks)


def test_resourcequota_counts_the_cpu_alias_as_requests():
    q = _quota(hard={"cpu": "3"}, used={"cpu": "0"})        # alias of requests.cpu
    checks = doctor.check_resourcequota(FACTS, {}, [q], [LR_MATCHING])
    assert any(c.status == doctor.FAIL for c in checks)


def test_resourcequota_without_a_limitrange_warns_about_explicit_requests():
    """With a cpu/memory quota, k8s rejects any pod that does not declare that
    resource -- and crane sets no requests on the engines it spawns."""
    q = _quota(hard={"requests.cpu": "40", "requests.memory": "160Gi"},
               used={"requests.cpu": "0", "requests.memory": "0"})
    checks = doctor.check_resourcequota(FACTS, {}, [q], [])
    warn = [c for c in checks if c.status == doctor.WARN]
    assert warn and any("LimitRange" in c.detail for c in warn)


# -- check_admission --------------------------------------------------------

def test_admission_k8s_restricted_fails_on_the_engine_pods():
    ns = {"metadata": {"labels": {"pod-security.kubernetes.io/enforce": "restricted"}}}
    c = doctor.check_admission({"platform": "k8s"}, ns)[0]
    assert c.status == doctor.FAIL
    assert "engine" in c.detail


def test_admission_k8s_baseline_passes():
    assert doctor.check_admission({"platform": "k8s"}, NS_BASELINE)[0].status == doctor.PASS


def test_admission_k8s_unlabelled_warns():
    c = doctor.check_admission({"platform": "k8s"}, {"metadata": {"labels": {}}})[0]
    assert c.status == doctor.WARN


def test_admission_openshift_needs_a_uid_range():
    ns = {"metadata": {"annotations": {"openshift.io/sa.scc.uid-range": "1000700000/10000"}}}
    assert doctor.check_admission({"platform": "openshift"}, ns)[0].status == doctor.PASS
    c = doctor.check_admission({"platform": "openshift"}, {"metadata": {}})[0]
    assert c.status == doctor.WARN
    assert "INHERIT_RUNNING_USER_AND_GROUP" in c.detail


# -- egress -----------------------------------------------------------------

def test_egress_targets_include_the_private_registry():
    targets = doctor.egress_targets({"private_registry": "reg.corp:5001/blazemeter"})
    assert any("a.blazemeter.com" in t for t in targets)
    assert any("reg.corp:5001" in t for t in targets)
    assert len(doctor.egress_targets({})) == 1


@pytest.mark.parametrize("rc,status,marker", [
    (0, doctor.PASS, None),
    (6, doctor.FAIL, "proxy"),          # DNS: reminder that a proxy/CA must be honoured
    (28, doctor.FAIL, "proxy"),
    (None, doctor.WARN, None),          # could not probe -- never a false FAIL
])
def test_egress_verdicts(rc, status, marker):
    c = doctor.check_egress({doctor.API_PROBE_URL: rc})[0]
    assert c.status == status
    assert doctor.API_PROBE_URL in c.detail
    if marker:
        assert marker in c.detail


def test_egress_without_probes_warns():
    assert doctor.check_egress(None)[0].status == doctor.WARN


def test_probe_egress_uses_the_crane_pod(monkeypatch):
    from bzm_opl_gen import livetest
    seen = []
    monkeypatch.setattr(doctor, "_crane_deployed", lambda cli, ns: True)
    monkeypatch.setattr(livetest, "_crane_exec",
                        lambda cli, ns, sh: seen.append(sh) or "rc=0")
    probes = doctor.probe_egress("kubectl", "ns1", {"ca_bundle": "PEM"})
    assert probes == {doctor.API_PROBE_URL: 0}
    # The CA the profile configures has to be the one curl verifies against.
    assert '--cacert "$REQUESTS_CA_BUNDLE"' in seen[0]


def test_probe_egress_reports_a_failed_exec_as_unknown(monkeypatch):
    """_crane_curl returns -1 when the exec itself never ran (pod not ready,
    no shell). That is 'we could not look', not 'BlazeMeter is unreachable'."""
    from bzm_opl_gen import livetest
    monkeypatch.setattr(doctor, "_crane_deployed", lambda cli, ns: True)
    monkeypatch.setattr(livetest, "_crane_exec", lambda cli, ns, sh: "")
    assert doctor.probe_egress("kubectl", "ns1", {}) == {doctor.API_PROBE_URL: None}


def test_probe_egress_cannot_honour_a_ca_without_crane(monkeypatch):
    """A bare curl pod has no trust bundle; report 'unknown', not 'broken'."""
    monkeypatch.setattr(doctor, "_crane_deployed", lambda cli, ns: False)
    probes = doctor.probe_egress("kubectl", "ns1", {"ca_bundle": "PEM"})
    assert probes == {doctor.API_PROBE_URL: None}


def test_probe_egress_falls_back_to_a_one_shot_pod(monkeypatch):
    monkeypatch.setattr(doctor, "_crane_deployed", lambda cli, ns: False)
    monkeypatch.setattr(doctor, "_oneshot_curl", lambda cli, ns, args, opts: 7)
    assert doctor.probe_egress("kubectl", "ns1", {}) == {doctor.API_PROBE_URL: 7}


# -- gather_cluster ---------------------------------------------------------

def test_gather_cluster_shape(monkeypatch):
    payloads = {"nodes": {"items": [_big("a")]},
                "limitrange": {"items": [LR_MATCHING]},
                "resourcequota": {"items": []},
                "ns": NS_BASELINE}

    def fake_run(cmd, **kw):
        kind = next(k for k in payloads if k in cmd)
        return type("R", (), {"returncode": 0, "stdout": json.dumps(payloads[kind])})()

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    data = doctor.gather_cluster("kubectl", "ns1")
    assert [n["metadata"]["name"] for n in data["nodes"]] == ["a"]
    assert data["limitranges"] == [LR_MATCHING]
    assert data["quotas"] == []
    assert data["namespace"] == NS_BASELINE


def test_gather_cluster_survives_a_missing_namespace(monkeypatch):
    """`get ns` fails on a namespace that does not exist yet -- that is the
    normal pre-flight case, not a crash."""
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": ""})())
    data = doctor.gather_cluster("kubectl", "ns1")
    assert data == {"nodes": [], "limitranges": [], "quotas": [], "namespace": {}}


# -- run() ------------------------------------------------------------------

HEALTHY = {"nodes": [_big("a"), _big("b")], "limitranges": [LR_MATCHING],
           "quotas": [], "namespace": NS_BASELINE}


def test_run_healthy_cluster_has_no_failures(capsys):
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter",
                        cluster_data=HEALTHY, probes={doctor.API_PROBE_URL: 0})
    assert not doctor.has_failures(checks)
    assert doctor.FAIL not in _statuses(checks)
    out = capsys.readouterr().out
    assert "PASS" in out and "location slots" in out


def test_run_broken_cluster_fails(capsys):
    broken = {"nodes": [_node("n1"), _node("n2")],
              "limitranges": [],
              "quotas": [_quota(hard={"pods": "1"}, used={"pods": "0"})],
              "namespace": {"metadata": {"labels":
                            {"pod-security.kubernetes.io/enforce": "restricted"}}}}
    checks = doctor.run({**FACTS, "threads_per_engine": None},
                        {"platform": "k8s"}, "blazemeter",
                        cluster_data=broken, probes={doctor.API_PROBE_URL: 28})
    assert doctor.has_failures(checks)
    out = capsys.readouterr().out
    for marker in ("threadsPerEngine", "per-node", "pods", "admission", "egress"):
        assert marker in out
    assert "FAIL" in out


def test_run_gathers_when_nothing_is_injected(monkeypatch):
    called = {}

    def fake_gather(cli, ns):
        called["gather"] = (cli, ns)
        return HEALTHY

    monkeypatch.setattr(doctor, "_cli", lambda cli: "kubectl")
    monkeypatch.setattr(doctor, "gather_cluster", fake_gather)
    monkeypatch.setattr(doctor, "probe_egress",
                        lambda cli, ns, opts: {doctor.API_PROBE_URL: 0})
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter")
    assert called["gather"] == ("kubectl", "blazemeter")
    assert not doctor.has_failures(checks)
