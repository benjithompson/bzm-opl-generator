"""smoke: the post-install check of a customer's deployed agent. The cluster is
faked at kube.quiet and the account at a client object; nothing here reaches a
cluster, a network or an account."""

import json
import subprocess
import time

import pytest

from bzm_opl_gen import api, cli, core, facts, kube, smoke
from bzm_opl_gen.verdict import FAIL, PASS, WARN

NS = "bzm"
HARBOR = "h1"
SHIP = "s1"
CRANE = "crane-5d4f8b7c9-x2x7q"
ENGINE = "r-v4-6512ab3f-0-0-c-7xk2p"
CRANE_IMAGE = "gcr.io/verdant-bulwark-278/blazemeter/crane:3.7.55"
ENGINE_IMAGE = "gcr.io/verdant-bulwark-278/blazemeter/v4:1.2.3"
TEST_ID = 4242
FORBIDDEN = 'Error from server (Forbidden): {} is forbidden: User "dev" cannot list'
NOT_FOUND = 'Error from server (NotFound): {} "x" not found'
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


# -- the cluster --------------------------------------------------------------------

def deployment(ready=1, replicas=1, secret=True, ca=None, image=CRANE_IMAGE,
               ship=SHIP):
    container = {"name": f"bzm-crane-{ship}", "image": image,
                 "envFrom": [{"configMapRef": {"name": "blazemeter-configmap"}}]}
    if secret:
        container["envFrom"].append({"secretRef": {"name": "blazemeter-secret"}})
    spec = {"containers": [container]}
    if ca:
        container["volumeMounts"] = [{"name": "cacerts", "mountPath": "/var/cm"}]
        spec["volumes"] = [{"name": "cacerts", "configMap": {"name": ca}}]
    labels = {"role": "role-crane", "harbor_id": HARBOR, "ship_id": ship}
    return {"metadata": {"name": "crane", "labels": labels},
            "spec": {"replicas": replicas, "selector": {"matchLabels": labels},
                     "template": {"metadata": {"labels": labels}, "spec": spec}},
            "status": {"readyReplicas": ready}}


def crane_pod(ready=True, restarts=0, waiting=None, last=None):
    state = {"waiting": waiting} if waiting else {"running": {}}
    return {"metadata": {"name": CRANE,
                         "labels": {"role": "role-crane", "harbor_id": HARBOR,
                                    "ship_id": SHIP}},
            "spec": {"containers": [{"name": f"bzm-crane-{SHIP}",
                                     "image": CRANE_IMAGE}]},
            "status": {"phase": "Running", "containerStatuses": [{
                "name": f"bzm-crane-{SHIP}", "image": CRANE_IMAGE,
                "ready": ready, "restartCount": restarts, "state": state,
                "lastState": {"terminated": last} if last else {}}]}}


def engine_pod(requests=None, limits=None, qos="Guaranteed", image=ENGINE_IMAGE,
               env=None):
    limits = limits or {"cpu": "2", "memory": "8Gi"}
    requests = requests or dict(limits)
    return {"metadata": {"name": ENGINE, "labels": {}},
            "spec": {"nodeName": "node-1", "containers": [{
                "name": "r-v4-6512ab3f", "image": image,
                "env": env or [{"name": "JVM_ARGS", "value": "-Xmx6g"}],
                "resources": {"limits": limits, "requests": requests}}]},
            "status": {"phase": "Running", "podIP": "10.0.0.9", "qosClass": qos}}


def cm_data(**over):
    data = {"HARBOR_ID": HARBOR, "SHIP_ID": SHIP,
            "CONTAINER_MANAGER_TYPE": "KUBERNETES",
            "DOCKER_REGISTRY": "gcr.io/verdant-bulwark-278",
            "AUTO_KUBERNETES_UPDATE": "false",
            "KUBERNETES_RESOURCES_LIMITS_CPU": "2",
            "KUBERNETES_RESOURCES_LIMITS_MEMORY": "8Gi",
            "KUBERNETES_RESOURCES_DEFAULT_CPU": "2",
            "KUBERNETES_RESOURCES_DEFAULT_MEM": "8192"}
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def items(*objs):
    return (0, json.dumps({"items": list(objs)}))


def obj(o):
    return (0, json.dumps(o))


def refused(kind):
    return (1, FORBIDDEN.format(kind))


def absent(kind):
    return (1, NOT_FOUND.format(kind))


class FakeKubectl:
    """kubectl by verb and object: `get deployments`, `get configmap <name>`,
    `get secret <name>`, `exec`, `logs`. A list answers in turn and then
    repeats its last. Every command is recorded."""

    def __init__(self, answers):
        self.answers = {"get events": items(), "logs": (0, ""),
                        f"get ns {NS}": obj({"metadata": {"name": NS}}),
                        **answers}
        self.cmds = []

    def key(self, cmd):
        argv = list(cmd[1:])
        if argv[:1] == ["-n"]:
            argv = argv[2:]
        if argv[0] != "get":
            return argv[0]
        name = argv[2] if len(argv) > 2 and not argv[2].startswith("-") else None
        return f"get {argv[1]}" + (f" {name}" if name else "")

    def __call__(self, cmd, timeout=None, input=None):
        self.cmds.append(cmd)
        key = self.key(cmd)
        if key not in self.answers:
            raise AssertionError(f"unexpected kubectl call: {cmd}")
        answer = self.answers[key]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        rc, out = answer
        return subprocess.CompletedProcess(cmd, rc, out if rc == 0 else "",
                                           "" if rc == 0 else out)


def healthy_cluster(**over):
    answers = {"get deployments": items(deployment()),
               "get pods": items(crane_pod()),
               "get configmap blazemeter-configmap": obj({"data": cm_data()}),
               "get secret blazemeter-secret": (0, "AUTH_TOKEN\n")}
    answers.update(over)
    return answers


@pytest.fixture
def cluster(monkeypatch):
    """Install a fake kubectl; call with the answers."""
    def install(answers):
        fake = FakeKubectl(answers)
        monkeypatch.setattr(kube, "quiet", fake)
        monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
        return fake
    return install


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Every poll runs on a clock that each sleep advances, so a timeout is
    reached without waiting for it."""
    real = kube.poll_until

    def fast(fn, timeout, interval, **kw):
        now = [0.0]
        return real(fn, timeout, interval, clock=lambda: now[0],
                    sleep=lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(kube, "poll_until", fast)


# -- the account --------------------------------------------------------------------

class FakeAccount:
    def __init__(self, heartbeat_age=10, state="idle", ships=None, harbor=None,
                 test=None, masters=("ENDED",), events=None, summary=None,
                 start_error=None, location_error=None):
        hb = time.time() - heartbeat_age if heartbeat_age is not None else 0
        self.harbor = harbor or {
            "id": HARBOR, "name": "corp-k8s", "funcIds": ["performance"],
            "slots": 2, "threadsPerEngine": 500,
            "ships": ships if ships is not None else [
                {"id": SHIP, "name": "agent-1", "state": state,
                 "lastHeartBeat": hb, "installedVersion": "3.7.55"}]}
        self._test = test if test is not None else {
            "id": TEST_ID, "name": "checkout flow",
            "executions": [{"locations": {f"harbor-{HARBOR}": 1}}]}
        self._masters = list(masters)
        self._events = events if events is not None else [
            "Status changed to ENDED (140)", "Taurus completed (Exit: 0)"]
        self._summary = summary or {"hits": 612, "avg": 211, "failed": 0}
        self._start_error = start_error
        self._location_error = location_error
        self.calls = []

    def private_location(self, harbor_id):
        self.calls.append(("private_location", harbor_id))
        if self._location_error:
            raise self._location_error
        return self.harbor

    def ship_versions(self, harbor_id, ship_id):
        raise api.BzmApiError("GET versions -> HTTP 404: not found", status=404)

    def test(self, test_id):
        self.calls.append(("test", test_id))
        return self._test

    def update_test(self, test_id, body):
        self.calls.append(("update_test", test_id))

    def start_test(self, test_id):
        self.calls.append(("start_test", test_id))
        if self._start_error:
            raise self._start_error
        return 9001

    def master_status(self, master_id):
        status = self._masters.pop(0) if len(self._masters) > 1 else self._masters[0]
        return {"status": status, "events": [{"message": m} for m in self._events]}

    def master_summary(self, master_id):
        return {"summary": [self._summary]}

    def stop_master(self, master_id):
        self.calls.append(("stop_master", master_id))


def checks(doc):
    return {c["name"]: c for s in doc["stages"] for c in s["checks"]}


def status(doc):
    return {name: c["status"] for name, c in checks(doc).items()}


def run(account=None, notify=None, **kw):
    return core.smoke(account or FakeAccount(), NS, notify=notify, **kw)


# -- a healthy agent ------------------------------------------------------------------

def test_a_healthy_agent_passes_every_check_and_skips_the_triage(cluster):
    fake = cluster(healthy_cluster())
    doc = run()
    assert set(status(doc).values()) == {PASS}, status(doc)
    assert doc["ok"] is True and doc["triage"] is None
    assert [s["stage"] for s in doc["stages"]] == [
        "cluster", "blazemeter", "configuration"]
    assert doc["summary"].endswith("no failures")
    assert not any(c[3] == "logs" for c in fake.cmds), "triage ran on a pass"


def test_the_ids_come_from_the_deployed_configmap(cluster):
    cluster(healthy_cluster())
    doc = run()
    assert (doc["harbor_id"], doc["ship_id"]) == (HARBOR, SHIP)
    assert "read from the ConfigMap" in checks(doc)["agent-ids"]["detail"]


def test_ids_that_name_another_agent_fail_and_the_deployed_agent_is_checked(cluster):
    cluster(healthy_cluster())
    doc = run(harbor_id=HARBOR, ship_id="s-other")
    ids = checks(doc)["agent-ids"]
    assert ids["status"] == FAIL and "not the agent id given (s-other)" in ids["detail"]
    assert doc["ship_id"] == SHIP


def test_the_secret_is_read_by_key_name_only(cluster):
    """The AUTH_TOKEN's value never leaves the cluster: the read asks for the
    key names alone."""
    fake = cluster(healthy_cluster())
    run()
    secret_reads = [c for c in fake.cmds if c[3:5] == ["get", "secret"]]
    assert secret_reads and all("-o" in c and c[c.index("-o") + 1].startswith(
        "go-template=") for c in secret_reads)
    assert not any("json" in c for c in secret_reads)


# -- stage 1: read states ---------------------------------------------------------------

def test_a_refused_read_is_unread_and_exits_clean(cluster):
    cluster(healthy_cluster(**{"get deployments": refused("deployments"),
                               "get secret blazemeter-secret": refused("secrets")}))
    doc = run()
    assert status(doc)["crane-deployment"] == smoke.UNREAD
    assert status(doc)["credential"] == smoke.UNREAD
    assert doc["ok"] is True and doc["triage"] is None


@pytest.mark.parametrize("secret,want", [
    ((0, "AUTH_TOKEN\n"), PASS),
    (absent("secrets"), smoke.UNREAD),
])
def test_an_unread_deployment_reads_the_secret_by_the_bundle_s_name(cluster, secret,
                                                                    want):
    """With the Deployment unread, a Secret missing by the bundle's name is not
    proof that crane has no credential: it may reference another."""
    cluster(healthy_cluster(**{"get deployments": refused("deployments"),
                               "get secret blazemeter-secret": secret}))
    assert status(run())["credential"] == want


@pytest.mark.parametrize("answer,want", [
    (absent("configmaps"), FAIL),
    (refused("configmaps"), smoke.UNREAD),
])
def test_a_missing_configmap_fails_and_an_unreadable_one_does_not(cluster, answer,
                                                                  want):
    cluster(healthy_cluster(**{"get configmap blazemeter-configmap": answer}))
    doc = run(harbor_id=HARBOR, ship_id=SHIP)
    assert status(doc)["configmap"] == want
    # The configuration checks follow the read, never the other answer.
    assert status(doc)["engine-sizing"] == (smoke.SKIP if want == FAIL else smoke.UNREAD)


@pytest.mark.parametrize("answer,want,words", [
    (absent("secrets"), FAIL, "not in the namespace"),
    ((0, "HTTPS_PROXY\n"), FAIL, "holds no AUTH_TOKEN"),
    ((0, "AUTH_TOKEN\n"), PASS, "value is not read"),
])
def test_the_credential(cluster, answer, want, words):
    cluster(healthy_cluster(**{"get secret blazemeter-secret": answer}))
    got = checks(run())["credential"]
    assert got["status"] == want and words in got["detail"]


def test_a_token_in_the_configmap_is_a_warning(cluster):
    cluster(healthy_cluster(**{
        "get deployments": items(deployment(secret=False)),
        "get configmap blazemeter-configmap": obj({"data": cm_data(AUTH_TOKEN="tok")})}))
    got = checks(run())["credential"]
    assert got["status"] == WARN and "use_secret" in got["fix"]
    assert "tok" not in got["detail"]


def test_no_crane_deployment_fails_and_runs_the_triage(cluster):
    fake = cluster(healthy_cluster(**{"get deployments": items(),
                                      "get pods": items()}))
    doc = run(harbor_id=HARBOR, ship_id=SHIP)
    assert status(doc)["crane-deployment"] == FAIL
    assert doc["ok"] is False
    assert doc["triage"] is not None
    assert "crane-missing" in [f["rule"] for f in doc["triage"]["findings"]]
    assert any(c[3:5] == ["get", "events"] for c in fake.cmds)


def test_a_crash_looping_crane_fails_with_the_triage_s_finding(cluster):
    looping = crane_pod(ready=False, restarts=7, waiting={
        "reason": "CrashLoopBackOff", "message": "back-off 5m0s restarting"})
    cluster(healthy_cluster(**{
        "get pods": items(looping),
        "logs": (0, "requests.exceptions.SSLError: HTTPSConnectionPool(host="
                    "'a.blazemeter.com', port=443): [SSL: CERTIFICATE_VERIFY_FAILED]"
                    " certificate verify failed")}))
    doc = run()
    pod = checks(doc)["crane-pod"]
    assert pod["status"] == FAIL and "CrashLoopBackOff" in pod["detail"]
    assert "tls-trust" in [f["rule"] for f in doc["triage"]["findings"]]


def test_a_crane_that_restarted_and_recovered_is_a_warning(cluster):
    cluster(healthy_cluster(**{"get pods": items(crane_pod(restarts=2, last={
        "reason": "OOMKilled", "exitCode": 137, "finishedAt": "2026-09-29T11:00:00Z"}))}))
    pod = checks(run())["crane-pod"]
    assert pod["status"] == WARN and "OOMKilled" in pod["detail"]


def test_a_deployment_scaled_to_zero_fails(cluster):
    cluster(healthy_cluster(**{"get deployments": items(deployment(ready=0, replicas=0))}))
    assert status(run())["crane-deployment"] == FAIL


def test_no_kubectl_leaves_the_cluster_unread_and_still_asks_blazemeter(monkeypatch):
    def none():
        raise RuntimeError("neither oc nor kubectl found on PATH")
    monkeypatch.setattr(kube, "cli_tool", none)
    doc = run(harbor_id=HARBOR, ship_id=SHIP)
    st = status(doc)
    assert st["crane-deployment"] == st["crane-pod"] == smoke.UNREAD
    assert st["agent"] == PASS and doc["ok"] is True


def test_nothing_read_says_so(monkeypatch):
    def none():
        raise RuntimeError("neither oc nor kubectl found on PATH")
    monkeypatch.setattr(kube, "cli_tool", none)
    doc = run()
    assert "Pass --harbor-id" in checks(doc)["agent-ids"]["fix"]
    assert "cannot show a failure" in doc["summary"]


# -- stage 2: BlazeMeter --------------------------------------------------------------

@pytest.mark.parametrize("account,words", [
    (FakeAccount(heartbeat_age=900), "not reporting"),
    (FakeAccount(heartbeat_age=None, state="created"), "never reported"),
    (FakeAccount(ships=[]), "does not know this agent"),
])
def test_an_agent_that_is_not_reporting_fails(cluster, account, words):
    cluster(healthy_cluster())
    doc = run(account)
    assert status(doc)["agent"] == FAIL and words in checks(doc)["agent"]["detail"]
    assert doc["triage"] is not None


def test_an_unreadable_account_is_unread_not_offline(cluster):
    cluster(healthy_cluster())
    doc = run(FakeAccount(location_error=api.BzmApiError("HTTP 500", status=500)))
    st = status(doc)
    assert st["agent"] == st["location"] == st["engine-overrides"] == smoke.UNREAD
    assert doc["ok"] is True


def test_a_location_a_test_cannot_start_on_fails(cluster):
    account = FakeAccount()
    del account.harbor["threadsPerEngine"]
    cluster(healthy_cluster())
    got = checks(run(account))["location"]
    assert got["status"] == FAIL and "403" in got["fix"]


# -- stage 3: configuration ----------------------------------------------------------

@pytest.mark.parametrize("over,words", [
    ({"KUBERNETES_RESOURCES_DEFAULT_CPU": None, "KUBERNETES_RESOURCES_DEFAULT_MEM": None},
     "250m / 256Mi"),
    ({"KUBERNETES_RESOURCES_DEFAULT_MEM": "4096"}, "request 2 CPU / 4Gi"),
    ({"KUBERNETES_RESOURCES_LIMITS_MEMORY": "lots"}, "not a quantity"),
])
def test_engine_sizing_fails_unless_requests_equal_limits(cluster, over, words):
    cluster(healthy_cluster(**{"get configmap blazemeter-configmap":
                               obj({"data": cm_data(**over)})}))
    got = checks(run())["engine-sizing"]
    assert got["status"] == FAIL and words in got["detail"]


@pytest.mark.parametrize("cpu,mem,want", [
    (None, None, PASS), (2, 8192, PASS), (1, 4096, FAIL)])
def test_location_overrides_must_equal_the_limits(cluster, cpu, mem, want):
    account = FakeAccount()
    account.harbor.update(overrideCPU=cpu, overrideMemory=mem)
    cluster(healthy_cluster())
    assert status(run(account))["engine-overrides"] == want


def _ca_cluster(**over):
    data = cm_data(REQUESTS_CA_BUNDLE="/var/cm/ca-bundle.crt",
                   KUBERNETES_CA_BUNDLE_MOUNT="REQUESTS_CA_BUNDLE=blazemeter-cacerts="
                                              "ca-bundle.crt")
    answers = healthy_cluster(**{
        "get deployments": items(deployment(ca="blazemeter-cacerts")),
        "get configmap blazemeter-configmap": obj({"data": data}),
        "get configmap blazemeter-cacerts": obj({"data": {"ca-bundle.crt": PEM}}),
        "exec": (0, "count=3\n")})
    answers.update(over)
    return answers


def test_a_mounted_ca_bundle_with_certificates_in_the_pod_passes(cluster):
    fake = cluster(_ca_cluster())
    got = checks(run())["ca-trust"]
    assert got["status"] == PASS and "3 certificates" in got["detail"]
    assert any(c[3] == "exec" and c[4] == CRANE for c in fake.cmds)


@pytest.mark.parametrize("over,want,words", [
    ({"get configmap blazemeter-cacerts": absent("configmaps")}, FAIL, "not in the namespace"),
    ({"get configmap blazemeter-cacerts": obj({"data": {"other.pem": PEM}})}, FAIL,
     "no certificate under the key ca-bundle.crt"),
    ({"exec": (0, "count=missing\n")}, FAIL, "inside the crane pod"),
    ({"exec": refused("pods/exec")}, PASS, "was not read"),
    ({"get deployments": items(deployment())}, FAIL, "mounts no ConfigMap"),
])
def test_ca_trust_failures(cluster, over, want, words):
    cluster(_ca_cluster(**over))
    got = checks(run())["ca-trust"]
    assert got["status"] == want and words in got["detail"], got


def test_proxy_credentials_in_the_configmap_are_named_not_shown(cluster):
    cluster(healthy_cluster(**{"get configmap blazemeter-configmap": obj({"data": cm_data(
        HTTPS_PROXY="http://svc:hunter2@proxy.corp:3128",
        NO_PROXY="kubernetes.default,.svc")})}))
    got = checks(run())["proxy"]
    assert got["status"] == WARN and "HTTPS_PROXY" in got["detail"]
    assert "hunter2" not in json.dumps(got)


def test_a_proxy_that_would_carry_the_api_calls_is_a_warning(cluster):
    cluster(healthy_cluster(**{"get configmap blazemeter-configmap": obj({"data": cm_data(
        HTTPS_PROXY="http://proxy.corp:3128", NO_PROXY="127.0.0.1")})}))
    got = checks(run())["proxy"]
    assert got["status"] == WARN and "proxy.corp:3128" in got["detail"]


def test_a_private_registry_missing_an_override_fails(cluster):
    cluster(healthy_cluster(**{"get configmap blazemeter-configmap": obj({"data": cm_data(
        DOCKER_REGISTRY="registry.corp/bzm",
        IMAGE_OVERRIDES=json.dumps({"taurus-cloud:latest": "registry.corp/bzm/v4:1"}))})}))
    got = checks(run())["registry"]
    assert got["status"] == FAIL and "IMAGE_OVERRIDES does not name" in got["detail"]


def test_a_private_registry_with_the_image_list_unread_is_a_warning(cluster):
    cluster(healthy_cluster(**{"get configmap blazemeter-configmap": obj({"data": cm_data(
        DOCKER_REGISTRY="registry.corp/bzm", IMAGE_OVERRIDES="{}")})}))
    doc = run(FakeAccount(location_error=api.BzmApiError("HTTP 503", status=503)))
    got = checks(doc)["registry"]
    assert got["status"] == WARN and "could not be read" in got["detail"]


# -- stage 4: one real engine run -------------------------------------------------------

def _engine_cluster(engine):
    return healthy_cluster(**{"get pods": [items(crane_pod()),
                                           items(crane_pod(), engine)]})


def test_a_guaranteed_engine_run_passes_and_nothing_is_repointed(cluster):
    cluster(_engine_cluster(engine_pod()))
    said, account = [], FakeAccount()

    def notify(line):
        said.append((line, list(account.calls)))
    doc = run(account, notify=notify, run_test=TEST_ID)
    engine = doc["stages"][-1]
    assert engine["stage"] == "engine run"
    assert {c["name"]: c["status"] for c in engine["checks"]} == {
        "test-target": PASS, "engine-pod": PASS, "engine-size": PASS,
        "engine-qos": PASS, "engine-config": PASS, "engine-heap": PASS,
        "run-status": PASS, "run-samples": PASS, "engine-exit": PASS}
    assert doc["ok"] is True
    # The plan is said before the start, and the test is never changed.
    plan, calls_then = said[0]
    assert "Starting test 4242 (checkout flow)" in plan and "real run" in plan
    assert ("start_test", TEST_ID) not in calls_then
    assert [c for c in account.calls if c[0] in ("update_test", "stop_master")] == []
    assert account.calls.count(("start_test", TEST_ID)) == 1


def test_an_engine_that_is_not_guaranteed_fails_and_says_why(cluster):
    burstable = engine_pod(requests={"cpu": "250m", "memory": "256Mi"},
                           qos="Burstable")
    cluster(_engine_cluster(burstable))
    doc = run(run_test=TEST_ID)
    qos = checks(doc)["engine-qos"]
    assert qos["status"] == FAIL and "Burstable, not Guaranteed" in qos["detail"]
    assert "KUBERNETES_RESOURCES_DEFAULT_CPU" in qos["fix"]
    assert doc["ok"] is False and doc["triage"] is not None


def test_an_engine_limited_to_another_size_fails(cluster):
    small = engine_pod(limits={"cpu": "1", "memory": "4Gi"})
    cluster(_engine_cluster(small))
    got = checks(run(run_test=TEST_ID))["engine-size"]
    assert got["status"] == FAIL and "not the configured 2" in got["detail"]


def test_a_test_that_does_not_run_here_is_not_started(cluster):
    cluster(healthy_cluster())
    account = FakeAccount(test={"name": "cloud test", "executions": [
        {"locations": {"us-east4-a": 1}}]})
    doc = run(account, run_test=TEST_ID)
    target = checks(doc)["test-target"]
    assert target["status"] == FAIL and "us-east4-a" in target["detail"]
    assert "Load Distribution" in target["fix"] and "never" in target["detail"]
    assert ("start_test", TEST_ID) not in account.calls
    assert not any(c[0] == "update_test" for c in account.calls)


def test_a_script_test_s_target_is_unread_and_the_run_goes_ahead(cluster):
    cluster(_engine_cluster(engine_pod()))
    account = FakeAccount(test={"name": "taurus script", "executions": []})
    doc = run(account, run_test=TEST_ID)
    assert status(doc)["test-target"] == smoke.UNREAD
    assert ("start_test", TEST_ID) in account.calls


def test_no_test_starts_on_an_agent_that_already_failed(cluster):
    cluster(healthy_cluster())
    account = FakeAccount(heartbeat_age=900)
    doc = run(account, run_test=TEST_ID)
    got = checks(doc)["engine-run"]
    assert got["status"] == smoke.SKIP and "agent" in got["detail"]
    assert ("start_test", TEST_ID) not in account.calls


def test_an_engine_that_never_appears_fails_and_the_run_is_stopped(cluster):
    cluster(healthy_cluster())
    account = FakeAccount(masters=("BOOT_STARTING",))
    doc = run(account, run_test=TEST_ID, engine_timeout=30)
    assert status(doc)["engine-pod"] == FAIL
    assert ("stop_master", 9001) in account.calls
    assert doc["triage"] is not None


def test_a_run_that_does_not_end_in_time_is_stopped(cluster):
    cluster(_engine_cluster(engine_pod()))
    account = FakeAccount(masters=("RUNNING",))
    doc = run(account, run_test=TEST_ID, run_timeout=60)
    got = checks(doc)["run-status"]
    assert got["status"] == FAIL and "RUNNING" in got["detail"]
    assert ("stop_master", 9001) in account.calls


def test_a_refused_start_names_the_busy_slots(cluster):
    cluster(healthy_cluster())
    account = FakeAccount(start_error=api.BzmApiError(
        "POST /tests/4242/start -> HTTP 403: Not enough available resources",
        status=403))
    got = checks(run(account, run_test=TEST_ID))["engine-run"]
    assert got["status"] == FAIL and "slot" in got["fix"]


@pytest.mark.parametrize("events,want", [
    (["Taurus completed (Exit: 1)"], FAIL),
    (["Status changed to ENDED (140)"], smoke.UNREAD),
])
def test_the_taurus_exit_code(cluster, events, want):
    cluster(_engine_cluster(engine_pod()))
    doc = run(FakeAccount(events=events), run_test=TEST_ID)
    assert status(doc)["engine-exit"] == want


def test_a_run_with_no_samples_fails(cluster):
    cluster(_engine_cluster(engine_pod()))
    doc = run(FakeAccount(summary={"hits": 0, "avg": None, "failed": 0}),
              run_test=TEST_ID)
    assert status(doc)["run-samples"] == FAIL


def test_an_engine_from_the_public_registry_fails_under_a_private_one(cluster):
    account = FakeAccount()
    keys = [i["key"] for i in facts.select_images(core.gather_facts(account, HARBOR))]
    data = cm_data(DOCKER_REGISTRY="registry.corp/bzm", IMAGE_OVERRIDES=json.dumps(
        {k: f"registry.corp/bzm/{k}" for k in keys}))
    answers = _engine_cluster(engine_pod())
    answers["get configmap blazemeter-configmap"] = obj({"data": data})
    answers["get deployments"] = items(deployment(image="registry.corp/bzm/crane:1"))
    cluster(answers)
    doc = run(account, run_test=TEST_ID)
    assert status(doc)["registry"] == PASS
    got = checks(doc)["engine-config"]
    assert got["status"] == FAIL and "not from the private registry" in got["detail"]


# -- the front doors ------------------------------------------------------------------

@pytest.mark.parametrize("kw", [{"engine_timeout": 0}, {"run_timeout": "10"},
                                {"run_timeout": True}])
def test_core_refuses_bad_timeouts(kw):
    with pytest.raises(core.BadRequest):
        core.smoke(FakeAccount(), NS, cli="kubectl", **kw)


def test_core_refuses_no_namespace():
    with pytest.raises(core.BadRequest):
        core.smoke(FakeAccount(), "", cli="kubectl")


def _cli(monkeypatch, account, *args):
    monkeypatch.setattr(core, "client_from_key", lambda *a, **k: account)
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", "smoke", *args])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_cli_exits_0_for_a_healthy_agent(cluster, monkeypatch, capsys):
    cluster(healthy_cluster())
    assert _cli(monkeypatch, FakeAccount(), "-n", NS) == 0
    out = capsys.readouterr().out
    assert "PASS    agent" in out and "no failures" in out


def test_cli_exits_1_for_a_failure_and_prints_the_fix_and_triage(cluster, monkeypatch,
                                                                 capsys):
    cluster(healthy_cluster())
    assert _cli(monkeypatch, FakeAccount(heartbeat_age=900), "-n", NS) == 1
    out = capsys.readouterr().out
    assert "FAIL    agent" in out and "fix:" in out
    assert "triage, because a check failed" in out


def test_cli_exits_0_when_reads_were_refused(cluster, monkeypatch, capsys):
    cluster(healthy_cluster(**{"get deployments": refused("deployments")}))
    assert _cli(monkeypatch, FakeAccount(), "-n", NS) == 0
    assert "UNREAD  crane-deployment" in capsys.readouterr().out


def test_cli_json_stays_json_through_an_engine_run(cluster, monkeypatch, capsys):
    cluster(_engine_cluster(engine_pod()))
    assert _cli(monkeypatch, FakeAccount(), "-n", NS, "--json",
                "--run-test", str(TEST_ID)) == 0
    got = capsys.readouterr()
    doc = json.loads(got.out)
    assert doc["ok"] is True and doc["stages"][-1]["stage"] == "engine run"
    assert "Starting test" in got.err


def test_the_cli_announces_the_run_before_it_starts(cluster, monkeypatch, capsys):
    cluster(_engine_cluster(engine_pod()))
    _cli(monkeypatch, FakeAccount(), "-n", NS, "--run-test", str(TEST_ID))
    out = capsys.readouterr().out
    assert out.index("Starting test") < out.index("started test")
