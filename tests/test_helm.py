"""The helm output format, without the helm binary: the files, the values
overlay and the refusals. Rendering parity with the manifests is
tests/helm_parity.py.
"""

import json
import os
import re
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import generate as gen  # noqa: E402
from bzm_opl_gen import (bundle_env, bundle_names, bundle_options,  # noqa: E402
                         footprint as footprint_mod, image_registry,
                         markers as markers_mod, service_virt)

from test_generate import FACTS  # noqa: E402

BASE = {"platform": "k8s", "ship_id": "bbb222", "namespace": "bzm-perf",
        "auth_token": "TOKEN", "output_format": "helm"}


def _values(**over):
    files = gen.generate(FACTS, {**BASE, **over})
    return yaml.safe_load(files[bundle_names.HELM_VALUES_FILE]), files


# -- what comes out -----------------------------------------------------------

def test_emits_the_chart_plus_an_overlay():
    files = gen.generate(FACTS, BASE)
    assert bundle_names.HELM_CHART_FILE in files
    assert f"{bundle_names.CHART_DIR}/templates/deployment.yaml" in files
    assert bundle_names.HELM_VALUES_FILE in files
    # No flat manifests beside the chart.
    assert not any(n.startswith("bzm_") and n.endswith(".yaml") for n in files)


def test_chart_is_copied_verbatim():
    """The chart in the bundle is byte-identical to the packaged one,
    values.yaml included."""
    files = gen.generate(FACTS, BASE)
    for name, content in files.items():
        if not name.startswith(f"{bundle_names.CHART_DIR}/"):
            continue
        src = os.path.join(bundle_names.HELM_DIR, name[len(bundle_names.CHART_DIR) + 1:])
        with open(src) as f:
            assert f.read() == content, name
    assert f"{bundle_names.CHART_DIR}/values.yaml" in files


def test_manifests_format_emits_no_chart():
    files = gen.generate(FACTS, {**BASE, "output_format": "manifests"})
    assert not any(n.startswith(f"{bundle_names.CHART_DIR}/") for n in files)
    assert bundle_names.HELM_VALUES_FILE not in files
    assert "bzm_deployment.yaml" in files


def test_profile_round_trips_the_format(tmp_path):
    """profile.json records the helm format, so a replay regenerates a
    chart."""
    files = gen.generate(FACTS, BASE)
    gen.write(files, str(tmp_path))
    assert gen.load_profile(str(tmp_path))["output_format"] == "helm"


def test_write_creates_chart_subdirectories(tmp_path):
    gen.write(gen.generate(FACTS, BASE), str(tmp_path))
    assert (tmp_path / bundle_names.CHART_DIR / "templates" / "deployment.yaml").is_file()
    assert (tmp_path / bundle_names.HELM_VALUES_FILE).is_file()


def test_preview_order_leads_with_the_generated_file():
    """The values overlay is listed first."""
    order = gen.preview_order(gen.generate(FACTS, BASE))
    assert order[0] == bundle_names.HELM_VALUES_FILE
    assert set(order) == set(gen.generate(FACTS, BASE))


def test_preview_order_of_manifests_is_apply_order():
    files = gen.generate(FACTS, {**BASE, "output_format": "manifests"})
    order = gen.preview_order(files)
    applied = [n for n in order if n in bundle_names.APPLY_ORDER]
    assert applied == [n for n in bundle_names.APPLY_ORDER if n in files]
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
    """Crane's resources and the probe timings are left to the chart."""
    v, _ = _values()
    assert "crane" not in v
    assert "probes" not in v


def test_crane_image_is_pinned_to_what_the_account_advertises():
    """The overlay pins the crane image the account advertises."""
    v, _ = _values()
    assert v["image"]["repository"] == "gcr.io/verdant-bulwark-278/blazemeter/crane"
    assert v["image"]["tag"] == "3.7.55"


def test_unfetched_token_is_left_empty_not_placeholdered():
    """An absent token is an empty authToken, not a marker, and earns no
    not-finished banner: a chart takes it at install time."""
    v, plain = _values(auth_token=bundle_options.DEFAULT_OPTIONS["auth_token"])
    assert v["authToken"] == ""
    assert not markers_mod.MARKER_RE.search(plain[bundle_names.HELM_VALUES_FILE])
    assert "not finished" not in plain["README.md"]


def test_the_not_finished_block_says_the_token_is_missing_too():
    """With other fields blank, the not-finished block also says the token
    is supplied at install time, without a row or marker for it."""
    # A location that does not exist yet, with namespace and ServiceAccount
    # blank too: four markers, plus the token.
    from bzm_opl_gen import facts as facts_mod
    files = gen.generate(facts_mod.manual("", ""),
                         {**BASE, "ship_id": "", "namespace": "",
                          "service_account_name": "", "auth_token": ""})
    readme = files["README.md"]
    # Unwrap the quoted, filled block before matching.
    said = " ".join(readme.replace("\n> ", " ").split())
    assert "4 fields were left blank" in said
    assert "AUTH_TOKEN is not among them and not in this bundle at all" in said
    assert "`--set-string authToken=...` in the Deploy command below" in said
    # Said, not listed: no row, and no <AUTH_TOKEN> in the values file.
    assert "| `auth_token` |" not in readme
    assert not markers_mod.MARKER_RE.search(files[bundle_names.HELM_VALUES_FILE].replace(
        markers_mod.marker("harbor_id"), "").replace(markers_mod.marker("ship_id"), "")
        .replace(markers_mod.marker("namespace"), "")
        .replace(markers_mod.marker("service_account_name"), ""))


def test_a_supplied_token_is_not_reported_as_missing():
    """An embedded token earns no install-time token sentence and no
    --set-string."""
    files = gen.generate(FACTS, {**BASE, "namespace": "",
                                 "auth_token": "de" * 32})
    readme = files["README.md"]
    assert "not finished" in readme                 # the namespace still is
    assert "AUTH_TOKEN is not among them" not in readme
    assert "--set-string authToken" not in readme


def test_only_an_absent_token_still_earns_no_banner():
    """A chart with every field filled and the token left for install time
    is finished."""
    files = gen.generate(FACTS, {**BASE, "auth_token": ""})
    assert "not finished" not in files["README.md"]
    assert "--set-string authToken" in files["README.md"]


def test_private_registry_carries_the_derived_image_map():
    v, _ = _values(private_registry="reg.io/bzm")
    assert v["privateRegistry"] == "reg.io/bzm"
    # Same keys the ConfigMap's IMAGE_OVERRIDES would carry, from the same facts.
    assert v["imageOverrides"] == image_registry.image_overrides(
        FACTS, {"private_registry": "reg.io/bzm"})


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
    assert v["proxy"]["noProxy"] == bundle_env.DEFAULT_NO_PROXY


def test_scheduling_survives_as_yaml_not_a_json_blob():
    """Tolerations are written as an editable YAML list."""
    tol = [{"key": "lifecycle", "operator": "Equal", "value": "spot",
            "effect": "NoSchedule"}]
    v, files = _values(tolerations=tol, node_selector={"workload": "perf"})
    assert v["tolerations"] == tol
    assert v["nodeSelector"] == {"workload": "perf"}
    assert "- key: " in files[bundle_names.HELM_VALUES_FILE]


def test_engine_placement_is_written_out_not_left_for_the_chart_to_derive():
    """With no engine override, the engine placement is written out as a
    copy of crane's."""
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
    assert "- key: " in files[bundle_names.HELM_VALUES_FILE]
    # The chart carries the recipe for the pool it now selects.
    assert bundle_names.NODEPOOLS_FILE in files


def test_explicitly_unpinned_engines_are_empty_in_the_overlay_not_cranes():
    """Explicitly empty engine placement reaches the overlay empty, not as
    crane's."""
    v, _ = _values(node_selector={"pool": "infra"},
                   tolerations=[{"key": "infra", "operator": "Exists",
                                 "effect": "NoSchedule"}],
                   engine_node_selector={}, engine_tolerations=[])
    assert v["nodeSelector"] == {"pool": "infra"}
    assert v["engineNodeSelector"] == {}
    assert v["engineTolerations"] == []


@pytest.mark.parametrize("opts,mode,extra", [
    # The overlay names the certificate file; `pem` is always empty.
    ({"ca_bundle": "-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----"},
     "inline", {"pem": "", "file": "ca-bundle.crt"}),
    ({"ca_bundle_slot": True, "ca_cert_file": "corp-root.crt"},
     "inline", {"pem": "", "file": "corp-root.crt", "key": "corp-root.crt"}),
    ({"ca_existing_configmap": "trust-bundle", "ca_configmap_key": "tls.pem"},
     "existing", {"existingConfigMap": "trust-bundle", "key": "tls.pem"}),
    ({"platform": "openshift", "openshift_cluster": True,
      "ca_openshift_inject": True}, "openshiftInject", {}),
    ({}, "none", {}),
])
def test_ca_modes_map_to_the_charts_vocabulary(opts, mode, extra):
    v, _ = _values(**opts)
    assert v["caBundle"]["mode"] == mode
    for k, want in extra.items():
        assert v["caBundle"][k] == want


def test_the_chart_keeps_its_install_time_refusal_and_carries_no_guard():
    """A file-mode CA adds no initContainer, and the README points at the
    chart directory for the certificate."""
    _, files = _values(ca_bundle_slot=True, ca_cert_file="corp-root.crt")
    for name, text in files.items():
        assert "initContainers" not in text, name
    readme = files["README.md"]
    assert bundle_names.HELM_VALUES_FILE in readme and "helm install" in readme
    assert bundle_names.CA_CONFIGMAP_FILE not in readme
    # helm reads the file only from the chart directory.
    assert f"{bundle_names.CHART_DIR}/" in readme and "corp-root.crt" in readme


def test_overlay_carries_no_limitrange_at_all():
    """The overlay carries no LimitRange for any engine size."""
    for over in ({}, {"engine_cpu_limit": "500m", "engine_mem_limit": "1Gi"},
                 {"engine_cpu_limit": "4", "engine_mem_limit": "16Gi"}):
        v, _ = _values(**over)
        assert "limitRange" not in v, over


def test_chart_carries_the_default_engine_limits_too():
    """The chart's ConfigMap always sets the engine limits, through helpers
    whose defaults equal ENGINE_DEFAULT_CPU/MEM."""
    with open(os.path.join(bundle_names.HELM_DIR, "templates", "configmap.yaml")) as f:
        template = f.read()
    with open(os.path.join(bundle_names.HELM_DIR, "templates", "_helpers.tpl")) as f:
        helpers = f.read()
    for values_key, env, helper, default in (
            ("engine.cpuLimit", "KUBERNETES_RESOURCES_LIMITS_CPU",
             "bzm-opl.engineCpuLimit", footprint_mod.ENGINE_DEFAULT_CPU),
            ("engine.memoryLimit", "KUBERNETES_RESOURCES_LIMITS_MEMORY",
             "bzm-opl.engineMemoryLimit", footprint_mod.ENGINE_DEFAULT_MEM)):
        # Unconditional: no `with` block around the env.
        assert f"with .Values.{values_key}" not in template
        line = next(l for l in template.splitlines() if l.startswith(f"  {env}:"))
        assert f'include "{helper}"' in line, line
        defn = next(l for l in helpers.splitlines() if f'define "{helper}"' in l)
        assert f'default "{default}" .Values.{values_key}' in defn, defn


def test_overlay_offers_no_engine_request_knob():
    """The overlay has no engine request value, and says why."""
    v, files = _values(engine_cpu_limit="4", engine_mem_limit="16Gi")
    assert "cpuRequest" not in v["engine"]
    assert "memoryRequest" not in v["engine"]
    assert "not settable" in files[bundle_names.HELM_VALUES_FILE]


def test_auto_update_is_left_to_the_chart_when_unset():
    """Unset auto_update stays unset in the overlay, with a note about helm
    upgrade."""
    v, files = _values()
    assert "autoUpdate" in v
    assert v["autoUpdate"] is None
    assert "helm upgrade" in files[bundle_names.HELM_VALUES_FILE]


def test_auto_update_is_stated_when_it_was_chosen():
    """An explicit auto_update is written out, whatever the registry."""
    assert _values(auto_update=False)[0]["autoUpdate"] is False
    assert _values(auto_update=True)[0]["autoUpdate"] is True
    v, _ = _values(auto_update=True, private_registry="reg.local/bzm")
    assert v["autoUpdate"] is True, "an explicit value must survive a registry"


def test_readme_upgrade_advice_matches_the_overlay():
    """The README's upgrade advice follows auto_update."""
    default = gen.generate(FACTS, BASE)["README.md"]
    assert "autoUpdate: false" not in default and "helm upgrade" in default
    on = gen.generate(FACTS, {**BASE, "auto_update": True})["README.md"]
    assert "autoUpdate: false" in on


def test_engine_sizing_is_passed_through_unresolved():
    """Unset engine limits stay empty, so the chart default applies."""
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
    """NODEPORT without clusterRbac is accepted; the chart has no refusal
    for it."""
    v, files = _values(service_type="NODEPORT")
    assert v["serviceType"] == "NODEPORT"
    assert v["clusterRbac"] is False
    validate = files[f"{bundle_names.CHART_DIR}/templates/_helpers.tpl"]
    coupled = [ln for ln in validate.splitlines()
               if "fail" in ln and "NODEPORT" in ln and "clusterRbac" in ln]
    assert not coupled, f"chart still refuses the pairing: {coupled}"


def test_no_chart_file_claims_nodeport_needs_the_node_object():
    """No chart file says NODEPORT requires cluster RBAC."""
    _, files = _values(service_type="NODEPORT")
    chart = {n: t for n, t in files.items() if n.startswith(f"{bundle_names.CHART_DIR}/")}
    assert chart
    for name, text in chart.items():
        low = text.lower()
        for claim in ("falls back to 127.0.0.1", "requires clusterrbac",
                      "needs clusterrbac"):
            assert claim not in low, f"{name} still says {claim!r}"


def test_service_account_defaults_are_stated_not_left_to_the_chart():
    """The overlay states the ServiceAccount, name included, even at the
    default."""
    v, _ = _values()
    assert v["serviceAccount"] == {"create": True, "name": "crane",
                                   "annotations": {}}


def test_existing_service_account_reaches_the_overlay():
    v, _ = _values(service_account_name="platform-sa",
                   service_account_create=False)
    assert v["serviceAccount"]["create"] is False
    assert v["serviceAccount"]["name"] == "platform-sa"


def test_the_cluster_check_reaches_the_overlay_only_when_asked_for():
    """craneHook is in the overlay only when enabled."""
    on, files = _values(crane_hook=True)
    assert on["craneHook"]["enabled"] is True
    # A `helm test` hook in the chart, not a flat manifest.
    assert "helm/templates/tests/cranehook.yaml" in files
    assert bundle_names.HOOK_FILE not in files
    off, _ = _values()
    assert "craneHook" not in off


def test_helm_readme_names_a_service_account_it_will_not_create():
    _, files = _values(service_account_name="platform-sa",
                       service_account_create=False)
    assert "platform-sa" in files["README.md"]
    _, plain = _values()
    assert "must already exist" not in plain["README.md"]


def test_unnamed_service_account_is_marked_in_helm_format_too():
    """A blank ServiceAccount name becomes its marker and is named in the
    README."""
    v, plain = _values(service_account_name="", service_account_create=False)
    assert v["serviceAccount"]["name"] == markers_mod.marker("service_account_name")
    assert "service_account_name" in plain["README.md"]
    assert "not finished" in plain["README.md"]


def test_the_charts_marker_test_is_the_generators_pattern():
    """The chart's marker regex equals markers.MARKER_PATTERN: it matches
    every generated marker and nothing else."""
    tpl = os.path.join(os.path.dirname(__file__), "..", "bzm_opl_gen",
                       "templates", "helm", "templates", "_helpers.tpl")
    with open(tpl) as fh:
        text = fh.read()
    found = re.search(r'regexMatch "([^"]+)" \$held', text)
    assert found, "the marker test is not where it was -- was it rewritten?"
    assert found.group(1) == f"^{markers_mod.MARKER_PATTERN}$"
    chart_re = re.compile(found.group(1))
    for key in ("auth_token", "service_account_name", "private_registry",
                "ca_existing_configmap", "ca_bundle", "proxy.https"):
        assert chart_re.fullmatch(markers_mod.marker(key)), key
    # ...and nothing else.
    for held in ("", "TOKEN", "<not a marker>", "reg.example.com/bzm",
                 "http://px:3128"):
        assert not chart_re.fullmatch(held), held


SV_FACTS = dict(FACTS, func_ids=["mockServices"])
SV_OPTS = {"sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
           "sv_tls_secret": "wildcard"}


def test_service_virtualization_reaches_the_overlay():
    """The SV options reach the overlay's sv block."""
    values, _ = _values(**SV_OPTS)
    assert values["sv"] == {"ingress": "nginx", "subdomain": "apps.example.com",
                            "tlsSecret": "wildcard"}


def test_the_istio_gateway_is_written_only_for_istio():
    """sv.istioGateway is written only for istio, empty when unset."""
    istio, _ = _values(sv_ingress="istio", sv_subdomain="apps.example.com",
                       sv_tls_secret="wildcard", sv_istio_gateway="shared-gw")
    assert istio["sv"]["istioGateway"] == "shared-gw"
    default, _ = _values(sv_ingress="istio", sv_subdomain="apps.example.com",
                         sv_tls_secret="wildcard")
    assert default["sv"]["istioGateway"] == ""
    nginx, _ = _values(**SV_OPTS)
    assert "istioGateway" not in nginx["sv"]


def test_a_declined_location_writes_no_sv_block():
    """sv_ingress none, like unset, writes no sv block."""
    values, files = _values(sv_ingress=service_virt.SV_INGRESS_NONE)
    assert "sv" not in values
    assert bundle_names.HELM_VALUES_FILE in files
    plain, _ = _values()
    assert "sv" not in plain


def test_the_charts_backend_table_is_the_generators():
    """The chart's svBackends block equals SV_INGRESS_BACKENDS field by
    field, via_ingress_class aside."""
    tpl = os.path.join(os.path.dirname(__file__), "..", "bzm_opl_gen",
                       "templates", "helm", "templates", "_helpers.tpl")
    with open(tpl) as fh:
        text = fh.read()
    body = re.search(r'{{- define "bzm-opl\.svBackends" -}}\n(.*?)\n{{- end -}}',
                     text, re.S)
    assert body, "the chart's backend table is not where it was -- renamed?"
    chart = yaml.safe_load(body.group(1))
    assert set(chart) == set(service_virt.SV_INGRESS_BACKENDS)
    for name, backend in service_virt.SV_INGRESS_BACKENDS.items():
        assert chart[name] == {
            "group": backend.group, "resources": list(backend.resources),
            "creates": backend.creates, "nodeportOk": backend.nodeport_ok,
            "tlsSecretRead": backend.tls_secret_read}, name


def test_the_readme_names_the_wildcard_secret_the_bundle_does_not_create():
    """The README names the TLS secret and its namespace, only for backends
    that read it."""
    readme = gen.generate(SV_FACTS, {**BASE, **SV_OPTS})["README.md"]
    assert "wildcard" in readme and BASE["namespace"] in readme
    silent = gen.generate(SV_FACTS, {**BASE, "sv_ingress": "istio",
                                     "sv_subdomain": "apps.example.com",
                                     "sv_tls_secret": "wildcard"})["README.md"]
    assert "has to be in" not in silent


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
    """The chart's own default restricts engines."""
    _, files = _values()
    chart = yaml.safe_load(files[f"{bundle_names.CHART_DIR}/values.yaml"])
    assert chart["restrictEngines"] is True


def test_restrict_engines_off_reaches_the_overlay():
    values, _ = _values(restrict_engines=False)
    assert values["restrictEngines"] is False


def test_restrict_engines_on_leaves_the_overlay_silent():
    values, _ = _values()
    assert "restrictEngines" not in values


def test_chart_default_crane_ephemeral_storage_is_a_matched_pair():
    """The chart's crane ephemeral-storage request equals its limit and the
    generator's default."""
    _, files = _values()
    chart = yaml.safe_load(files[f"{bundle_names.CHART_DIR}/values.yaml"])
    res = chart["crane"]["resources"]
    assert (res["requests"]["ephemeral-storage"]
            == res["limits"]["ephemeral-storage"]
            == footprint_mod.CRANE_EPHEMERAL_STORAGE)


def test_crane_ephemeral_storage_override_reaches_the_overlay_as_both_fields():
    values, _ = _values(crane_ephemeral_storage="4Gi")
    res = values["crane"]["resources"]
    assert res["requests"]["ephemeral-storage"] == "4Gi"
    assert res["limits"]["ephemeral-storage"] == "4Gi"


def test_unset_crane_ephemeral_storage_leaves_the_overlay_silent():
    """An unset crane ephemeral storage adds no crane block."""
    values, _ = _values()
    assert "crane" not in values


# -- the bundle a chart install actually needs --------------------------------

def test_readme_is_short_and_actionable():
    """The README stays short and carries install, verify and upgrade steps."""
    readme = gen.generate(FACTS, BASE)["README.md"]
    assert len(readme.splitlines()) < 45, "README is getting long"
    assert "helm install crane" in readme
    assert "rollout status deploy/crane" in readme
    assert "online" in readme
    # Auto-update is off by default, so no autoUpdate advice.
    assert "helm upgrade" in readme
    assert "autoUpdate: false" not in readme


def test_readme_names_the_overlay_in_its_install_command():
    files = gen.generate(FACTS, BASE)
    readme = files["README.md"]
    assert f"-f {bundle_names.HELM_VALUES_FILE}" in readme
    assert f"./{bundle_names.CHART_DIR}" in readme
    assert "bzm-perf" in readme


def test_readme_tells_you_to_pass_a_token_when_none_was_fetched():
    files = gen.generate(FACTS, {**BASE, "auth_token": bundle_options.DEFAULT_OPTIONS["auth_token"]})
    assert "--set-string authToken=" in files["README.md"]
    files = gen.generate(FACTS, BASE)
    assert "--set-string authToken=" not in files["README.md"]


def test_the_token_sample_is_not_marker_shaped():
    """The install-time token sample is lower case, so it does not match the
    marker pattern."""
    readme = gen.generate(
        FACTS, {**BASE, "auth_token": bundle_options.DEFAULT_OPTIONS["auth_token"]})["README.md"]
    line, = [ln for ln in readme.splitlines() if "--set-string authToken=" in ln]
    assert "--set-string authToken=<token>" in line
    assert not markers_mod.MARKER_RE.search(line)


def test_overlay_json_values_are_parseable_where_the_chart_re_encodes_them():
    """nodeSelector values survive a YAML load as plain structures."""
    v, _ = _values(node_selector={"node-role.kubernetes.io/perf": "true"})
    assert json.dumps(v["nodeSelector"])  # no exotic types survived
    assert v["nodeSelector"] == {"node-role.kubernetes.io/perf": "true"}
