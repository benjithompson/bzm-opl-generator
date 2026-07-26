import io
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bzm_opl_gen import server  # noqa: E402
from test_generate import FACTS  # noqa: E402

client = TestClient(server.app)


def test_generate_preview_no_key_needed():
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "fetch_token": False})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert names[0] == "bzm_serviceaccount.yaml"      # apply order
    assert "bzm_deployment.yaml" in names and "README.md" in names


def test_generate_zip_mirror_script_executable():
    r = client.post("/api/generate/zip", json={
        "facts": FACTS,
        "options": {"namespace": "ns1", "private_registry": "reg.local/bzm"},
        "fetch_token": False})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    info = z.getinfo("bzm-opl/bzm-opl-image-mirror.sh")
    assert info.external_attr >> 16 & 0o111          # executable bits
    assert "bzm-opl/bzm_configmap.yaml" in z.namelist()


def test_generate_invalid_options_400():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)    # ambiguous ship
    r = client.post("/api/generate", json={
        "facts": facts, "options": {}, "fetch_token": False})
    assert r.status_code == 400


def test_profiles_and_defaults():
    r = client.get("/api/profiles")
    assert {p["name"] for p in r.json()} >= {
        "standard", "private-registry", "proxy-ca"}
    r2 = client.get("/api/option-defaults")
    assert r2.json()["platform"] == "openshift"


def test_sv_constants_are_served_from_the_generator():
    """The UI renders the ingress picker and decides the SV group is mandatory
    from these. Served rather than copied into TypeScript, so a new backend
    cannot be added to generate() and silently miss the picker."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/sv-constants").json()
    assert body["func_ids"] == list(gen_mod.SV_FUNC_IDS)
    assert body["ingress_types"] == list(gen_mod.SV_INGRESS_TYPES)
    assert "openshift" in body["ingress_types"]     # the newest one reaches the UI
    # Kept out of option-defaults: that response is spread into the options the
    # UI submits, and these are not options.
    assert "ingress_types" not in client.get("/api/option-defaults").json()


def test_api_requires_key():
    assert client.get("/api/accounts").status_code == 401


# -- sv-expose from the browser ------------------------------------------------
# The one endpoint that reads a cluster. It is optional everywhere: the UI is
# API-only by design and most people running it have no kubecontext, so each way
# the read can fail comes back 200 with the reason and a runnable CLI equivalent
# rather than an error the browser can only print.

import argparse  # noqa: E402
import json  # noqa: E402

import yaml  # noqa: E402

from bzm_opl_gen import cli as cli_mod, generate as gen_mod, livetest  # noqa: E402
# The faked kubectl lives with the other cluster-reading tests; reused rather
# than re-declared so both layers exercise the same stand-in binary.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402

SV_EXPOSE_BODY = {"namespace": "ns1", "sv_subdomain": "apps.example.com",
                  "sv_tls_secret": "wildcard-tls",
                  "sv_ingress_class": "openshift-default"}
SV_PODS = json.dumps({"items": [_sv_pod("vs1svc2", 8080, "aaa111", "bbb222"),
                                _sv_pod(None, 5000, extra={"role": "role-crane"})]})


@pytest.fixture
def fake_cluster(monkeypatch):
    """Installs a faked kubectl/oc for one test. Yields the installer so a test
    can choose what the binary does; always clears cli_tool()'s memo of which
    binary exists, which otherwise outlives the monkeypatch."""
    def install(**kw):
        _fake_kubectl(monkeypatch, **kw)
    install()
    yield install
    livetest.cli_tool.cache_clear()


def test_sv_expose_renders_the_deployed_mocks(fake_cluster):
    fake_cluster(stdout=SV_PODS)
    body = client.post("/api/sv-expose", json=SV_EXPOSE_BODY).json()
    assert body["status"] == "ok"
    # Only the mock pod; crane's own pod carries none of the identity labels.
    assert body["mocks"] == [{"name": "vs1svc2", "port": 8080,
                              "harbor": "aaa111", "ship": "bbb222"}]
    assert [f["name"] for f in body["files"]] == [gen_mod.SV_EXPOSE_FILE]
    yamls = list(yaml.safe_load_all(body["files"][0]["content"]))
    assert [d["kind"] for d in yamls] == ["Service", "Ingress"]
    assert yamls[0]["metadata"]["namespace"] == "ns1"


def test_sv_expose_output_is_byte_identical_to_the_cli(fake_cluster, tmp_path):
    """The acceptance criterion this endpoint exists to keep: one renderer. The
    UI reaches generate.sv_expose through the same sv_publish_cfg resolution the
    command does, so the file it offers is the file `sv-expose` writes."""
    fake_cluster(stdout=SV_PODS)
    body = client.post("/api/sv-expose", json=SV_EXPOSE_BODY).json()
    out = tmp_path / gen_mod.SV_EXPOSE_FILE
    cli_mod.cmd_sv_expose(argparse.Namespace(
        manifests="", namespace="ns1", sv_subdomain="apps.example.com",
        sv_tls_secret="wildcard-tls", ingress_class="openshift-default",
        output=str(out)))
    assert body["files"][0]["content"] == out.read_text()


def test_sv_expose_ingress_class_is_settable_and_reaches_the_ingress(fake_cluster):
    """No UI path could set this before. It matters because we own this Ingress:
    it can name the class the cluster really has (openshift-default), which is
    what removes the need for an `nginx` alias on OpenShift."""
    fake_cluster(stdout=SV_PODS)
    def ing(body):
        r = client.post("/api/sv-expose", json=body).json()
        return next(d for d in yaml.safe_load_all(r["files"][0]["content"])
                    if d["kind"] == "Ingress")
    assert ing(SV_EXPOSE_BODY)["spec"]["ingressClassName"] == "openshift-default"
    unset = {**SV_EXPOSE_BODY, "sv_ingress_class": None}
    assert (ing(unset)["spec"]["ingressClassName"]
            == gen_mod.SV_EXPOSE_DEFAULT_INGRESS_CLASS)


@pytest.mark.parametrize("status, kw", [
    # Four distinct answers, because four distinct things went wrong -- and a
    # bare "could not read the cluster" tells nobody what to do next.
    ("no_cli", {"tools": ()}),
    ("no_context", {"rc": 1, "stderr": "The connection to the server "
                    "localhost:8080 was refused - did you specify the right "
                    "host or port?"}),
    ("denied", {"rc": 1, "stderr": 'Error from server (Forbidden): pods is '
                'forbidden: User "dev" cannot list resource "pods"'}),
    ("no_mocks", {"stdout": json.dumps({"items": []})}),
])
def test_sv_expose_distinguishes_every_unreachable_cluster(fake_cluster, status, kw):
    fake_cluster(**kw)
    r = client.post("/api/sv-expose", json=SV_EXPOSE_BODY)
    body = r.json()
    # Not an HTTP error: no cluster is an expected state for this server, and a
    # 4xx/5xx leaves the browser with nothing but a red string.
    assert r.status_code == 200
    assert body["status"] == status
    assert body["files"] == [] and body["mocks"] == []
    assert body["message"]                       # says what happened
    # ...and every one of them hands over the command to run where there IS
    # access, prefilled with what the user has already typed.
    cmd = body["command"]
    assert cmd.startswith("bzm-opl-gen sv-expose ")
    assert "--namespace ns1" in cmd
    assert "--sv-subdomain apps.example.com" in cmd
    assert "--sv-tls-secret wildcard-tls" in cmd
    assert "--ingress-class openshift-default" in cmd


def test_sv_expose_command_does_not_send_the_user_to_a_profile_they_lack():
    """--manifests defaults to out/ and the CLI reads profile.json from it,
    which a browser user need never have downloaded. Every option is on the
    command line instead, so the suggestion runs from any directory."""
    cmd = server._sv_expose_command(server.SvExposeIn(**SV_EXPOSE_BODY))
    assert "--manifests ''" in cmd


def test_sv_expose_needs_a_wildcard_domain(fake_cluster):
    """The one genuine input error: without it there is no host to route, and
    the CLI equivalent would fail the same way. Distinct from the cluster
    outcomes, which are not the user getting anything wrong."""
    r = client.post("/api/sv-expose", json={"namespace": "ns1"})
    assert r.status_code == 400 and "sv_subdomain" in r.json()["detail"]


def test_no_cluster_access_leaves_the_rest_of_the_api_working(fake_cluster):
    """The rule this endpoint must not break: nothing else may start depending
    on a reachable cluster. With no kubectl on the machine at all, every other
    route answers exactly as it does with one."""
    fake_cluster(tools=())
    assert client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "fetch_token": False}).status_code == 200
    assert client.get("/api/profiles").status_code == 200
    assert client.get("/api/option-defaults").status_code == 200
    assert client.get("/api/sv-constants").status_code == 200
