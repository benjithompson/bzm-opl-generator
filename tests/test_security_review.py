"""SECURITY-REVIEW.md: the document every bundle carries for an approval
board, the `review` command and the MCP action that print it.

The RBAC rules and security contexts in it are read from the rendered objects,
so these tests change a rendered rule and require the document to follow.
"""

import json
import re
from string import Template

import pytest
import yaml

from bzm_opl_gen import (bundle_names, bundle_options, cli, core, facts as facts_mod,
                         footprint, generate as gen, markers, mcp_server,
                         render_docker, render_manifests, security_review,
                         service_virt)
from test_generate import FACTS, SV_FACTS, SV_OPTS
from tls_fixtures import SV_CERT, SV_HOST, SV_KEY

REVIEW = bundle_names.REVIEW_FILE
TOKEN = "de" * 32
BASE = {"namespace": "ns1", "platform": "k8s", "auth_token": TOKEN}
FORMATS = ("manifests", "helm", "docker")


def review(o, facts=FACTS):
    return gen.generate(facts, o)[REVIEW]


def flat(text):
    """Text with its wrapping undone, for matching a phrase."""
    return " ".join(text.split())


def section(md, title):
    """The text of one `## title` section."""
    return md.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


# -- every format carries it ---------------------------------------------------

@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_carries_the_review(fmt):
    md = review({**BASE, "output_format": fmt})
    for title in ("What runs", "Images", "Network", "TLS trust", "Resources",
                  "Secrets"):
        assert f"\n## {title}\n" in md
    for host in footprint.ENGINE_UPLOAD_HOSTS:
        assert f"`{host}`" in flat(md)
    assert "`a.blazemeter.com`" in flat(md)
    assert "bzm-opl-gen ca-check --ca-bundle <ca-file>" in flat(md)
    assert bundle_names.IMAGES_FILE in flat(md)


def test_the_preview_lists_the_review_before_images_md():
    order = gen.preview_order(gen.generate(FACTS, BASE))
    assert order.index(REVIEW) == order.index(bundle_names.IMAGES_FILE) - 1


def test_a_manifests_review_lists_every_object_it_applies():
    files = gen.generate(FACTS, {**BASE, "cluster_rbac": True, "crane_hook": True})
    md = files[REVIEW]
    listed = re.findall(r"^\| (\w+) \| `([^`]+)` \| [^|]+ \| `([^`]+)` \|$",
                        section(md, "What runs"), re.M)
    rendered = [(d["kind"], d["metadata"]["name"], name)
                for name in [*bundle_names.APPLY_ORDER, bundle_names.HOOK_FILE]
                if name in files
                for d in yaml.safe_load_all(files[name]) if d]
    assert listed == rendered
    assert "| ClusterRole | `cluster-role-crane` | cluster |" in flat(md)


def test_a_chart_review_names_no_object_the_release_names():
    md = review({**BASE, "output_format": "helm", "cluster_rbac": True})
    assert "### ClusterRole (cluster-wide)" in flat(md)
    assert "`cluster-role-crane`" not in md
    assert "helm template crane ./helm -f bzm-opl-values.yaml" in flat(md)


def test_a_docker_review_describes_the_host_not_a_cluster():
    o = {**BASE, "output_format": "docker", "namespace": "ns-docker",
         "cluster_rbac": True, "crane_hook": True, "run_as_user": 4242}
    md = review(o)
    assert "Kubernetes permissions" not in md and "Pod security" not in md
    assert "ClusterRole" not in md and "crane-hook" not in md
    # Set, but ignored by this format: never claimed.
    assert "ns-docker" not in md and "4242" not in md
    assert "/var/run/docker.sock" in section(md, "Host access")
    assert f"`{render_docker.DOCKER_PORT_RANGE}`" in flat(md)
    ignored = bundle_options.IGNORED_BY_FORMAT["docker"]
    assert ignored["restrict_engines"] in flat(md)


# -- RBAC, read from the rendered objects --------------------------------------

RBAC_CASES = [
    {},
    {"cluster_rbac": True},
    {"crane_hook": True},
    *({"sv_ingress": b, "sv_subdomain": "apps.example.com",
       "sv_tls_secret": "wild", "platform": "openshift",
       "openshift_cluster": True}
      for b in service_virt.SV_INGRESS_TYPES),
]


@pytest.mark.parametrize("extra", RBAC_CASES)
def test_the_rules_read_are_the_rules_rendered(extra):
    facts = SV_FACTS if "sv_ingress" in extra else FACTS
    files = gen.generate(facts, {**BASE, **extra})
    read = security_review.rbac(files)
    rendered = [d for name in files if name.endswith(".yaml")
                for d in yaml.safe_load_all(files[name])
                if d and d["kind"] in ("Role", "ClusterRole")]
    assert sorted((r["kind"], r["name"]) for r in read) == \
        sorted((d["kind"], d["metadata"]["name"]) for d in rendered)
    by_name = {d["metadata"]["name"]: d["rules"] for d in rendered}
    for role in read:
        assert role["rules"] == by_name[role["name"]]


@pytest.mark.parametrize("fmt", ["manifests", "helm"])
def test_a_rule_changed_in_the_template_reaches_the_review(fmt, monkeypatch):
    before = review({**BASE, "output_format": fmt})
    assert "escalate" not in before
    tpl = render_manifests._tpl

    def widened(name):
        t = tpl(name)
        if name == "role.yaml":
            return Template(t.template.replace(
                "verbs: [create]\n", "verbs: [create, escalate]\n", 1))
        return t

    monkeypatch.setattr(render_manifests, "_tpl", widened)
    after = review({**BASE, "output_format": fmt})
    assert "| core | `pods/exec` | create, escalate |" in after


def test_the_service_virtualization_rule_names_the_backend_group():
    md = review({**SV_OPTS, "platform": "k8s", "auth_token": TOKEN}, SV_FACTS)
    backend = service_virt.SV_INGRESS_BACKENDS[SV_OPTS["sv_ingress"]]
    assert f"| `{backend.group}` |" in section(md, "Kubernetes permissions")
    assert f"one {backend.creates} (`{backend.group}`)" in flat(md)


def test_a_rule_that_cannot_be_read_is_refused_not_shortened():
    with pytest.raises(ValueError, match="could not read"):
        security_review.rules(
            'kind: Role\nrules:\n  - apiGroups: [""]\n    resources: [pods]\n')


def test_a_role_with_no_rules_reads_as_empty():
    assert security_review.rules("kind: Role\nmetadata: { name: r }\n") == []


def test_the_cluster_role_is_called_read_only_only_when_it_is(monkeypatch):
    assert "grants reads only" in review({**BASE, "cluster_rbac": True})
    tpl = render_manifests._tpl

    def writable(name):
        t = tpl(name)
        if name == "clusterrole.yaml":
            return Template(t.template.replace("[get, list, watch]",
                                               "[get, list, watch, patch]"))
        return t

    monkeypatch.setattr(render_manifests, "_tpl", writable)
    assert "grants more than reads" in review({**BASE, "cluster_rbac": True})


# -- pod security ----------------------------------------------------------------

def test_the_security_contexts_are_the_rendered_ones():
    files = gen.generate(FACTS, BASE)
    dep = yaml.safe_load(files[bundle_names.DEPLOYMENT_FILE])
    shown = section(files[REVIEW], "Pod security").split("```yaml\n", 1)[1]
    shown = yaml.safe_load(shown.split("```", 1)[0].replace(
        "# the crane container\nsecurityContext:", "container:"))
    assert shown["container"] == dep["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    assert "Crane runs as UID and GID `1337`" in flat(files[REVIEW])
    assert "meets the `restricted` Pod Security Standard" in flat(files[REVIEW])


def test_an_openshift_review_pins_no_uid():
    md = review({**BASE, "platform": "openshift"})
    assert "sets no UID" in flat(md) and "runAsUser" not in section(md, "Pod security")


def test_unrestricted_engines_are_named_as_privileged():
    on = section(review(BASE), "Pod security")
    off = section(review({**BASE, "restrict_engines": False}), "Pod security")
    assert "INHERIT_RUNNING_USER_AND_GROUP: 'true'" in flat(on)
    assert "Engines run privileged" in flat(off) and "INHERIT_RUNNING" not in flat(off)


def test_the_hook_pod_is_judged_by_its_own_context():
    md = review({**BASE, "crane_hook": True})
    assert "crane-hook check pod meets the `restricted`" in flat(md)
    assert security_review.restricted_gaps(["runAsNonRoot: true"]) == [
        "allowPrivilegeEscalation: false", "capabilities dropped (drop: ALL)",
        "seccompProfile: RuntimeDefault"]


# -- network, trust, resources ---------------------------------------------------

def test_the_registry_row_follows_the_images():
    md = review({**BASE, "private_registry": "reg.corp:5001/bzm"})
    assert "| `reg.corp` | 5001 | each node's container runtime |" in flat(md)
    assert "--registry reg.corp:5001/bzm" in flat(md)
    assert "| `gcr.io` | 443 |" in review(BASE)


@pytest.mark.parametrize("extra, words", [
    ({"ca_bundle": "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----"},
     "an inline PEM"),
    ({"ca_existing_configmap": "corp-ca"}, "an existing ConfigMap"),
    ({"ca_bundle_slot": True, "ca_cert_file": "corp.pem"},
     "a certificate file the ConfigMap is built from"),
    ({"ca_openshift_inject": True, "platform": "openshift",
      "openshift_cluster": True}, "OpenShift injection"),
])
def test_each_ca_mode_is_named(extra, words):
    md = section(review({**BASE, **extra}), "TLS trust")
    assert f"The CA comes from {words}" in flat(md)
    assert "bzm-opl-gen ca-check" in flat(md)


def test_the_proxy_is_shown_without_its_credentials():
    o = {**BASE, "proxy": {"http": "http://px:3128", "https": "http://px:3128",
                           "username": "proxyuser", "password": "xyzzy plugh",
                           "no_proxy": "*.corp.example"}}
    md = review(o)
    assert "HTTPS_PROXY=http://<credentials>@px:3128" in flat(md)
    assert "NO_PROXY=*.corp.example" in flat(md)
    assert "proxyuser" not in md and "xyzzy plugh" not in md
    assert "--proxy <proxy-url>" in flat(md)


def test_resources_add_up_for_the_locations_slots():
    md = section(review(BASE, dict(FACTS, slots=3)), "Resources")
    assert "| Crane (one, always running) | 250m | 1 | 512Mi | 2Gi |" in flat(md)
    assert "| Each engine | 2 | 2 | 8Gi | 8Gi |" in flat(md)
    assert "request 6250m CPU" in flat(md) and "limits of 7 CPU and 26Gi" in flat(md)
    assert "3 such nodes" in flat(md)


def test_resources_follow_the_engine_size_and_the_overrides():
    md = section(review({**BASE, "engine_cpu_limit": "1",
                         "engine_mem_limit": "4Gi"}), "Resources")
    assert "| Each engine | 1 | 1 | 4Gi | 4Gi |" in flat(md)
    over = section(review(BASE, dict(FACTS, override_cpu=1,
                                     override_memory=4096)), "Resources")
    assert "overrideCPU" in flat(over)


def test_an_unread_slot_count_claims_no_node_count():
    md = review(BASE, {k: v for k, v in FACTS.items() if k != "slots"})
    assert "not read here" in flat(md) and "such node" not in flat(md)


def test_unread_functionalities_are_not_called_none():
    unread = review(BASE, {k: v for k, v in FACTS.items() if k != "func_ids"})
    empty = review(BASE, dict(FACTS, func_ids=[]))
    assert "| Functionalities | not read |" in flat(unread)
    assert "| Functionalities | none |" in flat(empty)


def test_an_uncovered_functionality_is_named():
    md = review(BASE, dict(FACTS, func_ids=["performance", "tdm"]))
    assert "`tdm`: this bundle does not configure it." in flat(md)


# -- secrets ---------------------------------------------------------------------

@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("use_secret", [True, False])
def test_the_review_never_carries_a_credential(fmt, use_secret):
    o = {**BASE, "output_format": fmt, "use_secret": use_secret,
         "proxy": {"http": "http://px:3128", "username": "u",
                   "password": "xyzzy plugh"}}
    if fmt == "docker":
        o.update(sv_hostname=SV_HOST, sv_tls_cert=SV_CERT, sv_tls_key=SV_KEY)
    md = review(o)
    assert TOKEN not in md and "xyzzy plugh" not in md
    assert "PRIVATE KEY" not in md
    if not use_secret:
        assert "AUTH_TOKEN is in plain text" in flat(md)


def test_the_secret_row_lists_the_keys_rendered():
    files = gen.generate(FACTS, {**BASE, "proxy": {
        "https": "http://px:3128", "username": "u", "password": "p"}})
    keys = yaml.safe_load(files[bundle_names.SECRET_FILE])["stringData"]
    shown = ", ".join(f"`{k}`" for k in keys)
    assert f"The Secret `blazemeter-secret` holds {shown}." in \
        files[REVIEW].replace("\n  ", " ")


def test_a_chart_token_passed_at_install_is_said_to_be_in_no_file():
    md = review({**BASE, "output_format": "helm", "auth_token": ""})
    assert "The AUTH_TOKEN is in no file of this bundle" in flat(md)


def test_the_profile_omissions_are_the_secret_options():
    md = review(BASE)
    for key in gen.SECRET_OPTIONS:
        assert f"`{key}`" in section(md, "Secrets")


# -- customer-facing text --------------------------------------------------------

@pytest.mark.parametrize("fmt", FORMATS)
def test_a_blank_field_is_a_lower_case_sample(fmt):
    """Invariant 2: only the files a field is filled in carry its marker."""
    facts = facts_mod.manual("", "")
    md = gen.generate(facts, {"output_format": fmt, "namespace": "",
                              "service_account_name": "",
                              "auth_token": ""})[REVIEW]
    assert not markers.MARKER_RE.search(md)
    assert "`<harbor-id>`" in flat(md)
    if fmt != "docker":
        assert "`<namespace>`" in flat(md) and "`<service-account-name>`" in flat(md)


@pytest.mark.parametrize("fmt", FORMATS)
def test_the_review_is_customer_facing(fmt):
    """Invariant 8: no issue numbers, file names of this tool's source or
    internal names."""
    md = review({**BASE, "output_format": fmt, "crane_hook": True,
                 "private_registry": "reg.corp/bzm"})
    assert not re.search(
        r"#\d|\.py\b|security_review|footprint|image_catalog|render_|"
        r"fallback-catalogue|FALLBACK|restated|helm_parity", md)


# -- the command and the MCP action ----------------------------------------------

def test_core_refuses_options_the_generator_refuses():
    with pytest.raises(core.BadRequest, match="output_format"):
        core.security_review(FACTS, {"output_format": "rpm"})


def test_the_command_prints_the_document_or_writes_it(tmp_path, monkeypatch, capsys):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps(FACTS))
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"namespace": "ns9", "platform": "k8s"}))
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", "review", "--facts",
                                     str(facts), "--profile", str(profile)])
    cli.main()
    out = capsys.readouterr().out
    assert out == core.security_review(FACTS, {"namespace": "ns9",
                                               "platform": "k8s"})
    target = tmp_path / "review.md"
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", "review", "--facts",
                                     str(facts), "-o", str(target)])
    cli.main()
    assert target.read_text() == core.security_review(FACTS, {})


def test_the_mcp_action_answers_the_document_and_refuses_a_secret():
    body = mcp_server._bundle("review", {"facts": FACTS,
                                         "options": {"namespace": "ns1"}})
    assert body["document"] == core.security_review(FACTS, {"namespace": "ns1"})
    assert body["file"] == REVIEW
    with pytest.raises(core.BadRequest, match="credential"):
        mcp_server._bundle("review", {"facts": FACTS,
                                      "options": {"auth_token": TOKEN}})
