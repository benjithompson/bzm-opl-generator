import json
import os
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import facts as facts_mod  # noqa: E402
from bzm_opl_gen import generate as gen  # noqa: E402
from bzm_opl_gen.api import BzmApiError, parse_auth_token  # noqa: E402
from bzm_opl_gen.quantity import parse_memory  # noqa: E402


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


def test_engines_drop_privileges_on_every_platform():
    """This used to assert the opposite for k8s, and the asymmetry was an
    accident of where a rejection was first noticed rather than a decision:
    `platform` defaults to openshift, so the restricted engine was already what
    the tool shipped, and naming k8s quietly opted out of it. Crane's own
    default engine pod is privileged, and restricted PodSecurity, OpenShift SCC
    and GKE Autopilot's Warden all refuse it -- late, after the agent is online,
    so the run hangs at BOOT_STARTING instead of failing usefully."""
    for platform in ("k8s", "openshift"):
        cm = yaml.safe_load(gen.generate(
            FACTS, {"namespace": "ns1", "platform": platform})["bzm_configmap.yaml"])
        assert cm["data"]["INHERIT_RUNNING_USER_AND_GROUP"] == "true", platform
        assert json.loads(cm["data"]["KUBERNETES_SECURITY_CONTEXT_CAP_JSON"]) == {
            "drop": ["ALL"]}, platform


def test_restrict_engines_can_be_turned_off_for_an_image_that_needs_a_capability():
    cm = yaml.safe_load(gen.generate(
        FACTS, {"namespace": "ns1", "platform": "k8s",
                "restrict_engines": False})["bzm_configmap.yaml"])
    assert "INHERIT_RUNNING_USER_AND_GROUP" not in cm["data"]
    assert "KUBERNETES_SECURITY_CONTEXT_CAP_JSON" not in cm["data"]


def test_cluster_rbac_optional():
    files = gen.generate(FACTS, {"namespace": "ns1", "cluster_rbac": True})
    _all_yaml_parse(files)
    assert "bzm_clusterrole.yaml" in files
    assert "get, list, watch" in files["bzm_clusterrole.yaml"] or "verbs: [get, list, watch]" in files["bzm_clusterrole.yaml"]


def test_nodeport_needs_no_cluster_rbac():
    """Rendered without the ClusterRole, and the file's own header does not say
    otherwise. A live performance location ran NODEPORT with namespaced RBAC
    only -- crane took its advertised address from its own interfaces, created
    the NodePort Service through the namespaced Role, and ran an engine to
    ENDED. The node reads stay optional for capacity awareness; what was wrong
    was tying them to service_type."""
    files = gen.generate(FACTS, {"namespace": "ns1", "service_type": "NODEPORT"})
    _all_yaml_parse(files)
    assert "bzm_clusterrole.yaml" not in files
    header = gen.generate(FACTS, {"namespace": "ns1", "service_type": "NODEPORT",
                                  "cluster_rbac": True})["bzm_clusterrole.yaml"]
    assert "127.0.0.1" not in header


# -- service account ---------------------------------------------------------
# The name reaches four places and `create` gates one file. What makes this
# worth its own block is that getting any single reference wrong is silent:
# a Deployment naming an account no binding grants to comes online and then
# 403s on the API it needs, and a binding subject naming one nothing runs as
# grants permissions to nobody.

def _sa_refs(files):
    """Every place the manifests name a ServiceAccount, by file."""
    dep = yaml.safe_load(files["bzm_deployment.yaml"])
    rb = yaml.safe_load(files["bzm_rolebinding.yaml"])
    refs = {
        "deployment": dep["spec"]["template"]["spec"]["serviceAccountName"],
        "rolebinding": rb["subjects"][0]["name"],
    }
    if "bzm_serviceaccount.yaml" in files:
        refs["serviceaccount"] = yaml.safe_load(
            files["bzm_serviceaccount.yaml"])["metadata"]["name"]
    if "bzm_clusterrolebinding.yaml" in files:
        refs["clusterrolebinding"] = yaml.safe_load(
            files["bzm_clusterrolebinding.yaml"])["subjects"][0]["name"]
    return refs


def test_service_account_defaults_to_crane_everywhere():
    files = gen.generate(FACTS, {"namespace": "ns1", "cluster_rbac": True})
    _all_yaml_parse(files)
    assert set(_sa_refs(files).values()) == {"crane"}


def test_named_service_account_is_used_by_every_reference():
    files = gen.generate(FACTS, {"namespace": "ns1", "cluster_rbac": True,
                                 "service_account_name": "bzm-agent"})
    _all_yaml_parse(files)
    assert set(_sa_refs(files).values()) == {"bzm-agent"}
    # The Role, RoleBinding and Deployment objects keep their own names: they
    # are ours, and the `-l role=role-crane` selectors in BlazeMeter's docs are
    # written against them.
    assert yaml.safe_load(files["bzm_rolebinding.yaml"])["metadata"]["name"] \
        == "role-binding-crane"


def test_existing_service_account_is_referenced_not_created():
    """create off = somebody else owns the object. Applying our own copy would
    take ownership of an account the platform team maintains."""
    files = gen.generate(FACTS, {"namespace": "ns1", "cluster_rbac": True,
                                 "service_account_name": "platform-sa",
                                 "service_account_create": False})
    _all_yaml_parse(files)
    assert "bzm_serviceaccount.yaml" not in files
    assert set(_sa_refs(files).values()) == {"platform-sa"}
    # ...and the README neither tells you to apply a file that is not there nor
    # leaves the prerequisite unsaid.
    assert "bzm_serviceaccount.yaml" not in files["README.md"]
    assert "platform-sa" in files["README.md"]


def test_created_service_account_is_not_advertised_as_a_prerequisite():
    files = gen.generate(FACTS, {"namespace": "ns1"})
    assert "bzm_serviceaccount.yaml" in files["README.md"]
    assert "must already exist" not in files["README.md"]


@pytest.mark.parametrize("name", ["", "   ", None])
def test_unnamed_service_account_is_refused(name):
    """Not defaulted at render time. The tempting fallback -- the namespace's
    `default` account -- deploys, works, and hands crane's Role to every other
    pod in the namespace."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {"namespace": "ns1", "service_account_name": name,
                             "service_account_create": False})
    assert "service_account_name" in str(e.value)


def test_service_account_round_trips_through_the_profile():
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "service_account_name": "platform-sa",
                                 "service_account_create": False})
    prof = json.loads(files[gen.PROFILE_FILE])
    assert prof["service_account_name"] == "platform-sa"
    assert prof["service_account_create"] is False
    replayed = gen.generate(FACTS, prof)
    assert "bzm_serviceaccount.yaml" not in replayed
    assert set(_sa_refs(replayed).values()) == {"platform-sa"}


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


def _crane_ephemeral(files):
    c = yaml.safe_load(files["bzm_deployment.yaml"])["spec"]["template"]["spec"]["containers"][0]
    return (c["resources"]["requests"]["ephemeral-storage"],
            c["resources"]["limits"]["ephemeral-storage"])


def test_crane_ephemeral_storage_request_equals_limit_by_default():
    # The pair being equal is the property, not the number. GKE Autopilot
    # rewrites the limit down to the request, so a bundle whose request is the
    # smaller of the two ships a ceiling the customer never chose -- at 100Mi
    # that evicted crane in ~12s, forever, because each replacement repeated it.
    req, lim = _crane_ephemeral(gen.generate(FACTS, {"namespace": "ns1"}))
    assert req == lim == gen.CRANE_EPHEMERAL_STORAGE


def test_crane_ephemeral_storage_clears_measured_usage():
    # Crane sits at ~161MiB, 107MiB of it /tmp, within seconds of starting.
    # A default below that is the bug this constant exists to prevent, so pin
    # the floor rather than the exact value -- raising it stays fine.
    assert parse_memory(gen.CRANE_EPHEMERAL_STORAGE) >= 512 * 1024 * 1024


def test_crane_ephemeral_storage_override_moves_both_fields():
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "crane_ephemeral_storage": "4Gi"})
    _all_yaml_parse(files)
    assert _crane_ephemeral(files) == ("4Gi", "4Gi")


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



def test_mirror_script_is_self_contained():
    """It is handed to someone who has neither this tool nor a BlazeMeter
    account, so it has to stand alone -- and it has to fail before transferring
    several GB, not after."""
    sh = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222",
                              "private_registry": "reg.corp.com/bzm"}
                      )["bzm-opl-image-mirror.sh"]
    # Nothing about how the bundle was generated leaks in: no API key, no token.
    assert "api-key" not in sh and "auth" not in sh.lower().replace("authenticat", "")
    # Says where credentials are and are not needed, and names the login.
    assert "Pulling needs no credentials" in sh
    assert "docker login reg.corp.com" in sh
    # crane is mirrored first: it is ~86MB against the engine's ~3.5GB, so a
    # registry that refuses the push costs one small image rather than the lot.
    assert sh.index("crane:") < sh.index("v4:")
    # A refused push says what to do about it, and stops rather than continuing.
    assert "docker login reg.corp.com" in sh
    assert "exit 1" in sh
    # No synthetic probe image: `docker rmi` is local-only, so anything pushed
    # to check access would stay in the customer's registry for good.
    assert "probe" not in sh.lower()
    # bash strict mode, so a mid-way failure stops rather than pushing garbage.
    assert "set -euo pipefail" in sh


def test_mirror_script_absent_without_a_private_registry():
    files = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222"})
    assert "bzm-opl-image-mirror.sh" not in files


def test_readme_is_short_and_actionable():
    """It is handed to a customer, so it is instructions -- the reasoning lives
    in the project README. It used to run to 52 lines of rationale."""
    readme = gen.generate(FACTS, {"namespace": "ns1"})["README.md"]
    assert len(readme.splitlines()) < 45, "README is creeping back towards an essay"
    # The four things someone needs: what this is, how to deploy, how to check,
    # and what it costs to run.
    assert "apply -f bzm_deployment.yaml" in readme
    assert "rollout status deploy/crane" in readme
    assert "online" in readme
    assert gen.ENGINE_STAMPED_REQUEST_CPU in readme     # the engine request gap
    assert "bzm_limitrange.yaml" not in readme


def test_no_limitrange_is_emitted():
    """Removed outright: it could not change the engine's requests, and the
    defaults it did apply reached crane's own helper pods, handing an engine's
    worth of CPU and memory to jobs that need neither."""
    files = gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "4",
                                 "engine_mem_limit": "16Gi"})
    assert not any("limitrange" in n.lower() for n in files)
    assert not any(yaml.safe_load(c).get("kind") == "LimitRange"
                   for n, c in files.items() if n.endswith(".yaml"))


def test_profile_json_round_trips_new_options():
    files = gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "1"})
    prof = json.loads(files[gen.PROFILE_FILE])
    assert prof["engine_cpu_limit"] == "1"
    assert prof["engine_mem_limit"] is None
    assert "emit_limitrange" not in prof
    assert "auth_token" not in prof


def test_mirror_script_with_private_registry():
    files = gen.generate(FACTS, {"namespace": "ns1", "private_registry": "reg.local/bzm"})
    sh = files["bzm-opl-image-mirror.sh"]
    assert sh.startswith("#!/usr/bin/env bash")
    # Every image is pulled amd64 (engines are amd64-only) and retagged into the
    # destination. The commands live in a shell function now, so this asserts the
    # refs reach the script rather than the exact line shape.
    assert "--platform linux/amd64" in sh
    assert "gcr.io/verdant-bulwark-278/blazemeter/v4:2.4.444-reduced" in sh
    assert "reg.local/bzm/crane:3.7.55" in sh
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


def test_retired_sv_bridge_funcid_demands_nothing():
    """sv-bridge is retired. Locations in real accounts still carry it, and they
    must generate like any other performance location -- not stall on ingress
    options for a feature that no longer exists, and not pull an image for it."""
    retired = dict(FACTS, func_ids=["performance", "sv-bridge"])
    files = gen.generate(retired, {"namespace": "ns1"})          # no ingress needed
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]
    assert not [i for i in gen.select_images(retired)
                if "sv-bridge" in i["repo"]]


def test_sv_ingress_requires_subdomain_and_tls_secret():
    with pytest.raises(ValueError, match="sv_subdomain and sv_tls_secret"):
        gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx"})
    # The TLS secret is mandatory even though the virtual service is HTTP.
    with pytest.raises(ValueError, match="sv_tls_secret"):
        gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx",
                                "sv_subdomain": "apps.example.com"})


def test_sv_ingress_allows_nodeport_where_it_was_measured_working():
    """Both settings reach the ConfigMap; neither is quietly rewritten.

    A rewrite would be worse than the refusal it replaced: the customer asked
    for NODEPORT, the manifests would say CLUSTERIP, and nothing would say why.
    """
    for ingress in [i for i, b in gen.SV_INGRESS_BACKENDS.items() if b.nodeport_ok]:
        opts = dict(SV_OPTS, service_type="NODEPORT", sv_ingress=ingress)
        if ingress == "openshift":
            opts["platform"] = "openshift"
        data = yaml.safe_load(
            gen.generate(SV_FACTS, opts)["bzm_configmap.yaml"])["data"]
        assert data["KUBERNETES_SERVICE_USE_TYPE"] == "NODEPORT"
        assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == ingress.upper()


def test_sv_ingress_refuses_nodeport_where_it_was_measured_broken():
    """contour and istio take the port from the Service's nodePort, so the
    object is written, the mock runs, BlazeMeter advertises an endpoint, and it
    does not serve -- the silent failure every other guard in _sv_cfg exists to
    stop. Asserted per backend off the table, so a fifth backend added without a
    measured `nodeport_ok` shows up here rather than on someone's cluster."""
    for ingress in [i for i, b in gen.SV_INGRESS_BACKENDS.items()
                    if not b.nodeport_ok]:
        with pytest.raises(ValueError, match="requires service_type=CLUSTERIP"):
            gen.generate(SV_FACTS, dict(SV_OPTS, service_type="NODEPORT",
                                        sv_ingress=ingress))


def test_the_nodeport_refusal_gives_the_measured_reason_not_the_disproved_one():
    """#49 and #60 each corrected a rationale that named a mechanism nobody had
    measured. This refusal is real, so it has to say the real thing: the port
    crane writes -- never the cluster-scoped Node read, which is denied on every
    backend including the two that work."""
    with pytest.raises(ValueError) as e:
        gen.generate(SV_FACTS, dict(SV_OPTS, service_type="NODEPORT",
                                    sv_ingress="contour"))
    msg = str(e.value).lower()
    assert "nodeport" in msg, "has to name the port it writes"
    for claim in ("cluster-scoped", "node object", "namespaced role cannot"):
        assert claim not in msg, f"refusal revives the disproved reason: {claim!r}"


def test_sv_nginx_configmap_envs():
    data = yaml.safe_load(gen.generate(SV_FACTS, SV_OPTS)["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "NGINX"
    assert data["KUBERNETES_WEB_EXPOSE_SUB_DOMAIN"] == "apps.example.com"
    assert data["KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME"] == "wildcard-tls"
    assert data["KUBERNETES_SERVICE_USE_TYPE"] == "CLUSTERIP"
    assert "KUBERNETES_ISTIO_GATEWAY_NAME" not in data


def _role_groups(files):
    role = yaml.safe_load(files["bzm_role.yaml"])
    return {g: r["resources"] for r in role["rules"] for g in r["apiGroups"]}


def test_sv_nginx_role_grants_modern_ingress_group():
    files = gen.generate(SV_FACTS, SV_OPTS)
    groups = _role_groups(files)
    assert "ingresses" in groups["networking.k8s.io"]
    assert "networking.istio.io" not in groups
    assert "projectcontour.io" not in groups
    # No ClusterRole needed for the ingress path -- that is its whole point.
    assert "bzm_clusterrole.yaml" not in files


def test_sv_istio_adds_gateway_rbac_and_optional_gateway_name():
    files = gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="istio"))
    groups = _role_groups(files)
    assert set(groups["networking.istio.io"]) == {"gateways", "virtualservices"}
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "ISTIO"
    assert "KUBERNETES_ISTIO_GATEWAY_NAME" not in data  # unset -> gateway per service
    named = gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="istio",
                                        sv_istio_gateway="bzm-gateway"))
    assert yaml.safe_load(named["bzm_configmap.yaml"])["data"][
        "KUBERNETES_ISTIO_GATEWAY_NAME"] == "bzm-gateway"


def test_sv_contour_configmap_and_httpproxy_rbac():
    files = gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="contour"))
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "CONTOUR"
    assert data["KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME"] == "wildcard-tls"
    # Verified live: crane creates one HTTPProxy per virtual service and nothing
    # else in that group.
    assert _role_groups(files)["projectcontour.io"] == ["httpproxies"]
    assert "KUBERNETES_ISTIO_GATEWAY_NAME" not in data


@pytest.mark.parametrize("ingress", ["istio", "contour", "openshift"])
def test_sv_ingress_rbac_is_not_granted_to_crd_based_types(ingress):
    """Crane's expose backends are separate implementations: only the nginx one
    creates an Ingress. Granting it elsewhere is dead permission, and this tool
    exists to keep the Role to what is actually used. Confirmed live for both --
    each published its object with a Role carrying only its own API group."""
    groups = _role_groups(gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress=ingress)))
    assert "ingresses" not in groups.get("networking.k8s.io", [])


def test_sv_openshift_route_rbac_includes_custom_host():
    """Proven live on CRC: with `routes` alone crane's create is rejected 422
    `spec.host: Forbidden: you do not have permission to set the host field of
    the route`, no Route appears, and the virtual service stalls. OpenShift
    gates spec.host behind a separate create on routes/custom-host."""
    files = gen.generate(SV_FACTS, dict(SV_OPTS, platform="openshift",
                                        sv_ingress="openshift"))
    groups = _role_groups(files)
    assert groups["route.openshift.io"] == ["routes", "routes/custom-host"]
    assert "networking.k8s.io" not in groups
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "OPENSHIFT"


def test_sv_openshift_ingress_requires_the_openshift_platform():
    """A Route only exists on OpenShift; asking for one on plain k8s would
    deploy cleanly and then stall with nothing to create."""
    with pytest.raises(ValueError, match="platform=openshift"):
        gen.generate(SV_FACTS, dict(SV_OPTS, platform="k8s",
                                    sv_ingress="openshift"))


def test_sv_istio_gateway_name_is_rejected_for_other_ingress_types():
    with pytest.raises(ValueError, match="sv_istio_gateway"):
        gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="contour",
                                    sv_istio_gateway="bzm-gateway"))


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


# -- sv_expose ---------------------------------------------------------------
# Crane publishes its own Service+Ingress, but the Ingress backend says
# port.number 8080 while its Service exposes port 80, so nothing claims it.
# These render a parallel pair that resolves, without touching crane's.

SV_MOCK = {"name": "vs1svc2", "port": 8080,
           "harbor": "aaa111", "ship": "bbb222"}
EXPOSE_OPTS = {"namespace": "ns1", "sv_subdomain": "apps.example.com",
               "sv_tls_secret": "wildcard-tls"}


def _expose_docs(mocks, opts):
    """Goes through sv_publish_cfg the way the CLI does, so these exercise the
    resolution as well as the rendering."""
    return [d for d in yaml.safe_load_all(
        gen.sv_expose(mocks, opts["namespace"], gen.sv_publish_cfg(opts))) if d]


def test_sv_expose_service_port_equals_target_port():
    """The mismatch that breaks crane's own pair: a backend's port.number is
    resolved against the Service's spec.ports[].port."""
    svc = next(d for d in _expose_docs([SV_MOCK], EXPOSE_OPTS)
               if d["kind"] == "Service")
    port = svc["spec"]["ports"][0]
    assert port["port"] == port["targetPort"] == 8080
    ing = next(d for d in _expose_docs([SV_MOCK], EXPOSE_OPTS)
               if d["kind"] == "Ingress")
    backend = ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend["port"]["number"] == port["port"]
    assert backend["name"] == svc["metadata"]["name"]


def test_sv_expose_selects_identity_labels_not_cranes_hashed_service():
    """Crane's Service names carry a per-deploy hash; the pod labels do not, so
    the pair survives a redeploy."""
    svc = next(d for d in _expose_docs([SV_MOCK], EXPOSE_OPTS)
               if d["kind"] == "Service")
    assert svc["spec"]["selector"] == {
        "BZM_CONTAINER_NAME": "vs1svc2",
        "BZM_HARBOR_ID": "aaa111",
        "BZM_SHIP_ID": "bbb222"}


def test_sv_expose_host_matches_the_endpoint_blazemeter_publishes():
    ing = next(d for d in _expose_docs([SV_MOCK], EXPOSE_OPTS)
               if d["kind"] == "Ingress")
    host = "vs1svc2-8080-ns1.apps.example.com"
    assert ing["spec"]["rules"][0]["host"] == host
    assert ing["spec"]["tls"][0]["secretName"] == "wildcard-tls"
    assert ing["spec"]["tls"][0]["hosts"] == [host]


def test_sv_expose_ingress_class_is_overridable():
    """We own this Ingress, so it can name whatever class the cluster has --
    which is why OpenShift needs no `nginx` IngressClass alias."""
    ing = next(d for d in _expose_docs(
        [SV_MOCK], {**EXPOSE_OPTS, "sv_ingress_class": "openshift-default"})
        if d["kind"] == "Ingress")
    assert ing["spec"]["ingressClassName"] == "openshift-default"
    plain = next(d for d in _expose_docs([SV_MOCK], EXPOSE_OPTS)
                 if d["kind"] == "Ingress")
    assert plain["spec"]["ingressClassName"] == "nginx"


def test_sv_expose_omits_tls_block_when_no_secret():
    ing = next(d for d in _expose_docs(
        [SV_MOCK], {"namespace": "ns1", "sv_subdomain": "apps.example.com"})
        if d["kind"] == "Ingress")
    assert "tls" not in ing["spec"]


def test_sv_expose_renders_every_mock():
    two = [SV_MOCK, {**SV_MOCK, "name": "vs9svc9", "port": 9090}]
    docs = _expose_docs(two, EXPOSE_OPTS)
    assert len(docs) == 4
    assert {d["metadata"]["name"] for d in docs} == {
        "bzm-sv-vs1svc2", "bzm-sv-vs9svc9"}


def test_sv_publish_cfg_requires_a_subdomain():
    with pytest.raises(ValueError, match="sv_subdomain"):
        gen.sv_publish_cfg({"namespace": "ns1"})


def test_sv_publish_cfg_keeps_tls_optional_unlike_generate():
    """_sv_cfg refuses without a TLS secret because crane crash-loops on the
    empty name. This Ingress is ours and never reaches crane, so a plain-HTTP
    pair is a legitimate thing to ask for."""
    cfg = gen.sv_publish_cfg({"sv_subdomain": "apps.example.com"})
    assert cfg.tls_secret is None
    assert cfg.ingress_class == gen.SV_EXPOSE_DEFAULT_INGRESS_CLASS


# --- contributor onboarding: the no-account path ------------------------------

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_missing_facts_file_points_at_the_sample():
    """Used to be a bare FileNotFoundError from cmd_generate's first line."""
    with pytest.raises(SystemExit) as e:
        facts_mod.load("/nonexistent/facts.json")
    msg = str(e.value)
    assert "examples/facts.example.json" in msg and "bzm-opl-gen facts" in msg


def test_malformed_facts_file(tmp_path):
    p = tmp_path / "facts.json"
    p.write_text("{oops")
    with pytest.raises(SystemExit, match="not valid JSON"):
        facts_mod.load(str(p))


def test_example_facts_generate_without_an_account():
    """`generate --facts examples/facts.example.json` is the first thing a
    contributor without a BlazeMeter key can run; keep the sample in step with
    what the generator reads out of it."""
    f = facts_mod.load(os.path.join(EXAMPLES, "facts.example.json"))
    files = gen.generate(f, {"namespace": "demo"})
    _all_yaml_parse(files)
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    assert cm["data"]["HARBOR_ID"] == f["harbor_id"]
    assert cm["data"]["SHIP_ID"] == f["ships"][0]["id"]   # single ship, auto


def test_example_facts_have_threads_per_engine():
    """Null threadsPerEngine is a hard doctor failure; the sample must not
    teach the shape that fails preflight."""
    f = facts_mod.load(os.path.join(EXAMPLES, "facts.example.json"))
    assert f["threads_per_engine"]


def test_endpoint_host_is_built_in_one_place():
    """The host BlazeMeter advertises is <mock>-<port>-<namespace>.<domain>.
    sv-expose puts it on an Ingress and the watch panel shows it to a human; a
    second copy of the formula would let those two disagree about the one
    string the whole feature is judged by."""
    host = gen.sv_endpoint_host("vs1", 8080, "ns1", "apps.example.com")
    assert host == "vs1-8080-ns1.apps.example.com"
    # No subdomain means there is no host to show yet, not a broken one.
    assert gen.sv_endpoint_host("vs1", 8080, "ns1", None) is None
    # ...and sv-expose's Ingress must use exactly that.
    out = gen.sv_expose([{"name": "vs1", "port": 8080, "harbor": "h", "ship": "s"}],
                        "ns1", gen.SvPublish("apps.example.com", None, "nginx"))
    assert f"host: {host}" in out


def test_sv_on_nodeport_still_needs_no_cluster_rbac():
    """What #60 actually measured, pinned as the thing that could regress.

    The old refusal claimed NODEPORT forced a cluster-scoped Node read that a
    namespaced Role cannot grant. The live run (crane 3.7.55, service-mock
    6.0.29.6, ingress-nginx v1.11.3 on k8s 1.32) denied that read -- crane logs
    the 403 and falls back to 127.0.0.1 -- and the virtual service served
    anyway, because the endpoint comes from the web-expose subdomain and not
    from that address. So the pairing must keep generating namespaced RBAC and
    nothing else; a ClusterRole appearing here would mean someone had reinstated
    the disproved mechanism by way of the manifests instead of the message.
    """
    files = gen.generate(SV_FACTS, dict(SV_OPTS, service_type="NODEPORT"))
    assert not [n for n in files if "clusterrole" in n.lower()]
    # The Role carries the ingress grant and no `nodes` rule to make up for it.
    # Every resource of every rule, flat: four rules share the core "" group, so
    # collecting them into a dict keyed by group drops three of them and lets a
    # `nodes` rule through in whichever position did not survive.
    role = yaml.safe_load(files["bzm_role.yaml"])
    granted = [res for r in role["rules"] for res in r["resources"]]
    assert "ingresses" in granted
    assert "nodes" not in granted
