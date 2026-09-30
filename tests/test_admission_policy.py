"""Admission policy engines in the doctor: Kyverno, Gatekeeper,
ValidatingAdmissionPolicy and other webhooks, judged against the pods a bundle
makes. Only enforcing policies that reach the namespace count; a policy
engine that is not installed is a PASS, one nobody could read is a WARN."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import admission_policy, doctor, evidence, kube  # noqa: E402
from bzm_opl_gen import facts as facts_mod  # noqa: E402
import policy_fixtures as P  # noqa: E402
from evidence_fixtures import EMPTY_LIST, document, raw  # noqa: E402
from test_doctor import (LR_MATCHING, NS_BASELINE, _big,  # noqa: E402
                         _find, _statuses)

HERE = os.path.dirname(__file__)
with open(os.path.join(HERE, "..", "examples", "facts.example.json")) as fh:
    # A location whose image list pins every image to a release.
    PINNED = {**json.load(fh), "slots": 2, "threads_per_engine": 500}
# Hand-entered facts: catalogue images, every tag `latest`.
FLOATING = {**facts_mod.manual("aaa111", "bbb222"), "slots": 2,
            "threads_per_engine": 500}
K8S = {"platform": "k8s"}


def _cluster(ns_obj=NS_BASELINE, limitranges=(), **policies):
    return {"nodes": [_big("a"), _big("b")], "ingressclasses": [],
            "limitranges": list(limitranges) if limitranges is not None
            else None, "quotas": [], "serviceaccounts": [],
            "namespace": ns_obj, **P.sections(**policies)}


def _checks(facts=PINNED, opts=K8S, namespace="blazemeter", **cluster):
    return doctor.evaluate(facts, opts, namespace,
                           cluster_data=_cluster(**cluster),
                           probes={doctor.API_PROBE_URL: 0})


def _policy(checks, name):
    return _find(checks, f"policy: {name}")


def _named(checks, prefix):
    return [c for c in checks if c.name.startswith(f"policy: {prefix}")]


# -- nothing installed, nothing readable -------------------------------------

def test_a_cluster_with_no_policy_engine_passes_every_policy_check():
    checks = _checks()
    for engine in ("Kyverno", "Gatekeeper", "ValidatingAdmissionPolicy",
                   "other webhooks"):
        assert _policy(checks, engine).status == doctor.PASS
    assert "not installed" in _policy(checks, "Kyverno").detail
    assert "not installed" in _policy(checks, "Gatekeeper").detail


def test_a_policy_engine_nobody_could_read_warns_and_never_fails():
    """Denied is not absent: every engine reports unverified, and a denied
    read never makes a FAIL."""
    unread = dict.fromkeys(P.sections())
    checks = doctor.evaluate(FLOATING, K8S, "blazemeter", probes={},
                             cluster_data={**_cluster(), **unread})
    for engine in ("Kyverno", "Gatekeeper", "ValidatingAdmissionPolicy",
                   "other webhooks"):
        c = _policy(checks, engine)
        assert c.status == doctor.WARN
        assert "could not be read" in c.detail
        assert "not installed" not in c.detail
    assert not any(c.status == doctor.FAIL and c.name.startswith("policy")
                   for c in checks)


def test_kyverno_absent_and_kyverno_unread_are_different_answers():
    absent = _policy(_checks(), "Kyverno")
    denied = doctor.check_kyverno(PINNED, K8S, {
        **_cluster(), "kyverno_clusterpolicies": None})[0]
    assert (absent.status, denied.status) == (doctor.PASS, doctor.WARN)
    assert absent.detail != denied.detail
    assert "list on clusterpolicies.kyverno.io" in denied.detail


def test_namespaced_kyverno_policies_that_could_not_be_read_are_named():
    """A namespaced token reads cluster policies elsewhere: the summary says
    what it did not see rather than claiming there is none."""
    checks = doctor.check_kyverno(PINNED, K8S, {
        **_cluster(kyverno=[]), "kyverno_policies": None})
    assert checks[0].status == doctor.WARN
    assert "Policies in namespace blazemeter could not be read" in \
        checks[0].detail


# -- Kyverno -----------------------------------------------------------------

def test_disallow_latest_in_enforce_fails_a_bundle_on_floating_tags():
    c = _policy(_checks(FLOATING, kyverno=[P.disallow_latest_tag()]),
                "Kyverno ClusterPolicy disallow-latest-tag")
    assert c.status == doctor.FAIL
    assert "blazemeter/crane:latest" in c.detail
    assert "blazemeter/v4:latest" in c.detail
    assert "PolicyException" in c.detail


def test_disallow_latest_passes_a_bundle_pinned_to_releases():
    c = _policy(_checks(PINNED, kyverno=[P.disallow_latest_tag()]),
                "Kyverno ClusterPolicy disallow-latest-tag")
    assert c.status == doctor.PASS
    assert "satisfy it" in c.detail


def test_crane_hook_is_named_for_its_latest_only_image():
    c = _policy(_checks(PINNED, {**K8S, "crane_hook": True},
                        kyverno=[P.disallow_latest_tag()]),
                "Kyverno ClusterPolicy disallow-latest-tag")
    assert c.status == doctor.WARN          # the hook, not the agent
    assert "cranehook:latest" in c.detail
    assert "crane_hook off" in c.detail


def test_a_policy_in_audit_mode_is_counted_and_not_judged():
    checks = _checks(FLOATING, kyverno=[P.disallow_latest_tag("Audit")])
    assert _named(checks, "Kyverno ClusterPolicy") == []
    summary = _policy(checks, "Kyverno")
    assert summary.status == doctor.PASS
    assert "Audit or warn only: 1" in summary.detail


def test_a_per_rule_enforce_overrides_a_policy_in_audit():
    """Kyverno 1.13 moved the action onto the rule."""
    policy = P.disallow_latest_tag("Audit")
    for rule in policy["spec"]["rules"]:
        rule["validate"]["failureAction"] = "Enforce"
    assert _policy(_checks(FLOATING, kyverno=[policy]),
                   "Kyverno ClusterPolicy").status == doctor.FAIL


def test_an_override_to_audit_for_this_namespace_is_honoured():
    policy = P.disallow_latest_tag()
    policy["spec"]["validationFailureActionOverrides"] = [
        {"action": "Audit", "namespaces": ["blaze*"]}]
    checks = _checks(FLOATING, kyverno=[policy])
    assert _named(checks, "Kyverno ClusterPolicy") == []


def test_a_policy_that_excludes_the_namespace_is_not_judged():
    policy = P.excluding(P.disallow_latest_tag(), ["blazemeter"])
    checks = _checks(FLOATING, kyverno=[policy])
    assert _named(checks, "Kyverno ClusterPolicy") == []
    assert "bundle's objects: 1" in _policy(checks, "Kyverno").detail


def test_scope_is_judged_for_the_namespace_being_preflighted():
    """-n on the command line decides the scope, not the profile's default."""
    policy = P.excluding(P.disallow_latest_tag(), ["team-*"])
    checks = _checks(FLOATING, namespace="team-a", kyverno=[policy])
    assert _named(checks, "Kyverno ClusterPolicy") == []
    checks = _checks(FLOATING, namespace="blazemeter", kyverno=[policy])
    assert _policy(checks, "Kyverno ClusterPolicy").status == doctor.FAIL


def test_a_namespace_selector_is_decided_from_the_namespaces_labels():
    policy = P.selecting(P.disallow_latest_tag(),
                         {"matchLabels": {"policy": "strict"}})
    labelled = {"metadata": {"name": "blazemeter",
                             "labels": {"policy": "strict"}}}
    assert _policy(_checks(FLOATING, ns_obj=labelled, kyverno=[policy]),
                   "Kyverno ClusterPolicy").status == doctor.FAIL
    assert _named(_checks(FLOATING, kyverno=[policy]),
                  "Kyverno ClusterPolicy") == []


@pytest.mark.parametrize("namespace", [None, {}], ids=["unread", "absent"])
def test_a_selector_over_an_unknown_namespace_may_apply_and_only_warns(
        namespace):
    policy = P.selecting(P.disallow_latest_tag(),
                         {"matchLabels": {"policy": "strict"}})
    c = _policy(_checks(FLOATING, ns_obj=namespace, kyverno=[policy]),
                "Kyverno ClusterPolicy")
    assert c.status == doctor.WARN
    assert "may not apply" in c.detail


def test_restrict_image_registries_compares_every_image_with_the_list():
    c = _policy(_checks(PINNED, kyverno=[P.restrict_image_registries()]),
                "Kyverno ClusterPolicy restrict-image-registries")
    assert c.status == doctor.FAIL
    assert "gcr.io/verdant-bulwark-278/blazemeter/crane:3.7.55" in c.detail
    assert "private_registry" in c.detail


def test_a_private_registry_the_policy_allows_passes():
    c = _policy(_checks(PINNED, {**K8S, "private_registry": "eu.foo.io/bzm"},
                        kyverno=[P.restrict_image_registries()]),
                "Kyverno ClusterPolicy restrict-image-registries")
    assert c.status == doctor.PASS


def test_require_requests_limits_warns_on_test_job_pods_only():
    """Crane and the engines declare both; crane's test-job pods declare none,
    so the verdict is theirs, and it is a WARN: what a refused housekeeping
    pod costs a run is unverified."""
    c = _policy(_checks(kyverno=[P.require_requests_limits()]),
                "Kyverno ClusterPolicy require-requests-limits")
    assert c.status == doctor.WARN
    assert "test-job" in c.detail
    assert "crane's own pod" not in c.detail
    assert "LimitRange" in c.detail


def test_limitrange_defaults_satisfy_a_requests_policy_for_test_job_pods():
    """LimitRanger fills defaults before a validating webhook judges a pod."""
    c = _policy(_checks(limitranges=[LR_MATCHING],
                        kyverno=[P.require_requests_limits()]),
                "Kyverno ClusterPolicy require-requests-limits")
    assert c.status == doctor.PASS


def test_unread_limitranges_leave_the_test_job_verdict_open():
    c = _policy(_checks(limitranges=None,
                        kyverno=[P.require_requests_limits()]),
                "Kyverno ClusterPolicy require-requests-limits")
    assert c.status == doctor.WARN
    assert "could not be read" in c.detail


def test_run_as_nonroot_fails_restricted_engines_and_says_why():
    """Engines carry no runAsNonRoot and no agent variable sets it, so a
    policy demanding it refuses every engine pod (measured under PSA)."""
    c = _policy(_checks(kyverno=[P.require_run_as_nonroot()]),
                "Kyverno ClusterPolicy require-run-as-nonroot")
    assert c.status == doctor.FAIL
    assert admission_policy.ENGINE_NO_RUN_AS_NON_ROOT in c.detail
    assert admission_policy.run_as_non_root_fix(
        "blazemeter", admission_policy.EXCEPTION["Kyverno"]) in c.detail
    assert "crane's own pod" not in c.detail      # crane sets it


def test_a_run_as_nonroot_cel_expression_fails_the_engines_too():
    c = _policy(_checks(admission=[P.vap(
        "non-root", "object.spec.containers.all(c, "
                    "c.securityContext.runAsNonRoot == true)")],
        bindings=[P.binding("non-root")]),
        "ValidatingAdmissionPolicy non-root")
    assert c.status == doctor.FAIL
    assert admission_policy.ENGINE_NO_RUN_AS_NON_ROOT in c.detail


def test_gatekeeper_must_run_as_non_root_accepts_the_engines_uid():
    """K8sPSPAllowedUsers MustRunAsNonRoot accepts a non-zero runAsUser, which
    the engines inherit from crane."""
    c = _policy(_checks(templates=P.TEMPLATES, constraints=[P.constraint(
        "K8sPSPAllowedUsers", "psp-pods-allowed-user-ranges",
        {"runAsUser": {"rule": "MustRunAsNonRoot"}})]),
        "Gatekeeper K8sPSPAllowedUsers")
    assert "engine pods" not in c.detail


def test_run_as_nonroot_fails_engines_without_restrict_engines():
    c = _policy(_checks(opts={**K8S, "restrict_engines": False},
                        kyverno=[P.require_run_as_nonroot()]),
                "Kyverno ClusterPolicy require-run-as-nonroot")
    assert c.status == doctor.FAIL
    assert "Turn restrict_engines back on" in c.detail


def test_privilege_escalation_passes_crane_and_restricted_engines():
    c = _policy(_checks(kyverno=[P.disallow_privilege_escalation()]),
                "Kyverno ClusterPolicy disallow-privilege-escalation")
    # Only crane's test-job pods are unknown.
    assert c.status == doctor.WARN
    assert "engine pods" not in c.detail and "crane's own pod" not in c.detail


def test_a_read_only_root_filesystem_is_a_failure_no_option_can_fix():
    c = _policy(_checks(kyverno=[P.require_ro_rootfs()]),
                "Kyverno ClusterPolicy require-ro-rootfs")
    assert c.status == doctor.FAIL
    assert "No option sets readOnlyRootFilesystem" in c.detail
    assert "PolicyException" in c.detail


def test_a_required_label_crane_does_not_carry_fails():
    c = _policy(_checks(kyverno=[P.require_labels()]),
                "Kyverno ClusterPolicy require-labels")
    assert c.status == doctor.FAIL
    assert "app.kubernetes.io/name" in c.detail
    assert "crane chooses the labels" in c.detail


def test_disallow_host_path_passes():
    c = _policy(_checks(kyverno=[P.disallow_host_path()]),
                "Kyverno ClusterPolicy disallow-host-path")
    assert c.status == doctor.PASS


@pytest.mark.parametrize("level,restricted,status", [
    ("baseline", True, doctor.PASS),
    ("restricted", True, doctor.FAIL),
    ("baseline", False, doctor.FAIL),
    ("restricted", False, doctor.FAIL),
])
def test_a_pod_security_rule_is_judged_by_its_level(level, restricted, status):
    c = _policy(_checks(opts={**K8S, "restrict_engines": restricted},
                        kyverno=[P.pod_security(level)]),
                "Kyverno ClusterPolicy")
    assert c.status == status


def test_an_unrecognised_enforcing_policy_is_named_for_review():
    c = _policy(_checks(kyverno=[P.unrecognised()]),
                "Kyverno ClusterPolicy require-pod-probes")
    assert c.status == doctor.WARN
    assert "does not recognise (validate-probes)" in c.detail
    assert "Liveness probes need to be configured" in c.detail


def test_a_namespaced_policy_in_the_target_namespace_is_judged():
    policy = P.disallow_latest_tag()
    policy["kind"] = "Policy"
    policy["metadata"]["namespace"] = "blazemeter"
    checks = _checks(FLOATING, kyverno=[], policies=[policy])
    assert _policy(checks, "Kyverno Policy disallow-latest-tag").status == \
        doctor.FAIL


# -- Gatekeeper ----------------------------------------------------------------

def test_allowed_repos_fails_the_public_registry():
    c = _policy(_checks(templates=P.TEMPLATES,
                        constraints=[P.allowed_repos()]),
                "Gatekeeper K8sAllowedRepos repo-is-corp")
    assert c.status == doctor.FAIL
    assert "registry.corp.example/" in c.detail
    assert "gcr.io/verdant-bulwark-278/blazemeter/crane" in c.detail


def test_allowed_repos_passes_a_mirror_under_an_allowed_prefix():
    c = _policy(_checks(opts={**K8S,
                              "private_registry": "registry.corp.example/bzm"},
                        templates=P.TEMPLATES,
                        constraints=[P.allowed_repos()]),
                "Gatekeeper K8sAllowedRepos")
    assert c.status == doctor.PASS


@pytest.mark.parametrize("change", [{"action": "dryrun"}, {"action": "warn"},
                                    {"excluded": ["blaze*"]}])
def test_a_constraint_not_enforcing_here_is_not_judged(change):
    checks = _checks(templates=P.TEMPLATES,
                     constraints=[P.allowed_repos(**change)])
    assert _named(checks, "Gatekeeper K8s") == []
    assert _policy(checks, "Gatekeeper").status == doctor.PASS


def test_required_resources_warns_on_test_job_pods_only():
    c = _policy(_checks(templates=P.TEMPLATES,
                        constraints=[P.required_resources()]),
                "Gatekeeper K8sRequiredResources")
    assert c.status == doctor.WARN
    assert "test-job" in c.detail


def test_container_limits_below_the_engine_size_fail():
    c = _policy(_checks(templates=P.TEMPLATES,
                        constraints=[P.container_limits(cpu="1")]),
                "Gatekeeper K8sContainerLimits")
    assert c.status == doctor.FAIL
    assert "2 CPU above 1" in c.detail
    assert "engine_cpu_limit" in c.detail


@pytest.mark.parametrize("restricted,status", [(True, doctor.PASS),
                                               (False, doctor.FAIL)])
def test_privileged_container_follows_restrict_engines(restricted, status):
    c = _policy(_checks(opts={**K8S, "restrict_engines": restricted},
                        templates=P.TEMPLATES,
                        constraints=[P.privileged_container()]),
                "Gatekeeper K8sPSPPrivilegedContainer")
    assert c.status == status


def test_an_unknown_constraint_kind_is_named_with_its_template_description():
    templates = P.TEMPLATES + [P.template("K8sBlockWildcardIngress",
                                          "Users should not be able to "
                                          "create Ingresses with a blank "
                                          "or wildcard hostname.")]
    c = _policy(_checks(templates=templates, constraints=[P.constraint(
        "K8sBlockWildcardIngress", "block-wildcard", kinds=("Ingress",))]),
        "Gatekeeper K8sBlockWildcardIngress")
    assert c.status == doctor.WARN
    assert "wildcard hostname" in c.detail


def test_a_constraint_on_kinds_the_bundle_never_makes_is_ignored():
    checks = _checks(templates=P.TEMPLATES, constraints=[P.constraint(
        "K8sRequiredLabels", "ns-must-have-owner",
        {"labels": [{"key": "owner"}]}, kinds=("Namespace",))])
    assert _named(checks, "Gatekeeper K8s") == []


def test_gatekeeper_with_templates_and_no_constraints_is_installed_and_quiet():
    """The constraints category exists only once a template defines a kind."""
    c = _policy(_checks(templates=P.TEMPLATES,
                        constraints=evidence.NOT_SERVED), "Gatekeeper")
    assert c.status == doctor.PASS
    assert "Gatekeeper is installed" in c.detail


# -- ValidatingAdmissionPolicy -------------------------------------------------

def test_a_deny_binding_on_a_no_latest_policy_fails_floating_tags():
    c = _policy(_checks(FLOATING, admission=[P.vap("no-latest",
                                                   P.NO_LATEST_CEL)],
                        bindings=[P.binding("no-latest")]),
                "ValidatingAdmissionPolicy no-latest")
    assert c.status == doctor.FAIL
    assert "crane:latest" in c.detail


def test_a_registry_prefix_in_cel_is_compared_with_the_images():
    c = _policy(_checks(admission=[P.vap("corp-only", P.CORP_ONLY_CEL)],
                        bindings=[P.binding("corp-only")]),
                "ValidatingAdmissionPolicy corp-only")
    assert c.status == doctor.FAIL
    assert "registry.corp.example/*" in c.detail


@pytest.mark.parametrize("bind", [
    P.binding("no-latest", actions=("Warn", "Audit")),
    P.binding("no-latest", selector={"matchLabels": {"env": "prod"}}),
], ids=["warn-only", "other-namespaces"])
def test_a_binding_that_does_not_deny_here_is_not_judged(bind):
    checks = _checks(FLOATING, admission=[P.vap("no-latest", P.NO_LATEST_CEL)],
                     bindings=[bind])
    assert _named(checks, "ValidatingAdmissionPolicy no-latest") == []


def test_an_api_server_without_validating_admission_policy_passes():
    cluster = {**_cluster(),
               "validating_admission_policies": evidence.NOT_SERVED,
               "validating_admission_policy_bindings": evidence.NOT_SERVED}
    [c] = doctor.check_admission_policies(PINNED, K8S, cluster)
    assert c.status == doctor.PASS
    assert "does not serve" in c.detail


# -- other webhooks -------------------------------------------------------------

def test_another_engines_webhook_on_pods_is_named():
    c = _policy(_checks(webhooks=[P.webhook("kubewarden-policy-server")]),
                "other webhooks")
    assert c.status == doctor.WARN
    assert "kubewarden-policy-server (Kubewarden)" in c.detail


@pytest.mark.parametrize("hook", [
    P.webhook("kyverno-resource-validating-webhook-cfg"),
    P.webhook("gatekeeper-validating-webhook-configuration"),
    P.webhook("cert-manager-webhook", resources=("certificates",)),
    P.webhook("team-b-only", selector={"matchLabels": {"team": "b"}}),
], ids=["kyverno", "gatekeeper", "not-pods", "other-namespace"])
def test_webhooks_reported_elsewhere_or_not_about_these_pods_pass(hook):
    assert _policy(_checks(webhooks=[hook]), "other webhooks").status == \
        doctor.PASS


# -- the combined report --------------------------------------------------------

def test_kyverno_and_gatekeeper_failures_both_reach_the_report(capsys):
    cluster = _cluster(kyverno=[P.disallow_latest_tag()],
                       templates=P.TEMPLATES, constraints=[P.allowed_repos()])
    checks = doctor.run(FLOATING, K8S, "blazemeter", cluster_data=cluster,
                        probes={doctor.API_PROBE_URL: 0})
    assert doctor.has_failures(checks)
    out = capsys.readouterr().out
    assert "FAIL  policy: Kyverno ClusterPolicy disallow-latest-tag" in out
    assert "FAIL  policy: Gatekeeper K8sAllowedRepos repo-is-corp" in out


def test_policy_details_are_plain_prose():
    """Shown in the terminal and the web UI alike."""
    cluster = _cluster(kyverno=[P.disallow_latest_tag(), P.require_labels(),
                                P.require_run_as_nonroot(), P.unrecognised()],
                       templates=P.TEMPLATES,
                       constraints=[P.allowed_repos(), P.required_resources()],
                       webhooks=[P.webhook("kubewarden-policy-server")])
    checks = doctor.evaluate(FLOATING, {**K8S, "restrict_engines": False},
                             "blazemeter", cluster_data=cluster, probes={})
    for c in checks:
        if c.name.startswith("policy"):
            for mark in ("`", "--", "->", "**"):
                assert mark not in c.detail, (c.name, mark)


# -- reading: live and from evidence ------------------------------------------

class _Done:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@pytest.mark.parametrize("answer,expected", [
    (_Done(0, json.dumps(EMPTY_LIST)), EMPTY_LIST),
    (_Done(1, err='error: the server doesn\'t have a resource type '
                  '"clusterpolicies"'), evidence.NOT_SERVED),
    (_Done(1, err='Error from server (Forbidden): clusterpolicies.kyverno.io '
                  'is forbidden'), None),
], ids=["read", "not-installed", "denied"])
def test_kget_served_keeps_not_installed_apart_from_denied(monkeypatch,
                                                           answer, expected):
    monkeypatch.setattr(kube, "quiet", lambda cmd, timeout=None: answer)
    assert kube.kget_served("kubectl", None,
                            "clusterpolicies.kyverno.io") == expected


def test_kget_served_is_unread_when_no_cluster_answers(monkeypatch):
    def gone(cmd, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(kube, "quiet", gone)
    assert kube.kget_served("kubectl", None, "constraints") is None


def test_gather_reads_each_policy_kind_once_and_namespaces_only_policies(
        monkeypatch):
    calls = []

    def served(cli, namespace, kind, timeout=None):
        calls.append((namespace, kind))
        return evidence.NOT_SERVED

    monkeypatch.setattr(kube, "kget", lambda *a, **k: EMPTY_LIST)
    monkeypatch.setattr(kube, "kget_named", lambda *a, **k: NS_BASELINE)
    monkeypatch.setattr(kube, "kget_served", served)
    data = doctor.gather_cluster("kubectl", "ns1")
    assert ("ns1", "policies.kyverno.io") in calls
    assert all(ns is None for ns, kind in calls
               if kind != "policies.kyverno.io")
    assert data["kyverno_clusterpolicies"] == evidence.NOT_SERVED
    assert data["validating_webhooks"] == []


def test_an_evidence_file_carries_not_installed_through_to_the_verdict():
    doc = document(raw=raw(kyverno_clusterpolicies={
        "apiVersion": "v1", "kind": "List",
        "items": [P.disallow_latest_tag()]},
        kyverno_policies=EMPTY_LIST))
    imported = doctor.cluster_from_evidence(doc, "blazemeter")
    assert imported.cluster["gatekeeper_constraints"] == evidence.NOT_SERVED
    checks = doctor.evaluate(FLOATING, K8S, "blazemeter", evidence=imported)
    assert _policy(checks, "Kyverno ClusterPolicy").status == doctor.FAIL
    assert "not installed" in _policy(checks, "Gatekeeper").detail


def test_only_a_policy_section_may_say_not_served():
    with pytest.raises(ValueError):
        doctor.cluster_from_evidence(document(raw=raw(nodes="not-served")))
    with pytest.raises(ValueError):
        doctor.cluster_from_evidence(document(raw=raw(
            validating_webhooks="not-served")))


def test_the_collector_writes_the_not_served_value_the_reader_expects():
    with open(os.path.join(HERE, "..", evidence.SCRIPT)) as fh:
        script = fh.read()
    assert f'"{evidence.NOT_SERVED}"' in script
    assert evidence.NOT_SERVED_ERROR in script
    for _, _, kind in doctor.POLICY_READS:
        assert kind in script


# -- the pure pieces ------------------------------------------------------------

@pytest.mark.parametrize("selector,labels,expected", [
    ({}, None, True),
    ({"matchLabels": {"a": "1"}}, None, None),
    ({"matchLabels": {"a": "1"}}, {"a": "1"}, True),
    ({"matchExpressions": [{"key": "a", "operator": "NotIn",
                            "values": ["1"]}]}, {}, True),
    ({"matchExpressions": [{"key": "a", "operator": "Exists"}]}, {}, False),
])
def test_selector_matches_is_three_valued(selector, labels, expected):
    assert admission_policy.selector_matches(selector, labels) is expected


def test_a_kyverno_subresource_rule_is_not_a_pod_rule():
    rule = {"match": {"any": [{"resources": {"kinds": ["Pod/exec"]}}]}}
    assert admission_policy.kyverno_scope(rule, "blazemeter", {}) is False


def test_every_recognised_gatekeeper_kind_yields_a_demand():
    for kind in ("K8sAllowedRepos", "K8sDisallowedRepos", "K8sDisallowedTags",
                 "K8sImageDigests", "K8sRequiredResources",
                 "K8sContainerLimits", "K8sContainerRequests",
                 "K8sPSPPrivilegedContainer",
                 "K8sPSPAllowPrivilegeEscalationContainer",
                 "K8sPSPCapabilities", "K8sPSPReadOnlyRootFilesystem",
                 "K8sPSPHostFilesystem", "K8sPSPHostNamespace",
                 "K8sPSPHostNetworkingPorts", "K8sPSPSeccomp",
                 "K8sRequiredLabels", "K8sRequiredAnnotations"):
        assert admission_policy.gatekeeper_findings(
            P.constraint(kind, "x", {})), kind


def test_a_foreach_deny_on_dropped_capabilities_is_read_as_drop_all():
    """Kyverno's require-drop-all states its demand in a deny condition."""
    rule = {"name": "require-drop-all", "match": P.POD, "validate": {
        "foreach": [{"list": "request.object.spec.containers[]",
                     "deny": {"conditions": {"all": [{
                         "key": "ALL", "operator": "AnyNotIn",
                         "value": "{{ element.securityContext.capabilities."
                                  "drop[].to_upper(@) || `[]` }}"}]}}}]}}
    assert admission_policy.kyverno_findings(rule) == [
        admission_policy.Finding(admission_policy.DROP_ALL, {})]


def test_an_image_verification_rule_warns_and_never_fails():
    policy = P._kyverno("check-image-signature", [
        {"name": "verify-signature", "match": P.POD,
         "verifyImages": [{"imageReferences": ["*"], "attestors": [
             {"entries": [{"keys": {"publicKeys": "<public-key>"}}]}]}]}])
    checks = _checks(kyverno=[policy])
    c = _policy(checks, "Kyverno ClusterPolicy check-image-signature")
    assert c.status == doctor.WARN
    assert "signed images" in c.detail
    assert doctor.FAIL not in _statuses(checks)
