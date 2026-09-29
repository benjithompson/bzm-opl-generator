"""Offline counterparts of the live rig: kube primitives, bundle checks, the
proxy/CA overlay, engine assertions, teardown ownership and the compose rig."""

import json
import os
import subprocess
import sys
import time

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import bundle_check, kube, livetest, sv_read  # noqa: E402
from bzm_opl_gen import generate as gen  # noqa: E402
from tests.test_generate import FACTS  # noqa: E402
from tests.tls_fixtures import SV_CERT, SV_HOST, SV_KEY  # noqa: E402
from bzm_opl_gen import (bundle_names, bundle_options, ca_trust,
                         image_registry, markers, required_fields, service_virt)  # noqa: E402

CA_PEM = "-----BEGIN CERTIFICATE-----\nmitm\n-----END CERTIFICATE-----"


def test_proxy_overlay_shape():
    o = livetest.proxy_overlay("192.168.49.1", 8080, CA_PEM, "bzm", "s3cr3t")
    assert o["proxy"]["http"] == "http://192.168.49.1:8080"
    assert o["proxy"]["https"] == "http://192.168.49.1:8080"
    assert (o["proxy"]["username"], o["proxy"]["password"]) == ("bzm", "s3cr3t")
    # blazemeter.com must NOT be excluded -- that traffic is the test.
    assert "blazemeter" not in o["proxy"]["no_proxy"]
    assert "kubernetes.default" in o["proxy"]["no_proxy"]
    assert o["ca_bundle"] == CA_PEM
    # Other CA modes cleared so _ca_cfg() never sees two.
    assert o["ca_existing_configmap"] is None and o["ca_openshift_inject"] is False


def test_proxy_overlay_open_proxy():
    o = livetest.proxy_overlay("host", 8080, CA_PEM)
    assert "username" not in o["proxy"] and "password" not in o["proxy"]


def test_overlay_renders_proxy_and_ca():
    """The overlay renders credentials in the Secret and mounts the mitm CA."""
    opts = {"namespace": "ns1", "auth_token": "tok",
            **livetest.proxy_overlay("192.168.49.1", 8080, CA_PEM, "bzm", "s3cr3t")}
    files = gen.generate(FACTS, opts)
    sec = yaml.safe_load(files["bzm_secret.yaml"])["stringData"]
    assert sec["HTTPS_PROXY"] == "http://bzm:s3cr3t@192.168.49.1:8080"
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert "HTTPS_PROXY" not in cm  # credentials stay out of the ConfigMap
    assert cm["REQUESTS_CA_BUNDLE"] == "/var/cm/ca-bundle.crt"
    assert cm["KUBERNETES_CA_BUNDLE_MOUNT"].startswith(
        "REQUESTS_CA_BUNDLE=blazemeter-cacerts=ca-bundle.crt")
    assert "mitm" in yaml.safe_load(files["bzm_cacerts.yaml"])["data"]["ca-bundle.crt"]


def test_overlay_replaces_existing_ca_mode():
    """The inline overlay replaces an existing-ConfigMap mode in the profile."""
    opts = {"namespace": "ns1", "ca_existing_configmap": "corp-trust",
            **livetest.proxy_overlay("h", 8080, CA_PEM)}
    files = gen.generate(FACTS, opts)
    assert "bzm_cacerts.yaml" in files  # inline mode won


def test_large_ca_bundle_warns_about_server_side_apply():
    """A bundle over the last-applied cap is told to apply server-side."""
    big = "-----BEGIN CERTIFICATE-----\n" + ("A" * 300_000) + "\n-----END CERTIFICATE-----"
    files = gen.generate(FACTS, {"namespace": "ns1", "ca_bundle": big})
    assert "--server-side" in files["README.md"]
    assert "--server-side" not in gen.generate(
        FACTS, {"namespace": "ns1", "ca_bundle": CA_PEM})["README.md"]


def test_apply_switches_to_server_side_for_big_manifests(tmp_path, monkeypatch):
    small = tmp_path / "small.yaml"
    small.write_text("kind: ConfigMap\n")
    big = tmp_path / "big.yaml"
    big.write_text("x" * (kube.LARGE_MANIFEST_BYTES + 1))
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    kube.apply("kubectl", "ns1", str(small))
    kube.apply("kubectl", "ns1", str(big))
    assert "--server-side" not in cmds[0]
    assert cmds[1][-2:] == ["--server-side", "--force-conflicts"]


def test_the_rig_mirrors_where_the_bundle_it_deploys_will_look(monkeypatch):
    """The mirror pushes to the paths the bundle's IMAGE_OVERRIDES and crane
    image name; only the registry host differs."""
    port = 5001
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    livetest.mirror_images(FACTS, port)
    pushed = {c[2] for c in cmds if c[:2] == ["docker", "push"]}

    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "private_registry": f"host.minikube.internal:{port}"})
    overrides = json.loads(yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    wanted = {r.split("/", 1)[1] for r in overrides.values()}
    wanted.add(image_registry.crane_image(FACTS, {
        "private_registry": f"host.minikube.internal:{port}"}).split("/", 1)[1])
    assert {p.split("/", 1)[1] for p in pushed} == wanted


def _live(monkeypatch, cm_data, images=(), ca_certs="2"):
    """Stand in for a deployed cluster: ConfigMap data, running images, CA
    count in the pod."""
    monkeypatch.setattr(kube, "kget", lambda *a, **k: {"data": cm_data})
    monkeypatch.setattr(kube, "pod_images", lambda *a: list(images))
    monkeypatch.setattr(kube, "crane_exec", lambda *a: ca_certs)


GOOD_CM = {"AUTO_KUBERNETES_UPDATE": "false",
           "IMAGE_OVERRIDES": json.dumps({"taurus-cloud:latest": "reg:5001/v4:1",
                                          "apm-image:latest": "reg:5001/apm:1"}),
           "REQUESTS_CA_BUNDLE": "/var/cm/ca-bundle.crt"}
REG_OPTS = {"private_registry": "reg:5001", "use_secret": True}


def test_live_config_clean(monkeypatch):
    _live(monkeypatch, GOOD_CM, images=["reg:5001/crane:1"])
    assert livetest.assert_live_config("kubectl", "ns", FACTS, REG_OPTS) == []


def test_live_config_catches_auth_token_in_configmap(monkeypatch):
    _live(monkeypatch, {**GOOD_CM, "AUTH_TOKEN": "tok"}, images=["reg:5001/crane:1"])
    fails = livetest.assert_live_config("kubectl", "ns", FACTS, REG_OPTS)
    assert any("AUTH_TOKEN" in f for f in fails)


def test_live_config_catches_proxy_creds_in_configmap(monkeypatch):
    _live(monkeypatch, {**GOOD_CM, "HTTPS_PROXY": "http://u:p@proxy:8080"},
          images=["reg:5001/crane:1"])
    fails = livetest.assert_live_config("kubectl", "ns", FACTS, REG_OPTS)
    assert any("credentials readable" in f for f in fails)


def test_live_config_catches_missing_image_override(monkeypatch):
    thin = {**GOOD_CM, "IMAGE_OVERRIDES": json.dumps({"taurus-cloud:latest": "reg:5001/v4:1"})}
    _live(monkeypatch, thin, images=["reg:5001/crane:1"])
    fails = livetest.assert_live_config("kubectl", "ns", FACTS, REG_OPTS)
    assert any("IMAGE_OVERRIDES missing" in f and "apm-image" in f for f in fails)


def test_live_config_catches_public_image_and_autoupdate(monkeypatch):
    _live(monkeypatch, {**GOOD_CM, "AUTO_KUBERNETES_UPDATE": "true"},
          images=["gcr.io/verdant-bulwark-278/blazemeter/crane:latest"])
    fails = livetest.assert_live_config("kubectl", "ns", FACTS, REG_OPTS)
    assert any("AUTO_KUBERNETES_UPDATE" in f for f in fails)
    assert any("not from the private registry" in f for f in fails)


def test_live_config_judges_autoupdate_against_the_option(monkeypatch):
    """AUTO_KUBERNETES_UPDATE is judged against the resolved option, not
    against the presence of a registry."""
    on = {**GOOD_CM, "AUTO_KUBERNETES_UPDATE": "true"}
    _live(monkeypatch, on, images=["reg:5001/crane:1"])
    assert livetest.assert_live_config(
        "kubectl", "ns", FACTS, {**REG_OPTS, "auto_update": True}) == []

    _live(monkeypatch, on)
    fails = livetest.assert_live_config("kubectl", "ns", FACTS,
                                        {"use_secret": True, "auto_update": False})
    assert any("AUTO_KUBERNETES_UPDATE" in f for f in fails)


def test_live_config_catches_ca_missing_in_pod(monkeypatch):
    _live(monkeypatch, GOOD_CM, images=["reg:5001/crane:1"], ca_certs="0")
    fails = livetest.assert_live_config("kubectl", "ns", FACTS, REG_OPTS)
    assert any("never reached the process" in f for f in fails)


def test_proxy_log_failures(monkeypatch):
    lines = {"407": ["<< HTTP/1.1 407 Proxy Authentication Required"],
             ":6443": ["server connect 10.96.0.1:6443"],
             "kubernetes.default": []}
    monkeypatch.setattr(livetest, "proxy_flows", lambda s="blazemeter.com": lines.get(s, []))
    fails = livetest.proxy_log_failures()
    assert any("407" in f for f in fails)
    assert any("NO_PROXY is wrong" in f for f in fails)
    monkeypatch.setattr(livetest, "proxy_flows", lambda s="blazemeter.com": [])
    assert livetest.proxy_log_failures() == []


def test_proxy_log_failures_ignores_the_negative_control(monkeypatch):
    """Lines logged before the marks do not fail the real run."""
    lines = {"407": ["407 once"], ":6443": [], "kubernetes.default": []}
    monkeypatch.setattr(livetest, "proxy_flows", lambda s="blazemeter.com": lines.get(s, []))
    marks = livetest.proxy_log_marks()
    assert livetest.proxy_log_failures(marks) == []
    lines["407"].append("407 again")           # new one, from the real run
    assert any("407" in f for f in livetest.proxy_log_failures(marks))


def test_blackhole_skips_the_private_registry(monkeypatch):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    hosts = livetest.blackhole_public_registries(FACTS, "minikube", "reg.corp:5001/bzm")
    assert hosts == ["gcr.io"]                      # the fixture's images live there
    assert "reg.corp:5001" not in " ".join(str(c) for c in cmds)


def test_egress_policy_allows_only_dns_apiserver_and_proxy():
    pol = yaml.safe_load(livetest.egress_policy(
        "ns1", "192.168.67.3", [("10.96.0.1", 443), ("192.168.49.2", 8443)]))
    assert pol["spec"]["policyTypes"] == ["Egress"]
    assert pol["spec"]["podSelector"] == {}          # whole namespace
    rules = pol["spec"]["egress"]
    assert len(rules) == 4
    dns, api_svc, api_ep, proxy = rules
    assert {p["port"] for p in dns["ports"]} == {53}
    # Both the ClusterIP and the endpoint: policy is matched after DNAT.
    assert api_svc["to"][0]["ipBlock"]["cidr"] == "10.96.0.1/32"
    assert api_ep["to"][0]["ipBlock"]["cidr"] == "192.168.49.2/32"
    assert api_ep["ports"][0]["port"] == 8443
    assert proxy["to"][0]["ipBlock"]["cidr"] == "192.168.67.3/32"
    assert proxy["ports"][0]["port"] == livetest.PROXY_PORT
    # No blanket allow anywhere: that would defeat the point.
    assert not any(r.get("to") == [] or "to" not in r for r in rules)


@pytest.mark.parametrize("direct,proxied,ok,marker", [
    (28, 0, True, None),                       # timed out direct, fine via proxy
    (7, 0, True, None),                        # refused direct
    (0, 0, False, "not contained"),            # direct reached BlazeMeter
    (60, 0, False, "not contained"),           # TLS error = it still got there
    (6, 0, False, "blocks DNS"),
    (28, 28, False, "denies more than it should"),
])
def test_egress_probe_verdicts(monkeypatch, direct, proxied, ok, marker):
    rcs = iter([direct, proxied])
    monkeypatch.setattr(kube, "crane_exec", lambda *a: f"rc={next(rcs)}")
    fails = livetest.assert_egress_contained("kubectl", "ns1")
    assert (fails == []) is ok
    if marker:
        assert any(marker in f for f in fails)


def test_policy_enforced_detects_calico(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pod/calico-node-abc\n"})())
    assert livetest.policy_enforced()
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": ""})())
    assert not livetest.policy_enforced()


def _engine_pod(image="reg:5001/v4:1", ca=True, proxy=True,
                resources=None, annotations=None):
    env = []
    mounts = []
    if ca:
        env.append({"name": "REQUESTS_CA_BUNDLE", "value": "/var/cm/ca-bundle.crt"})
        # What crane actually gives an engine: the bundle file via subPath,
        # not the /var/cm directory it mounts for itself.
        mounts.append({"name": "cacerts", "mountPath": "/var/cm/ca-bundle.crt",
                       "subPath": "ca-bundle.crt"})
    if proxy:
        env.append({"name": "HTTPS_PROXY", "value": "http://bzm:s3cr3t@1.2.3.4:8080"})
    return {"metadata": {"name": "engine-abc", "annotations": annotations or {}},
            "status": {"phase": "Running"},
            "spec": {"containers": [{"name": "ctr", "image": image, "env": env,
                                     "volumeMounts": mounts,
                                     "resources": resources or {}}]}}


ENGINE_OPTS = {"private_registry": "reg:5001", "ca_bundle": CA_PEM,
               "proxy": {"https": "http://1.2.3.4:8080"}}


def test_engine_config_clean():
    assert livetest.assert_engine_config(_engine_pod(), ENGINE_OPTS) == []


def test_engine_config_catches_public_engine_image():
    pod = _engine_pod(image="gcr.io/verdant-bulwark-278/blazemeter/v4:latest")
    fails = livetest.assert_engine_config(pod, ENGINE_OPTS)
    assert any("does not cover the engine" in f for f in fails)


def test_engine_config_catches_missing_ca_propagation():
    fails = livetest.assert_engine_config(_engine_pod(ca=False), ENGINE_OPTS)
    assert any("KUBERNETES_CA_BUNDLE_MOUNT did not propagate" in f for f in fails)
    assert any("REQUESTS_CA_BUNDLE" in f for f in fails)


@pytest.mark.parametrize("mode", sorted(ca_trust.CA_MODES))
def test_engine_config_checks_the_ca_whichever_mode_configured_it(mode):
    """The engine CA assertions run for every CA mode, not only the inline one
    the proxy rig writes."""
    value = True if bundle_options.DEFAULT_OPTIONS[mode] is False else "x"
    opts = {**ENGINE_OPTS, "ca_bundle": None, mode: value}
    fails = livetest.assert_engine_config(_engine_pod(ca=False), opts)
    assert any("KUBERNETES_CA_BUNDLE_MOUNT did not propagate" in f for f in fails)
    assert any("REQUESTS_CA_BUNDLE" in f for f in fails)


def test_engine_config_catches_missing_proxy_env():
    fails = livetest.assert_engine_config(_engine_pod(proxy=False), ENGINE_OPTS)
    assert any("bypassing the customer's proxy" in f for f in fails)


# -- engine size and pool, the two halves of "did it get what we configured" --

SPLIT_OPTS = {"node_selector": {"pool": "crane"},
              "engine_node_selector": {"pool": "bzm-engines"},
              "engine_cpu_limit": "2", "engine_mem_limit": "8Gi"}


def _placed_pod(node="e1", limits=None):
    pod = _engine_pod(resources={"limits": limits or {"cpu": "2", "memory": "8Gi"},
                                 "requests": {"cpu": "250m", "memory": "256Mi"}})
    pod["spec"]["nodeName"] = node
    return pod


def _node(name="e1", labels=None):
    return {"metadata": {"name": name, "labels": labels or {"pool": "bzm-engines"}}}


def test_engine_size_matches_what_the_bundle_configured():
    assert livetest.assert_engine_size(_placed_pod(), SPLIT_OPTS) == []


def test_engine_size_catches_limits_the_configmap_never_delivered():
    """An engine at other limits than the bundle's means the ConfigMap never
    reached it."""
    pod = _placed_pod(limits={"cpu": "1", "memory": "4Gi"})
    fails = livetest.assert_engine_size(pod, SPLIT_OPTS)
    assert any("CPU limit of 1" in f for f in fails)
    assert any("memory limit of 4Gi" in f for f in fails)


def test_engine_landed_on_the_pool_it_was_aimed_at():
    assert livetest.assert_engine_pool(_placed_pod(), _node(), SPLIT_OPTS) == []


def test_engine_on_the_wrong_pool_is_a_failure():
    """An engine on a node lacking the engine pool's labels is a failure."""
    wrong = _node("c1", labels={"pool": "crane"})
    fails = livetest.assert_engine_pool(_placed_pod(node="c1"), wrong, SPLIT_OPTS)
    assert any("does not carry the engine pool's labels" in f for f in fails)


def test_unread_node_is_not_a_wrong_pool():
    """An unreadable node is reported, not failed."""
    assert livetest.assert_engine_pool(_placed_pod(), None, SPLIT_OPTS) == []


def test_engine_pool_is_not_asserted_on_a_one_pool_bundle():
    """Nothing was aimed anywhere, so there is nothing to be wrong about."""
    assert livetest.assert_engine_pool(
        _placed_pod(), _node("n1", labels={}), {"node_selector": {"pool": "x"}}) == []


# -- the engine finished, versus the run being reported as finished -----------
#
# The event shapes are real masters from starved engines: each reached ENDED
# with zero failures, so only the Taurus exit code tells them apart.

class _EventClient:
    def __init__(self, messages, summary=None):
        self._m, self._s = messages, summary or {}

    def master_status(self, master_id):
        return {"status": "ENDED", "events": [{"message": m} for m in self._m]}

    def master_summary(self, master_id):
        return self._s


CLEAN = ["Status changed to ENDED (140)", "Taurus completed (Exit: 0)"]
DIED = ["Status changed to ENDED (140)", "Taurus completed (Exit: 1)"]


def test_clean_exit_passes():
    assert livetest.assert_engine_exited_cleanly(_EventClient(CLEAN), 1) == []


def test_engine_that_died_partway_is_caught_despite_ending():
    """Exit 1 fails the run although it reached ENDED."""
    fails = livetest.assert_engine_exited_cleanly(_EventClient(DIED), 1)
    assert any("exited 1" in f for f in fails)
    assert any("reported as ENDED" in f for f in fails)


def test_a_starved_run_looks_healthier_than_a_good_one():
    """Only the exit code tells a starved run from a healthy one; both pass the
    summary check."""
    starved = _EventClient(DIED, {"summary": [{"hits": 31130, "avg": 322, "failed": 0}]})
    healthy = _EventClient(CLEAN, {"summary": [{"hits": 61348, "avg": 322, "failed": 21}]})
    assert livetest.assert_engine_did_work(starved, 1) == []      # looks fine
    assert livetest.assert_engine_did_work(healthy, 1) == []      # also fine
    assert livetest.assert_engine_exited_cleanly(starved, 1) != []   # only this differs
    assert livetest.assert_engine_exited_cleanly(healthy, 1) == []


def test_a_missing_exit_status_is_unverified_not_passed():
    """No Taurus exit status is unverified, not passed."""
    fails = livetest.assert_engine_exited_cleanly(_EventClient(["Status changed to ENDED (140)"]), 1)
    assert any("unverified" in f for f in fails)


def _heap_pod(xmx, limit="8Gi", where="env"):
    """An engine started with -Xmx in env, args or command (a location setting,
    so any of the three)."""
    pod = _engine_pod(resources={"limits": {"cpu": "2", "memory": limit}})
    c = pod["spec"]["containers"][0]
    if xmx is not None:
        if where == "env":
            c["env"].append({"name": "JVM_ARGS", "value": f"-Xms1g -Xmx{xmx}"})
        elif where == "args":
            c["args"] = ["-jar", "taurus.jar", f"-Xmx{xmx}"]
        else:
            c["command"] = ["java", f"-Xmx{xmx}", "-cp", "/x"]
    return pod


@pytest.mark.parametrize("where", ["env", "args", "command"])
def test_engine_heap_is_found_wherever_the_location_put_it(where):
    assert livetest.engine_heap_bytes(_heap_pod("6g", where=where)) == 6 * 1024 ** 3


@pytest.mark.parametrize("xmx,expected", [
    ("4096m", 4096 * 1024 ** 2), ("6g", 6 * 1024 ** 3),
    ("1048576k", 1024 ** 3), ("2147483648", 2 * 1024 ** 3),
])
def test_engine_heap_units(xmx, expected):
    assert livetest.engine_heap_bytes(_heap_pod(xmx)) == expected


def test_unread_heap_is_not_a_matching_heap():
    """An unreadable heap is None and the note says unread."""
    assert livetest.engine_heap_bytes(_heap_pod(None)) is None
    assert "unread" in livetest.engine_heap_note(_heap_pod(None))


def test_engine_heap_note_flags_a_heap_that_can_fill_the_limit():
    note = livetest.engine_heap_note(_heap_pod("8g", limit="8Gi"))
    assert "OOMKill" in note


def test_engine_heap_note_flags_a_limit_the_jvm_cannot_reach():
    """The default pairing (4096MB in 8Gi) is flagged as half unused."""
    note = livetest.engine_heap_note(_heap_pod("4096m", limit="8Gi"))
    assert "reserved and unused" in note and "50%" in note


def test_engine_heap_note_is_quiet_when_the_pairing_is_sane():
    note = livetest.engine_heap_note(_heap_pod("6g", limit="8Gi"))
    assert "OOMKill" not in note and "unused" not in note
    assert "6Gi" in note and "8Gi" in note


def _sized_pod(requests, limits, annotations=None):
    """An engine pod with the given requests and limits."""
    return _engine_pod(resources={"requests": requests, "limits": limits},
                       annotations=annotations)


def test_engine_request_gap_is_what_a_real_run_returns():
    """Requests below limits, with no limit-ranger annotation, are reported as
    a gap no LimitRange can close."""
    pod = _sized_pod({"cpu": "250m", "memory": "256Mi"},
                     {"cpu": "1", "memory": "4Gi"})
    gap = livetest.engine_request_gap(pod)
    assert "250m" in gap and "cpu" in gap and "memory" in gap
    assert "cannot change them" in gap


def test_engine_request_gap_silent_when_requests_match_limits():
    assert livetest.engine_request_gap(
        _sized_pod({"cpu": "1", "memory": "4Gi"}, {"cpu": "1", "memory": "4Gi"})) is None


def test_engine_request_gap_notes_when_a_limitrange_did_act():
    """With a limit-ranger annotation, the gap does not claim a LimitRange
    could not act."""
    pod = _sized_pod({"cpu": "250m", "memory": "256Mi"}, {"cpu": "1", "memory": "4Gi"},
                     annotations={livetest.LIMIT_RANGER_ANNOTATION: "set: cpu request"})
    assert "cannot change them" not in livetest.engine_request_gap(pod)


def test_engine_pods_excludes_crane(monkeypatch):
    items = {"items": [{"metadata": {"name": "crane-7d9-abc"}},
                       {"metadata": {"name": "taurus-cloud-xyz"}}]}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, json.dumps(items), ""))
    pods = livetest.engine_pods("kubectl", "ns1")
    assert [p["metadata"]["name"] for p in pods] == ["taurus-cloud-xyz"]


def test_engine_proxy_evidence(monkeypatch):
    """Engine traffic is recognised by the upload hosts only engines use."""
    log = {"data.blazemeter.com": [], "storage.blazemeter.com": []}
    monkeypatch.setattr(livetest, "proxy_flows", lambda s="blazemeter.com": log.get(s, []))
    before = livetest.engine_upload_marks()
    fails = livetest.engine_proxy_evidence(before)
    assert any("never went through the proxy" in f for f in fails)
    log["data.blazemeter.com"] = ["POST https://data.blazemeter.com/api/v4/taurus/r-v4-x"]
    assert livetest.engine_proxy_evidence(before) == []


class _FakeClient:
    def __init__(self, summary):
        self._s = summary

    def master_summary(self, master_id):
        return {"summary": [self._s]}


@pytest.mark.parametrize("summary,ok,marker", [
    ({"hits": 41, "avg": 431.0, "failed": 0}, True, None),
    ({"hits": 0, "avg": None, "failed": 0}, False, "never issued a request"),
    ({"hits": 1, "avg": 120145.0, "failed": 1}, False, "could not reach the target"),
])
def test_engine_did_work_verdicts(summary, ok, marker):
    fails = livetest.assert_engine_did_work(_FakeClient(summary), 1)
    assert (fails == []) is ok
    if marker:
        assert any(marker in f for f in fails)


def test_point_test_at_location_skips_script_driven_tests(monkeypatch):
    """A test whose locations live in its script is not repointed."""
    from bzm_opl_gen import api
    patched = []
    monkeypatch.setattr(api.BzmClient, "test", lambda self, tid: {"executions": None})
    monkeypatch.setattr(api.BzmClient, "update_test",
                        lambda self, tid, body: patched.append(body))
    c = api.BzmClient.__new__(api.BzmClient)
    assert c.point_test_at_location(1, "abc") is None
    assert patched == []


def test_point_test_at_location_returns_original(monkeypatch):
    """The original test body comes back for a verbatim restore."""
    from bzm_opl_gen import api
    original = {"executions": [{"concurrency": 5, "locations": {"us-east4-a": 5},
                                "executor": "jmeter"}],
                "overrideExecutions": [{"locations": {"us-east4-a": 5}}]}
    sent = {}
    c = api.BzmClient.__new__(api.BzmClient)
    monkeypatch.setattr(api.BzmClient, "test", lambda self, tid: dict(original))
    monkeypatch.setattr(api.BzmClient, "update_test",
                        lambda self, tid, body: sent.update(body))
    before = c.point_test_at_location(10000001, "abc123", concurrency=1)
    assert before == original                       # caller can restore verbatim
    ex = sent["executions"][0]
    assert ex["locations"] == {"harbor-abc123": 1} and ex["concurrency"] == 1
    assert ex["executor"] == "jmeter"               # untouched fields survive
    assert sent["overrideExecutions"][0]["locations"] == {"harbor-abc123": 1}


# -- the directory is the bundle under test (bundle_check) --------------------

def _bundle(tmp_path, facts=FACTS, **opts):
    gen.write(gen.generate(facts, {"namespace": "ns1", "auth_token": "tok",
                                   **opts}), str(tmp_path))
    return str(tmp_path)


def test_bundle_check_passes_this_generators_own_output(tmp_path):
    d = _bundle(tmp_path)
    check = bundle_check.bundle_check(d, "aaa111", "bbb222",
                                  gen.load_profile(d))
    assert check == bundle_check.BundleCheck([], [])


def test_bundle_check_names_the_ship_on_disk_and_the_ship_asked_for(tmp_path):
    """A ConfigMap ship id other than the one under test is refused, naming
    both."""
    d = _bundle(tmp_path)
    refusals = bundle_check.bundle_check(d, "aaa111", "ccc333").refusals
    assert refusals and all("bbb222" in r and "ccc333" in r for r in refusals)
    assert any(bundle_check.CONFIGMAP_FILE in r for r in refusals)


def test_bundle_check_names_the_harbor_on_disk_and_the_harbor_asked_for(tmp_path):
    """A ConfigMap harbor id other than the one under test is refused, naming
    both."""
    d = _bundle(tmp_path)
    r = " ".join(bundle_check.bundle_check(d, "zzz999", "bbb222").refusals)
    assert "aaa111" in r and "zzz999" in r


def test_bundle_check_catches_a_stale_ship_in_the_profile(tmp_path):
    """A profile.json ship_id other than the one under test is refused."""
    d = _bundle(tmp_path)
    prof = {**gen.load_profile(d), "ship_id": "ddd444"}
    r = " ".join(bundle_check.bundle_check(d, "aaa111", "bbb222", prof).refusals)
    assert "ddd444" in r and "bbb222" in r and bundle_names.PROFILE_FILE in r


def test_bundle_check_refuses_a_bundle_with_a_field_left_blank(tmp_path):
    """A profile with a field left blank is refused, naming the field and its
    marker."""
    d = _bundle(tmp_path, service_account_name="")
    prof = gen.load_profile(d)
    assert prof["service_account_name"] == markers.marker("service_account_name")
    r = " ".join(bundle_check.bundle_check(d, "aaa111", "bbb222", prof).refusals)
    # Both halves: the field somebody has to fill in, and the string they will
    # find in the bundle when they go looking.
    assert "service_account_name" in r
    assert markers.marker("service_account_name") in r


def test_bundle_check_refuses_a_yaml_this_generator_does_not_emit(tmp_path):
    """A leftover *.yaml this generator does not emit is refused."""
    d = _bundle(tmp_path)
    open(os.path.join(d, "bzm_limitrange.yaml"), "w").write("kind: LimitRange\n")
    r = " ".join(bundle_check.bundle_check(d, "aaa111", "bbb222").refusals)
    assert "bzm_limitrange.yaml" in r


def test_every_yaml_this_generator_emits_is_one_the_rig_expects():
    """Every *.yaml any helm_parity case emits is in emitted_yaml_files(), so
    no good bundle is refused."""
    import helm_parity                                  # no helm binary needed
    for name, extra in helm_parity.CASES.items():
        files = gen.generate(FACTS, {**helm_parity.COMMON, **extra})
        unknown = [f for f in files if f.endswith(".yaml")
                   and f not in bundle_check.emitted_yaml_files()]
        assert not unknown, f"case {name} emits {unknown}"


def test_bundle_check_judges_exactly_what_deploy_would_apply(tmp_path):
    """Only the *.yaml deploy() applies are judged; dotfiles and other files
    are ignored."""
    d = _bundle(tmp_path)
    open(os.path.join(d, ".egress-policy.yaml"), "w").write("kind: NetworkPolicy\n")
    open(os.path.join(d, "notes.txt"), "w").write("scratch\n")
    assert bundle_check.bundle_check(d, "aaa111", "bbb222").refusals == []


def test_bundle_check_does_not_invent_an_identity_it_could_not_read(tmp_path):
    """A missing ConfigMap is a note, not a refusal, and manifest_identity
    answers None."""
    check = bundle_check.bundle_check(str(tmp_path), "aaa111", "bbb222")
    assert check.refusals == []
    assert any("aaa111" in n and "bbb222" in n for n in check.notes)
    assert bundle_check.manifest_identity(str(tmp_path)) is None


def test_bundle_check_tells_an_unread_configmap_from_one_that_names_nothing(
        tmp_path):
    """A ConfigMap that was read and names no id gets its own note, not "could
    not be read"."""
    d = _bundle(tmp_path)
    open(os.path.join(d, bundle_check.CONFIGMAP_FILE), "w").write(
        "kind: ConfigMap\ndata: {}\n")
    assert bundle_check.manifest_identity(d) == {}          # read, and names none
    notes = bundle_check.bundle_check(d, "aaa111", "bbb222").notes
    assert any("carries no HARBOR_ID/SHIP_ID" in n for n in notes)
    assert not any("could not be read" in n for n in notes)


def test_run_refuses_before_it_builds_anything(monkeypatch, tmp_path):
    """run() raises BundleMismatch before any cluster, deploy or teardown step."""
    d = _bundle(tmp_path)
    for name in ("ensure_cluster", "deploy", "teardown", "ensure_registry"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a bundle built for another agent"))
    with pytest.raises(bundle_check.BundleMismatch) as caught:
        livetest.run(None, d, "ns1", "aaa111", "ccc333", cluster="minikube")
    assert "bbb222" in str(caught.value) and "ccc333" in str(caught.value)


# -- the compose rig ----------------------------------------------------------
#
# The daemon is a recorded command list and BlazeMeter is a dict.

def _docker_bundle(tmp_path, facts=FACTS, **opts):
    gen.write(gen.generate(facts, {"output_format": "docker",
                                   "ship_id": "bbb222", "auth_token": "tok",
                                   **opts}), str(tmp_path))
    return str(tmp_path)


class _FakeBzm:
    """BlazeMeter's location read: one ship, heartbeating now, in `state`."""

    def __init__(self, state="idle", ship="bbb222"):
        self.state, self.ship, self.calls = state, ship, 0

    def private_location(self, harbor_id):
        self.calls += 1
        return {"ships": [{"id": self.ship, "state": self.state,
                           "lastHeartBeat": time.time()}]}


def _fake_daemon(monkeypatch, *, present=(), compose=True):
    """Stand in for the docker daemon; returns the recorded commands. `present`
    is what `docker ps -aq` reports; `compose=False` is a host without
    Compose v2."""
    cmds = []

    def run(cmd, *a, **kw):
        cmds.append(cmd)
        if cmd[:3] == livetest.COMPOSE_TOOL + ["version"]:
            if not compose:
                raise FileNotFoundError("docker")
            return subprocess.CompletedProcess(cmd, 0, "Docker Compose v2.29.0", "")
        if cmd[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(present), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return cmds


def test_bundle_platform_reads_the_profile(tmp_path):
    """The profile's output_format decides the platform."""
    assert bundle_check.bundle_platform(
        str(tmp_path), {"output_format": "docker"}) == bundle_check.PLATFORM_COMPOSE
    assert bundle_check.bundle_platform(
        str(tmp_path), {"output_format": "manifests"}) == bundle_check.PLATFORM_MANIFESTS


def test_bundle_platform_reads_the_directory_when_there_is_no_profile(tmp_path):
    """Without a profile, a compose file makes a directory a docker bundle."""
    d = _docker_bundle(tmp_path)
    os.remove(os.path.join(d, bundle_names.PROFILE_FILE))
    assert bundle_check.bundle_platform(d) == bundle_check.PLATFORM_COMPOSE
    assert bundle_check.bundle_platform(str(tmp_path / "empty")) == \
        bundle_check.PLATFORM_MANIFESTS


def test_bundle_platform_does_not_let_a_stray_compose_file_win(tmp_path):
    """A manifests profile wins over a stray compose file, which is refused as
    an unknown *.yaml."""
    d = _bundle(tmp_path)
    open(os.path.join(d, bundle_names.DOCKER_COMPOSE_FILE), "w").write("services: {}\n")
    prof = gen.load_profile(d)
    assert bundle_check.bundle_platform(d, prof) == bundle_check.PLATFORM_MANIFESTS
    refusals = bundle_check.bundle_check(d, "aaa111", "bbb222", prof).refusals
    assert any(bundle_names.DOCKER_COMPOSE_FILE in r for r in refusals)


def test_compose_bundle_check_passes_this_generators_own_output(tmp_path):
    d = _docker_bundle(tmp_path)
    check = bundle_check.bundle_check(d, "aaa111", "bbb222", gen.load_profile(d))
    assert check == bundle_check.BundleCheck([], [])


def test_compose_bundle_check_refuses_another_agents_container(tmp_path):
    """A compose container_name for another ship is refused, naming both."""
    d = _docker_bundle(tmp_path)
    refusals = bundle_check.bundle_check(d, "aaa111", "ccc333").refusals
    assert any(bundle_names.docker_container_name("bbb222") in r
               and bundle_names.docker_container_name("ccc333") in r for r in refusals)


def test_compose_bundle_check_refuses_another_locations_harbor(tmp_path):
    d = _docker_bundle(tmp_path)
    refusals = bundle_check.bundle_check(d, "zzz999", "bbb222").refusals
    assert any("HARBOR_ID" in r and "aaa111" in r and "zzz999" in r
               for r in refusals)


def test_compose_bundle_check_refuses_a_directory_with_no_compose_file(tmp_path):
    """A docker profile with no compose file is refused."""
    d = _docker_bundle(tmp_path)
    os.remove(os.path.join(d, bundle_names.DOCKER_COMPOSE_FILE))
    refusals = bundle_check.bundle_check(d, "aaa111", "bbb222",
                                     gen.load_profile(d)).refusals
    assert any(bundle_names.DOCKER_COMPOSE_FILE in r for r in refusals)


def test_compose_bundle_check_refuses_a_bundle_nobody_finished(tmp_path):
    """A blank credential, read off the compose files, is refused."""
    d = _docker_bundle(tmp_path, auth_token=None)
    refusals = bundle_check.bundle_check(d, "aaa111", "bbb222",
                                     gen.load_profile(d)).refusals
    assert any("AUTH_TOKEN" in r for r in refusals)


def test_compose_bundle_check_notes_an_identity_it_could_not_read(tmp_path):
    """A compose file that names no container or ids gets per-field notes, not
    a refusal."""
    d = str(tmp_path)
    open(os.path.join(d, bundle_names.DOCKER_COMPOSE_FILE), "w").write(
        "services:\n  crane:\n    image: x\n")
    assert bundle_check.compose_identity(d) == {}           # read, and names none
    check = bundle_check.bundle_check(d, "aaa111", "bbb222")
    assert check.refusals == []
    assert any("container_name" in n for n in check.notes)
    assert any("HARBOR_ID" in n for n in check.notes)
    # ...and it was *read*. The state below is the other one.
    assert not any("could not be read" in n for n in check.notes)


def test_compose_bundle_check_tells_an_unread_file_from_one_that_names_nothing(
        tmp_path):
    """An undecodable compose file gets exactly one "could not be read" note
    and no per-field notes."""
    d = str(tmp_path)
    open(os.path.join(d, bundle_names.DOCKER_COMPOSE_FILE), "wb").write(b"\xff\xfe\x00x")
    assert bundle_check.compose_identity(d) is None         # not {}: nobody read it
    check = bundle_check.bundle_check(d, "aaa111", "bbb222")
    assert check.refusals == []
    assert len([n for n in check.notes if "could not be read" in n]) == 1
    assert not any("names no" in n for n in check.notes)


# -- a required value written as a file, not as a variable --------------------
#
# A blank file option puts its marker in the file's bytes, where neither
# compose_unset nor profile.json (which omits sv_tls_key) can see it.

def _sv_docker_bundle(tmp_path, **opts):
    return _docker_bundle(tmp_path, **{"sv_hostname": SV_HOST,
                                       "sv_tls_cert": SV_CERT,
                                       "sv_tls_key": SV_KEY, **opts})


def test_compose_bundle_check_refuses_a_mounted_file_left_blank(tmp_path):
    """A marker inside a mounted file is refused though neither compose_unset
    nor the profile can see it."""
    d = _sv_docker_bundle(tmp_path, sv_tls_key="")
    prof = gen.load_profile(d)
    # Neither of the two things this check already read can see it.
    assert required_fields.placeholder_options(prof) == [] and bundle_check.compose_unset(d) == []
    refusals = bundle_check.bundle_check(d, "aaa111", "bbb222", prof).refusals
    assert any(bundle_names.DOCKER_SV_KEY_FILE in r
               and markers.marker("sv_tls_key") in r
               and "sv_tls_key" in r for r in refusals)


def test_compose_bundle_check_passes_a_bundle_whose_files_are_filled_in(tmp_path):
    """The same SV bundle with its TLS pair supplied passes."""
    d = _sv_docker_bundle(tmp_path)
    assert bundle_check.bundle_check(d, "aaa111", "bbb222",
                                 gen.load_profile(d)) == bundle_check.BundleCheck([], [])


def test_compose_bundle_check_takes_the_escape_hatch_the_bundle_offers(
        tmp_path, monkeypatch):
    """An override variable pointing at a real file is what gets checked, as
    compose would mount it."""
    d = _sv_docker_bundle(tmp_path, sv_tls_key="")
    real = tmp_path / "keys" / "host.key"
    real.parent.mkdir()
    real.write_text(SV_KEY)
    monkeypatch.setenv("SV_TLS_KEY", str(real))
    assert bundle_check.bundle_check(d, "aaa111", "bbb222",
                                 gen.load_profile(d)).refusals == []


def test_compose_bundle_check_notes_a_mounted_file_it_could_not_read(tmp_path):
    """An unreadable mounted file is a note, not a refusal."""
    d = _docker_bundle(tmp_path, ca_bundle=CA_PEM)
    open(os.path.join(d, bundle_names.DOCKER_CA_FILE), "wb").write(b"\xff\xfe\x00x")
    check = bundle_check.bundle_check(d, "aaa111", "bbb222", gen.load_profile(d))
    assert check.refusals == []
    assert any(bundle_names.DOCKER_CA_FILE in n and "could not be read" in n
               for n in check.notes)


def test_run_compose_refuses_a_blank_mounted_file_before_it_starts_anything(
        monkeypatch, tmp_path):
    """run_compose raises before compose is touched when a mounted file is
    blank."""
    d = _sv_docker_bundle(tmp_path, sv_tls_key="")
    for name in ("compose_up", "compose_down", "compose_tool"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a bundle with a blank {bundle_names.DOCKER_SV_KEY_FILE}"))
    with pytest.raises(bundle_check.BundleMismatch) as caught:
        livetest.run_compose(None, d, "aaa111", "bbb222",
                             opts=gen.load_profile(d))
    assert bundle_names.DOCKER_SV_KEY_FILE in str(caught.value)


def test_bundle_check_reports_its_notes_and_hands_back_its_refusals(capsys):
    """report() prints the notes and returns the joined refusals, or None."""
    assert bundle_check.BundleCheck([], []).report() is None
    assert bundle_check.BundleCheck([], ["only a note"]).report() is None
    assert capsys.readouterr().out == "note: only a note\n"
    assert bundle_check.BundleCheck(["a", "b"], []).report() == "a\nb"


def test_run_compose_refuses_before_it_starts_anything(monkeypatch, tmp_path):
    """run_compose raises before compose is touched for another agent's bundle."""
    d = _docker_bundle(tmp_path)
    for name in ("compose_up", "compose_down", "compose_tool"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a bundle built for another agent"))
    with pytest.raises(bundle_check.BundleMismatch) as caught:
        livetest.run_compose(None, d, "aaa111", "ccc333")
    assert bundle_names.docker_container_name("ccc333") in str(caught.value)


def test_run_refuses_a_compose_bundle_rather_than_deploying_nothing(
        monkeypatch, tmp_path):
    """run() raises for a docker bundle instead of applying an empty glob."""
    d = _docker_bundle(tmp_path)
    for name in ("ensure_cluster", "deploy", "teardown"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a docker bundle"))
    with pytest.raises(bundle_check.BundleMismatch) as caught:
        livetest.run(None, d, "ns1", "aaa111", "bbb222",
                     opts=gen.load_profile(d), cluster="minikube")
    assert "docker" in str(caught.value) and "livetest" in str(caught.value)


def test_run_compose_brings_it_up_waits_and_takes_it_down(monkeypatch, tmp_path):
    """Up, one online check against the account, then down after up."""
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch)
    client = _FakeBzm()
    assert livetest.run_compose(client, d, "aaa111", "bbb222",
                                opts=gen.load_profile(d)) is True
    assert client.calls == 1
    assert cmds[0] == livetest.COMPOSE_TOOL + ["version"]
    # Everything after `-f <the bundle's compose file>`.
    verbs = [c[4:] for c in cmds if c[:3] == livetest.COMPOSE_TOOL + ["-f"]]
    assert ["up", "-d"] in verbs
    # Down after up, and not before: a finally that ran on a run which never
    # started would stop whatever else holds that project name.
    assert verbs.index(["up", "-d"]) < verbs.index(["down", "--remove-orphans"])


def test_run_compose_fails_when_the_account_never_sees_the_agent(monkeypatch,
                                                                 tmp_path):
    """An agent never online fails the run and prints the container log."""
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch)
    assert livetest.run_compose(_FakeBzm(state="offline"), d, "aaa111",
                               "bbb222", timeout=0) is False
    assert any(c[4:6] == ["logs", "--tail"] for c in cmds)


def test_run_compose_takes_it_down_even_when_the_wait_raises(monkeypatch,
                                                             tmp_path):
    """compose down runs in the finally."""
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch)
    monkeypatch.setattr(livetest, "wait_online", lambda *a, **kw: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        livetest.run_compose(_FakeBzm(), d, "aaa111", "bbb222")
    assert any(c[4:] == ["down", "--remove-orphans"] for c in cmds)


def test_run_compose_keeps_it_up_when_asked(monkeypatch, tmp_path):
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch)
    livetest.run_compose(_FakeBzm(), d, "aaa111", "bbb222", keep=True)
    assert not any("down" in c for c in cmds)


def test_teardown_removes_a_container_compose_down_left_behind(monkeypatch,
                                                               tmp_path):
    """A container that survives `down` is removed by name."""
    d = _docker_bundle(tmp_path)
    name = bundle_names.docker_container_name("bbb222")
    cmds = _fake_daemon(monkeypatch, present=[name])
    livetest.compose_down(d, name)
    assert ["docker", "rm", "-f", name] in cmds


def test_teardown_removes_nothing_by_name_when_down_worked(monkeypatch,
                                                           tmp_path):
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch, present=[])
    livetest.compose_down(d, bundle_names.docker_container_name("bbb222"))
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in cmds)


def test_compose_tool_says_so_when_the_plugin_is_missing(monkeypatch):
    """A host without Compose v2 raises one sentence up front."""
    _fake_daemon(monkeypatch, compose=False)
    with pytest.raises(RuntimeError) as caught:
        livetest.compose_tool()
    assert "Compose v2" in str(caught.value)


def test_compose_file_relative_paths_resolve_against_the_bundle(monkeypatch,
                                                                tmp_path):
    """compose is invoked with -f on the bundle's own compose file."""
    d = _docker_bundle(tmp_path, ca_bundle=CA_PEM)
    cmds = _fake_daemon(monkeypatch)
    livetest.run_compose(_FakeBzm(), d, "aaa111", "bbb222")
    up = next(c for c in cmds if c[-2:] == ["up", "-d"])
    assert up[2] == "-f" and up[3] == os.path.join(d, bundle_names.DOCKER_COMPOSE_FILE)


def test_profile_json_roundtrip(tmp_path):
    files = gen.generate(FACTS, {"namespace": "ns1", "platform": "k8s",
                                 "private_registry": "reg:5001",
                                 "auth_token": "tok"})
    gen.write(files, str(tmp_path))
    prof = gen.load_profile(str(tmp_path))
    assert prof["namespace"] == "ns1" and prof["platform"] == "k8s"
    assert prof["private_registry"] == "reg:5001"
    assert "auth_token" not in prof  # credential is re-fetched, never written
    # Replaying it reproduces the manifests.
    again = gen.generate(FACTS, {**prof, "auth_token": "tok"})
    assert again["bzm_deployment.yaml"] == files["bzm_deployment.yaml"]
    assert json.loads(again[bundle_names.PROFILE_FILE]) == prof


# -- sv_mocks ---------------------------------------------------------------

def _sv_pod(name, port, harbor="h1", ship="s1", extra=None):
    labels = {service_virt.SV_POD_NAME_LABEL: name, service_virt.SV_POD_HARBOR_LABEL: harbor,
              service_virt.SV_POD_SHIP_LABEL: ship} if name else {}
    return {"metadata": {"labels": {**labels, **(extra or {})}},
            "spec": {"containers": [
                {"ports": [{"containerPort": port}] if port else []}]}}


def _pods(monkeypatch, items):
    monkeypatch.setattr(kube, "kget",
                        lambda cli, ns, kind, name=None: {"items": items})


def test_sv_mocks_reads_identity_and_port_off_the_pods(monkeypatch):
    """Name, port, harbor and ship come off the pod labels and container port."""
    _pods(monkeypatch, [_sv_pod("vs2", 8080, "hA", "sA"),
                        _sv_pod("vs1", 9000, "hA", "sA")])
    assert sv_read.sv_mocks("kubectl", "ns1") == [
        {"name": "vs1", "port": 9000, "harbor": "hA", "ship": "sA"},
        {"name": "vs2", "port": 8080, "harbor": "hA", "ship": "sA"},
    ]


def test_sv_mocks_ignores_pods_that_are_not_virtual_services(monkeypatch):
    """Pods without the mock name label are skipped."""
    _pods(monkeypatch, [_sv_pod(None, 5000, extra={"role": "role-crane"}),
                        _sv_pod("vs1", 8080)])
    assert [m["name"] for m in sv_read.sv_mocks("kubectl", "ns1")] == ["vs1"]


def test_sv_mocks_dedupes_a_mid_rollout_namespace(monkeypatch):
    """Two pods for one mock yield one entry."""
    _pods(monkeypatch, [_sv_pod("vs1", 8080), _sv_pod("vs1", 8080)])
    assert len(sv_read.sv_mocks("kubectl", "ns1")) == 1


def test_sv_mocks_skips_a_labelled_pod_with_no_container_port(monkeypatch):
    """A mock pod with no container port is skipped."""
    _pods(monkeypatch, [_sv_pod("vs1", None)])
    assert sv_read.sv_mocks("kubectl", "ns1") == []


def test_sv_mocks_is_empty_when_the_namespace_cannot_be_read(monkeypatch):
    """An unreadable namespace yields no mocks."""
    monkeypatch.setattr(kube, "kget", lambda *a, **k: {})
    assert sv_read.sv_mocks("kubectl", "ns1") == []


# -- sv_read: the same namespace, plus why it could not be read ---------------

def _fake_kubectl(monkeypatch, *, tools=("kubectl",), rc=0, stdout="", stderr=""):
    """Stand in for kubectl/oc; any binary not in `tools` raises
    FileNotFoundError."""
    kube.cli_tool.cache_clear()

    def run(cmd, *a, **kw):
        if cmd[0] not in tools:
            raise FileNotFoundError(cmd[0])
        if cmd[1:3] == ["version", "--client"]:
            return subprocess.CompletedProcess(cmd, 0, "v1.32.0", "")
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)


@pytest.fixture(autouse=True)
def _uncached_cli_tool():
    """cli_tool() is memoised; clear it around each test."""
    kube.cli_tool.cache_clear()
    yield
    kube.cli_tool.cache_clear()


def test_sv_read_returns_the_mocks_when_the_namespace_can_be_read(monkeypatch):
    _fake_kubectl(monkeypatch, stdout=json.dumps(
        {"items": [_sv_pod("vs1", 8080, "hA", "sA")]}))
    read = sv_read.sv_read("ns1")
    assert read.status == sv_read.SV_READ_OK
    assert read.mocks == [{"name": "vs1", "port": 8080, "harbor": "hA",
                           "ship": "sA"}]


def test_sv_read_reports_a_missing_cli_rather_than_an_empty_namespace(monkeypatch):
    _fake_kubectl(monkeypatch, tools=())
    read = sv_read.sv_read("ns1")
    assert read.status == sv_read.SV_READ_NO_CLI
    assert read.mocks == [] and "kubectl" in read.detail


@pytest.mark.parametrize("stderr, status", [
    # kubectl with no kubeconfig at all, and with one whose server is gone.
    ("The connection to the server localhost:8080 was refused - did you "
     "specify the right host or port?", sv_read.SV_READ_NO_CONTEXT),
    ("error: current-context is not set", sv_read.SV_READ_NO_CONTEXT),
    ("Unable to connect to the server: dial tcp: lookup api.example.com: "
     "no such host", sv_read.SV_READ_NO_CONTEXT),
    # oc's wording for the same thing.
    ("error: Missing or incomplete configuration info.",
     sv_read.SV_READ_NO_CONTEXT),
    # Authenticated but not allowed, and no longer authenticated at all.
    ('Error from server (Forbidden): pods is forbidden: User "dev" cannot '
     'list resource "pods" in API group "" in the namespace "ns1"',
     sv_read.SV_READ_DENIED),
    ("error: You must be logged in to the server (Unauthorized)",
     sv_read.SV_READ_DENIED),
    # A namespace that is not there answers the same question as an empty one:
    # nothing is deployed to expose.
    ('Error from server (NotFound): namespaces "ns1" not found',
     sv_read.SV_READ_NO_MOCKS),
])
def test_sv_read_tells_the_failures_apart_by_what_the_cli_printed(
        monkeypatch, stderr, status):
    _fake_kubectl(monkeypatch, rc=1, stderr=stderr)
    read = sv_read.sv_read("ns1")
    assert read.status == status
    assert read.detail == stderr        # the raw message travels with it


def test_sv_read_separates_a_readable_namespace_with_no_virtual_services(monkeypatch):
    """A readable namespace with no mock pods is no_mocks, not denied or no
    cluster."""
    _fake_kubectl(monkeypatch, stdout=json.dumps(
        {"items": [_sv_pod(None, 5000, extra={"role": "role-crane"})]}))
    read = sv_read.sv_read("ns1")
    assert read.status == sv_read.SV_READ_NO_MOCKS and read.mocks == []


def test_sv_read_survives_a_zero_exit_that_is_not_json(monkeypatch):
    """A zero exit with non-JSON output is no_context carrying the raw output."""
    _fake_kubectl(monkeypatch, stdout="Kubeconfig user entry is using deprecated API\n")
    read = sv_read.sv_read("ns1")
    assert read.status == sv_read.SV_READ_NO_CONTEXT
    assert read.mocks == []
    assert "deprecated" in read.detail        # the raw output travels with it


def test_sv_read_does_not_hang_on_an_unreachable_api_server(monkeypatch):
    """A kubectl timeout is no_context naming the timeout."""
    def run(cmd, *a, **kw):
        if cmd[1:3] == ["version", "--client"]:
            return subprocess.CompletedProcess(cmd, 0, "v1.32.0", "")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 15))

    monkeypatch.setattr(subprocess, "run", run)
    read = sv_read.sv_read("ns1", timeout=3)
    assert read.status == sv_read.SV_READ_NO_CONTEXT
    assert "3s" in read.detail


# -- whose cluster is it -------------------------------------------------------
#
# The rig reuses an existing bzm-opl-test cluster; teardown may delete only one
# this run built.


def _kind_present(monkeypatch, names, cmds):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, stdout=" ".join(names), stderr=""))
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))


def test_ensure_kind_reports_a_cluster_it_created(monkeypatch):
    cmds = []
    _kind_present(monkeypatch, [], cmds)
    assert livetest.ensure_kind() is True
    assert any("create" in c for c in cmds)


def test_ensure_kind_reports_a_cluster_it_only_reused(monkeypatch):
    cmds = []
    _kind_present(monkeypatch, [livetest.KIND_CLUSTER], cmds)
    assert livetest.ensure_kind() is False
    assert not any("create" in c for c in cmds)


def test_ensure_cluster_passes_the_answer_on(monkeypatch):
    monkeypatch.setattr(livetest, "ensure_kind", lambda **k: True)
    monkeypatch.setattr(livetest, "ensure_minikube", lambda *a, **k: False)
    assert livetest.ensure_cluster("kind") is True
    assert livetest.ensure_cluster("minikube") is False
    # Nobody's cluster to own: the run was pointed at whatever kubectl has.
    assert livetest.ensure_cluster("current") is False


def test_teardown_deletes_a_cluster_this_run_created(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned(cluster=True))
    assert ["kind", "delete", "cluster", "--name", livetest.KIND_CLUSTER] in cmds


def test_teardown_leaves_a_cluster_it_did_not_create(monkeypatch, tmp_path):
    """A reused cluster survives teardown, and the applied objects are still
    deleted."""
    (tmp_path / "bzm_deployment.yaml").write_text("kind: Deployment\n")
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned())
    assert not any("delete" in c and "cluster" in c for c in cmds)
    assert any(c[:2] == ["kubectl", "-n"] and "delete" in c for c in cmds)


def test_teardown_leaves_a_minikube_profile_it_did_not_create(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "minikube", livetest.Owned())
    assert not any("minikube" in c for c in cmds)


def test_teardown_defaults_to_leaving_the_cluster_alone(monkeypatch, tmp_path):
    """With no Owned record, teardown deletes no cluster."""
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "minikube")
    assert not any("minikube" in c for c in cmds)


def _minikube_host(monkeypatch, host, cmds, exists=None):
    """`host` is what `minikube status` prints; `exists` defaults to whether a
    state was printed."""
    if exists is None:
        exists = bool(host)
    monkeypatch.setattr(livetest, "minikube_profile_exists", lambda: exists)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, stdout=host, stderr=""))
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest.platform, "machine", lambda: "x86_64")


def test_a_minikube_profile_this_run_started_from_nothing_is_ours(monkeypatch):
    cmds = []
    _minikube_host(monkeypatch, "", cmds)          # no such profile
    assert livetest.ensure_minikube() is True
    assert any("start" in c for c in cmds)


def test_a_running_minikube_profile_is_not_ours(monkeypatch):
    cmds = []
    _minikube_host(monkeypatch, "Running", cmds)
    assert livetest.ensure_minikube() is False
    assert not any("start" in c for c in cmds)


def test_a_stopped_minikube_profile_is_started_but_still_not_ours(monkeypatch):
    """A stopped profile is started but not owned."""
    cmds = []
    _minikube_host(monkeypatch, "Stopped", cmds)
    assert livetest.ensure_minikube() is False
    assert any("start" in c for c in cmds)          # still started, just not owned


def test_a_recreated_minikube_profile_is_ours(monkeypatch):
    """A running profile recreated for --contain-egress is owned."""
    cmds = []
    _minikube_host(monkeypatch, "Running", cmds)
    monkeypatch.setattr(livetest, "policy_enforced", lambda: False)
    assert livetest.ensure_minikube(cni="calico") is True
    assert any("delete" in c for c in cmds) and any("start" in c for c in cmds)


# -- what a cluster that survives keeps ---------------------------------------


def test_a_profile_in_an_unlisted_state_is_not_claimed(monkeypatch):
    """A profile listed in any state exists, so it is not ours."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0,
                            stdout=json.dumps({"valid": [{"Name": livetest.MINIKUBE_PROFILE}],
                                               "invalid": []}), stderr=""))
    assert livetest.minikube_profile_exists() is True


def test_an_absent_profile_is_ours_to_create(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, stdout='{"valid":[],"invalid":[]}', stderr=""))
    assert livetest.minikube_profile_exists() is False


def test_an_unreadable_profile_list_is_not_ours(monkeypatch, capsys):
    """An unreadable profile list answers "exists" and says so."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 1, stdout="minikube: command not found", stderr=""))
    assert livetest.minikube_profile_exists() is True
    assert "could not read" in capsys.readouterr().out


def test_the_profile_state_is_only_read_where_a_profile_exists(monkeypatch):
    """A not-found status message is not read as a host state."""
    cmds = []
    _minikube_host(monkeypatch, 'Profile "bzm-opl-test" not found.', cmds,
                   exists=False)
    assert livetest.ensure_minikube() is True


def test_policy_is_judged_after_the_context_is_this_profile(monkeypatch):
    """policy_enforced() is asked only after switching to this profile's
    context."""
    cmds, asked_at = [], []
    _minikube_host(monkeypatch, "Running", cmds)
    monkeypatch.setattr(livetest, "policy_enforced",
                        lambda: asked_at.append(len(cmds)) or True)
    livetest.ensure_minikube(cni="calico")
    switched = [i for i, c in enumerate(cmds) if "use-context" in c]
    assert switched and asked_at[0] > switched[0]


def test_teardown_drops_a_namespace_this_run_created(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned(namespace=True))
    assert ["kubectl", "delete", "ns", "ns1", "--ignore-not-found"] in cmds


def test_teardown_removes_the_egress_policy_from_a_namespace_it_kept(monkeypatch, tmp_path):
    """The dotfile egress policy is deleted by name from a surviving namespace."""
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned())
    assert any(livetest.EGRESS_POLICY_NAME in c and "delete" in c for c in cmds)


def test_teardown_restores_the_node_hosts_file_on_a_cluster_it_keeps(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "minikube",
                      livetest.Owned(blackholed=["gcr.io"]))
    assert any("/etc/hosts" in str(c) and "gcr.io" in str(c) for c in cmds)


def test_teardown_does_not_bother_restoring_hosts_on_a_cluster_it_deletes(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    livetest.teardown(str(tmp_path), "ns1", "minikube",
                      livetest.Owned(cluster=True, blackholed=["gcr.io"]))
    assert not any("/etc/hosts" in str(c) for c in cmds)


def test_ensure_namespace_reports_one_it_did_not_create(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: None)
    assert kube.ensure_namespace("kubectl", "ns1") is False


def test_ensure_namespace_reports_one_it_created(monkeypatch):
    cmds = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""))
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    assert kube.ensure_namespace("kubectl", "ns1") is True
    assert ["kubectl", "create", "ns", "ns1"] in cmds


def test_owned_defaults_to_owning_nothing():
    o = livetest.Owned()
    assert (o.cluster, o.namespace, o.blackholed) == (False, False, ())


# -- the existing-ConfigMap and file CA modes ----------------------------------
#
# The rig's key is not `ca-bundle.crt` (_ca_cfg's fallback), so a pass proves
# the configured key reached the pod.


def test_the_existing_mode_overlay_names_the_rigs_own_configmap():
    o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")
    assert o["ca_existing_configmap"] == bundle_check.CA_RIG_CONFIGMAP
    assert o["ca_configmap_key"] == bundle_check.CA_RIG_KEY
    assert o["ca_bundle"] is None and o["ca_openshift_inject"] is False


def test_the_rigs_key_is_not_the_generators_default():
    """The rig's CA key differs from the generator's fallback key."""
    assert bundle_check.CA_RIG_KEY != ca_trust.CA_FILENAME


def test_the_existing_mode_still_carries_the_proxy():
    o = livetest.proxy_overlay("h", 8080, CA_PEM, "bzm", "s3cr3t",
                               ca_mode="existing")
    assert o["proxy"]["https"] == "http://h:8080"
    assert o["proxy"]["username"] == "bzm"


def test_the_existing_mode_renders_a_bundle_that_mounts_the_rigs_key():
    """The existing-mode overlay renders no ConfigMap of its own and mounts the
    rig's name and key."""
    opts = {"namespace": "ns1", "auth_token": "tok",
            **livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")}
    files = gen.generate(FACTS, opts)
    assert "bzm_cacerts.yaml" not in files
    d = yaml.safe_load(files["bzm_deployment.yaml"])
    vol = d["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"]
    assert vol == bundle_check.CA_RIG_CONFIGMAP
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["REQUESTS_CA_BUNDLE"] == f"/var/cm/{bundle_check.CA_RIG_KEY}"
    assert cm["KUBERNETES_CA_BUNDLE_MOUNT"] == (
        f"REQUESTS_CA_BUNDLE={bundle_check.CA_RIG_CONFIGMAP}={bundle_check.CA_RIG_KEY}:"
        f"AWS_CA_BUNDLE={bundle_check.CA_RIG_CONFIGMAP}={bundle_check.CA_RIG_KEY}")


def test_the_existing_mode_wins_over_a_profile_carrying_an_inline_pem():
    """The overlay replaces an inline PEM already in the profile."""
    opts = {"namespace": "ns1", "ca_bundle": "-----BEGIN CERTIFICATE-----\nx\n"
                                             "-----END CERTIFICATE-----",
            **livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")}
    files = gen.generate(FACTS, opts)
    assert "bzm_cacerts.yaml" not in files


def test_ensure_ca_configmap_creates_it_with_the_key_the_bundle_mounts(
        monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    assert livetest.ensure_ca_configmap("kubectl", "ns1", CA_PEM) is True
    create, = [c for c in cmds if "create" in c and "configmap" in c]
    assert create[:5] == ["kubectl", "-n", "ns1", "create", "configmap"]
    assert create[5] == bundle_check.CA_RIG_CONFIGMAP
    # --from-file=<key>=<path>, the explicit form: the bare one would key the
    # entry on the temp file's own name and mount an empty file.
    arg, = [a for a in create if a.startswith("--from-file=")]
    assert arg.startswith(f"--from-file={bundle_check.CA_RIG_KEY}=")
    written = arg.split("=", 2)[2]
    assert not os.path.exists(written), "the temp CA file outlived the call"


def test_ensure_ca_configmap_refuses_one_it_did_not_create(monkeypatch):
    """An existing ConfigMap of that name is refused, not overwritten."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    with pytest.raises(RuntimeError, match=bundle_check.CA_RIG_CONFIGMAP):
        livetest.ensure_ca_configmap("kubectl", "ns1", CA_PEM)


def test_teardown_removes_the_ca_configmap_from_a_namespace_it_kept(
        monkeypatch, tmp_path):
    """The rig's CA ConfigMap is deleted from a surviving namespace."""
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind",
                      livetest.Owned(ca_configmap=bundle_check.CA_RIG_CONFIGMAP))
    assert ["kubectl", "-n", "ns1", "delete", "cm", bundle_check.CA_RIG_CONFIGMAP,
            "--ignore-not-found"] in cmds


def test_teardown_removes_the_name_this_run_made_not_a_constant(
        monkeypatch, tmp_path):
    """Teardown deletes the recorded ConfigMap name, e.g. the generator's in
    file mode."""
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind",
                      livetest.Owned(ca_configmap=bundle_names.CA_CONFIGMAP))
    assert ["kubectl", "-n", "ns1", "delete", "cm", bundle_names.CA_CONFIGMAP,
            "--ignore-not-found"] in cmds
    assert not any(bundle_check.CA_RIG_CONFIGMAP in c for c in cmds)


def test_teardown_does_not_remove_a_ca_configmap_this_run_did_not_create(
        monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(kube, "run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned())
    assert not any(bundle_check.CA_RIG_CONFIGMAP in c for c in cmds)


def test_the_negative_control_deletes_the_name_the_file_mode_depends_on(
        monkeypatch, tmp_path):
    """The negative control deletes CA_CONFIGMAP, which is why run() creates
    the file-mode ConfigMap after it."""
    cmds = []
    monkeypatch.setattr(kube, "run",
                        lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(livetest, "deploy", lambda *a, **k: "kubectl")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    overlay = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="file")
    livetest.negative_control(lambda o: None, overlay, str(tmp_path), "ns1",
                              "kind", timeout=0)
    assert ["kubectl", "-n", "ns1", "delete", "cm", bundle_names.CA_CONFIGMAP,
            "--ignore-not-found"] in cmds


def test_owned_still_defaults_to_owning_nothing():
    """Owned().ca_configmap defaults to None."""
    assert livetest.Owned().ca_configmap is None


def test_the_negative_control_clears_whichever_mode_the_run_is_using(
        monkeypatch, tmp_path):
    """The control clears the existing-ConfigMap mode, not only ca_bundle."""
    seen = {}
    monkeypatch.setattr(kube, "run", lambda *a, **k: None)
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(livetest, "deploy", lambda *a, **k: "kubectl")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    overlay = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")
    livetest.negative_control(lambda o: seen.update(o), overlay,
                              str(tmp_path), "ns1", "kind", timeout=0)
    assert seen["ca_bundle"] is None
    assert seen["ca_existing_configmap"] is None
    assert seen["ca_openshift_inject"] is False


def test_the_negative_control_clears_every_ca_option_the_generator_has(
        monkeypatch, tmp_path):
    """The control resets every generator CA option to its default, leaving no
    CA configured."""
    seen = {}
    monkeypatch.setattr(kube, "run", lambda *a, **k: None)
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(livetest, "deploy", lambda *a, **k: "kubectl")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    overlay = {**livetest.proxy_overlay("h", 8080, CA_PEM), "ca_bundle_slot": True}
    livetest.negative_control(lambda o: seen.update(o), overlay,
                              str(tmp_path), "ns1", "kind", timeout=0)
    for key in ca_trust.CA_OPTIONS:
        assert seen[key] == bundle_options.DEFAULT_OPTIONS[key], key
    assert ca_trust.ca_cfg({**bundle_options.DEFAULT_OPTIONS, **seen}) is None


def test_the_proxy_overlay_replaces_every_ca_mode_too():
    """The overlay sets every CA option, so a mode already in the profile
    cannot survive beside it."""
    for mode in ("inline", "existing", "file"):
        o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode=mode)
        for key in ca_trust.CA_OPTIONS:
            assert key in o, f"{mode}: {key} is left for the profile to answer"
        assert ca_trust.ca_cfg({**bundle_options.DEFAULT_OPTIONS, "ca_bundle_slot": True, **o})


def test_the_file_mode_names_the_configmap_the_bundle_itself_writes():
    """File mode mounts bundle_names.CA_CONFIGMAP under the rig's non-default key."""
    o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="file")
    ca = ca_trust.ca_cfg({**bundle_options.DEFAULT_OPTIONS, **o})
    assert ca["mode"] == "file"
    assert ca["cm"] == bundle_names.CA_CONFIGMAP
    assert ca["key"] == bundle_check.CA_RIG_KEY != ca_trust.CA_FILENAME


def test_the_file_mode_bundle_creates_no_configmap_for_the_rig_to_collide_with():
    """A file-mode bundle emits no CA ConfigMap but its Deployment mounts one."""
    from test_generate import FACTS
    o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="file")
    files = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222", **o})
    assert bundle_names.CA_CONFIGMAP_FILE not in files
    # ...and the Deployment still mounts it, which is the half that makes the
    # rig-created object reachable at all.
    dep = files["bzm_deployment.yaml"]
    assert bundle_names.CA_CONFIGMAP in dep and bundle_check.CA_RIG_KEY in \
        files["bzm_configmap.yaml"]


# -- what a run owes the CA mode it was handed ---------------------------------
#
# Without the proxy, file/existing name a ConfigMap nothing creates and are
# refused; with it, the re-render uses the bundle's own mode unless told
# otherwise.


@pytest.mark.parametrize("options,mode", [
    ({}, None),
    ({"ca_bundle": CA_PEM}, "inline"),
    ({"ca_existing_configmap": "corp-trust"}, "existing"),
    ({"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"}, "file"),
    ({"ca_openshift_inject": True}, None),
])
def test_the_rig_reads_which_ca_mode_a_bundle_is_already_in(options, mode):
    """No CA and OpenShift injection both answer None."""
    assert bundle_check.rig_ca_mode(dict(options, namespace="ns1")) == mode


def test_every_rig_ca_mode_is_one_the_overlay_can_build():
    """Every --ca-mode choice renders and reads back as itself."""
    for mode in bundle_check.RIG_CA_MODES:
        o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode=mode)
        assert bundle_check.rig_ca_mode({"namespace": "ns1", **o}) == mode


@pytest.mark.parametrize("options,names", [
    ({"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"},
     (bundle_names.CA_CONFIGMAP, "corp-root.pem", "--ca-mode file")),
    ({"ca_existing_configmap": "corp-trust"},
     ("corp-trust", "--ca-mode existing")),
])
def test_a_ca_configmap_nothing_creates_is_refused_before_the_cluster(options,
                                                                     names):
    """file/existing without --local-proxy are refused, naming the ConfigMap
    and the flag that builds it."""
    said = bundle_check.ca_configmap_refusal(dict(options, namespace="ns1"), False)
    for name in names:
        assert name in said


def test_a_bundle_that_leaves_the_certificate_unnamed_is_refused_by_its_marker():
    """An unnamed certificate file is refused quoting its marker."""
    said = bundle_check.ca_configmap_refusal({"namespace": "ns1",
                                          "ca_bundle_slot": True}, False)
    assert markers.marker("ca_cert_file") in said


@pytest.mark.parametrize("options", [
    {},
    {"ca_bundle": CA_PEM},
    {"ca_openshift_inject": True},
])
def test_the_modes_that_carry_their_own_configmap_are_not_refused(options):
    """No CA, inline and inject are not refused."""
    assert bundle_check.ca_configmap_refusal(dict(options, namespace="ns1"),
                                         False) is None


@pytest.mark.parametrize("options", [
    {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"},
    {"ca_existing_configmap": "corp-trust"},
])
def test_the_proxy_is_what_makes_those_two_modes_deployable(options):
    """--local-proxy makes file/existing deployable."""
    assert bundle_check.ca_configmap_refusal(dict(options, namespace="ns1"),
                                         True) is None


@pytest.mark.parametrize("local_proxy", [False, True])
def test_a_profile_setting_two_ca_modes_is_refused_either_way(local_proxy):
    """Two CA modes in one profile are refused with or without the proxy."""
    said = bundle_check.ca_configmap_refusal(
        {"namespace": "ns1", "ca_bundle_slot": True, "ca_bundle": CA_PEM},
        local_proxy)
    assert said and bundle_names.PROFILE_FILE in said and "CA mode" in said


@pytest.mark.parametrize("asked,carried,resolved", [
    (None, {}, "inline"),
    (None, {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"}, "file"),
    (None, {"ca_openshift_inject": True}, "inline"),
    ("inline", {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"},
     "inline"),
])
def test_one_resolver_answers_for_the_cli_and_for_run(asked, carried, resolved):
    """asked, else the bundle's mode, else inline; idempotent."""
    profile = dict(carried, namespace="ns1")
    assert bundle_check.resolved_ca_mode(profile, asked) == resolved
    assert bundle_check.resolved_ca_mode(profile, resolved) == resolved


@pytest.mark.parametrize("options,says", [
    ({"ca_bundle": CA_PEM}, "what this bundle was generated for"),
    ({}, "configures no CA trust"),
    ({"ca_openshift_inject": True}, "OpenShift trust injection"),
])
def test_the_run_says_which_ca_mode_it_is_about_to_deploy(options, says):
    """The notice distinguishes the bundle's own mode, no CA, and injection."""
    profile = dict(options, namespace="ns1")
    said = bundle_check.ca_mode_notice(profile,
                                   bundle_check.resolved_ca_mode(profile))
    assert says in said


def test_the_notice_says_when_a_flag_replaces_the_bundles_mode():
    """A --ca-mode replacing the bundle's mode is said out loud."""
    said = bundle_check.ca_mode_notice(
        {"namespace": "ns1", "ca_bundle_slot": True,
         "ca_cert_file": "corp-root.pem"}, "inline")
    assert "file" in said and "--ca-mode inline replaces it" in said


def test_the_notice_does_not_claim_the_existing_mode_keeps_the_bundles_object():
    """The existing-mode notice names the rig's own ConfigMap."""
    said = bundle_check.ca_mode_notice({"namespace": "ns1",
                                    "ca_existing_configmap": "corp-trust"},
                                   "existing")
    assert bundle_check.CA_RIG_CONFIGMAP in said


def test_a_second_ensure_cluster_does_not_say_the_run_will_keep_what_it_built(
        monkeypatch, capsys):
    """announce=False prints no "will not delete it"."""
    monkeypatch.setattr(livetest, "minikube_profile_exists", lambda: True)
    monkeypatch.setattr(kube, "run", lambda *a, **k: None)
    monkeypatch.setattr(livetest, "policy_enforced", lambda *a, **k: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "Running", ""))
    livetest.ensure_minikube(announce=False)
    assert "will not delete it" not in capsys.readouterr().out


def test_ensure_cluster_still_announces_a_reuse_to_the_caller_that_keeps_it(
        monkeypatch, capsys):
    """The default call announces a reused profile."""
    monkeypatch.setattr(livetest, "minikube_profile_exists", lambda: True)
    monkeypatch.setattr(kube, "run", lambda *a, **k: None)
    monkeypatch.setattr(livetest, "policy_enforced", lambda *a, **k: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "Running", ""))
    livetest.ensure_minikube()
    assert "will not delete it" in capsys.readouterr().out


class _Done:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.mark.parametrize("answer, expected", [
    (_Done(0, '{"metadata": {"name": "ns1"}}'), {"metadata": {"name": "ns1"}}),
    (_Done(1, stderr='Error from server (NotFound): namespaces "ns1" not found'), {}),
    (_Done(1, stderr='Error from server (Forbidden): namespaces "ns1" is '
                     'forbidden: User "u" cannot get resource'), None),
    (FileNotFoundError("kubectl"), None),
])
def test_kget_named_keeps_absent_apart_from_unread(monkeypatch, answer, expected):
    """NotFound is {}; every other failure is None; kget folds both to {}."""
    def run(cmd, **kw):
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(subprocess, "run", run)
    assert kube.kget_named("kubectl", None, "ns", "ns1") == expected
    assert kube.kget("kubectl", None, "ns", "ns1") == (expected or {})


# -- kube primitives -------------------------------------------------------------

class _Clock:
    """A fake monotonic clock that advances only when slept on."""

    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def test_poll_until_returns_the_first_truthy_result():
    clock, answers = _Clock(), iter([None, 0, "ready"])
    got = kube.poll_until(lambda: next(answers), 60, 5, clock=clock, sleep=clock.sleep)
    assert got == "ready" and clock.slept == [5, 5]


def test_poll_until_gives_up_at_the_deadline_with_the_last_result():
    clock, calls = _Clock(), []
    got = kube.poll_until(lambda: calls.append(1) or False, 12, 5,
                          clock=clock, sleep=clock.sleep)
    assert got is False and len(calls) == 4 and clock.now == 15


def test_poll_until_asks_once_even_with_no_time():
    clock, calls = _Clock(), []
    kube.poll_until(lambda: calls.append(1), 0, 5, clock=clock, sleep=clock.sleep)
    assert calls == [1] and clock.slept == []


def test_apiserver_targets_are_the_clusterip_and_the_endpoints(monkeypatch):
    docs = {"svc": {"spec": {"clusterIP": "10.96.0.1", "ports": [{"port": 443}]}},
            "endpoints": {"subsets": [{"addresses": [{"ip": "192.168.49.2"}],
                                       "ports": [{"port": 8443}]}]}}
    monkeypatch.setattr(kube, "kget", lambda cli, ns, kind, name=None: docs[kind])
    assert livetest._apiserver_targets("kubectl") == [("10.96.0.1", 443),
                                                      ("192.168.49.2", 8443)]


def test_callers_reach_kube_through_the_module():
    """`from .kube import x` would bind a copy that a monkeypatch of kube.x misses."""
    pkg = os.path.join(os.path.dirname(__file__), "..", "bzm_opl_gen")
    offenders = [n for n in sorted(os.listdir(pkg)) if n.endswith(".py")
                 and any(line.lstrip().startswith("from .kube import")
                         for line in open(os.path.join(pkg, n)))]
    assert offenders == []
