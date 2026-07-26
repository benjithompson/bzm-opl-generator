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
