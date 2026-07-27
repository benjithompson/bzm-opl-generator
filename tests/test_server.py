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
    CLI or the BlazeMeter web app -- which is what a hardcoded copy of the list
    in TypeScript caused. Serving it from the funcId vocabulary the facts layer
    already keys its image selection off means adding one there is enough to
    make it selectable, and retiring one removes it from the form."""
    from bzm_opl_gen import facts as facts_mod
    body = client.get("/api/func-ids").json()
    assert [c["id"] for c in body] == list(facts_mod.CATEGORY_BY_FUNC)
    ids = {c["id"] for c in body}
    assert {"mockServices", "proxyRecorder"} <= ids
    assert "sv-bridge" not in ids                 # retired, so not offered
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


def test_features_are_served_with_a_label_and_a_suggested_namespace():
    """The configure step shows one feature's options at a time and offers this
    list. Served rather than written in TypeScript for the same reason as the
    funcId choices: functional testing, secrets and API monitoring are expected
    to follow, and a feature has to become selectable by being added here."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/features").json()
    assert [f["id"] for f in body] == [f["id"] for f in server.FEATURES]
    assert body[0]["id"] == "performance"       # the common case is the default
    for f in body:
        assert f["label"] and f["namespace"] and f["func_ids"]
    sv = next(f for f in body if f["id"] == "sv")
    # Which funcIds mean service virtualization is generate.SV_FUNC_IDS', not a
    # second list -- the same reason /api/sv-constants exists.
    assert sv["func_ids"] == list(gen_mod.SV_FUNC_IDS)
    # Distinct namespaces are the point of suggesting one per feature: sharing a
    # namespace is what makes redeploying one agent take the other's pods down.
    assert len({f["namespace"] for f in body}) == len(body)


def test_a_feature_added_to_the_vocabulary_is_offered(monkeypatch):
    """The end-to-end shape of adding a feature: one entry here, plus a tag on
    whichever option groups it owns. Nothing in the frontend enumerates
    features, so this is the whole of the backend half."""
    monkeypatch.setattr(server, "FEATURES", server.FEATURES + [
        {"id": "secrets", "label": "Private vault", "hint": "secrets from a vault",
         "namespace": "blazemeter-vault", "func_ids": ["secretsPrivateVault"]}])
    body = client.get("/api/features").json()
    assert body[-1] == {"id": "secrets", "label": "Private vault",
                        "hint": "secrets from a vault",
                        "namespace": "blazemeter-vault",
                        "func_ids": ["secretsPrivateVault"]}


def test_every_modelled_func_id_belongs_to_a_feature():
    """A funcId the facts layer models but no feature claims would leave a
    location carrying only that one with no feature to start on. The reverse is
    deliberately allowed: a feature may claim a funcId that needs no images of
    its own (tdm and delphix are already in that position), and the funcIds the
    tool does not model at all stay unclaimed -- the selector reads those as no
    signal rather than as an error."""
    from bzm_opl_gen import facts as facts_mod
    claimed = {f for feat in server.FEATURES for f in feat["func_ids"]}
    assert set(facts_mod.CATEGORY_BY_FUNC) <= claimed
    assert "tdm" not in claimed


def test_create_location_forwards_every_selected_func_id(monkeypatch):
    """The funcIds the form submits must reach the API verbatim -- for several
    of them the UI is the only way in short of the BlazeMeter web app."""
    seen = {}

    class FakeClient:
        def create_private_location(self, name, account_id, workspace_ids, **kw):
            seen.update(kw, name=name, workspaces=workspace_ids)
            return {"id": "h9", "name": name, "funcIds": kw["func_ids"]}

    monkeypatch.setitem(server._state, "client", FakeClient())
    r = client.post("/api/locations", json={
        "name": "sv-loc", "account_id": 1, "workspace_id": 2,
        "func_ids": ["mockServices", "proxyRecorder"]})
    assert r.status_code == 200
    assert seen["func_ids"] == ["mockServices", "proxyRecorder"]
    assert r.json()["funcIds"] == ["mockServices", "proxyRecorder"]


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


# -- does the endpoint answer? -------------------------------------------------
# The listed mocks are running pods, which says nothing about whether anything
# routes to them -- crane's nginx Ingress names a port its own Service does not
# expose, so the published endpoint 503s while the pod is healthy. This is the
# only outbound HTTP request the server makes that is not the BlazeMeter API,
# and every test of it fakes that request: a real one would pass or fail on
# whatever DNS answered that day, and the failure kinds below cannot be
# provoked from a machine that may have no network at all.

import socket  # noqa: E402
import ssl  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


class _FakeResponse:
    """The little of http.client.HTTPResponse that the probe touches."""

    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Nothing in this module reaches the network, and a test that starts to
    must say so rather than depend on the host it happened to hit."""
    def refuse(*a, **kw):
        raise AssertionError("a test made a real HTTP request")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def fake_endpoint(monkeypatch):
    """Stand in for the virtual service's endpoint. Yields (install, calls):
    `install` takes a status code to answer with or an exception to raise, and
    `calls` records what the probe asked for, so the URL and the deadline are
    assertable rather than taken on trust."""
    calls = []

    def install(answer):
        def urlopen(req, timeout=None, **kw):
            calls.append({"url": getattr(req, "full_url", req), "timeout": timeout})
            if isinstance(answer, Exception):
                raise answer
            return _FakeResponse(answer)
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    return install, calls


def test_sv_check_reports_the_status_code_when_the_endpoint_answers(fake_endpoint):
    install, calls = fake_endpoint
    install(200)
    body = client.get("/api/sv-check",
                      params={"host": "vs1-8080-ns1.apps.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 200
    assert "200" in body["message"]
    assert calls[0]["url"] == "http://vs1-8080-ns1.apps.example.com/"


def test_sv_check_probes_the_host_the_panel_already_shows(fake_cluster, fake_endpoint):
    """The one string that must not be rebuilt: what is checked has to be what
    the row above it displays, or a green tick would be vouching for an address
    nobody was given. Handed back from /api/sv-mocks untouched."""
    fake_cluster(stdout=SV_PODS)
    host = client.get("/api/sv-mocks",
                      params={"namespace": "ns1",
                              "sv_subdomain": "apps.example.com"}
                      ).json()["mocks"][0]["host"]
    install, calls = fake_endpoint
    install(200)
    client.get("/api/sv-check", params={"host": host})
    assert calls[0]["url"] == f"http://{host}/"


def test_sv_check_reads_a_503_as_a_diagnosis_not_a_failure(fake_endpoint):
    """The whole point of the button. A 503 from the ingress controller is the
    answer -- it is what crane's port mismatch looks like from outside -- so it
    reports the status it got and names the command that fixes it."""
    install, _ = fake_endpoint
    install(urllib.error.HTTPError(
        "http://vs1-8080-ns1.apps.example.com/", 503, "Service Unavailable",
        {}, None))
    r = client.get("/api/sv-check",
                   params={"host": "vs1-8080-ns1.apps.example.com"})
    assert r.status_code == 200
    body = r.json()
    # It answered: this is not one of the failure kinds.
    assert body["status"] == "ok" and body["code"] == 503
    assert "sv-expose" in body["message"]


def test_sv_check_reports_any_other_http_status_it_gets(fake_endpoint):
    """404 from the mock itself is a routed endpoint, and the panel must not
    round it up to a failure the way a 503 diagnosis would."""
    install, _ = fake_endpoint
    install(urllib.error.HTTPError("http://h/", 404, "Not Found", {}, None))
    body = client.get("/api/sv-check", params={"host": "h.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 404
    assert "sv-expose" not in body["message"]


@pytest.mark.parametrize("status, error", [
    # Four distinct answers because four distinct things went wrong, and each
    # has its own way forward: no DNS record, nothing listening, a certificate
    # this machine will not accept, or a host that never replied.
    ("dns", urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known"))),
    ("refused", urllib.error.URLError(
        ConnectionRefusedError(61, "Connection refused"))),
    ("tls", urllib.error.URLError(ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self signed certificate (_ssl.c:1000)"))),
    ("timeout", urllib.error.URLError(socket.timeout("timed out"))),
    # A read that stalls after the connect succeeds surfaces bare, not wrapped.
    ("timeout", TimeoutError("timed out")),
    # Anything unforeseen still comes back as an answer, never a traceback.
    ("error", urllib.error.URLError("<unknown>")),
])
def test_sv_check_tells_the_failure_kinds_apart(fake_endpoint, status, error):
    install, _ = fake_endpoint
    install(error)
    r = client.get("/api/sv-check", params={"host": "vs1-8080-ns1.example.com"})
    # Never an HTTP error: the browser can only print those in red, and a host
    # that does not answer is the expected outcome this button exists to find.
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == status
    assert body["code"] is None
    assert body["message"]              # says which one it was, in words
    assert body["detail"]               # ...and carries the raw reason


def test_sv_check_waits_no_longer_than_a_poll_interval(fake_endpoint):
    """A hung endpoint must not stall the panel it sits in: the watch poll comes
    round every 10s, so the deadline is below that."""
    install, calls = fake_endpoint
    install(200)
    client.get("/api/sv-check", params={"host": "h.example.com"})
    assert calls[0]["timeout"] == server.SV_CHECK_TIMEOUT_S
    assert 0 < server.SV_CHECK_TIMEOUT_S < 10


def test_sv_check_can_be_asked_for_https(fake_endpoint):
    """TLS is configured per deployment (sv_tls_secret), and probing the wrong
    scheme answers a question nobody asked -- plain http against a TLS-only
    route, or a handshake against a listener that speaks none."""
    install, calls = fake_endpoint
    install(200)
    client.get("/api/sv-check", params={"host": "h.example.com", "scheme": "https"})
    assert calls[0]["url"] == "https://h.example.com/"


@pytest.mark.parametrize("bad", [
    "h.example.com/admin",              # a path -- would fetch anything
    "user:pw@h.example.com",
    "h.example.com evil.example.com",
    "",
])
def test_sv_check_refuses_anything_that_is_not_a_host(fake_endpoint, bad):
    """The host arrives from the browser, so the guard is what keeps this from
    being a general-purpose fetcher pointed by whatever loads the page. A
    rejected request is the user getting it wrong, so unlike an endpoint that
    will not answer it is a 4xx."""
    install, calls = fake_endpoint
    install(200)
    assert client.get("/api/sv-check", params={"host": bad}).status_code == 400
    assert calls == []


@pytest.mark.parametrize("scheme", ["ftp", "file"])
def test_sv_check_refuses_a_scheme_it_does_not_speak(fake_endpoint, scheme):
    install, calls = fake_endpoint
    install(200)
    assert client.get("/api/sv-check", params={
        "host": "h.example.com", "scheme": scheme}).status_code == 400
    assert calls == []


def test_sv_check_needs_no_cluster(fake_cluster, fake_endpoint):
    """It reads nothing from kubectl -- the host was resolved when the panel
    listed the mock. With no CLI on the machine at all it still answers, which
    is what keeps the button from being a second thing that needs a cluster."""
    fake_cluster(tools=())
    install, _ = fake_endpoint
    install(200)
    body = client.get("/api/sv-check", params={"host": "h.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 200
