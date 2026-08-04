"""The helm output format, without needing helm.

What is checked here is everything that is decided in Python: which files come
out, what the values overlay says, and the combinations generate() must refuse.
Whether the chart then *renders* those values into the same objects the
manifests format produces is a different question with a different dependency --
it needs the helm binary, so it lives in tests/helm_parity.py and runs as its
own CI job. Keeping it out of here is what lets this suite stay `N passed` with
nothing skipped on a machine with no helm installed.
"""

import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import generate as gen  # noqa: E402

from test_generate import FACTS  # noqa: E402

BASE = {"platform": "k8s", "ship_id": "bbb222", "namespace": "bzm-perf",
        "auth_token": "TOKEN", "output_format": "helm"}


def _values(**over):
    files = gen.generate(FACTS, {**BASE, **over})
    return yaml.safe_load(files[gen.HELM_VALUES_FILE]), files


# -- what comes out -----------------------------------------------------------

def test_emits_the_chart_plus_an_overlay():
    files = gen.generate(FACTS, BASE)
    assert gen.HELM_CHART_FILE in files
    assert f"{gen.CHART_DIR}/templates/deployment.yaml" in files
    assert gen.HELM_VALUES_FILE in files
    # No flat manifests: the chart's templates are the manifests now, and a
    # bundle carrying both would leave "which of these do I apply?" open.
    assert not any(n.startswith("bzm_") and n.endswith(".yaml") for n in files)


def test_chart_is_copied_verbatim():
    """The chart in the bundle is byte-identical to the one in the repo --
    including its values.yaml, which the overlay layers onto rather than
    replaces. A generated copy that had been rewritten could not be reviewed
    once and trusted everywhere."""
    files = gen.generate(FACTS, BASE)
    for name, content in files.items():
        if not name.startswith(f"{gen.CHART_DIR}/"):
            continue
        src = os.path.join(gen.HELM_DIR, name[len(gen.CHART_DIR) + 1:])
        with open(src) as f:
            assert f.read() == content, name
    assert f"{gen.CHART_DIR}/values.yaml" in files


def test_manifests_format_emits_no_chart():
    files = gen.generate(FACTS, {**BASE, "output_format": "manifests"})
    assert not any(n.startswith(f"{gen.CHART_DIR}/") for n in files)
    assert gen.HELM_VALUES_FILE not in files
    assert "bzm_deployment.yaml" in files


def test_profile_round_trips_the_format(tmp_path):
    """profile.json is what livetest and `generate --profile` replay from, so
    the format has to survive it -- otherwise a helm bundle silently
    re-generates as manifests."""
    files = gen.generate(FACTS, BASE)
    gen.write(files, str(tmp_path))
    assert gen.load_profile(str(tmp_path))["output_format"] == "helm"


def test_write_creates_chart_subdirectories(tmp_path):
    gen.write(gen.generate(FACTS, BASE), str(tmp_path))
    assert (tmp_path / gen.CHART_DIR / "templates" / "deployment.yaml").is_file()
    assert (tmp_path / gen.HELM_VALUES_FILE).is_file()


def test_preview_order_leads_with_the_generated_file():
    """The overlay is the only file in a chart bundle that came from the
    account, so it is the one a reviewer is looking for."""
    order = gen.preview_order(gen.generate(FACTS, BASE))
    assert order[0] == gen.HELM_VALUES_FILE
    assert set(order) == set(gen.generate(FACTS, BASE))


def test_preview_order_of_manifests_is_apply_order():
    files = gen.generate(FACTS, {**BASE, "output_format": "manifests"})
    order = gen.preview_order(files)
    applied = [n for n in order if n in gen.APPLY_ORDER]
    assert applied == [n for n in gen.APPLY_ORDER if n in files]
    assert set(order) == set(files)


def test_mirror_script_still_emitted_for_a_private_registry():
    files = gen.generate(FACTS, {**BASE, "private_registry": "reg.io/bzm"})
    assert "bzm-opl-image-mirror.sh" in files


# -- what the overlay says ----------------------------------------------------

def test_overlay_is_valid_yaml_and_carries_the_account_facts():
    v, _ = _values()
    assert v["harborId"] == FACTS["harbor_id"]
    assert v["shipId"] == "bbb222"
    assert v["authToken"] == "TOKEN"
    assert v["platform"] == "k8s"


def test_overlay_omits_chart_owned_defaults():
    """crane's resources and the probe timings are the chart's to define. If
    they appeared here, the two would drift the first time either changed."""
    v, _ = _values()
    assert "crane" not in v
    assert "probes" not in v


def test_crane_image_is_pinned_to_what_the_account_advertises():
    """The chart's own default floats on `latest` because a chart has no API
    access. A bundle generated against a real account does, and pins it."""
    v, _ = _values()
    assert v["image"]["repository"] == "gcr.io/verdant-bulwark-278/blazemeter/crane"
    assert v["image"]["tag"] == "3.7.55"


def test_unfetched_token_is_left_empty_not_placeholdered():
    """The default auth_token is a <YOUR_AUTH_TOKEN> placeholder for the
    manifests. Carried into values it would install an agent that authenticates
    with the literal string, so it becomes an empty value the chart rejects."""
    v, _ = _values(auth_token=gen.DEFAULT_OPTIONS["auth_token"])
    assert v["authToken"] == ""


def test_private_registry_carries_the_derived_image_map():
    v, _ = _values(private_registry="reg.io/bzm")
    assert v["privateRegistry"] == "reg.io/bzm"
    # Same keys the ConfigMap's IMAGE_OVERRIDES would carry, from the same facts.
    assert v["imageOverrides"] == gen._image_overrides(FACTS, "reg.io/bzm")


def test_no_private_registry_leaves_overrides_empty():
    v, _ = _values()
    assert v["privateRegistry"] == ""
    assert v["imageOverrides"] == {}


def test_proxy_credentials_are_embedded_in_the_url():
    v, _ = _values(proxy={"http": "http://px:3128", "username": "u", "password": "p"})
    assert v["proxy"]["enabled"] is True
    assert v["proxy"]["http"] == "http://u:p@px:3128"


def test_no_proxy_defaults_are_carried_even_when_off():
    v, _ = _values()
    assert v["proxy"]["enabled"] is False
    assert v["proxy"]["noProxy"] == gen.DEFAULT_NO_PROXY


def test_scheduling_survives_as_yaml_not_a_json_blob():
    """Tolerations are the thing most often hand-edited after generating, so
    they are emitted as a YAML list rather than the JSON block that would also
    have parsed."""
    tol = [{"key": "lifecycle", "operator": "Equal", "value": "spot",
            "effect": "NoSchedule"}]
    v, files = _values(tolerations=tol, node_selector={"workload": "perf"})
    assert v["tolerations"] == tol
    assert v["nodeSelector"] == {"workload": "perf"}
    assert "- key: " in files[gen.HELM_VALUES_FILE]


def test_engine_placement_is_written_out_not_left_for_the_chart_to_derive():
    """The chart renders a values file and cannot see the options that produced
    it, so "engines follow crane" has to be resolved here. With no engine
    override the two pairs are equal -- and that equality is a *copy*, not a
    fallback the chart performs."""
    tol = [{"key": "lifecycle", "operator": "Equal", "value": "spot",
            "effect": "NoSchedule"}]
    v, _ = _values(tolerations=tol, node_selector={"workload": "perf"})
    assert v["engineNodeSelector"] == v["nodeSelector"] == {"workload": "perf"}
    assert v["engineTolerations"] == v["tolerations"] == tol


def test_engine_pool_overrides_cranes_in_the_overlay():
    v, files = _values(node_selector={"pool": "crane"},
                       engine_node_selector={"pool": "bzm-engines"},
                       engine_tolerations=[{"key": "bzm.io/engines",
                                            "operator": "Equal", "value": "true",
                                            "effect": "NoSchedule"}])
    assert v["nodeSelector"] == {"pool": "crane"}
    assert v["engineNodeSelector"] == {"pool": "bzm-engines"}
    assert v["engineTolerations"][0]["key"] == "bzm.io/engines"
    assert v["tolerations"] == []          # crane's pool needs no taint
    # Hand-editable, same as crane's list.
    assert "- key: " in files[gen.HELM_VALUES_FILE]
    # The chart carries the recipe for the pool it now selects.
    assert gen.NODEPOOLS_FILE in files


def test_explicitly_unpinned_engines_are_empty_in_the_overlay_not_cranes():
    """The case a fallback in the chart would destroy: crane pinned to a tainted
    infra pool, engines deliberately free. `{}` has to reach the values file as
    `{}`, because `{{- with .Values.engineNodeSelector }}` is the only thing
    standing between that and every engine inheriting crane's taint."""
    v, _ = _values(node_selector={"pool": "infra"},
                   tolerations=[{"key": "infra", "operator": "Exists",
                                 "effect": "NoSchedule"}],
                   engine_node_selector={}, engine_tolerations=[])
    assert v["nodeSelector"] == {"pool": "infra"}
    assert v["engineNodeSelector"] == {}
    assert v["engineTolerations"] == []


@pytest.mark.parametrize("opts,mode,extra", [
    ({"ca_bundle": "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----"},
     "inline", {"pem": "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n"}),
    ({"ca_existing_configmap": "trust-bundle", "ca_configmap_key": "tls.pem"},
     "existing", {"existingConfigMap": "trust-bundle", "key": "tls.pem"}),
    ({"platform": "openshift", "ca_openshift_inject": True}, "openshiftInject", {}),
    ({}, "none", {}),
])
def test_ca_modes_map_to_the_charts_vocabulary(opts, mode, extra):
    v, _ = _values(**opts)
    assert v["caBundle"]["mode"] == mode
    for k, want in extra.items():
        assert v["caBundle"][k] == want


def test_overlay_carries_no_limitrange_at_all():
    """It used to, and pinned the max computed at generate time -- so raising
    the engine size later produced `default` above `max` and the API server
    rejected the object mid-upgrade, ConfigMap already applied. The object is
    gone entirely now: it could not change what crane requests for engines, and
    the defaults it did apply landed on crane's own helper pods."""
    for over in ({}, {"engine_cpu_limit": "500m", "engine_mem_limit": "1Gi"},
                 {"engine_cpu_limit": "4", "engine_mem_limit": "16Gi"}):
        v, _ = _values(**over)
        assert "limitRange" not in v, over


def test_chart_carries_the_default_engine_limits_too():
    """The chart's ConfigMap emits the two engine limits unconditionally,
    falling back to the generator's own defaults when the overlay is empty --
    the same change the manifests emitter made for #132, restated in Go
    templates because the chart renders with no Python in reach. The default
    literals live in the template, so this holds them equal to
    ENGINE_DEFAULT_CPU/MEM; whether the render then matches the manifests
    object-for-object is helm_parity.py's job."""
    with open(os.path.join(gen.HELM_DIR, "templates", "configmap.yaml")) as f:
        template = f.read()
    for values_key, env, default in (
            ("engine.cpuLimit", "KUBERNETES_RESOURCES_LIMITS_CPU",
             gen.ENGINE_DEFAULT_CPU),
            ("engine.memoryLimit", "KUBERNETES_RESOURCES_LIMITS_MEMORY",
             gen.ENGINE_DEFAULT_MEM)):
        # Unconditional: a `with` block around the env is the old shape, where
        # an empty overlay meant no limits at all.
        assert f"with .Values.{values_key}" not in template
        line = next(l for l in template.splitlines() if l.startswith(f"  {env}:"))
        assert f'default "{default}"' in line, line


def test_overlay_offers_no_engine_request_knob():
    """Crane stamps engine requests itself; a value that silently did nothing
    would be worse than its absence."""
    v, files = _values(engine_cpu_limit="4", engine_mem_limit="16Gi")
    assert "cpuRequest" not in v["engine"]
    assert "memoryRequest" not in v["engine"]
    assert "not settable" in files[gen.HELM_VALUES_FILE]


def test_auto_update_is_left_to_the_chart_when_unset():
    """A Helm-managed release usually wants autoUpdate false -- crane otherwise
    takes ownership of its own Deployment and the next upgrade conflicts -- but
    that changes how the customer's agent gets upgraded, so an overlay that was
    not told stays silent and lets the chart resolve it from privateRegistry."""
    v, files = _values()
    assert "autoUpdate" in v
    assert v["autoUpdate"] is None
    assert "helm upgrade" in files[gen.HELM_VALUES_FILE]


def test_auto_update_is_stated_when_it_was_chosen():
    """Stated, not left to the chart to infer: the chart resolves an unset
    value from privateRegistry, so a later edit adding or dropping a registry
    would flip a setting somebody had made deliberately."""
    assert _values(auto_update=False)[0]["autoUpdate"] is False
    assert _values(auto_update=True)[0]["autoUpdate"] is True
    v, _ = _values(auto_update=True, private_registry="reg.local/bzm")
    assert v["autoUpdate"] is True, "an explicit value must survive a registry"


def test_readme_upgrade_advice_matches_the_overlay():
    """Two instructions, and the wrong one wastes the upgrade: the default
    bundle upgrades normally, and only a bundle that asked for auto-update
    needs telling that it cannot."""
    default = gen.generate(FACTS, BASE)["README.md"]
    assert "autoUpdate: false" not in default and "helm upgrade" in default
    on = gen.generate(FACTS, {**BASE, "auto_update": True})["README.md"]
    assert "autoUpdate: false" in on


def test_engine_sizing_is_passed_through_unresolved():
    """Empty means "chart default", which is BlazeMeter's documented footprint.
    Resolving it here would bake today's default into every bundle."""
    v, _ = _values()
    assert v["engine"]["cpuLimit"] == ""
    assert v["engine"]["memoryLimit"] == ""
    v, _ = _values(engine_cpu_limit="2", engine_mem_limit="8Gi",
                   engine_ephemeral_limit_mb=61440)
    assert v["engine"]["cpuLimit"] == "2"
    assert v["engine"]["memoryLimit"] == "8Gi"
    assert v["engine"]["ephemeralLimitMb"] == "61440"


# -- what it refuses ----------------------------------------------------------

def test_nodeport_is_not_refused_without_cluster_rbac():
    """The chart used to `fail` on NODEPORT unless clusterRbac was on. A live
    performance location disproved the premise: deployed with namespaced RBAC
    only and no ClusterRole in the cluster, crane came online, created its
    `NodePort` Service through the namespaced Role, and ran a real engine to
    ENDED. It resolves its advertised address from its own interfaces
    (`crane_updater/machine_ip_finder.py`), never from the Node -- nothing in
    the log was forbidden, so there was no 127.0.0.1 fallback to protect
    against. The manifests format has always rendered this pairing; the chart
    refusing it sent customers to ask for the one permission a locked-down
    cluster will not grant."""
    v, files = _values(service_type="NODEPORT")
    assert v["serviceType"] == "NODEPORT"
    assert v["clusterRbac"] is False
    validate = files[f"{gen.CHART_DIR}/templates/_helpers.tpl"]
    coupled = [ln for ln in validate.splitlines()
               if "fail" in ln and "NODEPORT" in ln and "clusterRbac" in ln]
    assert not coupled, f"chart still refuses the pairing: {coupled}"


def test_no_chart_file_claims_nodeport_needs_the_node_object():
    """The refusal is only half of it -- the reason travelled, into values.yaml,
    the chart README and the ClusterRole's own header. Left there, a customer
    who never hits the guard still reads that NODEPORT costs cluster-scoped
    RBAC. The node reads themselves stay optional (capacity awareness); it is
    the tie to serviceType that is wrong."""
    _, files = _values(service_type="NODEPORT")
    chart = {n: t for n, t in files.items() if n.startswith(f"{gen.CHART_DIR}/")}
    assert chart
    for name, text in chart.items():
        low = text.lower()
        for claim in ("falls back to 127.0.0.1", "requires clusterrbac",
                      "needs clusterrbac"):
            assert claim not in low, f"{name} still says {claim!r}"


def test_service_account_defaults_are_stated_not_left_to_the_chart():
    """Unlike crane's resources, this one is the customer's answer, so the
    overlay states it. `name` is written out rather than left empty even at the
    default: the manifests format has no fullname to fall back to, and the two
    formats agreeing on the rendered name is what helm_parity checks."""
    v, _ = _values()
    assert v["serviceAccount"] == {"create": True, "name": "crane",
                                   "annotations": {}}


def test_existing_service_account_reaches_the_overlay():
    v, _ = _values(service_account_name="platform-sa",
                   service_account_create=False)
    assert v["serviceAccount"]["create"] is False
    assert v["serviceAccount"]["name"] == "platform-sa"


def test_the_cluster_check_reaches_the_overlay_only_when_asked_for():
    """The chart's default is off, and an overlay that restated every default
    would stop being the record of what was chosen."""
    on, files = _values(crane_hook=True)
    assert on["craneHook"]["enabled"] is True
    # ...and it is a chart file, not a flat manifest: the chart carries it as a
    # `helm test` hook, so it is in templates/, not beside bzm_deployment.yaml.
    assert "helm/templates/tests/cranehook.yaml" in files
    assert gen.HOOK_FILE not in files
    off, _ = _values()
    assert "craneHook" not in off


def test_helm_readme_names_a_service_account_it_will_not_create():
    _, files = _values(service_account_name="platform-sa",
                       service_account_create=False)
    assert "platform-sa" in files["README.md"]
    _, plain = _values()
    assert "must already exist" not in plain["README.md"]


def test_unnamed_service_account_is_refused_in_helm_format_too():
    """The chart refuses the same combination in Go (see bzm-opl.validate), but
    a bundle that only fails at `helm install` has already been handed over."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**BASE, "service_account_name": "",
                             "service_account_create": False})
    assert "service_account_name" in str(e.value)


def test_service_virtualization_is_refused():
    """The chart is performance-only. Emitting one that quietly dropped the
    ingress, its RBAC and the TLS secret would deploy, report idle, and stall at
    WAITING_FOR_DOMAIN -- the exact silent failure the SV validation exists to
    prevent."""
    sv_facts = dict(FACTS, func_ids=["mockServices"])
    with pytest.raises(ValueError) as e:
        gen.generate(sv_facts, {**BASE, "sv_ingress": "nginx",
                                "sv_subdomain": "apps.example.com",
                                "sv_tls_secret": "wildcard"})
    assert "performance testing only" in str(e.value)
    assert "manifests" in str(e.value)


def test_a_declined_sv_location_may_have_the_chart():
    """The refusal is about what the chart cannot carry, not about the location.

    Declared performance-only, there is no ingress, no SV RBAC and no TLS secret
    to drop -- so the chart carries everything this bundle asks for, and holding
    such a location to manifests would be refusing what generate() accepts.
    """
    sv_facts = dict(FACTS, func_ids=["mockServices"])
    files = gen.generate(sv_facts, {**BASE, "sv_ingress": gen.SV_INGRESS_NONE})
    assert gen.HELM_VALUES_FILE in files


def test_service_virtualization_still_works_as_manifests():
    sv_facts = dict(FACTS, func_ids=["mockServices"])
    files = gen.generate(sv_facts, {**BASE, "output_format": "manifests",
                                    "sv_ingress": "nginx",
                                    "sv_subdomain": "apps.example.com",
                                    "sv_tls_secret": "wildcard"})
    assert "networking.k8s.io" in files["bzm_role.yaml"]


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**BASE, "output_format": "kustomize"})
    assert "output_format" in str(e.value)


def test_bad_engine_size_is_still_caught_in_helm_format():
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**BASE, "engine_mem_limit": "not-a-quantity"})
    assert "engine_mem_limit" in str(e.value)


def test_chart_defaults_to_restricted_engines():
    """The chart's own default matters more than the overlay here: a bare
    `helm install` of this chart used to get privileged engines, because
    values.yaml sets platform=k8s and the template gated on it."""
    _, files = _values()
    chart = yaml.safe_load(files[f"{gen.CHART_DIR}/values.yaml"])
    assert chart["restrictEngines"] is True


def test_restrict_engines_off_reaches_the_overlay():
    values, _ = _values(restrict_engines=False)
    assert values["restrictEngines"] is False


def test_restrict_engines_on_leaves_the_overlay_silent():
    values, _ = _values()
    assert "restrictEngines" not in values


def test_chart_default_crane_ephemeral_storage_is_a_matched_pair():
    """The chart carries its own defaults, so the manifests-side constant does
    not reach it -- this is the restatement that can drift. Equal request and
    limit is the property: GKE Autopilot rewrites the limit down to the request,
    and a chart whose request is the smaller number evicts crane on Autopilot
    while rendering perfectly well everywhere the parity test can look."""
    _, files = _values()
    chart = yaml.safe_load(files[f"{gen.CHART_DIR}/values.yaml"])
    res = chart["crane"]["resources"]
    assert (res["requests"]["ephemeral-storage"]
            == res["limits"]["ephemeral-storage"]
            == gen.CRANE_EPHEMERAL_STORAGE)


def test_crane_ephemeral_storage_override_reaches_the_overlay_as_both_fields():
    values, _ = _values(crane_ephemeral_storage="4Gi")
    res = values["crane"]["resources"]
    assert res["requests"]["ephemeral-storage"] == "4Gi"
    assert res["limits"]["ephemeral-storage"] == "4Gi"


def test_unset_crane_ephemeral_storage_leaves_the_overlay_silent():
    """The overlay names only what came from the account or the flags; an
    untouched default belongs to the chart, not to a key repeated here."""
    values, _ = _values()
    assert "crane" not in values


# -- the bundle a chart install actually needs --------------------------------

def test_readme_is_short_and_actionable():
    """Handed to a customer, so it is instructions; the chart's own README keeps
    the reasoning. This one used to run to 78 lines."""
    readme = gen.generate(FACTS, BASE)["README.md"]
    assert len(readme.splitlines()) < 50, "README is creeping back towards an essay"
    assert "helm install crane" in readme
    assert "rollout status deploy/crane" in readme
    assert "online" in readme
    # The default bundle upgrades normally now, so the README says so rather
    # than carrying the old "set autoUpdate: false first" instruction.
    assert "helm upgrade" in readme
    assert "autoUpdate: false" not in readme


def test_readme_names_the_overlay_in_its_install_command():
    files = gen.generate(FACTS, BASE)
    readme = files["README.md"]
    assert f"-f {gen.HELM_VALUES_FILE}" in readme
    assert f"./{gen.CHART_DIR}" in readme
    assert "bzm-perf" in readme


def test_readme_tells_you_to_pass_a_token_when_none_was_fetched():
    files = gen.generate(FACTS, {**BASE, "auth_token": gen.DEFAULT_OPTIONS["auth_token"]})
    assert "--set-string authToken=" in files["README.md"]
    files = gen.generate(FACTS, BASE)
    assert "--set-string authToken=" not in files["README.md"]


def test_overlay_json_values_are_parseable_where_the_chart_re_encodes_them():
    """nodeSelector and tolerations become KUBERNETES_*_JSON envs in the
    rendered ConfigMap, so what the overlay holds has to survive a YAML load as
    the structure the chart will re-encode."""
    v, _ = _values(node_selector={"node-role.kubernetes.io/perf": "true"})
    assert json.dumps(v["nodeSelector"])  # no exotic types survived
    assert v["nodeSelector"] == {"node-role.kubernetes.io/perf": "true"}
