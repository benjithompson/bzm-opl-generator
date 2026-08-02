import json
import os
import pathlib
import re
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
    assert ov == {"taurus-cloud:latest": "reg.local/bzm/v4:2.4.444-reduced",
                  "apm-image:latest": "reg.local/bzm/apm:1.7.112"}  # mock excluded
    assert cm["data"]["AUTO_KUBERNETES_UPDATE"] == "false"
    d = files["bzm_deployment.yaml"]
    assert "reg.local/bzm/crane:3.7.55" in d
    assert "imagePullSecrets" in d and "pullsec" in d


def _auto(**over):
    cm = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1", **over})
                        ["bzm_configmap.yaml"])
    return cm["data"]["AUTO_KUBERNETES_UPDATE"]


def test_auto_update_defaults_off():
    """The whole point of the default, and a deliberate departure from
    BlazeMeter's own Kubernetes manifest, which ships it on: with it on, crane
    takes ownership of its Deployment within seconds and the next `helm
    upgrade` fails on a conflict --force-conflicts cannot resolve. The registry
    no longer enters into it -- it used to, which left the trap armed for
    exactly the customers pulling from the public registry."""
    assert _auto() == "false"
    assert _auto(private_registry="reg.local/bzm") == "false"


def test_auto_update_can_be_asked_for():
    """Off is not a refusal to emit true -- a customer who wants the agent to
    keep itself current, and will reinstall rather than upgrade, says so."""
    assert _auto(auto_update=True) == "true"
    assert _auto(auto_update=True, private_registry="reg.local/bzm") == "true"
    assert _auto(auto_update=False) == "false"


def test_auto_update_writes_the_kubernetes_variable_only():
    """BlazeMeter has an AUTO_UPDATE too, and it is the Docker-side switch --
    inert on a Kubernetes agent. Emitting it beside this one would look like a
    second, contradictory setting to anyone reading the ConfigMap."""
    cm = yaml.safe_load(gen.generate(FACTS, {"namespace": "ns1", "auto_update": False})
                        ["bzm_configmap.yaml"])["data"]
    assert "AUTO_UPDATE" not in cm
    assert cm["AUTO_KUBERNETES_UPDATE"] == "false"


def test_configmap_states_what_each_setting_costs():
    """Someone reading the ConfigMap to decide whether they may change it needs
    the consequence, not the value they can already see: on takes field
    ownership, off means nobody is updating the agent but them."""
    on = gen.generate(FACTS, {"namespace": "ns1", "auto_update": True}
                      )["bzm_configmap.yaml"]
    assert "--auto-update" in on and "field ownership" in on
    off = gen.generate(FACTS, {"namespace": "ns1"})["bzm_configmap.yaml"]
    assert "loses support" in off


def test_readme_says_the_agent_is_pinned():
    """On the resolved value, so the default bundle -- the common one -- says
    it. Whoever receives it has to notice the agent ageing, and nothing else in
    the bundle tells them the agent will not do it itself."""
    for over in ({}, {"auto_update": False}, {"private_registry": "reg.local/bzm"}):
        readme = gen.generate(FACTS, {"namespace": "ns1", **over})["README.md"]
        assert "3.7.55" in readme and "Auto-update is **off**" in readme, over
    on = gen.generate(FACTS, {"namespace": "ns1", "auto_update": True})["README.md"]
    assert "Auto-update is **off**" not in on


def test_auto_update_refuses_a_value_that_is_neither():
    """A string "false" resolves truthy and would silently turn auto-update on
    -- the shape a profile.json hand-edited by a customer arrives in."""
    with pytest.raises(ValueError, match="auto_update"):
        gen.generate(FACTS, {"namespace": "ns1", "auto_update": "false"})


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


# -- two node pools: crane small and always-on, engines large and ephemeral ---
#
# The split is the whole point of these options, and it is invisible in a single
# object: crane's placement is the Deployment's podspec, the engines' is the
# KUBERNETES_*_JSON env crane reads. A regression that fed one from the other
# would still produce a bundle that applies.

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
    """The one-pool shape, and what every bundle generated before these options
    did. Unset has to keep meaning "inherit", or an upgrade silently unpins
    every existing location's engines."""
    tol = [{"key": "lifecycle", "operator": "Exists", "effect": "NoSchedule"}]
    files = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                 "tolerations": tol})
    crane_sel, crane_tol, eng_sel, eng_tol = _placement(files)
    assert (eng_sel, eng_tol) == (crane_sel, crane_tol) == (CRANE_POOL, tol)


def test_empty_engine_placement_is_not_the_same_as_unset():
    """`{}` says "engines take no selector even though crane has one" -- the
    crane-on-a-tainted-infra-pool case. Collapsing it into unset would make
    that shape unsayable, and would silently pin engines to crane's pool."""
    files = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                 "tolerations": ENGINE_TOL,
                                 "engine_node_selector": {},
                                 "engine_tolerations": []})
    crane_sel, crane_tol, eng_sel, eng_tol = _placement(files)
    assert (crane_sel, crane_tol) == (CRANE_POOL, ENGINE_TOL)
    # Absent from the ConfigMap entirely, so crane stamps nothing on the engines.
    assert eng_sel is None and eng_tol is None


def test_nodepool_recipe_only_when_the_pools_differ():
    """It is emitted for the shape it describes and not otherwise -- a one-pool
    bundle gaining a file about node pools it does not have is one more thing to
    read before reaching the part that applies."""
    one_pool = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL})
    assert gen.NODEPOOLS_FILE not in one_pool

    two_pool = gen.generate(FACTS, {"namespace": "ns1", "node_selector": CRANE_POOL,
                                    "engine_node_selector": ENGINE_POOL,
                                    "engine_tolerations": ENGINE_TOL,
                                    "engine_cpu_limit": "2",
                                    "engine_mem_limit": "8Gi"})
    md = two_pool[gen.NODEPOOLS_FILE]
    # The label and taint the manifests actually use, so the commands create the
    # pool this bundle selects rather than a worked example of a different one.
    assert "pool=bzm-engines" in md
    assert "bzm.io/engines=true:NoSchedule" in md
    # The stamped-request trap and the only lever that closes it.
    assert gen.ENGINE_STAMPED_REQUEST_CPU in md and "maxPods" in md
    for flavour in ("GKE", "EKS", "AKS", "OpenShift", "kubeadm"):
        assert flavour in md


def test_gke_maxpods_respects_the_floor_the_api_enforces():
    """GKE refuses --max-pods-per-node below 8 ("must be at least 8 and at most
    256"), verified against the API. The recipe wanted 7 (6 system pods + 1
    engine) and emitted a command that could not run -- worse than no recipe,
    because it looks authoritative."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "engine_node_selector": ENGINE_POOL,
                                 "engine_cpu_limit": "2", "engine_mem_limit": "8Gi"})
    md = files[gen.NODEPOOLS_FILE]
    gke = md[md.index("### GKE"):md.index("### EKS")]
    emitted = int(re.search(r"--max-pods-per-node (\d+)", gke).group(1))
    assert emitted >= gen.GKE_MIN_MAX_PODS
    # And having been forced up, it says what that costs rather than still
    # claiming one engine per node.
    assert "will not go below" in gke
    assert f"{gen._engines_per_node(gen.TYPICAL_SYSTEM_PODS + 1, gen.GKE_MIN_MAX_PODS)} engines a node" in gke


def test_gke_node_is_sized_for_the_engines_the_floor_permits():
    """The two halves have to agree: a maxPods that admits 2 engines and a
    machine sized for 1 is the over-commit this file exists to prevent."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "engine_node_selector": ENGINE_POOL,
                                 "engine_cpu_limit": "2", "engine_mem_limit": "8Gi"})
    md = files[gen.NODEPOOLS_FILE]
    gke = md[md.index("### GKE"):md.index("### EKS")]
    per_node = gen._engines_per_node(gen.TYPICAL_SYSTEM_PODS + 1, gen.GKE_MIN_MAX_PODS)
    cpu = int(re.search(r"--machine-type <at least (\d+) vCPU", gke).group(1))
    assert cpu >= 2 * per_node          # 2 CPU of engine each, plus overhead


def test_nodepool_recipe_emits_no_dangling_continuations():
    """Every command is copy-pasteable. The conditional flags (labels, taints)
    drop out on a bundle that has none, and a `\\` left behind by an omitted
    line makes the shell swallow whatever follows it."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "engine_node_selector": {},
                                 "engine_tolerations": []})
    md = files[gen.NODEPOOLS_FILE]
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
    """Both prerequisites are optional and independent, so neither can number
    itself -- with a private registry *and* split pools they each used to
    produce their own "1." and "2."."""
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



# -- the cluster check --------------------------------------------------------

def _hook_docs(out):
    return {d["metadata"]["name"]: d
            for d in yaml.safe_load_all(out[gen.HOOK_FILE])}


def test_no_cluster_check_unless_asked_for():
    """Off by default. It is a check, not part of the agent, and a bundle that
    quietly carried an extra Pod would surprise whoever applies it."""
    assert gen.HOOK_FILE not in gen.generate(FACTS, {"ship_id": "s1"})


def test_the_cluster_check_is_told_what_the_bundle_decided():
    """Upstream ships this manifest with `default` in every namespace field and
    placeholders for the rest. Every one of those is a value the bundle already
    has, so leaving them to be filled in by hand is two chances to disagree."""
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
    """Its Role is its own and read-only. A check that could create is a check
    that can break the thing it is checking."""
    docs = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True}))
    verbs = {v for rule in docs["bzm-cranehook"]["rules"] for v in rule["verbs"]}
    assert verbs == {"get", "list"}
    assert docs["bzm-cranehook-binding"]["roleRef"]["name"] == "bzm-cranehook"


def test_the_cluster_check_follows_the_platform_uid_rule():
    """Same rule as the agent: OpenShift's SCC assigns the UID and a pinned one
    is refused, so it is pinned only where it is wanted."""
    k8s = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True,
                                          "platform": "k8s", "run_as_user": 1500}))
    sc = k8s["cranehook"]["spec"]["containers"][0]["securityContext"]
    assert sc["runAsUser"] == 1500 and sc["runAsGroup"] == 1500
    ocp = _hook_docs(gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True,
                                          "platform": "openshift"}))
    assert "runAsUser" not in ocp["cranehook"]["spec"]["containers"][0]["securityContext"]


def test_the_cluster_check_is_told_about_the_ingress_it_should_check():
    """Only when there is one. Empty strings would have it check for an ingress
    named "" and a TLS secret named "", which is a failure it invented."""
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
    """It is not in the location's inventory -- the agent never runs it -- so an
    air-gapped bundle would otherwise carry the one object that cannot pull."""
    out = gen.generate(FACTS, {"ship_id": "s1", "crane_hook": True,
                               "private_registry": "reg.local/bzm"})
    assert "reg.local/bzm/cranehook:latest" in out["bzm-opl-image-mirror.sh"]
    pod = _hook_docs(out)["cranehook"]
    assert pod["spec"]["containers"][0]["image"] == "reg.local/bzm/cranehook:latest"


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


def test_a_browser_repo_mirrors_to_exactly_what_the_override_names():
    """Browser repos are the only ones with a directory inside them
    (`.../blazemeter/charmander/chrome_136...`), and both sides flatten a repo
    to its last segment independently. They have to agree: if they drift, the
    mirror pushes one name while IMAGE_OVERRIDES tells crane to pull another,
    and nothing between here and a run says so."""
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
    assert target == "reg.corp.com/bzm/chrome_136.0.7103.113:2.10.45"
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
# Each of these mirrors a failure seen on a real cluster: a mockServices
# location generated without ingress options deploys and then hangs at
# WAITING_FOR_DOMAIN, so the generator refuses instead.

SV_FACTS = dict(FACTS, func_ids=["mockServices"])
SV_OPTS = {"namespace": "ns1", "sv_ingress": "nginx",
           "sv_subdomain": "apps.example.com", "sv_tls_secret": "wildcard-tls"}


def test_sv_location_without_ingress_refuses():
    with pytest.raises(ValueError, match="WAITING_FOR_DOMAIN") as e:
        gen.generate(SV_FACTS, {"namespace": "ns1"})
    # ...and names the way out, because "not answered" is the only state that
    # blocks and the answer "no" is not obvious from a list of four backends.
    assert f"sv_ingress={gen.SV_INGRESS_NONE}" in str(e.value)


def test_sv_location_declining_an_ingress_generates_the_performance_bundle():
    """A location can carry mockServices and be wanted for performance alone.

    Unset is "nobody answered" and stays refused; SV_INGRESS_NONE is the answer,
    and what it buys is only that -- the manifests are byte-identical to the
    ones a location with no ingress options would produce, so nothing about the
    decision leaks into the bundle except the profile that records it.
    """
    declined = gen.generate(SV_FACTS, {"namespace": "ns1",
                                       "sv_ingress": gen.SV_INGRESS_NONE})
    data = yaml.safe_load(declined["bzm_configmap.yaml"])["data"]
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in data
    assert "networking.k8s.io" not in _role_groups(declined)
    # No object the SV path adds, either: the same file set a location that
    # never ran mockServices produces.
    assert declined.keys() == gen.generate(FACTS, {"namespace": "ns1"}).keys()
    # The images still follow the location, not the option: what this location
    # runs is a fact about the account, whatever this bundle publishes.
    mirrored = gen.generate(SV_FACTS, {"namespace": "ns1", "private_registry": "reg.local",
                                       "sv_ingress": gen.SV_INGRESS_NONE})
    ov = json.loads(yaml.safe_load(
        mirrored["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert "blazemeter/service-mock:latest" in ov


def test_declining_an_ingress_is_recorded_in_the_profile():
    """livetest and the UI re-render from profile.json, so a decision that only
    lived in the session would come back as the refusal on the next render."""
    files = gen.generate(SV_FACTS, {"namespace": "ns1",
                                    "sv_ingress": gen.SV_INGRESS_NONE})
    assert json.loads(files[gen.PROFILE_FILE])["sv_ingress"] == gen.SV_INGRESS_NONE


def test_declining_an_ingress_on_a_location_that_never_asked_is_accepted():
    """No funcId demands it, so the value says nothing -- and must not be a new
    way to fail. A profile carrying it moves between locations freely."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "sv_ingress": gen.SV_INGRESS_NONE})
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]


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


def test_a_written_bundle_carries_an_executable_mirror_script(tmp_path):
    """`generate -o out` used to leave it non-executable while the zip download
    carried the bit, so the bundle had an undocumented chmod step -- the two
    writers disagreed because only one of them set it."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "private_registry": "reg.local/bzm"})
    gen.write(files, str(tmp_path))
    script = tmp_path / "bzm-opl-image-mirror.sh"
    assert script.exists() and os.access(script, os.X_OK)


# -- what the bundle says about the location it deploys into -------------------

def _readme(**facts_over):
    return gen.generate({**FACTS, **facts_over}, {"namespace": "ns1"})["README.md"]


def test_the_readme_states_the_location_settings_it_found():
    """The bundle deploys an agent and cannot set the location, so the handover
    has to say what the location must hold -- neither figure is in a manifest."""
    r = _readme(slots=4, threads_per_engine=1000)
    assert "4 engine(s) per agent at 1,000 virtual users" in r
    assert "`slots` / `threadsPerEngine`" in r
    # And says which way it multiplies, because that is the half people get
    # wrong: agents x slots, not slots per location.
    assert "times the agents in" in r


def test_the_readme_names_the_403_when_a_figure_is_missing():
    """The most-documented failure in this project, and the handover used to be
    silent on it: the agent comes online, looks healthy, and every test start
    fails."""
    for over in ({"slots": 1, "threads_per_engine": None},
                 {"slots": None, "threads_per_engine": 500},
                 {"slots": None, "threads_per_engine": None}):
        r = _readme(**over)
        assert "Not enough available resources" in r, over
        assert "Check this location's" in r, over


def test_the_readme_does_not_diagnose_a_location_nobody_asked_about():
    """`facts.manual()` leaves both None because there was no account to ask,
    and `gather()` returns the same None for a location that genuinely has
    neither. Nothing that *generates* may read the marker that tells those
    apart -- see facts.from_manual_entry -- so the README does not claim which
    it is. It says *check*, which is true either way.
    """
    typed = gen.generate(facts_mod.manual("aaa111", "bbb222"),
                         {"namespace": "ns1"})["README.md"]
    assert "Check this location's" in typed
    for claim in ("has no `slots`", "has no `threadsPerEngine`",
                  "typed in", "manual"):
        assert claim not in typed, claim


def test_generate_never_asks_how_the_facts_arrived():
    """The manifests are identical either way, and that is the property
    manual() exists to preserve -- so the source marker is doctor's to read and
    nothing here may branch on it.

    Over the *parsed* source rather than its text, so the rule can be explained
    in a comment -- as it is at _location_bullet -- without the explanation
    tripping it. A docstring naming the marker is a Constant; reading it is a
    Name or an Attribute.
    """
    import ast
    tree = ast.parse(pathlib.Path(gen.__file__).read_text())
    banned = {"from_manual_entry", "MANUAL_SOURCE"}
    read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    read |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not (read & banned), (
        f"generate reads {sorted(read & banned)} -- the manifests are identical "
        f"however the facts arrived, and that is the property facts.manual() "
        f"exists to preserve. The marker is doctor's to read.")
