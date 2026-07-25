import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import generate as gen  # noqa: E402
from bzm_opl_gen.api import BzmApiError, parse_auth_token  # noqa: E402


def test_parse_auth_token():
    cmd = ("sudo docker run -d --env HARBOR_ID=aaa --env SHIP_ID=bbb "
           "--env AUTH_TOKEN=0k3ycd8fb0a1e2d3 --name=blazemeter-crane blazemeter/crane")
    assert parse_auth_token(cmd) == "0k3ycd8fb0a1e2d3"


def test_parse_auth_token_missing():
    with pytest.raises(BzmApiError):
        parse_auth_token("docker run blazemeter/crane")

FACTS = {
    "harbor_id": "aaa111",
    "harbor_name": "Test Location",
    "func_ids": ["performance"],
    "ships": [{"id": "bbb222", "name": "agent1", "state": "idle",
               "installed_version": "3.7.55", "last_heartbeat": 0}],
    "crane_image": "gcr.io/verdant-bulwark-278/blazemeter/crane:3.7.55",
    "images": [
        {"key": "taurus-cloud:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/v4",
         "tag": "2.4.444-reduced", "category": "performance"},
        {"key": "apm-image:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/apm",
         "tag": "1.7.112", "category": "performance"},
        {"key": "blazemeter/service-mock:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/service-mock",
         "tag": "1.0", "category": "mock"},
        {"key": "blazemeter/doduo:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/doduo",
         "tag": "2.1", "category": "gui"},
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


def test_images_follow_location_funcids():
    gui_facts = dict(FACTS, func_ids=["performance", "functionalGui", "chrome:default"])
    files = gen.generate(gui_facts, {"namespace": "ns1", "private_registry": "reg.local"})
    ov = json.loads(yaml.safe_load(files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert "blazemeter/doduo:latest" in ov          # gui feature -> gui images
    assert "taurus-cloud:latest" in ov              # gui tests still need engines
    assert "blazemeter/service-mock:latest" not in ov  # mocks not enabled

    # A mockServices location will not generate without ingress options -- see
    # test_sv_location_without_ingress_refuses.
    mock_facts = dict(FACTS, func_ids=["mockServices"])
    files = gen.generate(mock_facts, {"namespace": "ns1", "private_registry": "reg.local",
                                      "sv_ingress": "nginx",
                                      "sv_subdomain": "apps.example.com",
                                      "sv_tls_secret": "wildcard-tls"})
    ov = json.loads(yaml.safe_load(files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert set(ov) == {"blazemeter/service-mock:latest"}


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


def test_tolerations_node_selector_in_pod_and_configmap():
    tol = [{"key": "lifecycle", "operator": "Equal", "value": "spot", "effect": "NoSchedule"}]
    files = gen.generate(FACTS, {"namespace": "ns1", "tolerations": tol,
                                 "node_selector": {"pool": "loadtest"}})
    _all_yaml_parse(files)
    d = yaml.safe_load(files["bzm_deployment.yaml"])
    spec = d["spec"]["template"]["spec"]
    assert spec["tolerations"] == tol
    assert spec["nodeSelector"] == {"pool": "loadtest"}
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert json.loads(cm["KUBERNETES_TOLERATIONS_JSON"]) == tol
    assert json.loads(cm["KUBERNETES_NODE_SELECTOR_JSON"]) == {"pool": "loadtest"}


def test_ca_bundle_configmap_mount_and_envs():
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    files = gen.generate(FACTS, {"namespace": "ns1", "ca_bundle": pem})
    _all_yaml_parse(files)
    assert "bzm_cacerts.yaml" in files
    cacm = yaml.safe_load(files["bzm_cacerts.yaml"])
    assert cacm["data"]["ca-bundle.crt"].strip() == pem
    d = yaml.safe_load(files["bzm_deployment.yaml"])
    spec = d["spec"]["template"]["spec"]
    assert spec["volumes"][0]["configMap"]["name"] == "blazemeter-cacerts"
    assert spec["containers"][0]["volumeMounts"][0]["mountPath"] == "/var/cm"
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["REQUESTS_CA_BUNDLE"] == "/var/cm/ca-bundle.crt"
    assert cm["KUBERNETES_CA_BUNDLE_MOUNT"] == (
        "REQUESTS_CA_BUNDLE=blazemeter-cacerts=ca-bundle.crt:"
        "AWS_CA_BUNDLE=blazemeter-cacerts=ca-bundle.crt")


def test_ca_existing_configmap_referenced_not_created():
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "ca_existing_configmap": "corp-trust",
                                 "ca_configmap_key": "trust.pem"})
    _all_yaml_parse(files)
    assert "bzm_cacerts.yaml" not in files  # platform team owns the ConfigMap
    d = yaml.safe_load(files["bzm_deployment.yaml"])
    spec = d["spec"]["template"]["spec"]
    assert spec["volumes"][0]["configMap"]["name"] == "corp-trust"
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["REQUESTS_CA_BUNDLE"] == "/var/cm/trust.pem"
    assert cm["KUBERNETES_CA_BUNDLE_MOUNT"] == (
        "REQUESTS_CA_BUNDLE=corp-trust=trust.pem:AWS_CA_BUNDLE=corp-trust=trust.pem")


def test_ca_openshift_inject():
    files = gen.generate(FACTS, {"namespace": "ns1", "ca_openshift_inject": True})
    _all_yaml_parse(files)
    cacm = yaml.safe_load(files["bzm_cacerts.yaml"])
    assert cacm["metadata"]["labels"]["config.openshift.io/inject-trusted-cabundle"] == "true"
    assert "data" not in cacm  # the cluster operator fills in ca-bundle.crt
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["REQUESTS_CA_BUNDLE"] == "/var/cm/ca-bundle.crt"


def test_ca_modes_mutually_exclusive():
    with pytest.raises(ValueError):
        gen.generate(FACTS, {"namespace": "ns1", "ca_bundle": "PEM",
                             "ca_existing_configmap": "corp-trust"})


def test_proxy_plain_in_configmap():
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "proxy": {"http": "http://proxy:3128",
                                           "https": "http://proxy:3128"}})
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["HTTP_PROXY"] == "http://proxy:3128"
    assert cm["NO_PROXY"] == "kubernetes.default,127.0.0.1,localhost"
    assert "HTTP_PROXY" not in files["bzm_secret.yaml"]


def test_proxy_credentials_move_to_secret():
    files = gen.generate(FACTS, {"namespace": "ns1", "auth_token": "tok",
                                 "proxy": {"http": "http://proxy:3128",
                                           "https": "http://proxy:3128",
                                           "username": "user@corp",
                                           "password": "p:ss@w"}})
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert "HTTP_PROXY" not in cm          # creds never land in the ConfigMap
    assert cm["NO_PROXY"] == "kubernetes.default,127.0.0.1,localhost"
    sec = yaml.safe_load(files["bzm_secret.yaml"])["stringData"]
    assert sec["HTTP_PROXY"] == "http://user%40corp:p%3Ass%40w@proxy:3128"
    assert sec["HTTPS_PROXY"] == "http://user%40corp:p%3Ass%40w@proxy:3128"


def test_proxy_credentials_no_secret_warns_in_configmap():
    files = gen.generate(FACTS, {"namespace": "ns1", "use_secret": False,
                                 "auth_token": "tok",
                                 "proxy": {"http": "http://proxy:3128",
                                           "username": "u", "password": "p"}})
    cm_text = files["bzm_configmap.yaml"]
    assert "WARNING: proxy credentials" in cm_text
    cm = yaml.safe_load(cm_text)["data"]
    assert cm["HTTP_PROXY"] == "http://u:p@proxy:3128"


def test_engine_resource_limits():
    files = gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "2",
                                 "engine_mem_limit": "8Gi",
                                 "engine_ephemeral_limit_mb": 40960})
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == "2"
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "8Gi"
    assert cm["KUBERNETES_LIMITS_EPHEMERAL_STORAGE"] == "40960"


def _limitrange(files):
    return yaml.safe_load(files["bzm_limitrange.yaml"])["spec"]["limits"][0]


def test_limitrange_absent_by_default():
    files = gen.generate(FACTS, {"namespace": "ns1"})
    assert "bzm_limitrange.yaml" not in files


def test_limitrange_default_request_matches_engine_limits():
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True,
                                 "engine_cpu_limit": "4", "engine_mem_limit": "16Gi"})
    _all_yaml_parse(files)
    lr = yaml.safe_load(files["bzm_limitrange.yaml"])
    assert lr["kind"] == "LimitRange"
    assert lr["metadata"]["namespace"] == "ns1"
    item = lr["spec"]["limits"][0]
    assert item["type"] == "Container"
    # The headline behaviour: engines request what they are limited to, instead
    # of crane's 250m/256Mi defaults.
    assert item["defaultRequest"] == {"cpu": "4", "memory": "16Gi"}
    assert item["default"] == {"cpu": "4", "memory": "16Gi"}
    # ephemeral-storage has its own agent envs -- deliberately not covered here
    assert "ephemeral-storage" not in item["defaultRequest"]


def test_limitrange_explicit_requests_honoured():
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True,
                                 "engine_cpu_limit": "4", "engine_mem_limit": "16Gi",
                                 "engine_cpu_request": "2", "engine_mem_request": "8Gi"})
    _all_yaml_parse(files)
    item = _limitrange(files)
    assert item["defaultRequest"] == {"cpu": "2", "memory": "8Gi"}
    assert item["default"] == {"cpu": "4", "memory": "16Gi"}


def test_limitrange_uses_documented_engine_size_when_unset():
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True})
    _all_yaml_parse(files)
    item = _limitrange(files)
    assert item["defaultRequest"] == {"cpu": "2", "memory": "8Gi"}
    assert item["default"] == {"cpu": "2", "memory": "8Gi"}
    assert item["max"] == {"cpu": "2", "memory": "8Gi"}


def test_limitrange_max_not_below_crane_own_limits():
    # Crane runs in the same namespace; a max under its own limits would get the
    # crane pod rejected by LimitRanger.
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True,
                                 "engine_cpu_limit": "500m", "engine_mem_limit": "1Gi"})
    _all_yaml_parse(files)
    item = _limitrange(files)
    assert item["max"] == {"cpu": "1", "memory": "2Gi"}
    assert item["default"] == {"cpu": "500m", "memory": "1Gi"}


def test_limitrange_applied_before_deployment():
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True})
    order = [f for f in gen.APPLY_ORDER if f in files]
    assert order.index("bzm_limitrange.yaml") < order.index("bzm_deployment.yaml")


def test_engine_request_above_limit_rejected():
    with pytest.raises(ValueError, match="engine_cpu_request"):
        gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "1",
                             "engine_cpu_request": "2"})
    with pytest.raises(ValueError, match="engine_mem_request"):
        gen.generate(FACTS, {"namespace": "ns1", "engine_mem_limit": "1Gi",
                             "engine_mem_request": "2Gi"})


def test_unparseable_engine_quantity_rejected():
    with pytest.raises(ValueError, match="engine_mem_limit"):
        gen.generate(FACTS, {"namespace": "ns1", "engine_mem_limit": "8 gigs"})
    with pytest.raises(ValueError, match="engine_cpu_limit"):
        gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "two"})


def test_engine_size_helper():
    o = {**gen.DEFAULT_OPTIONS, "engine_cpu_limit": "500m", "engine_mem_limit": "1Gi"}
    assert gen.engine_size(o) == (500, 1024 ** 3)
    assert gen.engine_size(dict(gen.DEFAULT_OPTIONS)) == (2000, 8 * 1024 ** 3)


def test_crane_resources_come_from_the_constants():
    """The LimitRange max and doctor's capacity maths are both computed from
    CRANE_*_LIMIT; the deployment must be the same numbers, not a second copy."""
    d = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1"})["bzm_deployment.yaml"])
    res = d["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert res["limits"]["cpu"] == gen.CRANE_CPU_LIMIT
    assert res["limits"]["memory"] == gen.CRANE_MEM_LIMIT
    assert res["requests"]["cpu"] == gen.CRANE_CPU_REQUEST
    assert res["requests"]["memory"] == gen.CRANE_MEM_REQUEST


def test_readme_documents_limitrange_and_applies_it():
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True})
    readme = files["README.md"]
    assert "apply -f bzm_limitrange.yaml" in readme
    assert "KUBERNETES_RESOURCES_LIMITS_CPU" in readme
    assert "250m" in readme                 # the requests crane stamps on engines
    assert "namespace" in readme
    # The README must not promise the one thing this object cannot do: crane
    # sets the engine's requests explicitly, so defaultRequest never reaches it.
    assert "does not fix that" in readme
    assert "only fills in fields a pod leaves unset" in readme
    plain = gen.generate(FACTS, {"namespace": "ns1"})["README.md"]
    assert "bzm_limitrange.yaml" not in plain


def test_profile_json_round_trips_new_options():
    files = gen.generate(FACTS, {"namespace": "ns1", "emit_limitrange": True,
                                 "engine_cpu_request": "1"})
    prof = json.loads(files[gen.PROFILE_FILE])
    assert prof["emit_limitrange"] is True
    assert prof["engine_cpu_request"] == "1"
    assert prof["engine_mem_request"] is None
    assert "auth_token" not in prof


def test_mirror_script_with_private_registry():
    files = gen.generate(FACTS, {"namespace": "ns1", "private_registry": "reg.local/bzm"})
    sh = files["bzm-opl-image-mirror.sh"]
    assert sh.startswith("#!/usr/bin/env bash")
    assert "docker pull --platform linux/amd64 gcr.io/verdant-bulwark-278/blazemeter/v4:2.4.444-reduced" in sh
    assert "docker push reg.local/bzm/crane:3.7.55" in sh
    assert "service-mock" not in sh  # performance-only by default
    files2 = gen.generate(FACTS, {"namespace": "ns1"})
    assert "bzm-opl-image-mirror.sh" not in files2


def test_multi_ship_requires_ship_id():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)
    with pytest.raises(ValueError):
        gen.generate(facts, {"namespace": "ns1"})
    files = gen.generate(facts, {"namespace": "ns1", "ship_id": "explicit"})
    assert yaml.safe_load(files["bzm_configmap.yaml"])["data"]["SHIP_ID"] == "explicit"


# -- service virtualization ------------------------------------------------
# Each of these mirrors a failure seen on a real cluster: a mockServices
# location generated without ingress options deploys and then hangs at
# WAITING_FOR_DOMAIN, so the generator refuses instead.

SV_FACTS = dict(FACTS, func_ids=["mockServices"])
SV_OPTS = {"namespace": "ns1", "sv_ingress": "nginx",
           "sv_subdomain": "apps.example.com", "sv_tls_secret": "wildcard-tls"}


def test_sv_location_without_ingress_refuses():
    with pytest.raises(ValueError, match="WAITING_FOR_DOMAIN"):
        gen.generate(SV_FACTS, {"namespace": "ns1"})


def test_sv_bridge_funcid_also_requires_ingress():
    """sv-bridge fronts the mocks, so it needs the same wiring as mockServices."""
    bridge = dict(FACTS, func_ids=["performance", "sv-bridge"])
    with pytest.raises(ValueError, match="sv-bridge"):
        gen.generate(bridge, {"namespace": "ns1"})
    data = yaml.safe_load(
        gen.generate(bridge, SV_OPTS)["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "NGINX"


def test_sv_ingress_requires_subdomain_and_tls_secret():
    with pytest.raises(ValueError, match="sv_subdomain and sv_tls_secret"):
        gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx"})
    # The TLS secret is mandatory even though the virtual service is HTTP.
    with pytest.raises(ValueError, match="sv_tls_secret"):
        gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx",
                                "sv_subdomain": "apps.example.com"})


def test_sv_ingress_rejects_nodeport():
    with pytest.raises(ValueError, match="cluster-scoped"):
        gen.generate(SV_FACTS, dict(SV_OPTS, service_type="NODEPORT"))


def test_sv_nginx_configmap_envs():
    data = yaml.safe_load(gen.generate(SV_FACTS, SV_OPTS)["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "NGINX"
    assert data["KUBERNETES_WEB_EXPOSE_SUB_DOMAIN"] == "apps.example.com"
    assert data["KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME"] == "wildcard-tls"
    assert data["KUBERNETES_SERVICE_USE_TYPE"] == "CLUSTERIP"
    assert "KUBERNETES_ISTIO_GATEWAY_NAME" not in data


def test_sv_nginx_role_grants_modern_ingress_group():
    files = gen.generate(SV_FACTS, SV_OPTS)
    role = yaml.safe_load(files["bzm_role.yaml"])
    groups = {g: r["resources"] for r in role["rules"] for g in r["apiGroups"]}
    assert "ingresses" in groups["networking.k8s.io"]
    assert "networking.istio.io" not in groups
    # No ClusterRole needed for the ingress path -- that is its whole point.
    assert "bzm_clusterrole.yaml" not in files


def test_sv_istio_adds_gateway_rbac_and_optional_gateway_name():
    files = gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="istio"))
    role = yaml.safe_load(files["bzm_role.yaml"])
    groups = {g: r["resources"] for r in role["rules"] for g in r["apiGroups"]}
    assert set(groups["networking.istio.io"]) == {"gateways", "virtualservices"}
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "ISTIO"
    assert "KUBERNETES_ISTIO_GATEWAY_NAME" not in data  # unset -> gateway per service
    named = gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="istio",
                                        sv_istio_gateway="bzm-gateway"))
    assert yaml.safe_load(named["bzm_configmap.yaml"])["data"][
        "KUBERNETES_ISTIO_GATEWAY_NAME"] == "bzm-gateway"


def test_legacy_extensions_ingress_grant_removed():
    """Ingress left extensions/v1beta1 in k8s 1.22; the old grant was inert."""
    role = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1"})["bzm_role.yaml"])
    for rule in role["rules"]:
        if "extensions" in rule["apiGroups"]:
            assert "ingresses" not in rule["resources"]


def test_performance_location_emits_no_sv_config():
    files = gen.generate(FACTS, {"namespace": "ns1"})
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert not [k for k in data if k.startswith("KUBERNETES_WEB_EXPOSE")]
    role = yaml.safe_load(files["bzm_role.yaml"])
    assert "networking.k8s.io" not in {g for r in role["rules"] for g in r["apiGroups"]}
