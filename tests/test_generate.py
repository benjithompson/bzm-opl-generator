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
def test_unnamed_service_account_becomes_a_placeholder(name):
    """Still not defaulted at render time -- the tempting fallback, the
    namespace's `default` account, deploys and hands crane's Role to every other
    pod in the namespace. What changed is only *where* that is stopped: this
    used to raise, which a page that had already let the field be emptied could
    not act on. The marker keeps the refusal and moves it to apply time, where
    `<SERVICE_ACCOUNT_NAME>` is not a legal name and the API server names the
    field."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "service_account_name": name,
                                 "service_account_create": False})
    assert set(_sa_refs(files).values()) == {gen.marker("service_account_name")}
    # ...and the person handed the bundle is told, rather than finding out from
    # a rejected apply.
    assert "service_account_name" in files["README.md"]
    assert "not finished" in files["README.md"]


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


# -- the create command for the recommended mode (#227) -----------------------
#
# The existing-ConfigMap mode is the one nearly every customer takes, and the
# bundle referenced a ConfigMap without ever saying how to make one. BlazeMeter
# document `kubectl create configmap <name> --from-file=<file>`, which keys the
# entry on the *file's* basename -- so a customer following that literally with
# `corp-root.pem` and leaving our key at its default gets a mount that is empty
# rather than one that fails.


def test_the_existing_configmap_mode_prints_the_create_command():
    files = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": False,
                                 "ca_existing_configmap": "corp-trust",
                                 "ca_configmap_key": "trust.pem"})
    assert ("kubectl -n ns1 create configmap corp-trust "
            "--from-file=trust.pem=" in files["README.md"])


def test_the_create_command_carries_the_default_key_when_none_was_set():
    """`ca_configmap_key` unset means `ca-bundle.crt`, so the command has to say
    so -- the whole trap is a key nobody typed disagreeing with a file name."""
    files = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": False,
                                 "ca_existing_configmap": "corp-trust"})
    assert ("kubectl -n ns1 create configmap corp-trust "
            "--from-file=ca-bundle.crt=" in files["README.md"])


def test_the_key_and_the_file_name_cannot_disagree():
    """`--from-file=KEY=PATH` is the explicit form, and it is what closes the
    trap: whatever the customer's file is called, the entry lands under the key
    the manifests mount. The bare form BlazeMeter document does not, so the
    bundle must not print it."""
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
    """Inline and injection both emit `bzm_cacerts.yaml`, so a command telling
    somebody to create one names an object this bundle already carries."""
    files = gen.generate(FACTS, dict(opts, namespace="ns1"))
    assert "create configmap" not in files["README.md"]


def test_the_create_command_is_in_the_helm_bundle_too():
    """The chart references the ConfigMap the same way, so the prerequisite is
    the same one. Both READMEs get it from `_deploy_steps`, which is why."""
    files = gen.generate(FACTS, {"namespace": "ns1", "output_format": "helm",
                                 "ca_existing_configmap": "corp-trust"})
    assert "create configmap corp-trust" in files["README.md"]


def test_the_helm_bundle_makes_the_namespace_before_it_puts_a_configmap_in_it():
    """Helm's own install line carries `--create-namespace`, so the namespace is
    allowed not to exist yet -- and a step ahead of it that names the namespace
    fails with `namespaces "ns1" not found` and no way past it."""
    readme = gen.generate(FACTS, {"namespace": "ns1", "output_format": "helm",
                                  "openshift_cluster": False,
                                  "ca_existing_configmap": "corp-trust"})["README.md"]
    assert "kubectl create namespace ns1" in readme
    assert readme.index("create namespace ns1") < readme.index("create configmap")


def test_the_manifests_bundle_does_not_grow_a_namespace_step():
    """Its apply lines have always assumed the namespace exists -- nothing in
    that bundle creates one. A step that created it here would be this README
    answering a question the rest of it does not."""
    readme = gen.generate(FACTS, {"namespace": "ns1",
                                  "ca_existing_configmap": "corp-trust"})["README.md"]
    assert "create namespace" not in readme


def test_the_create_command_follows_the_cluster_not_the_posture():
    """Same rule as every other emitted command: `openshift_cluster` says which
    binary the person deploying has."""
    files = gen.generate(FACTS, {"namespace": "ns1",
                                 "ca_existing_configmap": "corp-trust"})
    assert "oc -n ns1 create configmap corp-trust" in _commands(files)
    plain = gen.generate(FACTS, {"namespace": "ns1", "openshift_cluster": False,
                                 "ca_existing_configmap": "corp-trust"})
    assert "kubectl -n ns1 create configmap corp-trust" in _commands(plain)


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


def test_limits_env_is_always_carried_defaults_included():
    """A bundle with no sizing options still carries the default limits env.

    doctor and the planner certify engine_size(), which falls back to
    ENGINE_DEFAULT_CPU/MEM -- so a ConfigMap that omitted the env when the two
    options were unset shipped engines with no limits at all, while the
    preflight had judged the cluster against 2/8Gi. Observed live as an engine
    OOMKilled 4s after start (#132). Unset means "documented default", never
    "whatever crane does with no env"."""
    cm = {}
    for platform in ("k8s", "openshift"):
        files = gen.generate(FACTS, {"namespace": "ns1", "platform": platform})
        cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
        assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == gen.ENGINE_DEFAULT_CPU
        assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == gen.ENGINE_DEFAULT_MEM
    # The ephemeral pair stays opt-in: engine_size() vouches for CPU and
    # memory only, and there is no documented ephemeral default to state.
    assert "KUBERNETES_LIMITS_EPHEMERAL_STORAGE" not in cm


def test_engine_limits_derive_from_the_location():
    """Requests and limits are one figure, and the location is where it is set
    (#132): a location carrying overrideCPU/overrideMemory -- the engine pod's
    requests -- gets the same figure as the bundle's limits, so the two agree
    by construction. overrideMemory is MB, read as Mi, formatted the way every
    other manifest quantity is (4096 -> 4Gi, 8196 -> 8196Mi)."""
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
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == gen.ENGINE_DEFAULT_CPU
    # An explicit option outranks the location: the CLI, a livetest overlay
    # and a replayed profile all speak through options.
    explicit = gen.generate(facts, {"namespace": "ns1",
                                    "engine_cpu_limit": "2",
                                    "engine_mem_limit": "8Gi"})
    cm = yaml.safe_load(explicit["bzm_configmap.yaml"])["data"]
    assert cm["KUBERNETES_RESOURCES_LIMITS_CPU"] == "2"
    assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == "8Gi"


def test_an_override_memory_below_an_engines_floor_is_not_derived():
    """overrideMemory's unit is unreliable -- one real account holds 32, 4000
    and 8196 -- and a derived limit of 4Mi is an OOMKill this derivation would
    be introducing: the incident it exists to fix, upside down. Below the
    floor the memory half falls back to the default; the CPU half still
    derives; and an *explicit* option is the user's own and is never floored."""
    for mb in (4, 32, 512):
        files = gen.generate(
            {**FACTS, "override_cpu": 1, "override_memory": mb},
            {"namespace": "ns1"})
        cm = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
        assert cm["KUBERNETES_RESOURCES_LIMITS_MEMORY"] == gen.ENGINE_DEFAULT_MEM, mb
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
    """The profile records *resolved* options, so the derivation resolves into
    it: a replay against different facts -- the location was resized since the
    bundle was cut -- must reproduce the bundle byte-for-byte, not re-derive."""
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
    """The two keys are ignored by docker, so deriving them would only add a
    README line about an option nobody set: the derivation asks
    ignored_options() like every other reader of the pair, and a docker
    bundle's README states the size it actually carries (the default, via
    engine_size)."""
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
    in the project README. It used to run to 52 lines of rationale.

    Measured on a *finished* bundle. The placeholder block is the one section
    whose length is not creep -- it appears only when a field was left blank,
    and it is bounded by the number of blank fields rather than by anyone's
    appetite for prose (test_placeholder_block_is_bounded holds it to that).
    Counting it here would have this guard fire on a bundle that is doing
    exactly what it should."""
    readme = gen.generate(
        FACTS, {"namespace": "ns1", "auth_token": "de" * 32})["README.md"]
    assert "not finished" not in readme
    assert len(readme.splitlines()) < 45, "README is creeping back towards an essay"
    # The four things someone needs: what this is, how to deploy, how to check,
    # and what it costs to run.
    assert "apply -f bzm_deployment.yaml" in readme
    assert "rollout status deploy/crane" in readme
    assert "online" in readme
    assert gen.ENGINE_STAMPED_REQUEST_CPU in readme     # the engine request gap
    assert "bzm_limitrange.yaml" not in readme


def _commands(files):
    """Every line of every emitted document that tells somebody to run a cluster
    command, as one blob -- the bundle is read as one document and a README that
    applies with `oc` beside a recipe that labels with `kubectl` is one of the
    two being wrong."""
    return "\n".join(v for k, v in files.items()
                     if k.endswith(".md") or k.endswith(".sh"))


def test_the_cluster_decides_oc_or_kubectl_not_the_posture():
    """`platform` says who assigns the UID, which is a posture that installs on
    vanilla Kubernetes too -- so it cannot also answer which binary the person
    deploying has. Before `openshift_cluster` it was answering both, and the
    default posture printed `oc` at a plain Kubernetes customer.

    The two-pool bundle is used because the node-pool recipe is where most of
    the emitted commands are; the README carries the rest."""
    opts = {"namespace": "ns1", "auth_token": "de" * 32,
            "node_selector": CRANE_POOL, "engine_node_selector": ENGINE_POOL,
            "engine_tolerations": ENGINE_TOL}
    oc = _commands(gen.generate(FACTS, opts))
    assert "oc -n ns1 rollout status" in oc and "oc label node" in oc
    assert "kubectl " not in oc

    plain = _commands(gen.generate(FACTS, dict(opts, openshift_cluster=False)))
    assert "kubectl -n ns1 rollout status" in plain and "kubectl label node" in plain
    assert "oc " not in plain
    # ...and the pinned-UID posture names its own cluster, so it answers alone.
    assert "oc " not in _commands(gen.generate(FACTS, dict(opts, platform="k8s")))


# -- blank required fields ----------------------------------------------------
#
# A field somebody left empty resolves to its own marker rather than to an
# empty string or a refusal. The empty string is what these are really about:
# every one of them had a plausible-looking failure ("" namespace -> the
# manifests apply into whatever namespace the command names, "" service account
# -> the namespace's `default`, "" token -> a pod that reads as a slow boot),
# and the marker converts all of them into one loud, early, named failure.


def test_a_finished_bundle_carries_no_marker():
    files = gen.generate(FACTS, {"namespace": "ns1", "auth_token": "de" * 32})
    assert gen.placeholder_options(json.loads(files[gen.PROFILE_FILE])) == []
    assert not gen.MARKER_RE.search("".join(files.values()))


def test_the_marker_reaches_the_objects_that_name_the_field():
    """Not only the README: the point is that applying it fails. `<NAMESPACE>`
    is not a legal RFC 1123 name, so each of these is rejected by the API server
    with the field named -- and it names the field twice over, since the marker
    is the field's own key."""
    files = gen.generate(FACTS, {"namespace": "", "ship_id": "bbb222"})
    assert yaml.safe_load(
        files["bzm_deployment.yaml"])["metadata"]["namespace"] \
        == gen.marker("namespace")
    assert "apply -f" in files["README.md"]


# The third is the marker every field carried before #245. A profile written
# by an older version of this generator still holds it, and reading it back as
# a value somebody meant is the failure this whole mechanism exists to prevent.
@pytest.mark.parametrize("given",
                         ["<NAMESPACE>", "  <NAMESPACE>  ", "<PLACEHOLDER>"])
def test_the_marker_is_recognised_around_whitespace(given):
    """A form hands back what was pasted, spaces included. A marker that stopped
    being one on a stray space would be carried into the bundle as a value
    somebody meant, which is the single failure this whole mechanism exists to
    prevent."""
    assert gen.is_placeholder(given)
    assert gen.placeholder_options({"namespace": given}) == ["namespace"]


def test_docker_does_not_mark_the_fields_it_ignores():
    """Same rule as everywhere else here: a format may not refuse -- or demand,
    or complain about -- what it says it ignores. A docker bundle has no
    namespace and no ServiceAccount, and marking them would put two fields in a
    README that the page for that format deliberately does not show."""
    files = gen.generate(FACTS, {**DOCKER, "namespace": "",
                                 "service_account_name": "",
                                 "auth_token": "de" * 32})
    assert gen.placeholder_options(json.loads(files[gen.PROFILE_FILE])) == []
    assert "not finished" not in files["README.md"]


def test_a_marker_the_page_supplied_is_reported_too():
    """The page holds the switch for a private registry, a proxy and a CA
    ConfigMap, so blank-but-wanted is a state only it can see -- it sends the
    marker itself. Found by reading the value, not by consulting REQUIRED_TEXT,
    which is what lets the two halves share one report."""
    o = {"namespace": "ns1", "auth_token": "de" * 32,
         "private_registry": gen.marker("private_registry"),
         "proxy": {"https": gen.marker("proxy.https"),
                   "no_proxy": "localhost"}}
    files = gen.generate(FACTS, o)
    assert gen.placeholder_options(json.loads(files[gen.PROFILE_FILE])) == [
        "private_registry", "proxy.https"]
    readme = files["README.md"]
    assert "`private_registry`" in readme and "`proxy.https`" in readme


def test_placeholder_block_is_bounded_by_the_fields_not_the_prose():
    """The one section that may lengthen the README, held to the thing it is
    reporting. Two blank fields is two rows more than none, not an essay."""
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
    """`generate --profile` replays a bundle exactly, and a bundle that was not
    finished is one of the things it has to replay faithfully. Silently
    re-defaulting the field would produce a *different* bundle from the same
    profile, and the marker is precisely the value nobody chose."""
    files = gen.generate(FACTS, {"namespace": "", "ship_id": "bbb222"})
    prof = json.loads(files[gen.PROFILE_FILE])
    assert prof["namespace"] == gen.marker("namespace")
    replayed = gen.generate(FACTS, prof)
    assert yaml.safe_load(
        replayed["bzm_deployment.yaml"])["metadata"]["namespace"] \
        == gen.marker("namespace")


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
    (`.../blazemeter/charmander/chrome_136...`), so they are where a rule that
    reduces the path shows first. The map and the mirror have to agree: if they
    drift, the mirror pushes one name while IMAGE_OVERRIDES tells crane to pull
    another, and nothing between here and a run says so."""
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
    options for a functionality that no longer exists, and not pull an image
    for it."""
    retired = dict(FACTS, func_ids=["performance", "sv-bridge"])
    files = gen.generate(retired, {"namespace": "ns1"})          # no ingress needed
    assert "KUBERNETES_WEB_EXPOSE_TYPE" not in yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]
    assert not [i for i in gen.select_images(retired)
                if "sv-bridge" in i["repo"]]


def test_sv_ingress_marks_a_missing_subdomain_and_tls_secret():
    """Both are still mandatory -- the TLS secret even though the virtual
    service is HTTP -- and both now say so in the bundle instead of refusing to
    produce one. The Ingress carries the marker into `host` and `secretName`,
    neither of which the API server will accept, so the combination that used to
    fail silently on a cluster still cannot reach one."""
    files = gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx"})
    assert gen.placeholder_options(json.loads(files[gen.PROFILE_FILE])) == [
        "sv_subdomain", "sv_tls_secret"]
    readme = files["README.md"]
    assert "sv_subdomain" in readme and "sv_tls_secret" in readme
    # ...and one supplied is one not marked.
    files = gen.generate(SV_FACTS, {"namespace": "ns1", "sv_ingress": "nginx",
                                    "sv_subdomain": "apps.example.com"})
    assert gen.placeholder_options(
        json.loads(files[gen.PROFILE_FILE])) == ["sv_tls_secret"]


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


def test_sv_readme_names_the_tls_secret_and_the_namespace_it_goes_in():
    """#185: the one prerequisite nothing in the bundle creates and nothing
    reports missing. Measured against crane's own Ingress -- the secret is
    resolved in the agent's namespace, and its absence still serves."""
    md = gen.generate(SV_FACTS, dict(SV_OPTS, namespace="bzm-agent",
                                     platform="kubernetes"))["README.md"]
    assert "wildcard-tls" in md
    assert "kubectl -n bzm-agent create secret tls wildcard-tls" in md
    assert "`*.apps.example.com`" in md
    # The bundle is read as one document, so this line follows whichever CLI the
    # rest of it applies with -- see gen.cli().
    oc = gen.generate(SV_FACTS, dict(SV_OPTS, namespace="bzm-agent",
                                     platform="openshift"))["README.md"]
    assert "oc -n bzm-agent create secret tls wildcard-tls" in oc
    # BlazeMeter's page is contradicted by name, or a reader who has it open
    # follows it instead.
    assert "`default`" in md
    # And the failure mode, which is the reason the bullet is worth the space.
    assert "200" in md.split("wildcard-tls")[-1]


@pytest.mark.parametrize("ingress", ["istio", "openshift"])
def test_sv_readme_is_silent_where_the_backend_never_reads_the_secret(ingress):
    """Naming a namespace for something nothing looks at is an instruction that
    gets followed and then disbelieved. Both of these require the *name* --
    crane crash-loops without it -- and neither references the Secret."""
    o = dict(SV_OPTS, sv_ingress=ingress)
    if ingress == "openshift":
        o["platform"] = "openshift"
    md = gen.generate(SV_FACTS, o)["README.md"]
    assert "create secret tls" not in md


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
    deploy cleanly and then stall with nothing to create.

    Both ways of saying "not OpenShift" are refused, because they are two
    answers and only one of them used to be asked: the pinned-UID posture, and
    the SCC-friendly posture on a cluster that is not OpenShift -- which is the
    combination the default posture makes easy to reach."""
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


# -- the docker format --------------------------------------------------------
#
# One agent, one container. Every check below is about the two things a bundle
# that "looks right" still gets wrong: a command that is not the shape
# BlazeMeter's own documentation gives, and a Kubernetes option silently read as
# applied when a docker host has nowhere to put it.

DOCKER = {"output_format": "docker", "ship_id": "bbb222",
          "auth_token": "de" * 32}


def docker_sh(**opts):
    return gen.generate(FACTS, {**DOCKER, **opts})["bzm-opl-agent.sh"]


def docker_compose(**opts):
    return gen.generate(FACTS, {**DOCKER, **opts})[gen.DOCKER_COMPOSE_FILE]


def test_docker_command_is_the_documented_shape():
    """BlazeMeter's Docker Command tab and their installation page give this
    command; api.parse_auth_token at the top of this file parses their version
    of it. What this generator adds is the bundle's settings -- the shape has to
    stay theirs, because their documentation is what a customer reads next."""
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
    assert "NAME=bzm-crane-bbb222" in sh
    # Which manager this agent is for, stated rather than defaulted -- the
    # Kubernetes ConfigMap states its own the same way.
    assert "--env CONTAINER_MANAGER_TYPE=DOCKER" in sh
    # And it is the crane image the account reports, not a guess.
    assert FACTS["crane_image"] in sh


def test_docker_runs_as_root_so_it_can_open_the_socket():
    """The bug this exists for, from a real host.

    The crane image runs as a non-root user and `/var/run/docker.sock` is
    root:docker 0660 on a stock daemon, so without `-u 0` the container starts,
    reaches the socket and dies:

        docker.errors.DockerException: Error while fetching server API version:
        ('Connection aborted.', PermissionError(13, 'Permission denied'))

    -- a Python traceback about a unix socket, which names neither the uid that
    could not open it nor the flag that would have. Starting engines through
    that socket is the only thing this agent does, so this is not a preference:
    the bundle was unusable without it, and BlazeMeter's own generated command
    has carried `-u 0` all along. Built from their documentation, which does not
    mention it, this did not.

    Asserted in every branch, because a flag that survives only the default one
    is a flag one option away from going missing again."""
    for opts in ({}, {"use_secret": False}, {"proxy": {"http": "http://p:3128"}},
                 {"ca_bundle": "-----BEGIN CERTIFICATE-----"},
                 {"private_registry": "reg.corp/bzm"}):
        assert "-u 0" in docker_sh(**opts), opts


def test_docker_hands_its_engines_a_port_range():
    """`--net=host` makes an engine's ports the *host's* ports, and BlazeMeter's
    own command always names the range. Left out, the range is whatever crane
    defaults to -- which is not visible in the bundle, and so is not a thing the
    operator whose host it is can check or change."""
    for sh in (docker_sh(), docker_sh(use_secret=False)):
        assert f"DOCKER_PORT_RANGE={gen.DOCKER_PORT_RANGE}" in sh
    # In the command, not the env file: it is configuration, not a credential.
    bundle = gen.generate(FACTS, DOCKER)
    assert "DOCKER_PORT_RANGE" not in bundle[gen.DOCKER_ENV_FILE]


def test_docker_scripts_are_valid_shell():
    """Every combination of the four options that change the script's control
    flow. It is a generated shell script: a quoting mistake in one branch is
    invisible until somebody runs that branch on a customer's host."""
    import itertools
    import subprocess
    for secret, ca, proxy, reg, blank in itertools.product([True, False], repeat=5):
        o = dict(DOCKER, use_secret=secret)
        if blank:
            # A field left blank adds a refusal per variable, in whichever of
            # the two files holds it -- and the pattern it greps for carries
            # a character class while the message carries quotes, which is
            # exactly
            # the shape a quoting mistake hides in.
            o["auth_token"] = gen.marker("auth_token")
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
    # ...and the mounted-file branches, which the product above cannot reach:
    # the guard over a file left blank is a second `if` inside the mount check,
    # and its message names two files and a variable in one echo.
    for extra in ({"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT,
                   "sv_tls_key": SV_KEY},
                  {"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT,
                   "sv_tls_key": ""},
                  {"sv_hostname": SV_HOST, "sv_tls_key": SV_KEY},
                  {"ca_bundle": gen.marker("ca_bundle")}):
        sh = gen.generate(FACTS, {**DOCKER, **extra})["bzm-opl-agent.sh"]
        r = subprocess.run(["sh", "-n", "-"], input=sh, text=True,
                           capture_output=True)
        assert r.returncode == 0, (extra, r.stderr)


def _run_bundle(tmp_path, files=None, env=None):
    """Write a docker bundle out and run its script against a stub docker.
    Called with no files, it re-runs whatever is in the directory now, which is
    how the fill-it-in half of these tests is expressed.

    `env` is the other way a bundle is finished, and the one only the mounted
    files have: every one of them is overridable to a path the host already
    keeps, so a run with `SV_TLS_KEY` set is a customer taking the escape hatch
    rather than editing the bundle.

    A stub rather than a daemon, and it is the point of the test: what is being
    checked is that the script refuses *before* it reaches `docker run`, so the
    thing that has to be observable is the argument list docker was never given.
    `docker ps` printing nothing is a host with no such container, which is the
    branch every other check here is downstream of."""
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
    """The hole this covers is `auth_token`'s and it predates compose.

    On Kubernetes the angle brackets are the guard -- the API server refuses the
    object and names the field -- and that is what makes a blank field safe to
    allow at all. `docker run` has no such opinion: an environment variable is a
    string to it, so the marker used to reach crane, which answers 404 and logs
    `Sleeping for 300` while looking like a slow boot. There is no API server on
    this platform, so the check is in the artefact."""
    files = gen.generate(FACTS, {**DOCKER, "auth_token": ""})
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 1
    assert "AUTH_TOKEN carries <AUTH_TOKEN>" in r.stderr
    # ...and the file to edit, which is the half a refusal without it leaves the
    # reader to guess -- the credential is not in the script.
    assert "Set it in bzm-opl-agent.env" in r.stderr
    assert made == ["ps"], made           # nothing was started

    # Filled in, the same bundle runs. The check reads the files as they stand
    # rather than what was blank when this was generated, so there is no second
    # step and nothing to delete: a guard that outlived its own fix would be a
    # bundle that could never be finished by hand.
    (tmp_path / "bzm-opl-agent.env").write_text("AUTH_TOKEN=" + "ab" * 32 + "\n")
    r, made = _run_bundle(tmp_path)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "ps", "run"], made


def test_docker_script_refuses_an_inline_placeholder_too(tmp_path):
    """With `use_secret` off the value is in the script itself, so the check is
    over its own run line -- anchored there, which is also what stops it
    matching the marker in its own message two lines below."""
    files = gen.generate(FACTS, {**DOCKER, "use_secret": False,
                                 "auth_token": "",
                                 "private_registry":
                                     gen.marker("private_registry")})
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


def test_a_finished_docker_bundle_carries_no_refusal(tmp_path):
    """Nothing is stated about a field nobody left blank. The check is emitted
    per variable that carries the marker, so the ordinary bundle is the script
    it was before this existed."""
    files = gen.generate(FACTS, DOCKER)
    assert not gen.MARKER_RE.search(files["bzm-opl-agent.sh"])
    assert not gen.MARKER_RE.search(files[gen.DOCKER_COMPOSE_FILE])
    assert "BZM_OPL_UNSET" not in files[gen.DOCKER_ENV_FILE]
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "run"], made


def test_docker_use_secret_keeps_the_token_out_of_the_process_list():
    """`use_secret` means the same thing here as for Kubernetes -- the
    credential lives apart from the configuration -- and docker's mechanism for
    it is --env-file. With it on, a value passed with --env would be in the
    host's process list for anyone running `ps`."""
    files = gen.generate(FACTS, DOCKER)
    sh, env = files["bzm-opl-agent.sh"], files["bzm-opl-agent.env"]
    # The value, not the name: the script names the variable in the sentence
    # explaining what the missing file holds, which is not the same as carrying
    # it.
    assert "de" * 32 not in sh
    assert '--env-file "$ENV_FILE"' in sh
    assert "AUTH_TOKEN=" + "de" * 32 in env
    # Off, it is inline and there is no second file -- BlazeMeter's own shape.
    plain = gen.generate(FACTS, {**DOCKER, "use_secret": False})
    assert "bzm-opl-agent.env" not in plain
    assert "--env AUTH_TOKEN=" + "de" * 32 in plain["bzm-opl-agent.sh"]


def test_docker_proxy_credentials_move_with_the_token():
    """A proxy URL carries user:password (see proxy_url), so it is a credential
    too -- the same rule the Kubernetes Secret follows."""
    o = dict(DOCKER, proxy={"http": "http://p:1", "https": "http://p:1",
                            "username": "u", "password": "pw"})
    files = gen.generate(FACTS, o)
    assert "HTTP_PROXY" not in files["bzm-opl-agent.sh"]
    assert "HTTP_PROXY=http://u:pw@p:1" in files["bzm-opl-agent.env"]
    # NO_PROXY is not a credential and stays in the command -- and it is the
    # docker default, not the cluster one: `kubernetes.default` is the API
    # service and resolves to nothing on a docker host.
    assert "kubernetes.default" not in files["bzm-opl-agent.sh"]
    assert "127.0.0.1,localhost" in files["bzm-opl-agent.sh"]


def test_docker_ca_bundle_is_mounted_where_the_variables_point():
    """BlazeMeter's CA page fixes the container path: the bundle replaces the
    container's own store at /etc/ssl/certs/ca-certificates.crt, and both
    REQUESTS_CA_BUNDLE and AWS_CA_BUNDLE have to name it."""
    files = gen.generate(FACTS, {**DOCKER, "ca_bundle": "PEM\n"})
    sh = files["bzm-opl-agent.sh"]
    assert files["ca-bundle.crt"] == "PEM\n"
    assert '-v "$CA_BUNDLE":/etc/ssl/certs/ca-certificates.crt:ro' in sh
    assert "--env REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in sh
    assert "--env AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" in sh


def test_docker_auto_update_is_the_docker_variable_and_off_unless_asked_for():
    """AUTO_UPDATE, not AUTO_KUBERNETES_UPDATE -- a different variable for a
    different mechanism, and **off by default** like the Kubernetes one (#222).

    It was left out unless answered, on the reasoning that there is no
    Deployment here for a self-update to fight over. There is something else:
    the image tags on the host. From the pinned mirror reference this generator
    writes, crane's updater removes `blazemeter/crane:<version>`, retags,
    renames, fails with `Failed to reload crane` and retries forever -- the
    agent stays healthy and idle while every test stalls at BOOT_STARTING with
    no engine. Measured: unset stalls, `false` runs the test."""
    assert "--env AUTO_UPDATE=false" in docker_sh()
    assert "--env AUTO_UPDATE=true" in docker_sh(auto_update=True)
    assert "--env AUTO_UPDATE=false" in docker_sh(auto_update=False)
    assert "AUTO_KUBERNETES_UPDATE" not in docker_sh(auto_update=True)


# -- ...and the same container as a compose project ---------------------------
#
# Compose buys no capability -- for one container it adds nothing `docker run`
# cannot do -- so every check below is about the two files staying one container
# rather than becoming two agents, and about the file compose reads that nobody
# meant it to.

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
    # A field left blank, in each of the two files it can land in: the guard is
    # a `${...:?}` carrying a sentence with punctuation in it, which is the
    # shape a YAML quoting mistake hides in.
    {"auth_token": gen.marker("auth_token")},
    {"auth_token": gen.marker("auth_token"), "use_secret": False},
    # Service virtualization, which is two more mounted files and three more
    # variables -- the case #182 added and the one a parity check written
    # before it would have passed vacuously.
    {"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY},
    # ...and the hostname alone, which is a real configuration (the endpoints
    # are plain HTTP) and writes the variable without either mount.
    {"sv_hostname": SV_HOST},
    # A blank hostname beside a certificate: HOSTNAME_OVERRIDE carries the
    # marker, so this is the guard in the *inline* file for a variable that is
    # not the token.
    {"sv_hostname": "", "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY},
    # ...and a blank half of the TLS pair, which is the guard over a mounted
    # *file* rather than over a variable. It is the case the two guards
    # disagreed about: the marker is inside sv-tls.key, so no environment value
    # carries it and the variable-level check saw a finished bundle.
    {"sv_hostname": SV_HOST, "sv_tls_cert": SV_CERT, "sv_tls_key": ""},
]


def test_the_docker_bundle_carries_both_routes_to_one_container():
    """Not a fourth output_format: a format is a platform, and these are two
    syntaxes for the same one. Some customers install with compose and will not
    take a script, so which file they use is theirs and the bundle carries
    both."""
    files = gen.generate(FACTS, DOCKER)
    assert gen.DOCKER_RUN_FILE in files and gen.DOCKER_COMPOSE_FILE in files
    # The script leads everywhere it is listed -- BlazeMeter's own shape is the
    # one their documentation describes, which is the tie-break this repo uses.
    order = gen.preview_order(list(files))
    assert order.index(gen.DOCKER_RUN_FILE) < order.index(gen.DOCKER_COMPOSE_FILE)


def test_compose_is_valid_yaml_in_every_branch():
    """The counterpart of test_docker_scripts_are_valid_shell. A generated file
    with a quoting mistake in one branch is invisible until somebody runs that
    branch on a customer's host -- and here it is a YAML value carrying a quote,
    a space or a backslash out of a proxy password or a free-form variable."""
    for extra in COMPOSE_CASES:
        doc = yaml.safe_load(docker_compose(**extra))
        svc = doc["services"][gen.DOCKER_COMPOSE_SERVICE]
        assert svc["image"] == FACTS["crane_image"] or extra.get("private_registry")
        assert svc["environment"]["HARBOR_ID"] == "aaa111"


def test_compose_is_v2_and_names_its_own_project():
    """`version:` has been obsolete since Compose v2 and a file carrying one is
    warned about on every command. The project name is the other half: left out,
    compose takes it from whatever directory the customer unzipped into, so the
    same bundle is one project or two depending on the file manager."""
    text = docker_compose()
    assert "version:" not in text
    doc = yaml.safe_load(text)
    assert doc["name"] == gen.docker_container_name("bbb222")
    assert set(doc["services"]) == {gen.DOCKER_COMPOSE_SERVICE}


def test_both_routes_carry_the_same_container_name():
    """The either/or rule, and the reason it is not a README warning. Run both
    and two cranes hold one agent identity, which BlazeMeter reports as
    duplicated results rather than as an error; a name collision fails at
    `compose up` with the name in the message. Verified live in both directions
    against a real daemon: `Conflict. The container name "/bzm-crane-..." is
    already in use`, and the script's own guard the other way round."""
    name = gen.docker_container_name("bbb222")
    files = gen.generate(FACTS, DOCKER)
    svc = yaml.safe_load(files[gen.DOCKER_COMPOSE_FILE])["services"]
    assert svc[gen.DOCKER_COMPOSE_SERVICE]["container_name"] == name
    assert f"NAME={name}" in files[gen.DOCKER_RUN_FILE]


def test_compose_reads_the_credential_file_the_script_does():
    """One credential file, not a copy: `use_secret` decides where the token is
    written and both routes read whatever that answer was."""
    files = gen.generate(FACTS, DOCKER)
    svc = yaml.safe_load(files[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["env_file"] == [f"./{gen.DOCKER_ENV_FILE}"]
    # The value, like the script: with use_secret on it is in neither file.
    assert "de" * 32 not in files[gen.DOCKER_COMPOSE_FILE]
    assert "AUTH_TOKEN" not in svc["environment"]
    # Off, there is no env file to point at and the token is inline in both --
    # BlazeMeter's own shape, and the same shape twice.
    plain = gen.generate(FACTS, {**DOCKER, "use_secret": False})
    svc = yaml.safe_load(plain[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert "env_file" not in svc
    assert svc["environment"]["AUTH_TOKEN"] == "de" * 32


def test_no_docker_bundle_holds_a_file_called_dot_env():
    """The trap the compose file's own header is about. Compose auto-loads a
    `.env` for variable interpolation into the compose file rather than into the
    container, so an AUTH_TOKEN written there never reaches crane while looking
    exactly as though it had -- and a `$` in a proxy password is substituted on
    the way past. The credential file is `bzm-opl-agent.env` for that reason, so
    this holds across every branch rather than at the one place it is named."""
    for extra in COMPOSE_CASES:
        files = gen.generate(FACTS, {**DOCKER, **extra})
        assert ".env" not in files
        assert not [f for f in files if f.endswith("/.env")]
        # ...and the compose file says why, in both branches -- with the
        # credential split out there is a file sitting there to be renamed,
        # without it there is only the one somebody is about to create.
        assert "`.env`" in files[gen.DOCKER_COMPOSE_FILE]


def test_compose_escapes_a_dollar_so_it_reaches_the_container():
    """Compose interpolates `$VAR` and `${VAR}` in this file's own values before
    parsing them, so a literal `$` has to be doubled or it is substituted --
    usually by nothing, and silently. `--env` in the script beside it passes the
    same value through untouched, so an unescaped compose file would start an
    agent authenticating with a shorter password and no error anywhere.

    Confirmed against a real daemon: `a$$b$${HOME}c` in the file arrives in the
    container as `a$b${HOME}c`.

    Escaped rather than moved into the env file: which variables live there is
    `use_secret`'s answer, and a value that changed file depending on its
    punctuation is a bundle nobody could reason about."""
    text = docker_compose(extra_env={"PREFERRED_INTERFACE": "a$b${HOME}c"})
    assert 'PREFERRED_INTERFACE: "a$$b$${HOME}c"' in text
    # ...and it is still one value to whatever reads the YAML.
    svc = yaml.safe_load(text)["services"]["crane"]
    assert svc["environment"]["PREFERRED_INTERFACE"] == "a$$b$${HOME}c"


def test_compose_restates_the_fixed_half_of_the_command():
    """user, network, restart, mounts, workdir and entrypoint are the same fact
    stated twice, so both sides read the constant rather than a literal of their
    own -- `-u 0` is the flag that already went missing once, and it would go
    missing here first. This is the shape, one side at a time;
    test_compose_and_docker_run_describe_the_same_container holds the two
    against each other over the whole option matrix."""
    svc = yaml.safe_load(docker_compose())["services"]["crane"]
    assert svc["user"] == gen.DOCKER_USER
    assert svc["restart"] == gen.DOCKER_RESTART
    assert svc["network_mode"] == gen.DOCKER_NETWORK
    assert svc["working_dir"] == gen.DOCKER_WORKDIR
    assert svc["command"] == gen.DOCKER_ENTRYPOINT
    assert svc["volumes"] == gen.DOCKER_MOUNTS
    # The CA mount is conditional in the script and conditional here, and it
    # keeps the same override: a host may already keep the trust bundle its
    # platform team maintains. Compose resolves the relative default against
    # this file's directory, which is what `$DIR` means in the script.
    with_ca = yaml.safe_load(docker_compose(ca_bundle="PEM\n"))["services"]["crane"]
    assert with_ca["volumes"][-1] == (
        f"${{CA_BUNDLE:-./{gen.DOCKER_CA_FILE}}}:{gen.DOCKER_CA_PATH}:ro")


def test_compose_refuses_a_placeholder_in_the_same_words_as_the_script():
    """The other half of the refusal, and the reason it is not a README note.

    Compose has no pre-flight and no shell of its own: a check inside the
    container is one `docker compose up -d` never prints. What it does have is
    interpolation, and `${X:?message}` aborts the command before anything is
    created, naming the field's path in the file and then this message.
    Verified live against a real compose (5.1.4):

        error while interpolating services.crane.environment.AUTH_TOKEN:
        required variable BZM_OPL_UNSET_AUTH_TOKEN is missing a value:
        AUTH_TOKEN carries <AUTH_TOKEN> -- ...

    One wording for both routes, so a customer reads the same sentence about the
    same variable and the same file whichever file they started from."""
    wrong, todo = gen._docker_blank_lines("AUTH_TOKEN", gen.DOCKER_ENV_FILE,
                                          gen.marker("auth_token"))
    files = gen.generate(FACTS, {**DOCKER, "auth_token": ""})
    # Split out, the credential is in the one file both routes read -- so that
    # is where the guard sits. Compose interpolates env_file values; docker's
    # own --env-file does not, and the script refuses the marker inside the
    # message before that literal could reach a container.
    env = files[gen.DOCKER_ENV_FILE]
    assert env.startswith("# Read by docker --env-file")
    assert f"AUTH_TOKEN=${{BZM_OPL_UNSET_AUTH_TOKEN:?{wrong} {todo}}}" in env
    for line in (wrong, todo):
        assert line in files[gen.DOCKER_RUN_FILE]
    # A guard in compose.yaml over a value living in the env file would go on
    # refusing after somebody had filled it in -- nothing in that file can see
    # that they had, and a check that outlives its own fix is worse than none.
    assert "AUTH_TOKEN" not in yaml.safe_load(
        files[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]["environment"]


def test_compose_refuses_an_inline_placeholder_at_the_value_itself():
    """Inline, the value is compose's own, so the guard is the value -- and both
    files name both files, because an inline value is in both of the two that
    start this container."""
    where = f"{gen.DOCKER_RUN_FILE} and {gen.DOCKER_COMPOSE_FILE}"
    wrong, todo = gen._docker_blank_lines("AUTH_TOKEN", where,
                                          gen.marker("auth_token"))
    files = gen.generate(FACTS, {**DOCKER, "auth_token": "", "use_secret": False})
    svc = yaml.safe_load(files[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["environment"]["AUTH_TOKEN"] == (
        f"${{BZM_OPL_UNSET_AUTH_TOKEN:?{wrong} {todo}}}")
    for line in (wrong, todo):
        assert line in files[gen.DOCKER_RUN_FILE]
    # The guard's variable is one nobody has. `${AUTH_TOKEN:?...}` would read the
    # ambient environment, and `${HTTP_PROXY:?...}` would resolve itself away on
    # the host most likely to have one -- leaving compose starting a bundle the
    # script beside it refuses, which is the two routes disagreeing about
    # whether the bundle is finished.
    proxy = gen.generate(FACTS, {**DOCKER, "use_secret": False,
                                 "proxy": {"http": gen.marker("proxy.http")}})
    svc = yaml.safe_load(proxy[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["environment"]["HTTP_PROXY"].startswith("${BZM_OPL_UNSET_HTTP_PROXY:?")


# -- ...and the two files are held equal, over the whole option matrix --------
#
# This is helm_parity.py's problem in miniature (#178). Every judgement in
# templates/*.yaml is restated in Go templates and nothing but that script
# notices one drifting; here `-u 0`, `--net=host`, the two mounts, the restart
# policy, the workdir and the entrypoint are restated in two languages, and the
# checks above assert each side against a constant rather than against the other
# side. A constant both renderers read is what makes the comparison cheap -- it
# is not what performs it, and a value written into one file alone would never
# have a constant to be caught by.
#
# It is pytest rather than a script beside helm_parity.py, and that is the whole
# of the difference between the two: helm parity shells out to `helm`, and a
# suite that skips when a binary is missing reports a clean pass having tested
# nothing (the fastapi lesson). Both sides here are built in Python from one
# call to generate(), so there is nothing to be missing, and a check that can
# run in the offline suite belongs in it.
#
# What CI adds instead is the question this cannot answer: `docker compose
# config -q` over a generated bundle proves compose *accepts* the file. Two
# python dicts can agree perfectly about a document compose refuses to parse.

# What a compose service may say about this container, compared as a *set*
# rather than read key by key. The failure this exists for is a judgement made
# in one file alone, and a key added here that nothing below holds against the
# script is exactly that -- it would pass a field-by-field comparison by never
# being looked at. `env_file` is separate because it is use_secret's answer
# rather than a fixed part of the shape.
COMPOSE_SERVICE_KEYS = {"image", "container_name", "user", "restart",
                        "network_mode", "working_dir", "environment",
                        "volumes", "command"}
# Compose's own required-variable expression, which is how a blank value is
# written in the file that has no shell to check it in (see _compose_required).
COMPOSE_GUARD = "${BZM_OPL_UNSET_"


def _blank_mount_vars(sh):
    """The mounted files `bzm-opl-agent.sh` refuses as unfinished, by variable.

    Matched on the whole line so it cannot pick up the *environment* guard,
    which greps the marker too -- that one is anchored to a run line or an env
    file (`"$0"`, `"$ENV_FILE"`) and this one names the mount's own variable,
    which is exactly the difference between the two halves of the check."""
    return set(re.findall(
        r"^if grep -q '" + re.escape(gen.MARKER_PATTERN)
        + r"' \"\$([A-Z][A-Z0-9_]*)\"; then$", sh, re.M))


def _blank_mount(var):
    """One name for "this mount was left blank", written by both parsers.

    The two files cannot say it the same way -- the script mounts the resolved
    path and refuses on its *content*, compose drops the bind source's default
    and refuses on the *variable* -- so comparing the strings would only ever
    report the difference in mechanism. Reduced to this, a mount guarded on one
    side and not the other is a plain mount diff, which is the thing worth
    failing on."""
    return f"<blank:{var}>"


def _env_file_env(files):
    """The credential file as {name: value}. Docker parses it itself -- one
    NAME=value per line, no quoting and no shell -- and compose reads the same
    file through `env_file:`, so this half is one text read twice."""
    text = files.get(gen.DOCKER_ENV_FILE)
    return dict(line.split("=", 1) for line in (text or "").splitlines()
                if line and not line.startswith("#"))


def _script_paths(sh):
    """The sibling files the script names, as `$VAR` -> bundle-relative path.

    Both routes resolve them against their own file's directory -- `$DIR` in the
    script, the compose file's directory for a relative bind mount -- so
    stripping that prefix from each side is what makes them comparable rather
    than a normalisation that hides a difference. CA_BUNDLE keeps its default
    because that is the value the container gets when nobody overrides it, which
    is the same thing `${CA_BUNDLE:-./ca-bundle.crt}` says next door.
    """
    out = {}
    # Every variable the script assigns at the top, rather than three named
    # ones: each mounted file adds one (generate.docker_file_mounts), and a
    # pattern that had to be extended per file is one that silently stops
    # resolving the mount it was not told about -- which reads here as a
    # difference between the two files rather than as a gap in this parser.
    for name, raw in re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", sh, re.M):
        value = raw.strip('"')
        default = re.fullmatch(r"\$\{" + name + r":-(.*)\}", value)
        out["$" + name] = (default.group(1) if default else value).replace("$DIR/", "")
    return out


def _container_from_script(files):
    """The container `bzm-opl-agent.sh` starts, as fields.

    Read out of the generated file rather than from `_docker_run_lines`
    directly: the file is what a customer runs, and the command reaching it
    intact is part of what the two files agreeing is worth.

    An argument this does not know about ends up as the image, so a flag added
    to one side fails loudly on `image` rather than being skipped -- the same
    rule the compose key set above states from the other direction.
    """
    sh = files[gen.DOCKER_RUN_FILE]
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
    """The same container as compose describes it.

    Two differences here are representation rather than substance, and both are
    undone: compose interpolates its own values, so every `$` is written `$$`
    where `--env` passes the string through untouched; and a relative host path
    is resolved against this file's directory, which is what `$DIR` means in the
    script. A guarded value is left as it stands -- it is not a value at all,
    and comparing it is the caller's business below.
    """
    doc = yaml.safe_load(files[gen.DOCKER_COMPOSE_FILE])
    assert set(doc) == {"name", "services"}, sorted(doc)
    svc = doc["services"][gen.DOCKER_COMPOSE_SERVICE]
    assert set(svc) - {"env_file"} == COMPOSE_SERVICE_KEYS, (
        f"compose says {sorted(set(svc) - COMPOSE_SERVICE_KEYS - {'env_file'})} "
        f"about this container and nothing holds it against {gen.DOCKER_RUN_FILE}")
    inline = {k: v if v.startswith(COMPOSE_GUARD) else v.replace("$$", "$")
              for k, v in svc["environment"].items()}
    c = {"name": svc["container_name"], "image": svc["image"],
         "user": svc["user"], "restart": svc["restart"],
         "network": svc["network_mode"], "workdir": svc["working_dir"],
         "command": svc["command"],
         "env_files": [re.sub(r"^\./", "", f) for f in svc.get("env_file", [])],
         # `${VAR:-./file}` is compose's spelling of the script's overridable
         # `VAR="${VAR:-$DIR/file}"`, and both sides are stripped to the file
         # itself. Any variable, not CA_BUNDLE by name: the TLS pair a docker
         # agent serves virtual services with is written the same way, and a
         # pattern naming one file cannot notice a second going missing.
         # ...and `${VAR:?sentence}` is its spelling of the script's
         # `grep -q '<[A-Z][A-Z0-9_]*>' "$VAR"`, reduced to the same sentinel
         # both sides so a mount guarded in one file alone fails right here.
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
    diffs = [f"{f}: {gen.DOCKER_RUN_FILE}={run.get(f)!r} "
             f"{gen.DOCKER_COMPOSE_FILE}={comp.get(f)!r}"
             for f in ("image", "name", "user", "restart", "network", "workdir",
                       "command", "mounts", "env_files")
             if run.get(f) != comp.get(f)]
    for k in sorted(set(run["env"]) | set(comp["env"])):
        rv, cv = run["env"].get(k), comp["env"].get(k)
        # The one licensed difference, and it is checked in both directions
        # rather than skipped: a value nobody supplied is the marker to the
        # script, which greps for it, and compose's `${...:?}` to compose, which
        # has no shell to check anything in (#183). Both refuse; the container
        # they describe once it is filled in is the same container, and a file
        # that stopped refusing while the other went on doing so is exactly the
        # one-sided change this walks the matrix for.
        blank_here = k in comp["inline"] and str(cv).startswith(COMPOSE_GUARD)
        blank_there = k in run["inline"] and gen.marker_in(rv) is not None
        if blank_here or blank_there:
            if not (blank_here and blank_there):
                diffs.append(
                    f"env {k}: left blank, and only "
                    f"{gen.DOCKER_COMPOSE_FILE if blank_here else gen.DOCKER_RUN_FILE}"
                    f" refuses it")
        elif rv != cv:
            diffs.append(f"env {k}: {gen.DOCKER_RUN_FILE}={rv!r} "
                         f"{gen.DOCKER_COMPOSE_FILE}={cv!r}")
    return diffs


def test_compose_and_docker_run_describe_the_same_container():
    """#178. Same image, same environment, same mounts, same user, network,
    restart policy, working directory and command -- over the whole option
    matrix rather than over one default bundle, because a parity check that only
    ever sees defaults is the drift it exists to catch, one release later.

    The matrix is helm_parity.py's own CASES plus COMPOSE_CASES above. Most of
    helm's entries are Kubernetes vocabulary this format ignores and render the
    same bundle twice, which is worth nothing here and costs nothing either --
    what they buy is that the day one of them stops being ignored, it is already
    generated both ways. COMPOSE_CASES carries what only this format has: the
    credential split, the CA mount, a `$` in a proxy password, and a field left
    blank in each of the two files it can land in.

    Imported inside the test rather than at the top of this module, because
    helm_parity imports FACTS from *here* -- at module level the two would be a
    circular import, and running `python tests/helm_parity.py` would load it
    twice.
    """
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
    """Parity is worth exactly what its two parsers read, and both of them
    normalise -- shell quoting on one side, `$$` and a bundle-relative path on
    the other. So this is what says the fields come out as the constants
    themselves rather than as two agreeing mistakes; without it, a rule that
    flattened both sides to nothing would pass every case above.

    Every field here is one renderer could stop writing without the other
    noticing, which is the whole of #178.
    """
    files = gen.generate(FACTS, {**DOCKER, "ca_bundle": "PEM\n",
                                 "proxy": {"http": "http://p:1"}})
    run = _container_from_script(files)
    assert run["image"] == FACTS["crane_image"]
    assert run["name"] == gen.docker_container_name("bbb222")
    assert run["user"] == gen.DOCKER_USER
    assert run["restart"] == gen.DOCKER_RESTART
    assert run["network"] == gen.DOCKER_NETWORK
    assert run["workdir"] == gen.DOCKER_WORKDIR
    assert run["command"] == gen.DOCKER_ENTRYPOINT
    assert run["mounts"] == gen.DOCKER_MOUNTS + [
        f"{gen.DOCKER_CA_FILE}:{gen.DOCKER_CA_PATH}:ro"]
    assert run["env_files"] == [gen.DOCKER_ENV_FILE]
    # The credential is in the file both routes read and in neither inline set,
    # and the environment the container ends up with is the union.
    assert "AUTH_TOKEN" not in run["inline"]
    assert run["env"]["AUTH_TOKEN"] == "de" * 32
    assert run["env"]["HTTP_PROXY"] == "http://p:1"
    assert _container_from_compose(files)["env"] == run["env"]


def test_docker_readme_offers_both_routes_and_says_to_pick_one():
    """`.sh` first: BlazeMeter's own shape leads. The sentence is what stops
    somebody running both and reading the duplicated results as a load problem
    -- docker refuses the second, but only after they have tried."""
    readme = gen.generate(FACTS, DOCKER)["README.md"]
    run = readme.split("## Run it")[1].split("##")[0]
    assert run.index(f"./{gen.DOCKER_RUN_FILE}") < run.index("docker compose up -d")
    assert "not both" in run
    assert "docker compose version" in run          # the version requirement
    # ...and the file people would tidy into a `.env` is named where it exists.
    assert "`.env`" in gen.generate(FACTS, DOCKER)["README.md"]


def test_docker_readme_names_what_it_could_not_carry():
    """The failure this exists to stop is silent: a bundle generated with a node
    selector and a namespace, handed over, and believed to have applied them.
    Only what was actually set away from its default -- a note listing all
    twenty-two every time would be read as boilerplate."""
    readme = gen.generate(FACTS, {**DOCKER, "namespace": "other",
                                  "node_selector": {"a": "b"}})["README.md"]
    assert "## Set here, but not carried" in readme
    assert "`namespace`" in readme
    assert "`node_selector`" in readme
    # ...and a bundle that asked for none of them says nothing about them.
    assert "Set here, but not carried" not in gen.generate(FACTS, DOCKER)["README.md"]


def test_docker_names_the_two_options_that_used_to_go_quiet():
    """crane_hook renders a Pod and registry_auth writes ConfigMap lines, and
    the docker branch returns before either. Both were set-able and silent --
    found by hiding this table's keys on the configure page and noticing the
    two controls still on screen."""
    readme = gen.generate(FACTS, {**DOCKER, "crane_hook": True,
                                  "private_registry": "reg.corp/bzm",
                                  "registry_auth": True})["README.md"]
    assert "`crane_hook`" in readme
    assert "`registry_auth`" in readme
    bundle = gen.generate(FACTS, {**DOCKER, "crane_hook": True})
    assert not [f for f in bundle if "cranehook" in f]


# The smallest options a bundle of each format generates from, so the two rules
# below can be walked over every format rather than over the one that happens to
# ignore anything today. Keyed by gen.OUTPUT_FORMATS, and the assertion in
# test_every_format_has_an_ignored_entry is what keeps a fourth from being
# tested by nobody.
FORMAT_BASE = {
    "manifests": {"namespace": "ns1", "ship_id": "bbb222",
                  "auth_token": "de" * 32},
    "helm": {"output_format": "helm", "namespace": "ns1", "ship_id": "bbb222",
             "auth_token": "de" * 32},
    "docker": DOCKER,
}


def test_every_format_has_an_ignored_entry():
    """One entry per output format, `{}` included.

    An entry that is empty is a format that ignores nothing, and it is an
    answer: only the *table* being unread means nobody has said, and that is a
    state a reader over the wire can be in and this module cannot (see
    core.ignored_options). So a fourth format cannot arrive without somebody
    deciding what it drops -- which is this assertion, and the FORMAT_BASE above
    it, which is what would then walk it."""
    assert set(gen.IGNORED_BY_FORMAT) == set(gen.OUTPUT_FORMATS)
    assert set(FORMAT_BASE) == set(gen.OUTPUT_FORMATS)
    # Named so a table that silently emptied itself fails here rather than
    # passing every rule below vacuously. Two of them now: since #182 the
    # cluster formats drop the docker agent's own way of publishing a virtual
    # service, which is what made this table symmetric for the first time.
    assert gen.IGNORED_BY_FORMAT["docker"]["namespace"]
    assert gen.IGNORED_BY_FORMAT["manifests"]["sv_hostname"]


def test_a_format_never_refuses_what_it_says_it_ignores():
    """The rule, over every format's table rather than the three keys that
    happened to break it.

    An ignored option has no control on the configure page for that format, so
    a refusal over its value is a blocker with nothing on screen to clear it.
    Three validators ran before anyone checked: `service_account_name` (empty
    was refused), the two engine limits, and the CA modes -- picking an
    existing ConfigMap, switching to docker and pasting a PEM gave "choose one
    CA mode" naming a field that format had just taken away.

    Junk in every one of them at once, because they are ignored: nothing reads
    them, so nothing can object to the shape. The README still names them all,
    which is the other half of the promise and the assertion below. Walked per
    format so the day a cluster format ignores something, its own README is
    held to the same promise without this test being rewritten first."""
    for fmt, ignored in gen.IGNORED_BY_FORMAT.items():
        junk = {k: "nonsense" for k in ignored}
        out = gen.generate(FACTS, {**FORMAT_BASE[fmt], **junk})
        for key in ignored:
            assert f"`{key}`" in out["README.md"], \
                f"{fmt}: {key} carried silently"
    # ...and the CA pair that is reachable by clicking: the inline PEM wins and
    # the ConfigMap it was switched away from is ignored, not a second mode.
    both = gen.generate(FACTS, {**DOCKER, "ca_existing_configmap": "corp-trust",
                                "ca_bundle": "-----BEGIN CERTIFICATE-----"})
    assert both[gen.DOCKER_CA_FILE] == "-----BEGIN CERTIFICATE-----"


@pytest.mark.parametrize("fmt", sorted(gen.IGNORED_BY_FORMAT))
def test_a_format_never_lets_an_ignored_option_reach_a_generated_file(fmt):
    """The other half of the promise above, and the half nothing was checking.

    "Ignored" is a claim about the *bundle*, not only about the validators: an
    option this format drops must change no file it emits. `_mirror_script` read
    `crane_hook` directly, so a docker bundle mirrored `cranehook:latest` into a
    customer's registry while its own README listed `crane_hook` under "Set
    here, but not carried" -- one bundle saying both, and a push of an image
    that format can never pull (#211). The refusal sweep beside this one could
    not see it: nothing was refused, something was quietly carried.

    Two files are exempt and both name the option by design -- README.md, whose
    "Set here, but not carried" table exists to name it, and profile.json, which
    records every resolved option so `generate --profile` replays a bundle
    exactly. Everything else is what the customer applies, and an ignored option
    must leave all of it byte-identical.

    A private registry is set throughout so the mirror script is among the files
    compared: without one it is not emitted at all, and the leak this test was
    written for lived in a file the sweep would not have generated.
    """
    named_by_design = {"README.md", gen.PROFILE_FILE}
    base = {**FORMAT_BASE[fmt], "private_registry": "reg.corp/bzm"}
    plain = gen.generate(FACTS, base)
    for key in gen.IGNORED_BY_FORMAT[fmt]:
        out = gen.generate(FACTS, {**base, key: "nonsense"})
        for name in sorted(set(plain) | set(out)):
            if name in named_by_design:
                continue
            assert out.get(name) == plain.get(name), \
                f"{fmt}: {key} is ignored, and it reached {name}"


def test_the_other_formats_still_refuse_all_of_it():
    """The rule above is about a format that ignores an option, not a licence
    to stop checking. Kubernetes has every one of these fields, so each is
    still refused there -- otherwise the fix would have bought the off-screen
    blocker back as a bad manifest."""
    k8s = {"ship_id": "bbb222", "auth_token": "de" * 32}
    # A malformed value and two modes chosen at once -- neither is a blank
    # field, so neither is something the marker can stand in for. A marker
    # says "nobody filled this in"; it cannot say "this says 4 gigglebytes" or
    # "you asked for two CA modes", and a bundle that carried it for those would
    # be reporting the wrong thing about itself.
    for over in ({"engine_cpu_limit": "not-a-cpu"},
                 {"engine_mem_limit": "not-a-memory"},
                 {"ca_existing_configmap": "cm", "ca_bundle": "PEM"}):
        with pytest.raises(ValueError):
            gen.generate(FACTS, {**k8s, **over})


def test_docker_reports_the_engine_size_it_actually_carries():
    """The limits are ignored, so the README must not advertise them: a page
    that says "each engine needs 4 CPU" under a footer saying the option was
    not carried is two answers to one question."""
    readme = gen.generate(FACTS, {**DOCKER, "engine_cpu_limit": "4",
                                  "engine_mem_limit": "16Gi"})["README.md"]
    assert "4 CPU + 16GiB RAM" not in readme
    assert "`engine_cpu_limit`" in readme      # named as not carried, though


def test_docker_readme_does_not_advertise_kubernetes_answers():
    """The bundle table is shared with the other two formats, where namespace
    and platform are the answer. Here they are neither applied nor applicable,
    and a page that opens by stating them contradicts its own footer."""
    readme = gen.generate(FACTS, {**DOCKER, "namespace": "other"})["README.md"]
    head = readme.split("## Run it")[0]
    assert "Namespace" not in head
    assert "Platform" not in head
    assert "bzm-crane-bbb222" in head


def test_docker_no_longer_refuses_a_service_virtualization_location():
    """It used to, and the refusal was always narrower than it read (#182): a
    docker agent serves virtual services perfectly well, publishing them with
    HOSTNAME_OVERRIDE and a TLS pair, and what was missing was options for that
    shape rather than anything about the agent.

    The four Kubernetes sv_* options are this format's ignored ones now, so a
    profile written for a cluster and switched to docker generates -- carrying
    them in profile.json and naming them in the README, which is what every
    other ignored option does.
    """
    sv_facts = {**FACTS, "func_ids": ["performance", "mockServices"]}
    files = gen.generate(sv_facts, {**DOCKER, "sv_ingress": "nginx",
                                    "sv_subdomain": "apps.example.com",
                                    "sv_tls_secret": "wild"})
    assert "`sv_ingress`" in files["README.md"]
    assert "KUBERNETES_WEB_EXPOSE" not in files[gen.DOCKER_RUN_FILE]
    # ...and a mockServices location with nothing configured is not refused
    # either: the demand `_sv_cfg` raises on is a demand for an *ingress*, and
    # this format has no field for one.
    gen.generate(sv_facts, DOCKER)


SV_DOCKER = {**DOCKER, "sv_hostname": SV_HOST,
             "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY}


def test_a_docker_agent_publishes_virtual_services_under_its_own_three():
    """The shape from BlazeMeter's bring-your-own-certificate page, folded into
    this bundle: the two PEMs become files, the files are mounted at the paths
    their command uses, and the variables name those paths.

    Content in the option and a file in the bundle, exactly as `ca_bundle` is:
    a path-valued option would break facts.manual()'s whole premise, since a
    bundle cannot be generated for a host nobody here can see if the option
    names a file on it."""
    files = gen.generate(FACTS, SV_DOCKER)
    assert files[gen.DOCKER_SV_CERT_FILE] == SV_CERT
    assert files[gen.DOCKER_SV_KEY_FILE] == SV_KEY
    sh = files[gen.DOCKER_RUN_FILE]
    assert f"--env HOSTNAME_OVERRIDE={SV_HOST}" in sh
    assert f"--env TLS_CERT={gen.DOCKER_SV_CERT_PATH}" in sh
    assert f"--env TLS_KEY={gen.DOCKER_SV_KEY_PATH}" in sh
    assert f'-v "$SV_TLS_CERT":{gen.DOCKER_SV_CERT_PATH}:ro' in sh
    assert f'-v "$SV_TLS_KEY":{gen.DOCKER_SV_KEY_PATH}:ro' in sh
    # ...and each keeps `ca_bundle`'s escape hatch: a host may already have the
    # certificate its platform team maintains.
    assert f'SV_TLS_CERT="${{SV_TLS_CERT:-$DIR/{gen.DOCKER_SV_CERT_FILE}}}"' in sh
    assert "virtual-service certificate not found" in sh
    # The compose file mounts the same pair at the same paths -- held over the
    # whole matrix by test_compose_and_docker_run_describe_the_same_container,
    # and stated here so the two files can be read side by side.
    svc = yaml.safe_load(files[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["volumes"][-2:] == [
        f"${{SV_TLS_CERT:-./{gen.DOCKER_SV_CERT_FILE}}}:{gen.DOCKER_SV_CERT_PATH}:ro",
        f"${{SV_TLS_KEY:-./{gen.DOCKER_SV_KEY_FILE}}}:{gen.DOCKER_SV_KEY_PATH}:ro"]
    assert svc["environment"]["HOSTNAME_OVERRIDE"] == SV_HOST


def test_the_hostname_alone_is_a_configuration():
    """BlazeMeter frame the pair as what you supply "to use HTTPS", so a
    hostname with no certificate is a real bundle -- the endpoints are plain
    HTTP under a name rather than under this host's IP address, which is the
    whole of what HOSTNAME_OVERRIDE is for. Refusing it would be inventing a
    requirement they do not state."""
    files = gen.generate(FACTS, {**DOCKER, "sv_hostname": SV_HOST})
    assert gen.DOCKER_SV_CERT_FILE not in files
    sh = files[gen.DOCKER_RUN_FILE]
    assert f"--env HOSTNAME_OVERRIDE={SV_HOST}" in sh
    assert "TLS_CERT" not in sh
    # ...and the README says which of the two it is, because "no certificate"
    # is a decision somebody may not have realised they were making.
    assert "plain HTTP" in files["README.md"]


def test_a_pkcs1_key_is_refused_by_name_with_the_conversion():
    """`-----BEGIN RSA PRIVATE KEY-----` is what `openssl genrsa` still writes
    on many builds, so it is the common export rather than an exotic case, and
    BlazeMeter require PKCS#8. Handed one the agent starts, reports online and
    fails at the first TLS handshake -- so the refusal is here, and it names
    the one command that fixes it."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": SV_KEY_PKCS1})
    assert "PKCS#1" in str(e.value)
    assert "openssl pkcs8 -topk8" in str(e.value)
    # An encrypted key is refused too, and for its own reason: nothing in this
    # bundle, in BlazeMeter's env reference or in their own docker command
    # supplies a passphrase.
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER,
                             "sv_tls_key": "-----BEGIN ENCRYPTED PRIVATE KEY-----\nx\n"})
    assert "passphrase" in str(e.value)
    # ...and something that is not a key at all.
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": "hunter2"})
    assert "sv_tls_key" in str(e.value)


def test_a_certificate_that_does_not_cover_the_hostname_is_refused():
    """The failure is invisible from the agent's end: crane starts, reports
    online, publishes the endpoint, and every client rejects the certificate.
    So it is checked at generate time against the certificate already in hand,
    and the refusal states what the certificate does name."""
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
    """Read, and it covers nothing -- which is a real answer about a real
    certificate and not the same thing as the one below it."""
    with pytest.raises(ValueError) as e:
        gen.generate(FACTS, {**SV_DOCKER, "sv_tls_cert": SV_CERT_NO_NAMES})
    assert "no DNS name at all" in str(e.value)


def test_a_certificate_this_cannot_read_is_not_checked_and_says_so():
    """The rule this repo keeps: a thing nobody has checked and a thing this
    tool verified must not share a representation.

    A PEM that will not parse is *not read*, so it is not refused -- refusing
    would turn "we did not look" into "it is wrong" about a certificate that may
    be fine. What is not allowed is silence, because a bundle that said nothing
    would read exactly like one that had passed. So the README says which of the
    two happened, in its own sentence, and points at the openssl command that
    answers it.
    """
    corrupt = SV_CERT.replace(SV_CERT.splitlines()[3], "AAAA")
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_cert": corrupt})
    readme = files["README.md"]
    assert "was not checked against the certificate" in readme
    assert "openssl x509" in readme
    # ...and the bundle a check did pass says that instead, naming what the
    # certificate carries and what was *not* asked about it.
    checked = gen.generate(FACTS, SV_DOCKER)["README.md"]
    assert "was checked against it" in checked
    assert "not its expiry" in checked
    assert "not whether the key beside it is its key" in checked


def test_the_private_key_is_never_in_the_profile_and_the_certificate_is():
    """A profile is the file people commit, diff and paste into tickets. The
    key is a credential; the certificate is what the agent hands to every client
    that connects, so dropping it would make a replay need two things supplied
    for no gain.

    The consequence is documented rather than worked around: `generate
    --profile` on such a bundle needs `--auth-token` and `--sv-tls-key`, and
    without the key the replayed bundle carries a marker and says so.
    """
    files = gen.generate(FACTS, SV_DOCKER)
    profile = json.loads(files[gen.PROFILE_FILE])
    assert "sv_tls_key" not in profile
    assert profile["sv_tls_cert"] == SV_CERT
    assert profile["sv_hostname"] == SV_HOST
    replayed = gen.generate(FACTS, {**profile, "auth_token": "de" * 32})
    assert replayed[gen.DOCKER_SV_KEY_FILE] == gen.marker("sv_tls_key")
    assert "`sv_tls_key`" in replayed["README.md"]


def test_half_a_tls_pair_is_a_blank_field_rather_than_a_refusal():
    """Each fills for the other, which is one rule read twice. A marker rather
    than a refusal because a half-answered pair is a field somebody left empty,
    and that is what the marker is for -- refusing would be a blocker on a
    page that had already let the box be emptied.

    A blank hostname beside a certificate is the same rule and lands in the
    variable, where #183's guard is what refuses it before the agent starts."""
    only_cert = gen.generate(FACTS, {**DOCKER, "sv_hostname": SV_HOST,
                                     "sv_tls_cert": SV_CERT})
    assert only_cert[gen.DOCKER_SV_KEY_FILE] == gen.marker("sv_tls_key")
    assert "`sv_tls_key`" in only_cert["README.md"]
    blank_host = gen.generate(FACTS, {**SV_DOCKER, "sv_hostname": ""})
    assert "HOSTNAME_OVERRIDE" in gen._blank_env_by_name(FACTS, {
        **gen.DEFAULT_OPTIONS, **SV_DOCKER,
        "sv_hostname": gen.marker("sv_hostname")})
    assert "HOSTNAME_OVERRIDE carries" in blank_host[gen.DOCKER_RUN_FILE]


def test_the_script_refuses_a_blank_mounted_file_before_starting_anything(tmp_path):
    """#183 made both routes refuse a blank field; #182 then added two options
    written as **files**, and the guard only ever looked at environment values.
    `TLS_KEY` holds a container path, so no environment value carried the
    marker, `sv-tls.key` was written containing it, and the README went on
    saying both routes refused a bundle neither refused -- a claim of
    verification, which is the worse failure of the two.

    The existence check next to it is not the guard: the bundle wrote that file,
    so `[ ! -f ]` passes over one whose whole content is `<SV_TLS_KEY>`. What
    is checked is the content of the **resolved** file, so both ways of
    finishing the bundle clear it.
    """
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": ""})
    assert files[gen.DOCKER_SV_KEY_FILE] == gen.marker("sv_tls_key")
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 1
    assert "sv-tls.key carries <SV_TLS_KEY>" in r.stderr
    # The option to re-generate with, and the variable that fixes it here --
    # "go and fill it in" without either is the half-answer the token's own
    # refusal already avoids.
    assert "sv_tls_key" in r.stderr and "Set SV_TLS_KEY" in r.stderr
    assert made == ["ps"], made           # nothing was started

    # Filled in, the same bundle runs: the check reads the file as it stands,
    # so there is nothing to delete afterwards.
    (tmp_path / gen.DOCKER_SV_KEY_FILE).write_text(SV_KEY)
    r, made = _run_bundle(tmp_path)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "ps", "run"], made


def test_the_escape_hatch_finishes_a_blank_mounted_file_too(tmp_path):
    """The judgement this guard makes, from the running end. `SV_TLS_KEY` is the
    bundle's documented override -- a host whose platform team already keeps the
    key points at it rather than copying it in -- so a run with it set is the
    intended fix and not a guard defeated. The check follows the variable
    because it reads the file the variable resolves to."""
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": ""})
    (tmp_path / "elsewhere.key").write_text(SV_KEY)
    r, made = _run_bundle(tmp_path, files,
                          env={"SV_TLS_KEY": str(tmp_path / "elsewhere.key")})
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "run"], made


def test_compose_refuses_a_blank_mounted_file_in_the_same_words():
    """Compose has no shell and cannot read a file, so its half is the bind
    source's default dropped: `${SV_TLS_KEY:?...}` aborts `compose up` before
    anything is created, printing the volume's path in the file and then the
    same two sentences the script echoes.

    The variable is the bundle's own, and that is deliberately **not** what
    `_compose_required` does for an environment value -- there the guard is
    `BZM_OPL_UNSET_<NAME>`, a name nobody has, because `${HTTP_PROXY:?}` would
    resolve itself away on the host most likely to carry one. `SV_TLS_KEY`
    exists only in this bundle and setting it is the documented fix, so guarding
    a name nobody has would refuse a bundle finished the way its own README
    asks.
    """
    m = [m for m in gen.docker_file_mounts({**gen.DEFAULT_OPTIONS, **SV_DOCKER,
                                            "sv_tls_key":
                                                gen.marker("sv_tls_key")})
         if m.var == "SV_TLS_KEY"][0]
    wrong, todo = gen._docker_blank_file_lines(m)
    files = gen.generate(FACTS, {**SV_DOCKER, "sv_tls_key": ""})
    svc = yaml.safe_load(files[gen.DOCKER_COMPOSE_FILE])["services"]["crane"]
    assert svc["volumes"][-1] == (
        f"${{SV_TLS_KEY:?{wrong} {todo}}}:{gen.DOCKER_SV_KEY_PATH}:ro")
    assert "BZM_OPL_UNSET" not in files[gen.DOCKER_COMPOSE_FILE]
    # One wording for both routes, so a customer reads the same sentence about
    # the same file whichever of the two they started from.
    for line in (wrong, todo):
        assert line in files[gen.DOCKER_RUN_FILE]
    # The certificate beside it was supplied, so it keeps its default and
    # neither route says anything about it: the guard is per file left blank.
    assert svc["volumes"][-2] == (
        f"${{SV_TLS_CERT:-./{gen.DOCKER_SV_CERT_FILE}}}:"
        f"{gen.DOCKER_SV_CERT_PATH}:ro")


def test_a_finished_tls_pair_carries_no_file_guard(tmp_path):
    """Nothing is stated about a file nobody left blank, which is the same rule
    the variable-level guard follows: the ordinary bundle is the pair of files
    it was before either check existed."""
    files = gen.generate(FACTS, SV_DOCKER)
    assert not gen.MARKER_RE.search(files[gen.DOCKER_RUN_FILE])
    assert not gen.MARKER_RE.search(files[gen.DOCKER_COMPOSE_FILE])
    assert ":?" not in files[gen.DOCKER_COMPOSE_FILE]
    r, made = _run_bundle(tmp_path, files)
    assert r.returncode == 0, r.stderr
    assert made == ["ps", "run"], made


# -- the images crane will not pull (#206) ------------------------------------
#
# A docker agent's crane composes the image name and calls the daemon's
# *create*; nothing pulls. The location decides whether the bundle says so --
# the mock images come off its funcIds, not off the sv_* options -- so these
# facts carry mockServices and the options mostly do not.

MOCK_FACTS = {**FACTS, "func_ids": ["mockServices"]}
MOCK_LATEST = f"{gen.PUBLIC_REGISTRY}/blazemeter/service-mock:latest"


def test_a_docker_sv_bundle_names_the_images_crane_will_not_pull():
    """The first deploy fails otherwise, ninety seconds later, with
    `Failed to find a deployed container` in BlazeMeter and `No such image`
    only in `docker logs` -- neither of which mentions a pull, and the first of
    which reads like a broken agent. So the bundle names the commands."""
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
    """The whole of the bug. BlazeMeter's deploy command names the image
    unqualified and `latest`, whatever the location's own /versions pins, and
    crane prefixes DOCKER_REGISTRY onto that -- so the pinned tag beside it is
    the one thing that is *not* worth fetching. Live: the pull target was
    `.../blazemeter/service-mock:latest` while the location pinned 6.0.30.4.
    """
    pinned = {**MOCK_FACTS, "images": [
        {**i, "key": "blazemeter/service-mock:6.0.30.4", "tag": "6.0.30.4"}
        if i["key"].startswith("blazemeter/service-mock") else i
        for i in FACTS["images"]]}
    readme = gen.generate(pinned, {**DOCKER, "private_registry": REG})["README.md"]
    assert f"docker pull {REG}/blazemeter/service-mock:latest" in readme
    assert "6.0.30.4" not in readme
    # ...and the repo's last segment is not the name either: that is how
    # IMAGE_OVERRIDES resolves a key on Kubernetes, and there is no such
    # variable here. Crane composes the key itself, `blazemeter/` and all.
    assert "{REG}/service-mock:" not in readme


def test_a_default_docker_bundle_names_no_registry_at_all():
    """#217. Crane composes `<DOCKER_REGISTRY>/<key>:latest` and pulls nothing,
    and the keys are not uniform: the mock ones carry the org
    (`blazemeter/service-mock`) and the engine one does not (`taurus-cloud`).
    So no value of that variable is right for both -- pointed at BlazeMeter's
    own gcr mirror it asks for `.../taurus-cloud:latest`, a path with zero tags,
    and the run sits at BOOT_STARTING with no engine ever created.

    BlazeMeter's own generated command sets it only when you mirror, which is
    the shape this follows. Absent, crane uses its own default and the engine
    resolves to something that exists.
    """
    files = gen.generate(FACTS, DOCKER)
    for name in ("bzm-opl-agent.sh", "compose.yaml", "bzm-opl-agent.env"):
        body = files.get(name) or ""
        assert "DOCKER_REGISTRY=" not in body, name
        assert "DOCKER_REGISTRY:" not in body, name


def test_a_mirrored_docker_bundle_still_names_its_registry():
    """The other half of #217: the variable is how a mirrored bundle tells crane
    where to look, so dropping it everywhere would break the case it exists
    for."""
    files = gen.generate(FACTS, {**DOCKER, "private_registry": "reg.corp/bzm"})
    assert "DOCKER_REGISTRY=reg.corp/bzm" in files["bzm-opl-agent.sh"]
    assert 'DOCKER_REGISTRY: "reg.corp/bzm"' in files["compose.yaml"]


def test_the_docker_pre_pull_list_covers_the_engine_images_too():
    """#218. The rule #209 established for mock images is the rule for every
    image crane creates here -- measured live for an engine, which asked for
    `<registry>/taurus-cloud:latest`, the same `<key_base>:latest` shape with no
    org because the engine key carries none. So a performance docker bundle
    carries the bullet as well; before this it said nothing at all."""
    files = gen.generate(FACTS, {**DOCKER, "private_registry": "reg.corp/bzm"})
    readme = files["README.md"]
    assert "docker pull reg.corp/bzm/taurus-cloud:latest" in readme
    # ...and the pinned tag is still not what to fetch.
    assert "docker pull reg.corp/bzm/taurus-cloud:2.4.454-reduced" not in readme


def test_the_docker_mirror_pushes_every_name_crane_composes():
    """#218's other half, and the property that makes the pair honest: what the
    mirror pushes and what the README says to pull are one set, for engines as
    well as mocks. Crane's own image is the exception and stays -- the bundle
    names that reference itself, so it is not crane's to compose."""
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
    """Both platforms compose, and they compose differently: docker builds the
    name from the crane *key* and `latest`, Kubernetes from the repo path and
    the tag IMAGE_OVERRIDES pins. So this must not follow docker's change --
    the key never appears in a cluster script, and the pinned tag always
    does."""
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
    """#217 changed which half is unknown. The engine trap is measured now and
    a performance bundle says so -- but with no `DOCKER_REGISTRY` set, the
    prefix crane composes with is *its* default and nothing here has read it. So
    the warning is carried and the list is not: a plausible-looking `docker
    pull` block would be indistinguishable from the measured one two lines of
    config away."""
    readme = gen.generate(FACTS, DOCKER)["README.md"]      # no private registry
    assert "does not pull" in readme
    # No *command* -- the prose may well mention the phrase while explaining why
    # there is nothing to run.
    assert not [l for l in readme.splitlines() if l.strip().startswith("docker pull ")]
    # The keys are still named -- what is unknown is the registry, not which
    # images the location runs.
    assert "`taurus-cloud`" in readme


def test_the_cluster_formats_carry_no_pre_pull_note():
    """Kubernetes pulls images the way Kubernetes does, and the bullet is about
    a docker daemon crane talks to directly."""
    manifests = gen.generate(MOCK_FACTS, dict(SV_OPTS, namespace="ns1"))
    helm = gen.generate(MOCK_FACTS, {"namespace": "ns1", "ship_id": "bbb222",
                                     "auth_token": "de" * 32,
                                     "output_format": "helm",
                                     "sv_ingress": gen.SV_INGRESS_NONE})
    for files in (manifests, helm):
        for name, body in files.items():
            if name.endswith(".md"):
                assert "docker pull" not in body, name


# -- and the registry they are mirrored into (#209) ---------------------------
#
# The other half of the same fact. #206 made the README name what crane asks
# for; the mirror script beside it went on pushing somewhere else, so a bundle
# with a private registry shipped two files that disagreed in writing and the
# mirror reported success over a path nothing reads.

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
    """The property option 1 of #209 exists to buy, and the only one worth
    having: the mock references in the two files are one set, whichever way you
    read them. Both sides are collected off the generated bundle rather than
    written out here, so a shape changed in one renderer fails against the
    other and not against a literal that would have to be edited to match.

    `blazemeter/` and `latest` are what crane composes -- DOCKER_REGISTRY plus
    BlazeMeter's own unqualified image name -- and this format has no
    IMAGE_OVERRIDES to map anything else onto it.
    """
    files = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})
    pushed = {d for _, d in _mirror_pairs(files) if "/blazemeter/" in d}
    pulled = set(_readme_pulls(files))
    assert pushed == pulled, (pushed, pulled)
    assert f"{REG}/blazemeter/service-mock:latest" in pushed
    # The source stays the pinned public ref: it is the content whose version is
    # known, and the line that pushed `:latest` is the one record of which
    # version that is. Nothing pushes the pinned tag as a second destination --
    # that would be a reference in this file the pull list does not name.
    sources = {s for s, d in _mirror_pairs(files) if "/blazemeter/" in d}
    assert f"{gen.PUBLIC_REGISTRY}/blazemeter/service-mock:1.0" in sources
    assert not [d for _, d in _mirror_pairs(files) if d.endswith(":1.0")]


def test_cranes_own_mirror_target_is_the_reference_the_bundle_runs():
    """The trap in the fix. Crane's image keeps the last-segment destination and
    must: this bundle's own two files name that reference themselves, so the
    shape is free as long as the three agree. Only the images crane *composes*
    the name for -- the ones the bundle gets no say in -- were wrong."""
    files = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})
    crane = f"{REG}/crane:3.7.55"
    assert crane in files["bzm-opl-agent.sh"]
    assert crane in files[gen.DOCKER_COMPOSE_FILE]
    assert crane in [d for _, d in _mirror_pairs(files)]


def _cluster_overrides(files):
    """IMAGE_OVERRIDES as the bundle wrote it, whichever cluster format it is:
    the ConfigMap on manifests, `imageOverrides` in the chart's values."""
    if "bzm_configmap.yaml" in files:
        return json.loads(yaml.safe_load(
            files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    return yaml.safe_load(files[gen.HELM_VALUES_FILE])["imageOverrides"]


CLUSTER_FORMATS = ("manifests", "helm")


def _cluster_bundle(fmt, facts=FACTS, **over):
    return gen.generate(facts, {"namespace": "ns1", "ship_id": "bbb222",
                                "auth_token": "de" * 32, "output_format": fmt,
                                "private_registry": REG, **over})


def test_the_cluster_map_and_its_mirror_name_one_set():
    """#234, and #209 one platform over. Crane composes the engine's reference
    from DOCKER_REGISTRY and the repo path and does not read IMAGE_OVERRIDES
    for it -- measured live -- so the map's value and the mirror's push target
    have to be the same string. They come from one function; this is the proof
    that both callers still use it, for both cluster formats.

    Collected off the generated bundle rather than written out here: a shape
    changed in one renderer then fails against the other, not against a literal
    somebody edits to match. Crane's own image is the one exception and is
    named as one -- the bundle chooses that reference itself, in the Deployment
    and in the chart's `image.repository`.
    """
    for fmt in CLUSTER_FORMATS:
        # A mockServices location declining an ingress: the widest image set
        # both formats will generate, since helm refuses a bundle configured
        # for a virtual service and the images follow the location either way.
        files = _cluster_bundle(fmt, MOCK_FACTS, sv_ingress=gen.SV_INGRESS_NONE)
        dests = {d for _, d in _mirror_pairs(files)}
        overrides = set(_cluster_overrides(files).values())
        crane = {d for d in dests if "/crane:" in d}
        assert crane, f"crane's own image is not in the mirror ({fmt})"
        assert dests - crane == overrides, (fmt, (dests - crane) ^ overrides)


def test_the_engine_is_mirrored_where_crane_composes_it():
    """The one reference that was observed live, pinned so the reduction cannot
    come back. `taurus-cloud` resolves to the repo `blazemeter/v4`, and crane
    asked a private registry for `<registry>/blazemeter/v4:<tag>` while the
    bundle had mirrored `<registry>/v4:<tag>` -- `manifest unknown`, on the
    first test, with the agent already online.

    The other images are the same rule and were not observed under one shape;
    what this pins is the engine.
    """
    for fmt in CLUSTER_FORMATS:
        files = _cluster_bundle(fmt)
        want = f"{REG}/blazemeter/v4:2.4.444-reduced"
        assert _cluster_overrides(files)["taurus-cloud:latest"] == want, fmt
        assert want in {d for _, d in _mirror_pairs(files)}, fmt


def test_cranes_cluster_mirror_target_is_the_reference_the_bundle_runs():
    """The other half of the exception above: crane's destination is free only
    because the bundle names it, so the Deployment and the chart's values have
    to carry exactly what the mirror pushed. Unchanged by #234 -- which is why
    crane pulled fine through the whole of it -- and held rather than assumed.
    """
    crane = f"{REG}/crane:3.7.55"
    manifests = _cluster_bundle("manifests")
    assert crane in manifests["bzm_deployment.yaml"]
    assert crane in [d for _, d in _mirror_pairs(manifests)]
    helm = _cluster_bundle("helm")
    values = yaml.safe_load(helm[gen.HELM_VALUES_FILE])
    assert f"{values['image']['repository']}:{values['image']['tag']}" == crane
    assert crane in [d for _, d in _mirror_pairs(helm)]


def test_a_performance_docker_bundle_mirrors_what_crane_will_ask_for():
    """#218: engines were the untested half and are not any more. A live docker
    performance agent asked for `<registry>/taurus-cloud:latest`, so the mirror
    pushes that rather than the last-segment pinned form it used to, and the
    README's pull list names the same thing. Crane's own image is untouched --
    the bundle chooses that reference itself."""
    files = gen.generate(FACTS, {**DOCKER, "private_registry": REG})
    dests = {d for _, d in _mirror_pairs(files)}
    assert dests == {f"{REG}/crane:3.7.55", f"{REG}/taurus-cloud:latest",
                     f"{REG}/apm-image:latest"}
    assert f"docker pull {REG}/taurus-cloud:latest" in files["README.md"]


def test_the_docker_mirror_says_which_of_its_two_shapes_is_which():
    """The reader this issue is about is holding this file beside the README's
    pull list. The lists differ on purpose in one place and agree exactly in the
    other, and an unexplained difference reads as a bug in one of them."""
    sh = gen.generate(MOCK_FACTS, {**DOCKER, "private_registry": REG})[MIRROR]
    assert "Two shapes of destination" in sh
    assert "README.md" in sh
    assert "DOCKER_REGISTRY" in sh


def test_the_two_platforms_never_offer_each_other_s_vocabulary():
    """The symmetry #182 introduced, from both ends. A cluster bundle carries no
    HOSTNAME_OVERRIDE and a docker bundle no KUBERNETES_WEB_EXPOSE_*, and each
    set is in the other format's ignored table rather than refused -- so a
    profile written for one platform generates for the other, keeping its values
    and naming them."""
    k8s = gen.generate(FACTS, {"ship_id": "bbb222", "auth_token": "de" * 32,
                               "namespace": "ns1", "sv_hostname": SV_HOST,
                               "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY})
    cm = yaml.safe_load(k8s["bzm_configmap.yaml"])
    assert not {"HOSTNAME_OVERRIDE", "TLS_CERT", "TLS_KEY"} & set(cm["data"])
    assert "sv-tls.crt" not in k8s
    # Named, never dropped -- the promise every ignored option is under, and
    # until #182 only docker's README kept it.
    assert "## Set here, but not carried" in k8s["README.md"]
    assert "`sv_hostname`" in k8s["README.md"]
    assert gen.SV_DOCKER_IGNORED.keys() <= gen.IGNORED_BY_FORMAT["helm"].keys()


def test_docker_profile_replays_and_carries_no_token():
    """The same contract every format has: profile.json is every resolved option
    and no credential."""
    files = gen.generate(FACTS, DOCKER)
    profile = json.loads(files["profile.json"])
    assert profile["output_format"] == "docker"
    assert "auth_token" not in profile


# -- free-form agent environment ---------------------------------------------
#
# The escape hatch for BlazeMeter's much wider agent-environment reference. The
# risk it introduces is a variable set twice -- once by an option and once by
# hand -- so most of what is checked here is the refusal.

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
    # The docker agent's own way of publishing a virtual service -- the branch
    # that writes HOSTNAME_OVERRIDE and the TLS pair, and the only one that
    # does. Without it those three names would be in RESERVED_ENV with nothing
    # emitting them, which is the half of this rule that refuses a variable the
    # customer could have had.
    (SV_FACTS, {**DOCKER, "sv_hostname": SV_HOST,
                "sv_tls_cert": SV_CERT, "sv_tls_key": SV_KEY}),
]


def _env_names(facts, opts):
    """Every environment variable name a bundle writes, commented stubs
    included: the registry-auth pair is emitted as commented ConfigMap lines
    for somebody to fill in, so the name is taken even though nothing reads it
    yet."""
    files = gen.generate(facts, opts)
    names = set()
    for name, content in files.items():
        if name.endswith(".yaml"):
            for doc in yaml.safe_load_all(content):
                # `metadata` is what makes it a Kubernetes object rather than
                # merely YAML. The docker bundle's compose.yaml is a .yaml this
                # generator emits and is not a manifest; its variables are
                # counted by docker_env below, with the rest of that format's.
                if not isinstance(doc, dict) or "metadata" not in doc:
                    continue
                # The agent's two only. The CA ConfigMap is keyed by *file*
                # name (ca-bundle.crt) and is mounted rather than read as env,
                # so its key is not a variable name and reserving it would
                # refuse a variable nothing writes.
                if doc["metadata"]["name"] in ("blazemeter-configmap",
                                               "blazemeter-secret"):
                    names |= set(doc.get("data") or {})
                    names |= set(doc.get("stringData") or {})
                    names |= set(re.findall(r"^\s*# ([A-Z][A-Z0-9_]*): <",
                                            content, re.M))
    if opts.get("output_format") == "docker":
        names |= set(gen.docker_env(facts, {**gen.DEFAULT_OPTIONS, **opts}))
    return names


def test_reserved_env_is_what_the_bundles_actually_write():
    """`extra_env` refuses this set, so it has to be the real one from both
    ends. A name a template writes and this set does not carry is silently
    overridable -- two entries for one key in one ConfigMap; a name in the set
    that nothing writes refuses a variable the customer could have had.

    Emitted from the bundles rather than restated, because a set restated is a
    set that drifts -- which is the whole of what this file's ignored-option
    tests are about one layer up."""
    written = set()
    for facts, opts in ENV_COVERAGE:
        written |= _env_names(facts, opts)
    assert written == set(gen.RESERVED_ENV)


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
        .get(gen.DOCKER_ENV_FILE, ""))


def test_extra_env_refuses_a_variable_the_bundle_already_writes():
    """The collision rule. Refused rather than merged: two values for one key
    is a ConfigMap with a duplicate entry, and whichever wins is not the one
    the form that set it shows. The message names the option that owns it,
    because "set it there" is the whole answer."""
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
    """The reserved set is the union across formats, deliberately. A
    KUBERNETES_* variable reaches nothing on a docker host either, so accepting
    it as free-form env would carry a variable the agent ignores while reading
    as a setting that had been made."""
    with pytest.raises(ValueError):
        gen.generate(FACTS, {**DOCKER,
                             "extra_env": {"KUBERNETES_NODE_SELECTOR_JSON": "{}"}})


def test_extra_env_refuses_a_name_no_process_could_read():
    """A ConfigMap key may hold dots and dashes; an environment variable named
    with one is not reachable from the process that reads it, so a bundle
    carrying it applies cleanly and changes nothing."""
    for bad in ("my-var", "9LIVES", "a.b", ""):
        with pytest.raises(ValueError) as e:
            gen.generate(FACTS, {"ship_id": "bbb222", "extra_env": {bad: "x"}})
        assert "environment variable name" in str(e.value)


def test_extra_env_values_are_written_as_the_container_will_see_them():
    """A boolean typed as one arrives as `true`, not Python's `True`: every
    boolean the agent reads is the lower-case form, and one odd variable in a
    ConfigMap reads as a typo."""
    cm = yaml.safe_load(gen.generate(FACTS, {
        "ship_id": "bbb222",
        "extra_env": {"A": True, "B": 8080, "C": None},
    })["bzm_configmap.yaml"])
    assert cm["data"]["A"] == "true"
    assert cm["data"]["B"] == "8080"
    assert cm["data"]["C"] == ""
    # A structure is refused rather than JSON-encoded on the customer's behalf:
    # an environment variable is text, and guessing the encoding is how a
    # bundle comes to carry one crane cannot parse.
    with pytest.raises(ValueError):
        gen.generate(FACTS, {"ship_id": "bbb222", "extra_env": {"A": {"b": 1}}})


def test_extra_env_travels_in_the_profile():
    """It is an option, so a regenerate replays it -- which is the whole point:
    the alternative it replaces is editing the ConfigMap by hand, and that is
    what the next generate silently reverts."""
    profile = json.loads(gen.generate(FACTS, {
        "ship_id": "bbb222", "extra_env": {"PREFERRED_INTERFACE": "eth1"},
    })["profile.json"])
    assert profile["extra_env"] == {"PREFERRED_INTERFACE": "eth1"}


# -- the bundle that is complete except for the certificate (#230) ------------
#
# The common moment is not "I have a PEM" but "crane is failing TLS and I am
# waiting on my platform team". `ca_bundle_slot` is that answer: the bundle
# carries bzm_cacerts.yaml wired to the Deployment, with the PEM slot marked.
# It is a deliberate choice, not a field somebody forgot, which is the whole
# difference between it and leaving `ca_bundle` blank.


def test_the_slot_emits_a_configmap_wired_like_any_other():
    files = gen.generate(FACTS, {"namespace": "ns1", "ca_bundle_slot": True})
    _all_yaml_parse(files)
    assert "bzm_cacerts.yaml" in files
    cm = yaml.safe_load(files["bzm_cacerts.yaml"])
    assert cm["data"][gen.CA_FILENAME].strip() == gen.marker("ca_bundle")
    # ...and everything downstream is identical to a filled-in inline bundle:
    # the point is a bundle that needs one edit, not one that needs wiring.
    d = yaml.safe_load(files["bzm_deployment.yaml"])
    spec = d["spec"]["template"]["spec"]
    assert spec["volumes"][0]["configMap"]["name"] == gen.CA_CONFIGMAP
    assert spec["containers"][0]["volumeMounts"][0]["mountPath"] == gen.CA_MOUNT_PATH
    conf = yaml.safe_load(files["bzm_configmap.yaml"])["data"]
    assert conf["REQUESTS_CA_BUNDLE"] == f"{gen.CA_MOUNT_PATH}/{gen.CA_FILENAME}"


def test_the_slot_is_findable_in_the_file_somebody_edits():
    """The manifest is the artefact a human opens, and `<CA_BUNDLE>` says which
    field is missing without saying what shape the value has. A YAML comment
    never reaches the applied object, so it costs nothing at apply time."""
    files = gen.generate(FACTS, {"namespace": "ns1", "ca_bundle_slot": True})
    text = files["bzm_cacerts.yaml"]
    assert "#" in text and "BEGIN CERTIFICATE" in text
    # It stays a comment: the ConfigMap still parses, and the data is the marker
    # alone rather than the marker plus prose.
    assert yaml.safe_load(text)["data"][gen.CA_FILENAME].strip() \
        == gen.marker("ca_bundle")


def test_a_filled_bundle_carries_no_slot_comment():
    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
    text = gen.generate(FACTS, {"namespace": "ns1",
                                "ca_bundle": pem})["bzm_cacerts.yaml"]
    assert "paste" not in text.lower()


def test_the_slot_and_a_pem_are_two_answers_to_one_question():
    """Not a merge and not a precedence: somebody has said both `here is the
    certificate` and `the certificate is coming`, and only they know which."""
    with pytest.raises(ValueError, match="ca_bundle_slot"):
        gen.generate(FACTS, {"namespace": "ns1", "ca_bundle_slot": True,
                             "ca_bundle": "-----BEGIN CERTIFICATE-----\nx\n"
                                          "-----END CERTIFICATE-----"})


def test_the_slot_is_one_of_the_ca_modes_not_a_fourth_thing():
    """It competes with the other modes like any other: a bundle cannot both
    reference somebody's ConfigMap and carry its own."""
    with pytest.raises(ValueError, match="choose one CA mode"):
        gen.generate(FACTS, {"namespace": "ns1", "ca_bundle_slot": True,
                             "ca_existing_configmap": "corp-trust"})


def test_the_slot_says_what_is_missing_rather_than_that_somebody_forgot():
    """`placeholder_options` reports fields nobody answered, and this one was
    answered -- with `later`. Reporting it there would put "this bundle is not
    finished" over a bundle that is exactly what was asked for, beside a
    sentence claiming the API server will reject it, which for a ConfigMap
    value it will not."""
    files = gen.generate(FACTS, {"namespace": "ns1", "auth_token": "de" * 32,
                                 "ca_bundle_slot": True})
    readme = files["README.md"]
    assert gen.placeholder_options(json.loads(files[gen.PROFILE_FILE])) == []
    assert "not finished" not in readme
    # ...but it is not silent either: the bundle cannot work until the PEM
    # lands, and the README is where the person applying it finds that out.
    assert "bzm_cacerts.yaml" in readme
    assert "certificate" in readme.lower()


def test_the_docker_bundle_gets_the_same_slot_in_its_own_shape():
    """Docker writes the PEM as a file beside the script rather than into a
    ConfigMap, and its run script already refuses a placeholder file -- so the
    slot needs no new guard there, only the file."""
    files = gen.generate(FACTS, {"output_format": "docker", "ship_id": "bbb222",
                                 "auth_token": "de" * 32,
                                 "ca_bundle_slot": True})
    assert files[gen.DOCKER_CA_FILE] == gen.marker("ca_bundle")
