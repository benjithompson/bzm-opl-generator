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
    assert _statuses(doctor.check_location(FACTS, {}, {})) == {doctor.PASS}


def test_location_missing_threads_per_engine_fails():
    c = _find(doctor.check_location({**FACTS, "threads_per_engine": None}, {}, {}),
              "threadsPerEngine")
    assert c.status == doctor.FAIL
    assert "403" in c.detail            # what the customer actually sees


@pytest.mark.parametrize("slots", [None, 0])
def test_location_without_slots_fails(slots):
    c = _find(doctor.check_location({**FACTS, "slots": slots}, {}, {}), "slots")
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
    checks = doctor.check_threads_per_engine({**FACTS, "threads_per_engine": threads}, opts, {})
    assert [c.status for c in checks] == [status]


def test_threads_per_engine_names_the_arithmetic():
    c = doctor.check_threads_per_engine(
        {**FACTS, "threads_per_engine": 500},
        {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}, {})[0]
    assert "250" in c.detail and "500" in c.detail


def test_threads_per_engine_silent_when_unset():
    """check_location already FAILs on this; don't say it twice."""
    assert doctor.check_threads_per_engine({**FACTS, "threads_per_engine": None}, {}, {}) == []


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
    checks = doctor.check_capacity(FACTS, {}, {"nodes": [_big("a"), _big("b")]})
    assert _statuses(checks) == {doctor.PASS}
    # allocatable is an upper bound, not free space -- say so.
    assert any("allocatable" in c.detail for c in checks)


def test_capacity_per_node_fit_fails_when_no_node_holds_one_engine():
    """A pod is not splittable: three 5.8Gi nodes cannot run one 8Gi engine."""
    checks = doctor.check_capacity(FACTS, {}, {"nodes": [_node("n1"), _node("n2"), _node("n3")]})
    fit = _find(checks, "per-node")
    assert fit.status == doctor.FAIL
    assert "8Gi" in fit.detail


def test_capacity_aggregate_fails_and_counts_engines():
    nodes = [_node("n1", cpu="4", mem="8Gi", disk="500Gi"),
             _node("n2", cpu="4", mem="8Gi", disk="500Gi")]
    checks = doctor.check_capacity({**FACTS, "slots": 5}, {}, {"nodes": nodes})
    assert _find(checks, "per-node").status == doctor.PASS
    agg = _find(checks, "aggregate")
    assert agg.status == doctor.FAIL
    # 16Gi across the two nodes, less crane's own 2Gi -- one 8Gi engine, not two.
    assert "1 engine" in agg.detail
    assert "crane" in agg.detail


def test_capacity_uses_the_configured_engine_size():
    """Sized down for a laptop, one engine fits the same node that cannot hold
    a documented 2 CPU / 8Gi one."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, opts, {"nodes": [_node()]}),
                 "per-node").status == doctor.PASS
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, {}, {"nodes": [_node()]}),
                 "per-node").status == doctor.FAIL


def test_capacity_aggregate_spends_cranes_own_share():
    """Crane runs in the same namespace: a 5.8Gi node cannot hold both crane
    (2Gi) and a 4Gi engine, even though the engine alone fits."""
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    agg = _find(doctor.check_capacity({**FACTS, "slots": 1}, opts, {"nodes": [_node()]}),
                "aggregate")
    assert agg.status == doctor.FAIL
    # Two of those nodes leave room once crane is paid for.
    assert _find(doctor.check_capacity({**FACTS, "slots": 1}, opts, {"nodes": [_node("n1"), _node("n2")]}),
                 "aggregate").status == doctor.PASS


def test_capacity_fails_when_the_selector_matches_nothing():
    checks = doctor.check_capacity(FACTS, {"node_selector": {"pool": "loadtest"}}, {"nodes": [_big("a")]})
    assert doctor.FAIL in _statuses(checks)
    assert any("pool" in c.detail for c in checks)


def test_capacity_says_allocatable_is_an_upper_bound():
    """The verdict must not read as 'there is room' -- allocatable counts what
    other workloads already hold."""
    checks = doctor.check_capacity(FACTS, {}, {"nodes": [_big("a")]})
    assert any("upper bound" in c.detail for c in checks)
    assert all("not free" in c.detail or "upper bound" in c.detail
               for c in checks)


# -- check_disk -------------------------------------------------------------

def test_disk_warns_on_a_laptop_node():
    c = doctor.check_disk(FACTS, {}, {"nodes": [_node()]})[0]
    assert c.status == doctor.WARN
    assert "60" in c.detail and "40" in c.detail       # total and /tmp


def test_disk_ok_on_a_real_node():
    assert doctor.check_disk(FACTS, {}, {"nodes": [_big("a")]})[0].status == doctor.PASS


def test_disk_warns_when_the_cluster_cannot_hold_every_slot():
    nodes = [_node("n1", disk="100G"), _node("n2", disk="100G")]
    c = doctor.check_disk({**FACTS, "slots": 5}, {}, {"nodes": nodes})[0]
    assert c.status == doctor.WARN
    assert "2" in c.detail                              # one engine per node


def test_disk_ignores_ineligible_nodes():
    nodes = [_big("a"), _node("cordoned", disk="1Gi", unschedulable=True)]
    assert doctor.check_disk(FACTS, {}, {"nodes": nodes})[0].status == doctor.PASS


# -- check_limitrange -------------------------------------------------------

def test_limitrange_absent_warns_about_cranes_defaults():
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": []})[0]
    assert c.status == doctor.WARN
    assert "250m" in c.detail and "256Mi" in c.detail


def test_limitrange_matching_passes():
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [LR_MATCHING]})) == {doctor.PASS}


def test_limitrange_max_below_engine_fails():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "2Gi"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "team-caps" in c.detail                      # name the object


def test_limitrange_min_above_the_stamped_request_fails():
    """min rejects from below exactly as max does from above, and it is measured
    against what crane actually requests (250m/256Mi), not the engine's limits --
    a namespace that insists on 1 CPU minimum rejects every engine pod."""
    lr = {"metadata": {"name": "floor"},
          "spec": {"limits": [{"type": "Container", "min": {"cpu": "1", "memory": "1Gi"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "min cpu" in c.detail and "250m" in c.detail


def test_limitrange_ratio_tighter_than_the_engines_own_gap_fails():
    """The engine limits 8Gi while requesting 256Mi -- a 32x ratio. Any
    maxLimitRequestRatio below that rejects it."""
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "4", "memory": "4"}}]}}
    c = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})[0]
    assert c.status == doctor.FAIL
    assert "maxLimitRequestRatio" in c.detail


def test_limitrange_ratio_wide_enough_passes():
    lr = {"metadata": {"name": "ratio"},
          "spec": {"limits": [{"type": "Container",
                               "maxLimitRequestRatio": {"cpu": "16", "memory": "64"}}]}}
    assert _statuses(doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})) == {doctor.PASS}


def test_limitrange_absent_does_not_promise_a_fix_it_cannot_deliver():
    """Crane stamps the engine's requests explicitly, so no LimitRange can raise
    them -- the WARN must not tell a customer that emitting one would."""
    detail = doctor.check_limitrange(FACTS, {}, {"limitranges": []})[0].detail
    assert "cannot override" in detail


def test_limitrange_conflicting_defaults_warn():
    lr = {"metadata": {"name": "platform-defaults"},
          "spec": {"limits": [{"type": "Container",
                               "defaultRequest": {"cpu": "500m", "memory": "1Gi"},
                               "default": {"cpu": "1", "memory": "2Gi"}}]}}
    checks = doctor.check_limitrange(FACTS, {}, {"limitranges": [lr]})
    assert doctor.WARN in _statuses(checks)
    assert any("platform-defaults" in c.detail for c in checks)


def test_limitrange_max_measured_against_the_configured_engine():
    lr = {"metadata": {"name": "team-caps"},
          "spec": {"limits": [{"type": "Container", "max": {"cpu": "1", "memory": "4Gi"},
                               "default": {"cpu": "1", "memory": "4Gi"},
                               "defaultRequest": {"cpu": "1", "memory": "4Gi"}}]}}
    opts = {"engine_cpu_limit": "1", "engine_mem_limit": "4Gi"}
    assert _statuses(doctor.check_limitrange(FACTS, opts, {"limitranges": [lr]})) == {doctor.PASS}


# -- check_resourcequota ----------------------------------------------------

def _quota(name="team-quota", hard=None, used=None):
    return {"metadata": {"name": name},
            "status": {"hard": hard or {}, "used": used or {}}}


def test_resourcequota_absent_passes():
    assert _statuses(doctor.check_resourcequota(FACTS, {}, {"quotas": []})) == {doctor.PASS}


def test_resourcequota_with_room_passes():
    q = _quota(hard={"limits.cpu": "20", "limits.memory": "80Gi", "pods": "50"},
               used={"limits.cpu": "2", "limits.memory": "4Gi", "pods": "3"})
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    assert _statuses(checks) == {doctor.PASS}


def test_resourcequota_too_small_for_the_concurrency_fails():
    """slots=2 needs 4 CPU / 16Gi of quota headroom."""
    q = _quota(hard={"limits.cpu": "4", "limits.memory": "8Gi"},
               used={"limits.cpu": "1", "limits.memory": "2Gi"})
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    fails = [c for c in checks if c.status == doctor.FAIL]
    assert fails and all("team-quota" in c.detail for c in fails)
    assert any("limits.memory" in c.detail for c in fails)


def test_resourcequota_pod_count_includes_crane():
    q = _quota(hard={"pods": "2"}, used={"pods": "0"})      # 2 engines + crane = 3
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    assert any(c.status == doctor.FAIL and "pods" in c.detail for c in checks)


def test_resourcequota_counts_the_cpu_alias_as_requests():
    q = _quota(hard={"cpu": "3"}, used={"cpu": "0"})        # alias of requests.cpu
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": [LR_MATCHING]})
    assert any(c.status == doctor.FAIL for c in checks)


def test_resourcequota_without_a_limitrange_warns_about_explicit_requests():
    """With a cpu/memory quota, k8s rejects any pod that does not declare that
    resource -- and crane sets no requests on the engines it spawns."""
    q = _quota(hard={"requests.cpu": "40", "requests.memory": "160Gi"},
               used={"requests.cpu": "0", "requests.memory": "0"})
    checks = doctor.check_resourcequota(FACTS, {}, {"quotas": [q], "limitranges": []})
    warn = [c for c in checks if c.status == doctor.WARN]
    assert warn and any("LimitRange" in c.detail for c in warn)


# -- check_admission --------------------------------------------------------

def test_admission_k8s_restricted_fails_on_the_engine_pods():
    ns = {"metadata": {"labels": {"pod-security.kubernetes.io/enforce": "restricted"}}}
    c = doctor.check_admission(FACTS, {"platform": "k8s"}, {"namespace": ns})[0]
    assert c.status == doctor.FAIL
    assert "engine" in c.detail


def test_admission_k8s_baseline_passes():
    assert doctor.check_admission(FACTS, {"platform": "k8s"}, {"namespace": NS_BASELINE})[0].status == doctor.PASS


def test_admission_k8s_unlabelled_warns():
    c = doctor.check_admission(FACTS, {"platform": "k8s"},
                               {"namespace": {"metadata": {"labels": {}}}})[0]
    assert c.status == doctor.WARN


def test_admission_openshift_needs_a_uid_range():
    ns = {"metadata": {"annotations": {"openshift.io/sa.scc.uid-range": "1000700000/10000"}}}
    assert doctor.check_admission(FACTS, {"platform": "openshift"}, {"namespace": ns})[0].status == doctor.PASS
    c = doctor.check_admission(FACTS, {"platform": "openshift"},
                               {"namespace": {"metadata": {}}})[0]
    assert c.status == doctor.WARN
    assert "INHERIT_RUNNING_USER_AND_GROUP" in c.detail


# -- check_service_account ---------------------------------------------------
# The live counterpart is a run that never produces a pod: the Deployment
# applies, the ReplicaSet records `serviceaccounts "x" not found`, and nothing
# else says anything. Cheap to catch here, expensive to catch there.

EXISTING_SA = {"service_account_name": "platform-sa",
               "service_account_create": False}


def _sa(*names):
    return [{"metadata": {"name": n}} for n in ("default",) + names]


def test_service_account_silent_when_the_bundle_creates_it():
    """The default bundle brings its own, so there is nothing to look up --
    and no verdict, rather than one that is trivially true."""
    assert doctor.check_service_account(FACTS, {}, {"serviceaccounts": _sa()}) == []


def test_existing_service_account_found_passes():
    c = doctor.check_service_account(
        FACTS, EXISTING_SA, {"serviceaccounts": _sa("platform-sa")})[0]
    assert c.status == doctor.PASS
    assert "platform-sa" in c.detail


def test_existing_service_account_missing_fails():
    c = doctor.check_service_account(
        FACTS, EXISTING_SA, {"serviceaccounts": _sa("something-else")})[0]
    assert c.status == doctor.FAIL
    assert "platform-sa" in c.detail
    # The failure mode is the point: nothing errors at apply time.
    assert "no pod is ever created" in c.detail


def test_unreadable_namespace_warns_rather_than_failing():
    """Every namespace that exists has `default`, so an empty list means we
    could not look -- which is not evidence the account is missing."""
    c = doctor.check_service_account(FACTS, EXISTING_SA, {"serviceaccounts": []})[0]
    assert c.status == doctor.WARN
    c2 = doctor.check_service_account(FACTS, EXISTING_SA, {})[0]
    assert c2.status == doctor.WARN


# -- check_ingress_class ----------------------------------------------------

SV_NGINX = {"sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
            "sv_tls_secret": "wildcard-credential"}


def _ingressclass(name, controller="k8s.io/ingress-nginx"):
    return {"metadata": {"name": name}, "spec": {"controller": controller}}


def test_ingress_class_silent_without_service_virtualization():
    """A performance-only location creates no Ingress; don't judge the cluster
    on something it never uses."""
    assert doctor.check_ingress_class(FACTS, {}, {"ingressclasses": []}) == []


def test_ingress_class_present_passes():
    checks = doctor.check_ingress_class(
        FACTS, SV_NGINX, {"ingressclasses": [_ingressclass("nginx"),
                                             _ingressclass("traefik")]})
    assert _statuses(checks) == {doctor.PASS}


def test_ingress_class_missing_fails_and_names_what_does_exist():
    """The OpenShift default: one class, called something else. Nothing in the
    deploy fails -- the endpoint just 503s -- so the detail has to explain it."""
    cluster = {"ingressclasses": [
        _ingressclass("openshift-default", "openshift.io/ingress-to-route")]}
    c = doctor.check_ingress_class(FACTS, SV_NGINX, cluster)[0]
    assert c.status == doctor.FAIL
    assert "openshift-default" in c.detail       # what the cluster has instead
    assert "503" in c.detail
    assert "hardcodes" in c.detail               # not a generator option


def test_ingress_class_none_at_all_fails():
    c = doctor.check_ingress_class(FACTS, SV_NGINX, {"ingressclasses": []})[0]
    assert c.status == doctor.FAIL


@pytest.mark.parametrize("ingress", ["istio", "contour", "openshift"])
def test_ingress_class_crd_based_types_are_never_a_failure(ingress):
    """istio routes through a Gateway/VirtualService, contour through an
    HTTPProxy, openshift through a Route; none creates an Ingress, and none of
    those controllers registers an IngressClass -- so failing on 'none found'
    would fail every correct install of all three."""
    checks = doctor.check_ingress_class(
        FACTS, {**SV_NGINX, "sv_ingress": ingress}, {"ingressclasses": []})
    assert _statuses(checks) == {doctor.PASS}
    assert ingress in checks[0].detail


def test_ingress_class_unrecognised_value_warns_rather_than_fails():
    """A hand-written profile can carry a value generate() would have rejected;
    checking nginx's class name against it would be a misleading FAIL."""
    checks = doctor.check_ingress_class(
        FACTS, {**SV_NGINX, "sv_ingress": "traefik"}, {"ingressclasses": []})
    assert _statuses(checks) == {doctor.WARN}
    assert "traefik" in checks[0].detail


@pytest.mark.parametrize("cluster", [{}, {"ingressclasses": None}])
def test_ingress_class_unreadable_warns_rather_than_fails(cluster):
    """cluster_data from an older caller has no such key -- 'we could not look',
    not 'the class is missing'."""
    checks = doctor.check_ingress_class(FACTS, SV_NGINX, cluster)
    assert _statuses(checks) == {doctor.WARN}


def test_ingress_class_openshift_alias_says_the_route_still_will_not_be_made():
    """Aliasing the name to the ingress-to-route controller satisfies the class
    lookup but not the port mismatch behind it; a bare PASS would mislead."""
    cluster = {"ingressclasses": [
        _ingressclass("nginx", "openshift.io/ingress-to-route")]}
    c = doctor.check_ingress_class(FACTS, SV_NGINX, cluster)[0]
    assert c.status == doctor.PASS
    assert "IncompleteIngressToRouteRules" in c.detail


# -- egress -----------------------------------------------------------------

def test_egress_targets_include_the_private_registry():
    targets = doctor.egress_targets({"private_registry": "reg.corp:5001/blazemeter"})
    assert any("a.blazemeter.com" in t for t in targets)
    assert any("reg.corp:5001" in t for t in targets)
    # Engines upload to hosts crane never touches -- an egress rule shaped
    # around crane alone would pass a crane-only probe and still break runs.
    assert any("data.blazemeter.com" in t for t in targets)
    assert any("storage.blazemeter.com" in t for t in targets)
    assert len(doctor.egress_targets({})) == len(doctor.egress_targets(
        {"private_registry": "reg.corp:5001/bzm"})) - 1


@pytest.mark.parametrize("rc,status,marker", [
    (0, doctor.PASS, None),
    (6, doctor.FAIL, "proxy"),          # DNS: reminder that a proxy/CA must be honoured
    (28, doctor.FAIL, "proxy"),
    (None, doctor.WARN, None),          # could not probe -- never a false FAIL
])
def test_egress_verdicts(rc, status, marker):
    c = doctor.check_egress(FACTS, {}, {"probes": {doctor.API_PROBE_URL: rc}})[0]
    assert c.status == status
    assert doctor.API_PROBE_URL in c.detail
    if marker:
        assert marker in c.detail


def test_egress_without_probes_warns():
    assert doctor.check_egress(FACTS, {}, {"probes": None})[0].status == doctor.WARN


def _crane(monkeypatch, deployed, output=""):
    monkeypatch.setattr(doctor.livetest, "kget",
                        lambda cli, ns, kind, name=None: {"x": 1} if deployed else {})
    seen = []
    monkeypatch.setattr(doctor.livetest, "_crane_exec",
                        lambda cli, ns, sh: seen.append(sh) or output)
    return seen


def test_probe_egress_uses_one_exec_for_every_target(monkeypatch):
    """One shell for all the probes: each exec is a spawn plus the round trips
    to resolve deploy -> pod, and the 20s timeouts would stack serially."""
    targets = doctor.egress_targets({"ca_bundle": "PEM"})
    seen = _crane(monkeypatch, True,
                  "\n".join(f"{t} rc=0" for t in targets))
    probes = doctor.probe_egress("kubectl", "ns1", {"ca_bundle": "PEM"})
    assert probes == {t: 0 for t in targets}
    assert len(seen) == 1
    # The CA the profile configures has to be the one curl verifies against.
    assert '--cacert "$REQUESTS_CA_BUNDLE"' in seen[0]


def test_curl_script_retries_each_probe_once():
    """A pod's first DNS lookup can fail before CoreDNS answers for it -- seen
    live, two of three hosts returning rc=6 and all three passing on a rerun.
    A doctor that FAILs on that is reporting something it cannot reproduce."""
    script = doctor._curl_script(["https://x/"])
    assert script.count("curl") == 2
    assert "sleep 2" in script
    # The one-shot pod also has to wait for `kubectl run -i` to attach, or the
    # first lines are written to nobody.
    assert doctor._curl_script(["https://x/"], settle=2).startswith("sleep 2")


def test_probe_egress_reports_a_target_with_no_rc_line_as_unknown(monkeypatch):
    """The exec never ran, or one curl produced nothing: 'we could not look',
    not 'BlazeMeter is unreachable'."""
    _crane(monkeypatch, True, "")
    assert set(doctor.probe_egress("kubectl", "ns1", {}).values()) == {None}


def test_probe_egress_mixes_known_and_unknown_targets(monkeypatch):
    targets = doctor.egress_targets({})
    _crane(monkeypatch, True, f"{targets[0]} rc=7")
    probes = doctor.probe_egress("kubectl", "ns1", {})
    assert probes[targets[0]] == 7
    assert all(probes[t] is None for t in targets[1:])


def test_probe_egress_cannot_honour_a_ca_without_crane(monkeypatch):
    """A bare curl pod has no trust bundle; report 'unknown', not 'broken'."""
    _crane(monkeypatch, False)
    probes = doctor.probe_egress("kubectl", "ns1", {"ca_bundle": "PEM"})
    assert set(probes.values()) == {None}


def test_probe_egress_falls_back_to_one_shot_pod(monkeypatch):
    """One throwaway pod for all targets -- not one image pull and schedule
    per URL."""
    _crane(monkeypatch, False)
    pods = []
    monkeypatch.setattr(doctor, "_oneshot_curl",
                        lambda cli, ns, targets, opts: pods.append(targets)
                        or {t: 7 for t in targets})
    assert set(doctor.probe_egress("kubectl", "ns1", {}).values()) == {7}
    assert len(pods) == 1


# -- gather_cluster ---------------------------------------------------------

QUOTA_ITEM = {"kind": "ResourceQuota", "metadata": {"name": "q"},
              "status": {"hard": {}, "used": {}}}


SA_ITEM = {"kind": "ServiceAccount", "metadata": {"name": "crane"}}


def test_gather_cluster_splits_one_namespaced_get_by_kind(monkeypatch):
    """LimitRanges, ResourceQuotas and ServiceAccounts come back from a single
    `get` -- one API round trip instead of three -- so the shape has to be split
    by kind."""
    calls = []

    def fake_kget(cli, namespace, kind, name=None):
        calls.append((namespace, kind, name))
        if kind == "nodes":
            return {"items": [_big("a")]}
        if kind == "ingressclass":
            return {"items": [_ingressclass("nginx")]}
        if kind == "ns":
            return NS_BASELINE
        return {"items": [dict(LR_MATCHING, kind="LimitRange"), QUOTA_ITEM,
                          SA_ITEM]}

    monkeypatch.setattr(doctor.livetest, "kget", fake_kget)
    data = doctor.gather_cluster("kubectl", "ns1")
    assert [n["metadata"]["name"] for n in data["nodes"]] == ["a"]
    assert data["limitranges"] == [dict(LR_MATCHING, kind="LimitRange")]
    assert data["quotas"] == [QUOTA_ITEM]
    assert data["serviceaccounts"] == [SA_ITEM]
    assert data["namespace"] == NS_BASELINE
    assert ("ns1", "limitrange,resourcequota,serviceaccount", None) in calls
    # IngressClass is cluster-scoped, so it is read like nodes are.
    assert ("ingressclass" in [kind for _, kind, _ in calls])
    assert [c["metadata"]["name"] for c in data["ingressclasses"]] == ["nginx"]


def test_gather_cluster_survives_a_missing_namespace(monkeypatch):
    """`get ns` fails on a namespace that does not exist yet -- that is the
    normal pre-flight case, not a crash."""
    monkeypatch.setattr(doctor.livetest, "kget", lambda *a, **k: {})
    data = doctor.gather_cluster("kubectl", "ns1")
    # Every list is None rather than []: kget reports a failed command as {},
    # and "could not ask" has to stay distinguishable from "asked, none exist"
    # -- an [] here would fail the capacity check of anyone who is merely not
    # allowed to list nodes. The namespace stays {}, which is what
    # check_admission already reads as "not created yet".
    assert data == {"nodes": None, "ingressclasses": None, "limitranges": None,
                    "quotas": None, "serviceaccounts": None, "namespace": {}}


@pytest.mark.parametrize("served,expected,status", [
    ({"items": []}, [], doctor.FAIL),   # asked, cluster has none -> nothing claims it
    ({}, None, doctor.WARN),            # kget's failure shape -> we did not look
])
def test_gather_cluster_keeps_unreadable_ingressclasses_apart_from_empty(
        monkeypatch, served, expected, status):
    """The two answers reach check_ingress_class as [] and None and it grades
    them differently. Collapsing both to [] -- which `.get("items", [])` does --
    turns a cluster whose API server does not serve IngressClass into a hard
    FAIL with a non-zero exit, for something never actually checked."""
    monkeypatch.setattr(doctor.livetest, "kget",
                        lambda cli, ns, kind, name=None:
                        served if kind == "ingressclass" else {})
    data = doctor.gather_cluster("kubectl", "ns1")
    assert data["ingressclasses"] == expected
    assert _statuses(doctor.check_ingress_class(FACTS, SV_NGINX, data)) == {status}


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

    monkeypatch.setattr(doctor.livetest, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(doctor, "gather_cluster", fake_gather)
    monkeypatch.setattr(doctor, "probe_egress",
                        lambda cli, ns, opts: {doctor.API_PROBE_URL: 0})
    checks = doctor.run(FACTS, {"platform": "k8s"}, "blazemeter")
    assert called["gather"] == ("kubectl", "blazemeter")
    assert not doctor.has_failures(checks)
