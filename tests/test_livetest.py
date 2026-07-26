"""Offline tests for the local proxy/CA rig: the option overlay livetest builds
and the profile.json round-trip it replays through."""

import json
import os
import subprocess
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


def test_engine_config_catches_missing_proxy_env():
    fails = livetest.assert_engine_config(_engine_pod(proxy=False), ENGINE_OPTS)
    assert any("bypassing the customer's proxy" in f for f in fails)


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
    browser, which /api/sv-expose promises never to hand a bare error, so this
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
