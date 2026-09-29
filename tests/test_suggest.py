"""What a cluster's evidence implies about the generate options: the
decisive/suggestive vocabulary as an invariant, nothing from unreadable
sections, and ruled-out values named."""

import dis
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import cli, doctor, evidence, kube, suggest  # noqa: E402
from bzm_opl_gen.generate import SV_INGRESS_TYPES as SV_TYPES  # noqa: E402

EXAMPLE_FACTS = os.path.join(os.path.dirname(__file__), "..", "examples",
                             "facts.example.json")

# The shared baseline document (a cluster read whole); each test overrides one
# section and asserts on the suggestion that moves.
from evidence_fixtures import (API_GROUPS, CLUSTER_SCOPED_DENIED,  # noqa: E402
                               DEGRADED, NAMESPACE_DENIED,
                               PERMISSIONS as PERMS, SERVED,
                               classes as _classes, document as _evidence,
                               load, raw as _raw, scoped as _scoped)


def _by_option(suggestions):
    by = {}
    for s in suggestions:
        # One option, one suggestion: two verdicts about the same field are a
        # contradiction the reader has to arbitrate, which is the tool's job.
        assert s.option not in by, f"two suggestions for {s.option}"
        by[s.option] = s
    return by


def _for(doc, option):
    return _by_option(suggest.from_evidence(doc)).get(option)


# -- the vocabulary ----------------------------------------------------------

def test_a_decisive_suggestion_carries_the_value_and_a_suggestive_one_does_not():
    """Across every fixture, decisive suggestions carry their value and
    suggestive ones carry None."""
    for doc in _every_fixture():
        for s in suggest.from_evidence(doc):
            assert s.strength in (suggest.DECISIVE, suggest.SUGGESTIVE)
            if s.strength == suggest.DECISIVE:
                assert s.value is not None
                assert s.candidates == (s.value,)
            else:
                assert s.value is None, f"{s.option} is suggestive with a value"


def test_every_suggestion_names_the_evidence_it_came_from_and_says_why():
    for doc in _every_fixture():
        for s in suggest.from_evidence(doc):
            assert s.evidence, f"{s.option} names no evidence"
            # A path into the file, so a reader can go and look at it.
            assert all(k.split(".")[0] in doc for k in s.evidence), s.evidence
            assert len(s.detail) > 40, f"{s.option}: {s.detail!r}"


def test_a_rule_takes_the_evidence_file_and_reads_every_argument_it_takes():
    """Every rule takes exactly one argument, the evidence file, and reads it."""
    for rule in suggest.RULES:
        code = rule.__code__
        args = code.co_varnames[:code.co_argcount]
        assert args == ("doc",), f"{rule.__name__}{args}"
        read = set()
        for op in dis.get_instructions(code):
            # LOAD_FAST, and the fused/borrowed spellings 3.13+ compiles it to --
            # LOAD_FAST_LOAD_FAST carries a pair of names in one argval.
            if op.opname.startswith("LOAD_FAST"):
                read.update(op.argval if isinstance(op.argval, tuple)
                            else (op.argval,))
        assert not set(args) - read, f"{rule.__name__} ignores {set(args) - read}"


def _every_fixture():
    """Every shape the tests below build, so the invariants above are checked
    against all of them rather than against a happy path."""
    return [_evidence(), _evidence(api_groups=dict(API_GROUPS, contour=True)),
            _evidence(raw=_raw(scoped=_scoped("default", "crane"))),
            _evidence(inventory={"configmaps": ["corp-ca-bundle"],
                                 "secrets": [{"name": "regcred",
                                              "type": "kubernetes.io/dockerconfigjson"}]}),
            _evidence(openshift={"ingress_config": INGRESS_CONFIG,
                                 "proxy_config": PROXY_CONFIG}),
            _evidence(permissions={"namespaced": dict(PERMS["namespaced"],
                                                      **{"create serviceaccounts": False}),
                                   "cluster_scoped": {"list nodes": True,
                                                      "create clusterroles": False,
                                                      "create clusterrolebindings": False}}),
            _evidence(api_groups={"openshift_route": False,
                                  "openshift_security": False,
                                  "istio": False, "contour": False},
                      raw=_raw(ingressclasses=_classes()))]


INGRESS_CONFIG = {"apiVersion": "config.openshift.io/v1", "kind": "Ingress",
                  "metadata": {"name": "cluster"},
                  "spec": {"domain": "apps.ocp.example.com"}}
PROXY_CONFIG = {"apiVersion": "config.openshift.io/v1", "kind": "Proxy",
                "metadata": {"name": "cluster"},
                "spec": {"httpProxy": "http://proxy.corp:3128",
                         "httpsProxy": "http://proxy.corp:3128",
                         "noProxy": "10.0.0.0/8",
                         "trustedCA": {"name": "corp-trust"}},
                "status": {"httpProxy": "http://proxy.corp:3128",
                           "httpsProxy": "http://proxy.corp:3128",
                           "noProxy": ".cluster.local,10.0.0.0/8,localhost"}}


# -- platform ----------------------------------------------------------------

def test_the_openshift_security_api_group_decides_the_platform():
    """security.openshift.io is served by OpenShift and by nothing else, so it
    settles an option with exactly two values."""
    s = _for(_evidence(), "platform")
    assert s.strength == suggest.DECISIVE and s.value == "openshift"
    assert "api_groups.openshift_security" in s.evidence


def test_a_cluster_without_the_openshift_security_group_is_plain_kubernetes():
    s = _for(_evidence(api_groups=dict(API_GROUPS, openshift_security=False)),
             "platform")
    assert s.strength == suggest.DECISIVE and s.value == "k8s"


@pytest.mark.parametrize("groups", [{}, {"openshift_security": None}, None])
def test_platform_is_not_guessed_when_the_api_groups_were_not_collected(groups):
    """Absent and unreadable are the same answer here: nothing to suggest from."""
    assert _for(_evidence(api_groups=groups), "platform") is None


# -- evidence the collector could not read -----------------------------------

def test_nothing_is_suggested_from_a_file_whose_collector_never_reached_a_cluster():
    """A file collected with no kubeconfig (all booleans false) yields no
    suggestions."""
    doc = doctor.load_evidence(DEGRADED)
    assert doc["api_groups"] == {"openshift_route": False,
                                 "openshift_security": False,
                                 "istio": False, "contour": False}
    assert suggest.from_evidence(doc) == []
    assert "serverVersion" in suggest.why_nothing(doc)


def test_a_cluster_that_answered_is_told_apart_by_the_version_the_collector_saw():
    """serverVersion is what marks a file as a cluster that answered."""
    assert suggest.from_evidence(_evidence(versions=SERVED))
    for versions in (None, {}, {"clientVersion": {"gitVersion": "v1.29.4"}}):
        assert suggest.from_evidence(_evidence(versions=versions)) == []


def test_the_degraded_file_still_preflights_so_the_two_readings_stay_separate():
    """The degraded file still preflights (as warnings) while suggesting
    nothing."""
    doc = doctor.load_evidence(DEGRADED)
    assert doctor.cluster_from_evidence(doc, "some-ns").checks
    assert suggest.from_evidence(doc) == []


# -- sv_ingress: the shape of ruling options out -----------------------------

def test_the_served_api_groups_narrow_the_ingress_without_choosing_one():
    """Served API groups give an sv_ingress shortlist, not a value."""
    s = _for(_evidence(api_groups=dict(API_GROUPS, contour=True)), "sv_ingress")
    assert s.strength == suggest.SUGGESTIVE and s.value is None
    assert set(s.candidates) == {"nginx", "contour", "openshift"}
    assert s.ruled_out == ("istio",)


def test_a_single_surviving_ingress_backend_is_still_not_chosen():
    """One surviving backend is still suggestive, not decisive."""
    s = _for(_evidence(api_groups={"openshift_route": False, "istio": True,
                                   "contour": False, "openshift_security": False},
                       raw=_raw(ingressclasses=_classes())), "sv_ingress")
    assert s.candidates == ("istio",)
    assert s.strength == suggest.SUGGESTIVE and s.value is None
    assert set(s.ruled_out) == {"nginx", "contour", "openshift"}


def test_nginx_is_ruled_out_by_the_ingress_class_crane_hardcodes():
    """nginx is ruled out by the missing `nginx` IngressClass, not by an API
    group."""
    s = _for(_evidence(raw=_raw(ingressclasses=_classes("openshift-default"))),
             "sv_ingress")
    assert "nginx" in s.ruled_out and "nginx" not in s.candidates
    assert "nginx" in s.detail and "raw.ingressclasses" in s.evidence


def test_an_unread_ingress_class_list_leaves_nginx_open_rather_than_ruled_out():
    s = _for(_evidence(raw=_raw(ingressclasses=None)), "sv_ingress")
    assert "nginx" not in s.ruled_out and "nginx" not in s.candidates


def test_a_cluster_serving_no_ingress_backend_says_so_rather_than_staying_quiet():
    """No usable backend gives an empty shortlist that says so."""
    s = _for(_evidence(api_groups={"openshift_route": False, "istio": False,
                                   "contour": False, "openshift_security": True},
                       raw=_raw(ingressclasses=_classes())), "sv_ingress")
    assert s.candidates == ()
    assert set(s.ruled_out) == set(SV_TYPES)
    assert "WAITING_FOR_DOMAIN" in s.detail


def test_no_ingress_suggestion_at_all_when_none_of_its_evidence_was_collected():
    assert _for(_evidence(api_groups=None, raw=_raw(ingressclasses=None)),
                "sv_ingress") is None


# -- the service account -----------------------------------------------------

def test_an_existing_crane_service_account_settles_the_create_toggle():
    """An existing account of the default name decides
    service_account_create=false."""
    s = _for(_evidence(raw=_raw(scoped=_scoped("default", "crane"))),
             "service_account_create")
    assert s.strength == suggest.DECISIVE and s.value is False
    assert "raw.scoped" in s.evidence


def test_the_accounts_in_the_namespace_are_a_shortlist_not_a_choice():
    """Existing accounts are a shortlist that never includes `default`."""
    s = _for(_evidence(raw=_raw(scoped=_scoped("default", "builder", "deployer"))),
             "service_account_name")
    assert s.strength == suggest.SUGGESTIVE
    assert s.candidates == ("builder", "deployer")


def test_a_namespace_with_only_the_default_account_suggests_no_name():
    assert _for(_evidence(), "service_account_name") is None
    assert _for(_evidence(), "service_account_create") is None


def test_being_unable_to_create_service_accounts_settles_the_toggle_too():
    """A token that cannot create ServiceAccounts decides
    service_account_create=false."""
    perms = {"namespaced": dict(PERMS["namespaced"],
                                **{"create serviceaccounts": False}),
             "cluster_scoped": PERMS["cluster_scoped"]}
    s = _for(_evidence(permissions=perms), "service_account_create")
    assert s.strength == suggest.DECISIVE and s.value is False
    assert "permissions.namespaced" in s.evidence


def test_an_unread_namespace_says_nothing_about_the_service_account():
    doc = _evidence(raw=_raw(scoped=None))
    assert _for(doc, "service_account_create") is None
    assert _for(doc, "service_account_name") is None


# -- pull secret -------------------------------------------------------------

def test_the_only_dockerconfigjson_secret_in_the_namespace_settles_pull_secret():
    """Secret *type* is the API server's own answer, not a guess off a name --
    which is why this one can be decisive where the CA ConfigMap below cannot."""
    s = _for(_evidence(inventory={"configmaps": [], "secrets": [
        {"name": "builder-token", "type": "kubernetes.io/service-account-token"},
        {"name": "regcred", "type": "kubernetes.io/dockerconfigjson"}]}),
        "pull_secret")
    assert s.strength == suggest.DECISIVE and s.value == "regcred"
    assert "inventory.secrets" in s.evidence


def test_several_pull_secrets_are_a_shortlist():
    s = _for(_evidence(inventory={"configmaps": [], "secrets": [
        {"name": "regcred", "type": "kubernetes.io/dockerconfigjson"},
        {"name": "quay-pull", "type": "kubernetes.io/dockerconfigjson"}]}),
        "pull_secret")
    assert s.strength == suggest.SUGGESTIVE
    assert s.candidates == ("quay-pull", "regcred")


@pytest.mark.parametrize("secrets", [[], None])
def test_no_pull_secret_is_suggested_where_there_is_none_or_none_was_read(secrets):
    """Read-and-empty and unreadable differ everywhere it changes an answer;
    here they agree, because the option's own default is already "none"."""
    assert _for(_evidence(inventory={"configmaps": [], "secrets": secrets}),
                "pull_secret") is None


# -- CA trust ----------------------------------------------------------------

def test_a_trust_bundle_configmap_is_a_shortlist_and_never_more():
    """A trust-bundle-looking ConfigMap name is only ever suggestive."""
    s = _for(_evidence(inventory={"secrets": [], "configmaps": [
        "kube-root-ca.crt", "corp-ca-bundle", "app-config", "trusted-ca"]}),
        "ca_existing_configmap")
    assert s.strength == suggest.SUGGESTIVE
    assert s.candidates == ("corp-ca-bundle", "trusted-ca")
    assert "inventory.configmaps" in s.evidence


def test_the_configmaps_kubernetes_puts_in_every_namespace_are_not_candidates():
    """kube-root-ca.crt and openshift-service-ca.crt are never candidates."""
    assert _for(_evidence(inventory={"secrets": [], "configmaps": [
        "kube-root-ca.crt", "openshift-service-ca.crt"]}),
        "ca_existing_configmap") is None


@pytest.mark.parametrize("configmaps", [[], None, ["app-config"]])
def test_no_ca_configmap_is_suggested_without_one_that_looks_like_a_bundle(
        configmaps):
    assert _for(_evidence(inventory={"secrets": [], "configmaps": configmaps}),
                "ca_existing_configmap") is None


# -- the OpenShift cluster config --------------------------------------------

def test_the_cluster_ingress_domain_is_offered_for_the_sv_subdomain():
    s = _for(_evidence(openshift={"ingress_config": INGRESS_CONFIG,
                                  "proxy_config": None}), "sv_subdomain")
    assert s.strength == suggest.SUGGESTIVE
    assert s.candidates == ("apps.ocp.example.com",)
    assert "openshift.ingress_config" in s.evidence


def test_the_cluster_proxy_settles_the_proxy_options():
    """A cluster-wide egress proxy is not advice: pods that reach BlazeMeter go
    through it, and nothing propagates it into a pod's env for you."""
    s = _for(_evidence(openshift={"ingress_config": None,
                                  "proxy_config": PROXY_CONFIG}), "proxy")
    assert s.strength == suggest.DECISIVE
    assert s.value["https"] == "http://proxy.corp:3128"
    # status is the effective proxy the operators publish; spec is what was
    # asked for. The expanded noProxy is the one a pod needs.
    assert s.value["no_proxy"] == ".cluster.local,10.0.0.0/8,localhost"


def test_a_cluster_proxy_with_a_trusted_ca_points_at_the_injected_bundle():
    s = _for(_evidence(openshift={"ingress_config": None,
                                  "proxy_config": PROXY_CONFIG}),
             "ca_openshift_inject")
    assert s.strength == suggest.SUGGESTIVE and s.candidates == (True,)
    assert "corp-trust" in s.detail


def test_a_cluster_that_declares_no_proxy_suggests_nothing_about_one():
    """The option's default is already "no proxy"; restating it is noise, and
    the empty Proxy object is what every cluster without one carries."""
    empty = {"kind": "Proxy", "metadata": {"name": "cluster"}, "spec": {},
             "status": {}}
    doc = _evidence(openshift={"ingress_config": None, "proxy_config": empty})
    assert _for(doc, "proxy") is None
    assert _for(doc, "ca_openshift_inject") is None


def test_an_unread_openshift_config_suggests_nothing():
    """Null OpenShift config suggests nothing."""
    doc = _evidence(openshift={"ingress_config": None, "proxy_config": None})
    assert _for(doc, "sv_subdomain") is None and _for(doc, "proxy") is None
    assert suggest.from_evidence(_evidence(openshift=None))


# -- cluster RBAC, and the inference that must not come back -----------------

def test_being_unable_to_create_clusterroles_rules_cluster_rbac_out():
    perms = {"namespaced": PERMS["namespaced"],
             "cluster_scoped": {"list nodes": True, "create clusterroles": False,
                                "create clusterrolebindings": False}}
    s = _for(_evidence(permissions=perms), "cluster_rbac")
    assert s.strength == suggest.DECISIVE and s.value is False
    assert "permissions.cluster_scoped" in s.evidence


def test_permission_to_create_clusterroles_narrows_nothing_so_says_nothing():
    """Permission to create cluster RBAC produces no suggestion."""
    assert _for(_evidence(), "cluster_rbac") is None


@pytest.mark.parametrize("scoped", [{}, None, {"create clusterroles": None,
                                               "create clusterrolebindings": None}])
def test_cluster_rbac_is_not_guessed_from_permissions_that_were_not_collected(scoped):
    assert _for(_evidence(permissions={"namespaced": {}, "cluster_scoped": scoped}),
                "cluster_rbac") is None


def test_no_cluster_permission_ever_says_anything_about_service_type():
    """Cluster permissions never produce a service_type suggestion (crane needs
    no Node read)."""
    denied = {"namespaced": dict.fromkeys(PERMS["namespaced"], False),
              "cluster_scoped": {"list nodes": False, "create clusterroles": False,
                                 "create clusterrolebindings": False}}
    for doc in _every_fixture() + [_evidence(permissions=denied)]:
        for s in suggest.from_evidence(doc):
            assert s.option != "service_type"
            assert "service_type" not in s.detail
            assert "NODEPORT" not in s.detail


# -- the command -------------------------------------------------------------

def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    # The whole point is a laptop with no kubeconfig and no API key.
    monkeypatch.setattr(kube, "cli_tool",
                        lambda: pytest.fail("suggest went looking for a cluster"))
    try:
        cli.main()
    except SystemExit as e:
        return e.code
    return 0


def test_suggest_reads_an_evidence_file_with_no_cluster_and_no_api_key(
        monkeypatch, capsys, tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_evidence(
        raw=_raw(scoped=_scoped("default", "crane")))))
    code = _run(monkeypatch, "suggest", "--cluster-evidence", str(path))
    out = capsys.readouterr().out
    assert code == 0
    assert "DECISIVE" in out and "service_account_create" in out
    assert "platform" in out and "openshift" in out
    assert "api_groups.openshift_security" in out       # the evidence, named
    assert "blazemeter" in out and "2026-07-27T10:00:00Z" in out


def test_suggest_applies_nothing(monkeypatch, capsys, tmp_path):
    """Reading the cluster and configuring from it are separate acts. Nothing
    is written, and the report says so rather than leaving it to be assumed."""
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_evidence()))
    monkeypatch.chdir(tmp_path)
    _run(monkeypatch, "suggest", "--cluster-evidence", str(path))
    assert "Nothing has been applied" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [path]


def test_suggest_says_why_it_has_nothing_to_say(monkeypatch, capsys):
    """An empty result says whether the collector reached a cluster."""
    code = _run(monkeypatch, "suggest", "--cluster-evidence", DEGRADED)
    out = capsys.readouterr().out
    assert code == 0
    assert "serverVersion" in out and evidence.SCRIPT in out
    assert "DECISIVE" not in out and "SUGGESTIVE" not in out


def test_suggest_emits_the_suggestions_as_data(monkeypatch, capsys, tmp_path):
    """--json emits strength and candidates as fields."""
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_evidence(
        api_groups=dict(API_GROUPS, contour=True))))
    _run(monkeypatch, "suggest", "--cluster-evidence", str(path), "--json")
    payload = json.loads(capsys.readouterr().out)
    by = {s["option"]: s for s in payload}
    assert by["platform"] == {"option": "platform", "strength": "DECISIVE",
                              "value": "openshift", "candidates": ["openshift"],
                              "ruled_out": [], "detail": by["platform"]["detail"],
                              "evidence": ["api_groups.openshift_security"]}
    assert by["sv_ingress"]["value"] is None
    assert by["sv_ingress"]["ruled_out"] == ["istio"]


def test_suggest_refuses_a_file_that_is_not_cluster_evidence(monkeypatch):
    """The likeliest wrong file is facts.json, and the refusal is doctor's own
    -- one reading of what a well-formed evidence file is, not two."""
    code = _run(monkeypatch, "suggest", "--cluster-evidence", EXAMPLE_FACTS)
    assert evidence.SCHEMA in str(code) and "no 'schema' field" in str(code)


def test_suggest_says_where_to_get_an_evidence_file_it_cannot_find(monkeypatch,
                                                                   tmp_path):
    code = _run(monkeypatch, "suggest", "--cluster-evidence",
                str(tmp_path / "nope.json"))
    assert "nope.json" in str(code) and "bzm-cluster-evidence.sh" in str(code)


# -- applying one to a configuration -----------------------------------------
# `merge` says how a suggestion stands against the current options. A value
# somebody set is never quietly replaced, so most assertions are about what
# merge refuses to hand back.

REGCRED = {"inventory": {"configmaps": [],
                         "secrets": [{"name": "regcred",
                                      "type": suggest.DOCKERCONFIGJSON}]}}
PLAIN_K8S = {"api_groups": dict(API_GROUPS, openshift_security=False)}


def _merge(doc, option, options):
    s = _for(doc, option)
    assert s is not None, f"no suggestion for {option} to merge"
    return suggest.merge(s, options)


def test_a_decisive_suggestion_fills_an_option_nobody_has_moved():
    """Decisive against an untouched default is FILL with the value."""
    m = _merge(_evidence(**PLAIN_K8S), "platform", {"namespace": "blazemeter"})
    assert m.state == suggest.FILL
    assert m.value == "k8s"
    # ...and what it would replace, which the caller shows either way.
    assert m.current == "openshift"


def test_an_option_already_holding_the_evidence_value_has_nothing_to_apply():
    """An option already holding the value is SETTLED with nothing to apply."""
    m = _merge(_evidence(**PLAIN_K8S), "platform", {"platform": "k8s"})
    assert m.state == suggest.SETTLED
    assert m.value is None


def test_a_value_somebody_moved_off_the_default_conflicts_rather_than_applying():
    """A non-default value the evidence disagrees with is CONFLICT, showing
    both."""
    m = _merge(_evidence(**REGCRED), "pull_secret", {"pull_secret": "team-creds"})
    assert m.state == suggest.CONFLICT
    assert m.current == "team-creds"
    # Still carried, because the resolution is a replace the user asks for by
    # name: what must not happen is it being written without that.
    assert m.value == "regcred"


def test_an_empty_field_is_not_a_choice_somebody_made():
    """An empty string is unset, not a choice."""
    m = _merge(_evidence(**REGCRED), "pull_secret", {"pull_secret": ""})
    assert m.state == suggest.FILL


def test_a_suggestive_suggestion_never_hands_back_a_value_to_apply():
    """A one-candidate suggestive suggestion is CHOOSE with no value."""
    doc = _evidence(openshift={"ingress_config": None, "proxy_config": PROXY_CONFIG})
    m = _merge(doc, "ca_openshift_inject", {})
    assert m.state == suggest.CHOOSE
    assert m.value is None
    assert _for(doc, "ca_openshift_inject").candidates == (True,)


def test_a_suggestive_suggestion_is_settled_by_any_of_its_candidates():
    """The shortlist is the whole answer, so a configuration already holding
    one of them is not something to nag about -- and not a conflict either."""
    doc = _evidence(api_groups=dict(API_GROUPS, contour=True))
    assert _merge(doc, "sv_ingress", {"sv_ingress": "contour"}).state \
        == suggest.SETTLED
    assert _merge(doc, "sv_ingress", {"sv_ingress": "openshift"}).state \
        == suggest.SETTLED


def test_declining_the_ingress_settles_it_rather_than_conflicting():
    """sv_ingress=none is SETTLED against any ingress shortlist."""
    doc = _evidence(api_groups=dict(API_GROUPS, contour=True))
    m = _merge(doc, "sv_ingress", {"sv_ingress": suggest.SV_INGRESS_NONE})
    assert m.state == suggest.SETTLED
    assert m.current == suggest.SV_INGRESS_NONE


def test_a_configured_value_the_cluster_rules_out_is_a_conflict():
    """A configured ingress the cluster rules out is CONFLICT with no value."""
    doc = _evidence(api_groups=dict(API_GROUPS, contour=True))
    m = _merge(doc, "sv_ingress", {"sv_ingress": "istio"})
    assert m.state == suggest.CONFLICT
    assert m.current == "istio"
    assert m.value is None
    assert "istio" in _for(doc, "sv_ingress").ruled_out


def test_a_deliberate_choice_that_matches_the_default_reads_as_untouched():
    """A deliberate choice equal to the default reads as untouched: FILL, not
    CONFLICT.

    Only a record of typed keys could tell them apart. It is safe because
    nothing applies without a click on a row that shows `current`."""
    doc = _evidence(**PLAIN_K8S)
    deliberate = _merge(doc, "platform", {"platform": "openshift"})
    assert deliberate.state == suggest.FILL
    assert deliberate.value == "k8s"
    # The mitigation, on the row itself: what would be replaced is on screen.
    assert deliberate.current == "openshift"


def test_merge_shows_what_would_be_replaced_whatever_the_state():
    """`current` is reported in every merge state."""
    for doc in _every_fixture():
        for s in suggest.from_evidence(doc):
            for options in ({}, {s.option: "something-else"}):
                m = suggest.merge(s, options)
                assert m.option == s.option
                # Present, and the value the caller holds -- not a re-derivation
                # a UI could disagree with.
                assert m.current == options.get(
                    s.option, suggest.DEFAULT_OPTIONS.get(s.option))


def test_every_option_a_suggestion_names_is_one_generate_actually_takes():
    """Every suggested option is one generate() takes."""
    from bzm_opl_gen.generate import DEFAULT_OPTIONS
    for doc in _every_fixture():
        for s in suggest.from_evidence(doc):
            assert s.option in DEFAULT_OPTIONS, s.option


def test_merge_reads_an_option_the_caller_left_out_as_its_default():
    """An option missing from a partial dict merges as its default."""
    doc = _evidence(**PLAIN_K8S)
    assert _merge(doc, "platform", {}) == _merge(doc, "platform",
                                                 {"platform": "openshift"})


def test_an_applied_value_is_indistinguishable_from_a_typed_one():
    """Applying writes the plain option: bundle and profile match a typed
    value's byte for byte."""
    from bzm_opl_gen import generate as gen
    facts = json.load(open(EXAMPLE_FACTS))
    base = {"namespace": "blazemeter", "auth_token": "tok"}
    doc = _evidence(**REGCRED)
    m = _merge(doc, "pull_secret", base)
    applied = dict(base, **{m.option: m.value})
    typed = dict(base, pull_secret="regcred")
    assert applied == typed
    assert gen.generate(facts, applied) == gen.generate(facts, typed)
    # ...and it replays as an ordinary option, with nothing extra beside it.
    profile = json.loads(gen.generate(facts, applied)[gen.PROFILE_FILE])
    assert profile["pull_secret"] == "regcred"
    assert set(profile) == set(json.loads(
        gen.generate(facts, typed)[gen.PROFILE_FILE]))


# -- the row a caller renders -------------------------------------------------
#
# What `merged_as_dict` adds: display values, and why a row cannot be offered.

def test_a_value_is_shown_the_way_the_file_that_would_carry_it_writes_it():
    assert suggest.shown("nginx") == "nginx"
    assert suggest.shown(False) == "false"          # not `False`
    assert suggest.shown(True) == "true"
    assert suggest.shown({"https": "http://p:3128"}) == '{"https": "http://p:3128"}'


def test_unset_is_said_in_words_rather_than_printed_as_null():
    """None and "" are shown as "not set"."""
    assert suggest.shown(None) == "not set"
    assert suggest.shown("") == "not set"
    # ...and a value that is merely falsy is still a value.
    assert suggest.shown(False) != "not set"
    assert suggest.shown(0) == "0"


def test_a_row_carries_every_value_it_displays_already_written():
    doc = _evidence(**REGCRED)
    row = suggest.merged_as_dict(_for(doc, "pull_secret"), {})
    assert row["current_shown"] == suggest.shown(row["current"])
    assert row["value_shown"] == suggest.shown(row["value"])
    assert row["candidates_shown"] == [suggest.shown(c) for c in row["candidates"]]
    assert row["ruled_out_shown"] == [suggest.shown(v) for v in row["ruled_out"]]


def test_a_ca_mode_cannot_be_offered_over_one_that_already_holds_a_value():
    """A CA mode suggestion is blocked while another CA mode holds a value."""
    doc = _evidence(openshift={"ingress_config": None, "proxy_config": PROXY_CONFIG})
    s = _for(doc, "ca_openshift_inject")
    blocked = suggest.merged_as_dict(s, {"ca_existing_configmap": "corp-ca"})
    assert "an existing ConfigMap" in blocked["blocked"]
    assert "does not generate" in blocked["blocked"]
    # Nothing claims the slot -> nothing to say.
    assert suggest.merged_as_dict(s, {})["blocked"] is None
    # An empty field is not a value, for the same reason it is not a choice.
    assert suggest.merged_as_dict(s, {"ca_bundle": ""})["blocked"] is None


def test_an_option_is_never_blocked_by_its_own_value():
    """Replacing a CA mode with the same mode is one mode, not two."""
    doc = _evidence(inventory={"configmaps": ["corp-trusted-ca"], "secrets": []})
    s = _for(doc, "ca_existing_configmap")
    assert s is not None
    assert suggest.merged_as_dict(s, {"ca_existing_configmap": "older-ca"})["blocked"] \
        is None


def test_the_blocked_rule_names_the_modes_generate_actually_refuses():
    """The set is generate's, and a copy here is how a fourth mode arrives
    offerable over the other three."""
    from bzm_opl_gen.generate import CA_MODES, DEFAULT_OPTIONS
    for option in CA_MODES:
        assert option in DEFAULT_OPTIONS


# -- the paths a suggestion cites --------------------------------------------
#
# Cited paths are built from the document's shape, so each resolves in it.

def test_every_path_a_suggestion_cites_is_one_the_document_defines():
    """Over every fixture here, because a rule that cites a stale path is one
    nobody notices until somebody follows it."""
    for doc in _every_fixture():
        for s in suggest.from_evidence(doc):
            for path in s.evidence:
                assert evidence.known(*path.split(".")), (
                    f"{s.option} cites '{path}', which is not a path in the "
                    f"cluster evidence document")


def test_a_rule_that_reads_a_path_the_document_has_no_key_for_is_refused():
    """_read raises UnknownSection for a path the document does not define."""
    with pytest.raises(evidence.UnknownSection) as e:
        suggest._read(_evidence(), "api_groups.openshift_sec", kind=bool)
    assert "api_groups.openshift_sec" in str(e.value)


def test_the_dotted_paths_the_docs_quote_still_resolve():
    """Every evidence path docs/preflight.md quotes is defined by the document."""
    text = open(os.path.join(os.path.dirname(__file__), "..", "docs",
                             "preflight.md")).read()
    quoted = set(re.findall(r"`([a-z_]+(?:\.[a-zA-Z_]+)+)`", text))
    cited = {q for q in quoted if q.split(".")[0] in evidence.DOCUMENT}
    assert cited, "no evidence paths found in docs/preflight.md at all"
    for path in sorted(cited):
        assert evidence.known(*path.split(".")), (
            f"docs/preflight.md sends a reader to '{path}', which the cluster "
            f"evidence document does not define")


# -- the half-read files -----------------------------------------------------
#
# Files with some sections read and some refused: what was read is suggested
# from, and what was not is not.

def test_a_half_read_file_suggests_from_what_was_read_and_not_from_what_was_not():
    doc = load(CLUSTER_SCOPED_DENIED)
    by = _by_option(suggest.from_evidence(doc))
    # raw.ingressclasses was refused, so nginx is neither open nor ruled out --
    # and the api groups, which were read, still decide the rest.
    assert "nginx" not in by["sv_ingress"].candidates
    assert "nginx" not in by["sv_ingress"].ruled_out
    assert "contour" in by["sv_ingress"].candidates
    # ...and what the cluster-scoped refusals themselves imply is still said.
    assert by["cluster_rbac"].value is False


def test_a_file_whose_namespaced_reads_were_refused_says_nothing_about_them():
    """Refused namespaced reads suggest nothing about accounts, but a real
    `false` permission still decides."""
    doc = load(NAMESPACE_DENIED)
    by = _by_option(suggest.from_evidence(doc))
    assert "service_account_name" not in by
    assert by["service_account_create"].value is False
    assert by["service_account_create"].evidence == ("permissions.namespaced",)
    assert "pull_secret" not in by and "ca_existing_configmap" not in by
