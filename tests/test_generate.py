import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import generate as gen  # noqa: E402

FACTS = {
    "harbor_id": "aaa111",
    "harbor_name": "Test Location",
    "func_ids": ["performance"],
    "ships": [{"id": "bbb222", "name": "agent1", "state": "idle",
               "installed_version": "3.7.55", "last_heartbeat": 0}],
    "crane_image": "gcr.io/verdant-bulwark-278/blazemeter/crane:3.7.55",
    "images": [
        {"key": "taurus-cloud:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/v4",
         "tag": "2.4.444-reduced", "performance": True},
        {"key": "apm-image:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/apm",
         "tag": "1.7.112", "performance": True},
        {"key": "blazemeter/service-mock:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/service-mock",
         "tag": "1.0", "performance": False, "excluded_reason": "mock services"},
    ],
    "images_source": "test fixture",
}


def _all_yaml_parse(files):
    for name, content in files.items():
        if name.endswith(".yaml"):
            list(yaml.safe_load_all(content))


def test_default_openshift():
    files = gen.generate(FACTS, {"namespace": "ns1"})
    _all_yaml_parse(files)
    assert "bzm_secret.yaml" in files
    assert "runAsUser:" not in files["bzm_deployment.yaml"]
    assert "secretRef" in files["bzm_deployment.yaml"]
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    assert cm["data"]["HARBOR_ID"] == "aaa111"
    assert cm["data"]["SHIP_ID"] == "bbb222"  # auto from single ship
    assert "AUTH_TOKEN" not in cm["data"]
    assert cm["data"]["INHERIT_RUNNING_USER_AND_GROUP"] == "true"
    assert cm["data"]["AUTO_KUBERNETES_UPDATE"] == "true"
    assert "bzm_clusterrole.yaml" not in files


def test_no_secret_token_in_configmap():
    files = gen.generate(FACTS, {"namespace": "ns1", "use_secret": False, "auth_token": "tok"})
    _all_yaml_parse(files)
    assert "bzm_secret.yaml" not in files
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    assert cm["data"]["AUTH_TOKEN"] == "tok"
    assert "secretRef" not in files["bzm_deployment.yaml"]


def test_private_registry_overrides_from_facts():
    files = gen.generate(FACTS, {"namespace": "ns1", "private_registry": "reg.local/bzm",
                                 "pull_secret": "pullsec"})
    _all_yaml_parse(files)
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    ov = json.loads(cm["data"]["IMAGE_OVERRIDES"])
    assert ov == {"taurus-cloud:latest": "reg.local/bzm/v4:2.4.444-reduced",
                  "apm-image:latest": "reg.local/bzm/apm:1.7.112"}  # mock excluded
    assert cm["data"]["AUTO_KUBERNETES_UPDATE"] == "false"
    d = files["bzm_deployment.yaml"]
    assert "reg.local/bzm/crane:3.7.55" in d
    assert "imagePullSecrets" in d and "pullsec" in d


def test_gui_includes_non_performance():
    files = gen.generate(FACTS, {"namespace": "ns1", "private_registry": "reg.local", "gui": True})
    ov = json.loads(yaml.safe_load(files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert "blazemeter/service-mock:latest" in ov


def test_k8s_platform_sets_runasuser():
    files = gen.generate(FACTS, {"namespace": "ns1", "platform": "k8s"})
    _all_yaml_parse(files)
    assert "runAsUser: 1337" in files["bzm_deployment.yaml"]
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    assert "INHERIT_RUNNING_USER_AND_GROUP" not in cm["data"]


def test_cluster_rbac_optional():
    files = gen.generate(FACTS, {"namespace": "ns1", "cluster_rbac": True})
    _all_yaml_parse(files)
    assert "bzm_clusterrole.yaml" in files
    assert "get, list, watch" in files["bzm_clusterrole.yaml"] or "verbs: [get, list, watch]" in files["bzm_clusterrole.yaml"]


def test_multi_ship_requires_ship_id():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)
    with pytest.raises(ValueError):
        gen.generate(facts, {"namespace": "ns1"})
    files = gen.generate(facts, {"namespace": "ns1", "ship_id": "explicit"})
    assert yaml.safe_load(files["bzm_configmap.yaml"])["data"]["SHIP_ID"] == "explicit"
