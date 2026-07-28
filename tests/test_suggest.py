"""What a cluster's evidence implies about the generate options.

`doctor` answers "would this deployment survive here". These tests cover the
question that comes first: how the bundle should have been configured, read off
the same evidence file. The vocabulary is the contract -- decisive means a
caller may offer the value as a default, suggestive means it must stay a
shortlist -- so it is asserted as an invariant, not just per mapping.

Two properties matter more than any single mapping and are tested hardest:
nothing is suggested from evidence the collector recorded as unreadable, and
evidence that eliminates values says so rather than handing back the survivor.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import cli, doctor, suggest  # noqa: E402
from bzm_opl_gen.generate import SV_INGRESS_TYPES as SV_TYPES  # noqa: E402

DEGRADED = os.path.join(os.path.dirname(__file__), "cluster-evidence.degraded.json")
EXAMPLE_FACTS = os.path.join(os.path.dirname(__file__), "..", "examples",
                             "facts.example.json")

# A cluster the collector could actually read: OpenShift, one nginx
# IngressClass, a namespace with a couple of ServiceAccounts. Every test below
# overrides one top-level section of this and asserts on the one suggestion it
# moves.
API_GROUPS = {"openshift_route": True, "openshift_security": True,
              "istio": False, "contour": False}
PERMS = {"namespaced": {"create serviceaccounts": True, "create roles": True,
                        "create rolebindings": True, "create configmaps": True,
                        "create secrets": True, "create deployments": True,
                        "create ingresses": True},
         "cluster_scoped": {"list nodes": True, "create clusterroles": True,
                            "create clusterrolebindings": True}}
# `kubectl version -o json` carries this only when a server answered -- see
# test_nothing_is_suggested_from_a_file_whose_collector_never_reached_a_cluster.
SERVED = {"clientVersion": {"gitVersion": "v1.29.4"},
          "serverVersion": {"gitVersion": "v1.29.4"}}


def _sa(name):
    return {"kind": "ServiceAccount", "metadata": {"name": name}}


def _scoped(*accounts):
    return {"apiVersion": "v1", "kind": "List", "items": list(accounts)}


def _classes(*names):
    return {"apiVersion": "v1", "kind": "List",
            "items": [{"kind": "IngressClass", "metadata": {"name": n},
                       "spec": {"controller": "k8s.io/ingress-nginx"}}
                      for n in names]}


def _evidence(**over):
    """An evidence file as the script emits one, with any top-level section
    replaced wholesale -- that is the granularity the collector fails at."""
    doc = {
        "schema": doctor.EVIDENCE_SCHEMA,
        "collected_at": "2026-07-27T10:00:00Z",
        "namespace": "blazemeter",
        "cli": "oc",
        "raw": {"nodes": None, "ingressclasses": _classes("nginx"),
                "namespace": {}, "scoped": _scoped(_sa("default"))},
        "inventory": {"configmaps": [], "secrets": []},
        "permissions": PERMS,
        "api_groups": API_GROUPS,
        "openshift": {"ingress_config": None, "proxy_config": None},
        "versions": SERVED,
        "notes": [],
    }
    doc.update(over)
    return doc


def _raw(**over):
    """`raw` with one section replaced -- `scoped=None` is a denied read."""
    sections = dict(_evidence()["raw"])
    sections.update(over)
    return sections


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
    """The distinction #54 builds on: decisive means a caller may offer the
    value as a default, suggestive means it has a shortlist to present and no
    right to pick from it. Asserted over every suggestion any fixture here
    produces, because a single mapping getting it wrong is what would leak a
    guess into a default."""
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


def _every_fixture():
    """Every shape the tests below build, so the invariants above are checked
    against all of them rather than against a happy path."""
    return [_evidence(), _evidence(api_groups=dict(API_GROUPS, contour=True)),
            _evidence(raw=_raw(scoped=_scoped(_sa("default"), _sa("crane")))),
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
    """The real output of the script run with no kubeconfig at all, and the
    reason this guard is not merely a null check.

    `raw` being null is easy: every rule that reads it stays quiet. The trap is
    `api_groups` and `permissions`, which are plain booleans -- the script's
    `api-resources` and `auth can-i` are error-to-false in shell, so a machine
    that reached nothing produces a file that reads, taken at face value, as a
    plain-Kubernetes cluster where nothing may be created. Every one of those
    would be a suggestion about a cluster nobody described.
    """
    doc = doctor.load_evidence(DEGRADED)
    assert doc["api_groups"] == {"openshift_route": False,
                                 "openshift_security": False,
                                 "istio": False, "contour": False}
    assert suggest.from_evidence(doc) == []
    assert "serverVersion" in suggest.why_nothing(doc)


def test_a_cluster_that_answered_is_told_apart_by_the_version_the_collector_saw():
    """`kubectl version -o json` carries a serverVersion only when a server
    answered -- the one field in the file that distinguishes a collection from
    a machine that could not reach the cluster."""
    assert suggest.from_evidence(_evidence(versions=SERVED))
    for versions in (None, {}, {"clientVersion": {"gitVersion": "v1.29.4"}}):
        assert suggest.from_evidence(_evidence(versions=versions)) == []


def test_the_degraded_file_still_preflights_so_the_two_readings_stay_separate():
    """`doctor` has plenty to say about that file -- six warnings and their
    reason. Suggestions are the stricter question: a warning about what could
    not be read is useful, a configuration guessed from it is not."""
    doc = doctor.load_evidence(DEGRADED)
    assert doctor.cluster_from_evidence(doc, "some-ns").checks
    assert suggest.from_evidence(doc) == []


# -- sv_ingress: the shape of ruling options out -----------------------------

def test_the_served_api_groups_narrow_the_ingress_without_choosing_one():
    """The issue's own example of suggestive. crane publishes through exactly
    one backend, and which one is a decision about the customer's platform --
    the cluster only says which are possible."""
    s = _for(_evidence(api_groups=dict(API_GROUPS, contour=True)), "sv_ingress")
    assert s.strength == suggest.SUGGESTIVE and s.value is None
    assert set(s.candidates) == {"nginx", "contour", "openshift"}
    assert s.ruled_out == ("istio",)


def test_a_single_surviving_ingress_backend_is_still_not_chosen():
    """The criterion that keeps the vocabulary honest: narrowing to one is not
    the same as deciding, and a caller that treated it as a default would be
    configuring a customer's cluster from an api-resources listing."""
    s = _for(_evidence(api_groups={"openshift_route": False, "istio": True,
                                   "contour": False, "openshift_security": False},
                       raw=_raw(ingressclasses=_classes())), "sv_ingress")
    assert s.candidates == ("istio",)
    assert s.strength == suggest.SUGGESTIVE and s.value is None
    assert set(s.ruled_out) == {"nginx", "contour", "openshift"}


def test_nginx_is_ruled_out_by_the_ingress_class_crane_hardcodes():
    """Not by an API group: networking.k8s.io is served everywhere. crane writes
    `ingressClassName: nginx` and BlazeMeter exposes no env to change it, so the
    class existing is what makes the value usable -- the same fact doctor
    fails on after the choice has been made."""
    s = _for(_evidence(raw=_raw(ingressclasses=_classes("openshift-default"))),
             "sv_ingress")
    assert "nginx" in s.ruled_out and "nginx" not in s.candidates
    assert "nginx" in s.detail and "raw.ingressclasses" in s.evidence


def test_an_unread_ingress_class_list_leaves_nginx_open_rather_than_ruled_out():
    s = _for(_evidence(raw=_raw(ingressclasses=None)), "sv_ingress")
    assert "nginx" not in s.ruled_out and "nginx" not in s.candidates


def test_a_cluster_serving_no_ingress_backend_says_so_rather_than_staying_quiet():
    """The one place an empty shortlist is itself the finding: sv_ingress is
    mandatory for a mockServices location, so "none of them" is what somebody
    needs to hear before the virtual services stall at WAITING_FOR_DOMAIN."""
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
    """The issue's own example of decisive: the namespace already holds the
    account the bundle would create, and creating it again is at best a no-op
    over somebody else's object."""
    s = _for(_evidence(raw=_raw(scoped=_scoped(_sa("default"), _sa("crane")))),
             "service_account_create")
    assert s.strength == suggest.DECISIVE and s.value is False
    assert "raw.scoped" in s.evidence


def test_the_accounts_in_the_namespace_are_a_shortlist_not_a_choice():
    """Which account crane runs as is the platform team's call. `default` is
    never among the candidates -- every namespace has one, and binding crane's
    Role to the account every other pod runs as is exactly what generate's own
    refusal exists to prevent."""
    s = _for(_evidence(raw=_raw(scoped=_scoped(_sa("default"), _sa("builder"),
                                               _sa("deployer")))),
             "service_account_name")
    assert s.strength == suggest.SUGGESTIVE
    assert s.candidates == ("builder", "deployer")


def test_a_namespace_with_only_the_default_account_suggests_no_name():
    assert _for(_evidence(), "service_account_name") is None
    assert _for(_evidence(), "service_account_create") is None


def test_being_unable_to_create_service_accounts_settles_the_toggle_too():
    """A different route to the same verdict, and it holds even where the
    account does not exist yet: a bundle carrying a ServiceAccount object simply
    does not apply, so somebody else has to create it first."""
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
    """Only the names are collected -- a CA bundle is ~300KB nobody needs, and
    reading one is what the collector script promises not to do. A name is a
    guess about contents, so this cannot be decisive however well it matches."""
    s = _for(_evidence(inventory={"secrets": [], "configmaps": [
        "kube-root-ca.crt", "corp-ca-bundle", "app-config", "trusted-ca"]}),
        "ca_existing_configmap")
    assert s.strength == suggest.SUGGESTIVE
    assert s.candidates == ("corp-ca-bundle", "trusted-ca")
    assert "inventory.configmaps" in s.evidence


def test_the_configmaps_kubernetes_puts_in_every_namespace_are_not_candidates():
    """kube-root-ca.crt and openshift-service-ca.crt are in every namespace and
    carry the cluster's own CA, not the corporate one an intercepting proxy
    needs -- offering them would send someone to trust the wrong issuer."""
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
    """Null here is the ordinary case on plain Kubernetes, where neither kind
    exists at all -- and identical to the OpenShift cluster whose config this
    account may not read."""
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
    """Whether the location wants cluster-scoped RBAC is a decision about the
    location, not a fact about the cluster. Permitted means the option stays
    open, and an entry that says "either value is fine" is noise in a list
    whose whole point is that every line carries information."""
    assert _for(_evidence(), "cluster_rbac") is None


@pytest.mark.parametrize("scoped", [{}, None, {"create clusterroles": None,
                                               "create clusterrolebindings": None}])
def test_cluster_rbac_is_not_guessed_from_permissions_that_were_not_collected(scoped):
    assert _for(_evidence(permissions={"namespaced": {}, "cluster_scoped": scoped}),
                "cluster_rbac") is None


def test_no_cluster_permission_ever_says_anything_about_service_type():
    """Issue #49, settled by a live run: crane resolves its advertised address
    from its own network interfaces, not from the Node object, and NODEPORT ran
    green against a cluster where the agent had namespaced RBAC only and no
    ClusterRole at all. The inference is plausible, was written down once, and
    is wrong -- so it is asserted against here rather than merely left out.
    """
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
    monkeypatch.setattr(doctor.livetest, "cli_tool",
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
        raw=_raw(scoped=_scoped(_sa("default"), _sa("crane"))))))
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
    """An empty list from a file that reached nothing looks identical to an
    empty list from a cluster that constrains nothing, and the difference is
    the whole reason to re-run the collector."""
    code = _run(monkeypatch, "suggest", "--cluster-evidence", DEGRADED)
    out = capsys.readouterr().out
    assert code == 0
    assert "serverVersion" in out and doctor.EVIDENCE_SCRIPT in out
    assert "DECISIVE" not in out and "SUGGESTIVE" not in out


def test_suggest_emits_the_suggestions_as_data(monkeypatch, capsys, tmp_path):
    """The shape #54 and the web UI read: strength and candidates as fields,
    not as prose to be parsed back out of a report."""
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
    assert doctor.EVIDENCE_SCHEMA in str(code) and "no 'schema' field" in str(code)


def test_suggest_says_where_to_get_an_evidence_file_it_cannot_find(monkeypatch,
                                                                   tmp_path):
    code = _run(monkeypatch, "suggest", "--cluster-evidence",
                str(tmp_path / "nope.json"))
    assert "nope.json" in str(code) and "bzm-cluster-evidence.sh" in str(code)


# -- applying one to a configuration -----------------------------------------
# The half #54 adds: a suggestion says what the cluster implies, and `merge`
# says how that stands against the options as they are right now. The rule the
# rest of the feature rests on is that a value somebody set is never quietly
# replaced -- so the interesting assertions below are about what merge REFUSES
# to hand back, not about what it fills in.

REGCRED = {"inventory": {"configmaps": [],
                         "secrets": [{"name": "regcred",
                                      "type": suggest.DOCKERCONFIGJSON}]}}
PLAIN_K8S = {"api_groups": dict(API_GROUPS, openshift_security=False)}


def _merge(doc, option, options):
    s = _for(doc, option)
    assert s is not None, f"no suggestion for {option} to merge"
    return suggest.merge(s, options)


def test_a_decisive_suggestion_fills_an_option_nobody_has_moved():
    """The plain case, and the only one that may be offered as a one-click
    default: the option still holds what the generator would have used anyway,
    so applying replaces nothing a person decided."""
    m = _merge(_evidence(**PLAIN_K8S), "platform", {"namespace": "blazemeter"})
    assert m.state == suggest.FILL
    assert m.value == "k8s"
    # ...and what it would replace, which the caller shows either way.
    assert m.current == "openshift"


def test_an_option_already_holding_the_evidence_value_has_nothing_to_apply():
    """SETTLED is the one state where "the cluster confirms this" is a truthful
    thing to say, and it carries no value: a caller that wrote it back would
    re-render a bundle byte-for-byte identical to the one on screen."""
    m = _merge(_evidence(**PLAIN_K8S), "platform", {"platform": "k8s"})
    assert m.state == suggest.SETTLED
    assert m.value is None


def test_a_value_somebody_moved_off_the_default_conflicts_rather_than_applying():
    """The constraint the whole feature is subordinate to. The evidence names
    the only pull secret in the namespace and the configuration names another
    one; that is a disagreement to show -- with both values -- and never a
    write to make on its own."""
    m = _merge(_evidence(**REGCRED), "pull_secret", {"pull_secret": "team-creds"})
    assert m.state == suggest.CONFLICT
    assert m.current == "team-creds"
    # Still carried, because the resolution is a replace the user asks for by
    # name: what must not happen is it being written without that.
    assert m.value == "regcred"


def test_an_empty_field_is_not_a_choice_somebody_made():
    """The web UI seeds an empty string into the field a group reveals -- CA
    trust switched on shows an empty ConfigMap name. Treating that as a value
    would turn every group the user opened into a conflict with nothing in it.
    """
    m = _merge(_evidence(**REGCRED), "pull_secret", {"pull_secret": ""})
    assert m.state == suggest.FILL


def test_a_suggestive_suggestion_never_hands_back_a_value_to_apply():
    """Narrowing to one is still not choosing. ca_openshift_inject has exactly
    one candidate -- True -- and it is still the user's to pick, because the
    alternative (naming a bundle already in the namespace) is a decision about
    the customer's platform that no evidence file settles."""
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


def test_a_configured_value_the_cluster_rules_out_is_a_conflict():
    """The loudest disagreement this can report, and the one worth importing an
    evidence file for: the ingress the bundle is configured for is not served
    here at all, so the deployment stalls at WAITING_FOR_DOMAIN. Reported as a
    conflict with no value to apply, because which of the survivors to use is
    still not the cluster's to say."""
    doc = _evidence(api_groups=dict(API_GROUPS, contour=True))
    m = _merge(doc, "sv_ingress", {"sv_ingress": "istio"})
    assert m.state == suggest.CONFLICT
    assert m.current == "istio"
    assert m.value is None
    assert "istio" in _for(doc, "sv_ingress").ruled_out


def test_every_option_a_suggestion_names_is_one_generate_actually_takes():
    """A suggestion for an option the generator has never heard of is a value
    that applies cleanly and changes nothing in the bundle. Checked here rather
    than discovered on a sealed cluster, in the same spirit as
    test_manual_facts' catalogue check."""
    from bzm_opl_gen.generate import DEFAULT_OPTIONS
    for doc in _every_fixture():
        for s in suggest.from_evidence(doc):
            assert s.option in DEFAULT_OPTIONS, s.option


def test_merge_reads_an_option_the_caller_left_out_as_its_default():
    """A caller holding a partial options dict -- the CLI's `generate --profile`
    shape, or a browser before /api/option-defaults lands -- must get the same
    answer as one holding the fully resolved set."""
    doc = _evidence(**PLAIN_K8S)
    assert _merge(doc, "platform", {}) == _merge(doc, "platform",
                                                 {"platform": "openshift"})


def test_an_applied_value_is_indistinguishable_from_a_typed_one():
    """The seam that makes the feature honest, and the same promise
    facts.manual() makes: nothing downstream learns that a value came off a
    cluster read. Applying is writing the option -- no marker, no wrapper, no
    second field -- so the bundle and the profile round-trip are byte-identical
    to the ones a person typing the same value gets."""
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
