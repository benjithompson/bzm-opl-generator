import json
import os
import pathlib
import re
import shlex
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from bzm_opl_gen import cert  # noqa: E402
from bzm_opl_gen import facts as facts_mod  # noqa: E402
from bzm_opl_gen import generate as gen  # noqa: E402
from bzm_opl_gen import (bundle_env, bundle_names, bundle_options,  # noqa: E402
                         ca_trust, footprint, markers as markers_mod,
                         nodepools, render_docker, required_fields,
                         service_virt)
from tls_fixtures import (  # noqa: E402
    SV_CERT, SV_CERT_NO_NAMES, SV_HOST, SV_KEY, SV_KEY_PKCS1, SV_NAMES,
    SV_WILDCARD_HOST, SV_WRONG_HOST)
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
    # Off, unlike BlazeMeter's own manifest -- see test_auto_update_defaults_off.
    assert cm["data"]["AUTO_KUBERNETES_UPDATE"] == "false"
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
    assert ov == {"taurus-cloud:latest": "reg.local/bzm/blazemeter/v4:2.4.444-reduced",
                  "apm-image:latest": "reg.local/bzm/blazemeter/apm:1.7.112"}  # mock excluded
    assert cm["data"]["AUTO_KUBERNETES_UPDATE"] == "false"
    d = files["bzm_deployment.yaml"]
    assert "reg.local/bzm/crane:3.7.55" in d
    assert "imagePullSecrets" in d and "pullsec" in d


def _auto(**over):
    cm = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1", **over})
                        ["bzm_configmap.yaml"])
    return cm["data"]["AUTO_KUBERNETES_UPDATE"]


def test_auto_update_defaults_off():
    """AUTO_KUBERNETES_UPDATE defaults to false, with or without a private
    registry."""
    assert _auto() == "false"
    assert _auto(private_registry="reg.local/bzm") == "false"


def test_auto_update_can_be_asked_for():
    """auto_update=True writes true; False writes false."""
    assert _auto(auto_update=True) == "true"
    assert _auto(auto_update=True, private_registry="reg.local/bzm") == "true"
    assert _auto(auto_update=False) == "false"


def test_auto_update_writes_the_kubernetes_variable_only():
    """A Kubernetes bundle writes AUTO_KUBERNETES_UPDATE and not the docker
    AUTO_UPDATE."""
    cm = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1", "auto_update": False})
                        ["bzm_configmap.yaml"])["data"]
    assert "AUTO_UPDATE" not in cm
    assert cm["AUTO_KUBERNETES_UPDATE"] == "false"


def test_configmap_states_what_each_setting_costs():
    """The ConfigMap comment beside AUTO_KUBERNETES_UPDATE states what on
    and off mean."""
    on = gen.generate(FACTS, {"namespace": "ns1", "auto_update": True}
                      )["bzm_configmap.yaml"]
    assert "Auto-update on" in on and "replaces its own image" in on
    off = gen.generate(FACTS, {"namespace": "ns1"})["bzm_configmap.yaml"]
    assert "loses support" in off


def test_readme_says_the_agent_is_pinned():
    """With auto-update off, the README says which version the agent stays
    on."""
    for over in ({}, {"auto_update": False}, {"private_registry": "reg.local/bzm"}):
        readme = gen.generate(FACTS, {"namespace": "ns1", **over})["README.md"]
        assert "3.7.55" in readme and "Auto-update is **off**" in readme, over
    on = gen.generate(FACTS, {"namespace": "ns1", "auto_update": True})["README.md"]
    assert "Auto-update is **off**" not in on


def test_auto_update_refuses_a_value_that_is_neither():
    """A non-boolean auto_update (such as the string "false") is refused."""
    with pytest.raises(ValueError, match="auto_update"):
        gen.generate(FACTS, {"namespace": "ns1", "auto_update": "false"})


def test_images_follow_location_funcids():
    gui_facts = dict(FACTS, func_ids=["performance", "functionalGui", "chrome:default"])
    files = gen.generate(gui_facts, {"namespace": "ns1", "private_registry": "reg.local"})
    ov = json.loads(yaml.safe_load(files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert "blazemeter/doduo:latest" in ov          # gui funcId -> gui images
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
    """Engines inherit crane's UID:GID and drop all capabilities on both
    platforms."""
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
    """NODEPORT renders without the ClusterRole, and nothing claims it needs
    one."""
    files = gen.generate(FACTS, {"namespace": "ns1", "service_type": "NODEPORT"})
    _all_yaml_parse(files)
    assert "bzm_clusterrole.yaml" not in files
    header = gen.generate(FACTS, {"namespace": "ns1", "service_type": "NODEPORT",
                                  "cluster_rbac": True})["bzm_clusterrole.yaml"]
    assert "127.0.0.1" not in header


# -- service account ---------------------------------------------------------
# The name reaches the Deployment and both bindings; `create` gates one file.

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
    # The Role and RoleBinding keep their fixed names.
    assert yaml.safe_load(files["bzm_rolebinding.yaml"])["metadata"]["name"] \
        == "role-binding-crane"


def test_existing_service_account_is_referenced_not_created():
    """With create off, the ServiceAccount is referenced everywhere and not
    emitted."""
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
def test_unnamed_service_account_becomes_a_placeholder(name):
    """A blank ServiceAccount name becomes its marker in every reference,
    and the README names it."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "service_account_name": name,
                                 "service_account_create": False})
    assert set(_sa_refs(files).values()) == {markers_mod.marker("service_account_name")}
    # ...and the person handed the bundle is told, rather than finding out from
    # a rejected apply.
    assert "service_account_name" in files["README.md"]
    assert "not finished" in files["README.md"]


def test_service_account_round_trips_through_the_profile():
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "service_account_name": "platform-sa",
                                 "service_account_create": False})
    prof = json.loads(files[bundle_names.PROFILE_FILE])
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


# -- two node pools -----------------------------------------------------------
# Crane's placement is the pod spec; the engines' is the KUBERNETES_*_JSON env.

CRANE_POOL = {"pool": "crane"}
ENGINE_POOL = {"pool": "bzm-engines"}
ENGINE_TOL = [{"key": "bzm.io/engines", "operator": "Equal", "value": "true",
               "effect": "NoSchedule"}]


def _placement(files):
    """(crane nodeSelector, crane tolerations, engine selector, engine tolerations)
    as the two mechanisms actually carry them."""
    spec = yaml.safe_load(files["bzm_deployment.yaml"])["spec"]["template"]["spec"]
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    return (spec.get("nodeSelector"), spec.get("tolerations"),
            json.loads(cm["KUBERNETES_NODE_SELECTOR_JSON"])
            if "KUBERNETES_NODE_SELECTOR_JSON" in cm else None,
            json.loads(cm["KUBERNETES_TOLERATIONS_JSON"])
            if "KUBERNETES_TOLERATIONS_JSON" in cm else None)


def test_engine_pool_is_separate_from_cranes():
    files = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                 "engine_node_selector": ENGINE_POOL,
                                 "engine_tolerations": ENGINE_TOL})
    _all_yaml_parse(files)
    crane_sel, crane_tol, eng_sel, eng_tol = _placement(files)
    assert crane_sel == CRANE_POOL      # crane stays on its own small pool
    assert crane_tol is None            # ...which needs no taint
    assert eng_sel == ENGINE_POOL       # engines are aimed elsewhere
    assert eng_tol == ENGINE_TOL


def test_engines_follow_crane_when_no_engine_override():
    """Unset engine placement inherits crane's."""
    tol = [{"key": "lifecycle", "operator": "Exists", "effect": "NoSchedule"}]
    files = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                 "tolerations": tol})
    crane_sel, crane_tol, eng_sel, eng_tol = _placement(files)
    assert (eng_sel, eng_tol) == (crane_sel, crane_tol) == (CRANE_POOL, tol)


def test_empty_engine_placement_is_not_the_same_as_unset():
    """Explicitly empty engine placement gives engines no selector or
    tolerations, unlike unset."""
    files = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                 "tolerations": ENGINE_TOL,
                                 "engine_node_selector": {},
                                 "engine_tolerations": []})
    crane_sel, crane_tol, eng_sel, eng_tol = _placement(files)
    assert (crane_sel, crane_tol) == (CRANE_POOL, ENGINE_TOL)
    # Absent from the ConfigMap entirely, so crane stamps nothing on the engines.
    assert eng_sel is None and eng_tol is None


def test_nodepool_recipe_only_when_the_pools_differ():
    """nodepools.md is emitted only for separate pools, with this bundle's
    labels and taints."""
    one_pool = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL})
    assert bundle_names.NODEPOOLS_FILE not in one_pool

    two_pool = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                    "engine_node_selector": ENGINE_POOL,
                                    "engine_tolerations": ENGINE_TOL,
                                    "engine_cpu_limit": "2",
                                    "engine_mem_limit": "8Gi"})
    md = two_pool[bundle_names.NODEPOOLS_FILE]
    # The label and taint the manifests actually use, so the commands create the
    # pool this bundle selects rather than a worked example of a different one.
    assert "pool=bzm-engines" in md
    assert "bzm.io/engines=true:NoSchedule" in md
    # The stamped-request trap and the only lever that closes it.
    assert footprint.ENGINE_DEFAULT_REQUEST_CPU in md and "maxPods" in md
    for flavour in ("GKE", "EKS", "AKS", "OpenShift", "kubeadm"):
        assert flavour in md


def test_gke_maxpods_respects_the_floor_the_api_enforces():
    """The GKE command never goes below GKE's max-pods floor, and says how
    many engines that admits."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "engine_node_selector": ENGINE_POOL,
                                 "engine_cpu_limit": "2", "engine_mem_limit": "8Gi"})
    md = files[bundle_names.NODEPOOLS_FILE]
    gke = md[md.index("### GKE"):md.index("### EKS")]
    emitted = int(re.search(r"--max-pods-per-node (\d+)", gke).group(1))
    assert emitted >= footprint.GKE_MIN_MAX_PODS
    # And having been forced up, it says what that costs rather than still
    # claiming one engine per node.
    assert "will not go below" in gke
    assert f"{nodepools._engines_per_node(footprint.TYPICAL_SYSTEM_PODS + 1, footprint.GKE_MIN_MAX_PODS)} engines a node" in gke


def test_gke_node_is_sized_for_the_engines_the_floor_permits():
    """The GKE machine type is sized for the engines its maxPods admits."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "engine_node_selector": ENGINE_POOL,
                                 "engine_cpu_limit": "2", "engine_mem_limit": "8Gi"})
    md = files[bundle_names.NODEPOOLS_FILE]
    gke = md[md.index("### GKE"):md.index("### EKS")]
    per_node = nodepools._engines_per_node(footprint.TYPICAL_SYSTEM_PODS + 1, footprint.GKE_MIN_MAX_PODS)
    cpu = int(re.search(r"--machine-type <at least (\d+) vCPU", gke).group(1))
    assert cpu >= 2 * per_node          # 2 CPU of engine each, plus overhead


def test_nodepool_recipe_emits_no_dangling_continuations():
    """No command in the recipe ends in a dangling line continuation."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "engine_node_selector": {},
                                 "engine_tolerations": []})
    md = files[bundle_names.NODEPOOLS_FILE]
    in_block, blocks = False, []
    for line in md.splitlines():
        if line.startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            blocks.append(line)
    assert blocks, "the recipe emitted no commands at all"
    # A trailing backslash must be continued by a real command line, never by
    # the end of the block or a blank.
    for i, line in enumerate(blocks):
        if line.rstrip().endswith("\\"):
            assert i + 1 < len(blocks) and blocks[i + 1].strip(), \
                f"dangling continuation at {line!r}"


@pytest.mark.parametrize("extra,expected", [
    ({}, []),
    ({"private_registry": "reg.example.com/bzm"}, ["1. Mirror", "2. Apply"]),
    ({"engine_node_selector": {"pool": "e"}}, ["1. Create the node pools", "2. Apply"]),
    ({"private_registry": "reg.example.com/bzm", "engine_node_selector": {"pool": "e"}},
     ["1. Create the node pools", "2. Mirror", "3. Apply"]),
])
def test_deploy_steps_are_numbered_once_across_both_prerequisites(extra, expected):
    """The optional deploy steps are numbered once, in order."""
    md = gen.generate(FACTS, {"namespace": "ns1", **extra})["README.md"]
    for marker in expected:
        assert f"**{marker}" in md
    if not expected:
        assert "**1." not in md          # nothing to do before applying


def _crane_ephemeral(files):
    c = yaml.safe_load(files["bzm_deployment.yaml"])["spec"]["template"]["spec"]["containers"][0]
    return (c["resources"]["requests"]["ephemeral-storage"],
            c["resources"]["limits"]["ephemeral-storage"])


def test_crane_ephemeral_storage_request_equals_limit_by_default():
    # Request equals limit: GKE Autopilot lowers the limit to the request.
    req, lim = _crane_ephemeral(gen.generate(FACTS, {"namespace": "ns1"}))
    assert req == lim == footprint.CRANE_EPHEMERAL_STORAGE


def test_crane_ephemeral_storage_clears_measured_usage():
    # Crane uses about 161MiB within seconds of starting; pin the floor.
    assert parse_memory(footprint.CRANE_EPHEMERAL_STORAGE) >= 512 * 1024 * 1024


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


# -- the create command for an existing trust-bundle ConfigMap ----------------
# The README prints it with --from-file=<key>=<path>, so the key matches the
# mount whatever the file is called.


def test_the_existing_configmap_mode_prints_the_create_command():
    files = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": False,
                                 "ca_existing_configmap": "corp-trust",
                                 "ca_configmap_key": "trust.pem"})
    assert ("kubectl -n ns1 create configmap corp-trust "
            "--from-file=trust.pem=" in files["README.md"])


def test_the_create_command_carries_the_default_key_when_none_was_set():
    """The create command uses the default key when none was set."""
    files = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": False,
                                 "ca_existing_configmap": "corp-trust"})
    assert ("kubectl -n ns1 create configmap corp-trust "
            "--from-file=ca-bundle.crt=" in files["README.md"])


def test_the_key_and_the_file_name_cannot_disagree():
    """The create command uses --from-file=KEY=PATH, so the key is the one
    the manifests mount."""
    readme = gen.generate(FACTS, {"namespace": "ns1",
                                  "ca_existing_configmap": "corp-trust",
                                  "ca_configmap_key": "trust.pem"})["README.md"]
    line, = [ln for ln in readme.splitlines() if "create configmap" in ln]
    _, _, from_file = line.partition("--from-file=")
    assert from_file.startswith("trust.pem=")


@pytest.mark.parametrize("opts", [
    {},
    {"ca_bundle": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"},
    {"ca_openshift_inject": True},
])
def test_no_create_command_where_the_bundle_makes_its_own_configmap(opts):
    """Inline and injected CA modes print no create command."""
    files = gen.generate(FACTS, dict(opts, namespace="ns1"))
    assert "create configmap" not in files["README.md"]


def test_the_create_command_is_in_the_helm_bundle_too():
    """The helm README prints the same create command."""
    files = gen.generate(FACTS, {"namespace": "ns1", "output_format": "helm",
                                 "ca_existing_configmap": "corp-trust"})
    assert "create configmap corp-trust" in files["README.md"]


@pytest.mark.parametrize("fmt", ["manifests", "helm"])
def test_a_cluster_bundle_makes_the_namespace_before_it_puts_a_configmap_in_it(fmt):
    """The namespace is created before the trust-bundle ConfigMap, in both
    formats."""
    readme = gen.generate(FACTS, {"namespace": "ns1", "output_format": fmt,
                                  "openshift_cluster": False,
                                  "ca_existing_configmap": "corp-trust"})["README.md"]
    assert "kubectl create namespace ns1" in readme
    assert readme.index("create namespace ns1") < readme.index("create configmap")


def test_the_create_command_follows_the_cluster_not_the_posture():
    """The create command uses oc or kubectl according to openshift_cluster."""
    files = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": True,
                                 "ca_existing_configmap": "corp-trust"})
    assert "oc -n ns1 create configmap corp-trust" in _commands(files)
    plain = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": False,
                                 "ca_existing_configmap": "corp-trust"})
    assert "kubectl -n ns1 create configmap corp-trust" in _commands(plain)


def test_a_plain_kubernetes_bundle_is_what_you_get_without_answering():
    """By default the bundle's commands use kubectl."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "ca_existing_configmap": "corp-trust"})
    cmds = _commands(files)
    assert "kubectl -n ns1 create configmap corp-trust" in cmds
    assert "oc " not in cmds


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
    assert "WARNING: plaintext proxy credentials" in cm_text
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


def test_limits_env_is_always_carried_defaults_included():
    """The engine limits env is always set, defaulting to
    ENGINE_DEFAULT_CPU/MEM."""
    cm = {}
    for platform in ("k8s", "openshift"):
        files = gen.generate(FACTS, {"namespace": "ns1", "platform": platform})
        cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
        assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == footprint.ENGINE_DEFAULT_CPU
        assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == footprint.ENGINE_DEFAULT_MEM
    # The ephemeral pair stays opt-in: engine_size() vouches for CPU and
    # memory only, and there is no documented ephemeral default to state.
    assert "KUBERNETES_LIMITS_EPHEMERAL_STORAGE" not in cm


def test_engine_limits_derive_from_the_location():
    """Engine limits default to the location's overrideCPU/overrideMemory
    (MB read as Mi)."""
    facts = {**FACTS, "override_cpu": 1, "override_memory": 4096}
    files = gen.generate(facts, {"namespace": "ns1"})
    cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == "1"
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "4Gi"
    # An odd MB value stays in Mi rather than being rounded to a lie.
    odd = gen.generate({**FACTS, "override_memory": 8196},
                       {"namespace": "ns1"})
    cm = yaml.safe_load(odd["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "8196Mi"
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == footprint.ENGINE_DEFAULT_CPU
    # An explicit option outranks the location.
    explicit = gen.generate(facts, {"namespace": "ns1",
                                    "engine_cpu_limit": "2",
                                    "engine_mem_limit": "8Gi"})
    cm = yaml.safe_load(explicit["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == "2"
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "8Gi"


def test_an_override_memory_below_an_engines_floor_is_not_derived():
    """An overrideMemory below the floor is not derived; an explicit option
    is never floored."""
    for mb in (4, 32, 512):
        files = gen.generate(
            {**FACTS, "override_cpu": 1, "override_memory": mb},
            {"namespace": "ns1"})
        cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
        assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == footprint.ENGINE_DEFAULT_MEM, mb
        assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == "1"
    at_floor = gen.generate({**FACTS, "override_memory": 1024},
                            {"namespace": "ns1"})
    cm = yaml.safe_load(at_floor["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "1Gi"
    explicit = gen.generate(FACTS, {"namespace": "ns1",
                                    "engine_mem_limit": "512Mi"})
    cm = yaml.safe_load(explicit["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "512Mi"


def test_derived_engine_limits_land_in_the_profile_and_replay_stably():
    """Derived limits are recorded in profile.json, so a replay against
    changed facts reproduces the bundle."""
    facts = {**FACTS, "override_cpu": 1, "override_memory": 4096}
    files = gen.generate(facts, {"namespace": "ns1"})
    prof = json.loads(files["profile.json"])
    assert prof["engine_cpu_limit"] == "1"
    assert prof["engine_mem_limit"] == "4Gi"
    resized = {**FACTS, "override_cpu": 4, "override_memory": 16384}
    replay = gen.generate(resized, prof)
    cm = yaml.safe_load(replay["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == "1"
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "4Gi"


def test_docker_derives_no_engine_limits():
    """Docker ignores the engine limits, so none are derived or reported."""
    facts = {**FACTS, "override_cpu": 1, "override_memory": 4096}
    files = gen.generate(facts, DOCKER)
    prof = json.loads(files["profile.json"])
    assert prof["engine_cpu_limit"] is None
    assert prof["engine_mem_limit"] is None
    assert "`engine_cpu_limit`" not in files["README.md"]







def test_unparseable_engine_quantity_rejected():
    with pytest.raises(ValueError, match="engine_mem_limit"):
        gen.generate(FACTS, {"namespace": "ns1", "engine_mem_limit": "8 gigs"})
    with pytest.raises(ValueError, match="engine_cpu_limit"):
        gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "two"})


def test_engine_size_helper():
    o = {**bundle_options.DEFAULT_OPTIONS, "engine_cpu_limit": "500m", "engine_mem_limit": "1Gi"}
    assert bundle_options.engine_size(o) == (500, 1024 ** 3)
    assert bundle_options.engine_size(dict(bundle_options.DEFAULT_OPTIONS)) == (2000, 8 * 1024 ** 3)


def test_crane_resources_come_from_the_constants():
    """Crane's resources in the Deployment come from the footprint
    constants."""
    d = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1"})["bzm_deployment.yaml"])
    res = d["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert res["limits"]["cpu"] == footprint.CRANE_CPU_LIMIT
    assert res["limits"]["memory"] == footprint.CRANE_MEM_LIMIT
    assert res["requests"]["cpu"] == footprint.CRANE_CPU_REQUEST
    assert res["requests"]["memory"] == footprint.CRANE_MEM_REQUEST



# -- the cluster check --------------------------------------------------------

def _hook_docs(out):
    return {d["metadata"]["name"]: d
            for d in yaml.safe_load_all(out[bundle_names.HOOK_FILE])}


def test_no_cluster_check_unless_asked_for():
    """crane-hook is not emitted by default."""
    assert bundle_names.HOOK_FILE not in gen.generate(FACTS, {"ship_id": "s1"})


def test_the_cluster_check_is_told_what_the_bundle_decided():
    """crane-hook's objects carry the bundle's namespace, account and
    registry."""
    out = gen.generate(FACTS, {"ship_id": "s1", "namespace": "ns1",
                               "service_account_name": "bzm-agent",
                               "crane_hook": True})
    docs = _hook_docs(out)
    assert sorted(docs) == ["bzm-cranehook", "bzm-cranehook-binding", "cranehook"]
    assert [d["kind"] for d in docs.values()] == ["Role", "RoleBinding", "Pod"]
    pod = docs["cranehook"]
    assert pod["metadata"]["namespace"] == "ns1"
    assert pod["spec"]["serviceAccountName"] == "bzm-agent"
    # A failed check is the answer. Restarting would turn a red exit code into a
    # CrashLoopBackOff, which reads like the check itself is broken.
    assert pod["spec"]["restartPolicy"] == "Never"
    env = {e["name"]: e["value"] for e in pod["spec"]["containers"][0]["env"]}
    assert env["WORKING_NAMESPACE"] == "ns1"
    assert env["SERVICE_ACCOUNT_NAME"] == "bzm-agent"
    # It is told what its own Role is called, so the names it checks are the
    # names that were emitted.
    assert env["ROLE_NAME"] == docs["bzm-cranehook"]["metadata"]["name"]
    assert env["ROLE_BINDING_NAME"] == docs["bzm-cranehook-binding"]["metadata"]["name"]


def test_the_cluster_check_grants_itself_nothing_the_agent_needs():
    """crane-hook's own Role is read-only."""
    docs = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True}))
    verbs = {v for rule in docs["bzm-cranehook"]["rules"] for v in rule["verbs"]}
    assert verbs == {"get", "list"}
    assert docs["bzm-cranehook-binding"]["roleRef"]["name"] == "bzm-cranehook"


def test_the_cluster_check_follows_the_platform_uid_rule():
    """crane-hook pins its UID only off OpenShift."""
    k8s = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True,
                                          "platform": "k8s", "run_as_user": 1500}))
    sc = k8s["cranehook"]["spec"]["containers"][0]["securityContext"]
    assert sc["runAsUser"] == 1500 and sc["runAsGroup"] == 1500
    ocp = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True,
                                          "platform": "openshift"}))
    assert "runAsUser" not in ocp["cranehook"]["spec"]["containers"][0]["securityContext"]


def test_the_cluster_check_is_told_about_the_ingress_it_should_check():
    """crane-hook gets the SV ingress env only when an ingress is
    configured."""
    sv = gen.generate(dict(FACTS, func_ids=["mockServices"]),
                      {"ship_id": "s1", "crane_hook": True, "sv_ingress": "nginx",
                       "sv_subdomain": "apps.example.com", "sv_tls_secret": "wild"})
    env = {e["name"]: e["value"] for e
           in _hook_docs(sv)["cranehook"]["spec"]["containers"][0]["env"]}
    assert env["KUBERNETES_WEB_EXPOSE_TYPE"] == "NGINX"
    assert env["KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME"] == "wild"

    perf = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True}))
    env = {e["name"]: e["value"] for e
           in perf["cranehook"]["spec"]["containers"][0]["env"]}
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in env


def test_the_cluster_check_image_is_mirrored_with_the_rest():
    """The crane-hook image is mirrored and pulled from the private
    registry."""
    out = gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True,
                               "private_registry": "reg.local/bzm"})
    assert "reg.local/bzm/cranehook:latest" in out["bzm-opl-image-mirror.sh"]
    pod = _hook_docs(out)["cranehook"]
    assert pod["spec"]["containers"][0]["image"] == "reg.local/bzm/cranehook:latest"


def test_mirror_script_is_self_contained():
    """The mirror script needs no API key and fails fast on a refused push."""
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
    """The finished manifests README stays under the cap for every sizing
    model, and carries the deploy and verify steps."""
    import bzm_opl_gen.plan as plan_mod
    opts = {"namespace": "ns1", "auth_token": "de" * 32, "sv_ingress": "none"}
    for fids in [[f] for f in plan_mod.SIZING_MODELS] + [["tdm"], []]:
        long = gen.generate({**FACTS, "func_ids": fids, "slots": 2,
                             "threads_per_engine": 500}, opts)["README.md"]
        assert len(long.splitlines()) < 48, (
            f"README for {fids} is {len(long.splitlines())} lines")
    readme = gen.generate(
        FACTS, {"namespace": "ns1", "auth_token": "de" * 32})["README.md"]
    assert "not finished" not in readme
    assert len(readme.splitlines()) < 48, "README is getting long"
    # The four things someone needs: what this is, how to deploy, how to check,
    # and what it costs to run.
    assert "apply -f bzm_deployment.yaml" in readme
    assert "rollout status deploy/crane" in readme
    assert "online" in readme
    assert footprint.ENGINE_DEFAULT_REQUEST_CPU in readme     # the engine request gap
    assert "bzm_limitrange.yaml" not in readme


def _deploy_block(readme):
    """The lines of the Deploy section, up to the next heading."""
    return readme.split("## Deploy", 1)[1].split("\n## ", 1)[0].splitlines()


@pytest.mark.parametrize("fmt,cli,openshift", [
    ("manifests", "kubectl", False), ("manifests", "oc", True),
    ("helm", "kubectl", False), ("helm", "oc", True),
])
def test_the_deploy_block_leads_with_a_command_that_makes_the_namespace(
        fmt, cli, openshift):
    """The first deploy command creates the namespace idempotently, and no
    bundle emits a namespace object."""
    readme = gen.generate(FACTS, {"namespace": "ns1", "output_format": fmt,
                                  "openshift_cluster": openshift})["README.md"]
    lines = [ln for ln in _deploy_block(readme)
             if ln.startswith((cli, "helm "))]
    assert lines, "the Deploy block prints no command at all"
    if fmt == "helm":
        assert lines[0].startswith("helm install")
        assert "--create-namespace" in lines[0]
    else:
        assert lines[0] == (f"{cli} get namespace ns1 >/dev/null 2>&1 "
                            f"|| {cli} create namespace ns1")
        # ...and it is the *first* thing, not merely present: everything under
        # it names the namespace with `-n`.
        assert all("-n ns1" in ln for ln in lines[1:])
    # The bundle does not own what it just created, and the README says so
    # rather than leaving a reader to infer it from a missing manifest.
    assert "bzm_namespace.yaml" not in gen.generate(
        FACTS, {"namespace": "ns1", "output_format": fmt})


def test_the_docker_readme_names_no_namespace_of_its_own():
    """The docker README gives no namespace instruction; a set namespace
    appears only in the not-carried table."""
    for opts in ({}, {"namespace": "ns1"}):
        readme = gen.generate(FACTS, dict(opts, output_format="docker",
                                          auth_token="de" * 32))["README.md"]
        assert "create namespace" not in readme
        assert "-n ns1" not in readme
        assert " -n " not in readme
        if "namespace" in readme:
            row, = [ln for ln in readme.splitlines() if "`namespace`" in ln]
            assert row.startswith("| `namespace` |")


def _commands(files):
    """Every emitted line that runs a cluster command, joined into one
    string."""
    return "\n".join(v for k, v in files.items()
                     if k.endswith(".md") or k.endswith(".sh"))


def test_the_cluster_decides_oc_or_kubectl_not_the_posture():
    """openshift_cluster, not platform, decides oc or kubectl in every
    emitted command."""
    opts = {"namespace": "ns1", "auth_token": "de" * 32,
            "node_selector": CRANE_POOL, "engine_node_selector": ENGINE_POOL,
            "engine_tolerations": ENGINE_TOL}
    oc = _commands(gen.generate(FACTS, dict(opts, openshift_cluster=True)))
    assert "oc -n ns1 rollout status" in oc and "oc label node" in oc
    assert "kubectl " not in oc

    plain = _commands(gen.generate(FACTS, dict(opts, openshift_cluster=False)))
    assert "kubectl -n ns1 rollout status" in plain and "kubectl label node" in plain
    assert "oc " not in plain
    # ...and the pinned-UID posture names its own cluster, so it answers alone.
    assert "oc " not in _commands(gen.generate(FACTS, dict(opts, platform="k8s")))


# -- blank required fields ----------------------------------------------------
# A field left empty resolves to its own marker, not to "" or a refusal.


def test_a_finished_bundle_carries_no_marker():
    files = gen.generate(FACTS, {"namespace": "ns1", "auth_token": "de" * 32})
    assert required_fields.placeholder_options(json.loads(files[bundle_names.PROFILE_FILE])) == []
    assert not markers_mod.MARKER_RE.search("".join(files.values()))


# Marker -> field, for the option keys plus harbor_id and ship_id. Only these
# are judged, so MARKER_PATTERN quoted as a pattern is not read as a field.
FIELD_BY_MARKER = {markers_mod.marker(k): k for k in
                   list(bundle_options.DEFAULT_OPTIONS) + ["harbor_id", "ship_id"]}


def _markers_carried(files):
    """Every field the bundle's files carry a marker for, excluding the
    README and the verbatim chart."""
    return {FIELD_BY_MARKER[m]
            for name, text in files.items()
            if name != "README.md" and not name.startswith(bundle_names.CHART_DIR + "/")
            for m in markers_mod.MARKER_RE.findall(text) if m in FIELD_BY_MARKER}


def _fields_named(readme):
    """Every field the README's not-finished table has a row for."""
    return set(re.findall(r"^\| `([a-z_.]+)` \| `<", readme, re.M))


# Every mix of the five fields a form can leave empty, over all three formats.
@pytest.mark.parametrize("fmt", ["manifests", "helm", "docker"])
@pytest.mark.parametrize("blank", [
    set(), {"harbor_id"}, {"ship_id"}, {"auth_token"},
    {"namespace"}, {"service_account_name"},
    {"harbor_id", "ship_id"},
    {"auth_token", "namespace", "service_account_name"},   # the mix reported
    {"harbor_id", "ship_id", "auth_token"},
    {"harbor_id", "ship_id", "namespace", "service_account_name", "auth_token"},
])
def test_the_readme_names_exactly_the_markers_the_bundle_carries(fmt, blank):
    """The README's not-finished table names exactly the fields the bundle's
    files carry markers for, across formats and blank-field mixes."""
    ids = {"harbor_id": "0a1b2c3d4e5f60718293a4b5",
           "ship_id": "6c5b4a39281706f5e4d3c2b1"}
    facts = facts_mod.manual(
        "" if "harbor_id" in blank else ids["harbor_id"],
        "" if "ship_id" in blank else ids["ship_id"])
    o = {"platform": "k8s", "output_format": fmt,
         "ship_id": "" if "ship_id" in blank else ids["ship_id"],
         "namespace": "" if "namespace" in blank else "cust",
         "service_account_name": "" if "service_account_name" in blank else "crane",
         "auth_token": "" if "auth_token" in blank else "de" * 32}
    files = gen.generate(facts, o)
    assert _markers_carried(files) == _fields_named(files["README.md"])


@pytest.mark.parametrize("use_secret", [True, False])
def test_the_summary_table_never_says_a_missing_credential_is_there(use_secret):
    """The summary table's AUTH_TOKEN row names the file and says when the
    token was not supplied."""
    where = "bzm_secret.yaml" if use_secret else "bzm_configmap.yaml"
    for token, supplied in (("de" * 32, True), ("", False)):
        readme = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222",
                                      "use_secret": use_secret,
                                      "auth_token": token})["README.md"]
        row, = [ln for ln in readme.splitlines() if ln.startswith("| AUTH_TOKEN")]
        assert where in row
        assert ("not supplied" in row) is not supplied
        assert (markers_mod.marker("auth_token") in row) is not supplied


def test_the_marker_reaches_the_objects_that_name_the_field():
    """A blank namespace's marker reaches every object's namespace, which
    the API server rejects."""
    files = gen.generate(FACTS, {"namespace": "", "ship_id": "bbb222"})
    assert yaml.safe_load(
        files["bzm_deployment.yaml"])["metadata"]["namespace"] \
        == markers_mod.marker("namespace")
    assert "apply -f" in files["README.md"]


# The third is the shared marker older profiles carry.
@pytest.mark.parametrize("given",
                         ["<NAMESPACE>", "  <NAMESPACE>  ", "<PLACEHOLDER>"])
def test_the_marker_is_recognised_around_whitespace(given):
    """A marker with surrounding whitespace is still a marker."""
    assert markers_mod.is_placeholder(given)
    assert required_fields.placeholder_options({"namespace": given}) == ["namespace"]


def test_docker_does_not_mark_the_fields_it_ignores():
    """Docker marks neither namespace nor ServiceAccount, which it ignores."""
    files = gen.generate(FACTS, {**DOCKER, "namespace": "",
                                 "service_account_name": "",
                                 "auth_token": "de" * 32})
    assert required_fields.placeholder_options(json.loads(files[bundle_names.PROFILE_FILE])) == []
    assert "not finished" not in files["README.md"]


def test_a_marker_the_page_supplied_is_reported_too():
    """Markers supplied in the options (registry, proxy) are reported like
    generated ones."""
    o = {"namespace": "ns1", "auth_token": "de" * 32,
         "private_registry": markers_mod.marker("private_registry"),
         "proxy": {"https": markers_mod.marker("proxy.https"),
                   "no_proxy": "localhost"}}
    files = gen.generate(FACTS, o)
    assert required_fields.placeholder_options(json.loads(files[bundle_names.PROFILE_FILE])) == [
        "private_registry", "proxy.https"]
    readme = files["README.md"]
    assert "`private_registry`" in readme and "`proxy.https`" in readme


def test_placeholder_block_is_bounded_by_the_fields_not_the_prose():
    """The not-finished block grows by one row per blank field."""
    def lines(**over):
        files = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222",
                                     **over})
        return len(files["README.md"].splitlines())
    finished = lines(auth_token="de" * 32)
    one = lines()                                   # the token alone
    two = lines(namespace="")                       # ...and the namespace
    assert one - finished <= 10, "the warning itself is creeping"
    assert two - one == 1, "each further blank field costs one table row"


def test_a_marker_survives_a_profile_round_trip():
    """A marker in profile.json is replayed as the same marker."""
    files = gen.generate(FACTS, {"namespace": "", "ship_id": "bbb222"})
    prof = json.loads(files[bundle_names.PROFILE_FILE])
    assert prof["namespace"] == markers_mod.marker("namespace")
    replayed = gen.generate(FACTS, prof)
    assert yaml.safe_load(
        replayed["bzm_deployment.yaml"])["metadata"]["namespace"] \
        == markers_mod.marker("namespace")


def test_no_limitrange_is_emitted():
    """No bundle emits a LimitRange."""
    files = gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "4",
                                 "engine_mem_limit": "16Gi"})
    assert not any("limitrange" in n.lower() for n in files)
    assert not any(yaml.safe_load(c).get("kind") == "LimitRange"
                   for n, c in files.items() if n.endswith(".yaml"))


def test_profile_json_round_trips_new_options():
    files = gen.generate(FACTS, {"namespace": "ns1", "engine_cpu_limit": "1"})
    prof = json.loads(files[bundle_names.PROFILE_FILE])
    assert prof["engine_cpu_limit"] == "1"
    assert prof["engine_mem_limit"] is None
    assert "emit_limitrange" not in prof
    assert "auth_token" not in prof


def test_mirror_script_with_private_registry():
    files = gen.generate(FACTS, {"namespace": "ns1", "private_registry": "reg.local/bzm"})
    sh = files["bzm-opl-image-mirror.sh"]
    assert sh.startswith("#!/usr/bin/env bash")
    # Every image is pulled amd64 and retagged; asserts the refs, not the lines.
    assert "--platform linux/amd64" in sh
    assert "gcr.io/verdant-bulwark-278/blazemeter/v4:2.4.444-reduced" in sh
    assert "reg.local/bzm/crane:3.7.55" in sh
    assert "service-mock" not in sh  # performance-only by default
    files2 = gen.generate(FACTS, {"namespace": "ns1"})
    assert "bzm-opl-image-mirror.sh" not in files2


def test_a_browser_repo_mirrors_to_exactly_what_the_override_names():
    """A browser image with a nested path mirrors to exactly the reference
    IMAGE_OVERRIDES names."""
    facts = dict(FACTS, func_ids=["performance", "functionalGui"],
                 images=FACTS["images"] + [{
                     "key": "blazemeter/charmander/chrome_136.0.7103.113:2.10.45",
                     "repo": "gcr.io/verdant-bulwark-278/blazemeter/charmander/"
                             "chrome_136.0.7103.113",
                     "tag": "2.10.45", "category": "gui"}])
    files = gen.generate(facts, {"namespace": "ns1", "ship_id": "bbb222",
                                 "private_registry": "reg.corp.com/bzm"})
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    target = json.loads(cm["data"]["IMAGE_OVERRIDES"])[
        "blazemeter/charmander/chrome_136.0.7103.113:2.10.45"]
    assert target == ("reg.corp.com/bzm/blazemeter/charmander/"
                      "chrome_136.0.7103.113:2.10.45")
    assert (f"mirror gcr.io/verdant-bulwark-278/blazemeter/charmander/"
            f"chrome_136.0.7103.113:2.10.45 {target}"
            in files["bzm-opl-image-mirror.sh"])


def test_multi_ship_requires_ship_id():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)
    with pytest.raises(ValueError):
        gen.generate(facts, {"namespace": "ns1"})
    files = gen.generate(facts, {"namespace": "ns1", "ship_id": "explicit"})
    assert yaml.safe_load(files["bzm_configmap.yaml"])["data"]["SHIP_ID"] == "explicit"


# -- service virtualization ------------------------------------------------
# A mockServices location with no ingress answer is refused: it would deploy
# and stall at WAITING_FOR_DOMAIN.

SV_FACTS = dict(FACTS, func_ids=["mockServices"])
SV_OPTS = {"namespace": "ns1", "sv_ingress": "nginx",
           "sv_subdomain": "apps.example.com", "sv_tls_secret": "wildcard-tls"}


def _sv(**kw):
    """SV_OPTS plus overrides, with openshift_cluster set for the openshift
    backend unless the case sets it."""
    if kw.get("sv_ingress") == "openshift":
        kw.setdefault("platform", "openshift")
        kw.setdefault("openshift_cluster", True)
    return dict(SV_OPTS, **kw)


def test_sv_location_without_ingress_refuses():
    with pytest.raises(ValueError, match="WAITING_FOR_DOMAIN") as e:
        gen.generate(SV_FACTS, {"namespace": "ns1"})
    # ...and names the way out, because "not answered" is the only state that
    # blocks and the answer "no" is not obvious from a list of four backends.
    assert f"sv_ingress={service_virt.SV_INGRESS_NONE}" in str(e.value)


def test_sv_location_declining_an_ingress_generates_the_performance_bundle():
    """sv_ingress none on an SV location generates the same files as a
    performance location."""
    declined = gen.generate(SV_FACTS, {"namespace": "ns1",
                                       "sv_ingress": service_virt.SV_INGRESS_NONE})
    data = yaml.safe_load(declined["bzm_configmap.yaml"])["data"]
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in data
    assert "networking.k8s.io" not in _role_groups(declined)
    # No object the SV path adds, either: the same file set a location that
    # never ran mockServices produces.
    assert declined.keys() == gen.generate(FACTS, {"namespace": "ns1"}).keys()
    # The images still follow the location, not the option: what this location
    # runs is a fact about the account, whatever this bundle publishes.
    mirrored = gen.generate(SV_FACTS, {"namespace": "ns1", "private_registry": "reg.local",
                                       "sv_ingress": service_virt.SV_INGRESS_NONE})
    ov = json.loads(yaml.safe_load(
        mirrored["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert "blazemeter/service-mock:latest" in ov


def test_declining_an_ingress_is_recorded_in_the_profile():
    """sv_ingress none is recorded in profile.json."""
    files = gen.generate(SV_FACTS, {"namespace": "ns1",
                                    "sv_ingress": service_virt.SV_INGRESS_NONE})
    assert json.loads(files[bundle_names.PROFILE_FILE])["sv_ingress"] == service_virt.SV_INGRESS_NONE


def test_declining_an_ingress_on_a_location_that_never_asked_is_accepted():
    """sv_ingress none on a location without mockServices is accepted."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "sv_ingress": service_virt.SV_INGRESS_NONE})
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]


def test_retired_sv_bridge_funcid_demands_nothing():
    """The retired sv-bridge funcId needs no ingress options and pulls no
    image."""
    retired = dict(FACTS, func_ids=["performance", "sv-bridge"])
    files = gen.generate(retired, {"namespace": "ns1"})          # no ingress needed
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]
    assert not [i for i in facts_mod.select_images(retired)
                if "sv-bridge" in i["repo"]]


def test_sv_ingress_marks_a_missing_subdomain_and_tls_secret():
    """A missing SV subdomain or TLS secret becomes its marker and is named
    in the README."""
    files = gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx"})
    assert required_fields.placeholder_options(json.loads(files[bundle_names.PROFILE_FILE])) == [
        "sv_subdomain", "sv_tls_secret"]
    readme = files["README.md"]
    assert "sv_subdomain" in readme and "sv_tls_secret" in readme
    # ...and one supplied is one not marked.
    files = gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx",
                                    "sv_subdomain": "apps.example.com"})
    assert required_fields.placeholder_options(
        json.loads(files[bundle_names.PROFILE_FILE])) == ["sv_tls_secret"]


def test_sv_ingress_allows_nodeport_where_it_was_measured_working():
    """nginx and openshift accept NODEPORT, and both settings reach the
    ConfigMap unchanged."""
    for ingress in [i for i, b in service_virt.SV_INGRESS_BACKENDS.items() if b.nodeport_ok]:
        opts = _sv(service_type="NODEPORT", sv_ingress=ingress)
        data = yaml.safe_load(
            gen.generate(SV_FACTS, opts)["bzm_configmap.yaml"])["data"]
        assert data["KUBERNETES_SERVICE_USE_TYPE"] == "NODEPORT"
        assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == ingress.upper()


def test_sv_ingress_refuses_nodeport_where_it_was_measured_broken():
    """Every backend without nodeport_ok refuses NODEPORT."""
    for ingress in [i for i, b in service_virt.SV_INGRESS_BACKENDS.items()
                    if not b.nodeport_ok]:
        with pytest.raises(ValueError, match="requires service_type=CLUSTERIP"):
            gen.generate(SV_FACTS, dict(SV_OPTS, service_type="NODEPORT",
                                        sv_ingress=ingress))


def test_the_nodeport_refusal_gives_the_measured_reason_not_the_disproved_one():
    """The NODEPORT refusal names the port, not a Node read."""
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


def test_sv_readme_names_the_tls_secret_and_the_namespace_it_goes_in():
    """The README names the TLS secret, its namespace and domain, and the
    create command."""
    md = gen.generate(SV_FACTS, dict(SV_OPTS, namespace="bzm-agent",
                                     platform="kubernetes"))["README.md"]
    assert "wildcard-tls" in md
    assert "kubectl -n bzm-agent create secret tls wildcard-tls" in md
    assert "`*.apps.example.com`" in md
    # The CLI follows openshift_cluster (bundle_options.cli), not platform.
    oc = gen.generate(SV_FACTS, dict(SV_OPTS, namespace="bzm-agent",
                                     openshift_cluster=True))["README.md"]
    assert "oc -n bzm-agent create secret tls wildcard-tls" in oc
    # And the failure mode, which is the reason the bullet is worth the space.
    assert "default\n  certificate" in md.split("wildcard-tls")[-1]


@pytest.mark.parametrize("ingress", ["istio", "openshift"])
def test_sv_readme_is_silent_where_the_backend_never_reads_the_secret(ingress):
    """No create-secret instruction for backends that do not read the
    Secret."""
    md = gen.generate(SV_FACTS, _sv(sv_ingress=ingress))["README.md"]
    assert "create secret tls" not in md


def test_sv_contour_configmap_and_httpproxy_rbac():
    files = gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="contour"))
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "CONTOUR"
    assert data["KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME"] == "wildcard-tls"
    # Crane creates only HTTPProxies in that group.
    assert _role_groups(files)["projectcontour.io"] == ["httpproxies"]
    assert "KUBERNETES_ISTIO_GATEWAY_NAME" not in data


@pytest.mark.parametrize("ingress", ["istio", "contour", "openshift"])
def test_sv_ingress_rbac_is_not_granted_to_crd_based_types(ingress):
    """Only nginx is granted ingresses."""
    groups = _role_groups(gen.generate(SV_FACTS, _sv(sv_ingress=ingress)))
    assert "ingresses" not in groups.get("networking.k8s.io", [])


def test_sv_openshift_route_rbac_includes_custom_host():
    """The openshift backend is granted routes and routes/custom-host."""
    files = gen.generate(SV_FACTS, dict(SV_OPTS, platform="openshift",
                                        openshift_cluster=True,
                                        sv_ingress="openshift"))
    groups = _role_groups(files)
    assert groups["route.openshift.io"] == ["routes", "routes/custom-host"]
    assert "networking.k8s.io" not in groups
    data = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert data["KUBERNETES_WEB_EXPOSE_TYPE"] == "OPENSHIFT"


def test_sv_openshift_ingress_requires_the_openshift_platform():
    """The openshift backend is refused unless the platform and cluster are
    OpenShift."""
    with pytest.raises(ValueError, match="requires an OpenShift cluster"):
        gen.generate(SV_FACTS, dict(SV_OPTS, platform="k8s",
                                    sv_ingress="openshift"))
    with pytest.raises(ValueError, match="requires an OpenShift cluster"):
        gen.generate(SV_FACTS, dict(SV_OPTS, openshift_cluster=False,
                                    sv_ingress="openshift"))


def test_sv_istio_gateway_name_is_rejected_for_other_ingress_types():
    with pytest.raises(ValueError, match="sv_istio_gateway"):
        gen.generate(SV_FACTS, dict(SV_OPTS, sv_ingress="contour",
                                    sv_istio_gateway="bzm-gateway"))


def test_legacy_extensions_ingress_grant_removed():
    """The Role grants no ingresses under extensions."""
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
# A Service + Ingress per virtual service whose ports agree, beside crane's own.

SV_MOCK = {"name": "vs1svc2", "port": 8080,
           "harbor": "aaa111", "ship": "bbb222"}
EXPOSE_OPTS = {"namespace": "ns1", "sv_subdomain": "apps.example.com",
               "sv_tls_secret": "wildcard-tls"}


def _expose_docs(mocks, opts):
    """Goes through sv_publish_cfg the way the CLI does, so these exercise the
    resolution as well as the rendering."""
    return [d for d in yaml.safe_load_all(
        service_virt.sv_expose(mocks, opts["namespace"], service_virt.sv_publish_cfg(opts))) if d]


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
    """sv-expose's IngressClass is overridable, defaulting to nginx."""
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
        service_virt.sv_publish_cfg({"namespace": "ns1"})


def test_sv_publish_cfg_keeps_tls_optional_unlike_generate():
    """sv-expose does not require a TLS secret."""
    cfg = service_virt.sv_publish_cfg({"sv_subdomain": "apps.example.com"})
    assert cfg.tls_secret is None
    assert cfg.ingress_class == service_virt.SV_EXPOSE_DEFAULT_INGRESS_CLASS


# --- contributor onboarding: the no-account path ------------------------------

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_missing_facts_file_points_at_the_sample():
    """A missing facts file names the sample and the facts command."""
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
    """The example facts file generates a bundle."""
    f = facts_mod.load(os.path.join(EXAMPLES, "facts.example.json"))
    files = gen.generate(f, {"namespace": "demo"})
    _all_yaml_parse(files)
    cm = yaml.safe_load(files["bzm_configmap.yaml"])
    assert cm["data"]["HARBOR_ID"] == f["harbor_id"]
    assert cm["data"]["SHIP_ID"] == f["ships"][0]["id"]   # single ship, auto


def test_example_facts_have_threads_per_engine():
    """The example facts set threads_per_engine."""
    f = facts_mod.load(os.path.join(EXAMPLES, "facts.example.json"))
    assert f["threads_per_engine"]


def test_endpoint_host_is_built_in_one_place():
    """sv_endpoint_host builds <mock>-<port>-<namespace>.<domain>, and
    sv-expose uses it."""
    host = service_virt.sv_endpoint_host("vs1", 8080, "ns1", "apps.example.com")
    assert host == "vs1-8080-ns1.apps.example.com"
    # No subdomain means there is no host to show yet, not a broken one.
    assert service_virt.sv_endpoint_host("vs1", 8080, "ns1", None) is None
    # ...and sv-expose's Ingress must use exactly that.
    out = service_virt.sv_expose([{"name": "vs1", "port": 8080, "harbor": "h", "ship": "s"}],
                        "ns1", service_virt.SvPublish("apps.example.com", None, "nginx"))
    assert f"host: {host}" in out


def test_sv_on_nodeport_still_needs_no_cluster_rbac():
    """SV with NODEPORT still generates namespaced RBAC only."""
    files = gen.generate(SV_FACTS, dict(SV_OPTS, service_type="NODEPORT"))
    assert not [n for n in files if "clusterrole" in n.lower()]
    # The ingress grant and no `nodes` rule. Flattened, because several rules
    # share the core "" group.
    role = yaml.safe_load(files["bzm_role.yaml"])
    granted = [res for r in role["rules"] for res in r["resources"]]
    assert "ingresses" in granted
    assert "nodes" not in granted


def test_a_written_bundle_carries_an_executable_mirror_script(tmp_path):
    """write() makes the mirror script executable."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "private_registry": "reg.local/bzm"})
    gen.write(files, str(tmp_path))
    script = tmp_path / "bzm-opl-image-mirror.sh"
    assert script.exists() and os.access(script, os.X_OK)


# -- what the bundle says about the location it deploys into -------------------

def _readme(**facts_over):
    return gen.generate({**FACTS, **facts_over}, {"namespace": "ns1"})["README.md"]


def test_the_readme_states_the_location_settings_it_found():
    """The README states the location's slots and threadsPerEngine."""
    r = _readme(slots=4, threads_per_engine=1000)
    assert "4 engine(s) per agent at 1,000 virtual users" in r
    assert "`slots` / `threadsPerEngine`" in r
    # And says which way it multiplies, because that is the half people get
    # wrong: agents x slots, not slots per location.
    assert "times the agents in" in r


def test_the_readme_names_the_403_when_a_figure_is_missing():
    """With slots or threadsPerEngine missing, the README warns of the 403."""
    for over in ({"slots": 1, "threads_per_engine": None},
                 {"slots": None, "threads_per_engine": 500},
                 {"slots": None, "threads_per_engine": None}):
        r = _readme(**over)
        assert "Not enough available resources" in r, over
        assert "Check this location's" in r, over


def test_the_readme_does_not_diagnose_a_location_nobody_asked_about():
    """Manual facts get the same check-your-location prompt, with no claim
    about why."""
    typed = gen.generate(facts_mod.manual("aaa111", "bbb222"),
                         {"namespace": "ns1"})["README.md"]
    assert "Check this location's" in typed
    for claim in ("has no `slots`", "has no `threadsPerEngine`",
                  "typed in", "manual"):
        assert claim not in typed, claim


def _readme_for(func_ids, **facts_over):
    """A README for a location running `func_ids`, with sv_ingress none so
    no ingress option is involved."""
    facts = {**FACTS, "func_ids": func_ids, "slots": 2,
             "threads_per_engine": 500, **facts_over}
    return gen.generate(facts, {"namespace": "ns1",
                                "sv_ingress": "none"})["README.md"]


def test_a_browser_location_is_not_told_it_runs_virtual_users():
    """A GUI location's README speaks in browser instances and states
    threadsPerEngine as the account's own field."""
    r = _readme_for(["functionalGui"])
    assert "virtual users" not in r
    assert "browser instances" in r
    # The account's own field, named as its own field, and never as the sizing
    # figure beside it.
    assert "500" in r and "threadsPerEngine" in r
    assert "500 browser instances" not in r


def test_a_service_virtualization_location_is_not_described_in_engines():
    """An SV location's README has no engine sentences; it states the mock
    pod limits and that requests per second are unmeasured."""
    r = _readme_for(["mockServices"])
    assert "virtual users" not in r
    assert "Each concurrent engine" not in r
    assert "mock pod" in r
    # And it does not invent the figure the table deliberately leaves out.
    assert "requests per second" in r and "has not been measured" in r


def test_funcids_this_tool_does_not_size_get_no_unit_at_all():
    """A funcId with no sizing model gets slots and threadsPerEngine stated
    without a unit."""
    r = _readme_for(["tdm"])
    assert "virtual users" not in r and "browser instances" not in r
    assert "slots" in r and "threadsPerEngine" in r


def test_a_performance_readme_is_unchanged_by_any_of_it():
    """A performance README states engines per agent and virtual users."""
    r = _readme_for(["performance"], slots=4, threads_per_engine=1000)
    assert "4 engine(s) per agent at 1,000 virtual users each" in r
    assert "Each concurrent engine needs" in r


def test_the_readme_reads_its_units_off_the_sizing_table():
    """Every sizing model's unit appears in its README."""
    import bzm_opl_gen.plan as plan_mod
    for fid, m in plan_mod.SIZING_MODELS.items():
        r = _readme_for([fid])
        assert m["unit"] in r, fid


def test_generate_never_asks_how_the_facts_arrived():
    """No module generate() loads reads the manual-entry marker (checked
    over the parsed source)."""
    import ast
    import importlib
    import subprocess
    code = ("import sys, bzm_opl_gen.generate\n"
            "print(' '.join(m for m in sys.modules "
            "if m.startswith('bzm_opl_gen.')))")
    loaded = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True,
        text=True, cwd=os.path.join(os.path.dirname(__file__), "..")
    ).stdout.split()
    walked = sorted(set(loaded) - {"bzm_opl_gen.facts", "bzm_opl_gen.api"})
    assert {"bzm_opl_gen.render_manifests", "bzm_opl_gen.render_helm",
            "bzm_opl_gen.render_docker", "bzm_opl_gen.readme_parts"} <= set(walked)
    banned = {"from_manual_entry", "MANUAL_SOURCE"}
    for name in walked:
        path = importlib.import_module(name).__file__
        tree = ast.parse(pathlib.Path(path).read_text())
        read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        read |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert not (read & banned), (
            f"{name} reads {sorted(read & banned)} -- the manifests are "
            f"identical however the facts arrived, and that is the property "
            f"facts.manual() exists to preserve. The marker is doctor's to read.")


# -- the docker format --------------------------------------------------------
# One agent, one container, in the shape of BlazeMeter's Docker Command.

DOCKER = {"output_format": "docker", "ship_id": "bbb222",
          "auth_token": "de" * 32}


def docker_sh(**opts):
    return gen.generate(FACTS, {**DOCKER, **opts})["bzm-opl-agent.sh"]


def docker_compose(**opts):
    return gen.generate(FACTS, {**DOCKER, **opts})[bundle_names.DOCKER_COMPOSE_FILE]


def test_docker_command_is_the_documented_shape():
    """The docker command keeps the shape of BlazeMeter's Docker Command."""
    sh = docker_sh(use_secret=False)
    assert "docker run -d" in sh
    for flag in ("--restart on-failure", "-u 0",
                 "-v /var/run/docker.sock:/var/run/docker.sock",
                 "-v /tmp:/tmp", "-w /usr/src/app/", "--net=host",
                 "python agent/agent.py"):
        assert flag in sh, flag
    # The identity, and the container named as BlazeMeter names it.
    assert "--env HARBOR_ID=aaa111" in sh
    assert "--env SHIP_ID=bbb222" in sh
    # Quoted at the assignment, because a ship id may be a marker and
    # `NAME=bzm-crane-<SHIP_ID>` unquoted is a redirection -- see _docker_run_sh.
    assert 'NAME="bzm-crane-bbb222"' in sh
    # Which manager this agent is for, stated rather than defaulted -- the
    # Kubernetes ConfigMap states its own the same way.
    assert "--env CONTAINER_MANAGER_TYPE=DOCKER" in sh
    # And it is the crane image the account reports, not a guess.
    assert FACTS["crane_image"] in sh


def test_docker_runs_as_root_so_it_can_open_the_socket():
    """The container runs with -u 0 in every branch, so it can open the
    docker socket."""
    for opts in ({}, {"use_secret": False}, {"proxy": {"http": "http://p:3128"}},
                 {"ca_bundle": "-----BEGIN CERTIFICATE-----"},
                 {"private_registry": "reg.corp/bzm"}):
        assert "-u 0" in docker_sh(**opts), opts


def test_docker_hands_its_engines_a_port_range():
    """DOCKER_PORT_RANGE is set on the command line."""
    for sh in (docker_sh(), docker_sh(use_secret=False)):
        assert f"DOCKER_PORT_RANGE={render_docker.DOCKER_PORT_RANGE}" in sh
    # In the command, not the env file: it is configuration, not a credential.
    bundle = gen.generate(FACTS, DOCKER)
    assert "DOCKER_PORT_RANGE" not in bundle[bundle_names.DOCKER_ENV_FILE]


def test_docker_scripts_are_valid_shell():
    """Every branch of the generated script passes sh -n."""
    import itertools
    import subprocess
    for secret, ca, proxy, reg, blank in itertools.product([True, False], repeat=5):
        o = dict(DOCKER, use_secret=secret)
        if blank:
            # A blank field adds a quoted refusal per variable.
            o["auth_token"] = markers_mod.marker("auth_token")
        if ca:
            o["ca_bundle"] = "-----BEGIN CERTIFICATE-----\nx\n"
        if proxy:
            # An apostrophe and a space in the password: the case BlazeMeter's
            # proxy page warns about, and the one that breaks a bare --env.
            o["proxy"] = {"http": "http://p:1", "username": "o'brien",
                          "password": "a b"}
        if reg:
            o["private_registry"] = "reg.example.com/bzm"
        sh = gen.generate(FACTS, o)["bzm-opl-agent.sh"]
        r = subprocess.run(["sh", "-n", "-"], input=sh, text=True,
                           capture_output=True)
        assert r.returncode == 0, (secret, ca, proxy, reg, blank, r.stderr)
    # ...and the mounted-file branches, including a blank file's guard.
    for extra in ({"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT,
                   "sv_tls_key": SV_KEY},
                  {"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT,
                   "sv_tls_key": ""},
                  {"sv_hostname": SV_HOST, "sv_tls_key": SV_KEY},
                  {"ca_bundle": markers_mod.marker("ca_bundle")}):
        sh = gen.generate(FACTS, {**DOCKER, **extra})["bzm-opl-agent.sh"]
        r = subprocess.run(["sh", "-n", "-"], input=sh, text=True,
                           capture_output=True)
        assert r.returncode == 0, (extra, r.stderr)
    # ...and blank ids, whose marker reaches the NAME= assignment.
    import bzm_opl_gen.facts as facts_mod
    sh = gen.generate(facts_mod.manual("", ""),
                      {**DOCKER, "ship_id": ""})["bzm-opl-agent.sh"]
    assert markers_mod.marker("ship_id") in sh, "the case did not arise"
    r = subprocess.run(["sh", "-n", "-"], input=sh, text=True,
                       capture_output=True)
    assert r.returncode == 0, r.stderr


def _run_bundle(tmp_path, files=None, env=None):
    """Write a docker bundle and run its script against a stub docker; with
    no files, re-run what is in the directory. `env` sets the script's
    override variables."""
    import subprocess
    for name, text in (files or {}).items():
        (tmp_path / name).write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "docker-calls"
    (bin_dir / "docker").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$1" >> "{calls}"\nexit 0\n')
    (bin_dir / "docker").chmod(0o755)
    r = subprocess.run(["sh", "bzm-opl-agent.sh"], cwd=tmp_path, text=True,
                       capture_output=True,
                       env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                            **(env or {})})
    made = calls.read_text().split() if calls.exists() else []
    return r, made


def test_docker_script_refuses_a_placeholder_before_starting_anything(tmp_path):
    """A marked AUTH_TOKEN in the env file stops the script before docker
    run, naming the file to fix."""
    files = gen.generate(FACTS, {**DOCKER, "auth_token": ""})
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 1
    assert "AUTH_TOKEN carries <AUTH_TOKEN>" in r.stderr
    # ...and the file to edit, which is the half a refusal without it leaves the
    # reader to guess -- the credential is not in the script.
    assert "Set it in bzm-opl-agent.env" in r.stderr
    assert made == ["ps"], made           # nothing was started

    # Filled in, the same bundle runs: the check reads the files as they stand.
    (tmp_path / "bzm-opl-agent.env").write_text("AUTH_TOKEN=" + "ab" * 32 + "\n")
    r, made = _run_bundle(tmp_path)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "ps", "run"], made


def test_docker_script_refuses_an_inline_placeholder_too(tmp_path):
    """With use_secret off, a marked AUTH_TOKEN on the run line stops the
    script."""
    files = gen.generate(FACTS, {**DOCKER, "use_secret": False,
                                 "auth_token": "",
                                 "private_registry":
                                     markers_mod.marker("private_registry")})
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 1
    assert "AUTH_TOKEN carries <AUTH_TOKEN>" in r.stderr
    # Both files, because inline means the value is in both of the two that
    # start this container and naming one sends somebody to fix half of it.
    assert "Set it in bzm-opl-agent.sh and compose.yaml" in r.stderr
    assert made == ["ps"], made
    # The crane image carries it too and is deliberately not checked: a
    # reference with `<` in it is refused by docker itself, from either route.
    assert "<PRIVATE_REGISTRY>/crane" in files["bzm-opl-agent.sh"]


def test_docker_script_refuses_an_identity_nobody_supplied(tmp_path):
    """Blank ids stop the script with a message naming them, not a shell
    syntax error."""
    import bzm_opl_gen.facts as facts_mod
    files = gen.generate(facts_mod.manual("", ""), {**DOCKER, "ship_id": ""})
    r, made = _run_bundle(tmp_path, files)
    assert "syntax error" not in r.stderr, r.stderr
    assert r.returncode == 1
    assert "HARBOR_ID carries <HARBOR_ID>" in r.stderr
    assert "Set it in bzm-opl-agent.sh and compose.yaml" in r.stderr
    assert made == ["ps"], made           # nothing was started


def test_a_finished_docker_bundle_carries_no_refusal(tmp_path):
    """A finished docker bundle carries no marker checks."""
    files = gen.generate(FACTS, DOCKER)
    assert not markers_mod.MARKER_RE.search(files["bzm-opl-agent.sh"])
    assert not markers_mod.MARKER_RE.search(files[bundle_names.DOCKER_COMPOSE_FILE])
    assert "BZM_OPL_UNSET" not in files[bundle_names.DOCKER_ENV_FILE]
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "run"], made


def test_docker_use_secret_keeps_the_token_out_of_the_process_list():
    """With use_secret the token is in the env file, not on the command
    line."""
    files = gen.generate(FACTS, DOCKER)
    sh, env = files["bzm-opl-agent.sh"], files["bzm-opl-agent.env"]
    # The value, not the name, which the script's message mentions.
    assert "de" * 32 not in sh
    assert '--env-file "$ENV_FILE"' in sh
    assert "AUTH_TOKEN=" + "de" * 32 in env
    # Off, it is inline and there is no second file -- BlazeMeter's own shape.
    plain = gen.generate(FACTS, {**DOCKER, "use_secret": False})
    assert "bzm-opl-agent.env" not in plain
    assert "--env AUTH_TOKEN=" + "de" * 32 in plain["bzm-opl-agent.sh"]


def test_docker_proxy_credentials_move_with_the_token():
    """Proxy URLs with credentials go to the env file too."""
    o = dict(DOCKER, proxy={"http": "http://p:1", "https": "http://p:1",
                            "username": "u", "password": "pw"})
    files = gen.generate(FACTS, o)
    assert "HTTP_PROXY" not in files["bzm-opl-agent.sh"]
    assert "HTTP_PROXY=http://u:pw@p:1" in files["bzm-opl-agent.env"]
    # NO_PROXY stays in the command, with docker's default.
    assert "kubernetes.default" not in files["bzm-opl-agent.sh"]
    assert "127.0.0.1,localhost" in files["bzm-opl-agent.sh"]


def test_docker_ca_bundle_is_mounted_where_the_variables_point():
    """The CA bundle is mounted at the path REQUESTS_CA_BUNDLE and
    AWS_CA_BUNDLE name."""
    files = gen.generate(FACTS, {**DOCKER, "ca_bundle": "PEM\n"})
    sh = files["bzm-opl-agent.sh"]
    assert files["ca-bundle.crt"] == "PEM\n"
    assert '-v "$CA_BUNDLE":/etc/ssl/certs/ca-certificates.crt:ro' in sh
    assert "--env REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in sh
    assert "--env AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in sh


def test_docker_auto_update_is_the_docker_variable_and_off_unless_asked_for():
    """Docker writes AUTO_UPDATE, false unless asked for."""
    assert "--env AUTO_UPDATE=false" in docker_sh()
    assert "--env AUTO_UPDATE=true" in docker_sh(auto_update=True)
    assert "--env AUTO_UPDATE=false" in docker_sh(auto_update=False)
    assert "AUTO_KUBERNETES_UPDATE" not in docker_sh(auto_update=True)


# -- ...and the same container as a compose project ---------------------------

# Every branch of the compose file that is conditional, so the parse and the
# escape rules below are walked over all of them rather than over the default.
COMPOSE_CASES = [
    {},
    {"use_secret": False},
    {"ca_bundle": "-----BEGIN CERTIFICATE-----\nx\n"},
    {"private_registry": "reg.example.com/bzm", "auto_update": True},
    {"proxy": {"http": "http://p:1", "https": "http://p:1",
               "username": "o'brien", "password": "a b"}},
    {"extra_env": {"PREFERRED_INTERFACE": "eth1"}},
    # A blank field in each of the two files it can land in.
    {"auth_token": markers_mod.marker("auth_token")},
    {"auth_token": markers_mod.marker("auth_token"), "use_secret": False},
    # Service virtualization: two more mounted files and three more variables.
    {"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY},
    # ...and the hostname alone, which is a real configuration (the endpoints
    # are plain HTTP) and writes the variable without either mount.
    {"sv_hostname": SV_HOST},
    # A blank hostname beside a certificate: an inline guard other than the token.
    {"sv_hostname": "", "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY},
    # ...and a blank half of the TLS pair: a guard over a mounted file.
    {"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT, "sv_tls_key": ""},
]


def test_the_docker_bundle_carries_both_routes_to_one_container():
    """The docker bundle carries the script and compose.yaml, script first."""
    files = gen.generate(FACTS, DOCKER)
    assert bundle_names.DOCKER_RUN_FILE in files and bundle_names.DOCKER_COMPOSE_FILE in files
    # The script leads everywhere it is listed -- BlazeMeter's own shape is the
    # one their documentation describes, which is the tie-break this repo uses.
    order = gen.preview_order(list(files))
    assert order.index(bundle_names.DOCKER_RUN_FILE) < order.index(bundle_names.DOCKER_COMPOSE_FILE)


def test_compose_is_valid_yaml_in_every_branch():
    """compose.yaml parses in every branch."""
    for extra in COMPOSE_CASES:
        doc = yaml.safe_load(docker_compose(**extra))
        svc = doc["services"][bundle_names.DOCKER_COMPOSE_SERVICE]
        assert svc["image"] == FACTS["crane_image"] or extra.get("private_registry")
        assert svc["environment"]["HARBOR_ID"] == "aaa111"


def test_compose_is_v2_and_names_its_own_project():
    """compose.yaml has no version key and names its project after the
    agent."""
    text = docker_compose()
    assert "version:" not in text
    doc = yaml.safe_load(text)
    assert doc["name"] == bundle_names.docker_container_name("bbb222")
    assert set(doc["services"]) == {bundle_names.DOCKER_COMPOSE_SERVICE}


def test_both_routes_carry_the_same_container_name():
    """The script and compose use the same container name, so only one can
    run."""
    name = bundle_names.docker_container_name("bbb222")
    files = gen.generate(FACTS, DOCKER)
    svc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])["services"]
    assert svc[bundle_names.DOCKER_COMPOSE_SERVICE]["container_name"] == name
    assert f'NAME="{name}"' in files[bundle_names.DOCKER_RUN_FILE]


def test_compose_reads_the_credential_file_the_script_does():
    """compose reads the same env file for the credential."""
    files = gen.generate(FACTS, DOCKER)
    svc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["env_file"] == [f"./{bundle_names.DOCKER_ENV_FILE}"]
    # The value, like the script: with use_secret on it is in neither file.
    assert "de" * 32 not in files[bundle_names.DOCKER_COMPOSE_FILE]
    assert "AUTH_TOKEN" not in svc["environment"]
    # Off, there is no env file to point at and the token is inline in both --
    # BlazeMeter's own shape, and the same shape twice.
    plain = gen.generate(FACTS, {**DOCKER, "use_secret": False})
    svc = yaml.safe_load(plain[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert "env_file" not in svc
    assert svc["environment"]["AUTH_TOKEN"] == "de" * 32


def test_no_docker_bundle_holds_a_file_called_dot_env():
    """No docker bundle contains a .env file, and compose.yaml warns against
    one."""
    for extra in COMPOSE_CASES:
        files = gen.generate(FACTS, {**DOCKER, **extra})
        assert ".env" not in files
        assert not [f for f in files if f.endswith("/.env")]
        # ...and compose.yaml warns against one, in both branches.
        assert "`.env`" in files[bundle_names.DOCKER_COMPOSE_FILE]


def test_compose_escapes_a_dollar_so_it_reaches_the_container():
    """A literal $ is doubled in compose.yaml so it reaches the container
    unchanged."""
    text = docker_compose(extra_env={"PREFERRED_INTERFACE": "a$b${HOME}c"})
    assert 'PREFERRED_INTERFACE: "a$$b$${HOME}c"' in text
    # ...and it is still one value to whatever reads the YAML.
    svc = yaml.safe_load(text)["services"]["crane"]
    assert svc["environment"]["PREFERRED_INTERFACE"] == "a$$b$${HOME}c"


def test_compose_restates_the_fixed_half_of_the_command():
    """compose.yaml's user, network, restart, mounts, workdir and command
    come from the shared constants."""
    svc = yaml.safe_load(docker_compose())["services"]["crane"]
    assert svc["user"] == render_docker.DOCKER_USER
    assert svc["restart"] == render_docker.DOCKER_RESTART
    assert svc["network_mode"] == render_docker.DOCKER_NETWORK
    assert svc["working_dir"] == render_docker.DOCKER_WORKDIR
    assert svc["command"] == render_docker.DOCKER_ENTRYPOINT
    assert svc["volumes"] == render_docker.DOCKER_MOUNTS
    # The CA mount keeps the script's CA_BUNDLE override.
    with_ca = yaml.safe_load(docker_compose(ca_bundle="PEM\n"))["services"]["crane"]
    assert with_ca["volumes"][-1] == (
        f"${{CA_BUNDLE:-./{bundle_names.DOCKER_CA_FILE}}}:{render_docker.DOCKER_CA_PATH}:ro")


def test_compose_refuses_a_placeholder_in_the_same_words_as_the_script():
    """A blank credential is guarded with compose's ${X:?message}, worded as
    the script words it."""
    wrong, todo = render_docker._docker_blank_lines("AUTH_TOKEN", bundle_names.DOCKER_ENV_FILE,
                                          markers_mod.marker("auth_token"))
    files = gen.generate(FACTS, {**DOCKER, "auth_token": ""})
    # The guard sits in the env file both routes read.
    env = files[bundle_names.DOCKER_ENV_FILE]
    assert env.startswith("# Read by docker --env-file")
    assert f"AUTH_TOKEN=${{BZM_OPL_UNSET_AUTH_TOKEN:?{wrong} {todo}}}" in env
    for line in (wrong, todo):
        assert line in files[bundle_names.DOCKER_RUN_FILE]
    # compose.yaml itself carries no guard for a value in the env file.
    assert "AUTH_TOKEN" not in yaml.safe_load(
        files[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]["environment"]


def test_compose_refuses_an_inline_placeholder_at_the_value_itself():
    """Inline blank values are guarded in compose.yaml and checked in the
    script."""
    where = f"{bundle_names.DOCKER_RUN_FILE} and {bundle_names.DOCKER_COMPOSE_FILE}"
    wrong, todo = render_docker._docker_blank_lines("AUTH_TOKEN", where,
                                          markers_mod.marker("auth_token"))
    files = gen.generate(FACTS, {**DOCKER, "auth_token": "", "use_secret": False})
    svc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["environment"]["AUTH_TOKEN"] == (
        f"${{BZM_OPL_UNSET_AUTH_TOKEN:?{wrong} {todo}}}")
    for line in (wrong, todo):
        assert line in files[bundle_names.DOCKER_RUN_FILE]
    # The guard's variable is one no host sets, so the ambient environment
    # cannot satisfy it.
    proxy = gen.generate(FACTS, {**DOCKER, "use_secret": False,
                                 "proxy": {"http": markers_mod.marker("proxy.http")}})
    svc = yaml.safe_load(proxy[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["environment"]["HTTP_PROXY"].startswith("${BZM_OPL_UNSET_HTTP_PROXY:?")


def test_docker_refuses_an_identity_nobody_supplied():
    """Blank HARBOR_ID and SHIP_ID are guarded in both routes."""
    import bzm_opl_gen.facts as facts_mod
    # Drop ship_id from the fixture, since the option outranks the facts.
    files = gen.generate(facts_mod.manual("", ""),
                         {**DOCKER, "ship_id": ""})
    svc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    for name, key in (("HARBOR_ID", "harbor_id"), ("SHIP_ID", "ship_id")):
        wrong, todo = render_docker._docker_blank_lines(
            name, f"{bundle_names.DOCKER_RUN_FILE} and {bundle_names.DOCKER_COMPOSE_FILE}",
            markers_mod.marker(key))
        assert svc["environment"][name] == (
            f"${{BZM_OPL_UNSET_{name}:?{wrong} {todo}}}")
        for line in (wrong, todo):
            assert line in files[bundle_names.DOCKER_RUN_FILE]
    # ...and the container name carries it too, which is what makes the two
    # routes exclusive whether the bundle is finished or not.
    assert svc["container_name"] == bundle_names.docker_container_name(
        markers_mod.marker("ship_id"))


# -- ...and the two files are held equal, over the whole option matrix --------
# Both files are parsed and compared field by field. CI separately runs
# `docker compose config -q` over generated bundles.

# The keys a compose service may have, compared as a set so an unexpected key
# fails. env_file depends on use_secret.
COMPOSE_SERVICE_KEYS = {"image", "container_name", "user", "restart",
                        "network_mode", "working_dir", "environment",
                        "volumes", "command"}
# Compose's own required-variable expression, which is how a blank value is
# written in the file that has no shell to check it in (see _compose_required).
COMPOSE_GUARD = "${BZM_OPL_UNSET_"


def _blank_mount_vars(sh):
    """The mounted files the script refuses as unfinished, by variable."""
    return set(re.findall(
        r"^if grep -q '" + re.escape(markers_mod.MARKER_PATTERN)
        + r"' \"\$([A-Z][A-Z0-9_]*)\"; then$", sh, re.M))


def _blank_mount(var):
    """A common representation of a mount left blank, for both parsers."""
    return f"<blank:{var}>"


def _env_file_env(files):
    """The env file as {name: value}."""
    text = files.get(bundle_names.DOCKER_ENV_FILE)
    return dict(line.split("=", 1) for line in (text or "").splitlines()
                if line and not line.startswith("#"))


def _script_paths(sh):
    """The sibling files the script names, as $VAR -> bundle-relative path."""
    out = {}
    # Every variable the script assigns at the top; each mount adds one.
    for name, raw in re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", sh, re.M):
        value = raw.strip('"')
        default = re.fullmatch(r"\$\{" + name + r":-(.*)\}", value)
        out["$" + name] = (default.group(1) if default else value).replace("$DIR/", "")
    return out


def _container_from_script(files):
    """The container bzm-opl-agent.sh starts, as fields, parsed from the
    file. An unknown argument lands in `image`, so it fails loudly."""
    sh = files[bundle_names.DOCKER_RUN_FILE]
    lines = sh.splitlines()
    i = lines.index("docker run -d \\")
    text = ""
    while True:
        text += lines[i].rstrip("\\") + " "
        if not lines[i].endswith("\\"):
            break
        i += 1
    paths = _script_paths(sh)
    # A mount the script refuses as unfinished resolves to the refusal rather
    # than to the file, so it lines up with compose's own way of saying it.
    for var in _blank_mount_vars(sh):
        paths["$" + var] = _blank_mount(var)
    words = []
    for word in shlex.split(text):          # _sh_value's quoting, undone
        for var, value in paths.items():
            word = word.replace(var, value)
        words.append(word)

    c = {"inline": {}, "mounts": [], "env_files": []}
    rest = iter(words[2:])                  # past `docker run`
    image_on = []
    for word in rest:
        if word == "-d":
            continue
        elif word == "--name":
            c["name"] = next(rest)
        elif word == "--restart":
            c["restart"] = next(rest)
        elif word == "-u":
            c["user"] = next(rest)
        elif word == "--env-file":
            c["env_files"].append(next(rest))
        elif word == "--env":
            k, _, v = next(rest).partition("=")
            c["inline"][k] = v
        elif word == "-v":
            c["mounts"].append(next(rest))
        elif word == "-w":
            c["workdir"] = next(rest)
        elif word.startswith("--net="):
            c["network"] = word.split("=", 1)[1]
        else:
            image_on = [word] + list(rest)
    c["image"] = image_on[0]
    c["command"] = " ".join(image_on[1:])
    c["env"] = {**_env_file_env(files), **c["inline"]}
    return c


def _container_from_compose(files):
    """The container compose.yaml describes, with $$ undone and relative
    paths resolved as the script resolves them."""
    doc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])
    assert set(doc) == {"name", "services"}, sorted(doc)
    svc = doc["services"][bundle_names.DOCKER_COMPOSE_SERVICE]
    assert set(svc) - {"env_file"} == COMPOSE_SERVICE_KEYS, (
        f"compose says {sorted(set(svc) - COMPOSE_SERVICE_KEYS - {'env_file'})} "
        f"about this container and nothing holds it against {bundle_names.DOCKER_RUN_FILE}")
    inline = {k: v if v.startswith(COMPOSE_GUARD) else v.replace("$$", "$")
              for k, v in svc["environment"].items()}
    c = {"name": svc["container_name"], "image": svc["image"],
         "user": svc["user"], "restart": svc["restart"],
         "network": svc["network_mode"], "workdir": svc["working_dir"],
         "command": svc["command"],
         "env_files": [re.sub(r"^\./", "", f) for f in svc.get("env_file", [])],
         # `${VAR:-./file}` and `${VAR:?sentence}` are compose's forms of the
         # script's override and marker check, reduced to the same values.
         "mounts": [re.sub(r"^\$\{([A-Z][A-Z0-9_]*):\?[^}]*\}",
                           lambda g: _blank_mount(g.group(1)),
                           re.sub(r"^\$\{[A-Z][A-Z0-9_]*:-\./(.*?)\}", r"\1", m))
                    for m in svc["volumes"]],
         "inline": inline}
    c["env"] = {**_env_file_env(files), **inline}
    return c


def _container_diffs(files):
    """Every way the bundle's two files describe different containers."""
    run, comp = _container_from_script(files), _container_from_compose(files)
    diffs = [f"{f}: {bundle_names.DOCKER_RUN_FILE}={run.get(f)!r} "
             f"{bundle_names.DOCKER_COMPOSE_FILE}={comp.get(f)!r}"
             for f in ("image", "name", "user", "restart", "network", "workdir",
                       "command", "mounts", "env_files")
             if run.get(f) != comp.get(f)]
    for k in sorted(set(run["env"]) | set(comp["env"])):
        rv, cv = run["env"].get(k), comp["env"].get(k)
        # The one allowed difference, checked both ways: a blank value is the
        # marker in the script and a `${...:?}` guard in compose.
        blank_here = k in comp["inline"] and str(cv).startswith(COMPOSE_GUARD)
        blank_there = k in run["inline"] and markers_mod.marker_in(rv) is not None
        if blank_here or blank_there:
            if not (blank_here and blank_there):
                diffs.append(
                    f"env {k}: left blank, and only "
                    f"{bundle_names.DOCKER_COMPOSE_FILE if blank_here else bundle_names.DOCKER_RUN_FILE}"
                    f" refuses it")
        elif rv != cv:
            diffs.append(f"env {k}: {bundle_names.DOCKER_RUN_FILE}={rv!r} "
                         f"{bundle_names.DOCKER_COMPOSE_FILE}={cv!r}")
    return diffs


def test_compose_and_docker_run_describe_the_same_container():
    """The script and compose.yaml describe the same container over
    helm_parity's CASES plus COMPOSE_CASES."""
    from helm_parity import CASES, COMMON

    cases = [(f"helm:{n}", e) for n, e in CASES.items()]
    cases += [(f"compose:{i}", e) for i, e in enumerate(COMPOSE_CASES)]
    failures = []
    for name, extra in cases:
        files = gen.generate(FACTS, {**DOCKER, **COMMON, **extra})
        failures += [f"{name}: {d}" for d in _container_diffs(files)]
    assert not failures, "\n".join(
        ["the two files in the docker bundle describe different containers:"]
        + failures)


def test_the_parity_check_reads_both_files_rather_than_agreeing_vacuously():
    """The parity parsers return the actual constants, so the comparison is
    not vacuous."""
    files = gen.generate(FACTS, {**DOCKER, "ca_bundle": "PEM\n",
                                 "proxy": {"http": "http://p:1"}})
    run = _container_from_script(files)
    assert run["image"] == FACTS["crane_image"]
    assert run["name"] == bundle_names.docker_container_name("bbb222")
    assert run["user"] == render_docker.DOCKER_USER
    assert run["restart"] == render_docker.DOCKER_RESTART
    assert run["network"] == render_docker.DOCKER_NETWORK
    assert run["workdir"] == render_docker.DOCKER_WORKDIR
    assert run["command"] == render_docker.DOCKER_ENTRYPOINT
    assert run["mounts"] == render_docker.DOCKER_MOUNTS + [
        f"{bundle_names.DOCKER_CA_FILE}:{render_docker.DOCKER_CA_PATH}:ro"]
    assert run["env_files"] == [bundle_names.DOCKER_ENV_FILE]
    # The credential is in the file both routes read and in neither inline set,
    # and the environment the container ends up with is the union.
    assert "AUTH_TOKEN" not in run["inline"]
    assert run["env"]["AUTH_TOKEN"] == "de" * 32
    assert run["env"]["HTTP_PROXY"] == "http://p:1"
    assert _container_from_compose(files)["env"] == run["env"]


def test_docker_readme_offers_both_routes_and_says_to_pick_one():
    """The docker README offers the script, then compose, and says to use
    one."""
    readme = gen.generate(FACTS, DOCKER)["README.md"]
    run = readme.split("## Run it")[1].split("##")[0]
    assert run.index(f"./{bundle_names.DOCKER_RUN_FILE}") < run.index("docker compose up -d")
    assert "not both" in run
    assert "docker compose version" in run          # the version requirement
    # ...and the file people would tidy into a `.env` is named where it exists.
    assert "`.env`" in gen.generate(FACTS, DOCKER)["README.md"]


def test_docker_readme_names_what_it_could_not_carry():
    """The docker README lists options set away from their default that it
    cannot carry."""
    readme = gen.generate(FACTS, {**DOCKER, "namespace": "other",
                                  "node_selector": {"a": "b"}})["README.md"]
    assert "## Set here, but not carried" in readme
    assert "`namespace`" in readme
    assert "`node_selector`" in readme
    # ...and a bundle that asked for none of them says nothing about them.
    assert "Set here, but not carried" not in gen.generate(FACTS, DOCKER)["README.md"]


def test_docker_names_the_two_options_that_used_to_go_quiet():
    """crane_hook and registry_auth are listed as not carried, and nothing
    is emitted for them."""
    readme = gen.generate(FACTS, {**DOCKER, "crane_hook": True,
                                  "private_registry": "reg.corp/bzm",
                                  "registry_auth": True})["README.md"]
    assert "`crane_hook`" in readme
    assert "`registry_auth`" in readme
    bundle = gen.generate(FACTS, {**DOCKER, "crane_hook": True})
    assert not [f for f in bundle if "cranehook" in f]


# The smallest options each format generates from, keyed by OUTPUT_FORMATS.
FORMAT_BASE = {
    "manifests": {"namespace": "ns1", "ship_id": "bbb222",
                  "auth_token": "de" * 32},
    "helm": {"output_format": "helm", "namespace": "ns1", "ship_id": "bbb222",
             "auth_token": "de" * 32},
    "docker": DOCKER,
}


def test_every_format_has_an_ignored_entry():
    """IGNORED_BY_FORMAT has an entry for every format."""
    assert set(bundle_options.IGNORED_BY_FORMAT) == set(bundle_options.OUTPUT_FORMATS)
    assert set(FORMAT_BASE) == set(bundle_options.OUTPUT_FORMATS)
    # Named, so an emptied table fails here. The cluster formats ignore the
    # docker SV options.
    assert bundle_options.IGNORED_BY_FORMAT["docker"]["namespace"]
    assert bundle_options.IGNORED_BY_FORMAT["manifests"]["sv_hostname"]


def test_a_format_never_refuses_what_it_says_it_ignores():
    """No format refuses a value for an option it ignores, and its README
    names each one."""
    for fmt, ignored in bundle_options.IGNORED_BY_FORMAT.items():
        junk = {k: "nonsense" for k in ignored}
        out = gen.generate(FACTS, {**FORMAT_BASE[fmt], **junk})
        for key in ignored:
            assert f"`{key}`" in out["README.md"], \
                f"{fmt}: {key} carried silently"
    # ...and the CA pair that is reachable by clicking: the inline PEM wins and
    # the ConfigMap it was switched away from is ignored, not a second mode.
    both = gen.generate(FACTS, {**DOCKER, "ca_existing_configmap": "corp-trust",
                                "ca_bundle": "-----BEGIN CERTIFICATE-----"})
    assert both[bundle_names.DOCKER_CA_FILE] == "-----BEGIN CERTIFICATE-----"


@pytest.mark.parametrize("fmt", sorted(bundle_options.IGNORED_BY_FORMAT))
def test_a_format_never_lets_an_ignored_option_reach_a_generated_file(fmt):
    """An ignored option changes no emitted file other than README.md and
    profile.json."""
    named_by_design = {"README.md", bundle_names.PROFILE_FILE}
    base = {**FORMAT_BASE[fmt], "private_registry": "reg.corp/bzm"}
    plain = gen.generate(FACTS, base)
    for key in bundle_options.IGNORED_BY_FORMAT[fmt]:
        out = gen.generate(FACTS, {**base, key: "nonsense"})
        for name in sorted(set(plain) | set(out)):
            if name in named_by_design:
                continue
            assert out.get(name) == plain.get(name), \
                f"{fmt}: {key} is ignored, and it reached {name}"


def test_the_other_formats_still_refuse_all_of_it():
    """The cluster formats still refuse the values docker ignores."""
    k8s = {"ship_id": "bbb222", "auth_token": "de" * 32}
    # A malformed value and two CA modes are refused, not marked.
    for over in ({"engine_cpu_limit": "not-a-cpu"},
                 {"engine_mem_limit": "not-a-memory"},
                 {"ca_existing_configmap": "cm", "ca_bundle": "PEM"}):
        with pytest.raises(ValueError):
            gen.generate(FACTS, {**k8s, **over})


def test_docker_reports_the_engine_size_it_actually_carries():
    """The docker README states the default engine size and lists the
    ignored limits."""
    readme = gen.generate(FACTS, {**DOCKER, "engine_cpu_limit": "4",
                                  "engine_mem_limit": "16Gi"})["README.md"]
    assert "4 CPU + 16GiB RAM" not in readme
    assert "`engine_cpu_limit`" in readme      # named as not carried, though


def test_docker_readme_does_not_advertise_kubernetes_answers():
    """The docker README's table has no namespace or platform row."""
    readme = gen.generate(FACTS, {**DOCKER, "namespace": "other"})["README.md"]
    head = readme.split("## Run it")[0]
    assert "Namespace" not in head
    assert "Platform" not in head
    assert "bzm-crane-bbb222" in head


def test_docker_no_longer_refuses_a_service_virtualization_location():
    """Docker generates for an SV location configured for Kubernetes,
    listing the sv_* options as not carried."""
    sv_facts = {**FACTS, "func_ids": ["performance", "mockServices"]}
    files = gen.generate(sv_facts, {**DOCKER, "sv_ingress": "nginx",
                                    "sv_subdomain": "apps.example.com",
                                    "sv_tls_secret": "wild"})
    assert "`sv_ingress`" in files["README.md"]
    assert "KUBERNETES_WEB_EXPOSE" not in files[bundle_names.DOCKER_RUN_FILE]
    # ...and a mockServices location with nothing configured is not refused.
    gen.generate(sv_facts, DOCKER)


SV_DOCKER = {**DOCKER, "sv_hostname": SV_HOST,
             "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY}


def test_a_docker_agent_publishes_virtual_services_under_its_own_three():
    """Docker SV writes the PEMs as files, mounts them, and sets
    HOSTNAME_OVERRIDE, TLS_CERT and TLS_KEY."""
    files = gen.generate(FACTS, SV_DOCKER)
    assert files[bundle_names.DOCKER_SV_CERT_FILE] == SV_CERT
    assert files[bundle_names.DOCKER_SV_KEY_FILE] == SV_KEY
    sh = files[bundle_names.DOCKER_RUN_FILE]
    assert f"--env HOSTNAME_OVERRIDE={SV_HOST}" in sh
    assert f"--env TLS_CERT={render_docker.DOCKER_SV_CERT_PATH}" in sh
    assert f"--env TLS_KEY={render_docker.DOCKER_SV_KEY_PATH}" in sh
    assert f'-v "$SV_TLS_CERT":{render_docker.DOCKER_SV_CERT_PATH}:ro' in sh
    assert f'-v "$SV_TLS_KEY":{render_docker.DOCKER_SV_KEY_PATH}:ro' in sh
    # ...and each keeps `ca_bundle`'s escape hatch: a host may already have the
    # certificate its platform team maintains.
    assert f'SV_TLS_CERT="${{SV_TLS_CERT:-$DIR/{bundle_names.DOCKER_SV_CERT_FILE}}}"' in sh
    assert "virtual-service certificate not found" in sh
    # compose.yaml mounts the same pair at the same paths.
    svc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["volumes"][-2:] == [
        f"${{SV_TLS_CERT:-./{bundle_names.DOCKER_SV_CERT_FILE}}}:{render_docker.DOCKER_SV_CERT_PATH}:ro",
        f"${{SV_TLS_KEY:-./{bundle_names.DOCKER_SV_KEY_FILE}}}:{render_docker.DOCKER_SV_KEY_PATH}:ro"]
    assert svc["environment"]["HOSTNAME_OVERRIDE"] == SV_HOST


def test_the_hostname_alone_is_a_configuration():
    """A hostname without a certificate generates plain-HTTP SV with no TLS
    variables."""
    files = gen.generate(FACTS, {**DOCKER, "sv_hostname": SV_HOST})
    assert bundle_names.DOCKER_SV_CERT_FILE not in files
    sh = files[bundle_names.DOCKER_RUN_FILE]
    assert f"--env HOSTNAME_OVERRIDE={SV_HOST}" in sh
    assert "TLS_CERT" not in sh
    # ...and the README says which of the two it is, because "no certificate"
    # is a decision somebody may not have realised they were making.
    assert "plain HTTP" in files["README.md"]


def test_a_pkcs1_key_is_refused_by_name_with_the_conversion():
    """A PKCS#1 key is refused, naming the openssl conversion."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": SV_KEY_PKCS1})
    assert "PKCS#1" in str(e.value)
    assert "openssl pkcs8 -topk8" in str(e.value)
    # An encrypted key is refused: nothing can supply its passphrase.
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER,
                             "sv_tls_key": "-----BEGIN ENCRYPTED PRIVATE KEY-----\nx\n"})
    assert "passphrase" in str(e.value)
    # ...and something that is not a key at all.
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": "hunter2"})
    assert "sv_tls_key" in str(e.value)


def test_a_certificate_that_does_not_cover_the_hostname_is_refused():
    """A certificate that does not cover the hostname is refused, naming
    what it covers."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_hostname": SV_WRONG_HOST})
    assert SV_WRONG_HOST in str(e.value)
    for name in SV_NAMES:
        assert name in str(e.value)
    # A wildcard covers one label, as it does everywhere else.
    gen.generate(FACTS, {**SV_DOCKER, "sv_hostname": SV_WILDCARD_HOST})
    with pytest.raises(ValueError):
        gen.generate(FACTS, {**SV_DOCKER, "sv_hostname": "a.b." + SV_HOST})


def test_a_certificate_naming_no_host_is_refused_in_its_own_words():
    """A certificate naming no host is refused as such."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_tls_cert": SV_CERT_NO_NAMES})
    assert "no DNS name at all" in str(e.value)


def test_a_certificate_this_cannot_read_is_not_checked_and_says_so():
    """An unreadable certificate is not refused; the README says it was not
    checked. A readable one is reported as checked."""
    corrupt = SV_CERT.replace(SV_CERT.splitlines()[3], "AAAA")
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_cert": corrupt})
    readme = files["README.md"]
    assert "was not checked against the certificate" in readme
    assert "openssl x509" in readme
    # ...and the bundle a check did pass says that instead, naming what the
    # certificate carries and what was *not* asked about it.
    checked = gen.generate(FACTS, SV_DOCKER)["README.md"]
    assert "which covers the hostname" in checked
    assert "expiry, issuer and key pair were not checked" in checked


def test_the_private_key_is_never_in_the_profile_and_the_certificate_is():
    """profile.json keeps the certificate and hostname but never the private
    key."""
    files = gen.generate(FACTS, SV_DOCKER)
    profile = json.loads(files[bundle_names.PROFILE_FILE])
    assert "sv_tls_key" not in profile
    assert profile["sv_tls_cert"] == SV_CERT
    assert profile["sv_hostname"] == SV_HOST
    replayed = gen.generate(FACTS, {**profile, "auth_token": "de" * 32})
    assert replayed[bundle_names.DOCKER_SV_KEY_FILE] == markers_mod.marker("sv_tls_key")
    assert "`sv_tls_key`" in replayed["README.md"]


def test_half_a_tls_pair_is_a_blank_field_rather_than_a_refusal():
    """Half a TLS pair marks the missing half; a blank hostname beside a
    certificate is marked in its variable."""
    only_cert = gen.generate(FACTS, {**DOCKER, "sv_hostname": SV_HOST,
                                     "sv_tls_cert": SV_CERT})
    assert only_cert[bundle_names.DOCKER_SV_KEY_FILE] == markers_mod.marker("sv_tls_key")
    assert "`sv_tls_key`" in only_cert["README.md"]
    blank_host = gen.generate(FACTS, {**SV_DOCKER, "sv_hostname": ""})
    assert "HOSTNAME_OVERRIDE" in render_docker._blank_env_by_name(FACTS, {
        **bundle_options.DEFAULT_OPTIONS, **SV_DOCKER,
        "sv_hostname": markers_mod.marker("sv_hostname")})
    assert "HOSTNAME_OVERRIDE carries" in blank_host[bundle_names.DOCKER_RUN_FILE]


def test_the_script_refuses_a_blank_mounted_file_before_starting_anything(tmp_path):
    """The script refuses a mounted file whose content is a marker, checking
    the resolved file."""
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": ""})
    assert files[bundle_names.DOCKER_SV_KEY_FILE] == markers_mod.marker("sv_tls_key")
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 1
    assert "sv-tls.key carries <SV_TLS_KEY>" in r.stderr
    # Names the option to re-generate with and the variable that fixes it.
    assert "sv_tls_key" in r.stderr and "Set SV_TLS_KEY" in r.stderr
    assert made == ["ps"], made           # nothing was started

    # Filled in, the same bundle runs: the check reads the file as it stands,
    # so there is nothing to delete afterwards.
    (tmp_path / bundle_names.DOCKER_SV_KEY_FILE).write_text(SV_KEY)
    r, made = _run_bundle(tmp_path)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "ps", "run"], made


def test_the_escape_hatch_finishes_a_blank_mounted_file_too(tmp_path):
    """Pointing SV_TLS_KEY at a real file clears the check."""
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": ""})
    (tmp_path / "elsewhere.key").write_text(SV_KEY)
    r, made = _run_bundle(tmp_path, files,
                          env={"SV_TLS_KEY": str(tmp_path / "elsewhere.key")})
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "run"], made


def test_compose_refuses_a_blank_mounted_file_in_the_same_words():
    """compose guards a blank mounted file on the bundle's own variable, in
    the script's words."""
    m = [m for m in render_docker.docker_file_mounts({**bundle_options.DEFAULT_OPTIONS, **SV_DOCKER,
                                            "sv_tls_key":
                                                markers_mod.marker("sv_tls_key")})
         if m.var == "SV_TLS_KEY"][0]
    wrong, todo = render_docker._docker_blank_file_lines(m)
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": ""})
    svc = yaml.safe_load(files[bundle_names.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["volumes"][-1] == (
        f"${{SV_TLS_KEY:?{wrong} {todo}}}:{render_docker.DOCKER_SV_KEY_PATH}:ro")
    assert "BZM_OPL_UNSET" not in files[bundle_names.DOCKER_COMPOSE_FILE]
    # One wording for both routes, so a customer reads the same sentence about
    # the same file whichever of the two they started from.
    for line in (wrong, todo):
        assert line in files[bundle_names.DOCKER_RUN_FILE]
    # The certificate beside it was supplied, so it keeps its default and
    # neither route says anything about it: the guard is per file left blank.
    assert svc["volumes"][-2] == (
        f"${{SV_TLS_CERT:-./{bundle_names.DOCKER_SV_CERT_FILE}}}:"
        f"{render_docker.DOCKER_SV_CERT_PATH}:ro")


def test_a_finished_tls_pair_carries_no_file_guard(tmp_path):
    """A complete TLS pair carries no file guard."""
    files = gen.generate(FACTS, SV_DOCKER)
    assert not markers_mod.MARKER_RE.search(files[bundle_names.DOCKER_RUN_FILE])
    assert not markers_mod.MARKER_RE.search(files[bundle_names.DOCKER_COMPOSE_FILE])
    assert ":?" not in files[bundle_names.DOCKER_COMPOSE_FILE]
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "run"], made


# -- the images crane will not pull -------------------------------------------
# On docker crane creates containers without pulling. The mock images follow
# the location's funcIds, not the sv_* options.

MOCK_FACTS = {**FACTS, "func_ids": ["mockServices"]}
MOCK_LATEST = f"{footprint.PUBLIC_REGISTRY}/blazemeter/service-mock:latest"


def test_a_docker_sv_bundle_names_the_images_crane_will_not_pull():
    """A docker SV bundle with a registry lists the docker pull commands."""
    readme = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})["README.md"]
    assert f"docker pull {REG}/blazemeter/service-mock:latest" in readme
    assert "does not pull" in readme
    assert "No such image" in readme
    # A sealed host needs the list rather than an attempt.
    assert "docker load" in readme
    # The registry is the configured one, because that is what crane prefixes.
    mirrored = gen.generate(MOCK_FACTS, {**DOCKER,
                                         "private_registry": "reg.corp/bzm"})
    assert ("docker pull reg.corp/bzm/blazemeter/service-mock:latest"
            in mirrored["README.md"])


def test_the_tag_to_pull_is_latest_and_never_the_one_the_location_pins():
    """The pull list uses latest, never the location's pinned tag."""
    pinned = {**MOCK_FACTS, "images": [
        {**i, "key": "blazemeter/service-mock:6.0.30.4", "tag": "6.0.30.4"}
        if i["key"].startswith("blazemeter/service-mock") else i
        for i in FACTS["images"]]}
    readme = gen.generate(pinned, {**DOCKER, "private_registry": REG})["README.md"]
    assert f"docker pull {REG}/blazemeter/service-mock:latest" in readme
    assert "6.0.30.4" not in readme
    # ...nor the repo's last segment: crane composes from the key.
    assert "{REG}/service-mock:" not in readme


def test_a_default_docker_bundle_names_no_registry_at_all():
    """Without a private registry, no docker file sets DOCKER_REGISTRY."""
    files = gen.generate(FACTS, DOCKER)
    for name in ("bzm-opl-agent.sh", "compose.yaml", "bzm-opl-agent.env"):
        body = files.get(name) or ""
        assert "DOCKER_REGISTRY=" not in body, name
        assert "DOCKER_REGISTRY:" not in body, name


def test_a_mirrored_docker_bundle_still_names_its_registry():
    """With a private registry, both docker files set DOCKER_REGISTRY."""
    files = gen.generate(FACTS, {**DOCKER, "private_registry": "reg.corp/bzm"})
    assert "DOCKER_REGISTRY=reg.corp/bzm" in files["bzm-opl-agent.sh"]
    assert 'DOCKER_REGISTRY: "reg.corp/bzm"' in files["compose.yaml"]


def test_the_docker_pre_pull_list_covers_the_engine_images_too():
    """A performance docker bundle lists engine images to pull, at latest."""
    files = gen.generate(FACTS, {**DOCKER, "private_registry": "reg.corp/bzm"})
    readme = files["README.md"]
    assert "docker pull reg.corp/bzm/taurus-cloud:latest" in readme
    # ...and the pinned tag is still not what to fetch.
    assert "docker pull reg.corp/bzm/taurus-cloud:2.4.454-reduced" not in readme


def test_the_docker_mirror_pushes_every_name_crane_composes():
    """The docker mirror pushes exactly the pull list, plus crane itself."""
    both = {**FACTS, "func_ids": ["performance", "mockServices"]}
    files = gen.generate(both, {**DOCKER, "private_registry": "reg.corp/bzm"})
    dests = {l.split()[-1] for l in files["bzm-opl-image-mirror.sh"].splitlines()
             if l.startswith("mirror ")}
    pulls = {l.split("docker pull ", 1)[1].strip()
             for l in files["README.md"].splitlines() if "docker pull " in l}
    crane = {d for d in dests if "/crane:" in d}
    assert crane, "crane's own image is not in the mirror"
    assert dests - crane == pulls, (dests - crane) ^ pulls


def test_the_cluster_mirror_does_not_take_dockers_shape():
    """The cluster mirror pushes repo path and pinned tag, not docker's
    key:latest."""
    files = gen.generate(FACTS, {"namespace": "ns1", "ship_id": "bbb222",
                                 "auth_token": "de" * 32,
                                 "private_registry": "reg.corp/bzm"})
    mirror = files["bzm-opl-image-mirror.sh"]
    assert "reg.corp/bzm/blazemeter/v4:2.4.444-reduced" in mirror
    assert "taurus-cloud:latest" not in mirror


def test_the_pull_command_names_the_registry_crane_is_actually_given():
    registry = "reg.corp/bzm"
    """The README's registry and `DOCKER_REGISTRY` are resolved in two places
    from the same expression, and only one of them reaches crane. Drift there
    would print a pull for a registry nothing goes on to ask for -- which is
    this bug again, wearing the fix -- so the two are held equal rather than
    left to agree."""
    files = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": registry})
    given = [l.split("=", 1)[1].strip().rstrip("\\").strip().strip('"')
             for l in files["bzm-opl-agent.sh"].splitlines()
             if "DOCKER_REGISTRY=" in l and "USERNAME" not in l
             and "PASSWORD" not in l and "EMAIL" not in l]
    assert given, "the script names no DOCKER_REGISTRY"
    pulls = [l.split("docker pull ", 1)[1].strip()
             for l in files["README.md"].splitlines() if "docker pull " in l]
    assert pulls
    for ref in pulls:
        assert ref.startswith(given[0] + "/"), (ref, given[0])


def test_a_docker_bundle_with_no_registry_warns_without_naming_names():
    """Without a registry, the docker README warns about pulling but lists
    no pull commands."""
    readme = gen.generate(FACTS, DOCKER)["README.md"]      # no private registry
    assert "does not pull" in readme
    # No *command* -- the prose may well mention the phrase while explaining why
    # there is nothing to run.
    assert not [l for l in readme.splitlines() if l.strip().startswith("docker pull ")]
    # The keys are still named -- what is unknown is the registry, not which
    # images the location runs.
    assert "`taurus-cloud`" in readme


def test_the_cluster_formats_carry_no_pre_pull_note():
    """Cluster READMEs carry no docker pull note."""
    manifests = gen.generate(MOCK_FACTS, dict(SV_OPTS, namespace="ns1"))
    helm = gen.generate(MOCK_FACTS, {"namespace": "ns1", "ship_id": "bbb222",
                                     "auth_token": "de" * 32,
                                     "output_format": "helm",
                                     "sv_ingress": service_virt.SV_INGRESS_NONE})
    for files in (manifests, helm):
        for name, body in files.items():
            if name.endswith(".md"):
                assert "docker pull" not in body, name


# -- and the registry they are mirrored into ---------------------------------
# The mirror pushes exactly what the README says to pull.

MIRROR = "bzm-opl-image-mirror.sh"
REG = "reg.corp/bzm"


def _mirror_pairs(files):
    """Every (source, destination) the mirror script pushes."""
    return [tuple(shlex.split(l)[1:3]) for l in files[MIRROR].splitlines()
            if l.startswith("mirror ")]


def _readme_pulls(files):
    return [l.split("docker pull ", 1)[1].strip()
            for l in files["README.md"].splitlines() if "docker pull " in l]


def test_the_docker_mirror_pushes_exactly_what_the_readme_says_to_pull():
    """The docker mirror's mock destinations equal the README's pull list."""
    files = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})
    pushed = {d for _, d in _mirror_pairs(files) if "/blazemeter/" in d}
    pulled = set(_readme_pulls(files))
    assert pushed == pulled, (pushed, pulled)
    assert f"{REG}/blazemeter/service-mock:latest" in pushed
    # The source is the pinned public ref; only :latest is pushed.
    sources = {s for s, d in _mirror_pairs(files) if "/blazemeter/" in d}
    assert f"{footprint.PUBLIC_REGISTRY}/blazemeter/service-mock:1.0" in sources
    assert not [d for _, d in _mirror_pairs(files) if d.endswith(":1.0")]


def test_cranes_own_mirror_target_is_the_reference_the_bundle_runs():
    """Crane's mirror target is the image both docker files run."""
    files = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})
    crane = f"{REG}/crane:3.7.55"
    assert crane in files["bzm-opl-agent.sh"]
    assert crane in files[bundle_names.DOCKER_COMPOSE_FILE]
    assert crane in [d for _, d in _mirror_pairs(files)]


def _cluster_overrides(files):
    """IMAGE_OVERRIDES as the bundle wrote it: the ConfigMap, or
    imageOverrides in the values."""
    if "bzm_configmap.yaml" in files:
        return json.loads(yaml.safe_load(
            files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    return yaml.safe_load(files[bundle_names.HELM_VALUES_FILE])["imageOverrides"]


CLUSTER_FORMATS = ("manifests", "helm")


def _cluster_bundle(fmt, facts=FACTS, **over):
    return gen.generate(facts, {"namespace": "ns1", "ship_id": "bbb222",
                                "auth_token": "de" * 32, "output_format": fmt,
                                "private_registry": REG, **over})


def test_the_cluster_map_and_its_mirror_name_one_set():
    """In both cluster formats, IMAGE_OVERRIDES values equal the mirror's
    destinations, crane aside."""
    for fmt in CLUSTER_FORMATS:
        # A mockServices location declining an ingress: the widest image set.
        files = _cluster_bundle(fmt, MOCK_FACTS, sv_ingress=service_virt.SV_INGRESS_NONE)
        dests = {d for _, d in _mirror_pairs(files)}
        overrides = set(_cluster_overrides(files).values())
        crane = {d for d in dests if "/crane:" in d}
        assert crane, f"crane's own image is not in the mirror ({fmt})"
        assert dests - crane == overrides, (fmt, (dests - crane) ^ overrides)


def test_the_engine_is_mirrored_where_crane_composes_it():
    """The engine maps and mirrors to <registry>/blazemeter/v4:<tag>."""
    for fmt in CLUSTER_FORMATS:
        files = _cluster_bundle(fmt)
        want = f"{REG}/blazemeter/v4:2.4.444-reduced"
        assert _cluster_overrides(files)["taurus-cloud:latest"] == want, fmt
        assert want in {d for _, d in _mirror_pairs(files)}, fmt


def test_cranes_cluster_mirror_target_is_the_reference_the_bundle_runs():
    """Crane's mirror target is the image the Deployment and the chart
    values name."""
    crane = f"{REG}/crane:3.7.55"
    manifests = _cluster_bundle("manifests")
    assert crane in manifests["bzm_deployment.yaml"]
    assert crane in [d for _, d in _mirror_pairs(manifests)]
    helm = _cluster_bundle("helm")
    values = yaml.safe_load(helm[bundle_names.HELM_VALUES_FILE])
    assert f"{values['image']['repository']}:{values['image']['tag']}" == crane
    assert crane in [d for _, d in _mirror_pairs(helm)]


def test_a_performance_docker_bundle_mirrors_what_crane_will_ask_for():
    """A performance docker mirror pushes <registry>/<key>:latest, and the
    README pulls the same."""
    files = gen.generate(FACTS, {**DOCKER, "private_registry": REG})
    dests = {d for _, d in _mirror_pairs(files)}
    assert dests == {f"{REG}/crane:3.7.55", f"{REG}/taurus-cloud:latest",
                     f"{REG}/apm-image:latest"}
    assert f"docker pull {REG}/taurus-cloud:latest" in files["README.md"]


def test_the_docker_mirror_says_which_of_its_two_shapes_is_which():
    """The docker mirror script explains its destinations."""
    sh = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})[MIRROR]
    assert "Destinations:" in sh
    assert "README.md" in sh
    assert "DOCKER_REGISTRY" in sh


def test_the_two_platforms_never_offer_each_other_s_vocabulary():
    """Each platform's SV variables are absent from the other and listed as
    not carried."""
    k8s = gen.generate(FACTS, {"ship_id": "bbb222", "auth_token": "de" * 32,
                               "namespace": "ns1", "sv_hostname": SV_HOST,
                               "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY})
    cm = yaml.safe_load(k8s["bzm_configmap.yaml"])
    assert not {"HOSTNAME_OVERRIDE", "TLS_CERT", "TLS_KEY"} & set(cm["data"])
    assert "sv-tls.crt" not in k8s
    # Named, never dropped.
    assert "## Set here, but not carried" in k8s["README.md"]
    assert "`sv_hostname`" in k8s["README.md"]
    assert bundle_options.SV_DOCKER_IGNORED.keys() <= bundle_options.IGNORED_BY_FORMAT["helm"].keys()


def test_docker_profile_replays_and_carries_no_token():
    """The docker profile.json records the format and no token."""
    files = gen.generate(FACTS, DOCKER)
    profile = json.loads(files["profile.json"])
    assert profile["output_format"] == "docker"
    assert "auth_token" not in profile


# -- free-form agent environment ---------------------------------------------
# Mostly the refusal of a variable an option already sets.

SV_FACTS = {**FACTS, "func_ids": ["performance", "mockServices"]}

# One bundle per branch that writes a variable, so the union below is the whole
# vocabulary rather than the common case's share of it.
ENV_COVERAGE = [
    (FACTS, {"ship_id": "bbb222", "auth_token": "de" * 32,
             "private_registry": "reg.example.com/bzm", "registry_auth": True,
             "auto_update": True, "use_secret": False,
             "proxy": {"http": "http://px:3128", "https": "http://px:3128"},
             "ca_bundle": "-----BEGIN CERTIFICATE-----",
             "engine_ephemeral_request_mb": 1024,
             "engine_ephemeral_limit_mb": 61440,
             "engine_node_selector": {"pool": "bzm-engines"},
             "engine_tolerations": [{"key": "pool", "operator": "Exists"}]}),
    (SV_FACTS, {"ship_id": "bbb222", "auth_token": "de" * 32,
                "sv_ingress": "istio", "sv_subdomain": "apps.example.com",
                "sv_tls_secret": "wild", "sv_istio_gateway": "gw"}),
    (FACTS, {**DOCKER, "auto_update": False,
             "proxy": {"http": "http://px:3128"},
             "ca_bundle": "-----BEGIN CERTIFICATE-----"}),
    # Docker SV: the only branch writing HOSTNAME_OVERRIDE and the TLS pair.
    (SV_FACTS, {**DOCKER, "sv_hostname": SV_HOST,
                "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY}),
]


def _env_names(facts, opts):
    """Every environment variable name a bundle writes, commented stubs
    included."""
    files = gen.generate(facts, opts)
    names = set()
    for name, content in files.items():
        if name.endswith(".yaml"):
            for doc in yaml.safe_load_all(content):
                # Kubernetes objects only; compose.yaml is counted via docker_env.
                if not isinstance(doc, dict) or "metadata" not in doc:
                    continue
                # The agent's ConfigMap and Secret only; the CA ConfigMap is
                # keyed by file name.
                if doc["metadata"]["name"] in ("blazemeter-configmap",
                                               "blazemeter-secret"):
                    names |= set(doc.get("data") or {})
                    names |= set(doc.get("stringData") or {})
                    names |= set(re.findall(r"^\s*# ([A-Z][A-Z0-9_]*): <",
                                            content, re.M))
    if opts.get("output_format") == "docker":
        names |= set(render_docker.docker_env(facts, {**bundle_options.DEFAULT_OPTIONS, **opts}))
    return names


def test_reserved_env_is_what_the_bundles_actually_write():
    """RESERVED_ENV equals the variable names the bundles actually write."""
    written = set()
    for facts, opts in ENV_COVERAGE:
        written |= _env_names(facts, opts)
    assert written == set(bundle_env.RESERVED_ENV)


def test_extra_env_reaches_every_format():
    env = {"PREFERRED_INTERFACE": "eth1", "KUBERNETES_USE_PRE_PULLING": "true"}
    base = {"ship_id": "bbb222", "auth_token": "de" * 32, "extra_env": env}

    cm = yaml.safe_load(
        gen.generate(FACTS, base)["bzm_configmap.yaml"])
    assert cm["data"]["PREFERRED_INTERFACE"] == "eth1"
    assert cm["data"]["KUBERNETES_USE_PRE_PULLING"] == "true"

    values = yaml.safe_load(
        gen.generate(FACTS, {**base, "output_format": "helm"})["bzm-opl-values.yaml"])
    assert values["extraEnv"] == env

    sh = gen.generate(FACTS, {**base, "output_format": "docker"})["bzm-opl-agent.sh"]
    assert "--env PREFERRED_INTERFACE=eth1" in sh
    # Configuration, not a credential: it stays in the command even where the
    # token has moved into the env file.
    assert "PREFERRED_INTERFACE" not in (
        gen.generate(FACTS, {**base, "output_format": "docker"})
        .get(bundle_names.DOCKER_ENV_FILE, ""))


def test_extra_env_refuses_a_variable_the_bundle_already_writes():
    """extra_env refuses a reserved name, naming the owning option."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {"ship_id": "bbb222",
                             "extra_env": {"KUBERNETES_SERVICE_USE_TYPE": "NODEPORT"}})
    assert "service_type" in str(e.value)
    # ...and one no option owns is still refused, without inventing a
    # redirection for it.
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {"ship_id": "bbb222", "extra_env": {"SHIP_ID": "x"}})
    assert "SHIP_ID" in str(e.value)


def test_extra_env_refuses_a_kubernetes_variable_in_a_docker_bundle():
    """extra_env refuses KUBERNETES_* names on docker too."""
    with pytest.raises(ValueError):
        gen.generate(FACTS, {**DOCKER,
                             "extra_env": {"KUBERNETES_NODE_SELECTOR_JSON": "{}"}})


def test_extra_env_refuses_a_name_no_process_could_read():
    """extra_env refuses names that are not valid environment variable
    names."""
    for bad in ("my-var", "9LIVES", "a.b", ""):
        with pytest.raises(ValueError) as e:
            gen.generate(FACTS, {"ship_id": "bbb222", "extra_env": {bad: "x"}})
        assert "environment variable name" in str(e.value)


def test_extra_env_values_are_written_as_the_container_will_see_them():
    """extra_env booleans are written lower case, numbers as strings."""
    cm = yaml.safe_load(gen.generate(FACTS, {
        "ship_id": "bbb222",
        "extra_env": {"A": True, "B": 8080, "C": None},
    })["bzm_configmap.yaml"])
    assert cm["data"]["A"] == "true"
    assert cm["data"]["B"] == "8080"
    assert cm["data"]["C"] == ""
    # A structure is refused rather than encoded.
    with pytest.raises(ValueError):
        gen.generate(FACTS, {"ship_id": "bbb222", "extra_env": {"A": {"b": 1}}})


def test_extra_env_travels_in_the_profile():
    """extra_env is recorded in profile.json."""
    profile = json.loads(gen.generate(FACTS, {
        "ship_id": "bbb222", "extra_env": {"PREFERRED_INTERFACE": "eth1"},
    })["profile.json"])
    assert profile["extra_env"] == {"PREFERRED_INTERFACE": "eth1"}

# -- the bundle that names a certificate it does not carry --------------------
# ca_bundle_slot: the certificate is a file named by ca_cert_file, supplied at
# deploy time. No PEM and no marker reach any manifest.

CERT_FILE_OPTS = {"namespace": "ns1", "ca_bundle_slot": True,
                  "ca_cert_file": "corp-root.crt"}


def test_the_file_mode_carries_no_configmap_and_no_pem():
    """File-mode CA emits no CA ConfigMap and no PEM."""
    files = gen.generate(FACTS, CERT_FILE_OPTS)
    _all_yaml_parse(files)
    assert "bzm_cacerts.yaml" not in files
    assert not any(markers_mod.marker("ca_bundle") in t for t in files.values())
    assert not any("BEGIN CERTIFICATE" in t for t in files.values())


def test_the_file_mode_wires_the_agent_exactly_like_a_filled_bundle():
    """File-mode CA wires the mount and env exactly like inline."""
    files = gen.generate(FACTS, CERT_FILE_OPTS)
    spec = yaml.safe_load(files["bzm_deployment.yaml"])["spec"]["template"]["spec"]
    assert spec["volumes"][0]["configMap"]["name"] == bundle_names.CA_CONFIGMAP
    assert spec["containers"][0]["volumeMounts"][0]["mountPath"] == ca_trust.CA_MOUNT_PATH
    conf = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    path = f"{ca_trust.CA_MOUNT_PATH}/corp-root.crt"
    assert conf["REQUESTS_CA_BUNDLE"] == path
    assert conf["AWS_CA_BUNDLE"] == path
    assert conf["KUBERNETES_CA_BUNDLE_MOUNT"] == (
        f"REQUESTS_CA_BUNDLE={bundle_names.CA_CONFIGMAP}=corp-root.crt:"
        f"AWS_CA_BUNDLE={bundle_names.CA_CONFIGMAP}=corp-root.crt")


def test_one_field_names_the_file_everywhere_it_appears():
    """ca_cert_file names the key in every CA variable and the mount."""
    conf = yaml.safe_load(gen.generate(FACTS, dict(
        CERT_FILE_OPTS, ca_cert_file="weird name.pem"))["bzm_configmap.yaml"])["data"]
    assert conf["REQUESTS_CA_BUNDLE"].endswith("/weird name.pem")
    assert conf["AWS_CA_BUNDLE"].endswith("/weird name.pem")
    assert conf["KUBERNETES_CA_BUNDLE_MOUNT"].count("weird name.pem") == 2


def test_an_unnamed_certificate_is_the_marker_and_not_a_name_we_invented():
    """An unnamed file-mode certificate is the marker; inline defaults to
    ca-bundle.crt."""
    conf = yaml.safe_load(gen.generate(FACTS, {
        "namespace": "ns1", "ca_bundle_slot": True})["bzm_configmap.yaml"])["data"]
    assert conf["REQUESTS_CA_BUNDLE"] == f"{ca_trust.CA_MOUNT_PATH}/{markers_mod.marker('ca_cert_file')}"
    # ...and the inline mode does not, because there the bundle writes the file
    # itself and the name is genuinely this generator's to pick.
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    inline = yaml.safe_load(gen.generate(FACTS, {
        "namespace": "ns1", "ca_bundle": pem})["bzm_configmap.yaml"])["data"]
    assert inline["REQUESTS_CA_BUNDLE"] == f"{ca_trust.CA_MOUNT_PATH}/{ca_trust.CA_FILENAME}"


def test_the_readme_prints_the_create_command_keyed_to_what_is_mounted():
    """The README's create command uses --from-file=<key>=<path>."""
    md = gen.generate(FACTS, CERT_FILE_OPTS)["README.md"]
    assert (f"kubectl create configmap {bundle_names.CA_CONFIGMAP}" in md
            and "--from-file=corp-root.crt=./corp-root.crt -n ns1" in md)
    # The failure when somebody skips it, because a step nobody explains is a
    # step people skip. It is the kubelet refusing the mount, not this bundle.
    assert "ContainerCreating" in md


def test_the_helm_bundle_ships_the_file_rather_than_inlining_the_pem():
    """The helm bundle ships the certificate beside the chart and names it
    in the values, which hold no PEM."""
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    files = gen.generate(FACTS, {"namespace": "ns1", "output_format": "helm",
                                 "ca_bundle": pem})
    assert files[f"{bundle_names.CHART_DIR}/{ca_trust.CA_FILENAME}"].strip() == pem
    values = files[bundle_names.HELM_VALUES_FILE]
    assert f'file: "{ca_trust.CA_FILENAME}"' in values
    assert "BEGIN CERTIFICATE" not in values
    # ...and with no certificate, the name alone and no file; the chart
    # refuses the install until it is supplied.
    named = gen.generate(FACTS, dict(CERT_FILE_OPTS, output_format="helm"))
    assert f"{bundle_names.CHART_DIR}/corp-root.crt" not in named
    assert 'file: "corp-root.crt"' in named[bundle_names.HELM_VALUES_FILE]


def test_naming_a_file_and_supplying_a_pem_are_two_answers_to_one_question():
    with pytest.raises(ValueError, match="ca_bundle_slot"):
        gen.generate(FACTS, {"namespace": "ns1", "ca_bundle_slot": True,
                             "ca_bundle": "-----BEGIN CERTIFICATE-----"})


def test_the_file_mode_is_one_of_the_ca_modes_not_a_fourth_thing():
    """The file mode is a CA mode, and no_ca() clears it."""
    assert "ca_bundle_slot" in ca_trust.CA_MODES
    assert "ca_cert_file" in ca_trust.CA_OPTIONS
    cleared = ca_trust.no_ca()
    assert ca_trust.ca_cfg({**bundle_options.DEFAULT_OPTIONS, **CERT_FILE_OPTS, **cleared}) is None


@pytest.mark.parametrize("options,mode", [
    ({}, None),
    ({"ca_bundle": "-----BEGIN CERTIFICATE-----"}, "inline"),
    ({"ca_existing_configmap": "corp-trust"}, "existing"),
    ({"ca_bundle_slot": True, "ca_cert_file": "corp-root.crt"}, "file"),
    ({"ca_openshift_inject": True}, "inject"),
])
def test_the_mode_a_bundle_is_in_is_readable_without_rendering_it(options, mode):
    """ca_mode() reads the CA mode from the options."""
    assert ca_trust.ca_mode(dict(options, namespace="ns1")) == mode


def test_options_generate_will_refuse_are_their_own_answer_not_no_ca():
    """Conflicting CA options read as CA_UNRESOLVED, not as no CA, without
    raising."""
    both = {"namespace": "ns1", "ca_bundle_slot": True,
            "ca_bundle": "-----BEGIN CERTIFICATE-----"}
    assert ca_trust.ca_mode(both) is ca_trust.CA_UNRESOLVED
    assert ca_trust.resolved_ca(both) is ca_trust.CA_UNRESOLVED
    assert ca_trust.ca_mode({"namespace": "ns1"}) is None
    # And the notice above it still says nothing about a bundle generate is
    # about to refuse.
    assert ca_trust.ca_slot_notice(both) is None


def test_generate_says_it_before_the_bundle_is_even_written():
    """The file-mode notice names the certificate and the ConfigMap."""
    said = ca_trust.ca_slot_notice(CERT_FILE_OPTS)
    assert "corp-root.crt" in said and bundle_names.CA_CONFIGMAP in said


def test_the_notice_tells_a_named_file_from_one_nobody_has_named():
    """The notice distinguishes a named certificate file from an unnamed
    one."""
    named = ca_trust.ca_slot_notice(CERT_FILE_OPTS)
    assert "corp-root.crt" in named and markers_mod.marker("ca_cert_file") not in named
    blank = ca_trust.ca_slot_notice({"namespace": "ns1", "ca_bundle_slot": True})
    assert markers_mod.marker("ca_cert_file") in blank


@pytest.mark.parametrize("fmt,says", [
    ("manifests", "kubectl apply"),
    ("helm", "helm install"),
    ("docker", bundle_names.DOCKER_RUN_FILE),
])
def test_the_notice_names_who_builds_the_configmap_on_this_format(fmt, says):
    """The notice says who builds the ConfigMap, per format."""
    said = ca_trust.ca_slot_notice(dict(CERT_FILE_OPTS, output_format=fmt))
    assert says in said


@pytest.mark.parametrize("options", [
    {"ca_bundle": "-----BEGIN CERTIFICATE-----"},   # refused beside the file mode
    {"output_format": "nonsense"},
])
def test_the_notice_leaves_every_refusal_to_the_one_that_owns_it(options):
    """ca_slot_notice never raises."""
    assert ca_trust.ca_slot_notice(dict(options, namespace="ns1",
                                   ca_bundle_slot=True)) is None


@pytest.mark.parametrize("use_secret", [True, False])
def test_every_configmap_value_survives_whatever_it_holds(use_secret):
    """ConfigMap and Secret values round-trip through YAML exactly."""
    no_proxy = "*.corp.example,10.0.0.0/8"
    proxy_url = 'http://px.example:3128'
    o = {"namespace": "ns1", "ship_id": "bbb222", "auth_token": "de" * 32,
         "use_secret": use_secret,
         "proxy": {"http": proxy_url, "https": proxy_url,
                   "username": "u", "password": 'p"w\\d',
                   "no_proxy": no_proxy},
         "node_selector": {"pool": "*.x"}}
    files = gen.generate(FACTS, o)
    docs = {name: list(yaml.safe_load_all(body))
            for name, body in files.items() if name.endswith(".yaml")}
    cm = next(d for ds in docs.values() for d in ds
              if d and d.get("kind") == "ConfigMap"
              and d["metadata"]["name"] == "blazemeter-configmap")
    assert cm["data"]["NO_PROXY"] == no_proxy
    carrier = next(d for ds in docs.values() for d in ds
                   if d and "HTTP_PROXY" in (d.get("stringData") or d.get("data") or {}))
    assert ((carrier.get("stringData") or carrier.get("data"))["HTTP_PROXY"]
            == bundle_env.proxy_env(o)["HTTP_PROXY"])
