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

from bzm_opl_gen import core, server  # noqa: E402
from test_generate import FACTS  # noqa: E402
# The same fakes tests/test_core.py and tests/test_cli.py drive, for the same
# reason they share them: three surfaces call the same core functions, and a
# fake per suite is how two of them end up disagreeing about what an account
# answers. `calls` is what makes "nothing was minted" assertable at all.
from test_core import FakeClient, RefusingClient  # noqa: E402

client = TestClient(server.app)


def connect(monkeypatch, account):
    """Put this server process in the state of being connected as `account`.

    The one place this suite stands in at, and it is `server._state` rather
    than `core.client_from_key` -- which is where tests/test_cli.py and
    tests/test_mcp.py stand in -- because those two build a client per call and
    this one does not. A browser session connects once, over `POST /api/key`,
    and every route afterwards acts as whatever that left in the process; the
    fact under test in most of what follows is "this server is connected as X",
    which is a fact about the server and has no equivalent in core. Patching
    the construction here would leave the state item set to None and every
    route 401ing before core was reached.

    A function rather than twenty `setitem` calls so that if that ever stops
    being true there is one place to change. The key-lifecycle tests below set
    the item directly on purpose: there _state is the subject, not the stand-in.
    """
    monkeypatch.setitem(server._state, "client", account)
    return account


def test_generate_preview_no_key_needed():
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert names[0] == "bzm_serviceaccount.yaml"      # apply order
    assert "bzm_deployment.yaml" in names and "README.md" in names


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


def test_generate_helm_rejects_service_virtualization_400():
    """The UI disables the segment for an SV location; this is the other half --
    an imported profile can arrive set to helm."""
    facts = dict(FACTS, func_ids=["mockServices"])
    r = client.post("/api/generate", json={
        "facts": facts,
        "options": {"namespace": "ns1", "output_format": "helm",
                    "sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
                    "sv_tls_secret": "wildcard"}})
    assert r.status_code == 400
    assert "performance testing only" in r.json()["detail"]


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


def test_manual_facts_flag_the_gui_image_gap():
    r = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["functionalGui"]})
    assert r.json()["gui_images_incomplete"] is True


def test_manual_facts_generate_without_a_token_fetch():
    """The typed token is the bundle's, and a key left over from an earlier
    connect must not be asked for one belonging to somebody else's agent. Free
    now that no route mints unasked, and asserted anyway: this mode is where a
    stray mint would be least visible, since there is no agent on screen."""
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
# The UI half of #64. Which of the four branches a token arrives by is core's
# rule and is tested in tests/test_core.py; what these pin is that no route here
# takes the one that mints unless it was asked to, and that the answer says which
# one it took. The download button was the last caller that rotated a live
# agent's credential as a side effect of being asked for a zip -- silently, and
# the pod it broke reads as a slow boot.

@pytest.fixture
def connected(monkeypatch):
    """A browser session holding an API key, counting what it was asked for.

    Holding one is the whole hazard: every assertion below is about a route that
    *could* mint and must not, so a fixture with no client would pass for the
    wrong reason.
    """
    return connect(monkeypatch, FakeClient())


@pytest.mark.parametrize("route", ["/api/generate", "/api/generate/zip",
                                   "/api/generate/save"])
def test_no_route_mints_unless_it_was_asked_to(connected, route, tmp_path):
    """All three, parametrised: the preview was already safe, and the two that
    hand a bundle over were not. A rule that holds on one of them is the bug."""
    r = client.post(route, json={"facts": FACTS, "out_dir": str(tmp_path / "b"),
                                 "options": {"namespace": "ns1"}})
    assert r.status_code == 200
    assert connected.calls == []


def test_the_preview_can_say_a_save_would_reuse_the_folder_s_token(
        connected, tmp_path):
    """The preview took no out_dir, so its branch could never be `reused` -- and
    the page reported "placeholder, fill it in before applying" over a folder
    whose own token a save was about to keep. Misleading in the one direction
    that matters: it invites a rotation nothing needed."""
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
    """The save route resolved the token first and hit the relative-path refusal
    afterwards, so a rotation that was then thrown away had already killed the
    running agent -- the exact failure #64 exists to prevent, on the one surface
    that had no guard. `require_absolute_out_dir` says so itself, and the MCP
    already ordered it this way; this is the missing half of "one copy of the
    rule, two moments"."""
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": "some/relative/dir", "rotate_token": True,
        "options": {"namespace": "ns1", "ship_id": FACTS["ships"][0]["id"]}})
    assert r.status_code == 400
    assert connected.calls == [], "it minted a credential it then threw away"


def test_a_page_still_asking_for_the_old_fetch_mints_nothing(connected):
    """`fetch_token` is gone from the request model, and a browser holding the
    previously-shipped bundle posts it on every download. Ignored rather than
    refused: a 422 would break that page, and the field only ever meant mint."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "fetch_token": True})
    assert r.status_code == 200 and connected.calls == []


def test_rotating_mints_once_and_names_whose_credential_it_replaced(connected):
    """Once, not twice: the route resolves the token itself to report the branch
    and then generates from the same options, so a resolution that did not stick
    would issue two tokens and leave the bundle holding the older one."""
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "rotate_token": True})
    assert connected.calls == [("auth_token", "aaa111", "bbb222")]
    token = r.json()["token"]
    assert (token["branch"], token["ship_id"]) == (core.TOKEN_ROTATED, "bbb222")
    assert "bbb222" in token["message"]
    secret = next(f for f in r.json()["files"] if f["name"] == "bzm_secret.yaml")
    assert "TOKEN-FROM-API" in secret["content"]


def test_a_bundle_with_no_token_says_so_rather_than_looking_finished(connected):
    """The default download for an agent nobody pasted a token for. It is a fine
    bundle to read and an unusable one to apply, and the only thing standing
    between those two readings is this sentence reaching the page."""
    body = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}}).json()
    assert body["token"]["branch"] == core.TOKEN_PLACEHOLDER
    assert "create-agent" in body["token"]["message"]


def test_the_zip_says_in_its_headers_which_branch_it_took(connected):
    """A zip's body is the bundle, so the branch travels beside the filename in
    the headers -- wrapping the bytes in an envelope would mean the browser
    saving something that is not a zip."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    # The literals, because the frontend reads these two names off the response
    # and a rename on one side loses the sentence silently rather than failing.
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
    """One name, held equal on the route rather than in each half: the header
    the browser saves the file under and the directory every entry sits in. They
    were computed apart, so a bundle downloaded as `bzm-opl-ns1.zip` extracted
    to `bzm-opl/`, and two locations' bundles merged into one folder."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    name = re.search(r'filename="([^"]+)"', r.headers["Content-Disposition"])[1]
    assert name.endswith(".zip")
    roots = {n.split("/")[0]
             for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist()}
    assert roots == {name[:-len(".zip")]}


def test_a_namespace_no_header_could_carry_does_not_fail_the_download(connected):
    """Both this route's headers quote the namespace -- the token message does and
    the zip's filename always did -- and a person types that into a browser. A
    header is latin-1 by the HTTP spec and starlette raises on anything else, so
    an unencodable character lost the whole download, which is a worse answer than
    a mangled filename. Fixed by the route, not by the namespace: a namespace a
    cluster would accept is an RFC 1123 label, so this is a typo either way."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "blazemeter-平"}})
    assert r.status_code == 200
    assert r.headers[server.TOKEN_BRANCH_HEADER] == core.TOKEN_PLACEHOLDER
    assert "attachment" in r.headers["Content-Disposition"]
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist()


BRANCHES = {core.TOKEN_GIVEN, core.TOKEN_ROTATED, core.TOKEN_REUSED,
            core.TOKEN_PLACEHOLDER}


def test_the_four_branch_names_are_what_the_page_switches_on():
    """frontend/src/api.ts declares this union rather than fetching it -- a closed
    set, like Strength and MergeState -- so the four spellings are load-bearing
    across two languages. Renamed here, one arrives in a browser as a branch no
    sentence covers, and the compiler over there cannot see it."""
    assert BRANCHES == {"given", "rotated", "reused", "placeholder"}


def test_the_page_declares_the_same_four_branches_this_does():
    """Read out of the TypeScript, not restated here.

    This test used to compare core's four constants against four literals and
    say in its docstring that api.ts declared the same union -- which nothing
    checked. A rename on either side left the other compiling: TypeScript cannot
    see Python, and a literal in a Python test cannot see TypeScript. So the
    union and the map that must cover it are parsed from the files themselves,
    and this fails on whichever side moves first.
    """
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    union = re.search(r"export type TokenBranch\s*=\s*([^;]+);",
                      (src / "api.ts").read_text())
    assert union, "TokenBranch union not found -- was it renamed or moved?"
    assert set(re.findall(r'"([^"]+)"', union.group(1))) == BRANCHES

    # The map keyed by that union: every branch needs the sentence beside the
    # button, and TypeScript's Record<TokenBranch, string> only enforces that
    # against whatever the union happens to say.
    carries = re.search(r"const CARRIES: Record<TokenBranch, string> = \{(.*?)\}",
                        (src / "token.ts").read_text(), re.S)
    assert carries, "CARRIES not found -- was it renamed or moved?"
    assert set(re.findall(r"^\s*(\w+):", carries.group(1), re.M)) == BRANCHES


def test_the_page_spells_the_declined_ingress_the_way_generate_does():
    """Read out of the TypeScript for the same reason as the union above.

    optionGroups.ts is pure data functions -- `detect(o)` is handed options and
    nothing else -- so the sentinel cannot arrive there from /api/sv-constants
    the way `ingress_types` does; it is a literal, and a literal in one language
    cannot see a constant in the other. Renamed on either side without this, the
    switch writes a value generate() refuses and the group snaps back on, which
    is the whole bug this option exists to fix.
    """
    from bzm_opl_gen import generate as gen_mod
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    m = re.search(r'export const SV_NONE = "([^"]+)"',
                  (src / "optionGroups.ts").read_text())
    assert m, "SV_NONE not found -- was it renamed or moved?"
    assert m.group(1) == gen_mod.SV_INGRESS_NONE


def test_every_group_s_tag_is_a_functionality_this_server_serves():
    """Read out of the TypeScript for the same reason again, and load-bearing
    since #113.

    A group tags itself with the functionality ids it belongs to; the ids
    themselves are served from core.FUNCTIONALITIES and enumerated nowhere in
    the frontend. A tag naming something not in that list was always a group on
    no card -- and now it is worse than invisible: `notRunPatch` clears the
    groups of a functionality the location does not run, and a functionality
    nothing serves is never run, so the group's options would be wiped by a rule
    nobody could see applying.
    """
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    body = re.search(r"export const OPTION_GROUPS: OptionGroup\[\] = \[(.*?)\n\];",
                     (src / "optionGroups.ts").read_text(), re.S)
    assert body, "OPTION_GROUPS not found -- was it renamed or moved?"
    tagged = set(re.findall(r'"([^"]+)"',
                            " ".join(re.findall(
                                r"^\s*functionalities: \[(.*?)\],$",
                                body.group(1), re.M))))
    # Not empty: every group being untagged would pass a subset check silently,
    # and that is the shape a bad regex leaves behind.
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
    """#91. The file is there, so the route's own existence check passes, and
    what it hands over is unparseable. Read inside `api.BzmClient(path)` that
    was a SystemExit -- a BaseException raised inside a route, which no `except
    Exception` anywhere in the stack stops. The assertion is as much that this
    call *returns* as that it says the right thing: an escaping SystemExit fails
    it before status_code is reached.
    """
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
    """The other half of #91: the happy path goes through the same
    construction and is unchanged by it."""
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
    """core's key directory, somewhere disposable. Never the real one: these
    write a key file, and the developer running this has their own at
    ~/.config/bzm-opl-gen/api-key.json."""
    d = tmp_path / "config"
    monkeypatch.setattr(core, "CONFIG_DIR", str(d))
    monkeypatch.setattr(core, "SAVED_KEY_PATH", str(d / "api-key.json"))
    monkeypatch.setattr(core, "user", lambda c: {
        "email": "se@example.com", "displayName": "SE",
        "defaultProject": {"accountId": 7}})
    return d


def test_a_pasted_key_reaches_core_as_a_pair_and_never_the_disk(monkeypatch,
                                                                tmp_path):
    """#95. A key typed into the connect form has no file behind it, and this
    route used to make one -- writing the secret to `.session-key.json`, calling
    a constructor that took only a path, and unlinking it after. A secret on
    disk to satisfy an argument list is worse than the argument, and since #92
    the construction takes the pair."""
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
    """#64's point, and the reason the rest of the UI can stop minting: the token
    is captured at the one moment it costs nothing, when the ship is new and has
    no previous credential to invalidate. core.create_ship does not fetch,
    because for an *existing* ship it would rotate one on an action whose name
    says nothing about credentials; `bzm-opl-gen create-agent` fetches for exactly
    this reason, and this is the same command with a browser in front of it."""
    body = client.post("/api/ships", json={
        "harbor_id": "aaa111", "name": "agent1"}).json()
    assert body["ship"]["id"] == "s2"
    assert body["auth_token"] == "TOKEN-FROM-API"
    assert body["token_error"] is None
    # For the ship it just made, not for whatever was selected before it.
    assert connected.calls == [("auth_token", "aaa111", "s2")]


def test_an_agent_whose_credential_was_refused_is_still_reported(monkeypatch):
    """The ship exists before the fetch, and some accounts refuse the token
    endpoint outright. A 502 would leave the new agent's id nowhere but a browser
    console -- and the next click creates a second agent in the same location."""
    connect(monkeypatch, RefusingClient())
    r = client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ship"]["id"] == "s2" and body["auth_token"] is None
    assert "could not be issued" in body["token_error"]
    # The way on, not just the failure: a bundle takes a token that was read off
    # the agent in the BlazeMeter UI just as happily as a fetched one.
    assert "auth_token" in body["token_error"]


def test_issuing_a_token_is_its_own_route(connected):
    """Not a flag on generate. Rotating as a side effect of asking for files is
    what #64 took out; a route whose whole name is the action cannot be reached
    by accident, and the page calling it has already said what it costs."""
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
# The other half of the two moments above. This app is shown a token exactly
# where it mints one, and until #123 the browser's options were the only copy:
# no API reads an AUTH_TOKEN back, so a reload dropped it for good and the next
# bundle silently carried a placeholder for an agent created a minute earlier.
# What these pin is that the store answers for the ship it was asked about and
# for no other, and that "nothing here" never arrives looking like anything else.

@pytest.fixture(autouse=True)
def _no_remembered_tokens():
    """Every test starts having minted nothing.

    Process-wide state shared between tests, like the cache below -- and worse
    to leave lying about, because the value is a credential and the assertion
    most of these make is that there is *not* one.
    """
    server._minted_tokens.clear()
    yield
    server._minted_tokens.clear()


class TwoAgentAccount(FakeClient):
    """An account where each agent is genuinely a different agent.

    FakeClient answers `s2` to every create and one constant to every token
    request, which is enough for the routes above and not enough here: a store
    keyed by ship and a store holding only the last token it was shown look
    identical against it.
    """

    def create_ship(self, harbor_id, name):
        return {"id": f"s-{name}", "name": name}

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        return f"TOKEN-{ship_id}"


def test_a_created_agent_s_credential_outlives_the_page_that_asked_for_it(
        connected):
    """The whole of #123: the token is captured at creation, and a refresh used
    to lose it because the browser held the only copy."""
    client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    r = client.get("/api/ships/minted-token?ship_id=s2")
    assert r.status_code == 200
    assert r.json()["auth_token"] == "TOKEN-FROM-API"
    # Read out of this process, not off the account: nothing reads a credential
    # back from BlazeMeter, and a lookup that asked would be minting.
    assert connected.calls == [("auth_token", "aaa111", "s2")]


def test_a_regenerated_credential_is_what_is_remembered_afterwards(connected):
    """The second of the two moments. A store that only followed creation would
    hand back the dead token after a Regenerate -- which applies cleanly and
    leaves the agent at 0/1."""
    client.post("/api/ships/token",
                json={"harbor_id": "aaa111", "ship_id": "s1"})
    assert client.get("/api/ships/minted-token?ship_id=s1").json() == {
        "auth_token": "TOKEN-FROM-API"}


def test_a_token_is_only_ever_found_under_the_ship_it_belongs_to(monkeypatch):
    """Two agents in one location, and neither one's credential can be attached
    to the other. By construction rather than by forgetting: the page used to
    keep one token and clear it whenever the target moved, which is the same
    guarantee held together by every caller remembering to let go."""
    connect(monkeypatch, TwoAgentAccount())
    for name in ("alpha", "bravo"):
        client.post("/api/ships", json={"harbor_id": "aaa111", "name": name})
    for name in ("alpha", "bravo"):
        assert client.get(
            f"/api/ships/minted-token?ship_id=s-{name}").json() == {
                "auth_token": f"TOKEN-s-{name}"}


def test_an_agent_this_app_never_minted_for_reads_as_no_token(connected):
    """Null, and a 200 -- an agent that already existed is the ordinary case,
    not a failure. Its token was issued once at creation and no API reads one
    back, so the page shows the placeholder and says why."""
    r = client.get("/api/ships/minted-token?ship_id=s1")
    assert r.status_code == 200 and r.json() == {"auth_token": None}
    assert connected.calls == []


def test_a_credential_that_was_refused_is_not_remembered_as_one(monkeypatch):
    """The agent exists and its token does not. Storing the None would make an
    entry meaning "asked, got nothing", which reads back as the same null the
    ship nobody minted for gives -- and this is the codebase where those two
    must not share a representation. There is nothing to remember, so nothing
    is written, and the answer is the honest empty one."""
    connect(monkeypatch, RefusingClient())
    assert client.post(
        "/api/ships", json={"harbor_id": "aaa111", "name": "agent1"}
    ).json()["auth_token"] is None
    assert server._minted_tokens == {}
    assert client.get("/api/ships/minted-token?ship_id=s2").json() == {
        "auth_token": None}


def test_a_token_typed_over_evicts_the_one_that_was_minted(connected):
    """A pasted token wins, and has to go on winning after a reload -- otherwise
    the remembered copy comes back and quietly replaces what was typed over it.
    The page cannot store the pasted one instead (session.strip), so the
    eviction is the whole mechanism."""
    client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert client.delete("/api/ships/minted-token?ship_id=s2").json() == {
        "forgotten": True}
    assert client.get("/api/ships/minted-token?ship_id=s2").json() == {
        "auth_token": None}
    # Idempotent, because the field it is driven from is a controlled input and
    # a second keystroke must not be an error.
    assert client.delete("/api/ships/minted-token?ship_id=s2").json() == {
        "forgotten": False}


def test_disconnecting_forgets_every_credential_this_app_minted(monkeypatch):
    """They were issued with the key being handed back, and they go with it and
    with the account tree -- the same clear the page makes of everything read
    under that key. Reconnecting offers a token for nothing."""
    connect(monkeypatch, FakeClient())
    client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert server._minted_tokens
    client.delete("/api/key")
    assert server._minted_tokens == {}
    assert client.get("/api/ships/minted-token?ship_id=s2").json() == {
        "auth_token": None}


def test_forgetting_a_minted_token_is_not_a_write_to_the_account(monkeypatch):
    """`_writes` is about the customer's account, and this changed nothing in
    one. Dropping the cache here would say a location list read a moment ago had
    been invalidated by somebody typing in a password field."""
    c = FakeClient(locations=[{"id": "h1", "name": "loc", "slots": 1}])
    connect(monkeypatch, c)
    client.get("/api/locations?workspace_id=42")
    assert server._cache
    client.delete("/api/ships/minted-token?ship_id=s1")
    assert server._cache


def test_the_store_is_addressed_by_ship_and_never_by_token():
    """A secret in a query string is a secret in every access log between the
    browser and here, and a lookup keyed by the value would have needed one. The
    key to the entry is all either route takes."""
    for name in ("ship_minted_token", "ship_forget_minted_token"):
        params = inspect.signature(getattr(server, name)).parameters
        assert set(params) == {"ship_id"}, (
            f"{name} takes {sorted(params)}; the token is not an argument to "
            f"anything that remembers it")


def test_a_remembered_credential_never_reaches_a_log_line(connected, caplog):
    """Not a hypothetical: a print left in while debugging a store is a
    credential in a terminal, in a screen share, and in whatever collects the
    stdout of the LaunchAgent this runs under. The whole lifecycle, at the
    loudest level anything here could log at."""
    with caplog.at_level(logging.DEBUG):
        client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
        client.get("/api/ships/minted-token?ship_id=s2")
        client.delete("/api/ships/minted-token?ship_id=s2")
        # ...and the shape of a request that is not one, which is where FastAPI
        # echoes the input back: the ship id is the only input there is.
        missing = client.get("/api/ships/minted-token")
    assert "TOKEN-FROM-API" not in caplog.text
    assert missing.status_code == 422
    assert "TOKEN-FROM-API" not in missing.text and "ship_id" in missing.text


def test_no_route_here_turns_a_functionality_on_for_a_location(monkeypatch):
    """POST /api/locations/func-id went with the affordance that was its only
    caller (#113). What funcIds a location carries is what the location *is*,
    and BlazeMeter's own UI is where that changes -- so a page that offered it
    made a bundle's configuration a reason to edit the account. 404 rather than
    a passthrough that has quietly stopped being reachable."""
    assert "/api/locations/func-id" not in {r.path for r in app_routes()}
    fake = FakeClient(harbor={"id": "aaa111", "funcIds": ["performance"]})
    connect(monkeypatch, fake)
    r = client.post("/api/locations/func-id",
                    json={"harbor_id": "aaa111", "func_id": "mockServices"})
    # 405, not 404: the SPA catch-all claims every unmatched path for GET. What
    # matters is the same either way -- refused, and nothing reached the account.
    assert r.status_code >= 400
    assert not [c for c in fake.calls if c[0] == "update_private_location"]


def test_func_ids_mark_which_ones_change_the_images(monkeypatch):
    """The create-location form needs every funcId the account offers; the
    manual form needs only the ones that change the answer. Both read this one
    response, so the distinction is served rather than re-derived in
    TypeScript."""
    connect(monkeypatch, FakeClient())
    by_id = {r["id"]: r for r in
             client.get("/api/func-ids?account_id=291446").json()}
    for f in ("performance", "mockServices", "proxyRecorder", "functionalGui"):
        assert by_id[f]["changes_images"] is True
    # A funcId whose images no bundle selects on. Offered, because the location
    # runs it and the page has to be able to name it -- see `covered`.
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
    from bzm_opl_gen import generate as gen_mod
    assert set(client.get("/api/option-defaults").json()) == set(gen_mod.DEFAULT_OPTIONS)


def test_option_docs_describe_every_option():
    """Field help comes from the registry rather than a copy in TypeScript --
    an option the UI renders with no description is one the registry is missing,
    and that is a test failure, not a blank tooltip."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/option-docs").json()
    assert set(body) == set(gen_mod.DEFAULT_OPTIONS)
    assert all(e["summary"] for e in body.values())
    assert body["sv_ingress"]["choices"] == (
        list(gen_mod.SV_INGRESS_TYPES) + [gen_mod.SV_INGRESS_NONE])
    assert body["private_registry"]["nullable"] is True
    # The UI must be able to tell which field not to echo back into a form it
    # might save; only the credential is marked.
    assert [k for k, e in body.items() if e["secret"]] == ["auth_token"]


def test_generate_invalid_options_400():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)    # ambiguous ship
    r = client.post("/api/generate", json={
        "facts": facts, "options": {}})
    assert r.status_code == 400


def test_sv_constants_are_served_from_the_generator():
    """The UI renders the ingress picker and decides the SV group is mandatory
    from these. Served rather than copied into TypeScript, so a new backend
    cannot be added to generate() and silently miss the picker."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/sv-constants").json()
    assert body["func_ids"] == list(gen_mod.SV_FUNC_IDS)
    assert body["ingress_types"] == list(gen_mod.SV_INGRESS_TYPES)
    assert "openshift" in body["ingress_types"]     # the newest one reaches the UI
    # The decline is NOT here. It is not a backend, and this response is what
    # the picker is built from -- offering it would be offering an ingress that
    # is not one. Where a caller learns it is the option registry's `choices`,
    # which is where the rest of what a value may be already lives.
    assert gen_mod.SV_INGRESS_NONE not in body["ingress_types"]
    assert gen_mod.SV_INGRESS_NONE in client.get(
        "/api/option-docs").json()["sv_ingress"]["choices"]
    # Kept out of option-defaults: that response is spread into the options the
    # UI submits, and these are not options.
    assert "ingress_types" not in client.get("/api/option-defaults").json()


def test_docker_ignored_is_served_from_the_generator():
    """What the configure step hides when the bundle is a docker one. Served
    for the same reason the SV vocabulary is: the page would otherwise carry a
    second copy of two dozen option keys, and a key added to the generator
    would go on being offered for a format that drops it."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/docker-ignored").json()
    assert body == gen_mod.DOCKER_IGNORED
    # The four the page hides whole sections for.
    for key in ("namespace", "service_account_name", "node_selector",
                "engine_cpu_limit"):
        assert body[key]
    # Every key is a real option: a name that matched nothing would hide
    # nothing, and would say so nowhere.
    assert not set(body) - set(client.get("/api/option-defaults").json())


def test_the_page_knows_the_same_three_formats_the_generator_does():
    """Read out of the TypeScript for the same reason as SV_NONE above.

    The ids are a closed set of three and the labels beside them are UI prose
    with no counterpart here, so the list is declared there rather than fetched
    -- but a fourth format added to the generator and not to that file is a
    control nobody can reach, and one removed is a segment that generates an
    error. Neither shows up in a type.
    """
    from bzm_opl_gen import generate as gen_mod
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    body = re.search(r"export const OUTPUT_FORMATS: OutputFormat\[\] = \[(.*?)\n\];",
                     (src / "formats.ts").read_text(), re.S)
    assert body, "OUTPUT_FORMATS not found -- was it renamed or moved?"
    assert tuple(re.findall(r'id: "([^"]+)"', body.group(1))) \
        == gen_mod.OUTPUT_FORMATS


def test_the_page_blocks_exactly_the_formats_that_refuse_a_virtual_service():
    """sv.ts takes an output format away from a bundle that carries a virtual
    service, and the refusal it is mirroring is generate()'s.

    Derived by asking the generator rather than by reading it: the refusals are
    two separate raises a long way apart, and this is the one thing about them
    the page restates. A third format growing one would leave a segment offered
    that cannot generate -- an off-screen blocker -- and helm losing its would
    leave a working segment disabled with a sentence about a chart that now
    carries an ingress.

    #115 was this same mismatch one level up and is why it is pinned: the page
    disabled these segments off the *location's demand*, while `_sv_cfg`
    refuses on the *configuration* and never looks at the funcIds. A location
    demanding nothing could therefore be configured for service virtualization
    and generated as docker, which the server refused with nothing on screen
    having said so.
    """
    from bzm_opl_gen import generate as gen_mod
    facts = {"harbor_id": "aaa111", "func_ids": ["mockServices"],
             "crane_image": "example.invalid/blazemeter/crane:3.7.55",
             "images": [], "ships": []}
    sv_opts = {"ship_id": "bbb222", "auth_token": "de" * 32,
               "sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
               "sv_tls_secret": "wildcard-credential"}
    refused = set()
    for fmt in gen_mod.OUTPUT_FORMATS:
        try:
            gen_mod.generate(facts, {**sv_opts, "output_format": fmt})
        except ValueError as exc:
            assert "service virtualization" in str(exc)
            refused.add(fmt)
    assert refused, "no format refuses a virtual service -- did the check move?"
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    body = re.search(
        r"const BLOCKED_FORMATS: Record<string, string> = \{(.*?)\n\};",
        (src / "sv.ts").read_text(), re.S)
    assert body, "BLOCKED_FORMATS not found -- was it renamed or moved?"
    assert set(re.findall(r"^  (\w+):", body.group(1), re.M)) == refused


def test_the_pages_copy_of_the_ignored_table_is_the_generators():
    """The one copy of DOCKER_IGNORED in TypeScript, held equal to this one.

    It cannot be derived -- the authority is Python and the page's tests run
    without a server -- so it is a fixture, and a fixture of a table is a table
    free to go stale. Two of them already had: formats.test.ts and App.test.tsx
    carried slices that differed by five keys, so the page test asserted against
    a table the unit test would have called incomplete. Now there is one, and
    this is what keeps it honest.
    """
    from bzm_opl_gen import generate as gen_mod
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const DOCKER_IGNORED: Record<string, string> = \{"
                     r"(.*?)\n\};", text, re.S)
    assert body, "DOCKER_IGNORED not found -- was it renamed or moved?"
    assert set(re.findall(r"^  (\w+):", body.group(1), re.M)) \
        == set(gen_mod.DOCKER_IGNORED)


def test_the_placeholder_marker_is_one_string_in_both_languages():
    """The page writes it into what it sends and the generator recognises it
    coming back, so a marker that differed by a character would be carried into
    the bundle as a value somebody meant -- silently, and in the one field
    nobody filled in. Not served like DOCKER_IGNORED, because the page has to
    write it before any response has arrived; held equal here instead, which is
    what every other constant these two share does."""
    from bzm_opl_gen import generate as gen_mod
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "placeholder.ts").read_text()
    m = re.search(r'export const PLACEHOLDER = "([^"]+)";', text)
    assert m, "PLACEHOLDER not found -- was it renamed or moved?"
    assert m.group(1) == gen_mod.PLACEHOLDER


def test_reserved_env_is_served_with_the_option_that_owns_each_name():
    """The env area on the configure step refuses a name the bundle already
    writes, and it must not keep its own list of them -- a variable added to a
    template would go on being offered, and the collision would surface as a
    ConfigMap with a duplicate key rather than as a message on the row.

    The owner is served beside the name because it is the answer: "set it with
    the proxy option" beats "that one is taken"."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/reserved-env").json()
    assert set(body) == set(gen_mod.RESERVED_ENV)
    assert body["KUBERNETES_SERVICE_USE_TYPE"] == "service_type"
    # Null is a real answer: the identity variables belong to no option, and
    # naming one would be worse than saying there is not one.
    assert body["SHIP_ID"] is None
    # Every owner named is a real option, or the message sends someone to a
    # field that does not exist. The CA trio names a one-of pair, which is what
    # the option table itself calls them.
    defaults = client.get("/api/option-defaults").json()
    for owner in filter(None, body.values()):
        for name in owner.split(" | "):
            assert name in defaults, f"{owner} names no option"


def test_agent_env_is_served_as_what_is_left_after_the_options():
    """The other half of the env area: the variables it offers.

    Served rather than listed in TypeScript for the reason the reserved names
    are -- but the direction is the opposite one, and that is the point. The
    reserved table says what may not be typed; this says what there is to
    choose from, and it is BlazeMeter's own reference minus everything a
    control on the configure step already writes. The two must not overlap, or
    the page offers a row the generator refuses.
    """
    from bzm_opl_gen import agent_env as env_mod, generate as gen_mod
    body = client.get("/api/agent-env").json()
    names = {v["name"] for v in body}
    assert names == {v["name"] for v in env_mod.AGENT_ENV} - gen_mod.RESERVED_ENV
    assert not names & set(client.get("/api/reserved-env").json())
    # A row the page can render: a control is chosen from `type`, and the two
    # tables decide which bundles are offered it.
    for v in body:
        assert v["type"] in env_mod.TYPES
        assert set(v["platforms"]) <= {"kubernetes", "docker"}
        assert v["summary"]


def test_the_pages_copy_of_the_env_name_rule_is_the_generators():
    """The *names* are served; what a name may look like is not, and could not
    usefully be -- it is a regex, and a page that had to compile a served one
    could not typecheck it. So it is a second copy, and this is the only thing
    that can hold it equal: a page accepting a name the generator refuses is a
    row that goes green and a download that fails."""
    from bzm_opl_gen import generate as gen_mod
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    pattern = re.search(r"^const NAME_RE = /(.+)/;$",
                        (src / "env.ts").read_text(), re.M)
    assert pattern, "NAME_RE not found -- was it renamed or moved?"
    assert pattern.group(1) == gen_mod.ENV_NAME_RE.pattern


def test_the_pages_copy_of_the_reserved_env_names_is_the_generators():
    """As with DOCKER_IGNORED above: the page's tests run without a server, so
    the fixture is a second copy, and this is what keeps it from drifting."""
    from bzm_opl_gen import generate as gen_mod
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    text = (src / "fixtures.ts").read_text()
    body = re.search(r"export const RESERVED_ENV: Record<string, string \| null> = \{"
                     r"(.*?)\n\};", text, re.S)
    assert body, "RESERVED_ENV not found -- was it renamed or moved?"
    assert set(re.findall(r"^  (\w+):", body.group(1), re.M)) \
        == set(gen_mod.RESERVED_ENV)


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
                                  "creates": b.creates, "nodeport_ok": b.nodeport_ok}
    # routes/custom-host is the one nobody would guess: OpenShift gates
    # spec.host behind it, and crane sets spec.host.
    assert "routes/custom-host" in backends["openshift"]["resources"]
    # nodeport_ok is served because the UI *decides* with it -- it greys out the
    # download rather than letting generate() refuse after the fact -- so a
    # backend added without it would silently offer a pairing that cannot serve.
    assert {n: b["nodeport_ok"] for n, b in backends.items()} == {
        "nginx": True, "openshift": True, "contour": False, "istio": False}


def test_func_id_choices_come_from_the_account_when_there_is_one(monkeypatch):
    """A location whose funcId the UI never offers can only be created from the
    CLI or the BlazeMeter web app -- which is what a hardcoded copy of the list
    in TypeScript caused, and then what a hardcoded copy in Python caused after
    it. The account is the vocabulary, so a funcId it adds is selectable with no
    edit here and one it retires leaves the form on its own."""
    connect(monkeypatch, FakeClient())
    body = client.get("/api/func-ids?account_id=291446").json()
    ids = [c["id"] for c in body]

    assert {"mockServices", "proxyRecorder", "tdm", "delphix"} <= set(ids)
    # Retired: the account stopped serving either, so neither is offered --
    # without a rule here naming them.
    assert "functionalApi" not in ids and "sv-bridge" not in ids
    assert all(c["label"] for c in body)


def test_the_vocabulary_is_reachable_with_no_account_at_all():
    """The page asks on mount, before a key exists; manual entry never has an
    account. `account_id` is therefore optional, and the answer with none is the
    three funcIds this tool covers, under the names the account would give
    them."""
    body = client.get("/api/func-ids").json()
    assert [(c["id"], c["label"], c["covered"]) for c in body] == [
        ("performance", "Performance", True),
        ("functionalGui", "GUI Functional", True),
        ("mockServices", "Service Virtualization", True)]


def test_an_unnamed_func_id_is_still_offered_under_its_raw_id(monkeypatch):
    """The display name is the account's, so a funcId it serves without one must
    still appear -- under the raw id, which is what a location carrying it shows
    anyway. Dropping it would hide the functionality exactly like the hardcoded
    list did."""
    class Unnamed(FakeClient):
        def functionalities(self, account_id):
            return {"functionalities": [{"funcId": "brandNew", "size": 1}]}

    connect(monkeypatch, Unnamed())
    body = client.get("/api/func-ids?account_id=291447").json()
    assert [(r["id"], r["label"], r["covered"]) for r in body] == [
        ("brandNew", "brandNew", False)]


def test_reading_the_vocabulary_is_not_a_write(monkeypatch):
    """It is account-scoped, so it is cached with the other account reads -- and
    it is a read, so it must not carry `_writes`, which drops that cache. A
    vocabulary that dropped the cache would re-fetch the account's locations
    every time the page reconnected."""
    fake = connect(monkeypatch, FakeClient())
    server._cache.clear()
    client.get("/api/func-ids?account_id=291446")
    client.get("/api/func-ids?account_id=291446")
    assert [c for c in fake.calls if c[0] == "functionalities"] == [
        ("functionalities", 291446)]


def test_functionalities_are_served_with_a_label_and_a_suggested_namespace():
    """The configure step shows one functionality's options at a time and offers
    this list. Served rather than written in TypeScript for the same reason as
    the funcId choices: functional testing, secrets and API monitoring are
    expected to follow, and a functionality has to become selectable by being
    added here."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/functionalities").json()
    assert [f["id"] for f in body] == [f["id"] for f in core.FUNCTIONALITIES]
    assert body[0]["id"] == "performance"       # the common case is the default
    for f in body:
        assert f["label"] and f["namespace"]
    # The id *is* the funcId (#149), so the join a location makes is on it and
    # there is no second list to keep: which funcId means service
    # virtualization is generate.SV_FUNC_IDS', the same answer
    # /api/sv-constants serves and _sv_cfg validates against.
    assert [f["id"] for f in body if f["id"] in gen_mod.SV_FUNC_IDS] \
        == list(gen_mod.SV_FUNC_IDS)
    # Distinct namespaces are the point of suggesting one per functionality:
    # sharing a namespace is what makes redeploying one agent take the other's
    # pods down.
    assert len({f["namespace"] for f in body}) == len(body)


def test_a_functionality_added_to_the_vocabulary_is_offered(monkeypatch):
    """The end-to-end shape of adding a functionality: one entry here, plus a
    tag on whichever option groups it owns. Nothing in the frontend enumerates
    functionalities, so this is the whole of the backend half."""
    monkeypatch.setattr(core, "FUNCTIONALITIES", core.FUNCTIONALITIES + [
        {"id": "secretsPrivateVault", "label": "Secrets Private Vault",
         "hint": "secrets from a vault", "namespace": "blazemeter-vault"}])
    body = client.get("/api/functionalities").json()
    assert body[-1] == {"id": "secretsPrivateVault",
                        "label": "Secrets Private Vault",
                        "hint": "secrets from a vault",
                        "namespace": "blazemeter-vault"}
    # ...and it is a covered funcId by the same act, because `covered` is that
    # list read as a vocabulary rather than a second table beside it.
    assert next(r for r in client.get("/api/func-ids").json()
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


def test_a_location_a_test_cannot_start_on_says_so(monkeypatch):
    """The warning came back from the terminal only, so the page could create a
    location that 403s every start and show nothing about it. core decides it
    now; the field rides beside the location document rather than nesting it,
    because the page reads `id` off this response to select what it just made.
    """
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
    """BZM_API_KEY_FILE used to be read into a module-level list at import, so
    a value set afterwards was invisible -- and `ui --dev` sets exactly that for
    its reloader subprocess. Asserted here as well as in test_core because this
    is the route that answers the question."""
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    monkeypatch.setenv("BZM_API_KEY_FILE", str(key))
    body = client.get("/api/key/detect").json()
    assert {"path": str(key), "key_id": "KID"} in body["candidates"]
    # The id identifies the key; the secret is what must never come back.
    assert "s" not in [c.get("secret") for c in body["candidates"]]


# The routes that carried an argued paragraph before the prose moved to core.
# Each now points /api/docs at core's docstring instead of keeping a copy;
# what this list guards is that they still point at something.
DOCUMENTED_ROUTES = [
    ("get", "/api/status"), ("post", "/api/facts/manual"),
    ("post", "/api/plan"),
    ("get", "/api/sv-mocks"),
    ("get", "/api/sv-check"), ("get", "/api/option-defaults"),
    ("get", "/api/option-docs"), ("get", "/api/func-ids"),
    ("get", "/api/functionalities"), ("get", "/api/sv-constants"),
    ("get", "/api/docker-ignored"), ("get", "/api/reserved-env"),
]


def test_the_routes_that_explained_themselves_still_do():
    """These answers need prose -- what an empty sv-mocks list means, what a
    manual fact set is for -- and it lives in core now. A route that
    stops pointing at it empties its own /api/docs entry, which is exactly
    where nobody would notice."""
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
# The one thing this server does that is not the BlazeMeter API. It is optional
# everywhere: the UI is API-only by design and most people running it have no
# kubecontext, so each way the read can fail comes back 200 with the reason
# rather than an error the browser can only print.

import json  # noqa: E402

from bzm_opl_gen import livetest  # noqa: E402
# The faked kubectl lives with the other cluster-reading tests; reused rather
# than re-declared so both layers exercise the same stand-in binary.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402

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


def test_no_cluster_access_leaves_the_rest_of_the_api_working(fake_cluster):
    """The rule the cluster-reading routes must not break: nothing else may
    start depending on a reachable cluster. With no kubectl on the machine at
    all, every other route answers exactly as it does with one."""
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
    """Widening the bind is a real exposure, and the one irreversible thing
    behind it is the download button: fetching an AUTH_TOKEN rotates it, so a
    stray click leaves a running agent stuck on a token that no longer works.
    Say so at startup, where it is still cheap to reconsider."""
    assert _served(monkeypatch, host="0.0.0.0")["host"] == "0.0.0.0"
    warning = capsys.readouterr().out
    assert "reachable" in warning and "AUTH_TOKEN" in warning


def test_a_bad_api_key_flag_does_not_stop_the_server_starting(monkeypatch,
                                                              tmp_path, capsys):
    """#91's start-up half. `--api-key` pointing at an unparseable file used to
    SystemExit out of main() before uvicorn was ever reached. The page this
    serves has a connect form on it, so opening unconnected with the reason on
    stdout is a better answer than not opening."""
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
    """--dev reloads through an import string, a second uvicorn.run call that
    used to hardcode its own host -- an easy place for the flag to go missing."""
    assert _served(monkeypatch, host="0.0.0.0", dev=True)["host"] == "0.0.0.0"


# -- the deployed virtual services, alongside the heartbeat --------------------
# The agent reports idle whether or not its virtual services ever became
# reachable, so a stall is invisible in the watch panel. This answers what is
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
    assert calls[0]["timeout"] == core.SV_CHECK_TIMEOUT_S
    assert 0 < core.SV_CHECK_TIMEOUT_S < 10


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


def test_group_tags_name_functionalities_the_server_actually_serves():
    """The frontend tags each option group with the functionality ids it belongs
    to, and those ids are the join between the two halves. Nothing else checks
    it: the vitest suite tags against its own fixture, so renaming a served id
    passes both suites green and silently empties a functionality's options in
    the browser. Read the tags out of the source rather than duplicating them."""
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
    # sv.ts keys one answer by functionality id rather than by group id -- which
    # format cannot serve the functionality at all -- so that literal is the
    # same join and fails the same way: the card would render its switches on a
    # bundle that cannot carry them, and nothing would say so.
    sv_src = os.path.join(os.path.dirname(src), "sv.ts")
    with open(sv_src) as fh:
        found = re.search(r'const SV_FUNCTIONALITY = "([^"]+)"', fh.read())
    assert found, "SV_FUNCTIONALITY not found -- was it renamed or moved?"
    assert found.group(1) in served


# -- saving a bundle to disk ---------------------------------------------------
# The zip is for handing a bundle to somebody; /api/generate/save writes the
# same files where livetest and an MCP session can pick them up.

def test_generate_save_writes_the_bundle_where_asked(tmp_path):
    out = str(tmp_path / "bundle")
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": out})
    assert r.status_code == 200
    body = r.json()
    assert body["out_dir"] == out
    names = [f["name"] for f in body["files"]]
    # profile.json is the handoff: livetest re-renders from it, and an MCP
    # session reads it to see what this bundle was configured as.
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
    """The reuse branch, which this route only reaches because it passes the
    directory it is about to write. Saving again -- to re-render with one option
    changed, which is what the folder handoff is for -- must leave the agent
    running from the last save alone, and produce the same bytes."""
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
    """The loud half of the same branch, and it is a refusal rather than a
    placeholder: saving into that folder would *overwrite* the other agent's
    bundle, and no API reads an AUTH_TOKEN back, so that folder was the only copy
    of it outside a running cluster. Refused, and the file is intact after."""
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
    """Every optional field arrives as "" from an untouched number input, and
    that has to mean 'not given' rather than a number that will not parse --
    otherwise the panel refuses the very first thing anyone types."""
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
    # Not a route argument at all, so the body is simply ignored by the model --
    # what matters is that nothing was written.
    assert r.status_code == 200
    assert r.json()["changed"] == {}


def test_location_settings_needs_a_key(monkeypatch):
    monkeypatch.setitem(server._state, "client", None)
    r = client.post("/api/locations/settings",
                    json={"harbor_id": "h1", "slots": 3})
    assert r.status_code == 401


# -- the account tree, remembered for a minute --------------------------------
# A page load is four round trips to BlazeMeter and reloading is what you do all
# day while configuring. What these defend is not the speed -- it is that the
# cache cannot outlive a change this server made itself.

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


def test_every_write_route_drops_the_cache():
    """The rule is the decorator's, not each route's memory of it.

    `/api/ships/token` is the one that had forgotten -- which is why this
    asserts over the app's own routes rather than over a list written here.
    Anything that POSTs to a customer's account and leaves a read cached is
    a page showing what the account held before the click.
    """
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
    """The word on the wire is `functionality`, which is BlazeMeter's own.

    Asserted over the app's own routes and their own answers rather than over a
    list written here, for the reason `test_every_write_route_drops_the_cache`
    gives: the surface a customer's browser and an MCP session read is the one
    that has to hold to it, and a route added later is exactly the one nobody
    would think to add to a list. Scoped to the API and nothing else -- comments
    and docs say `feature` about plenty of things that are not this one, and
    a rule that reached prose would be a rule about English.

    Every parameterless GET is called for its body; the rest answer 422 or 401,
    which is a body too and is checked the same way. `/api/docs` is the same
    vocabulary once more, from FastAPI's side.
    """
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
    # Not empty: a walk that reached nothing would pass this silently, which is
    # the shape a changed route registry leaves behind.
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
