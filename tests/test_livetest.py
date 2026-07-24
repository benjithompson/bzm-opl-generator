"""Offline tests for the local proxy/CA rig: the option overlay livetest builds
and the profile.json round-trip it replays through."""

import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import generate as gen, livetest  # noqa: E402
from tests.test_generate import FACTS  # noqa: E402

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
    """The overlay is what livetest merges onto profile.json -- the result must
    put the credentialed proxy URL in the Secret and mount the mitm CA."""
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
    """A profile already using the existing-ConfigMap mode must not collide with
    the rig's inline CA."""
    opts = {"namespace": "ns1", "ca_existing_configmap": "corp-trust",
            **livetest.proxy_overlay("h", 8080, CA_PEM)}
    files = gen.generate(FACTS, opts)
    assert "bzm_cacerts.yaml" in files  # inline mode won


def test_large_ca_bundle_warns_about_server_side_apply():
    """A realistic bundle (corporate CA + public roots) exceeds the 256KB
    last-applied-configuration annotation, so client-side apply rejects it."""
    big = "-----BEGIN CERTIFICATE-----\n" + ("A" * 300_000) + "\n-----END CERTIFICATE-----"
    files = gen.generate(FACTS, {"namespace": "ns1", "ca_bundle": big})
    assert "--server-side" in files["README.md"]
    assert "--server-side" not in gen.generate(
        FACTS, {"namespace": "ns1", "ca_bundle": CA_PEM})["README.md"]


def test_apply_switches_to_server_side_for_big_manifests(tmp_path, monkeypatch):
    small = tmp_path / "small.yaml"
    small.write_text("kind: ConfigMap\n")
    big = tmp_path / "big.yaml"
    big.write_text("x" * (livetest.LARGE_MANIFEST_BYTES + 1))
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    livetest._apply("kubectl", "ns1", str(small))
    livetest._apply("kubectl", "ns1", str(big))
    assert "--server-side" not in cmds[0]
    assert cmds[1][-2:] == ["--server-side", "--force-conflicts"]


def _live(monkeypatch, cm_data, images=(), ca_certs="2"):
    """Stand in for a deployed cluster: ConfigMap contents, running images, and
    what the crane pod sees at the CA path."""
    monkeypatch.setattr(livetest, "_kget", lambda *a, **k: {"data": cm_data})
    monkeypatch.setattr(livetest, "_pod_images", lambda *a: list(images))
    monkeypatch.setattr(livetest, "_crane_exec", lambda *a: ca_certs)


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
    """Lines the control's broken run logged must not fail the real run."""
    lines = {"407": ["407 once"], ":6443": [], "kubernetes.default": []}
    monkeypatch.setattr(livetest, "proxy_flows", lambda s="blazemeter.com": lines.get(s, []))
    marks = livetest.proxy_log_marks()
    assert livetest.proxy_log_failures(marks) == []
    lines["407"].append("407 again")           # new one, from the real run
    assert any("407" in f for f in livetest.proxy_log_failures(marks))


def test_blackhole_skips_the_private_registry(monkeypatch):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
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
    monkeypatch.setattr(livetest, "_crane_exec", lambda *a: f"rc={next(rcs)}")
    fails = livetest.assert_egress_contained("kubectl", "ns1")
    assert (fails == []) is ok
    if marker:
        assert any(marker in f for f in fails)


def test_policy_enforced_detects_calico(monkeypatch):
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "pod/calico-node-abc\n"})())
    assert livetest.policy_enforced()
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": ""})())
    assert not livetest.policy_enforced()


def _engine_pod(image="reg:5001/v4:1", ca=True, proxy=True):
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
    return {"metadata": {"name": "engine-abc"},
            "status": {"phase": "Running"},
            "spec": {"containers": [{"image": image, "env": env,
                                     "volumeMounts": mounts}]}}


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


def test_engine_config_catches_missing_proxy_env():
    fails = livetest.assert_engine_config(_engine_pod(proxy=False), ENGINE_OPTS)
    assert any("bypassing the customer's proxy" in f for f in fails)


def test_engine_pods_excludes_crane(monkeypatch):
    items = {"items": [{"metadata": {"name": "crane-7d9-abc"}},
                       {"metadata": {"name": "taurus-cloud-xyz"}}]}
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": json.dumps(items)})())
    pods = livetest.engine_pods("kubectl", "ns1")
    assert [p["metadata"]["name"] for p in pods] == ["taurus-cloud-xyz"]


def test_engine_proxy_evidence(monkeypatch):
    """Engine traffic is identified by the hosts only engines use -- pod IPs are
    SNAT'd to the node before the proxy sees them."""
    log = {"data.blazemeter.com": [], "storage.blazemeter.com": []}
    monkeypatch.setattr(livetest, "proxy_flows", lambda s="blazemeter.com": log.get(s, []))
    before = livetest.engine_upload_marks()
    fails = livetest.engine_proxy_evidence(before)
    assert any("never went through the proxy" in f for f in fails)
    log["data.blazemeter.com"] = ["POST https://data.blazemeter.com/api/v4/taurus/r-v4-x"]
    assert livetest.engine_proxy_evidence(before) == []


def test_point_test_at_location_returns_original(monkeypatch):
    """The rig must be able to put the customer's test back the way it was."""
    from bzm_opl_gen import api
    original = {"executions": [{"concurrency": 5, "locations": {"us-east4-a": 5},
                                "executor": "jmeter"}],
                "overrideExecutions": [{"locations": {"us-east4-a": 5}}]}
    sent = {}
    c = api.BzmClient.__new__(api.BzmClient)
    monkeypatch.setattr(api.BzmClient, "test", lambda self, tid: dict(original))
    monkeypatch.setattr(api.BzmClient, "update_test",
                        lambda self, tid, body: sent.update(body))
    before = c.point_test_at_location(15783207, "abc123", concurrency=1)
    assert before == original                       # caller can restore verbatim
    ex = sent["executions"][0]
    assert ex["locations"] == {"harbor-abc123": 1} and ex["concurrency"] == 1
    assert ex["executor"] == "jmeter"               # untouched fields survive
    assert sent["overrideExecutions"][0]["locations"] == {"harbor-abc123": 1}


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
    assert json.loads(again[gen.PROFILE_FILE]) == prof
