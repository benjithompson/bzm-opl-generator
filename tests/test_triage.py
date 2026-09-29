"""triage: a deployed namespace's events, pods and crane log, matched against
the rule table. Every rule has a realistic fixture here; the cluster is faked at
kube.quiet / kube.kget_named, never reached."""

import datetime
import json
import subprocess

import pytest

from bzm_opl_gen import cli, core, kube, options, triage
from bzm_opl_gen.verdict import FAIL, WARN

NOW = datetime.datetime(2026, 9, 29, 12, 0, tzinfo=datetime.timezone.utc)
NS = "bzm"
CRANE = "crane-5d4f8b7c9-x2x7q"
ENGINE = "r-v4-6512ab3f-0-0-c-7xk2p"
CRANE_IMAGE = "registry.corp/blazemeter/crane:latest-master"
ENGINE_IMAGE = "registry.corp/blazemeter/v4:1.2.3"


def _ts(minutes_ago):
    return (NOW - datetime.timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def event(reason, message, kind="Pod", name=CRANE, type="Warning", count=1,
          field_path=None, minutes_ago=5):
    obj = {"kind": kind, "name": name, "namespace": NS}
    if field_path:
        obj["fieldPath"] = field_path
    return {"kind": "Event", "type": type, "reason": reason, "message": message,
            "involvedObject": obj, "count": count,
            "lastTimestamp": _ts(minutes_ago),
            "metadata": {"name": f"{name}.1", "namespace": NS}}


def pod(name=CRANE, image=CRANE_IMAGE, container="crane", labels=None,
        state=None, last_state=None, restarts=0, phase="Running", reason=None,
        message=None, unschedulable=None):
    status = {"phase": phase, "containerStatuses": [{
        "name": container, "image": image, "restartCount": restarts,
        "state": state or {"running": {"startedAt": _ts(30)}},
        "lastState": last_state or {}}]}
    if reason:
        status["reason"], status["message"] = reason, message
    if unschedulable:
        status["conditions"] = [{"type": "PodScheduled", "status": "False",
                                 "reason": "Unschedulable",
                                 "message": unschedulable}]
    return {"metadata": {"name": name, "namespace": NS,
                         "labels": labels if labels is not None
                         else ({"role": "role-crane"} if name == CRANE else {})},
            "spec": {"containers": [{"name": container, "image": image}]},
            "status": status}


def engine(**kw):
    return pod(name=ENGINE, image=ENGINE_IMAGE, container="jmeter", **kw)


def run(events=(), pods=None, log="", previous=None, namespace_obj=None,
        unread=(), since="1h"):
    """evaluate() over what a gather would have returned."""
    pods = [pod()] if pods is None else pods
    logs = [triage.LogRead(CRANE, False, log, None)]
    if previous is not None:
        logs.append(triage.LogRead(CRANE, True, previous, None))
    gathered = triage.Gathered(
        {"metadata": {"name": NS}} if namespace_obj is None else namespace_obj,
        None if events is None else list(events), pods, logs, list(unread))
    return triage.as_dict(triage.evaluate(gathered, NS, since, now=NOW))


def rules(doc):
    return [f["rule"] for f in doc["findings"]]


def finding(doc, rule):
    hits = [f for f in doc["findings"] if f["rule"] == rule]
    assert len(hits) == 1, (rule, rules(doc), doc["unrecognised"])
    return hits[0]


# -- one realistic fixture per rule --------------------------------------------

def _pull_event(tail, image=CRANE_IMAGE):
    return event("Failed", f'Failed to pull image "{image}": rpc error: code = '
                           f'Unknown desc = failed to pull and unpack image '
                           f'"{image}": failed to resolve reference "{image}": '
                           f'{tail}', field_path="spec.containers{crane}")


def _log(line):
    return {"log": "2026-09-29 11:58:01,404 INFO starting agent\n" + line}


# As the Kubernetes client logs it: the body is JSON, so inner quotes are \".
API_ERR = ('kubernetes.client.exceptions.ApiException: (403) Reason: Forbidden '
           'HTTP response body: {"kind":"Status","message":"%s",'
           '"reason":"Forbidden","code":403}')

# rule id -> (evaluate() arguments, expected subject)
CASES = {
    "namespace-missing": (dict(pods=[], namespace_obj={}), None),
    "crane-missing": (dict(pods=[engine()]), None),
    "image-pull-registry-tls": (dict(events=[_pull_event(
        'failed to do request: Head "https://registry.corp/v2/blazemeter/crane/'
        'manifests/latest-master": tls: failed to verify certificate: x509: '
        'certificate signed by unknown authority')]), CRANE_IMAGE),
    "image-pull-auth": (dict(events=[_pull_event(
        "pulling from host registry.corp failed with status code "
        "[manifests latest-master]: 401 Unauthorized")]), CRANE_IMAGE),
    "image-not-found": (dict(pods=[pod(), engine(state={"waiting": {
        "reason": "ErrImagePull",
        "message": f"rpc error: code = NotFound desc = failed to pull and "
                   f"unpack image \"{ENGINE_IMAGE}\": failed to resolve "
                   f"reference \"{ENGINE_IMAGE}\": {ENGINE_IMAGE}: not found"}})]),
        ENGINE_IMAGE),
    "image-pull-unreachable": (dict(events=[_pull_event(
        'failed to do request: Head "https://registry.corp/v2/": dial tcp: '
        'lookup registry.corp on 10.96.0.10:53: no such host')]), CRANE_IMAGE),
    "image-pull": (dict(events=[event(
        "BackOff", f'Back-off pulling image "{ENGINE_IMAGE}"', name=ENGINE,
        field_path="spec.containers{jmeter}", count=7)]), ENGINE_IMAGE),
    "missing-reference": (dict(events=[event(
        "FailedMount", 'MountVolume.SetUp failed for volume "ca-bundle" : '
                       'configmap "corp-ca" not found')]), "configmap corp-ca"),
    "tls-trust": (_log(
        "requests.exceptions.SSLError: HTTPSConnectionPool(host='a.blazemeter.com'"
        ", port=443): Max retries exceeded with url: /api/v4/ships/abc/status "
        "(Caused by SSLError(SSLCertVerificationError(1, '[SSL: "
        "CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
        "local issuer certificate (_ssl.c:1006)')))"), None),
    "proxy-kube-api": (_log(
        "urllib3.exceptions.MaxRetryError: HTTPSConnectionPool(host='10.96.0.1', "
        "port=443): Max retries exceeded with url: /api/v1/namespaces/bzm/pods "
        "(Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel "
        "connection failed: 403 Forbidden')))"), None),
    "proxy-auth": (_log(
        "requests.exceptions.ProxyError: HTTPSConnectionPool(host="
        "'a.blazemeter.com', port=443): Max retries exceeded with url: "
        "/api/v4/ships/abc/status (Caused by ProxyError('Unable to connect to "
        "proxy', OSError('Tunnel connection failed: 407 Proxy Authentication "
        "Required')))"), None),
    "proxy-unreachable": (_log(
        "requests.exceptions.ProxyError: HTTPSConnectionPool(host="
        "'a.blazemeter.com', port=443): Max retries exceeded with url: "
        "/api/v4/ships/abc/status (Caused by ProxyError('Unable to connect to "
        "proxy', NewConnectionError('<urllib3.connection.HTTPSConnection object "
        "at 0x7f2a>: Failed to establish a new connection: [Errno 111] "
        "Connection refused')))"), None),
    "egress-blocked": (_log(
        "requests.exceptions.ConnectTimeout: HTTPSConnectionPool(host="
        "'a.blazemeter.com', port=443): Max retries exceeded with url: "
        "/api/v4/ships/abc/status (Caused by ConnectTimeoutError(<urllib3."
        "connection.HTTPSConnection object at 0x7f2a>, 'Connection to "
        "a.blazemeter.com timed out. (connect timeout=30)'))"), None),
    "auth-token": (_log(
        "requests.exceptions.HTTPError: 404 Client Error: Not Found for url: "
        "https://a.blazemeter.com/api/v4/ships/abc/status"), None),
    "disk-pressure": (dict(events=[event(
        "FailedScheduling", "0/1 nodes are available: 1 node(s) had untolerated "
        "taint {node.kubernetes.io/disk-pressure: }. preemption: 0/1 nodes are "
        "available: 1 Preemption is not helpful for scheduling.", name=ENGINE)]),
        None),
    "schedule-taint": (dict(pods=[pod(), engine(
        phase="Pending", unschedulable="0/3 nodes are available: 3 node(s) had "
        "untolerated taint {dedicated: engines}. preemption: 0/3 nodes are "
        "available: 3 Preemption is not helpful for scheduling.")]),
        "dedicated: engines"),
    "schedule-resources": (dict(events=[event(
        "FailedScheduling", "0/3 nodes are available: 3 Insufficient cpu, 2 "
        "Insufficient memory. preemption: 0/3 nodes are available: 3 No "
        "preemption victims found for incoming pod.", name=ENGINE, count=4)]),
        "cpu, memory"),
    "schedule-selector": (dict(events=[event(
        "FailedScheduling", "0/3 nodes are available: 3 node(s) didn't match "
        "Pod's node affinity/selector. preemption: 0/3 nodes are available: 3 "
        "Preemption is not helpful for scheduling.", name=ENGINE)]), None),
    "quota": (dict(events=[event(
        "FailedCreate", 'Error creating: pods "crane-5d4f8b7c9-abcde" is '
        'forbidden: exceeded quota: compute, requested: limits.cpu=1, used: '
        'limits.cpu=8, limited: limits.cpu=8', kind="ReplicaSet",
        name="crane-5d4f8b7c9")]), "compute"),
    "pod-security": (dict(events=[event(
        "FailedCreate", 'Error creating: pods "crane-5d4f8b7c9-abcde" is '
        'forbidden: violates PodSecurity "restricted:latest": '
        'allowPrivilegeEscalation != false (container "crane" must set '
        'securityContext.allowPrivilegeEscalation=false), runAsNonRoot != true '
        '(pod or container "crane" must set securityContext.runAsNonRoot=true)',
        kind="ReplicaSet", name="crane-5d4f8b7c9")]), "restricted:latest"),
    "openshift-scc": (dict(events=[event(
        "FailedCreate", 'Error creating: pods "crane-5d4f8b7c9-" is forbidden: '
        'unable to validate against any security context constraint: '
        '[provider "anyuid": Forbidden: not usable by user or serviceaccount, '
        'spec.containers[0].securityContext.runAsUser: Invalid value: 1337: '
        'must be in the ranges: [1000650000, 1000659999]]',
        kind="ReplicaSet", name="crane-5d4f8b7c9")]), None),
    "admission-webhook": (dict(events=[event(
        "FailedCreate", 'Error creating: admission webhook '
        '"validation.gatekeeper.sh" denied the request: [allowed-repos] '
        'container <crane> has an invalid image repo <gcr.io/verdant-bulwark-'
        '278/blazemeter/crane:latest-master>, allowed repos are '
        '["registry.corp/"]', kind="ReplicaSet", name="crane-5d4f8b7c9")]),
        "validation.gatekeeper.sh, policy allowed-repos"),
    "rbac": (_log(API_ERR % (
        'pods is forbidden: User \\"system:serviceaccount:bzm:bzm-crane\\" '
        'cannot create resource \\"pods\\" in API group \\"\\" in the '
        'namespace \\"bzm\\"')),
        "system:serviceaccount:bzm:bzm-crane cannot create pods"),
    "oom-engine": (dict(pods=[pod(), engine(phase="Failed", state={
        "terminated": {"reason": "OOMKilled", "exitCode": 137,
                       "finishedAt": _ts(3)}})]), "engine"),
    "oom": (dict(pods=[pod(restarts=2, last_state={"terminated": {
        "reason": "OOMKilled", "exitCode": 137, "finishedAt": _ts(10)}})]),
        "crane"),
    "evicted": (dict(pods=[pod(), engine(
        phase="Failed", reason="Evicted",
        message="The node was low on resource: ephemeral-storage. Threshold "
                "quantity: 10Gi, available: 9Gi. Container jmeter was using "
                "38Gi, request is 0, has larger consumption of "
                "ephemeral-storage.")]), "ephemeral-storage"),
    "crash-loop": (dict(pods=[pod(restarts=6, state={"waiting": {
        "reason": "CrashLoopBackOff",
        "message": f"back-off 5m0s restarting failed container=crane "
                   f"pod={CRANE}_bzm(1a2b)"}})]), "crane"),
}


def test_every_rule_has_a_fixture():
    assert set(CASES) == set(triage.RULES_BY_ID)


@pytest.mark.parametrize("rule_id", list(CASES))
def test_each_rule_names_its_failure_and_subject(rule_id):
    kwargs, subject = CASES[rule_id]
    doc = run(**kwargs)
    f = finding(doc, rule_id)
    assert f["subject"] == subject
    assert f["status"] == triage.RULES_BY_ID[rule_id].status
    assert f["evidence"] and f["fix"] and f["objects"]
    assert doc["ok"] is (f["status"] != FAIL)


def test_rule_ids_are_unique():
    ids = [r.id for r in triage.RULES]
    assert len(ids) == len(set(ids))


def test_every_option_a_fix_names_is_a_real_option():
    for rule in triage.RULES:
        missing = [o for o in rule.options if o not in options.BY_NAME]
        assert not missing, (rule.id, missing)
        for o in rule.options:
            assert o in rule.fix or o in rule.finding, (rule.id, o)


def test_finding_and_fix_text_is_plain_prose():
    for rule in triage.RULES:
        for text in (rule.finding, rule.fix, rule.title):
            assert "`" not in text and "->" not in text and "**" not in text, rule.id


# -- families and the generic image rule ---------------------------------------

def test_a_certificate_failure_through_the_proxy_is_one_tls_finding():
    doc = run(**CASES["tls-trust"][0])
    assert rules(doc) == ["tls-trust"]


def test_a_timestamp_that_contains_404_is_not_an_auth_failure():
    doc = run(log="2026-09-29 10:00:00,404 INFO GET "
                  "https://a.blazemeter.com/api/v4/ships/abc/status 200")
    assert rules(doc) == [] and doc["unrecognised"] == []


def test_the_generic_pull_finding_gives_way_to_the_specific_one():
    """ErrImagePull and BackOff events name no cause; the Failed event beside
    them does, so the specific finding stands alone."""
    events = CASES["image-pull-auth"][0]["events"] + [
        event("Failed", "Error: ErrImagePull", field_path="spec.containers{crane}",
              count=3),
        event("BackOff", f'Back-off pulling image "{CRANE_IMAGE}"',
              field_path="spec.containers{crane}", count=9)]
    doc = run(events=events)
    assert rules(doc) == ["image-pull-auth"]
    f = finding(doc, "image-pull-auth")
    assert f["subject"] == CRANE_IMAGE
    assert f["count"] == 13                        # the causeless repeats fold in
    assert "401 Unauthorized" in f["evidence"]


def test_a_generic_pull_of_another_image_stays_its_own_finding():
    events = CASES["image-pull-auth"][0]["events"] + CASES["image-pull"][0]["events"]
    doc = run(events=events)
    assert rules(doc) == ["image-pull-auth", "image-pull"]
    assert finding(doc, "image-pull")["subject"] == ENGINE_IMAGE


def test_an_event_names_the_image_through_its_pod_when_its_text_does_not():
    doc = run(events=[event("Failed", "Error: ImagePullBackOff",
                            field_path="spec.containers{crane}")])
    assert finding(doc, "image-pull")["subject"] == CRANE_IMAGE


def test_one_scheduling_message_with_two_causes_gives_two_findings():
    doc = run(events=[event(
        "FailedScheduling", "0/3 nodes are available: 1 node(s) had untolerated "
        "taint {node-role.kubernetes.io/control-plane: }, 2 Insufficient cpu. "
        "preemption: 0/3 nodes are available: 1 Preemption is not helpful for "
        "scheduling, 2 No preemption victims found for incoming pod.",
        name=ENGINE)])
    assert rules(doc) == ["schedule-taint", "schedule-resources"]
    assert finding(doc, "schedule-taint")["subject"] == \
        "node-role.kubernetes.io/control-plane:"


def test_an_event_and_the_pod_condition_for_one_cause_are_one_finding():
    message = "0/2 nodes are available: 2 Insufficient memory."
    doc = run(events=[event("FailedScheduling", message, name=ENGINE, count=3)],
              pods=[pod(), engine(phase="Pending", unschedulable=message)])
    f = finding(doc, "schedule-resources")
    assert f["count"] == 4 and f["objects"] == [f"Pod/{ENGINE}"]


def test_quota_refusal_crane_logs_for_an_engine_is_found():
    """Crane creates engine pods itself, so their refusal is in its log."""
    doc = run(log=API_ERR % (
        'pods \\"r-v4-6512ab-0-0-c-7xk2p\\" is forbidden: failed '
        'quota: bzm-quota: must specify limits.cpu for: jmeter'))
    assert rules(doc) == ["quota"]
    assert finding(doc, "quota")["subject"] == "bzm-quota"


def test_kyverno_names_its_policy():
    doc = run(events=[event(
        "FailedCreate", 'Error creating: admission webhook '
        '"validate.kyverno.svc-fail" denied the request: \n\nresource '
        'Pod/bzm/crane-abc was blocked due to the following policies '
        '\n\ndisallow-privilege-escalation:\n  privilege-escalation: '
        "'validation error: Privilege escalation is disallowed.'",
        kind="ReplicaSet", name="crane-5d4f8b7c9")])
    assert finding(doc, "admission-webhook")["subject"] == \
        "validate.kyverno.svc-fail, policy disallow-privilege-escalation"


def test_a_crash_in_the_previous_run_is_read_from_the_previous_log():
    doc = run(previous=CASES["tls-trust"][0]["log"])
    f = finding(doc, "tls-trust")
    assert f["objects"] == [f"Pod/{CRANE} (previous run)"]


def test_findings_come_before_warnings():
    crash = CASES["crash-loop"][0]["pods"]
    doc = run(pods=crash, **CASES["tls-trust"][0])
    assert [f["status"] for f in doc["findings"]] == [FAIL, WARN]


def test_a_missing_namespace_is_not_also_a_missing_crane():
    doc = run(pods=[], namespace_obj={})
    assert rules(doc) == ["namespace-missing"]


def test_an_unreadable_namespace_object_is_neither_missing_nor_crane_less():
    """None: the Namespace object could not be read. An empty pod list still
    says crane is not there."""
    doc = run(pods=[pod(name="something-else", labels={})], namespace_obj=None)
    assert "namespace-missing" not in rules(doc)


# -- what no rule knows -----------------------------------------------------------

def test_unknown_warnings_are_listed_grouped_and_counted():
    sandbox = ('Failed to create pod sandbox: rpc error: code = Unknown desc = '
               'failed to setup network for sandbox "%s": plugin type="calico" '
               'failed (add): error getting ClusterInformation: connection is '
               'unauthorized: Unauthorized')
    doc = run(events=[
        event("FailedCreatePodSandBox", sandbox % "3f9a1c2b7d", name=ENGINE,
              count=2),
        event("FailedCreatePodSandBox", sandbox % "8e7d6c5b4a",
              name="r-v4-6512ab3f-0-0-c-9zq1w", count=3),
        event("Pulled", "Successfully pulled image", type="Normal"),
    ], log="2026-09-29 11:58:02,120 ERROR ship status loop: KeyError: 'slots'\n"
           "2026-09-29 11:58:03,000 INFO heartbeat sent")
    assert rules(doc) == []
    by_reason = {u["reason"]: u for u in doc["unrecognised"]}
    sandbox_group = by_reason["FailedCreatePodSandBox"]
    assert sandbox_group["count"] == 5 and len(sandbox_group["objects"]) == 2
    assert by_reason[""]["source"] == "log"
    assert "KeyError" in by_reason[""]["example"]
    assert len(doc["unrecognised"]) == 2           # the Normal and INFO lines are not
    assert doc["ok"] is True


def test_an_unmatched_container_state_is_listed_not_dropped():
    doc = run(pods=[pod(state={"waiting": {
        "reason": "CreateContainerError",
        "message": "container create failed: time=\"x\" level=error"}})])
    assert doc["unrecognised"][0]["reason"] == "CreateContainerError"


def test_events_older_than_since_are_left_out():
    old = CASES["quota"][0]["events"][0] | {"lastTimestamp": _ts(90)}
    assert rules(run(events=[old])) == []
    assert rules(run(events=[old], since="2h")) == ["quota"]


def test_an_event_series_uses_its_last_observed_time():
    e = CASES["quota"][0]["events"][0] | {
        "lastTimestamp": None, "eventTime": "2026-09-29T09:00:00.123456789Z",
        "series": {"count": 12, "lastObservedTime": "2026-09-29T11:59:00.000000Z"}}
    assert finding(run(events=[e]), "quota")["count"] == 12


@pytest.mark.parametrize("text,seconds", [
    ("1h", 3600), ("30m", 1800), ("1h30m", 5400), ("90s", 90), ("2d", 172800)])
def test_since_accepts_durations(text, seconds):
    assert triage.parse_since(text) == seconds


@pytest.mark.parametrize("text", ["", "1", "h", "1x", "-1h", "0m", "1h 30m"])
def test_since_refuses_anything_else(text):
    with pytest.raises(ValueError):
        triage.parse_since(text)


# -- unread is never empty ---------------------------------------------------------

def test_a_denied_events_read_is_unread_and_not_a_failure():
    doc = run(events=None, unread=[("events", "Error from server (Forbidden): "
                                    "events is forbidden: User \"alice\" cannot "
                                    "list resource \"events\"")])
    assert doc["unread"][0]["section"] == "events"
    assert rules(doc) == [] and doc["ok"] is True
    assert "1 section unread" in doc["summary"]
    assert not any(r.startswith("events") for r in doc["read"])


class FakeKubectl:
    """kube.quiet by argv: each read answers from a table, or is refused."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, timeout=None):
        self.calls.append(cmd)
        verb = cmd[3] if cmd[3] != "get" else f"get {cmd[4]}"
        key = verb + (" previous" if "--previous" in cmd else "")
        rc, out = self.answers.get(key, (1, "no fake for " + key))
        return subprocess.CompletedProcess(cmd, rc, out if rc == 0 else "",
                                           out if rc else "")


def _gather(monkeypatch, answers, ns_obj=None):
    fake = FakeKubectl(answers)
    monkeypatch.setattr(kube, "quiet", fake)
    monkeypatch.setattr(kube, "kget_named", lambda *a, **k: ns_obj)
    return triage.gather("kubectl", NS, 3600, 200), fake


FORBIDDEN = ('Error from server (Forbidden): events is forbidden: User '
             '"system:serviceaccount:ops:viewer" cannot list resource "events" '
             'in API group "" in the namespace "bzm"')


def test_gather_reads_each_section_and_keeps_a_refusal_as_unread(monkeypatch):
    crane = pod(restarts=1)
    g, fake = _gather(monkeypatch, {
        "get events": (1, FORBIDDEN),
        "get pods": (0, json.dumps({"items": [crane, engine()]})),
        "logs": (0, "line one\nline two"),
        "logs previous": (1, 'Error from server (BadRequest): previous '
                             'terminated container "crane" not found'),
    }, ns_obj={"metadata": {"name": NS}})
    assert g.events is None and len(g.pods) == 2
    assert [(r.pod, r.previous, r.text) for r in g.logs] == [
        (CRANE, False, "line one\nline two"), (CRANE, True, None)]
    assert [s for s, _ in g.unread] == ["events",
                                        f"crane log ({CRANE}, previous run)"]
    assert "Forbidden" in g.unread[0][1]
    # The refusal of triage's own read is not crane's RBAC failure.
    doc = triage.as_dict(triage.evaluate(g, NS, now=NOW))
    assert "rbac" not in rules(doc) and doc["ok"] is True
    logs = [c for c in fake.calls if c[3] == "logs"]
    assert all(c[4] == CRANE for c in logs)
    assert "--since=3600s" in logs[0] and "--previous" in logs[1]
    assert all("--tail=200" in c for c in logs)
    assert all(any(a.startswith("--request-timeout=") for a in c)
               for c in fake.calls)


def test_gather_reads_crane_by_its_deployment_when_pods_are_unread(monkeypatch):
    g, fake = _gather(monkeypatch, {"get events": (0, '{"items": []}'),
                                    "get pods": (1, "forbidden"),
                                    "logs": (0, "hello")})
    assert g.pods is None and g.events == []
    assert [r.pod for r in g.logs] == ["crane"]
    assert any("deploy/crane" in c for c in fake.calls)
    doc = triage.as_dict(triage.evaluate(g, NS, now=NOW))
    assert "crane-missing" not in rules(doc)


def test_gather_calls_an_empty_namespace_it_cannot_confirm_unread(monkeypatch):
    g, _ = _gather(monkeypatch, {"get events": (0, '{"items": []}'),
                                 "get pods": (0, '{"items": []}')}, ns_obj=None)
    assert ("namespace" in [s for s, _ in g.unread])
    doc = triage.as_dict(triage.evaluate(g, NS, now=NOW))
    assert rules(doc) == ["crane-missing"]


def test_gather_sees_a_missing_namespace(monkeypatch):
    g, _ = _gather(monkeypatch, {"get events": (0, '{"items": []}'),
                                 "get pods": (0, '{"items": []}')}, ns_obj={})
    assert rules(triage.as_dict(triage.evaluate(g, NS, now=NOW))) == \
        ["namespace-missing"]


def test_a_timeout_is_unread(monkeypatch):
    def slow(cmd, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(kube, "quiet", slow)
    monkeypatch.setattr(kube, "kget_named", lambda *a, **k: None)
    g = triage.gather("kubectl", NS, 3600, 200)
    assert g.events is None and g.pods is None
    assert all("did not answer" in why for _, why in g.unread)


def test_unparseable_output_is_unread(monkeypatch):
    g, _ = _gather(monkeypatch, {"get events": (0, "Warning: a plugin said hi"),
                                 "get pods": (0, '{"items": []}')})
    assert g.events is None and "unparseable" in dict(g.unread)["events"]


# -- core, the CLI and the summary ---------------------------------------------------

def test_core_with_no_kubectl_reads_nothing_and_says_so(monkeypatch):
    def none():
        raise RuntimeError("neither oc nor kubectl found on PATH")
    monkeypatch.setattr(kube, "cli_tool", none)
    doc = core.triage(NS, now=NOW)
    assert doc["read"] == [] and doc["ok"] is True
    assert doc["unread"] == [{"section": "cluster",
                              "detail": "neither oc nor kubectl found on PATH"}]
    assert "Nothing could be read" in doc["summary"]


@pytest.mark.parametrize("kwargs", [{"since": "soon"}, {"log_lines": 0},
                                    {"log_lines": "10"}])
def test_core_refuses_bad_arguments(kwargs):
    with pytest.raises(core.BadRequest):
        core.triage(NS, cli="kubectl", **kwargs)


def test_core_refuses_no_namespace():
    with pytest.raises(core.BadRequest):
        core.triage("", cli="kubectl")


def _fake_cluster(monkeypatch, answers, ns_obj=None):
    monkeypatch.setattr(kube, "quiet", FakeKubectl(answers))
    monkeypatch.setattr(kube, "kget_named", lambda *a, **k: ns_obj or {"metadata": {}})
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")


def _cli(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", "triage", *args])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_cli_exits_1_on_a_known_failure_and_prints_the_fix(monkeypatch, capsys):
    _fake_cluster(monkeypatch, {
        "get events": (0, json.dumps({"items": CASES["image-not-found"][0].get(
            "events", [])})),
        "get pods": (0, json.dumps({"items": CASES["image-not-found"][0]["pods"]})),
        "logs": (0, CASES["tls-trust"][0]["log"])})
    assert _cli(monkeypatch, "-n", NS) == 1
    out = capsys.readouterr().out
    assert "FAIL  image-not-found" in out and ENGINE_IMAGE in out
    assert "FAIL  tls-trust" in out and "ca_existing_configmap" in out
    assert "2 known failures" in out


def test_cli_exits_0_when_only_reads_were_denied(monkeypatch, capsys):
    _fake_cluster(monkeypatch, {"get events": (1, FORBIDDEN),
                                "get pods": (0, json.dumps({"items": [pod()]})),
                                "logs": (0, "heartbeat sent")})
    assert _cli(monkeypatch, "-n", NS) == 0
    out = capsys.readouterr().out
    assert "WARN  unread: events" in out and "1 section unread" in out


def test_cli_json_is_the_core_document(monkeypatch, capsys):
    _fake_cluster(monkeypatch, {"get events": (0, '{"items": []}'),
                                "get pods": (0, json.dumps({"items": [pod()]})),
                                "logs": (0, "")})
    assert _cli(monkeypatch, "-n", NS, "--json", "--since", "30m",
                "--crane-log-lines", "50") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["since"] == "30m" and doc["findings"] == []


def test_cli_reports_a_bad_since_as_a_sentence(monkeypatch):
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    code = _cli(monkeypatch, "-n", NS, "--since", "yesterday")
    assert "--since" in str(code)


def test_the_triage_doc_names_every_rule():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs",
                        "triage.md")
    with open(path, encoding="utf-8") as fh:
        doc = fh.read()
    missing = [r.id for r in triage.RULES if f"| `{r.id}` |" not in doc]
    assert not missing, f"docs/triage.md has no row for {missing}"
