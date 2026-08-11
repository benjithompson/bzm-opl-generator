"""Offline tests for the local proxy/CA rig: the option overlay livetest builds
and the profile.json round-trip it replays through."""

import json
import os
import subprocess
import sys
import time

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import generate as gen, livetest  # noqa: E402
from tests.test_generate import FACTS  # noqa: E402
from tests.tls_fixtures import SV_CERT, SV_HOST, SV_KEY  # noqa: E402

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


def test_the_rig_mirrors_where_the_bundle_it_deploys_will_look(monkeypatch):
    """#234's other half. The rig had its own copy of the destination rule, so
    it pushed the engine where the bundle's IMAGE_OVERRIDES pointed and crane
    asked somewhere else -- which is how `--local-registry` passed for months
    while proving only that the agent comes online.

    The registry differs by host on purpose: the rig pushes to `localhost:<port>`
    and the node reaches the same registry through `host.minikube.internal`. So
    the paths below the two are what must agree, and they are compared here off
    a real bundle rather than restated.
    """
    port = 5001
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    livetest.mirror_images(FACTS, port)
    pushed = {c[2] for c in cmds if c[:2] == ["docker", "push"]}

    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "private_registry": f"host.minikube.internal:{port}"})
    overrides = json.loads(yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    wanted = {r.split("/", 1)[1] for r in overrides.values()}
    wanted.add(gen._crane_image(FACTS, {
        "private_registry": f"host.minikube.internal:{port}"}).split("/", 1)[1])
    assert {p.split("/", 1)[1] for p in pushed} == wanted


def _live(monkeypatch, cm_data, images=(), ca_certs="2"):
    """Stand in for a deployed cluster: ConfigMap contents, running images, and
    what the crane pod sees at the CA path."""
    monkeypatch.setattr(livetest, "kget", lambda *a, **k: {"data": cm_data})
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


def test_live_config_judges_autoupdate_against_the_option(monkeypatch):
    """The check is what the options asked for, not a flat "false with a
    registry": a bundle that deliberately asked for auto-update is correct with
    the cluster saying true, and one on the default -- off -- is wrong if the
    cluster says true, whether or not a registry is involved."""
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


@pytest.mark.parametrize("mode", sorted(gen.CA_MODES))
def test_engine_config_checks_the_ca_whichever_mode_configured_it(mode):
    """The gate read two of the four modes, so a slot bundle (or an
    OpenShift-injected one) run with `--run-test` and no `--local-proxy` skipped
    both CA assertions and the rig reported a pass having checked neither. The
    proxy path hides it -- proxy_overlay always writes `ca_bundle` -- which is
    why this is read off CA_MODES rather than off the run the rig usually
    makes."""
    value = True if gen.DEFAULT_OPTIONS[mode] is False else "x"
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
    """A 2 CPU / 8Gi bundle whose engine comes back at crane's default means the
    ConfigMap never reached the engine -- indistinguishable from a good run
    until someone reads the numbers it produced."""
    pod = _placed_pod(limits={"cpu": "1", "memory": "4Gi"})
    fails = livetest.assert_engine_size(pod, SPLIT_OPTS)
    assert any("CPU limit of 1" in f for f in fails)
    assert any("memory limit of 4Gi" in f for f in fails)


def test_engine_landed_on_the_pool_it_was_aimed_at():
    assert livetest.assert_engine_pool(_placed_pod(), _node(), SPLIT_OPTS) == []


def test_engine_on_the_wrong_pool_is_a_failure():
    """The one part of the two-pool shape only a spawned engine can confirm:
    the manifests carry the selector and the node carries the labels, but
    whether crane joined them up is invisible until it does."""
    wrong = _node("c1", labels={"pool": "crane"})
    fails = livetest.assert_engine_pool(_placed_pod(node="c1"), wrong, SPLIT_OPTS)
    assert any("does not carry the engine pool's labels" in f for f in fails)


def test_unread_node_is_not_a_wrong_pool():
    """Unreadable and mismatched are different findings and only one is a
    failure -- a livetest without node read access must not invent a placement
    error."""
    assert livetest.assert_engine_pool(_placed_pod(), None, SPLIT_OPTS) == []


def test_engine_pool_is_not_asserted_on_a_one_pool_bundle():
    """Nothing was aimed anywhere, so there is nothing to be wrong about."""
    assert livetest.assert_engine_pool(
        _placed_pod(), _node("n1", labels={}), {"node_selector": {"pool": "x"}}) == []


# -- the engine finished, versus the run being reported as finished -----------
#
# Every fixture below is a real master from a memory bisection: the engine was
# starved by degrees and each run reached ENDED with ZERO failures. The starved
# ones report better latency than the healthy one, because an engine that dies
# early only samples the gentle part of the ramp.

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
    """Master 82869312: 2560MB, 31,130 samples, 0 failures, 322ms -- and exit 1
    halfway through. ENDED and the summary both say it was fine."""
    fails = livetest.assert_engine_exited_cleanly(_EventClient(DIED), 1)
    assert any("exited 1" in f for f in fails)
    assert any("reported as ENDED" in f for f in fails)


def test_a_starved_run_looks_healthier_than_a_good_one():
    """The reason the summary cannot be the criterion. Both of these pass
    assert_engine_did_work; only the exit code separates them."""
    starved = _EventClient(DIED, {"summary": [{"hits": 31130, "avg": 322, "failed": 0}]})
    healthy = _EventClient(CLEAN, {"summary": [{"hits": 61348, "avg": 322, "failed": 21}]})
    assert livetest.assert_engine_did_work(starved, 1) == []      # looks fine
    assert livetest.assert_engine_did_work(healthy, 1) == []      # also fine
    assert livetest.assert_engine_exited_cleanly(starved, 1) != []   # only this differs
    assert livetest.assert_engine_exited_cleanly(healthy, 1) == []


def test_a_missing_exit_status_is_unverified_not_passed():
    """Absent is not zero -- an aged-out master or an unrecognised shape must
    not read as a clean finish."""
    fails = livetest.assert_engine_exited_cleanly(_EventClient(["Status changed to ENDED (140)"]), 1)
    assert any("unverified" in f for f in fails)


def _heap_pod(xmx, limit="8Gi", where="env"):
    """An engine started with a heap. Crane does not write -Xmx -- it is a
    location setting pushed by BlazeMeter -- so where it lands is not ours to
    choose, and all three carriers are searched."""
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
    """None means "not found here", never "the default" -- an engine whose heap
    could not be read must not be reported as one that fits."""
    assert livetest.engine_heap_bytes(_heap_pod(None)) is None
    assert "unread" in livetest.engine_heap_note(_heap_pod(None))


def test_engine_heap_note_flags_a_heap_that_can_fill_the_limit():
    note = livetest.engine_heap_note(_heap_pod("8g", limit="8Gi"))
    assert "OOMKill" in note


def test_engine_heap_note_flags_a_limit_the_jvm_cannot_reach():
    """The default pairing: engineXmx 4096MB against the documented 8Gi limit,
    so every engine pod reserves twice what its JVM can address."""
    note = livetest.engine_heap_note(_heap_pod("4096m", limit="8Gi"))
    assert "reserved and unused" in note and "50%" in note


def test_engine_heap_note_is_quiet_when_the_pairing_is_sane():
    note = livetest.engine_heap_note(_heap_pod("6g", limit="8Gi"))
    assert "OOMKill" not in note and "unused" not in note
    assert "6Gi" in note and "8Gi" in note


def _sized_pod(requests, limits, annotations=None):
    """A real engine pod, sized as crane sizes it."""
    return _engine_pod(resources={"requests": requests, "limits": limits},
                       annotations=annotations)


def test_engine_request_gap_is_what_a_real_run_returns():
    """Observed on a live run: limits 1/4Gi from our envs, requests 250m/256Mi
    from crane -- and no limit-ranger annotation, so the LimitRange we emit did
    not touch the pod and could not have."""
    pod = _sized_pod({"cpu": "250m", "memory": "256Mi"},
                     {"cpu": "1", "memory": "4Gi"})
    gap = livetest.engine_request_gap(pod)
    assert "250m" in gap and "cpu" in gap and "memory" in gap
    assert "cannot change them" in gap


def test_engine_request_gap_silent_when_requests_match_limits():
    assert livetest.engine_request_gap(
        _sized_pod({"cpu": "1", "memory": "4Gi"}, {"cpu": "1", "memory": "4Gi"})) is None


def test_engine_request_gap_notes_when_a_limitrange_did_act():
    """Crane's test-job pods declare nothing, so the LimitRanger does fill them
    in and stamps the annotation -- don't blame the LimitRange then."""
    pod = _sized_pod({"cpu": "250m", "memory": "256Mi"}, {"cpu": "1", "memory": "4Gi"},
                     annotations={livetest.LIMIT_RANGER_ANNOTATION: "set: cpu request"})
    assert "cannot change them" not in livetest.engine_request_gap(pod)


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
    """A taurus-script test keeps its locations in the YAML; patching executions
    is silently ignored, so the rig must not claim it repointed anything."""
    from bzm_opl_gen import api
    patched = []
    monkeypatch.setattr(api.BzmClient, "test", lambda self, tid: {"executions": None})
    monkeypatch.setattr(api.BzmClient, "update_test",
                        lambda self, tid, body: patched.append(body))
    c = api.BzmClient.__new__(api.BzmClient)
    assert c.point_test_at_location(1, "abc") is None
    assert patched == []


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
    before = c.point_test_at_location(10000001, "abc123", concurrency=1)
    assert before == original                       # caller can restore verbatim
    ex = sent["executions"][0]
    assert ex["locations"] == {"harbor-abc123": 1} and ex["concurrency"] == 1
    assert ex["executor"] == "jmeter"               # untouched fields survive
    assert sent["overrideExecutions"][0]["locations"] == {"harbor-abc123": 1}


# -- the directory is the bundle under test -----------------------------------
#
# #107: a run given --ship-id and --auth-token deployed a nine-day-old bundle
# for a *different* agent, because --manifests defaults to out/ and out/ is
# whatever the last `generate` left there. Crane came up with an identity it
# could not register, the rollout timed out saying only that, and the rig then
# deleted the cluster. Every fact needed to refuse it was in two files that had
# been sitting on disk the whole time.

def _bundle(tmp_path, facts=FACTS, **opts):
    gen.write(gen.generate(facts, {"namespace": "ns1", "auth_token": "tok",
                                   **opts}), str(tmp_path))
    return str(tmp_path)


def test_bundle_check_passes_this_generators_own_output(tmp_path):
    d = _bundle(tmp_path)
    check = livetest.bundle_check(d, "aaa111", "bbb222",
                                  gen.load_profile(d))
    assert check == livetest.BundleCheck([], [])


def test_bundle_check_names_the_ship_on_disk_and_the_ship_asked_for(tmp_path):
    """The reported case. The refusal has to carry both ids: the operator typed
    one of them and has no idea the other is in the directory."""
    d = _bundle(tmp_path)
    refusals = livetest.bundle_check(d, "aaa111", "ccc333").refusals
    assert refusals and all("bbb222" in r and "ccc333" in r for r in refusals)
    assert any(livetest.CONFIGMAP_FILE in r for r in refusals)


def test_bundle_check_names_the_harbor_on_disk_and_the_harbor_asked_for(tmp_path):
    """The other half of the identity, and the one that changes when the
    directory was generated for a different account entirely."""
    d = _bundle(tmp_path)
    r = " ".join(livetest.bundle_check(d, "zzz999", "bbb222").refusals)
    assert "aaa111" in r and "zzz999" in r


def test_bundle_check_catches_a_stale_ship_in_the_profile(tmp_path):
    """profile.json is what the re-rendering paths merge their overlay onto, and
    _regenerator prefers the ship_id it finds there -- so a stale one deploys the
    wrong agent even on a path that does re-render."""
    d = _bundle(tmp_path)
    prof = {**gen.load_profile(d), "ship_id": "ddd444"}
    r = " ".join(livetest.bundle_check(d, "aaa111", "bbb222", prof).refusals)
    assert "ddd444" in r and "bbb222" in r and gen.PROFILE_FILE in r


def test_bundle_check_refuses_a_bundle_with_a_field_left_blank(tmp_path):
    """The API server would refuse `<SERVICE_ACCOUNT_NAME>` as a name anyway --
    but only
    after this rig has built a cluster, which is 12-20 minutes and a teardown to
    learn something profile.json says on disk. Same shape as the identity
    guards above, and the same reason for being one."""
    d = _bundle(tmp_path, service_account_name="")
    prof = gen.load_profile(d)
    assert prof["service_account_name"] == gen.marker("service_account_name")
    r = " ".join(livetest.bundle_check(d, "aaa111", "bbb222", prof).refusals)
    # Both halves: the field somebody has to fill in, and the string they will
    # find in the bundle when they go looking.
    assert "service_account_name" in r
    assert gen.marker("service_account_name") in r


def test_bundle_check_refuses_a_yaml_this_generator_does_not_emit(tmp_path):
    """bzm_limitrange.yaml is the file that happened: emitted by a version that
    is gone, left behind in out/, and applied by the rig as part of the run."""
    d = _bundle(tmp_path)
    open(os.path.join(d, "bzm_limitrange.yaml"), "w").write("kind: LimitRange\n")
    r = " ".join(livetest.bundle_check(d, "aaa111", "bbb222").refusals)
    assert "bzm_limitrange.yaml" in r


def test_every_yaml_this_generator_emits_is_one_the_rig_expects():
    """The drift guard on the refusal above. A new manifest file that
    emitted_yaml_files() had to be told about separately would be refused as an
    older version's leftover -- a good bundle, rejected, with a message pointing
    at the wrong thing. The option matrix is helm_parity's, imported rather than
    restated: it is the one place in this suite that already enumerates every
    combination that changes which files come out."""
    import helm_parity                                  # no helm binary needed
    for name, extra in helm_parity.CASES.items():
        files = gen.generate(FACTS, {**helm_parity.COMMON, **extra})
        unknown = [f for f in files if f.endswith(".yaml")
                   and f not in livetest.emitted_yaml_files()]
        assert not unknown, f"case {name} emits {unknown}"


def test_bundle_check_judges_exactly_what_deploy_would_apply(tmp_path):
    """Same glob as deploy(), so there is nothing it applies that this did not
    look at -- and nothing it refuses that would never have been applied. The
    rig's own .egress-policy.yaml is dot-prefixed for that reason; a README or a
    mirror script is not applied either."""
    d = _bundle(tmp_path)
    open(os.path.join(d, ".egress-policy.yaml"), "w").write("kind: NetworkPolicy\n")
    open(os.path.join(d, "notes.txt"), "w").write("scratch\n")
    assert livetest.bundle_check(d, "aaa111", "bbb222").refusals == []


def test_bundle_check_does_not_invent_an_identity_it_could_not_read(tmp_path):
    """A directory with no ConfigMap of ours says nothing about which agent it
    is for, which is not the same as saying the wrong one. It is a note, and the
    run goes ahead -- unreadable and mismatched must not share a
    representation."""
    check = livetest.bundle_check(str(tmp_path), "aaa111", "bbb222")
    assert check.refusals == []
    assert any("aaa111" in n and "bbb222" in n for n in check.notes)
    assert livetest.manifest_identity(str(tmp_path)) is None


def test_bundle_check_tells_an_unread_configmap_from_one_that_names_nothing(
        tmp_path):
    """The other half of the same rule, and the one the shape above could not
    express: a ConfigMap that is *there* and carries neither id is a file
    somebody read, and its note says so rather than "could not be read". Both
    are notes and neither is a refusal -- what must not happen is the two
    arriving as one sentence."""
    d = _bundle(tmp_path)
    open(os.path.join(d, livetest.CONFIGMAP_FILE), "w").write(
        "kind: ConfigMap\ndata: {}\n")
    assert livetest.manifest_identity(d) == {}          # read, and names none
    notes = livetest.bundle_check(d, "aaa111", "bbb222").notes
    assert any("carries no HARBOR_ID/SHIP_ID" in n for n in notes)
    assert not any("could not be read" in n for n in notes)


def test_run_refuses_before_it_builds_anything(monkeypatch, tmp_path):
    """Building a cluster and waiting out a 300s rollout is the expensive part of
    this failure; the check is a file read. In run() as well as in the CLI
    because the MCP server deploys through run() directly."""
    d = _bundle(tmp_path)
    for name in ("ensure_cluster", "deploy", "teardown", "ensure_registry"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a bundle built for another agent"))
    with pytest.raises(livetest.BundleMismatch) as caught:
        livetest.run(None, d, "ns1", "aaa111", "ccc333", cluster="minikube")
    assert "bbb222" in str(caught.value) and "ccc333" in str(caught.value)


# -- the compose rig ----------------------------------------------------------
#
# The offline counterpart to `livetest` on a docker bundle: the daemon is a
# recorded command list and BlazeMeter is a dict. Every live check has one of
# these, and this rig is the *only* live proof --format docker has, so its
# guards are the half that has to hold without a daemon at all.

def _docker_bundle(tmp_path, facts=FACTS, **opts):
    gen.write(gen.generate(facts, {"output_format": "docker",
                                   "ship_id": "bbb222", "auth_token": "tok",
                                   **opts}), str(tmp_path))
    return str(tmp_path)


class _FakeBzm:
    """BlazeMeter, as wait_online asks it: one location, one ship, heartbeating
    now. `state` is what makes the difference between online and not."""

    def __init__(self, state="idle", ship="bbb222"):
        self.state, self.ship, self.calls = state, ship, 0

    def private_location(self, harbor_id):
        self.calls += 1
        return {"ships": [{"id": self.ship, "state": self.state,
                           "lastHeartBeat": time.time()}]}


def _fake_daemon(monkeypatch, *, present=(), compose=True):
    """Stand in for the docker daemon. Returns the recorded command list.

    `present` is what `docker ps -aq` reports still existing, which is how the
    teardown's own check is driven; `compose=False` is a host with no Compose
    v2 plugin, which raises exactly as subprocess does.
    """
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

    monkeypatch.setattr(livetest.subprocess, "run", run)
    return cmds


def test_bundle_platform_reads_the_profile(tmp_path):
    """The bundle already knows what it is, and profile.json is where it says
    so -- no flag, so there is no second place to get it wrong."""
    assert livetest.bundle_platform(
        str(tmp_path), {"output_format": "docker"}) == livetest.PLATFORM_COMPOSE
    assert livetest.bundle_platform(
        str(tmp_path), {"output_format": "manifests"}) == livetest.PLATFORM_MANIFESTS


def test_bundle_platform_reads_the_directory_when_there_is_no_profile(tmp_path):
    """A directory with no profile still says which it is: nothing but a docker
    bundle carries a compose file. The wrong answer here is the silent run this
    whole check exists to prevent, so it is read rather than defaulted."""
    d = _docker_bundle(tmp_path)
    os.remove(os.path.join(d, gen.PROFILE_FILE))
    assert livetest.bundle_platform(d) == livetest.PLATFORM_COMPOSE
    assert livetest.bundle_platform(str(tmp_path / "empty")) == \
        livetest.PLATFORM_MANIFESTS


def test_bundle_platform_does_not_let_a_stray_compose_file_win(tmp_path):
    """A manifests bundle with a compose file in it is a directory holding two
    versions' output, and the manifests branch already names it -- as an
    unknown *.yaml, which is the refusal that fits. Routing it to compose
    instead would deploy one file out of a bundle of nine."""
    d = _bundle(tmp_path)
    open(os.path.join(d, gen.DOCKER_COMPOSE_FILE), "w").write("services: {}\n")
    prof = gen.load_profile(d)
    assert livetest.bundle_platform(d, prof) == livetest.PLATFORM_MANIFESTS
    refusals = livetest.bundle_check(d, "aaa111", "bbb222", prof).refusals
    assert any(gen.DOCKER_COMPOSE_FILE in r for r in refusals)


def test_compose_bundle_check_passes_this_generators_own_output(tmp_path):
    d = _docker_bundle(tmp_path)
    check = livetest.bundle_check(d, "aaa111", "bbb222", gen.load_profile(d))
    assert check == livetest.BundleCheck([], [])


def test_compose_bundle_check_refuses_another_agents_container(tmp_path):
    """#107 on the other platform. The container name carries the ship id, so a
    directory left over from a different agent is refused before anything is
    started -- otherwise crane comes up with an identity BlazeMeter will not
    register and the run waits out its whole timeout saying only that."""
    d = _docker_bundle(tmp_path)
    refusals = livetest.bundle_check(d, "aaa111", "ccc333").refusals
    assert any(gen.docker_container_name("bbb222") in r
               and gen.docker_container_name("ccc333") in r for r in refusals)


def test_compose_bundle_check_refuses_another_locations_harbor(tmp_path):
    d = _docker_bundle(tmp_path)
    refusals = livetest.bundle_check(d, "zzz999", "bbb222").refusals
    assert any("HARBOR_ID" in r and "aaa111" in r and "zzz999" in r
               for r in refusals)


def test_compose_bundle_check_refuses_a_directory_with_no_compose_file(tmp_path):
    """Reachable one way: profile.json says docker and the compose file is gone
    -- a bundle from before #177, or one somebody tidied. There is nothing for
    `compose up` to start, and it would say so several layers into a run."""
    d = _docker_bundle(tmp_path)
    os.remove(os.path.join(d, gen.DOCKER_COMPOSE_FILE))
    refusals = livetest.bundle_check(d, "aaa111", "bbb222",
                                     gen.load_profile(d)).refusals
    assert any(gen.DOCKER_COMPOSE_FILE in r for r in refusals)


def test_compose_bundle_check_refuses_a_bundle_nobody_finished(tmp_path):
    """The credential is the value most often left blank and the one
    profile.json can never carry, so this is read off the files. `compose up`
    refuses it too -- but as a non-zero exit partway through a run, with a
    container already created against a real account."""
    d = _docker_bundle(tmp_path, auth_token=None)
    refusals = livetest.bundle_check(d, "aaa111", "bbb222",
                                     gen.load_profile(d)).refusals
    assert any("AUTH_TOKEN" in r for r in refusals)


def test_compose_bundle_check_notes_an_identity_it_could_not_read(tmp_path):
    """Unreadable and mismatched must not share a representation here either: a
    hand-assembled compose file that names no container is a note, and the run
    goes ahead."""
    d = str(tmp_path)
    open(os.path.join(d, gen.DOCKER_COMPOSE_FILE), "w").write(
        "services:\n  crane:\n    image: x\n")
    assert livetest.compose_identity(d) == {}           # read, and names none
    check = livetest.bundle_check(d, "aaa111", "bbb222")
    assert check.refusals == []
    assert any("container_name" in n for n in check.notes)
    assert any("HARBOR_ID" in n for n in check.notes)
    # ...and it was *read*. The state below is the other one.
    assert not any("could not be read" in n for n in check.notes)


def test_compose_bundle_check_tells_an_unread_file_from_one_that_names_nothing(
        tmp_path):
    """The second of _file_text's three answers, which the check above cannot
    reach: a compose file that is there and that nothing could read. It named no
    container either, and for the opposite reason -- so it gets one note saying
    so, and not the three per-field ones, which are claims about a file somebody
    read.

    Undecodable bytes rather than a chmod: it is a state every uid sees the same
    way (a root CI runner reads a 0o000 file happily), and it is a real one --
    a DER file saved over a PEM is exactly what the certificate check next door
    exists to catch."""
    d = str(tmp_path)
    open(os.path.join(d, gen.DOCKER_COMPOSE_FILE), "wb").write(b"\xff\xfe\x00x")
    assert livetest.compose_identity(d) is None         # not {}: nobody read it
    check = livetest.bundle_check(d, "aaa111", "bbb222")
    assert check.refusals == []
    assert len([n for n in check.notes if "could not be read" in n]) == 1
    assert not any("names no" in n for n in check.notes)


# -- a required value written as a file, not as a variable --------------------
#
# The guard #183 built reads the rendered *environment*, and #182 then added two
# options the bundle writes as files: the marker lands in sv-tls.key's bytes,
# where TLS_KEY beside it holds a container path that was never blank. So both
# of the things this rig already read said the bundle was finished --
# compose_unset sees no ${BZM_OPL_UNSET_...}, and placeholder_options cannot see
# sv_tls_key at all, because SECRET_OPTIONS keeps it out of profile.json.

def _sv_docker_bundle(tmp_path, **opts):
    return _docker_bundle(tmp_path, **{"sv_hostname": SV_HOST,
                                       "sv_tls_cert": SV_CERT,
                                       "sv_tls_key": SV_KEY, **opts})


def test_compose_bundle_check_refuses_a_mounted_file_left_blank(tmp_path):
    """The finding. A blank private key gives a bundle whose compose file drops
    the mount's default to `${SV_TLS_KEY:?...}` -- so `compose up` refuses it,
    but only after this run has started building against a real account."""
    d = _sv_docker_bundle(tmp_path, sv_tls_key="")
    prof = gen.load_profile(d)
    # Neither of the two things this check already read can see it.
    assert gen.placeholder_options(prof) == [] and livetest.compose_unset(d) == []
    refusals = livetest.bundle_check(d, "aaa111", "bbb222", prof).refusals
    assert any(gen.DOCKER_SV_KEY_FILE in r
               and gen.marker("sv_tls_key") in r
               and "sv_tls_key" in r for r in refusals)


def test_compose_bundle_check_passes_a_bundle_whose_files_are_filled_in(tmp_path):
    """The other half, and the one that makes the refusal above worth having:
    the same bundle with the pair supplied is silent."""
    d = _sv_docker_bundle(tmp_path)
    assert livetest.bundle_check(d, "aaa111", "bbb222",
                                 gen.load_profile(d)) == livetest.BundleCheck([], [])


def test_compose_bundle_check_takes_the_escape_hatch_the_bundle_offers(
        tmp_path, monkeypatch):
    """A guard that survives its own fix is the failure `_compose_required_file`
    rules out, so this reads the file the container would actually mount. Set
    SV_TLS_KEY and compose mounts that instead -- the marker in the bundle's own
    copy reaches nothing, and the run is one this rig has no business refusing.
    """
    d = _sv_docker_bundle(tmp_path, sv_tls_key="")
    real = tmp_path / "keys" / "host.key"
    real.parent.mkdir()
    real.write_text(SV_KEY)
    monkeypatch.setenv("SV_TLS_KEY", str(real))
    assert livetest.bundle_check(d, "aaa111", "bbb222",
                                 gen.load_profile(d)).refusals == []


def test_compose_bundle_check_notes_a_mounted_file_it_could_not_read(tmp_path):
    """Same rule, one layer down: a mounted file nothing could read says nothing
    about whether it carries the marker, and a run refused on that would be
    "could not read" wearing "there is nothing there"."""
    d = _docker_bundle(tmp_path, ca_bundle=CA_PEM)
    open(os.path.join(d, gen.DOCKER_CA_FILE), "wb").write(b"\xff\xfe\x00x")
    check = livetest.bundle_check(d, "aaa111", "bbb222", gen.load_profile(d))
    assert check.refusals == []
    assert any(gen.DOCKER_CA_FILE in n and "could not be read" in n
               for n in check.notes)


def test_run_compose_refuses_a_blank_mounted_file_before_it_starts_anything(
        monkeypatch, tmp_path):
    """The ordering that makes the whole check worth making: nothing is started,
    and on this platform starting something is a container registering against a
    real account."""
    d = _sv_docker_bundle(tmp_path, sv_tls_key="")
    for name in ("compose_up", "compose_down", "compose_tool"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a bundle with a blank {gen.DOCKER_SV_KEY_FILE}"))
    with pytest.raises(livetest.BundleMismatch) as caught:
        livetest.run_compose(None, d, "aaa111", "bbb222",
                             opts=gen.load_profile(d))
    assert gen.DOCKER_SV_KEY_FILE in str(caught.value)


def test_bundle_check_reports_its_notes_and_hands_back_its_refusals(capsys):
    """One method, four call sites, and the disposition is not in it: the CLI
    exits with what this returns and livetest raises BundleMismatch with it,
    which the MCP server depends on telling apart from an agent that never came
    online."""
    assert livetest.BundleCheck([], []).report() is None
    assert livetest.BundleCheck([], ["only a note"]).report() is None
    assert capsys.readouterr().out == "note: only a note\n"
    assert livetest.BundleCheck(["a", "b"], []).report() == "a\nb"


def test_run_compose_refuses_before_it_starts_anything(monkeypatch, tmp_path):
    """The spirit of test_run_refuses_before_it_builds_anything, on the platform
    where the cost is not a cluster build but a container registering against a
    real account under somebody else's identity."""
    d = _docker_bundle(tmp_path)
    for name in ("compose_up", "compose_down", "compose_tool"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a bundle built for another agent"))
    with pytest.raises(livetest.BundleMismatch) as caught:
        livetest.run_compose(None, d, "aaa111", "ccc333")
    assert gen.docker_container_name("ccc333") in str(caught.value)


def test_run_refuses_a_compose_bundle_rather_than_deploying_nothing(
        monkeypatch, tmp_path):
    """run() is the cluster rig, and the MCP server calls it directly. Without
    this the *.yaml glob comes back empty, every object "applies", nothing is
    created, and the run waits out its whole timeout."""
    d = _docker_bundle(tmp_path)
    for name in ("ensure_cluster", "deploy", "teardown"):
        monkeypatch.setattr(livetest, name, lambda *a, **kw: pytest.fail(
            f"{name} ran on a docker bundle"))
    with pytest.raises(livetest.BundleMismatch) as caught:
        livetest.run(None, d, "ns1", "aaa111", "bbb222",
                     opts=gen.load_profile(d), cluster="minikube")
    assert "docker" in str(caught.value) and "livetest" in str(caught.value)


def test_run_compose_brings_it_up_waits_and_takes_it_down(monkeypatch, tmp_path):
    """Up, online, down -- the whole of what this rig is. The daemon is faked
    and BlazeMeter is a dict; what is asserted is the three commands and that
    the answer comes from the account rather than from the daemon."""
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
    """A container that is up is not an agent that is online -- the claim is
    BlazeMeter's, exactly as it is on the cluster path. And a failure prints the
    container's own log, because `up -d` returns before crane says anything and
    a crash-looper is otherwise indistinguishable from a slow boot."""
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch)
    assert livetest.run_compose(_FakeBzm(state="offline"), d, "aaa111",
                               "bbb222", timeout=0) is False
    assert any(c[4:6] == ["logs", "--tail"] for c in cmds)


def test_run_compose_takes_it_down_even_when_the_wait_raises(monkeypatch,
                                                             tmp_path):
    """The finally is the whole of the promise that the daemon is left as it was
    found."""
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
    """`down` is a no-op for a container whose compose file has been rewritten
    out from under it, and the leftover then holds the name the next run needs.
    Named leftovers are the one thing this rig promises not to leave."""
    d = _docker_bundle(tmp_path)
    name = gen.docker_container_name("bbb222")
    cmds = _fake_daemon(monkeypatch, present=[name])
    livetest.compose_down(d, name)
    assert ["docker", "rm", "-f", name] in cmds


def test_teardown_removes_nothing_by_name_when_down_worked(monkeypatch,
                                                           tmp_path):
    d = _docker_bundle(tmp_path)
    cmds = _fake_daemon(monkeypatch, present=[])
    livetest.compose_down(d, gen.docker_container_name("bbb222"))
    assert not any(c[:3] == ["docker", "rm", "-f"] for c in cmds)


def test_compose_tool_says_so_when_the_plugin_is_missing(monkeypatch):
    """`docker-compose` (the v1 script) is a different command with a different
    file precedence, and a host with neither cannot start anything -- so it is
    one sentence up front rather than a failure partway in."""
    _fake_daemon(monkeypatch, compose=False)
    with pytest.raises(RuntimeError) as caught:
        livetest.compose_tool()
    assert "Compose v2" in str(caught.value)


def test_compose_file_relative_paths_resolve_against_the_bundle(monkeypatch,
                                                                tmp_path):
    """-f rather than a cwd change, and the bundle's own directory is what
    compose resolves `env_file:` and every relative bind against."""
    d = _docker_bundle(tmp_path, ca_bundle=CA_PEM)
    cmds = _fake_daemon(monkeypatch)
    livetest.run_compose(_FakeBzm(), d, "aaa111", "bbb222")
    up = next(c for c in cmds if c[-2:] == ["up", "-d"])
    assert up[2] == "-f" and up[3] == os.path.join(d, gen.DOCKER_COMPOSE_FILE)


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


# -- sv_mocks ---------------------------------------------------------------

def _sv_pod(name, port, harbor="h1", ship="s1", extra=None):
    labels = {gen.SV_POD_NAME_LABEL: name, gen.SV_POD_HARBOR_LABEL: harbor,
              gen.SV_POD_SHIP_LABEL: ship} if name else {}
    return {"metadata": {"labels": {**labels, **(extra or {})}},
            "spec": {"containers": [
                {"ports": [{"containerPort": port}] if port else []}]}}


def _pods(monkeypatch, items):
    monkeypatch.setattr(livetest, "kget",
                        lambda cli, ns, kind, name=None: {"items": items})


def test_sv_mocks_reads_identity_and_port_off_the_pods(monkeypatch):
    """The offline counterpart to reading a live namespace: harbor and ship come
    from the pod labels because profile.json carries neither."""
    _pods(monkeypatch, [_sv_pod("vs2", 8080, "hA", "sA"),
                        _sv_pod("vs1", 9000, "hA", "sA")])
    assert livetest.sv_mocks("kubectl", "ns1") == [
        {"name": "vs1", "port": 9000, "harbor": "hA", "ship": "sA"},
        {"name": "vs2", "port": 8080, "harbor": "hA", "ship": "sA"},
    ]


def test_sv_mocks_ignores_pods_that_are_not_virtual_services(monkeypatch):
    """crane itself, engines and test jobs share the namespace and carry no
    BZM_CONTAINER_NAME label."""
    _pods(monkeypatch, [_sv_pod(None, 5000, extra={"role": "role-crane"}),
                        _sv_pod("vs1", 8080)])
    assert [m["name"] for m in livetest.sv_mocks("kubectl", "ns1")] == ["vs1"]


def test_sv_mocks_dedupes_a_mid_rollout_namespace(monkeypatch):
    """Two pods for one mock while a redeploy rolls; emitting two Services with
    the same selector would leave a duplicate object behind."""
    _pods(monkeypatch, [_sv_pod("vs1", 8080), _sv_pod("vs1", 8080)])
    assert len(livetest.sv_mocks("kubectl", "ns1")) == 1


def test_sv_mocks_skips_a_labelled_pod_with_no_container_port(monkeypatch):
    """Without a port there is nothing to point a Service at -- the pair would
    be unroutable rather than merely wrong."""
    _pods(monkeypatch, [_sv_pod("vs1", None)])
    assert livetest.sv_mocks("kubectl", "ns1") == []


def test_sv_mocks_is_empty_when_the_namespace_cannot_be_read(monkeypatch):
    """kget reports a failed get as {} -- sv-expose then exits telling the user
    to deploy first, rather than writing an empty manifest."""
    monkeypatch.setattr(livetest, "kget", lambda *a, **k: {})
    assert livetest.sv_mocks("kubectl", "ns1") == []


# -- sv_read: the same namespace, plus why it could not be read ---------------
# sv_mocks() reports every failure as "no mocks", which is enough for its
# callers: they are mid-livetest, where a cluster is a precondition. The UI
# calls sv_read instead -- plenty of people running it have no kubecontext at
# all, and have to be told which of the reasons applied.

def _fake_kubectl(monkeypatch, *, tools=("kubectl",), rc=0, stdout="", stderr=""):
    """Stand in for the kubectl/oc binary. `tools` is what exists on PATH --
    anything else raises FileNotFoundError, exactly as subprocess does."""
    livetest.cli_tool.cache_clear()

    def run(cmd, *a, **kw):
        if cmd[0] not in tools:
            raise FileNotFoundError(cmd[0])
        if cmd[1:3] == ["version", "--client"]:
            return subprocess.CompletedProcess(cmd, 0, "v1.32.0", "")
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    monkeypatch.setattr(livetest.subprocess, "run", run)


@pytest.fixture(autouse=True)
def _uncached_cli_tool():
    """cli_tool() memoises which binary is on PATH for the life of the process;
    a test that fakes PATH must not leak that answer into the next one."""
    livetest.cli_tool.cache_clear()
    yield
    livetest.cli_tool.cache_clear()


def test_sv_read_returns_the_mocks_when_the_namespace_can_be_read(monkeypatch):
    _fake_kubectl(monkeypatch, stdout=json.dumps(
        {"items": [_sv_pod("vs1", 8080, "hA", "sA")]}))
    read = livetest.sv_read("ns1")
    assert read.status == livetest.SV_READ_OK
    assert read.mocks == [{"name": "vs1", "port": 8080, "harbor": "hA",
                           "ship": "sA"}]


def test_sv_read_reports_a_missing_cli_rather_than_an_empty_namespace(monkeypatch):
    _fake_kubectl(monkeypatch, tools=())
    read = livetest.sv_read("ns1")
    assert read.status == livetest.SV_READ_NO_CLI
    assert read.mocks == [] and "kubectl" in read.detail


@pytest.mark.parametrize("stderr, status", [
    # kubectl with no kubeconfig at all, and with one whose server is gone.
    ("The connection to the server localhost:8080 was refused - did you "
     "specify the right host or port?", livetest.SV_READ_NO_CONTEXT),
    ("error: current-context is not set", livetest.SV_READ_NO_CONTEXT),
    ("Unable to connect to the server: dial tcp: lookup api.example.com: "
     "no such host", livetest.SV_READ_NO_CONTEXT),
    # oc's wording for the same thing.
    ("error: Missing or incomplete configuration info.",
     livetest.SV_READ_NO_CONTEXT),
    # Authenticated but not allowed, and no longer authenticated at all.
    ('Error from server (Forbidden): pods is forbidden: User "dev" cannot '
     'list resource "pods" in API group "" in the namespace "ns1"',
     livetest.SV_READ_DENIED),
    ("error: You must be logged in to the server (Unauthorized)",
     livetest.SV_READ_DENIED),
    # A namespace that is not there answers the same question as an empty one:
    # nothing is deployed to expose.
    ('Error from server (NotFound): namespaces "ns1" not found',
     livetest.SV_READ_NO_MOCKS),
])
def test_sv_read_tells_the_failures_apart_by_what_the_cli_printed(
        monkeypatch, stderr, status):
    _fake_kubectl(monkeypatch, rc=1, stderr=stderr)
    read = livetest.sv_read("ns1")
    assert read.status == status
    assert read.detail == stderr        # the raw message travels with it


def test_sv_read_separates_a_readable_namespace_with_no_virtual_services(monkeypatch):
    """crane and its engines share the namespace and carry none of the mock
    labels, so a healthy agent with nothing deployed reads as no_mocks -- a
    different thing to say than "denied" or "no cluster"."""
    _fake_kubectl(monkeypatch, stdout=json.dumps(
        {"items": [_sv_pod(None, 5000, extra={"role": "role-crane"})]}))
    read = livetest.sv_read("ns1")
    assert read.status == livetest.SV_READ_NO_MOCKS and read.mocks == []


def test_sv_read_survives_a_zero_exit_that_is_not_json(monkeypatch):
    """A wrapper or kubectl plugin that prints to stdout before the JSON leaves
    a successful exit whose output will not parse. sv_read exists to answer a
    browser, which /api/sv-mocks promises never to hand a bare error, so this
    has to come back as an unreadable cluster rather than an exception."""
    _fake_kubectl(monkeypatch, stdout="Kubeconfig user entry is using deprecated API\n")
    read = livetest.sv_read("ns1")
    assert read.status == livetest.SV_READ_NO_CONTEXT
    assert read.mocks == []
    assert "deprecated" in read.detail        # the raw output travels with it


def test_sv_read_does_not_hang_on_an_unreachable_api_server(monkeypatch):
    """kubectl keeps retrying an unreachable server rather than failing, and an
    HTTP request from the browser cannot wait it out."""
    def run(cmd, *a, **kw):
        if cmd[1:3] == ["version", "--client"]:
            return subprocess.CompletedProcess(cmd, 0, "v1.32.0", "")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 15))

    monkeypatch.setattr(livetest.subprocess, "run", run)
    read = livetest.sv_read("ns1", timeout=3)
    assert read.status == livetest.SV_READ_NO_CONTEXT
    assert "3s" in read.detail


# -- whose cluster is it -------------------------------------------------------
#
# The rig reuses a cluster that is already there, and used to delete it either
# way. On this machine the name it reuses (`bzm-opl-test`) is a standing kind
# testbed holding two agents and a serving virtual service (#226). So the
# question teardown has to be able to answer is not "which cluster" but "did
# this run build it".


def _kind_present(monkeypatch, names, cmds):
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, stdout=" ".join(names), stderr=""))
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))


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
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned(cluster=True))
    assert ["kind", "delete", "cluster", "--name", livetest.KIND_CLUSTER] in cmds


def test_teardown_leaves_a_cluster_it_did_not_create(monkeypatch, tmp_path):
    """The whole point of #226: a reused cluster survives, and the run still
    clears the objects it applied into it."""
    (tmp_path / "bzm_deployment.yaml").write_text("kind: Deployment\n")
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned())
    assert not any("delete" in c and "cluster" in c for c in cmds)
    assert any(c[:2] == ["kubectl", "-n"] and "delete" in c for c in cmds)


def test_teardown_leaves_a_minikube_profile_it_did_not_create(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "minikube", livetest.Owned())
    assert not any("minikube" in c for c in cmds)


def test_teardown_defaults_to_leaving_the_cluster_alone(monkeypatch, tmp_path):
    """A run that fell over before ensure_cluster answered knows nothing about
    whose cluster it is, so the default has to be the safe one."""
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "minikube")
    assert not any("minikube" in c for c in cmds)


def _minikube_host(monkeypatch, host, cmds, exists=None):
    """`host` is what `minikube status --format {{.Host}}` prints; `exists`
    defaults to "there is a profile whenever a state was reported"."""
    if exists is None:
        exists = bool(host)
    monkeypatch.setattr(livetest, "minikube_profile_exists", lambda: exists)
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, stdout=host, stderr=""))
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
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
    """Starting somebody's stopped profile is not creating it. It was there
    before the run and has to be there after (#226)."""
    cmds = []
    _minikube_host(monkeypatch, "Stopped", cmds)
    assert livetest.ensure_minikube() is False
    assert any("start" in c for c in cmds)          # still started, just not owned


def test_a_recreated_minikube_profile_is_ours(monkeypatch):
    """--contain-egress deletes a running profile with no policy enforcer. What
    comes back is this run's, and teardown may take it."""
    cmds = []
    _minikube_host(monkeypatch, "Running", cmds)
    monkeypatch.setattr(livetest, "policy_enforced", lambda: False)
    assert livetest.ensure_minikube(cni="calico") is True
    assert any("delete" in c for c in cmds) and any("start" in c for c in cmds)


# -- what a cluster that survives keeps ---------------------------------------
#
# Deleting the cluster used to clean up everything inside it. Once a reused one
# survives, each of those has to be answered for on its own.


def test_a_profile_in_an_unlisted_state_is_not_claimed(monkeypatch):
    """minikube reports Saved/Error/Starting/Stopping/Timeout as well, and a
    state nobody listed must not read as 'no profile, so it is mine'."""
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0,
                            stdout=json.dumps({"valid": [{"Name": livetest.MINIKUBE_PROFILE}],
                                               "invalid": []}), stderr=""))
    assert livetest.minikube_profile_exists() is True


def test_an_absent_profile_is_ours_to_create(monkeypatch):
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 0, stdout='{"valid":[],"invalid":[]}', stderr=""))
    assert livetest.minikube_profile_exists() is False


def test_an_unreadable_profile_list_is_not_ours(monkeypatch, capsys):
    """Could-not-read must not arrive as there-is-nothing-there: one answer
    leaves a profile behind, the other deletes somebody's cluster."""
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(
                            a[0], 1, stdout="minikube: command not found", stderr=""))
    assert livetest.minikube_profile_exists() is True
    assert "could not read" in capsys.readouterr().out


def test_the_profile_state_is_only_read_where_a_profile_exists(monkeypatch):
    """A missing profile makes `status` print a not-found sentence, which is
    not a host state -- reading it as one is how 'Stopped' logic goes wrong."""
    cmds = []
    _minikube_host(monkeypatch, 'Profile "bzm-opl-test" not found.', cmds,
                   exists=False)
    assert livetest.ensure_minikube() is True


def test_policy_is_judged_after_the_context_is_this_profile(monkeypatch):
    """policy_enforced() reads whatever kubectl points at. Asked first it
    answered about the standing kind testbed, and it now decides a delete."""
    cmds, asked_at = [], []
    _minikube_host(monkeypatch, "Running", cmds)
    monkeypatch.setattr(livetest, "policy_enforced",
                        lambda: asked_at.append(len(cmds)) or True)
    livetest.ensure_minikube(cni="calico")
    switched = [i for i, c in enumerate(cmds) if "use-context" in c]
    assert switched and asked_at[0] > switched[0]


def test_teardown_drops_a_namespace_this_run_created(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned(namespace=True))
    assert ["kubectl", "delete", "ns", "ns1", "--ignore-not-found"] in cmds


def test_teardown_removes_the_egress_policy_from_a_namespace_it_kept(monkeypatch, tmp_path):
    """The policy is written to a dotfile so deploy()'s glob skips it, so the
    *.yaml sweep cannot reach it either. Left behind on a surviving cluster it
    denies the next run's egress, which reads as an agent that never came up."""
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned())
    assert any(livetest.EGRESS_POLICY_NAME in c and "delete" in c for c in cmds)


def test_teardown_restores_the_node_hosts_file_on_a_cluster_it_keeps(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "minikube",
                      livetest.Owned(blackholed=["gcr.io"]))
    assert any("/etc/hosts" in str(c) and "gcr.io" in str(c) for c in cmds)


def test_teardown_does_not_bother_restoring_hosts_on_a_cluster_it_deletes(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    livetest.teardown(str(tmp_path), "ns1", "minikube",
                      livetest.Owned(cluster=True, blackholed=["gcr.io"]))
    assert not any("/etc/hosts" in str(c) for c in cmds)


def test_ensure_namespace_reports_one_it_did_not_create(monkeypatch):
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: None)
    assert livetest.ensure_namespace("kubectl", "ns1") is False


def test_ensure_namespace_reports_one_it_created(monkeypatch):
    cmds = []
    monkeypatch.setattr(livetest.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""))
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    assert livetest.ensure_namespace("kubectl", "ns1") is True
    assert ["kubectl", "create", "ns", "ns1"] in cmds


def test_owned_defaults_to_owning_nothing():
    o = livetest.Owned()
    assert (o.cluster, o.namespace, o.blackholed) == (False, False, ())


# -- the existing-ConfigMap CA mode, live (#227) -------------------------------
#
# The rig has only ever exercised the inline mode: proxy_overlay seeds
# `ca_bundle` and clears the other two, so the mode nearly every customer takes
# has never been deployed under TLS interception. `--ca-mode existing` makes the
# rig create the trust ConfigMap itself and reference it.
#
# The key is deliberately NOT `ca-bundle.crt`. That is what `_ca_cfg` falls back
# to when `ca_configmap_key` is unset, so a run using it would pass whether or
# not the key reached anything -- the same shape of vacuous proof the negative
# control exists to stop.


def test_the_existing_mode_overlay_names_the_rigs_own_configmap():
    o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")
    assert o["ca_existing_configmap"] == livetest.CA_RIG_CONFIGMAP
    assert o["ca_configmap_key"] == livetest.CA_RIG_KEY
    assert o["ca_bundle"] is None and o["ca_openshift_inject"] is False


def test_the_rigs_key_is_not_the_generators_default():
    """Or the run would pass with `ca_configmap_key` reaching nothing."""
    assert livetest.CA_RIG_KEY != gen.CA_FILENAME


def test_the_existing_mode_still_carries_the_proxy():
    o = livetest.proxy_overlay("h", 8080, CA_PEM, "bzm", "s3cr3t",
                               ca_mode="existing")
    assert o["proxy"]["https"] == "http://h:8080"
    assert o["proxy"]["username"] == "bzm"


def test_the_existing_mode_renders_a_bundle_that_mounts_the_rigs_key():
    """The whole chain in one assertion: no ConfigMap of our own, the reference
    by name, and both variables built from the non-default key."""
    opts = {"namespace": "ns1", "auth_token": "tok",
            **livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")}
    files = gen.generate(FACTS, opts)
    assert "bzm_cacerts.yaml" not in files
    d = yaml.safe_load(files["bzm_deployment.yaml"])
    vol = d["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"]
    assert vol == livetest.CA_RIG_CONFIGMAP
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["REQUESTS_CA_BUNDLE"] == f"/var/cm/{livetest.CA_RIG_KEY}"
    assert cm["KUBERNETES_CA_BUNDLE_MOUNT"] == (
        f"REQUESTS_CA_BUNDLE={livetest.CA_RIG_CONFIGMAP}={livetest.CA_RIG_KEY}:"
        f"AWS_CA_BUNDLE={livetest.CA_RIG_CONFIGMAP}={livetest.CA_RIG_KEY}")


def test_the_existing_mode_wins_over_a_profile_carrying_an_inline_pem():
    """The mirror of test_overlay_replaces_existing_ca_mode: whichever mode the
    bundle under test was written with, the rig's own answer replaces it, or
    _ca_cfg sees two and refuses."""
    opts = {"namespace": "ns1", "ca_bundle": "-----BEGIN CERTIFICATE-----\nx\n"
                                             "-----END CERTIFICATE-----",
            **livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="existing")}
    files = gen.generate(FACTS, opts)
    assert "bzm_cacerts.yaml" not in files


def test_ensure_ca_configmap_creates_it_with_the_key_the_bundle_mounts(
        monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    assert livetest.ensure_ca_configmap("kubectl", "ns1", CA_PEM) is True
    create, = [c for c in cmds if "create" in c and "configmap" in c]
    assert create[:5] == ["kubectl", "-n", "ns1", "create", "configmap"]
    assert create[5] == livetest.CA_RIG_CONFIGMAP
    # --from-file=<key>=<path>, the explicit form: the bare one would key the
    # entry on the temp file's own name and mount an empty file.
    arg, = [a for a in create if a.startswith("--from-file=")]
    assert arg.startswith(f"--from-file={livetest.CA_RIG_KEY}=")
    written = arg.split("=", 2)[2]
    assert not os.path.exists(written), "the temp CA file outlived the call"


def test_ensure_ca_configmap_refuses_one_it_did_not_create(monkeypatch):
    """Same rule as the cluster and the namespace: what this run did not make,
    it does not overwrite -- and overwriting is what a rig replacing the content
    of somebody's trust bundle would do."""
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    with pytest.raises(RuntimeError, match=livetest.CA_RIG_CONFIGMAP):
        livetest.ensure_ca_configmap("kubectl", "ns1", CA_PEM)


def test_teardown_removes_the_ca_configmap_from_a_namespace_it_kept(
        monkeypatch, tmp_path):
    """A surviving namespace keeps it otherwise, and the next run into that
    namespace is then refused by ensure_ca_configmap -- correctly, but over an
    object this rig left there."""
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind",
                      livetest.Owned(ca_configmap=livetest.CA_RIG_CONFIGMAP))
    assert ["kubectl", "-n", "ns1", "delete", "cm", livetest.CA_RIG_CONFIGMAP,
            "--ignore-not-found"] in cmds


def test_teardown_removes_the_name_this_run_made_not_a_constant(
        monkeypatch, tmp_path):
    """`--ca-mode file` has the rig create the *generator's* ConfigMap, because
    that is the one the bundle mounts and does not create. Deleting
    CA_RIG_CONFIGMAP by name would remove nothing and leave blazemeter-cacerts
    in a surviving namespace, which the next run is then refused over."""
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind",
                      livetest.Owned(ca_configmap=gen.CA_CONFIGMAP))
    assert ["kubectl", "-n", "ns1", "delete", "cm", gen.CA_CONFIGMAP,
            "--ignore-not-found"] in cmds
    assert not any(livetest.CA_RIG_CONFIGMAP in c for c in cmds)


def test_teardown_does_not_remove_a_ca_configmap_this_run_did_not_create(
        monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(livetest, "_run", lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    livetest.teardown(str(tmp_path), "ns1", "kind", livetest.Owned())
    assert not any(livetest.CA_RIG_CONFIGMAP in c for c in cmds)


def test_the_negative_control_deletes_the_name_the_file_mode_depends_on(
        monkeypatch, tmp_path):
    """Why the rig creates its ConfigMap *after* the control, not before.

    The control has to deploy with no CA reachable, so it deletes CA_CONFIGMAP
    by name. In `--ca-mode file` that is exactly the object the rig creates --
    the bundle mounts the generator's ConfigMap and creates none -- so creating
    it first meant creating it and then having the control delete it. Measured:
    the real deploy mounted a ConfigMap that was no longer there and sat in
    ContainerCreating until the rollout timed out, with the bundle itself
    correct. `existing` never showed it, its name being the rig's own.

    Asserted over the command the control runs, so the ordering rule in run()
    has something that fails when the name it deletes changes."""
    cmds = []
    monkeypatch.setattr(livetest, "_run",
                        lambda cmd, **kw: cmds.append(cmd))
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(livetest, "deploy", lambda *a, **k: "kubectl")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    overlay = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="file")
    livetest.negative_control(lambda o: None, overlay, str(tmp_path), "ns1",
                              "kind", timeout=0)
    assert ["kubectl", "-n", "ns1", "delete", "cm", gen.CA_CONFIGMAP,
            "--ignore-not-found"] in cmds


def test_owned_still_defaults_to_owning_nothing():
    """The field carries a *name* now, so the safe default is None rather than
    False -- teardown branches on it either way, and a run that fell over before
    it created anything must still own nothing."""
    assert livetest.Owned().ca_configmap is None


def test_the_negative_control_clears_whichever_mode_the_run_is_using(
        monkeypatch, tmp_path):
    """It cleared `ca_bundle` alone, which for an existing-ConfigMap run leaves
    the reference in place: the pod then never starts (the ConfigMap is gone
    with it), so nothing ever logs CERTIFICATE_VERIFY_FAILED and the control
    fails for the wrong reason."""
    seen = {}
    monkeypatch.setattr(livetest, "_run", lambda *a, **k: None)
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
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
    """Read off the generator rather than listed here, so a mode added later
    fails this rather than passing quietly (#250). `ca_bundle_slot` was that
    mode: a slot bundle handed to `livetest --manifests` kept its slot through
    the negative control, so the pod stopped at `Init:Error`, crane never
    started, nothing logged CERTIFICATE_VERIFY_FAILED, and the run spent its
    whole timeout reporting a failed control over a bundle whose CA
    configuration it had not removed."""
    seen = {}
    monkeypatch.setattr(livetest, "_run", lambda *a, **k: None)
    monkeypatch.setattr(livetest, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(livetest, "deploy", lambda *a, **k: "kubectl")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    overlay = {**livetest.proxy_overlay("h", 8080, CA_PEM), "ca_bundle_slot": True}
    livetest.negative_control(lambda o: seen.update(o), overlay,
                              str(tmp_path), "ns1", "kind", timeout=0)
    for key in gen.CA_OPTIONS:
        assert seen[key] == gen.DEFAULT_OPTIONS[key], key
    assert gen._ca_cfg({**gen.DEFAULT_OPTIONS, **seen}) is None


def test_the_proxy_overlay_replaces_every_ca_mode_too():
    """Same fault, same source: the overlay is merged onto a profile.json that
    may carry any mode, so a slot left standing beside the rig's own PEM is
    `_ca_cfg` refusing the pair -- the run dies at the re-render instead of at
    the deploy, which is louder and just as wrong."""
    for mode in ("inline", "existing", "file"):
        o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode=mode)
        for key in gen.CA_OPTIONS:
            assert key in o, f"{mode}: {key} is left for the profile to answer"
        assert gen._ca_cfg({**gen.DEFAULT_OPTIONS, "ca_bundle_slot": True, **o})


def test_the_file_mode_names_the_configmap_the_bundle_itself_writes():
    """The rig has to create the object the bundle references, and for the file
    mode that name is the *generator's* -- it is what lands in
    KUBERNETES_CA_BUNDLE_MOUNT, so a rig ConfigMap under any other name is one
    the agent never mounts and the run fails for a reason that is the rig's.

    The key is deliberately not `ca-bundle.crt`: that is `_ca_cfg`'s fallback,
    so a run using it would pass whether or not `ca_cert_file` reached
    anything -- the same trap `existing` avoids with the same key."""
    o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="file")
    ca = gen._ca_cfg({**gen.DEFAULT_OPTIONS, **o})
    assert ca["mode"] == "file"
    assert ca["cm"] == gen.CA_CONFIGMAP
    assert ca["key"] == livetest.CA_RIG_KEY != gen.CA_FILENAME


def test_the_file_mode_bundle_creates_no_configmap_for_the_rig_to_collide_with():
    """`ensure_ca_configmap` refuses a name it did not create, which is the rule
    that stops the rig replacing a trust bundle that is somebody's. That rule
    only holds here because the bundle emits no ConfigMap of its own -- if it
    still shipped one, applying the bundle and then creating the rig's would be
    the rig colliding with itself."""
    from test_generate import FACTS
    o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode="file")
    files = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222", **o})
    assert gen.CA_CONFIGMAP_FILE not in files
    # ...and the Deployment still mounts it, which is the half that makes the
    # rig-created object reachable at all.
    dep = files["bzm_deployment.yaml"]
    assert gen.CA_CONFIGMAP in dep and livetest.CA_RIG_KEY in \
        files["bzm_configmap.yaml"]


# -- what a run owes the CA mode it was handed (#251) --------------------------
#
# Two halves of one question. A run with no proxy re-renders no CA and deploys
# what is on disk: for `file` and `existing` that is a ConfigMap this rig never
# creates, so it is refused before the cluster exists. A run with the proxy
# re-renders the CA whatever happens -- the CA under test is the proxy's own --
# so the only question left is which mode it re-renders *to*, and the answer is
# the bundle's own unless somebody says otherwise.


@pytest.mark.parametrize("options,mode", [
    ({}, None),
    ({"ca_bundle": CA_PEM}, "inline"),
    ({"ca_existing_configmap": "corp-trust"}, "existing"),
    ({"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"}, "file"),
    ({"ca_openshift_inject": True}, None),
])
def test_the_rig_reads_which_ca_mode_a_bundle_is_already_in(options, mode):
    """None is the two bundles the rig has no mode for: one with no CA at all,
    and an OpenShift-injection one -- nothing here injects a trust bundle, the
    cluster network operator does."""
    assert livetest.rig_ca_mode(dict(options, namespace="ns1")) == mode


def test_every_rig_ca_mode_is_one_the_overlay_can_build():
    """The flag's choices and the overlay's branches are one list, so a mode
    named on the command line always has something to render."""
    for mode in livetest.RIG_CA_MODES:
        o = livetest.proxy_overlay("h", 8080, CA_PEM, ca_mode=mode)
        assert livetest.rig_ca_mode({"namespace": "ns1", **o}) == mode


@pytest.mark.parametrize("options,names", [
    ({"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"},
     (gen.CA_CONFIGMAP, "corp-root.pem", "--ca-mode file")),
    ({"ca_existing_configmap": "corp-trust"},
     ("corp-trust", "--ca-mode existing")),
])
def test_a_ca_configmap_nothing_creates_is_refused_before_the_cluster(options,
                                                                     names):
    """The bundle names a ConfigMap somebody else builds -- a pipeline holding
    the certificate, a platform team holding the trust bundle -- and this rig
    deploys into a namespace it creates itself, where neither is there. The
    kubelet holds the pod at ContainerCreating, no heartbeat arrives, and the
    run spends its whole 12-20 minutes reporting only that the agent never came
    online. The refusal names the object and the run that would build it."""
    said = livetest.ca_configmap_refusal(dict(options, namespace="ns1"), False)
    for name in names:
        assert name in said


def test_a_bundle_that_leaves_the_certificate_unnamed_is_refused_by_its_marker():
    """A bundle generated before anybody knew what the certificate was called.
    The marker is what somebody greps the directory with, so it is what the
    refusal quotes."""
    said = livetest.ca_configmap_refusal({"namespace": "ns1",
                                          "ca_bundle_slot": True}, False)
    assert gen.marker("ca_cert_file") in said


@pytest.mark.parametrize("options", [
    {},
    {"ca_bundle": CA_PEM},
    {"ca_openshift_inject": True},
])
def test_the_modes_that_carry_their_own_configmap_are_not_refused(options):
    """Inline writes the ConfigMap into the bundle; inject emits an empty one
    for the cluster operator to fill, so the object is there and the pod starts
    -- a cluster with no operator then fails TLS, which is a line in a log
    rather than a pod that never runs. Neither is this guard's business."""
    assert livetest.ca_configmap_refusal(dict(options, namespace="ns1"),
                                         False) is None


@pytest.mark.parametrize("options", [
    {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"},
    {"ca_existing_configmap": "corp-trust"},
])
def test_the_proxy_is_what_makes_those_two_modes_deployable(options):
    """--local-proxy replaces whatever mode the profile carried with one the rig
    builds itself, so under it every mode is fine. That is the honest reason the
    pairing is safe: #251 found it as an accident, a slot beside the rig's own
    PEM being `_ca_cfg` refusing the pair."""
    assert livetest.ca_configmap_refusal(dict(options, namespace="ns1"),
                                         True) is None


@pytest.mark.parametrize("local_proxy", [False, True])
def test_a_profile_setting_two_ca_modes_is_refused_either_way(local_proxy):
    """The third answer `generate.ca_mode` keeps separate, arriving where it
    matters: a re-rendering run would raise out of generate() with the cluster
    already built, and a lean one would deploy manifests whose own profile
    disagrees with them. Refused before either can happen, and not read as a
    bundle with no CA trust."""
    said = livetest.ca_configmap_refusal(
        {"namespace": "ns1", "ca_bundle_slot": True, "ca_bundle": CA_PEM},
        local_proxy)
    assert said and gen.PROFILE_FILE in said and "CA mode" in said


@pytest.mark.parametrize("asked,carried,resolved", [
    (None, {}, "inline"),
    (None, {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"}, "file"),
    (None, {"ca_openshift_inject": True}, "inline"),
    ("inline", {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"},
     "inline"),
])
def test_one_resolver_answers_for_the_cli_and_for_run(asked, carried, resolved):
    """The default is here rather than in `run()`'s signature and again in the
    CLI: the CLI needs the answer before the credential mint and `run()` needs
    it for a caller that is not the CLI at all, so both ask this. Idempotent, so
    resolving an already-resolved mode is the same answer."""
    profile = dict(carried, namespace="ns1")
    assert livetest.resolved_ca_mode(profile, asked) == resolved
    assert livetest.resolved_ca_mode(profile, resolved) == resolved


@pytest.mark.parametrize("options,says", [
    ({"ca_bundle": CA_PEM}, "what this bundle was generated for"),
    ({}, "configures no CA trust"),
    ({"ca_openshift_inject": True}, "OpenShift trust injection"),
])
def test_the_run_says_which_ca_mode_it_is_about_to_deploy(options, says):
    """Three different facts about the bundle, and the two behind one None get
    their own sentences: no CA at all, and an injection bundle this rig cannot
    deploy."""
    profile = dict(options, namespace="ns1")
    said = livetest.ca_mode_notice(profile,
                                   livetest.resolved_ca_mode(profile))
    assert says in said


def test_the_notice_says_when_a_flag_replaces_the_bundles_mode():
    """Still allowed -- --ca-mode is how somebody deliberately tests another
    configuration -- but never the quiet answer."""
    said = livetest.ca_mode_notice(
        {"namespace": "ns1", "ca_bundle_slot": True,
         "ca_cert_file": "corp-root.pem"}, "inline")
    assert "file" in said and "--ca-mode inline replaces it" in said


def test_the_notice_does_not_claim_the_existing_mode_keeps_the_bundles_object():
    """True of the mode and false of the object: the rig deploys `existing`
    under a ConfigMap of its own name and key, because a customer's belongs to a
    customer. The sentence says so rather than reading as "unchanged"."""
    said = livetest.ca_mode_notice({"namespace": "ns1",
                                    "ca_existing_configmap": "corp-trust"},
                                   "existing")
    assert livetest.CA_RIG_CONFIGMAP in said


def test_a_second_ensure_cluster_does_not_say_the_run_will_keep_what_it_built(
        monkeypatch, capsys):
    """run() creates the cluster and keeps the answer; deploy() then calls
    ensure_cluster again and drops it. That second call finds the profile it
    just made and printed "reusing ... this run will not delete it" -- which
    teardown then contradicts by deleting it, correctly.

    Seen in the #227 live run: the log said the profile would survive, twice,
    and the run ended with `minikube delete`. A function that drops the answer
    must not narrate it either, or the log reports the opposite of what happens.
    """
    monkeypatch.setattr(livetest, "minikube_profile_exists", lambda: True)
    monkeypatch.setattr(livetest, "_run", lambda *a, **k: None)
    monkeypatch.setattr(livetest, "policy_enforced", lambda *a, **k: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "Running", ""))
    livetest.ensure_minikube(announce=False)
    assert "will not delete it" not in capsys.readouterr().out


def test_ensure_cluster_still_announces_a_reuse_to_the_caller_that_keeps_it(
        monkeypatch, capsys):
    """The message is the point everywhere else: it is the only place a run says
    which cluster it is about to leave alone."""
    monkeypatch.setattr(livetest, "minikube_profile_exists", lambda: True)
    monkeypatch.setattr(livetest, "_run", lambda *a, **k: None)
    monkeypatch.setattr(livetest, "policy_enforced", lambda *a, **k: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "Running", ""))
    livetest.ensure_minikube()
    assert "will not delete it" in capsys.readouterr().out
