"""Facts built from the three values BlazeMeter shows, with no account access.

The case: producing manifests for a customer whose BlazeMeter account and
cluster you cannot reach. What matters is that the resulting facts are the same
*shape* gather() returns, so nothing downstream learns which way they arrived --
and that the image catalogue actually covers the features a location can be
told it has, because a missing key is the silent failure (crane resolves it
against the public registry).
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import facts as facts_mod  # noqa: E402
from bzm_opl_gen import generate as gen  # noqa: E402

from test_generate import FACTS  # noqa: E402

H, S = "6a63a79dcc45dccca90bf440", "6a679d3445115b6651011715"


def _cm(files):
    return yaml.safe_load(files["bzm_configmap.yaml"])["data"]


def _gen(f, **opts):
    return gen.generate(f, {"platform": "k8s", "namespace": "cust",
                            "auth_token": "TOK", "ship_id": f["ships"][0]["id"],
                            **opts})


# -- shape --------------------------------------------------------------------

def test_manual_facts_match_the_shape_gather_returns():
    """Same keys, so every consumer downstream is indifferent to the source."""
    m = facts_mod.manual(H, S)
    assert set(m) == set(FACTS) | {"slots", "threads_per_engine"} - set()
    for key in ("harbor_id", "func_ids", "ships", "images", "images_source",
                "crane_image"):
        assert key in m, key


def test_the_three_values_reach_the_manifests():
    files = _gen(facts_mod.manual(H, S))
    cm = _cm(files)
    assert cm["HARBOR_ID"] == H
    assert cm["SHIP_ID"] == S
    assert yaml.safe_load(files["bzm_secret.yaml"])["stringData"]["AUTH_TOKEN"] == "TOK"


def test_a_full_bundle_generates_from_nothing_else():
    """The point of the feature: three values in, deployable manifests out."""
    files = _gen(facts_mod.manual(H, S))
    for name in ("bzm_serviceaccount.yaml", "bzm_configmap.yaml", "bzm_secret.yaml",
                 "bzm_role.yaml", "bzm_rolebinding.yaml", "bzm_deployment.yaml"):
        assert name in files, name
    for name, content in files.items():
        if name.endswith(".yaml"):
            assert yaml.safe_load(content)["kind"]


def test_it_also_generates_a_chart():
    files = _gen(facts_mod.manual(H, S), output_format="helm")
    assert gen.HELM_VALUES_FILE in files
    v = yaml.safe_load(files[gen.HELM_VALUES_FILE])
    assert v["harborId"] == H and v["shipId"] == S and v["authToken"] == "TOK"


def test_crane_image_floats_because_nothing_can_pin_it():
    """gather() pins the tag the account advertises. Manually there is no
    account, so it is `latest` -- which is also what a location with no live
    agent reports."""
    assert facts_mod.manual(H, S)["crane_image"].endswith(":latest")


def test_no_validation_of_the_ids():
    """Deliberate: there is nothing here to check them against, and a format
    guess would reject input that is correct."""
    m = facts_mod.manual("  not-an-id  ", "also/not/one")
    assert m["harbor_id"] == "  not-an-id  "
    assert m["ships"][0]["id"] == "also/not/one"


# -- the image catalogue ------------------------------------------------------

@pytest.mark.parametrize("func_ids,expect_keys", [
    (["performance"], {"taurus-cloud:latest", "apm-image:latest"}),
    (["functionalApi"], {"taurus-cloud:latest", "apm-image:latest"}),
    (["mockServices"], {"blazemeter/service-mock:latest",
                        "blazemeter/group-gateway:latest",
                        "blazemeter/mock-pc-service:latest"}),
    (["proxyRecorder"], {"blazemeter/proxy-recorder:latest"}),
    (["functionalGui"], {"taurus-cloud:latest", "apm-image:latest",
                         "blazemeter/doduo:latest"}),
])
def test_every_selectable_feature_names_its_images(func_ids, expect_keys):
    """A feature whose category the catalogue does not cover produces an empty
    or partial IMAGE_OVERRIDES, and crane then resolves the missing keys against
    the public registry without logging anything -- which looks fine until the
    cluster is actually sealed."""
    sv = ({"sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
           "sv_tls_secret": "wc"} if "mockServices" in func_ids else {})
    f = facts_mod.manual(H, S, func_ids=func_ids)
    cm = _cm(_gen(f, private_registry="reg.io/bzm", **sv))
    assert set(yaml.safe_load(cm["IMAGE_OVERRIDES"])) == expect_keys


def test_catalogue_covers_every_category_the_funcid_vocabulary_can_ask_for():
    """Guards the pairing directly: adding a funcId that needs a new category
    without adding images for it should fail here, not on a sealed cluster."""
    have = {i["category"] for i in facts_mod.FALLBACK_IMAGES}
    want = set()
    for cats in facts_mod.CATEGORY_BY_FUNC.values():
        want |= cats
    assert want <= have, f"no fallback images for {want - have}"


def test_gui_browser_images_are_the_known_gap():
    """Not an oversight: the account carries a version-pinned repo per browser
    build and only a live inventory names the one a location uses."""
    assert facts_mod.gui_images_incomplete(
        facts_mod.manual(H, S, func_ids=["functionalGui"]))
    assert not facts_mod.gui_images_incomplete(
        facts_mod.manual(H, S, func_ids=["performance"]))
    # Never claimed of facts that came from a real inventory.
    assert not facts_mod.gui_images_incomplete(
        dict(FACTS, func_ids=["functionalGui"], images_source="live agent inventory"))


def test_fallback_catalogue_repos_are_all_under_the_blazemeter_project():
    """These were read off live inventories rather than derived from the keys --
    four of them (v4, v3, apm, secrets) do not match their key name, so a typo
    here is a repo that does not exist."""
    for i in facts_mod.FALLBACK_IMAGES:
        assert i["repo"].startswith("gcr.io/verdant-bulwark-278/blazemeter/"), i
        assert i["key"] and i["tag"] and i["category"]


# -- interaction with the rest of the generator -------------------------------

def test_service_virtualization_still_refuses_without_an_ingress():
    """Manual entry does not become a way around the validations -- the failure
    it prevents is on the cluster, not in the account."""
    f = facts_mod.manual(H, S, func_ids=["mockServices"])
    with pytest.raises(ValueError, match="WAITING_FOR_DOMAIN"):
        _gen(f)


def test_helm_format_still_refuses_a_mock_location():
    f = facts_mod.manual(H, S, func_ids=["mockServices"])
    with pytest.raises(ValueError, match="performance testing only"):
        _gen(f, output_format="helm", sv_ingress="nginx",
             sv_subdomain="apps.example.com", sv_tls_secret="wc")
