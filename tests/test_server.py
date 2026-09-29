import inspect
import io
import json
import logging
import os
import pathlib
import re
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bzm_opl_gen import core, server, ui_build  # noqa: E402
from test_generate import FACTS  # noqa: E402
# The fakes shared with tests/test_core.py and tests/test_cli.py; `calls` is
# what makes "nothing was minted" assertable.
from test_core import FakeClient, RefusingClient  # noqa: E402

client = TestClient(server.app)


def connect(monkeypatch, account):
    """Put this server process in the state of being connected as `account`.

    Stands in at `server._state`, not `core.client_from_key`: a browser session
    connects once and every route acts as what that left in the process."""
    monkeypatch.setitem(server._state, "client", account)
    return account


def test_generate_preview_no_key_needed():
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert names[0] == "bzm_serviceaccount.yaml"      # apply order
    assert "bzm_deployment.yaml" in names and "README.md" in names


def test_generate_preview_carries_the_ca_bundle_lint():
    """Warned beside the files, never refused: the preview still renders."""
    from ca_fixtures import CHAIN_PEM, LEAF_PEM
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1", "ca_bundle": LEAF_PEM}})
    assert r.status_code == 200
    assert r.json()["warnings"][0].startswith("CA bundle FAIL: ")
    clean = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1", "ca_bundle": CHAIN_PEM}})
    assert clean.json()["warnings"] == []


def test_generate_zip_mirror_script_executable():
    r = client.post("/api/generate/zip", json={
        "facts": FACTS,
        "options": {"namespace": "ns1", "private_registry": "reg.local/bzm"}})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    info = z.getinfo("bzm-opl-ns1/bzm-opl-image-mirror.sh")
    assert info.external_attr >> 16 & 0o111          # executable bits
    assert "bzm-opl-ns1/bzm_configmap.yaml" in z.namelist()


def test_generate_preview_helm_format():
    """The preview leads with the values overlay -- the only file in a chart
    bundle that came from the account, and so the one worth reading first."""
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1", "output_format": "helm"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert names[0] == "bzm-opl-values.yaml"
    assert "helm/templates/deployment.yaml" in names
    assert "bzm_deployment.yaml" not in names


def test_generate_zip_helm_keeps_the_chart_directory():
    """Names carry directories in this format, and a zip that flattened them
    would download as a pile of files no helm command can install."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1", "output_format": "helm"}})
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "bzm-opl-ns1/helm/Chart.yaml" in names
    assert "bzm-opl-ns1/helm/templates/deployment.yaml" in names
    assert "bzm-opl-ns1/bzm-opl-values.yaml" in names


def test_generate_helm_serves_a_service_virtualization_location():
    """A helm bundle for a service-virtualization location renders."""
    facts = dict(FACTS, func_ids=["mockServices"])
    r = client.post("/api/generate", json={
        "facts": facts,
        "options": {"namespace": "ns1", "output_format": "helm",
                    "sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
                    "sv_tls_secret": "wildcard"}})
    assert r.status_code == 200
    files = {f["name"]: f["content"] for f in r.json()["files"]}
    assert "ingress: \"nginx\"" in files["bzm-opl-values.yaml"]


def test_manual_facts_need_no_api_key():
    """The whole point: this mode exists for an account nobody here can reach,
    so requiring a key would defeat it. Every other /api route 401s."""
    r = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["performance"]})
    assert r.status_code == 200
    f = r.json()["facts"]
    assert f["harbor_id"] == "H1"
    assert f["ships"][0]["id"] == "S1"
    assert f["images_source"] == "manual entry (no account access)"
    assert r.json()["gui_images_incomplete"] is False


def test_manual_facts_need_no_ids_either():
    """Manual facts take both ids blank and answer the markers, not a 422."""
    r = client.post("/api/facts/manual", json={"func_ids": ["performance"]})
    assert r.status_code == 200
    f = r.json()["facts"]
    assert f["harbor_id"] == markers.marker("harbor_id")
    # The page reads its agent back out of this answer, so it is the marker.
    assert f["ships"][0]["id"] == markers.marker("ship_id")


def test_manual_facts_flag_the_gui_image_gap():
    r = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["functionalGui"]})
    assert r.json()["gui_images_incomplete"] is True


def test_manual_facts_generate_without_a_token_fetch():
    """Manual facts with a typed token mint nothing, even with a key connected."""
    facts = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["performance"]}).json()["facts"]
    r = client.post("/api/generate", json={
        "facts": facts,
        "options": {"namespace": "cust", "auth_token": "TOK", "ship_id": "S1"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert "bzm_deployment.yaml" in names and "bzm_secret.yaml" in names
    assert r.json()["token"]["branch"] == core.TOKEN_GIVEN


# -- what a download does to a running agent's credential ----------------------
# The branch rule is core's (tests/test_core.py); these pin that no route
# mints unless asked, and that each answer says which branch it took.

@pytest.fixture
def connected(monkeypatch):
    """A browser session holding an API key, counting what it was asked for."""
    return connect(monkeypatch, FakeClient())


@pytest.mark.parametrize("route", ["/api/generate", "/api/generate/zip",
                                   "/api/generate/save"])
def test_no_route_mints_unless_it_was_asked_to(connected, route, tmp_path):
    """Preview, zip and save each mint nothing unless rotate_token is set."""
    r = client.post(route, json={"facts": FACTS, "out_dir": str(tmp_path / "b"),
                                 "options": {"namespace": "ns1"}})
    assert r.status_code == 200
    assert connected.calls == []


def test_the_preview_can_say_a_save_would_reuse_the_folder_s_token(
        connected, tmp_path):
    """The preview reads out_dir, so it can report the `reused` branch a save
    would take."""
    out = str(tmp_path / "bundle")
    ship = FACTS["ships"][0]["id"]
    client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": ship,
                    "auth_token": "ALREADY-THERE"}})
    r = client.post("/api/generate", json={
        "facts": FACTS, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": ship}})
    assert r.json()["token"]["branch"] == core.TOKEN_REUSED
    assert connected.calls == []


def test_a_folder_it_will_refuse_is_refused_before_anything_is_issued(
        connected):
    """A relative save folder is refused before any token is issued."""
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": "some/relative/dir", "rotate_token": True,
        "options": {"namespace": "ns1", "ship_id": FACTS["ships"][0]["id"]}})
    assert r.status_code == 400
    assert connected.calls == [], "it minted a credential it then threw away"


def test_a_page_still_asking_for_the_old_fetch_mints_nothing(connected):
    """A stale `fetch_token` field is ignored, not refused, and mints nothing."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "fetch_token": True})
    assert r.status_code == 200 and connected.calls == []


def test_rotating_mints_once_and_names_whose_credential_it_replaced(connected):
    """A rotation mints once and names the ship whose credential it replaced."""
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "rotate_token": True})
    assert connected.calls == [("auth_token", "aaa111", "bbb222")]
    token = r.json()["token"]
    assert (token["branch"], token["ship_id"]) == (core.TOKEN_ROTATED, "bbb222")
    assert "bbb222" in token["message"]
    secret = next(f for f in r.json()["files"] if f["name"] == "bzm_secret.yaml")
    assert "TOKEN-FROM-API" in secret["content"]


def test_a_bundle_with_no_token_says_so_rather_than_looking_finished(connected):
    """A bundle with no token says so."""
    body = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}}).json()
    assert body["token"]["branch"] == core.TOKEN_PLACEHOLDER
    assert "create-agent" in body["token"]["message"]


def test_the_zip_says_in_its_headers_which_branch_it_took(connected):
    """The zip carries the token branch and message in its headers."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    # Literal header names: the frontend reads these.
    assert (server.TOKEN_BRANCH_HEADER, server.TOKEN_MESSAGE_HEADER) == (
        "X-Bzm-Token-Branch", "X-Bzm-Token-Message")
    assert r.headers[server.TOKEN_BRANCH_HEADER] == core.TOKEN_PLACEHOLDER
    message = r.headers[server.TOKEN_MESSAGE_HEADER]
    assert "create-agent" in message
    # One line, because a header is one line -- the recovery hint is three.
    assert "\n" not in message
    assert "bzm-opl-ns1/bzm_secret.yaml" in zipfile.ZipFile(
        io.BytesIO(r.content)).namelist()


def test_the_download_extracts_to_the_folder_it_is_named():
    """The download filename and the directory inside the zip are one name."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    name = re.search(r'filename="([^"]+)"', r.headers["Content-Disposition"])[1]
    assert name.endswith(".zip")
    roots = {n.split("/")[0]
             for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist()}
    assert roots == {name[:-len(".zip")]}


def test_a_namespace_no_header_could_carry_does_not_fail_the_download(connected):
    """A namespace no header can carry does not fail the download."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "blazemeter-平"}})
    assert r.status_code == 200
    assert r.headers[server.TOKEN_BRANCH_HEADER] == core.TOKEN_PLACEHOLDER
    assert "attachment" in r.headers["Content-Disposition"]
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist()


BRANCHES = {core.TOKEN_GIVEN, core.TOKEN_ROTATED, core.TOKEN_REUSED,
            core.TOKEN_PLACEHOLDER}


def test_the_four_branch_names_are_what_the_page_switches_on():
    """The four branch names are the literals the page switches on."""
    assert BRANCHES == {"given", "rotated", "reused", "placeholder"}


def test_the_page_declares_the_same_four_branches_this_does():
    """frontend/src/api.ts declares the same four branches, read from the file."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    union = re.search(r"export type TokenBranch\s*=\s*([^;]+);",
                      (src / "api.ts").read_text())
    assert union, "TokenBranch union not found -- was it renamed or moved?"
    assert set(re.findall(r'"([^"]+)"', union.group(1))) == BRANCHES

    # The map keyed by that union must cover every branch.
    carries = re.search(r"const CARRIES: Record<TokenBranch, string> = \{(.*?)\}",
                        (src / "token.ts").read_text(), re.S)
    assert carries, "CARRIES not found -- was it renamed or moved?"
    assert set(re.findall(r"^\s*(\w+):", carries.group(1), re.M)) == BRANCHES


def test_the_page_spells_the_declined_ingress_the_way_generate_does():
    """optionGroups.ts spells the declined ingress as generate does."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    m = re.search(r'export const SV_NONE = "([^"]+)"',
                  (src / "optionGroups.ts").read_text())
    assert m, "SV_NONE not found -- was it renamed or moved?"
    assert m.group(1) == service_virt.SV_INGRESS_NONE


def test_every_group_s_tag_is_a_functionality_this_server_serves():
    """Every group's functionality tag is one this server serves; an unknown tag
    would have its options cleared by notRunPatch."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    body = re.search(r"export const OPTION_GROUPS: OptionGroup\[\] = \[(.*?)\n\];",
                     (src / "optionGroups.ts").read_text(), re.S)
    assert body, "OPTION_GROUPS not found -- was it renamed or moved?"
    tagged = set(re.findall(r'"([^"]+)"',
                            " ".join(re.findall(
                                r"^\s*functionalities: \[(.*?)\],$",
                                body.group(1), re.M))))
    # Not empty, so a regex that matched nothing cannot pass.
    assert tagged, "no tags found -- did the field move or get renamed?"
    assert tagged <= {f["id"] for f in core.FUNCTIONALITIES}


# -- the connection outlives the page -----------------------------------------

def test_key_status_reports_the_connection_the_process_still_holds(connected):
    """A browser refresh never disconnected anything -- the page just forgot.
    This is what it asks on load."""
    body = client.get("/api/key").json()
    assert body["connected"] is True
    assert body["user"]["email"] == "se@example.com"
    assert body["default_account_id"] == 7


def test_key_status_says_no_when_nothing_is_held(monkeypatch):
    monkeypatch.setitem(server._state, "client", None)
    assert client.get("/api/key").json() == {"connected": False}


def test_a_key_that_stopped_working_reads_as_disconnected(monkeypatch):
    """Accepted once is not the same as working now. A key revoked since then
    has to surface here, not on whichever call happens to be first."""
    class Revoked:
        def user(self):
            raise core.CoreError("401 unauthorized")
    connect(monkeypatch, Revoked())
    assert client.get("/api/key").json() == {"connected": False}
    # ...and it is dropped, rather than left for every later call to fail on.
    assert server._state["client"] is None


def test_connecting_with_a_malformed_key_file_refuses_without_exiting(monkeypatch,
                                                                      tmp_path):
    """A malformed key file is a 400, not a SystemExit out of the route."""
    monkeypatch.setitem(server._state, "client", None)
    bad = tmp_path / "api-key.json"
    bad.write_text("not json")
    r = client.post("/api/key", json={"path": str(bad)})
    assert r.status_code in (400, 401, 502)
    assert "not valid JSON" in r.json()["detail"]      # core's own sentence
    assert server._state["client"] is None
    # The process is still serving, which is the whole point.
    assert client.get("/api/key").json() == {"connected": False}


def test_connecting_with_a_good_key_file_still_reports_the_user(monkeypatch,
                                                                tmp_path):
    """A good key file connects and reports the user."""
    monkeypatch.setitem(server._state, "client", None)
    monkeypatch.setitem(server._state, "key_id", None)
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    monkeypatch.setattr(core, "user", lambda c: {
        "email": "se@example.com", "displayName": "SE",
        "defaultProject": {"accountId": 7}})
    body = client.post("/api/key", json={"path": str(key)}).json()
    assert body["user"]["email"] == "se@example.com"
    assert body["default_account_id"] == 7 and body["key_id"] == "KID"
    assert server._state["client"] is not None


def _config_dir(monkeypatch, tmp_path):
    """core's key directory, somewhere disposable (never the developer's own)."""
    d = tmp_path / "config"
    monkeypatch.setattr(core, "CONFIG_DIR", str(d))
    monkeypatch.setattr(core, "SAVED_KEY_PATH", str(d / "api-key.json"))
    monkeypatch.setattr(core, "user", lambda c: {
        "email": "se@example.com", "displayName": "SE",
        "defaultProject": {"accountId": 7}})
    return d


def test_a_pasted_key_reaches_core_as_a_pair_and_never_the_disk(monkeypatch,
                                                                tmp_path):
    """A pasted key reaches core as a pair and touches no disk."""
    d = _config_dir(monkeypatch, tmp_path)
    seen = []

    def seam(path=None, **kw):
        seen.append((path, kw))
        return FakeClient()

    monkeypatch.setattr(core, "client_from_key", seam)
    monkeypatch.setitem(server._state, "client", None)
    body = client.post("/api/key", json={"id": "KID", "secret": "SHHH"}).json()
    assert seen == [(None, {"key_id": "KID", "secret": "SHHH"})]
    assert not d.exists(), "the pasted secret was written to disk"
    assert body["key_id"] == "KID" and body["saved"] is False


def test_a_pasted_key_saved_on_purpose_is_written_where_detect_finds_it(
        monkeypatch, tmp_path):
    """The other half: `save: true` is a key the user asked to keep, which is a
    file on disk by definition. Mode 600, and at the path core detects from."""
    d = _config_dir(monkeypatch, tmp_path)
    monkeypatch.setitem(server._state, "client", None)
    body = client.post("/api/key", json={"id": "KID", "secret": "SHHH",
                                         "save": True}).json()
    assert body["saved"] is True
    saved = d / "api-key.json"
    assert json.loads(saved.read_text()) == {"id": "KID", "secret": "SHHH"}
    assert os.stat(saved).st_mode & 0o777 == 0o600


def test_disconnect_forgets_the_key_without_deleting_a_saved_one(connected, tmp_path):
    """Only what is in memory. A key the user asked to save stays on disk --
    deleting it is not what a Disconnect button on a web page should mean."""
    saved = tmp_path / "api-key.json"
    saved.write_text('{"id": "k", "secret": "s"}')
    assert client.delete("/api/key").json() == {"connected": False}
    assert server._state["client"] is None
    assert saved.exists()


# -- issuing the credential once, where the agent is made ----------------------

def test_creating_an_agent_issues_its_credential_with_it(connected):
    """Creating an agent issues its credential with it, for the new ship only."""
    body = client.post("/api/ships", json={
        "harbor_id": "aaa111", "name": "agent1"}).json()
    assert body["ship"]["id"] == "s2"
    assert body["auth_token"] == "TOKEN-FROM-API"
    assert body["token_error"] is None
    # For the ship it just made, not for whatever was selected before it.
    assert connected.calls == [("auth_token", "aaa111", "s2")]


def test_an_agent_whose_credential_was_refused_is_still_reported(monkeypatch):
    """A refused credential is reported beside the created agent, with a 200."""
    connect(monkeypatch, RefusingClient())
    r = client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ship"]["id"] == "s2" and body["auth_token"] is None
    assert "could not be issued" in body["token_error"]
    # The way on: a token read off the BlazeMeter UI works as well.
    assert "auth_token" in body["token_error"]


def test_issuing_a_token_is_its_own_route(connected):
    """Issuing a token is its own route."""
    r = client.post("/api/ships/token",
                    json={"harbor_id": "aaa111", "ship_id": "s1"})
    assert r.status_code == 200
    assert r.json()["auth_token"] == "TOKEN-FROM-API"
    assert connected.calls == [("auth_token", "aaa111", "s1")]


def test_issuing_a_token_reports_a_closed_endpoint(monkeypatch):
    connect(monkeypatch, RefusingClient())
    r = client.post("/api/ships/token",
                    json={"harbor_id": "aaa111", "ship_id": "s1"})
    assert r.status_code == 502
    assert "could not be issued" in r.json()["detail"]


# -- ...and remembering it, so a refresh does not throw it away ----------------
# No API reads an AUTH_TOKEN back, so the server keeps what it minted, by ship.

@pytest.fixture(autouse=True)
def _no_remembered_tokens():
    """Every test starts having minted nothing."""
    server._minted_tokens.clear()
    yield
    server._minted_tokens.clear()


class TwoAgentAccount(FakeClient):
    """An account where each agent and its token are distinct, so a store keyed by
    ship differs from one holding the last token."""

    def create_ship(self, harbor_id, name):
        return {"id": f"s-{name}", "name": name}

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        return f"TOKEN-{ship_id}"


def test_a_created_agent_s_credential_outlives_the_page_that_asked_for_it(
        connected):
    """A token minted at creation can be read back after a page refresh."""
    client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    r = client.get("/api/ships/minted-token?ship_id=s2")
    assert r.status_code == 200
    assert r.json()["auth_token"] == "TOKEN-FROM-API"
    # Read from this process, not from the account (a read there would mint).
    assert connected.calls == [("auth_token", "aaa111", "s2")]


def test_a_regenerated_credential_is_what_is_remembered_afterwards(connected):
    """A regenerated credential replaces the remembered one."""
    client.post("/api/ships/token",
                json={"harbor_id": "aaa111", "ship_id": "s1"})
    assert client.get("/api/ships/minted-token?ship_id=s1").json() == {
        "auth_token": "TOKEN-FROM-API"}


def test_a_token_is_only_ever_found_under_the_ship_it_belongs_to(monkeypatch):
    """A token is only ever found under the ship it belongs to."""
    connect(monkeypatch, TwoAgentAccount())
    for name in ("alpha", "bravo"):
        client.post("/api/ships", json={"harbor_id": "aaa111", "name": name})
    for name in ("alpha", "bravo"):
        assert client.get(
            f"/api/ships/minted-token?ship_id=s-{name}").json() == {
                "auth_token": f"TOKEN-s-{name}"}


def test_an_agent_this_app_never_minted_for_reads_as_no_token(connected):
    """An agent this app never minted for reads as null, with a 200."""
    r = client.get("/api/ships/minted-token?ship_id=s1")
    assert r.status_code == 200 and r.json() == {"auth_token": None}
    assert connected.calls == []


def test_a_credential_that_was_refused_is_not_remembered_as_one(monkeypatch):
    """A refused credential is not remembered."""
    connect(monkeypatch, RefusingClient())
    assert client.post(
        "/api/ships", json={"harbor_id": "aaa111", "name": "agent1"}
    ).json()["auth_token"] is None
    assert server._minted_tokens == {}
    assert client.get("/api/ships/minted-token?ship_id=s2").json() == {
        "auth_token": None}


def test_a_token_typed_over_evicts_the_one_that_was_minted(connected):
    """A typed token evicts the minted one, so it is not restored after a reload."""
    client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert client.delete("/api/ships/minted-token?ship_id=s2").json() == {
        "forgotten": True}
    assert client.get("/api/ships/minted-token?ship_id=s2").json() == {
        "auth_token": None}
    # Idempotent: a second keystroke must not be an error.
    assert client.delete("/api/ships/minted-token?ship_id=s2").json() == {
        "forgotten": False}


def test_disconnecting_forgets_every_credential_this_app_minted(monkeypatch):
    """Disconnecting forgets every minted credential."""
    connect(monkeypatch, FakeClient())
    client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert server._minted_tokens
    client.delete("/api/key")
    assert server._minted_tokens == {}
    assert client.get("/api/ships/minted-token?ship_id=s2").json() == {
        "auth_token": None}


def test_forgetting_a_minted_token_is_not_a_write_to_the_account(monkeypatch):
    """Forgetting a minted token does not drop the account cache."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    assert server._cache
    client.delete("/api/ships/minted-token?ship_id=s1")
    assert server._cache


def test_the_store_is_addressed_by_ship_and_never_by_token():
    """Both token routes take the ship id and never the token."""
    for name in ("ship_minted_token", "ship_forget_minted_token"):
        params = inspect.signature(getattr(server, name)).parameters
        assert set(params) == {"ship_id"}, (
            f"{name} takes {sorted(params)}; the token is not an argument to "
            f"anything that remembers it")


def test_a_remembered_credential_never_reaches_a_log_line(connected, caplog):
    """A minted token never reaches a log line, including FastAPI's 422 echo."""
    with caplog.at_level(logging.DEBUG):
        client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
        client.get("/api/ships/minted-token?ship_id=s2")
        client.delete("/api/ships/minted-token?ship_id=s2")
        # A malformed request, where FastAPI echoes the input back.
        missing = client.get("/api/ships/minted-token")
    assert "TOKEN-FROM-API" not in caplog.text
    assert missing.status_code == 422
    assert "TOKEN-FROM-API" not in missing.text and "ship_id" in missing.text


def test_no_route_here_turns_a_functionality_on_for_a_location(monkeypatch):
    """There is no route that writes a location's funcIds."""
    assert "/api/locations/func-id" not in {r.path for r in app_routes()}
    fake = FakeClient(harbor={"id": "aaa111", "funcIds": ["performance"]})
    connect(monkeypatch, fake)
    r = client.post("/api/locations/func-id",
                    json={"harbor_id": "aaa111", "func_id": "mockServices"})
    # 405, not 404: the SPA catch-all claims every unmatched GET. Either way
    # nothing reached the account.
    assert r.status_code >= 400
    assert not [c for c in fake.calls if c[0] == "update_private_location"]


def test_func_ids_mark_which_ones_change_the_images(monkeypatch):
    """func-ids marks which funcIds change the bundle's images."""
    connect(monkeypatch, FakeClient())
    by_id = {r["id"]: r for r in
             client.get("/api/func-ids?account_id=123456").json()["choices"]}
    for f in ("performance", "mockServices", "proxyRecorder", "functionalGui"):
        assert by_id[f]["changes_images"] is True
    # A funcId whose images no bundle selects on is still offered.
    assert by_id["tdm"]["changes_images"] is False


def test_option_defaults_are_served():
    """The UI seeds its options from this response, so anything missing from
    DEFAULT_OPTIONS is a control that starts blank."""
    body = client.get("/api/option-defaults").json()
    assert body["platform"] == "openshift"
    assert body["output_format"] == "manifests"


def test_option_defaults_carry_no_metadata():
    """Every key in this response becomes an option the UI submits, so a
    description or a type added here would arrive at generate() as one."""
    assert set(client.get("/api/option-defaults").json()) == set(bundle_options.DEFAULT_OPTIONS)


def test_option_docs_describe_every_option():
    """Every option has a description in the served docs."""
    body = client.get("/api/option-docs").json()
    assert set(body) == set(bundle_options.DEFAULT_OPTIONS)
    assert all(e["summary"] for e in body.values())
    assert body["sv_ingress"]["choices"] == (
        list(service_virt.SV_INGRESS_TYPES) + [service_virt.SV_INGRESS_NONE])
    assert body["private_registry"]["nullable"] is True
    # `secret` marks what not to echo: the TLS key is secret, the certificate
    # beside it is not.
    assert [k for k, e in body.items() if e["secret"]] \
        == ["auth_token", "sv_tls_key"]
    assert body["sv_tls_cert"]["secret"] is False


def test_generate_invalid_options_400():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)    # ambiguous ship
    r = client.post("/api/generate", json={
        "facts": facts, "options": {}})
    assert r.status_code == 400


def test_sv_constants_are_served_from_the_generator():
    """The SV vocabulary is served from the generator."""
    body = client.get("/api/sv-constants").json()
    assert body["func_ids"] == list(service_virt.SV_FUNC_IDS)
    assert body["ingress_types"] == list(service_virt.SV_INGRESS_TYPES)
    assert "openshift" in body["ingress_types"]     # the newest one reaches the UI
    # The `none` decline is not a backend, so it is not here; it is in the
    # option registry's `choices`.
    assert service_virt.SV_INGRESS_NONE not in body["ingress_types"]
    assert service_virt.SV_INGRESS_NONE in client.get(
        "/api/option-docs").json()["sv_ingress"]["choices"]
    # Not in option-defaults, which the UI spreads into options.
    assert "ingress_types" not in client.get("/api/option-defaults").json()


def test_ignored_options_are_served_from_the_generator():
    """The ignored options are served per format, and every format has an entry
    (so `{}` means read and nothing dropped)."""
    body = client.get("/api/ignored-options").json()
    assert body == bundle_options.IGNORED_BY_FORMAT
    assert set(body) == set(bundle_options.OUTPUT_FORMATS)
    # The four the page hides whole sections for.
    for key in ("namespace", "service_account_name", "node_selector",
                "engine_cpu_limit"):
        assert body["docker"][key]
    # Symmetric: each platform's SV options are the other's ignored options.
    assert body["helm"] == body["manifests"]
    for key in ("sv_hostname", "sv_tls_cert", "sv_tls_key"):
        assert body["helm"][key] and key not in body["docker"]
    for key in ("sv_ingress", "sv_subdomain", "sv_tls_secret",
                "sv_istio_gateway"):
        assert body["docker"][key] and key not in body["helm"]
    # Every key is a real option.
    options = set(client.get("/api/option-defaults").json())
    for fmt, table in body.items():
        assert not set(table) - options, fmt


def test_the_page_knows_the_same_three_formats_the_generator_does():
    """frontend/src/formats.ts knows the same formats as the generator."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    body = re.search(r"export const OUTPUT_FORMATS: OutputFormat\[\] = \[(.*?)\n\];",
                     (src / "formats.ts").read_text(), re.S)
    assert body, "OUTPUT_FORMATS not found -- was it renamed or moved?"
    assert tuple(re.findall(r'id: "([^"]+)"', body.group(1))) \
        == bundle_options.OUTPUT_FORMATS


def test_no_format_refuses_a_virtual_service():
    """No format refuses a virtual service configuration, derived by calling
    generate() per format; a new refusal fails here."""
    from bzm_opl_gen import generate as gen_mod
    facts = {"harbor_id": "aaa111", "func_ids": ["mockServices"],
             "crane_image": "example.invalid/blazemeter/crane:3.7.55",
             "images": [], "ships": []}
    sv_opts = {"ship_id": "bbb222", "auth_token": "de" * 32,
               "sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
               "sv_tls_secret": "wildcard-credential"}
    for fmt in bundle_options.OUTPUT_FORMATS:
        gen_mod.generate(facts, {**sv_opts, "output_format": fmt})
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    assert "BLOCKED_FORMATS: Record" not in (src / "sv.ts").read_text(), \
        "the page has a blocked-format table again -- hold it equal to the " \
        "formats generate() refuses"


def test_the_pages_copy_of_the_ignored_table_is_the_generators():
    """fixtures.ts's copy of IGNORED_BY_FORMAT equals the generator's, per format
    and empties included."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const IGNORED_BY_FORMAT: "
                     r"Record<string, Record<string, string>> = \{"
                     r"(.*?)\n\};", text, re.S)
    assert body, "IGNORED_BY_FORMAT not found -- was it renamed or moved?"
    # A format at 2 spaces, its option keys at 4; `+` lines continue a reason.
    found, table = {}, None
    for line in body.group(1).splitlines():
        empty = re.match(r"  (\w+): \{\},?$", line)
        opens = re.match(r"  (\w+): \{$", line)
        key = re.match(r"    (\w+):", line)
        if empty:
            found[empty.group(1)] = set()
        elif opens:
            table = found.setdefault(opens.group(1), set())
        elif key and table is not None:
            table.add(key.group(1))
    assert found == {fmt: set(keys)
                     for fmt, keys in bundle_options.IGNORED_BY_FORMAT.items()}


def test_the_marker_rule_is_one_rule_in_both_languages():
    """The marker rule agrees between markers.marker and the worked examples in
    fixtures.ts that the page's own tests use."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const MARKER_EXAMPLES: Record<string, string> "
                     r"= \{(.*?)\n\};", text, re.S)
    assert body, "MARKER_EXAMPLES not found -- was it renamed or moved?"
    found = dict(re.findall(r'^  "?([\w.]+)"?: "([^"]+)",$',
                            body.group(1), re.M))
    # The examples cover every key shape: one word, several, nested, extra_env.
    assert set(found) >= {"namespace", "auth_token", "service_account_name",
                          "proxy.https", "extra_env.FOO"}
    for key, want in found.items():
        assert markers.marker(key) == want, key
    # ...and the recogniser accepts every one of them.
    for want in found.values():
        assert markers.is_placeholder(want)
        assert markers.marker_in(f"user:pass@{want}:3128") == want


def test_the_served_marker_is_the_generators_own():
    """The served markers are markers.marker's."""
    body = client.get("/api/placeholders").json()
    assert set(body) == set(required_fields.PLACEHOLDER_SOURCE)
    for key, entry in body.items():
        assert entry["marker"] == markers.marker(key), key
        # Never empty: an empty source would claim a source it does not have.
        assert entry["source"].strip()


def test_the_placeholder_sentences_render_the_same_two_ways():
    """The placeholder sentences contain no Markdown syntax: they render as a
    README cell and as panel text."""
    for key, source in required_fields.PLACEHOLDER_SOURCE.items():
        assert "`" not in source, f"{key}: backticks render as backticks"
        assert "--" not in source.replace("--auth-token", ""), \
            f"{key}: use an em dash"
        assert "->" not in source, f"{key}: use an arrow"
        assert "*" not in source, f"{key}: emphasis renders as asterisks"


def test_the_served_placeholders_carry_no_severity():
    """The served placeholders carry a marker and a source only, no severity."""
    body = client.get("/api/placeholders").json()
    for key, entry in body.items():
        assert set(entry) == {"marker", "source"}, key
    for key in required_fields.PLACEHOLDER_REFUSED_BY_API:
        assert key in body, key


def test_reserved_env_is_served_with_the_option_that_owns_each_name():
    """Reserved env names are served with the option that owns each."""
    body = client.get("/api/reserved-env").json()
    assert set(body) == set(bundle_env.RESERVED_ENV)
    assert body["KUBERNETES_SERVICE_USE_TYPE"] == "service_type"
    # Null is a real answer: the identity variables belong to no option.
    assert body["SHIP_ID"] is None
    # Every named owner is a real option.
    defaults = client.get("/api/option-defaults").json()
    for owner in filter(None, body.values()):
        for name in owner.split(" | "):
            assert name in defaults, f"{owner} names no option"


def test_agent_env_is_served_as_what_is_left_after_the_options():
    """agent-env offers BlazeMeter's reference minus every reserved name, so it
    never offers a row the generator refuses."""
    from bzm_opl_gen import agent_env as env_mod
    body = client.get("/api/agent-env").json()
    names = {v["name"] for v in body}
    assert names == {v["name"] for v in env_mod.AGENT_ENV} - bundle_env.RESERVED_ENV
    assert not names & set(client.get("/api/reserved-env").json())
    # A row the page can render: `type` picks the control.
    for v in body:
        assert v["type"] in env_mod.TYPES
        assert set(v["platforms"]) <= {"kubernetes", "docker"}
        assert v["summary"]
        assert isinstance(v["functionalities"], list)


def test_agent_env_is_scoped_to_what_the_location_runs():
    """agent-env is filtered by the location's funcIds; absent and empty are
    different answers."""
    whole = {v["name"] for v in client.get("/api/agent-env").json()}
    perf = {v["name"] for v in
            client.get("/api/agent-env?func_ids=performance").json()}
    assert perf < whole
    assert "VERIFY_SSL" in perf
    assert "DODUO_PORT" in whole and "DODUO_PORT" not in perf

    # Several, comma-separated, the way a location carries several funcIds.
    two = {v["name"] for v in client.get(
        "/api/agent-env?func_ids=performance,functionalGui").json()}
    assert "DODUO_PORT" in two

    # Absent, and answered-empty, are not the same read.
    empty = {v["name"] for v in client.get("/api/agent-env?func_ids=").json()}
    assert "VERIFY_SSL" in empty and "DODUO_PORT" not in empty


def test_the_pages_copy_of_the_env_name_rule_is_the_generators():
    """frontend/src/env.ts's env-name rule is generate's."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    pattern = re.search(r"^const NAME_RE = /(.+)/;$",
                        (src / "env.ts").read_text(), re.M)
    assert pattern, "NAME_RE not found -- was it renamed or moved?"
    assert pattern.group(1) == bundle_env.ENV_NAME_RE.pattern


def test_the_pages_copy_of_the_reserved_env_names_is_the_generators():
    """As with IGNORED_BY_FORMAT above: the page's tests run without a server, so
    the fixture is a second copy, and this is what keeps it from drifting."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const RESERVED_ENV: Record<string, string \| null> = \{"
                     r"(.*?)\n\};", text, re.S)
    assert body, "RESERVED_ENV not found -- was it renamed or moved?"
    assert set(re.findall(r"^  (\w+):", body.group(1), re.M)) \
        == set(bundle_env.RESERVED_ENV)


def test_sv_constants_carry_what_each_backend_publishes():
    """sv-constants carries what each backend publishes (SV_INGRESS_BACKENDS)."""
    backends = client.get("/api/sv-constants").json()["backends"]
    assert set(backends) == set(service_virt.SV_INGRESS_TYPES)
    for name, b in service_virt.SV_INGRESS_BACKENDS.items():
        assert backends[name] == {"group": b.group, "resources": list(b.resources),
                                  "creates": b.creates, "nodeport_ok": b.nodeport_ok}
    # routes/custom-host: OpenShift gates spec.host behind it, and crane sets it.
    assert "routes/custom-host" in backends["openshift"]["resources"]
    # nodeport_ok is served because the UI decides with it.
    assert {n: b["nodeport_ok"] for n, b in backends.items()} == {
        "nginx": True, "openshift": True, "contour": False, "istio": False}


def test_func_id_choices_come_from_the_account_when_there_is_one(monkeypatch):
    """With an account, func-ids answers the account's own vocabulary."""
    connect(monkeypatch, FakeClient())
    body = client.get("/api/func-ids?account_id=123456").json()["choices"]
    ids = [c["id"] for c in body]

    assert {"mockServices", "proxyRecorder", "tdm", "delphix"} <= set(ids)
    # Retired funcIds are simply not in the account's list.
    assert "functionalApi" not in ids and "sv-bridge" not in ids
    assert all(c["label"] for c in body)


def _ts_type_fields(text, name):
    """The field names of an `export type X = {...}` in TypeScript source, with
    block comments stripped first."""
    body = re.search(rf"export type {name} = \{{(.*?)\n\}};", text, re.S)
    assert body, f"{name} not found in api.ts -- was it renamed or moved?"
    return set(re.findall(r"(?:^|;)\s*(\w+)\??:",
                          re.sub(r"/\*.*?\*/", "", body.group(1), flags=re.S)))


def test_the_pages_type_for_the_vocabulary_is_the_shape_this_route_serves(monkeypatch):
    """api.ts's type for /api/func-ids has the fields the route serves."""
    connect(monkeypatch, FakeClient())
    text = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "src" / "api.ts").read_text()
    body = client.get("/api/func-ids?account_id=123456").json()

    assert _ts_type_fields(text, "FuncIdVocabulary") == set(body)
    assert _ts_type_fields(text, "FuncIdChoice") == set(body["choices"][0])
    # ...and `source` takes exactly the two values the page branches on.
    assert re.search(r'source: "account" \| "baseline";', text), \
        "FuncIdVocabulary.source no longer names both sources"
    assert {core.func_ids()["source"], body["source"]} == {"baseline", "account"}


def test_the_vocabulary_is_reachable_with_no_account_at_all():
    """func-ids needs no account, and answers the covered funcIds."""
    body = client.get("/api/func-ids").json()["choices"]
    assert [(c["id"], c["label"], c["covered"]) for c in body] == [
        ("performance", "Performance", True),
        ("functionalGui", "GUI Functional", True),
        ("mockServices", "Service Virtualization", True)]


def test_an_unnamed_func_id_is_still_offered_under_its_raw_id(monkeypatch):
    """A funcId served without a display name is offered under its raw id."""
    class Unnamed(FakeClient):
        def functionalities(self, account_id):
            return {"functionalities": [{"funcId": "brandNew", "size": 1}]}

    connect(monkeypatch, Unnamed())
    body = client.get("/api/func-ids?account_id=291447").json()["choices"]
    assert [(r["id"], r["label"], r["covered"]) for r in body] == [
        ("brandNew", "brandNew", False)]


def test_reading_the_vocabulary_is_not_a_write(monkeypatch):
    """Reading func-ids is cached and does not drop the cache."""
    fake = connect(monkeypatch, FakeClient())
    server._cache.clear()
    client.get("/api/func-ids?account_id=123456")
    client.get("/api/func-ids?account_id=123456")
    assert [c for c in fake.calls if c[0] == "functionalities"] == [
        ("functionalities", 123456)]


def test_functionalities_are_served_with_a_label_and_a_suggested_namespace():
    """Functionalities are served with a label and a suggested namespace."""
    body = client.get("/api/functionalities").json()
    assert [f["id"] for f in body] == [f["id"] for f in core.FUNCTIONALITIES]
    assert body[0]["id"] == "performance"       # the common case is the default
    for f in body:
        assert f["label"] and f["namespace"]
    # The id is the funcId; SV's is service_virt.SV_FUNC_IDS'.
    assert [f["id"] for f in body if f["id"] in service_virt.SV_FUNC_IDS] \
        == list(service_virt.SV_FUNC_IDS)
    # Distinct namespaces, so redeploying one agent leaves the other's pods.
    assert len({f["namespace"] for f in body}) == len(body)


def test_a_functionality_added_to_the_vocabulary_is_offered(monkeypatch):
    """Adding an entry to FUNCTIONALITIES is the whole backend half of offering a
    functionality."""
    monkeypatch.setattr(core, "FUNCTIONALITIES", core.FUNCTIONALITIES + [
        {"id": "secretsPrivateVault", "label": "Secrets Private Vault",
         "hint": "secrets from a vault", "namespace": "blazemeter-vault"}])
    body = client.get("/api/functionalities").json()
    assert body[-1] == {"id": "secretsPrivateVault",
                        "label": "Secrets Private Vault",
                        "hint": "secrets from a vault",
                        "namespace": "blazemeter-vault",
                        # False: no category says this agent carries an engine.
                        "runs_engine": False}
    # ...and it is covered by the same act.
    assert next(r for r in client.get("/api/func-ids").json()["choices"]
                if r["id"] == "secretsPrivateVault")["covered"] is True


def test_create_location_forwards_every_selected_func_id(monkeypatch):
    """The funcIds the form submits must reach the API verbatim -- for several
    of them the UI is the only way in short of the BlazeMeter web app."""
    seen = {}

    class FakeClient:
        def create_private_location(self, name, account_id, workspace_ids, **kw):
            seen.update(kw, name=name, workspaces=workspace_ids)
            return {"id": "h9", "name": name, "funcIds": kw["func_ids"]}

    connect(monkeypatch, FakeClient())
    r = client.post("/api/locations", json={
        "name": "sv-loc", "account_id": 1, "workspace_id": 2,
        "func_ids": ["mockServices", "proxyRecorder"]})
    assert r.status_code == 200
    assert seen["func_ids"] == ["mockServices", "proxyRecorder"]
    assert r.json()["funcIds"] == ["mockServices", "proxyRecorder"]


def test_a_gui_functional_location_is_refused_at_one_slot(monkeypatch):
    """A GUI Functional location at one slot is a 400, and nothing is written."""
    posted = []

    class Watching(FakeClient):
        def create_private_location(self, *a, **kw):
            posted.append(kw)
            return {"id": "h9"}

    connect(monkeypatch, Watching())
    r = client.post("/api/locations", json={
        "name": "gui", "account_id": 1, "workspace_id": 2,
        "func_ids": ["functionalGui"], "slots": 1})
    assert r.status_code == 400
    assert "Parallel engine runs must be greater than 1" in r.json()["detail"]
    assert posted == []


def test_the_slot_minimums_are_served_so_the_form_can_say_them_first():
    """The slot minimums are served so the form can state them first."""
    body = client.get("/api/slot-minimums").json()
    assert body == core.SLOT_MINIMUMS
    assert body["functionalGui"]["minimum"] == 2


def test_the_pages_copy_of_the_slot_minimums_is_cores():
    """fixtures.ts's copy of the slot minimums equals core's."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const SLOT_MINIMUMS: Record<string, SlotMinimum>"
                     r" = \{(.*?)\n\};", text, re.S)
    assert body, "SLOT_MINIMUMS not found -- was it renamed or moved?"
    served = client.get("/api/slot-minimums").json()
    assert re.findall(r"^  (\w+): \{", body.group(1), re.M) == list(served)
    assert re.findall(r"minimum: (\d+)", body.group(1)) \
        == [str(r["minimum"]) for r in served.values()]
    # BlazeMeter's own sentence, verbatim.
    assert re.findall(r'label: "([^"]+)"', body.group(1)) \
        == [r["label"] for r in served.values()]
    # Joined across `+` continuations before comparing.
    flat = re.sub(r'"\s*\+\s*"', "", " ".join(body.group(1).split()))
    for rule in served.values():
        assert rule["message"] in flat


def test_a_location_a_test_cannot_start_on_says_so(monkeypatch):
    """Creating an unrunnable location answers a warning beside the location."""
    made = {}

    class FakeClient:
        def create_private_location(self, name, account_id, workspace_ids, **kw):
            stored = {"id": "h9", "name": name, "funcIds": kw["func_ids"],
                      "slots": kw["slots"],
                      "threadsPerEngine": kw.get("threads_per_engine")}
            # Applied last: `made` is what this account declined to store.
            stored.update(made)
            return stored

    connect(monkeypatch, FakeClient())

    def create(**kw):
        return client.post("/api/locations", json={
            "name": "loc", "account_id": 1, "workspace_id": 2, **kw}).json()

    # threadsPerEngine is the one POST /private-locations accepts and drops.
    made["threadsPerEngine"] = None
    body = create(slots=2)
    assert body["id"] == "h9"
    assert "403" in body["warning"]
    made.clear()
    assert create(slots=2, threads_per_engine=500)["warning"] is None


def test_api_requires_key():
    assert client.get("/api/accounts").status_code == 401


def test_key_detection_sees_a_key_named_after_startup(monkeypatch, tmp_path):
    """Key detection reads BZM_API_KEY_FILE per request."""
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    monkeypatch.setenv("BZM_API_KEY_FILE", str(key))
    body = client.get("/api/key/detect").json()
    assert {"path": str(key), "key_id": "KID"} in body["candidates"]
    # The id identifies the key; the secret is what must never come back.
    assert "s" not in [c.get("secret") for c in body["candidates"]]


# Routes whose /api/docs description is core's docstring.
DOCUMENTED_ROUTES = [
    ("get", "/api/status"), ("post", "/api/facts/manual"),
    ("post", "/api/plan"),
    ("get", "/api/sv-mocks"),
    ("get", "/api/sv-check"), ("get", "/api/option-defaults"),
    ("get", "/api/option-docs"), ("get", "/api/func-ids"),
    ("get", "/api/functionalities"), ("get", "/api/sv-constants"),
    ("get", "/api/ignored-options"), ("get", "/api/reserved-env"),
]


def test_the_routes_that_explained_themselves_still_do():
    """Those routes still carry a description."""
    spec = server.app.openapi()
    bare = [f"{m} {path}" for m, path in DOCUMENTED_ROUTES
            if not (spec["paths"][path][m].get("description") or "").strip()]
    assert not bare, f"no description in /api/docs for: {bare}"


def test_a_malformed_request_is_refused_before_the_missing_key_is():
    """Neither scope given: that is wrong with or without a key, and 401 would
    send the caller off to configure one only to be refused again."""
    r = client.get("/api/locations")
    assert r.status_code == 400 and "account_id" in r.json()["detail"]


# -- reading the cluster from the server ---------------------------------------
# Optional: each way the read can fail answers 200 with the reason.


from bzm_opl_gen import kube  # noqa: E402
# The faked kubectl shared with the other cluster-reading tests.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402

SV_PODS = json.dumps({"items": [_sv_pod("vs1svc2", 8080, "aaa111", "bbb222"),
                                _sv_pod(None, 5000, extra={"role": "role-crane"})]})


@pytest.fixture
def fake_cluster(monkeypatch):
    """Install a faked kubectl/oc for one test, clearing cli_tool()'s memo after."""
    def install(**kw):
        _fake_kubectl(monkeypatch, **kw)
    install()
    yield install
    kube.cli_tool.cache_clear()


def test_no_cluster_access_leaves_the_rest_of_the_api_working(fake_cluster):
    """With no kubectl on the machine, every other route answers as before."""
    fake_cluster(tools=())
    assert client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}}).status_code == 200
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
    """A non-loopback bind prints a warning at startup."""
    assert _served(monkeypatch, host="0.0.0.0")["host"] == "0.0.0.0"
    warning = capsys.readouterr().out
    assert "reachable" in warning and "AUTH_TOKEN" in warning


def test_a_bad_api_key_flag_does_not_stop_the_server_starting(monkeypatch,
                                                              tmp_path, capsys):
    """An unreadable --api-key is reported and the server still starts."""
    monkeypatch.setitem(server._state, "client", None)
    bad = tmp_path / "api-key.json"
    bad.write_text("not json")
    assert _served(monkeypatch, api_key_path=str(bad))["host"] == "127.0.0.1"
    assert "not valid JSON" in capsys.readouterr().out
    assert server._state["client"] is None


def test_a_good_api_key_flag_connects_at_startup(monkeypatch, tmp_path):
    monkeypatch.setitem(server._state, "client", None)
    monkeypatch.setitem(server._state, "key_id", None)
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    _served(monkeypatch, api_key_path=str(key))
    assert server._state["client"] is not None
    assert server._state["key_id"] == "KID"


def test_ui_dev_mode_binds_the_same_host(monkeypatch):
    """--dev (a second uvicorn.run, via an import string) binds the same host."""
    assert _served(monkeypatch, host="0.0.0.0", dev=True)["host"] == "0.0.0.0"


# -- the deployed virtual services, alongside the heartbeat --------------------

def test_sv_mocks_lists_what_is_deployed_and_where_it_answers(fake_cluster):
    fake_cluster(stdout=SV_PODS)
    body = client.get("/api/sv-mocks",
                      params={"namespace": "ns1",
                              "sv_subdomain": "apps.example.com"}).json()
    assert body["status"] == "ok"
    # The host is the one BlazeMeter advertises, built by the generator.
    assert body["mocks"] == [{"name": "vs1svc2", "port": 8080,
                              "host": "vs1svc2-8080-ns1.apps.example.com"}]


def test_sv_mocks_separates_deployed_nothing_from_cannot_look(fake_cluster):
    """An empty namespace and an unreadable cluster are different answers."""
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
    """sv-mocks never answers an error status: it rides the status poll."""
    fake_cluster(rc=1, stderr="error: current-context is not set")
    r = client.get("/api/sv-mocks", params={"namespace": "ns1"})
    assert r.status_code == 200 and r.json()["status"] == "no_context"


# -- does the endpoint answer? -------------------------------------------------
# The request is always faked: a real one would depend on the day's DNS.

import socket  # noqa: E402
import ssl  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from bzm_opl_gen import (bundle_env, bundle_options, markers, required_fields,
                         service_virt)  # noqa: E402


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
    `install` takes a status or an exception; `calls` records the probe."""
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
    """sv-check probes exactly the host sv-mocks returned."""
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
    """A 503 is reported as the diagnosis, with the command that fixes it."""
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
    # Four failures, four remedies: no DNS, nothing listening, an untrusted
    # certificate, no reply.
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
    # Never an HTTP error: an endpoint that does not answer is a finding.
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
    assert calls[0]["timeout"] == core.SV_CHECK_TIMEOUT_S
    assert 0 < core.SV_CHECK_TIMEOUT_S < 10


def test_sv_check_can_be_asked_for_https(fake_endpoint):
    """sv-check can probe over https."""
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
    """Anything that is not a host is a 400."""
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
    """sv-check needs no cluster."""
    fake_cluster(tools=())
    install, _ = fake_endpoint
    install(200)
    body = client.get("/api/sv-check", params={"host": "h.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 200


def test_group_tags_name_functionalities_the_server_actually_serves():
    """Every functionality id the frontend's groups and sv.ts name is one this
    server serves."""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "frontend", "src", "optionGroups.ts")
    with open(src) as fh:
        text = fh.read()
    tagged = {i for line in text.splitlines() if "functionalities:" in line
              for i in re.findall(r'"([^"]+)"', line)}
    served = {f["id"] for f in client.get("/api/functionalities").json()}
    assert tagged, "no group tags found -- has the declaration shape changed?"
    assert tagged <= served, (
        f"option groups tag functionalities the server does not serve: "
        f"{sorted(tagged - served)}. Either the id was renamed in "
        f"core.FUNCTIONALITIES, or the tag is a typo -- the group's options would "
        f"never appear.")
    # sv.ts keys an answer by functionality id too.
    sv_src = os.path.join(os.path.dirname(src), "sv.ts")
    with open(sv_src) as fh:
        found = re.search(r'const SV_FUNCTIONALITY = "([^"]+)"', fh.read())
    assert found, "SV_FUNCTIONALITY not found -- was it renamed or moved?"
    assert found.group(1) in served


# -- saving a bundle to disk ---------------------------------------------------

def test_generate_save_writes_the_bundle_where_asked(tmp_path):
    out = str(tmp_path / "bundle")
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": out})
    assert r.status_code == 200
    body = r.json()
    assert body["out_dir"] == out
    names = [f["name"] for f in body["files"]]
    # profile.json is what livetest and an MCP session read.
    assert "bzm_deployment.yaml" in names and "profile.json" in names
    assert os.path.isfile(os.path.join(out, "bzm_deployment.yaml"))
    assert os.path.isfile(os.path.join(out, "profile.json"))


def test_generate_save_expands_home(tmp_path, monkeypatch):
    """`~` is how a person types their home directory into a browser field."""
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": "~/bundle"})
    assert r.status_code == 200
    assert r.json()["out_dir"] == str(tmp_path / "bundle")
    assert os.path.isfile(tmp_path / "bundle" / "bzm_deployment.yaml")


def test_generate_save_refuses_a_relative_dir():
    """core's refusal (a relative path resolves against a cwd nobody chose)
    must arrive as this transport's 400, not a 500."""
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": "some/relative/dir"})
    assert r.status_code == 400
    assert "absolute" in r.json()["detail"]


def test_saving_twice_into_the_same_folder_reuses_the_token(connected, tmp_path):
    """Saving twice into the same folder reuses the token and writes identical bytes."""
    out = str(tmp_path / "bundle")
    first = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": out,
        "options": {"namespace": "ns1", "auth_token": "TOKENVALUE"}})
    assert first.json()["token"]["branch"] == core.TOKEN_GIVEN
    secret = os.path.join(out, "bzm_secret.yaml")
    was = open(secret).read()
    again = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": out, "options": {"namespace": "ns1"}})
    token = again.json()["token"]
    assert (token["branch"], token["ship_id"]) == (core.TOKEN_REUSED, "bbb222")
    assert out in token["message"]
    assert open(secret).read() == was and connected.calls == []


def test_saving_a_bundle_for_another_agent_does_not_inherit_its_token(
        connected, tmp_path):
    """Saving into another agent's folder is refused and leaves it intact."""
    out = str(tmp_path / "bundle")
    two = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                             dict(FACTS["ships"][0], id="b2")])
    client.post("/api/generate/save", json={
        "facts": two, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": "b1", "auth_token": "B1TOKEN"}})
    again = client.post("/api/generate/save", json={
        "facts": two, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": "b2"}})
    assert again.status_code == 400
    assert "b1" in again.json()["detail"] and "b2" in again.json()["detail"]
    assert "B1TOKEN" in open(os.path.join(out, "bzm_secret.yaml")).read(), \
        "the refusal must not have destroyed the token it was protecting"


# -- planning, with nothing connected -----------------------------------------

def test_plan_answers_a_browser_that_has_connected_to_nothing(monkeypatch):
    """The case this route exists for: the UI open, no key, no account, no
    cluster, and somebody who needs a number to raise a ticket with."""
    monkeypatch.setattr(core, "client_from_key", lambda *a, **k: pytest.fail(
        "the planner asked for a BlazeMeter client"))
    r = client.post("/api/plan", json={"users": 5000})
    assert r.status_code == 200
    body = r.json()
    assert body["engines"] == 10 and body["nodes"] == 10
    assert body["location"]["slots"] == 10


def test_plan_returns_the_document_with_the_numbers():
    """One call, one plan. Two would let a panel show numbers from one request
    and a document from another."""
    body = client.post("/api/plan", json={"users": 5000}).json()
    assert body["document"].startswith("# Infrastructure request")
    assert "5,000 virtual users" in body["document"]
    assert body["document_file"] == "capacity-request.md"


def test_plan_refuses_a_bad_number_in_the_planner_s_own_words():
    """400 naming the field, not a 422 naming a model attribute -- the person
    reading it typed a load target, not a request body."""
    r = client.post("/api/plan", json={"users": 0})
    assert r.status_code == 400
    assert "users must be at least 1" in r.json()["detail"]


def test_plan_takes_the_empty_strings_a_form_posts():
    """The empty strings an untouched form posts mean "not given"."""
    r = client.post("/api/plan", json={
        "users": "5000", "vus_per_engine": "", "engine_cpu": "",
        "engine_mem": "  ", "engines_per_node": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["vus_per_engine_assumed"] is True
    assert body["engines"] == 10 and body["engines_per_node"] == 1
    assert body["engine"]["memory"] == "8Gi"       # the documented default


def test_plan_still_refuses_a_target_that_was_never_typed():
    """`users` is the one field with no default, so blank is a refusal rather
    than an assumption -- there is no plan without a load target."""
    assert client.post("/api/plan", json={"users": ""}).status_code == 400


def test_plan_sizes_the_functionalities_the_card_asked_about():
    """The plan returns one row per sizing and names the one that drove the pool."""
    body = client.post("/api/plan", json={
        "users": "5000",
        "sizings": [{"functionality": "functionalGui", "target": "20"},
                    {"functionality": "mockServices", "target": "2000"}],
    }).json()
    assert body["driven_by"] == "performance"
    assert [(s["functionality"], s["pods"]) for s in body["sizings"]] == [
        ("performance", 10), ("functionalGui", 5), ("mockServices", None)]


def test_plan_takes_the_blanks_a_sizing_row_arrives_with():
    """A blank figure inside a sizing row is "not given" too."""
    body = client.post("/api/plan", json={
        "sizings": [{"functionality": "functionalGui", "target": "20",
                     "figure": ""}]}).json()
    assert body["sizings"][0]["per_pod_source"] == "assumed"
    assert body["engines"] == 5


def test_plan_refuses_a_sizing_it_cannot_work_out_and_says_why():
    """Service virtualization alone. 400 carrying the sentence, because the
    panel shows the refusal where it would otherwise show a node count."""
    r = client.post("/api/plan", json={
        "sizings": [{"functionality": "mockServices", "target": "2000"}]})
    assert r.status_code == 400
    assert "has not been measured" in r.json()["detail"]


def test_sizing_models_are_served_with_the_account_s_own_label():
    """Sizing models are served with BlazeMeter's label."""
    from bzm_opl_gen import plan as plan_mod
    body = client.get("/api/sizing-models").json()
    assert [m["functionality"] for m in body] == list(plan_mod.SIZING_MODELS)
    by_id = {m["functionality"]: m for m in body}
    assert by_id["mockServices"]["label"] == "Service Virtualization"
    assert by_id["performance"]["unit"] == "virtual users"
    # A model with no measured figure offers no figure box.
    assert [m["measured"] for m in body] == [True, True, False]
    assert by_id["mockServices"]["figure_unit"] == "requests per second per core"


def test_the_pages_copy_of_the_sizing_models_is_the_planner_s():
    """fixtures.ts's copy of the sizing models equals the planner's."""
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const SIZING_MODELS: SizingModel\[\] = \[(.*?)\n\];",
                     text, re.S)
    assert body, "SIZING_MODELS not found -- was it renamed or moved?"
    served = client.get("/api/sizing-models").json()
    assert re.findall(r'functionality: "(\w+)"', body.group(1)) \
        == [m["functionality"] for m in served]
    assert re.findall(r"measured: (true|false)", body.group(1)) \
        == ["true" if m["measured"] else "false" for m in served]
    assert re.findall(r'unit: "([^"]+)"', body.group(1)) \
        == [v for m in served for v in (m["unit"], m["figure_unit"])]
    # ...including `label` and `pods`, which both render.
    assert re.findall(r'label: "([^"]+)"', body.group(1)) \
        == [m["label"] for m in served]
    assert re.findall(r'pods: "([^"]+)"', body.group(1)) \
        == [m["pods"] for m in served]
    # The page's default sizings are built from example_target.
    assert re.findall(r"example_target: (\d+)", body.group(1)) \
        == [str(m["example_target"]) for m in served]
    # Every field the page's SizingModel type declares.
    assert set(re.findall(r"(\w+):", body.group(1))) == {
        "functionality", "label", "unit", "figure_unit", "pods", "measured",
        "example_target"}


def test_the_engine_rating_is_answered_for_every_sizing_model():
    """engine-vus rates a pod per sizing model, null where unmeasured."""
    from bzm_opl_gen import plan as plan_mod
    body = client.get("/api/engine-vus",
                      params={"cpu": "4", "mem": "16Gi"}).json()
    assert set(body["rated"]) == set(plan_mod.SIZING_MODELS)
    # Twice the standard engine, twice the rating; supported_vus is the same.
    assert body["rated"]["performance"] == 1000 == body["supported_vus"]
    assert body["rated"]["functionalGui"] == 8
    assert body["rated"]["mockServices"] is None


def test_a_functionality_says_whether_its_agent_carries_an_engine():
    """`runs_engine` agrees with the planner: a model whose pods are engines is a
    functionality whose agent carries one."""
    from bzm_opl_gen import plan as plan_mod
    served = {f["id"]: f["runs_engine"]
              for f in client.get("/api/functionalities").json()}
    assert served == {"performance": True, "functionalGui": True,
                      "mockServices": False}
    for fid, m in plan_mod.SIZING_MODELS.items():
        assert served[fid] == (m["pod"] == "engine"), fid


def test_every_sizing_model_is_a_functionality_the_page_configures():
    """A model for a funcId with no card is a unit nothing can be asked for."""
    from bzm_opl_gen import plan as plan_mod
    assert set(plan_mod.SIZING_MODELS) <= set(core.covered_func_ids())


# -- changing a location's settings -------------------------------------------

def test_location_settings_reports_what_the_account_now_holds(monkeypatch):
    """Not what was sent. The panel shows this answer, so a field the account
    dropped has to arrive as dropped rather than as saved."""
    c = FakeClient(harbor={"id": "h1", "name": "loc", "slots": 2,
                           "threadsPerEngine": 500, "overrideCPU": None,
                           "overrideMemory": None},
                   ignores={"overrideCPU"})
    connect(monkeypatch, c)
    r = client.post("/api/locations/settings", json={
        "harbor_id": "h1", "threads_per_engine": 1000, "override_cpu": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] == {"threads_per_engine": 1000}
    assert body["ignored"] == ["override_cpu"]
    assert body["before"]["threads_per_engine"] == 500


def test_location_settings_leaves_out_what_the_form_did_not_send(monkeypatch):
    """The browser sends only the fields it changed; the rest must not be
    written back from a page that may have been open for an hour."""
    c = FakeClient(harbor={"id": "h1", "slots": 2, "threadsPerEngine": 500,
                           "overrideCPU": None, "overrideMemory": None})
    connect(monkeypatch, c)
    body = client.post("/api/locations/settings", json={
        "harbor_id": "h1", "threads_per_engine": 1000}).json()
    assert body["after"]["slots"] == 2
    assert body["changed"] == {"threads_per_engine": 1000}


def test_location_settings_refuses_a_field_it_does_not_own(monkeypatch):
    """`funcIds` is nobody's here: what a location runs changes in BlazeMeter's
    own UI, and this PATCH would replace the list wholesale."""
    connect(monkeypatch, FakeClient(harbor={"id": "h1"}))
    r = client.post("/api/locations/settings",
                    json={"harbor_id": "h1", "funcIds": ["mockServices"]})
    # Not a model field, so it is ignored and nothing is written.
    assert r.status_code == 200
    assert r.json()["changed"] == {}


def test_location_settings_needs_a_key(monkeypatch):
    monkeypatch.setitem(server._state, "client", None)
    r = client.post("/api/locations/settings",
                    json={"harbor_id": "h1", "slots": 3})
    assert r.status_code == 401


# -- the account tree, remembered for a minute --------------------------------
# The cache must not outlive a change this server made itself.

@pytest.fixture(autouse=True)
def _empty_cache():
    """Every test starts cold. Without this the cache is process-wide state
    shared between tests, which is how one passes because another ran first."""
    server._forget()
    yield
    server._forget()


def test_the_account_tree_is_read_once_not_once_per_reload(monkeypatch):
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    for _ in range(3):
        assert client.get("/api/locations?workspace_id=42").status_code == 200
    assert [x[0] for x in c.calls].count("private_locations") == 1


def test_a_created_location_is_not_hidden_by_the_cache(monkeypatch):
    """The write this server made itself is the one staleness it cannot
    tolerate: create a location, and it has to be in the next list."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    client.post("/api/locations", json={"name": "new", "account_id": 7,
                                        "workspace_id": 42})
    client.get("/api/locations?workspace_id=42")
    assert [x[0] for x in c.calls].count("private_locations") == 2


def test_changing_a_location_s_settings_drops_the_cache(monkeypatch):
    c = FakeClient(harbor={"id": "h1", "slots": 2, "threadsPerEngine": 500,
                           "overrideCPU": None, "overrideMemory": None},
                   locations=[{"id": "h1", "name": "loc", "slots": 2}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    client.post("/api/locations/settings",
                json={"harbor_id": "h1", "slots": 4})
    client.get("/api/locations?workspace_id=42")
    assert [x[0] for x in c.calls].count("private_locations") == 2


def test_a_new_agent_drops_the_cache(monkeypatch):
    c = FakeClient(harbor={"id": "h1", "ships": []},
                   locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    client.post("/api/ships", json={"harbor_id": "h1", "name": "agent1"})
    client.get("/api/locations?workspace_id=42")
    assert [x[0] for x in c.calls].count("private_locations") == 2


def test_a_different_key_is_a_different_account(monkeypatch, tmp_path):
    """Nothing read with the old credential may survive into the new one."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    assert server._cache
    client.delete("/api/key")
    assert not server._cache


def test_the_cache_expires(monkeypatch):
    """Sixty seconds, so a change made in the BlazeMeter UI shows up while you
    are still looking for it."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    now = time.monotonic()
    monkeypatch.setattr(server.time, "monotonic",
                        lambda: now + server.CACHE_TTL_S + 1)
    client.get("/api/locations?workspace_id=42")
    assert [x[0] for x in c.calls].count("private_locations") == 2


def test_refresh_is_what_makes_the_button_mean_anything(monkeypatch):
    """A read after Refresh reaches the account again."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    client.get("/api/locations?workspace_id=42")
    assert [x[0] for x in c.calls].count("private_locations") == 1
    assert client.post("/api/refresh").status_code == 200
    client.get("/api/locations?workspace_id=42")
    assert [x[0] for x in c.calls].count("private_locations") == 2


def test_refresh_reaches_blazemeter_by_itself_for_nothing(monkeypatch):
    """Refresh itself reads nothing from the account."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.post("/api/refresh")
    assert c.calls == []


def test_refresh_needs_no_key(monkeypatch):
    """Refresh needs no key."""
    server._state["client"] = None
    server._cache["locations:None:42"] = (time.monotonic() + 60, [])
    assert client.post("/api/refresh").status_code == 200
    assert not server._cache


class DeletedLocation(FakeClient):
    """An account that has had this location removed out from under the page."""

    def private_location(self, harbor_id):
        self.calls.append(("private_location", harbor_id))
        raise core.api.BzmApiError(
            f"GET /private-locations/{harbor_id} -> HTTP 404: not found",
            status=404)


def test_a_deleted_location_reaches_the_browser_as_a_404(monkeypatch):
    """A deleted location is a 404 with a detail (the page branches on status)."""
    connect(monkeypatch, DeletedLocation())
    r = client.get("/api/status?harbor_id=h1&ship_id=s1")
    assert r.status_code == 404
    assert "404" in r.json()["detail"]


def test_an_account_that_refuses_is_not_an_account_that_deleted_anything(
        monkeypatch):
    """An expired key is not a 404."""
    from test_core import ExpiredClient
    connect(monkeypatch, ExpiredClient())
    assert client.get("/api/status?harbor_id=h1&ship_id=s1").status_code == 502


def test_every_write_route_drops_the_cache():
    """Every route that writes to the account carries `_writes`."""
    writes = [r for r in app_routes()
              if "POST" in r.methods and r.path in {
                  "/api/locations", "/api/ships",
                  "/api/locations/settings", "/api/ships/token"}]
    assert len(writes) == 4, "a write route was renamed; name it here too"
    missing = [r.path for r in writes
               if getattr(r.endpoint, "__wrapped__", None) is None]
    assert not missing, (
        f"{missing} write to the account without _writes, so a read cached "
        f"before the click survives it")


def app_routes():
    return [r for r in server.app.routes if hasattr(r, "methods")]


def _keys(body):
    """Every key anywhere in a decoded response body."""
    if isinstance(body, dict):
        return set(body) | {k for v in body.values() for k in _keys(v)}
    if isinstance(body, list):
        return {k for v in body for k in _keys(v)}
    return set()


def test_this_api_never_says_feature():
    """No route path, response key or OpenAPI path says `feature`; the word is
    `functionality`. Every parameterless GET is called for its body."""
    paths = [r.path for r in app_routes()]
    assert not [p for p in paths if "feature" in p.lower()]

    served, seen = set(), []
    for r in app_routes():
        if "GET" not in r.methods or "{" in r.path or not r.path.startswith("/api/"):
            continue
        body = client.get(r.path)
        seen.append(r.path)
        try:
            served |= _keys(body.json())
        except ValueError:                    # the SPA's HTML, not an answer
            pass
    # Not empty, so a changed route registry cannot pass silently.
    assert len(seen) > 5, f"only reached {seen} -- did the routes move?"
    assert not [k for k in served if "feature" in k.lower()], sorted(served)

    spec = server.app.openapi()
    assert not [p for p in spec["paths"] if "feature" in p.lower()]


def test_an_agent_s_heartbeat_is_never_cached(monkeypatch):
    """Liveness is the one read that must always be live: the status poll is
    what says an agent came online, and a cached answer would say it had not."""
    c = FakeClient(harbor={"id": "h1", "ships": [
        {"id": "s1", "state": "idle", "lastHeartBeat": 0}]})
    connect(monkeypatch, c)
    for _ in range(3):
        client.get("/api/status?harbor_id=h1&ship_id=s1")
    assert [x[0] for x in c.calls].count("private_location") == 3


def _page_and_sources(monkeypatch, tmp_path, sources=None,
                      recorded="match"):
    """Point the routes at a frontend and a built page of this test's making.

    `recorded` is "match" for the sources' fingerprint, None for a page that
    records nothing, or any other string for a page built from something else."""
    frontend = tmp_path / "frontend"
    if sources is not None:
        (frontend / "src").mkdir(parents=True)
        for rel, body in sources.items():
            (frontend / rel).write_text(body)
    dist = tmp_path / "ui_dist"
    dist.mkdir()
    (dist / ui_build.BUILT_PAGE).write_text("<html></html>")
    if recorded == "match":
        recorded = ui_build.source_fingerprint(str(frontend))
    if recorded is not None:
        (dist / ui_build.FINGERPRINT_FILE).write_text(json.dumps(
            {"algorithm": ui_build.ALGORITHM, "fingerprint": recorded}))
    monkeypatch.setattr(server, "UI_DIST", str(dist))
    monkeypatch.setattr(server, "_FRONTEND", str(frontend))
    return frontend


def test_build_route_says_what_is_being_served(monkeypatch, tmp_path):
    """/api/build reports version, build time, staleness and commit."""
    body = client.get("/api/build").json()
    assert set(body) == {"version", "built", "stale", "commit"}

    frontend = _page_and_sources(monkeypatch, tmp_path, {"src/App.tsx": "x"})
    assert server.build_state()["stale"] is False

    (frontend / "src" / "App.tsx").write_text("y")
    assert server.build_state()["stale"] is True


def test_the_route_answers_staleness_by_content_and_not_by_a_clock(
        monkeypatch, tmp_path):
    """Staleness is by content: touching the sources does not make the page stale."""
    frontend = _page_and_sources(monkeypatch, tmp_path, {"src/App.tsx": "x"})
    touched = frontend / "src" / "App.tsx"
    os.utime(touched, (10 ** 10, 10 ** 10))     # newer than the built page
    assert server.build_state()["stale"] is False


def test_a_page_that_records_nothing_is_its_own_answer(monkeypatch, tmp_path):
    """A page that records no fingerprint is served as "unrecorded"."""
    _page_and_sources(monkeypatch, tmp_path, {"src/App.tsx": "x"}, recorded=None)
    assert server.build_state()["stale"] == "unrecorded"
    assert client.get("/api/build").json()["stale"] == "unrecorded"


def test_a_wheel_has_no_source_to_be_stale_against(monkeypatch, tmp_path):
    """A wheel (no frontend) answers None, never False."""
    _page_and_sources(monkeypatch, tmp_path, sources=None, recorded="deadbeef")
    assert server.build_state()["stale"] is None
    assert client.get("/api/build").json()["stale"] is None


def test_only_a_stale_page_is_worded_as_a_warning(monkeypatch, capsys):
    """At startup only a stale page prints the `!!` warning; "unrecorded" prints
    a notice and the other two print nothing."""
    def printed(state):
        monkeypatch.setattr(server, "build_state",
                            lambda: {"stale": state}, raising=True)
        capsys.readouterr()
        _served(monkeypatch)
        return capsys.readouterr().out

    warned = printed(True)
    assert "!!" in warned and "not built from" in warned

    noted = printed(ui_build.UNRECORDED)
    assert "!!" not in noted                    # nothing is known to be wrong
    assert "records nothing about the sources" in noted
    assert "npm run build" in noted             # and a rebuild answers it

    assert "npm run build" not in printed(False)
    assert "npm run build" not in printed(None)


# -- /api/images: the catalogue the page shows beside the bundle --------------------

IMAGES_KEYS = {"source", "location", "image_list_state", "registry_lookup",
               "images"}


def test_images_without_a_location_is_the_catalogue_and_needs_no_key():
    r = client.get("/api/images", params={"lookup": "false"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == IMAGES_KEYS
    assert body["source"] == "catalogue" and body["location"] is None
    assert body["registry_lookup"] == {"state": "not-asked", "detail": None}
    assert all(i["required"] is None for i in body["images"])


def test_images_for_a_location_needs_a_key():
    r = client.get("/api/images", params={"harbor_id": "h1", "lookup": "false"})
    assert r.status_code == 401


def test_images_reads_the_same_cached_facts_as_the_facts_route(monkeypatch):
    c = connect(monkeypatch, FakeClient(harbor={
        "id": "h1", "name": "L", "funcIds": ["performance"], "ships": []}))
    client.get("/api/facts", params={"harbor_id": "h1"})
    r = client.get("/api/images", params={"harbor_id": "h1", "lookup": "false",
                                          "all": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["location"] == {"harbor_id": "h1", "name": "L",
                                "func_ids": ["performance"]}
    assert body["image_list_state"] == "no-agent"
    assert {i["required"] for i in body["images"]} == {True, False}
    assert [x[0] for x in c.calls].count("private_location") == 1


def test_an_unreachable_registry_is_an_answer_not_an_error():
    """lookup defaults on; offline, every image is unread and the route is 200."""
    body = client.get("/api/images").json()
    assert body["registry_lookup"]["state"] == "unread"
    assert body["registry_lookup"]["detail"]
