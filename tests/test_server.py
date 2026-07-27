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


def test_sv_constants_carry_what_each_backend_publishes():
    """The UI tells the user which Role the bundle grants and what crane creates
    with it. That is SV_INGRESS_BACKENDS -- restating it in TypeScript is the
    same duplication the funcId list was just deleted for, and it would go stale
    silently, because a wrong Role reads as plausible right up until the virtual
    service stalls."""
    from bzm_opl_gen import generate as gen_mod
    backends = client.get("/api/sv-constants").json()["backends"]
    assert set(backends) == set(gen_mod.SV_INGRESS_TYPES)
    for name, b in gen_mod.SV_INGRESS_BACKENDS.items():
        assert backends[name] == {"group": b.group, "resources": list(b.resources),
                                  "creates": b.creates}
    # routes/custom-host is the one nobody would guess: OpenShift gates
    # spec.host behind it, and crane sets spec.host.
    assert "routes/custom-host" in backends["openshift"]["resources"]


def test_func_id_choices_cover_the_whole_generator_vocabulary():
    """A location whose funcId the UI never offers can only be created from the
    CLI or the BlazeMeter web app -- which is what happened to sv-bridge while
    the checkbox list was a hardcoded copy in TypeScript. Serving it from the
    funcId vocabulary the facts layer already keys its image selection off means
    adding a funcId there is enough to make it selectable."""
    from bzm_opl_gen import facts as facts_mod
    body = client.get("/api/func-ids").json()
    assert [c["id"] for c in body] == list(facts_mod.CATEGORY_BY_FUNC)
    ids = {c["id"] for c in body}
    assert {"mockServices", "sv-bridge"} <= ids   # both SV funcIds reach the UI
    assert all(c["label"] for c in body)


def test_unlabelled_func_id_is_still_offered(monkeypatch):
    """The label map is presentation only, so a funcId added to the facts layer
    without one must still appear under its raw name -- the same deliberate
    failure mode as the SV ingress picker. Dropping it would hide the feature
    exactly like the hardcoded list did."""
    from bzm_opl_gen import facts as facts_mod
    monkeypatch.setitem(facts_mod.CATEGORY_BY_FUNC, "tdm", {"performance"})
    body = client.get("/api/func-ids").json()
    assert {"id": "tdm", "label": "tdm"} in body


def test_create_location_forwards_every_selected_func_id(monkeypatch):
    """The funcIds the form submits must reach the API verbatim; sv-bridge in
    particular has no other way in from the UI."""
    seen = {}

    class FakeClient:
        def create_private_location(self, name, account_id, workspace_ids, **kw):
            seen.update(kw, name=name, workspaces=workspace_ids)
            return {"id": "h9", "name": name, "funcIds": kw["func_ids"]}

    monkeypatch.setitem(server._state, "client", FakeClient())
    r = client.post("/api/locations", json={
        "name": "sv-loc", "account_id": 1, "workspace_id": 2,
        "func_ids": ["mockServices", "sv-bridge"]})
    assert r.status_code == 200
    assert seen["func_ids"] == ["mockServices", "sv-bridge"]
    assert r.json()["funcIds"] == ["mockServices", "sv-bridge"]


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
                              "harbor": "aaa111", "ship": "bbb222",
                              # Carried so the UI never rebuilds this string --
                              # it is the same host the Ingress below routes.
                              "host": "vs1svc2-8080-ns1.apps.example.com"}]
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


# -- how the server is bound ---------------------------------------------------

def _served(monkeypatch, **kw):
    """Run main() without actually serving, and report what it asked for."""
    import uvicorn
    calls = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, **k: calls.update(app=app, **k))
    server.main(open_browser=False, **kw)
    return calls


def test_ui_binds_loopback_unless_told_otherwise(monkeypatch, capsys):
    """The default has to stay 127.0.0.1: this server holds a BzmClient in
    process memory, so anything that can reach it can act as the API key."""
    assert _served(monkeypatch)["host"] == "127.0.0.1"
    assert "reachable" not in capsys.readouterr().out


def test_ui_warns_when_the_bind_leaves_this_machine(monkeypatch, capsys):
    """Widening the bind is a real exposure, and the one irreversible thing
    behind it is the download button: fetching an AUTH_TOKEN rotates it, so a
    stray click leaves a running agent stuck on a token that no longer works.
    Say so at startup, where it is still cheap to reconsider."""
    assert _served(monkeypatch, host="0.0.0.0")["host"] == "0.0.0.0"
    warning = capsys.readouterr().out
    assert "reachable" in warning and "AUTH_TOKEN" in warning


def test_ui_dev_mode_binds_the_same_host(monkeypatch):
    """--dev reloads through an import string, a second uvicorn.run call that
    used to hardcode its own host -- an easy place for the flag to go missing."""
    assert _served(monkeypatch, host="0.0.0.0", dev=True)["host"] == "0.0.0.0"


# -- the deployed virtual services, alongside the heartbeat --------------------
# The agent reports idle whether or not its virtual services ever became
# reachable, so a stall is invisible in the watch panel. This is the same
# cluster read as /api/sv-expose, minus the rendering: it answers what is
# deployed and at which host, cheaply enough to sit on the existing 10s poll.

def test_sv_mocks_lists_what_is_deployed_and_where_it_answers(fake_cluster):
    fake_cluster(stdout=SV_PODS)
    body = client.get("/api/sv-mocks",
                      params={"namespace": "ns1",
                              "sv_subdomain": "apps.example.com"}).json()
    assert body["status"] == "ok"
    # The host is the one BlazeMeter advertises, so it can be pasted straight
    # into a browser -- and it is built by the generator, not restated here.
    assert body["mocks"] == [{"name": "vs1svc2", "port": 8080,
                              "host": "vs1svc2-8080-ns1.apps.example.com"}]


def test_sv_mocks_separates_deployed_nothing_from_cannot_look(fake_cluster):
    """An empty namespace and an unreadable cluster are different answers: one
    says the virtual service has not deployed yet, the other says this machine
    cannot tell you either way. The watch panel has to say which."""
    fake_cluster(stdout=json.dumps({"items": []}))
    empty = client.get("/api/sv-mocks", params={"namespace": "ns1"}).json()
    assert empty["status"] == "no_mocks" and empty["mocks"] == []
    assert empty["message"]

    fake_cluster(tools=())
    blind = client.get("/api/sv-mocks", params={"namespace": "ns1"}).json()
    assert blind["status"] == "no_cli" and blind["mocks"] == []
    assert "kubectl" in blind["message"] or "oc" in blind["message"]


def test_sv_mocks_without_a_subdomain_still_lists_the_mocks(fake_cluster):
    """The subdomain lives in the options, and the panel polls whether or not
    one is set yet. Losing the host is fine; losing the list is not."""
    fake_cluster(stdout=SV_PODS)
    body = client.get("/api/sv-mocks", params={"namespace": "ns1"}).json()
    assert [m["name"] for m in body["mocks"]] == ["vs1svc2"]
    assert body["mocks"][0]["host"] is None


def test_sv_mocks_never_errors_the_poll(fake_cluster):
    """It rides the status poll, which keeps the last good answer on failure.
    A 4xx/5xx here would either spam the console every 10s or, worse, be
    swallowed and read as 'no virtual services'."""
    fake_cluster(rc=1, stderr="error: current-context is not set")
    r = client.get("/api/sv-mocks", params={"namespace": "ns1"})
    assert r.status_code == 200 and r.json()["status"] == "no_context"
