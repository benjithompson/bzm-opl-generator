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


def test_overlay_never_pins_the_limitrange_max():
    """Regression, found by installing for real.

    The overlay used to carry the max computed at generate time. The chart
    derives `default` from the engine size at render time, so the moment anyone
    raised the engine -- `helm upgrade --set engine.memoryLimit=6Gi` -- the two
    disagreed and the API server rejected the LimitRange ("default request value
    6Gi is greater than max value 4Gi"), mid-upgrade, with the ConfigMap already
    applied. Leaving it to the chart keeps the object self-consistent under any
    override.
    """
    for over in ({"emit_limitrange": True},
                 {"emit_limitrange": True, "engine_cpu_limit": "500m",
                  "engine_mem_limit": "1Gi"},
                 {"emit_limitrange": True, "engine_cpu_limit": "4",
                  "engine_mem_limit": "16Gi"}):
        v, _ = _values(**over)
        assert v["limitRange"]["enabled"] is True
        assert "maxCpu" not in v["limitRange"], over
        assert "maxMemory" not in v["limitRange"], over


def test_limitrange_derivation_is_still_documented_in_the_overlay():
    """Not pinned, but the reader should still see what it will be -- the
    numbers are why someone opened the file."""
    _, files = _values(engine_cpu_limit="4", engine_mem_limit="16Gi",
                       emit_limitrange=True)
    text = files[gen.HELM_VALUES_FILE]
    assert "max is derived: 4 CPU / 16Gi" in text


def test_auto_update_is_left_to_the_chart():
    """A Helm-managed release usually wants autoUpdate false -- crane otherwise
    takes ownership of its own Deployment and the next upgrade conflicts -- but
    that changes how the customer's agent gets upgraded, so the overlay offers
    the key and does not decide it."""
    v, files = _values()
    assert "autoUpdate" in v
    assert v["autoUpdate"] is None
    assert "helm upgrade" in files[gen.HELM_VALUES_FILE]


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
        gen.generate(FACTS, {**BASE, "engine_cpu_limit": "1",
                             "engine_cpu_request": "2"})
    assert "engine_cpu_request" in str(e.value)


# -- the bundle a chart install actually needs --------------------------------

def test_readme_does_not_promise_force_conflicts_fixes_the_upgrade():
    """It does not, and the earlier draft of this README said it did.

    Forcing hands back only the fields Helm declares; crane's
    `strategy.rollingUpdate` is not one, and it survives beside the forced
    `type: Recreate` for the API server to reject.
    """
    readme = gen.generate(FACTS, BASE)["README.md"]
    assert "autoUpdate: false" in readme
    assert "--force-conflicts` does not rescue" in readme or \
           "--force-conflicts does not rescue" in readme
    # The plain upgrade command must not carry the flag as if it were the fix.
    upgrade = [l for l in readme.splitlines() if l.startswith("helm upgrade")]
    assert upgrade and not any("--force-conflicts" in l for l in upgrade)


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
