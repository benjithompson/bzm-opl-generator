"""The orchestration layer, driven directly rather than over HTTP.

This module imports no fastapi and skips nothing. tests/test_server.py covers
the same behaviour through the HTTP layer; these pin the decisions, those the
status codes."""

import ast
import base64
import inspect
import io
import json
import os
import time
import zipfile

import pytest
import yaml

from bzm_opl_gen import api, core, generate as gen, kube
from test_generate import FACTS


# What a real account answers to GET /accounts/{id}/functionalities, trimmed.
# `functionalApi` is absent (retired). `functionalGui` carries three of its
# browser pins, which are parameters of that funcId, not funcIds of their own.
ACCOUNT_FUNCTIONALITIES = {
    "additionalSpace": 50,
    "functionalities": [
        {"funcId": "performance", "size": 5, "displayName": "Performance"},
        {"funcId": "proxyRecorder", "size": 1, "displayName": "Proxy Recorder"},
        {"funcId": "secretsPrivateVault", "size": 1,
         "displayName": "Secrets Private Vault"},
        {"funcId": "enableSecretsToggle", "size": 1,
         "displayName": "Vault Access Controls"},
        {"funcId": "mockServices", "size": 1,
         "displayName": "Service Virtualization"},
        {"funcId": "functionalGui", "size": 0, "displayName": "GUI Functional",
         "subFunctionalities": [
             {"id": "chrome:default", "size": 2, "displayName": "Chrome Default",
              "default": True},
             {"id": "firefox:139", "size": 2, "displayName": "Firefox 139"},
             {"id": "safari:15", "size": 2, "displayName": "Safari 15"},
         ]},
        {"funcId": "tdm", "size": 1, "displayName": "TDM Integration"},
        {"funcId": "dataPublisher", "size": 1, "displayName": "Data Orchestration"},
        {"funcId": "delphix", "size": 1, "displayName": "Delphix Integration"},
    ],
}


class FakeClient:
    """Enough BzmClient to exercise core. Shared with tests/test_mcp.py so both
    suites agree on what an account looks like."""

    def __init__(self, token="TOKEN-FROM-API", harbor=None, locations=None,
                 ignores=(), versions=None):
        self._token = token
        # GET .../versions: a recorded payload, or an exception to raise (the
        # default, so the image list decides nothing unless a test asks).
        self._versions = versions if versions is not None else api.BzmApiError(
            "GET /private-locations/h/ships/s/versions -> HTTP 404: not found")
        self._harbor = harbor if harbor is not None else {}
        self._locations = locations
        self._workspaces = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}]
        # Fields this account accepts on a PATCH and does not store, as the real
        # POST does with threadsPerEngine.
        self._ignores = set(ignores)
        self.calls = []

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        return self._token

    def private_location(self, harbor_id):
        self.calls.append(("private_location", harbor_id))
        return self._harbor

    def ship_versions(self, harbor_id, ship_id):
        self.calls.append(("ship_versions", harbor_id, ship_id))
        if isinstance(self._versions, Exception):
            raise self._versions
        return self._versions

    def user(self):
        return {"email": "se@example.com", "displayName": "SE",
                "defaultProject": {"accountId": 7}}

    def workspaces(self, account_id):
        self.calls.append(("workspaces", account_id))
        return self._workspaces

    def functionalities(self, account_id):
        self.calls.append(("functionalities", account_id))
        return ACCOUNT_FUNCTIONALITIES

    def private_locations(self, account_id=None, workspace_id=None):
        self.calls.append(("private_locations", account_id, workspace_id))
        if self._locations is not None:
            return self._locations
        return [{"id": "h1", "name": "loc", "slots": 2,
                 "funcIds": ["performance"],
                 "ships": [{"id": "s1", "name": "agent1", "state": "idle"}]}]

    def create_ship(self, harbor_id, name):
        return {"id": "s2", "name": name}

    def create_private_location(self, name, account_id, workspace_ids,
                                func_ids=("performance",), slots=1,
                                threads_per_engine=None):
        self.calls.append(("create_private_location", name, account_id))
        # A fresh harbor has no ships, which is why create_ship exists.
        return {"id": "h9", "name": name, "slots": slots,
                "funcIds": list(func_ids), "ships": []}

    def delete_private_location(self, harbor_id):
        self.calls.append(("delete", harbor_id))

    def update_private_location(self, harbor_id, slots=None,
                                threads_per_engine=None,
                                override_cpu=None, override_memory=None):
        self.calls.append(("update_private_location", harbor_id))
        # The write lands on the harbor this fake hands back, so a re-read sees it.
        sent = {"slots": slots, "threadsPerEngine": threads_per_engine,
                "overrideCPU": override_cpu, "overrideMemory": override_memory}
        for field, value in sent.items():
            if value is not None and field not in self._ignores:
                self._harbor[field] = value
        return dict(self._harbor)


# -- nothing here turns a functionality on for a location ---------------------


def test_the_client_cannot_replace_a_location_s_functionalities():
    """BlazeMeter's PATCH replaces `funcIds` wholesale, so the client does not
    accept them."""
    assert "func_ids" not in inspect.signature(
        api.BzmClient.update_private_location).parameters
    assert not hasattr(core, "add_func_id")


# -- creating one, and whether it can start a test -----------------------------

class _RunnableClient(FakeClient):
    """An account that stores threadsPerEngine, as the PATCH after the POST does."""

    def create_private_location(self, name, account_id, workspace_ids,
                                func_ids=("performance",), slots=1,
                                threads_per_engine=None):
        return dict(super().create_private_location(
            name, account_id, workspace_ids, func_ids=func_ids, slots=slots),
            threadsPerEngine=threads_per_engine)


def test_a_location_that_cannot_start_a_test_says_so_when_it_is_created():
    """A location missing threadsPerEngine is reported unrunnable when created."""
    made = core.create_location(FakeClient(), "loc", 7, 2, slots=2)
    assert made["location"]["id"] == "h9"
    assert made["runnable"] is False
    assert "403" in made["warning"]
    assert "Not enough available resources" in made["warning"]


def test_a_runnable_location_carries_no_warning():
    made = core.create_location(_RunnableClient(), "loc", 7, 2, slots=2,
                                threads_per_engine=500)
    assert made["runnable"] is True and made["warning"] is None


def test_a_location_created_without_slots_is_unrunnable_too():
    """Either field missing is the same 403, so the verdict reads both."""
    made = core.create_location(_RunnableClient(), "loc", 7, 2, slots=0,
                                threads_per_engine=500)
    assert made["runnable"] is False and made["warning"]


# -- the slots a functionality needs before BlazeMeter will make the location --

def test_gui_functional_cannot_be_created_at_the_default_one_slot():
    """The POST 400s, so the refusal is here -- before the write, on every
    surface at once, rather than three renderings of BlazeMeter's error."""
    client = FakeClient()
    with pytest.raises(core.BadRequest) as e:
        core.create_location(client, "loc", 7, 2,
                             func_ids=["performance", "functionalGui"])
    assert "Parallel engine runs must be greater than 1" in str(e.value)
    assert "GUI Functional" in str(e.value)
    # Nothing reached the account.
    assert not [c for c in client.calls if c[0] == "create_private_location"]


def test_the_refusal_says_which_number_to_type():
    """BlazeMeter's own sentence says what is wrong and not what to do about
    it, and "greater than 1" is one reading away from 1.5."""
    with pytest.raises(core.BadRequest) as e:
        core.create_location(FakeClient(), "loc", 7, 2,
                             func_ids=["functionalGui"], slots=1)
    assert "slots=2" in str(e.value)


def test_gui_functional_is_created_at_two_slots():
    """Verified live: the same funcIds that 400 at 1 succeed at 2. So the
    minimum is a minimum and not a ban."""
    made = core.create_location(_RunnableClient(), "loc", 7, 2,
                                func_ids=["functionalGui"], slots=2,
                                threads_per_engine=500)
    assert made["location"]["slots"] == 2


def test_a_location_without_gui_functional_is_still_created_at_one_slot():
    """`slots` is engines per agent and a real cost -- accounts run 17 agents
    at slots=1 -- so the rule reaches exactly the funcId it was found on."""
    made = core.create_location(_RunnableClient(), "loc", 7, 2,
                                func_ids=["performance"], slots=1,
                                threads_per_engine=500)
    assert made["location"]["slots"] == 1


def test_nobody_s_slots_are_raised_for_them():
    """The failure this is not allowed to become: a location that quietly asks
    for twice the concurrency somebody chose."""
    with pytest.raises(core.BadRequest):
        core.create_location(_RunnableClient(), "loc", 7, 2,
                             func_ids=["functionalGui"], slots=1,
                             threads_per_engine=500)


def test_the_minimum_is_a_table_the_page_can_be_told():
    """Served rather than restated, so the form can say it before the account
    does -- the IGNORED_BY_FORMAT rule, one vocabulary along."""
    mins = core.slot_minimums()
    assert mins["functionalGui"]["minimum"] == 2
    assert mins["functionalGui"]["label"] == "GUI Functional"
    assert "Parallel engine runs must be greater than 1" in (
        mins["functionalGui"]["message"])


def test_slots_refusal_is_none_for_what_the_account_would_accept():
    assert core.slots_refusal(["performance"], 1) is None
    assert core.slots_refusal(["functionalGui"], 2) is None
    assert core.slots_refusal(["functionalGui"], 9) is None
    assert core.slots_refusal([], 1) is None


def test_fetch_ship_token_mints_for_the_ship_it_was_given():
    client = FakeClient()
    assert core.fetch_ship_token(client, "h1", "s1") == "TOKEN-FROM-API"
    assert client.calls == [("auth_token", "h1", "s1")]


def test_fetch_ship_token_reports_a_refused_endpoint_as_such():
    """The refusal names the ship and says what to do without the endpoint."""
    with pytest.raises(core.TokenRefused) as e:
        core.fetch_ship_token(RefusingClient(), "h1", "s1")
    assert "could not be issued" in str(e.value)


# -- the pieces every front door shares ----------------------------------------

def test_create_agent_issues_the_new_agent_s_token():
    client = FakeClient()
    made = core.create_agent(client, "h1", "agent1")
    assert made == {"ship": {"id": "s2", "name": "agent1"},
                    "auth_token": "TOKEN-FROM-API", "token_error": None}
    assert client.calls == [("auth_token", "h1", "s2")]


def test_create_agent_reports_a_refused_token_beside_the_agent():
    """The agent exists either way; its id must survive a refused token."""
    made = core.create_agent(RefusingClient(), "h1", "agent1")
    assert made["ship"]["id"] == "s2" and made["auth_token"] is None
    assert "could not be issued" in made["token_error"]


def test_create_agent_can_issue_nothing():
    client = FakeClient()
    made = core.create_agent(client, "h1", "agent1", issue_token=False)
    assert made["auth_token"] is None and made["token_error"] is None
    assert client.calls == []


def test_whoami_names_the_user_and_default_account():
    assert core.whoami(FakeClient()) == {
        "email": "se@example.com", "display_name": "SE",
        "default_account_id": 7}
    assert core.default_account_id(FakeClient()) == 7


def test_a_user_with_no_default_project_has_no_default_account():
    class NoProject(FakeClient):
        def user(self):
            return {"email": "x@example.com"}
    assert core.default_account_id(NoProject()) is None


def test_reporting_counts_keep_unknown_apart_from_not_reporting():
    ships = [{"lastHeartBeat": int(time.time()), "state": "idle"},
             {"lastHeartBeat": 1, "state": "idle"},
             {}]
    assert core.reporting_counts(ships) == {"agents_reporting": 1,
                                            "agents_unknown": 1}
    assert core.reporting_counts([]) == {"agents_reporting": 0,
                                         "agents_unknown": 0}


def test_facts_warnings_name_blank_ids_by_marker():
    said = " ".join(core.facts_warnings(core.manual_facts()["facts"]))
    assert "harbor_id (<HARBOR_ID>) and ship_id (<SHIP_ID>)" in said
    assert "not a legal label value" in said
    assert core.facts_warnings(core.manual_facts("H1", "S1")["facts"]) == []


def test_facts_warnings_name_a_gui_location_with_no_browser_image():
    facts = core.manual_facts("H1", "S1", func_ids=["functionalGui"])["facts"]
    assert any("browser" in w for w in core.facts_warnings(facts))


def test_facts_warnings_name_a_refused_image_list():
    facts = dict(core.manual_facts("H1", "S1")["facts"],
                 image_list={"state": "unread", "count": None,
                             "detail": "HTTP 403"})
    assert any("could not be read" in w and "HTTP 403" in w
               for w in core.facts_warnings(facts))


def test_build_bundle_refuses_a_relative_out_dir_before_minting():
    client = FakeClient()
    with pytest.raises(core.BadRequest, match="absolute"):
        core.build_bundle(FACTS, {"namespace": "ns1"}, client=client,
                          rotate=True, out_dir="rel/dir", write=True)
    assert client.calls == []


def test_build_bundle_writes_and_reports_the_token_source(tmp_path):
    built = core.build_bundle(FACTS, {"namespace": "ns1", "auth_token": "T"},
                              out_dir=str(tmp_path), write=True)
    assert built.token.branch == core.TOKEN_GIVEN
    assert {w["name"] for w in built.written} == set(built.files)
    assert (tmp_path / bundle_names.PROFILE_FILE).exists()


def test_build_bundle_without_write_writes_nothing(tmp_path):
    built = core.build_bundle(FACTS, {"namespace": "ns1"},
                              out_dir=str(tmp_path))
    assert built.written is None and not list(tmp_path.iterdir())
    assert built.token.branch == core.TOKEN_PLACEHOLDER


def test_build_bundle_does_not_mutate_the_caller_s_options():
    opts = {"namespace": "ns1"}
    core.build_bundle(FACTS, opts, client=FakeClient(), rotate=True)
    assert opts == {"namespace": "ns1"}


def test_a_generate_refusal_after_a_rotation_still_reports_the_rotation():
    with pytest.raises(core.BadRequest) as e:
        core.build_bundle(FACTS, {"namespace": "ns1",
                                  "engine_cpu_limit": "not-a-cpu"},
                          client=FakeClient(), rotate=True)
    assert "rotated" in str(e.value)


# -- the split itself ---------------------------------------------------------

def _imports(path):
    """Every top-level name a file imports, read from the parsed source (another
    test module has already loaded fastapi)."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.name for a in node.names)
            if node.module and not node.level:
                names.add(node.module.split(".")[0])
    return names


def test_core_is_transport_free():
    """A web framework imported here would put the whole HTTP stack behind
    every other consumer -- and behind this suite, which then skips."""
    banned = _imports(core.__file__) & {"fastapi", "pydantic", "starlette",
                                        "uvicorn"}
    assert not banned, (
        f"core imports {sorted(banned)} -- the transport belongs in the layer "
        f"above it")


def test_this_suite_is_transport_free_too():
    """This suite imports no `server`, or it would skip without fastapi."""
    assert "server" not in _imports(__file__)


@pytest.mark.parametrize("exc,status", [
    (core.BadRequest, 400),
    (core.NotFound, 404),
    (core.UpstreamError, 502),
])
def test_errors_carry_the_status_the_web_layer_answers_with(exc, status):
    """The status belongs to the error, so every transport answers it the same."""
    e = exc("nope")
    assert isinstance(e, core.CoreError) and e.status == status
    assert str(e) == "nope"


def test_an_unclassified_failure_does_not_blame_the_caller():
    """A subclass naming no status reports a bug (500), not bad input."""
    assert core.CoreError.status == 500
    assert all(e.status != core.CoreError.status
               for e in (core.BadRequest, core.NotFound, core.UpstreamError))


def test_a_deleted_location_is_not_the_same_failure_as_an_unreachable_one():
    """404 becomes NotFound; an unreachable BlazeMeter stays an UpstreamError."""
    def deleted():
        raise api.BzmApiError("GET /private-locations/h1 -> HTTP 404: gone",
                              status=404)
    with pytest.raises(core.NotFound) as caught:
        core._upstream(deleted)
    assert "404" in str(caught.value)


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, None])
def test_only_404_says_the_thing_asked_for_is_gone(status):
    """Only 404 says the thing asked for is gone; 401, 403, 5xx and status-less
    failures do not."""
    def refuse():
        raise api.BzmApiError("nope", status=status)
    with pytest.raises(core.UpstreamError) as caught:
        core._upstream(refuse)
    assert not isinstance(caught.value, core.NotFound)
    assert caught.value.status == 502


# -- where a bundle's AUTH_TOKEN comes from -----------------------------------
# Four branches and only one mints; minting revokes the running agent's token.

def _bundle(tmp_path, **opts):
    """A written bundle, as a predecessor for the reuse branch to read back."""
    files = gen.generate(dict(FACTS), {"namespace": "ns1", **opts})
    gen.write(files, str(tmp_path))
    return files


def test_generate_mints_nothing_by_default_even_holding_a_key():
    """Holding a client is not permission to rotate."""
    c = FakeClient()
    files = core.generate_bundle(FACTS, {"namespace": "ns1"}, client=c)
    assert c.calls == []
    assert bundle_options.DEFAULT_OPTIONS["auth_token"] in files["bzm_secret.yaml"]


def test_a_token_in_the_options_wins_outright_and_a_rotation_is_not_silent():
    """A supplied token wins over rotate, and the dropped rotation is said."""
    c = FakeClient()
    opts = {"namespace": "ns1", "auth_token": "MINE"}
    src = core.resolve_auth_token(FACTS, opts, client=c, rotate=True)
    assert src.branch == core.TOKEN_GIVEN
    assert opts["auth_token"] == "MINE" and c.calls == []
    assert "--rotate-token was NOT acted on" in src.message
    assert "--rotate-token" not in core.resolve_auth_token(
        FACTS, dict(opts)).message


def test_rotating_mints_and_names_the_ship_it_was_for():
    """`rotate=True` calls the endpoint and names the ship it rotated."""
    c = FakeClient()
    opts = {"namespace": "ns1"}
    src = core.resolve_auth_token(FACTS, opts, client=c, rotate=True)
    assert c.calls == [("auth_token", "aaa111", "bbb222")]
    assert (src.branch, src.ship_id) == (core.TOKEN_ROTATED, "bbb222")
    assert "bbb222" in src.message
    assert opts["auth_token"] == "TOKEN-FROM-API"


def test_rotating_warns_before_it_acts_not_after():
    """The rotation warning is announced before the mint, not after."""
    said = []

    class Announcing(FakeClient):
        def auth_token(self, harbor_id, ship_id):
            said.append("MINTED")
            return super().auth_token(harbor_id, ship_id)

    core.resolve_auth_token(FACTS, {"namespace": "ns1"}, client=Announcing(),
                            rotate=True, announce=said.append)
    assert said[0] == core.rotation_warning("bbb222")
    assert said[1] == "MINTED"
    assert "0/1" in said[0] and "re-appl" in said[0]


def test_rotating_without_a_credential_says_which_one_it_needs():
    with pytest.raises(core.BadRequest) as caught:
        core.resolve_auth_token(FACTS, {"namespace": "ns1"}, rotate=True)
    assert "--api-key" in str(caught.value)


def test_rotating_never_guesses_between_two_ships():
    """Rotating the wrong one revokes the credential of an agent nobody
    mentioned, and the mistake is invisible until that pod is looked at."""
    c = FakeClient()
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                               dict(FACTS["ships"][0], id="b2")])
    with pytest.raises(core.BadRequest) as caught:
        core.resolve_auth_token(facts, {"namespace": "ns1"}, client=c,
                                rotate=True)
    assert "b1" in str(caught.value) and "b2" in str(caught.value)
    assert c.calls == []


def test_rotating_uses_the_ship_it_was_told_about():
    c = FakeClient()
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0]),
                               dict(FACTS["ships"][0], id="ccc333")])
    core.resolve_auth_token(facts, {"namespace": "ns1", "ship_id": "ccc333"},
                            client=c, rotate=True)
    assert c.calls == [("auth_token", "aaa111", "ccc333")]


def test_the_token_already_in_the_output_directory_is_reused(tmp_path):
    """A bundle in out_dir for the same ship lends its token (reused branch)."""
    _bundle(tmp_path, auth_token="TOKENVALUE")
    c = FakeClient()
    opts = {"namespace": "ns1"}
    src = core.resolve_auth_token(FACTS, opts, client=c, out_dir=str(tmp_path))
    assert (src.branch, src.ship_id) == (core.TOKEN_REUSED, "bbb222")
    assert opts["auth_token"] == "TOKENVALUE" and c.calls == []


def test_regenerating_a_bundle_twice_is_byte_identical(tmp_path):
    """Regenerating into the same directory reuses its token, so the bundle is
    byte-identical, profile.json included."""
    first = _bundle(tmp_path, auth_token="TOKENVALUE")
    opts = {"namespace": "ns1"}
    core.resolve_auth_token(FACTS, opts, client=FakeClient(),
                            out_dir=str(tmp_path))
    second = core.generate_bundle(FACTS, opts, out_dir=str(tmp_path))
    assert second == first


def test_a_bundle_for_another_ship_is_refused_rather_than_overwritten(tmp_path):
    """A directory holding another ship's bundle is refused and left intact."""
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                               dict(FACTS["ships"][0], id="b2")])
    gen.write(gen.generate(facts, {"namespace": "ns1", "ship_id": "b1",
                                   "auth_token": "B1TOKEN"}), str(tmp_path))
    opts = {"namespace": "ns1", "ship_id": "b2"}
    with pytest.raises(core.BadRequest) as caught:
        core.resolve_auth_token(facts, opts, out_dir=str(tmp_path))
    assert "b1" in str(caught.value) and "b2" in str(caught.value)
    assert "B1TOKEN" in (tmp_path / "bzm_secret.yaml").read_text(), \
        "the refusal must not have destroyed the token it was protecting"


def test_saying_what_this_bundle_s_token_is_makes_the_overwrite_deliberate(
        tmp_path):
    """A supplied token never reads the directory, so replacing another ship's
    bundle stays possible deliberately."""
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                               dict(FACTS["ships"][0], id="b2")])
    gen.write(gen.generate(facts, {"namespace": "ns1", "ship_id": "b1",
                                   "auth_token": "B1TOKEN"}), str(tmp_path))
    opts = {"namespace": "ns1", "ship_id": "b2", "auth_token": "B2TOKEN"}
    src = core.resolve_auth_token(facts, opts, out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_GIVEN and opts["auth_token"] == "B2TOKEN"


def test_the_refusal_never_names_a_ship_called_None(tmp_path):
    """With several agents and no ship named, the refusal says so rather than
    naming a ship "None"."""
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                               dict(FACTS["ships"][0], id="b2")])
    gen.write(gen.generate(facts, {"namespace": "ns1", "ship_id": "b1",
                                   "auth_token": "B1TOKEN"}), str(tmp_path))
    with pytest.raises(core.BadRequest) as caught:
        core.resolve_auth_token(facts, {"namespace": "ns1"},
                               out_dir=str(tmp_path))
    said = str(caught.value)
    assert "None" not in said, said
    assert "ship_id" in said, "the remedy is to say which ship"


def test_a_bundle_whose_ship_cannot_be_confirmed_is_refused_too(tmp_path):
    """A bundle whose ship cannot be confirmed is refused, with the remedy of
    passing its token."""
    _bundle(tmp_path, auth_token="TOKENVALUE")
    os.remove(os.path.join(str(tmp_path), bundle_names.PROFILE_FILE))
    opts = {"namespace": "ns1"}
    with pytest.raises(core.BadRequest) as caught:
        core.resolve_auth_token(FACTS, opts, out_dir=str(tmp_path))
    assert bundle_names.PROFILE_FILE in str(caught.value)
    assert "auth_token" not in opts
    assert "TOKENVALUE" in (tmp_path / "bzm_secret.yaml").read_text()


def test_an_empty_directory_is_not_a_bundle_to_protect(tmp_path):
    """A directory with no token in it is the ordinary placeholder case."""
    src = core.resolve_auth_token(FACTS, {"namespace": "ns1"},
                                  out_dir=str(tmp_path / "nothing-here"))
    assert src.branch == core.TOKEN_PLACEHOLDER


def test_no_token_anywhere_says_where_a_real_one_comes_from(tmp_path):
    """The placeholder message names where a real token comes from; the kubectl
    is named, never run."""
    src = core.resolve_auth_token(FACTS, {"namespace": "ns1"},
                                  out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_PLACEHOLDER
    assert "kubectl -n ns1 get secret" in src.message
    assert "base64 -d" in src.message


def test_the_placeholder_message_reads_on_every_surface_that_shows_it():
    """The placeholder message names the option and both the CLI and page spellings."""
    msg = core.token_recovery_hint({"namespace": "ns1"})
    assert "auth_token" in msg, "the option itself, which every surface has"
    assert "--auth-token" in msg, "the command line"
    assert "field" in msg.lower(), "the page"


def test_one_sentence_names_every_place_a_token_can_be_got_from():
    """One sentence names every source of a real token."""
    msg = core.token_recovery_hint({"namespace": "ns1"})
    assert "create-agent" in msg, "what was printed when the agent was made"
    assert "Private Locations" in msg, "the BlazeMeter UI's install command"
    assert "kubectl -n ns1 get secret" in msg, "an agent already deployed"


def test_a_refused_endpoint_says_where_else_a_token_lives():
    """A refused endpoint also names the running agent as a token source."""
    c = RefusingClient()
    with pytest.raises(core.TokenRefused) as caught:
        core.fetch_ship_token(c, "h1", "s1")
    assert "Private Locations" in str(caught.value)
    assert "get secret" in str(caught.value)


def test_the_placeholder_branch_needs_no_output_directory():
    """The MCP and UI callers generate before they have anywhere to write."""
    assert (core.resolve_auth_token(FACTS, {"namespace": "ns1"}).branch
            == core.TOKEN_PLACEHOLDER)


def test_generate_needs_no_client_at_all():
    files = core.generate_bundle(FACTS, {"namespace": "ns1"}, client=None)
    assert "bzm_deployment.yaml" in files


def test_generate_refuses_options_it_cannot_render():
    """A value it cannot make sense of is still a BadRequest. A value nobody
    supplied is not one -- see below."""
    with pytest.raises(core.BadRequest):
        core.generate_bundle(FACTS, {"engine_cpu_limit": "not-a-cpu"},
                             client=None)


def test_generate_marks_a_blank_field_rather_than_refusing_it():
    """A blank required field becomes its marker rather than a refusal."""
    files = core.generate_bundle(FACTS, {"service_account_name": ""},
                                 client=None)
    assert "not finished" in files["README.md"]
    assert "service_account_name" in files["README.md"]


# -- a credential the account will not issue ----------------------------------

# A restricted account: the token endpoint answers only BlazeMeter's gateway.
TOKEN_403 = ('POST /private-locations/aaa111/ships/bbb222/docker-command -> '
             'HTTP 403: {"error": {"code": 403, "message": "Forbidden: Should '
             'access from Private-Data gateway"}}')


class RefusingClient(FakeClient):
    """An account whose token endpoint is closed. Shared with tests/test_cli.py,
    which drives the other surviving caller of the fetch."""

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        raise api.BzmApiError(TOKEN_403)


# A key BlazeMeter no longer accepts; every surface must report it as a sentence.
EXPIRED_401 = ('GET /user -> HTTP 401: {"error": {"code": 401, "message": '
               '"Unauthorized: invalid API key"}}')


class ExpiredClient(FakeClient):
    """An account that answers 401 to everything. Shared with tests/test_cli.py."""

    def _refuse(self, *a, **kw):
        raise api.BzmApiError(EXPIRED_401)

    user = accounts = workspaces = functionalities = _refuse
    private_locations = private_location = _refuse
    create_private_location = update_private_location = _refuse
    delete_private_location = create_ship = auth_token = _refuse


# Every path that still reaches the token endpoint: the refusal must arrive
# whole at each caller.
REFUSED_CALLS = {
    "rotate_auth_token":
        lambda c: core.rotate_auth_token(c, FACTS, {"namespace": "ns1"}),
    "resolve_auth_token":
        lambda c: core.resolve_auth_token(FACTS, {"namespace": "ns1"},
                                          client=c, rotate=True),
    "generate_bundle":
        lambda c: core.generate_bundle(FACTS, {"namespace": "ns1"}, client=c,
                                       rotate_token=True),
    "reveal_token":
        lambda c: core.reveal_token(c, "aaa111", "bbb222"),
}


def _refusal(name):
    with pytest.raises(core.CoreError) as caught:
        REFUSED_CALLS[name](RefusingClient())
    return caught.value


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_names_the_ship_and_what_failed(name):
    """The refusal names the ship and says it was the credential fetch that failed."""
    msg = str(_refusal(name))
    assert "bbb222" in msg              # the ship, which the body never names
    assert "AUTH_TOKEN" in msg
    assert "could not be issued" in msg


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_says_the_token_can_be_supplied_instead(name):
    """The refusal says the token can be supplied instead."""
    msg = str(_refusal(name))
    assert "auth_token" in msg and "--auth-token" in msg
    assert "BlazeMeter UI" in msg


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_keeps_the_upstream_reason(name):
    """The upstream reason stays in the message and on `.upstream`."""
    e = _refusal(name)
    assert e.upstream == TOKEN_403
    assert TOKEN_403 in str(e)
    assert str(e) != TOKEN_403          # ...but is not the whole message


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_is_not_a_malformed_request(name):
    """An upstream refusal, so 502: nothing the caller sent was wrong, and a 400
    would have the web UI and the MCP session both blame the person asking."""
    e = _refusal(name)
    assert isinstance(e, core.UpstreamError)
    assert e.status == 502
    assert not isinstance(e, core.BadRequest)


def test_mirroring_by_hand_pushes_where_the_bundle_will_look():
    """`mirror_images` pushes to the names a Kubernetes bundle's IMAGE_OVERRIDES
    map to (crane keeps its short form)."""
    reg = "reg.local/bzm"
    pushed = {c.split()[-1] for c in core.mirror_images(
        core.bundle_images(FACTS), mirror=reg, dry_run=True)["commands"]
        if " push " in c}
    files = gen.generate(FACTS, {"namespace": "ns1", "private_registry": reg})
    overrides = json.loads(yaml.safe_load(
        files["bzm_configmap.yaml"])["data"]["IMAGE_OVERRIDES"])
    assert pushed == set(overrides.values()) | {image_registry.crane_image(
        FACTS, {"private_registry": reg})}


# -- the zip ------------------------------------------------------------------

def test_zip_keeps_the_mirror_script_executable():
    files = core.generate_bundle(
        FACTS, {"namespace": "ns1", "private_registry": "reg.local/bzm"},
        client=None)
    z = zipfile.ZipFile(io.BytesIO(core.zip_bundle(files, "bzm-opl-ns1")))
    info = z.getinfo("bzm-opl-ns1/bzm-opl-image-mirror.sh")
    assert info.external_attr >> 16 & 0o111


def test_zip_keeps_the_chart_directory():
    """Names carry directories in the helm format, and a flattened archive is
    a pile of files no helm command can install."""
    files = core.generate_bundle(
        FACTS, {"namespace": "ns1", "output_format": "helm"}, client=None)
    names = zipfile.ZipFile(
        io.BytesIO(core.zip_bundle(files, "bzm-opl-ns1"))).namelist()
    assert "bzm-opl-ns1/helm/templates/deployment.yaml" in names


def test_zip_filename_names_the_namespace():
    assert core.zip_filename({"namespace": "ns1"}) == "bzm-opl-ns1.zip"
    assert core.zip_filename({}) == "bzm-opl-blazemeter.zip"


def test_zip_extracts_to_the_directory_the_archive_is_named():
    """The archive and the directory it extracts to share one name."""
    files = core.generate_bundle(FACTS, {"namespace": "ns1"}, client=None)
    stem = core.zip_stem({"namespace": "ns1"})
    assert core.zip_filename({"namespace": "ns1"}) == stem + ".zip"
    roots = {n.split("/")[0] for n in zipfile.ZipFile(
        io.BytesIO(core.zip_bundle(files, stem))).namelist()}
    assert roots == {stem}


def test_zip_stem_survives_a_blank_and_a_placeholder_namespace():
    """A blank gave `bzm-opl-.zip`; the placeholder marker's angle brackets are
    a directory no Windows extractor will write."""
    assert core.zip_stem({"namespace": ""}) == "bzm-opl-blazemeter"
    assert core.zip_stem({"namespace": "<NAMESPACE>"}) == "bzm-opl-NAMESPACE"


# -- which ship an operation is about -----------------------------------------

def _with_ships(*ids):
    return dict(FACTS, ships=[dict(FACTS["ships"][0], id=s) for s in ids])


@pytest.mark.parametrize("ids,explicit,expect", [
    (("bbb222",), None, "bbb222"),           # the only ship
    (("b1", "b2"), None, None),              # no right answer -- say which
    (("b1", "b2"), "b2", "b2"),              # named explicitly
    ((), None, None),                        # manual facts carry no ships
])
def test_which_ship_an_operation_is_about(ids, explicit, expect):
    assert core.sole_ship_id(_with_ships(*ids), explicit) == expect


def test_a_second_ship_is_never_resolved_by_position():
    """The failure this prevents acts on an agent nobody mentioned -- fetching
    its token rotates it, and the running agent starts logging 404."""
    assert core.sole_ship_id(_with_ships("b1", "b2")) is None


@pytest.mark.parametrize("options,ids,expect", [
    ({}, ("bbb222",), "bbb222"),
    ({}, ("b1", "b2"), None),
    ({"ship_id": "b2"}, ("b1", "b2"), "b2"),
    ({"auth_token": "MINE"}, ("bbb222",), None),      # already held; never rotate
])
def test_which_ship_a_token_would_be_fetched_for(options, ids, expect):
    assert core.token_ship_id(_with_ships(*ids), options) == expect


def test_no_caller_still_decides_which_ship_for_itself():
    """No caller picks a ship by position; they ask core.sole_ship_id. Read by
    path, since importing `server` needs fastapi."""
    here = os.path.dirname(os.path.abspath(core.__file__))
    for name in ("cli.py", "server.py"):
        with open(os.path.join(here, name), encoding="utf-8") as fh:
            hits = [ln.strip() for ln in fh if 'ships"][0]' in ln
                    or "ships[0]" in ln]
        assert not hits, (
            f"{name} picks a ship by position rather than asking "
            f"core.sole_ship_id: {hits}")


# -- the agent's heartbeat ----------------------------------------------------

def _harbor(**ship):
    base = {"id": "bbb222", "state": "idle", "installedVersion": "3.7.55"}
    return {"ships": [dict(base, **ship)]}


def test_agent_is_online_on_a_recent_heartbeat():
    import time
    c = FakeClient(harbor=_harbor(lastHeartBeat=time.time() - 5))
    st = core.agent_status(c, "aaa111", "bbb222")
    assert st["online"] is True and st["heartbeat_age_s"] < 10


def test_agent_is_not_online_on_a_stale_heartbeat():
    """An agent that stopped reporting keeps its last state, so the state
    alone would read as healthy indefinitely."""
    import time
    c = FakeClient(harbor=_harbor(lastHeartBeat=time.time() - 3600))
    assert core.agent_status(c, "aaa111", "bbb222")["online"] is False


def test_agent_with_no_heartbeat_has_no_age_rather_than_a_huge_one():
    c = FakeClient(harbor=_harbor(lastHeartBeat=0))
    st = core.agent_status(c, "aaa111", "bbb222")
    assert st["heartbeat_age_s"] is None and st["online"] is False


def test_unknown_ship_is_not_found():
    c = FakeClient(harbor=_harbor(lastHeartBeat=0))
    with pytest.raises(core.NotFound):
        core.agent_status(c, "aaa111", "nope")


# -- preflight ----------------------------------------------------------------

from evidence_fixtures import document as _evidence  # noqa: E402
from test_doctor import FACTS as LOC_FACTS           # noqa: E402
from bzm_opl_gen import (bundle_names, bundle_options, image_registry)  # noqa: E402


def test_preflight_answers_evidence_and_suggestions_together():
    """One file judged against one configuration. Two calls would be two
    answers that can end up describing different configurations."""
    body = core.preflight(LOC_FACTS, {"namespace": "blazemeter"}, _evidence())
    assert body["checks"] and "suggestions" in body
    assert body["evidence"]["namespace"]


def test_preflight_prefers_the_namespace_being_configured():
    doc = _evidence()
    body = core.preflight(LOC_FACTS, {"namespace": "elsewhere"}, doc)
    assert body["namespace"] == "elsewhere"


def test_preflight_falls_back_to_the_namespace_the_file_was_collected_for():
    doc = _evidence()
    body = core.preflight(LOC_FACTS, {}, doc)
    assert body["namespace"] == doc["namespace"]


# preflight_cluster: the half of preflight() that `doctor --cluster-evidence`
# needs on its own.

def test_preflight_cluster_decides_the_same_namespace_preflight_does():
    doc = _evidence()
    for options in ({"namespace": "elsewhere"}, {}):
        _, namespace = core.preflight_cluster(doc, options)
        assert namespace == core.preflight(LOC_FACTS, options, doc)["namespace"]


def test_an_explicitly_asked_for_namespace_wins_over_both():
    """`doctor -n` wins over the options' namespace and the evidence's."""
    doc = _evidence()
    _, namespace = core.preflight_cluster(doc, {"namespace": "elsewhere"},
                                          namespace="asked-for")
    assert namespace == "asked-for"


def test_no_evidence_at_all_is_an_empty_read_rather_than_a_refusal():
    """No evidence is an empty read (a live cluster), not a refusal."""
    imported, namespace = core.preflight_cluster(None, {"namespace": "ns1"})
    assert imported == core.doctor.Evidence(None, None, ())
    assert namespace == "ns1"


def test_preflight_cluster_refuses_a_file_that_is_not_evidence():
    with pytest.raises(core.BadRequest):
        core.preflight_cluster([1, 2, 3], {"namespace": "blazemeter"})


def test_preflight_refuses_a_file_that_is_not_evidence():
    with pytest.raises(core.BadRequest):
        core.preflight(LOC_FACTS, {"namespace": "blazemeter"}, [1, 2, 3])


def test_preflight_reaches_no_cluster(monkeypatch):
    """The file is the cluster read. A preflight that shelled out would be
    answering about the machine serving the page."""
    monkeypatch.setattr(kube, "cli_tool",
                        lambda *a, **k: pytest.fail("preflight ran a cluster CLI"))
    assert core.preflight(LOC_FACTS, {"namespace": "blazemeter"}, _evidence())["checks"]


# -- evidence as the file it is -----------------------------------------------
# A file that could not be read and one that is not evidence are different
# refusals.

def _written(tmp_path, text, name="cluster-evidence.json"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_evidence_may_be_the_path_of_the_file_it_is(tmp_path):
    doc = _evidence()
    assert core.evidence_document(_written(tmp_path, json.dumps(doc))) == doc


def test_evidence_already_in_hand_passes_straight_through():
    doc = _evidence()
    assert core.evidence_document(doc) is doc


def test_a_path_with_no_file_there_is_unread_rather_than_not_evidence(tmp_path):
    """The distinction the whole helper exists for: nothing was read, so
    nothing can be said about whether it was evidence."""
    with pytest.raises(core.EvidenceUnreadable) as e:
        core.evidence_document(str(tmp_path / "nope.json"))
    assert "nope.json" in str(e.value)


def test_a_file_that_is_not_json_is_unread_too(tmp_path):
    with pytest.raises(core.EvidenceUnreadable) as e:
        core.evidence_document(_written(tmp_path, "{not json"))
    assert "JSON" in str(e.value)


def test_a_path_that_is_not_a_file_at_all_is_unread_too(tmp_path):
    """A directory, or a file nothing may open. Neither is "not evidence", and
    neither may arrive here as the IsADirectoryError no caller expected."""
    with pytest.raises(core.EvidenceUnreadable) as e:
        core.evidence_document(str(tmp_path))
    assert str(tmp_path) in str(e.value)


def test_a_file_that_was_read_and_is_not_evidence_is_a_different_refusal(tmp_path):
    """A readable file that is not evidence is a BadRequest, not EvidenceUnreadable."""
    path = _written(tmp_path, json.dumps({"harbor_id": "h1", "images": {}}))
    doc = core.evidence_document(path)             # read, and read fine
    with pytest.raises(core.BadRequest) as e:
        core.preflight(LOC_FACTS, {"namespace": "blazemeter"}, doc)
    assert "schema" in str(e.value)
    assert not isinstance(e.value, core.EvidenceUnreadable)


def test_the_two_evidence_refusals_are_not_each_other():
    """Neither catches the other, so no `except` in any transport can collapse
    "could not read it" into "read it and it was the wrong document"."""
    assert not issubclass(core.EvidenceUnreadable, core.BadRequest)
    assert not issubclass(core.BadRequest, core.EvidenceUnreadable)
    assert issubclass(core.EvidenceUnreadable, core.CoreError)


def test_a_wrong_typed_value_is_refused_as_a_type_not_as_a_path():
    """A number or a list is neither a path nor a document, and saying "no file
    there" about one would send the caller looking for a file they never named."""
    for bad in ([1, 2, 3], 7, True):
        with pytest.raises(core.BadRequest):
            core.preflight(LOC_FACTS, {}, core.evidence_document(bad))


def test_a_path_and_its_contents_preflight_identically(tmp_path):
    """Nothing downstream may learn which way the evidence arrived -- the same
    rule facts.manual() keeps on the account side."""
    doc = _evidence()
    path = _written(tmp_path, json.dumps(doc))
    opts = {"namespace": "blazemeter"}
    assert (core.preflight(LOC_FACTS, opts, core.evidence_document(path))
            == core.preflight(LOC_FACTS, opts, doc))


# -- the endpoint probe -------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "http://host/", "a host", "host/path",
                                 "user@host"])
def test_sv_check_refuses_anything_that_is_not_a_host(bad):
    """This string arrives from a browser or a model; a URL carrying a path or
    credentials would turn a reachability probe into a general fetcher."""
    with pytest.raises(core.BadRequest):
        core.sv_check(bad)


def test_sv_check_refuses_a_scheme_it_does_not_speak():
    with pytest.raises(core.BadRequest):
        core.sv_check("host.example.com", scheme="file")


def test_listing_locations_needs_a_scope():
    """Asked on its own by a caller that also has a credential to check, so
    that the malformed request is refused before the missing key is."""
    with pytest.raises(core.BadRequest):
        core.require_location_scope(None, None)
    assert core.require_location_scope(account_id=1) is None
    assert core.require_location_scope(workspace_id=2) is None


# -- narrowing a real account's listing ---------------------------------------

def _locs(*names):
    return [{"id": f"h{i}", "name": n} for i, n in enumerate(names)]


def test_a_cap_accounts_for_what_it_left_out():
    """A cap counts what it left out."""
    sel = core.select_locations(_locs("a", "b", "c", "d", "e"), limit=2)
    assert [l["name"] for l in sel["locations"]] == ["a", "b"]
    assert sel["total"] == 5 and sel["returned"] == 2
    assert sel["matched"] == 5
    assert sel["omitted_by_limit"] == 3 and sel["omitted_by_filter"] == 0


def test_a_name_substring_matches_anywhere_and_ignores_case():
    """Substring, not prefix: real location names lead with a customer or a
    region, and the word someone knows is usually in the middle."""
    sel = core.select_locations(_locs("EU-perf-1", "us-PERF-2", "eu-sv-3"),
                                name_contains="perf")
    assert [l["name"] for l in sel["locations"]] == ["EU-perf-1", "us-PERF-2"]
    assert sel["total"] == 3 and sel["matched"] == 2
    assert sel["omitted_by_filter"] == 1 and sel["omitted_by_limit"] == 0


def test_the_two_kinds_of_omission_are_counted_separately():
    """A filter is what the caller asked for; a cap is not. Summing them would
    hide which of the two is worth undoing."""
    sel = core.select_locations(_locs("perf-a", "perf-b", "perf-c", "sv-d"),
                                name_contains="perf", limit=2)
    assert sel["returned"] == 2
    assert sel["omitted_by_filter"] == 1 and sel["omitted_by_limit"] == 1


def test_no_cap_returns_the_whole_account():
    sel = core.select_locations(_locs("a", "b", "c"), limit=None)
    assert sel["returned"] == 3 and sel["omitted_by_limit"] == 0


def test_a_cap_below_one_is_refused_rather_than_returning_nothing():
    """`limit=0` reads as "no limit" to whoever passed it and as "nothing
    matched" in the answer."""
    with pytest.raises(core.BadRequest):
        core.select_locations(_locs("a"), limit=0)


def test_a_location_with_no_name_is_not_a_crash():
    """Names are not guaranteed by the API and a filter must not be the thing
    that discovers that."""
    sel = core.select_locations([{"id": "h1"}], name_contains="perf")
    assert sel["returned"] == 0 and sel["omitted_by_filter"] == 1


def test_a_ship_with_no_heartbeat_field_is_unknown_not_silent():
    """A ship with no heartbeat field is unknown (None), not "not reporting"."""
    assert core.ship_reporting({"id": "s1", "state": "idle"}) is None
    assert core.ship_reporting({"id": "s1", "state": "idle",
                                "lastHeartBeat": 0}) is False
    assert core.ship_reporting({"id": "s1", "state": "idle",
                                "lastHeartBeat": time.time()}) is True


# -- where a key might be -----------------------------------------------------

def test_key_candidates_read_the_environment_when_asked(monkeypatch, tmp_path):
    """BZM_API_KEY_FILE is read per call, not frozen at import."""
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    monkeypatch.setenv("BZM_API_KEY_FILE", str(key))
    assert str(key) in core.key_candidates()
    assert {"path": str(key), "key_id": "KID"} in core.detect_keys()


def test_a_key_file_that_does_not_parse_is_skipped_not_raised(monkeypatch,
                                                              tmp_path):
    """Detection runs before anything is configured, so a half-written file in
    one of the four locations must not stop the other three being offered."""
    bad = tmp_path / "api-key.json"
    bad.write_text("{ not json")
    monkeypatch.setenv("BZM_API_KEY_FILE", str(bad))
    assert all(f["path"] != str(bad) for f in core.detect_keys())


def test_a_malformed_key_file_is_a_refusal_rather_than_an_exit(tmp_path):
    """A malformed key file is a CoreError, never SystemExit."""
    bad = tmp_path / "api-key.json"
    bad.write_text("not json")
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key(str(bad))
    assert "not valid JSON" in str(e.value)
    with pytest.raises(TypeError):
        api.BzmClient(str(bad))


# -- one construction for the client ------------------------------------------
# Each refusal asserts a CoreError, so an escaping SystemExit fails the test.

@pytest.fixture
def no_key_env(monkeypatch):
    """No key in the environment of whoever runs the suite."""
    for var in (core.KEY_FILE_ENV, core.KEY_ID_ENV, core.KEY_SECRET_ENV):
        monkeypatch.delenv(var, raising=False)


def credential_of(client):
    """The (id, secret) a built client will authenticate as."""
    return tuple(base64.b64decode(client._auth).decode().split(":", 1))


def test_a_client_is_built_from_a_key_file(no_key_env, tmp_path):
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "SHHH"}')
    client = core.client_from_key(str(key))
    assert isinstance(client, api.BzmClient)
    assert credential_of(client) == ("KID", "SHHH")


def test_a_client_is_built_from_an_id_and_secret(no_key_env, monkeypatch):
    """An id and secret build a client with no file on disk."""
    monkeypatch.setattr(api, "read_key_file", lambda p: pytest.fail(
        f"a pasted id and secret read {p} -- it should reach no disk at all"))
    client = core.client_from_key(key_id="KID", secret="SHHH")
    assert credential_of(client) == ("KID", "SHHH")


def test_a_key_file_that_is_not_there_is_a_refusal_naming_the_path(no_key_env,
                                                                   tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key(str(missing))
    assert str(missing) in str(e.value)


def test_a_key_file_missing_half_the_key_is_a_refusal(no_key_env, tmp_path):
    half = tmp_path / "api-key.json"
    half.write_text('{"id": "KID"}')
    with pytest.raises(core.NotConfigured, match='"id" and "secret"'):
        core.client_from_key(str(half))


def test_a_key_file_that_cannot_be_read_at_all_is_a_refusal(no_key_env,
                                                            tmp_path):
    """A directory, an unreadable mode or a binary file is a refusal, not a bare
    OSError or UnicodeDecodeError."""
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key(str(tmp_path))            # a directory
    assert str(tmp_path) in str(e.value)

    binary = tmp_path / "api-key.json"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    with pytest.raises(core.NotConfigured, match="not valid JSON"):
        core.client_from_key(str(binary))


def test_a_path_and_a_pasted_pair_together_are_refused(no_key_env, tmp_path):
    """A path and a pasted pair together are refused."""
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "FILE", "secret": "s"}')
    with pytest.raises(core.BadRequest) as e:
        core.client_from_key(str(key), key_id="PASTED", secret="s")
    assert "not both" in str(e.value)


def test_half_a_pasted_pair_names_the_half_that_is_missing(no_key_env):
    """Half a pasted pair names the missing half."""
    with pytest.raises(core.BadRequest, match="secret"):
        core.client_from_key(key_id="KID")
    with pytest.raises(core.BadRequest, match="id"):
        core.client_from_key(secret="SHHH")


def test_the_environment_is_the_last_place_looked(no_key_env, monkeypatch,
                                                  tmp_path):
    """Arguments beat the environment, and both environment forms work."""
    env_key = tmp_path / "env-key.json"
    env_key.write_text('{"id": "FROM-FILE-ENV", "secret": "s"}')
    monkeypatch.setenv(core.KEY_FILE_ENV, str(env_key))
    assert credential_of(core.client_from_key())[0] == "FROM-FILE-ENV"

    named = tmp_path / "named.json"
    named.write_text('{"id": "NAMED", "secret": "s"}')
    assert credential_of(core.client_from_key(str(named)))[0] == "NAMED"
    assert credential_of(
        core.client_from_key(key_id="PASTED", secret="s"))[0] == "PASTED"

    monkeypatch.delenv(core.KEY_FILE_ENV)
    monkeypatch.setenv(core.KEY_ID_ENV, "FROM-ID-ENV")
    monkeypatch.setenv(core.KEY_SECRET_ENV, "s")
    assert credential_of(core.client_from_key())[0] == "FROM-ID-ENV"
    assert credential_of(core.client_from_key(str(named)))[0] == "NAMED"


def test_no_key_anywhere_says_how_to_supply_one(no_key_env, monkeypatch):
    """With no key anywhere, the refusal says how to supply one; the working
    directory is not searched."""
    monkeypatch.setattr(core, "detect_keys", lambda: pytest.fail(
        "the construction discovered a key from the working directory"))
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key()
    for var in (core.KEY_FILE_ENV, core.KEY_ID_ENV, core.KEY_SECRET_ENV):
        assert var in str(e.value)


SEAM = f"core.{core.client_from_key.__name__}"


def _client_constructions(path):
    """Every `BzmClient(...)` call in a source file, by line (parsed, so prose
    mentioning it does not count)."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    called = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name == "BzmClient":
            called.append(node.lineno)
    return called


def test_the_client_is_built_in_exactly_one_place():
    """The package builds a BzmClient in exactly one place: core.client_from_key.
    Tests build their own on purpose."""
    pkg = os.path.dirname(core.__file__)
    found = {}
    for root, _dirs, names in os.walk(pkg):
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            for line in _client_constructions(path):
                found.setdefault(os.path.relpath(path, pkg), []).append(line)

    where = sorted(f"{f}:{n}" for f, lines in found.items() for n in lines)
    first, last = _line_range(core.client_from_key)
    assert len(where) == 1 and first <= found.get("core.py", [0])[0] <= last, (
        f"the client is constructed at {where} -- {SEAM} is the one "
        f"construction, and a caller that needs a client asks it for one. It "
        f"takes a key file path, or an id and secret, or neither and reads the "
        f"environment, and it refuses with a CoreError carrying a status where "
        f"the constructor cannot. Building one here also puts it outside the "
        f"point tests/test_cli.py, tests/test_mcp.py and tests/test_server.py "
        f"stand in at, so nothing in the suite covers the caller.")


def _line_range(fn):
    """First and last line of a function's source, in its own file."""
    lines, start = inspect.getsourcelines(fn)
    return start, start + len(lines) - 1


def test_the_fake_client_is_a_second_adapter_and_not_a_third_interface():
    """FakeClient's methods and parameter names match BzmClient's (defaults
    excluded), so the suites stand in for the real interface."""
    def methods(cls):
        return {n: f for n, f in inspect.getmembers(cls, inspect.isfunction)
                if not n.startswith("_")}

    real, fake = methods(api.BzmClient), methods(FakeClient)
    assert not set(fake) - set(real)
    for name in sorted(fake):
        def params(f):
            return [(p.name, p.kind) for p in
                    inspect.signature(f).parameters.values()]
        assert params(fake[name]) == params(real[name]), name


def test_detect_never_reads_a_secret_back_out(monkeypatch, tmp_path):
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "SHHH"}')
    monkeypatch.setenv("BZM_API_KEY_FILE", str(key))
    assert "SHHH" not in json.dumps(core.detect_keys())


# -- the vocabulary -----------------------------------------------------------

def test_option_defaults_are_the_generator_s_own():
    assert core.option_defaults() == bundle_options.DEFAULT_OPTIONS


def test_option_docs_cover_every_option():
    assert set(core.option_docs()) == set(bundle_options.DEFAULT_OPTIONS)


# -- the funcId vocabulary, and where it comes from ----------------------------


def test_the_keyless_vocabulary_is_the_funcids_this_tool_covers():
    """With no account, the vocabulary is the covered funcIds under BlazeMeter's
    own display names."""
    rows = core.func_ids()["choices"]
    assert [(r["id"], r["label"]) for r in rows] == [
        ("performance", "Performance"),
        ("functionalGui", "GUI Functional"),
        ("mockServices", "Service Virtualization")]
    assert all(r["covered"] for r in rows)


def test_the_vocabulary_says_whether_it_is_the_account_s_or_the_baseline():
    """`source` says whether the vocabulary is the account's or the baseline."""
    assert core.func_ids()["source"] == "baseline"
    assert core.func_ids(FakeClient(), 123456)["source"] == "account"


def test_a_browser_pin_is_a_parameter_of_its_parent_not_a_funcid_of_its_own():
    """Browser pins are served under functionalGui, not as rows of their own."""
    by_id = {r["id"]: r for r in core.func_ids(FakeClient(), 123456)["choices"]}
    assert by_id["functionalGui"]["sub_func_ids"] == [
        "chrome:default", "firefox:139", "safari:15"]
    assert not any(":" in f for f in by_id)
    # Every row carries a list, empty meaning no pins.
    assert all(r["sub_func_ids"] == []
               for f, r in by_id.items() if f != "functionalGui")


def test_the_baseline_claims_no_pins_rather_than_guessing_at_them():
    """The baseline carries no pins; only the account knows them."""
    rows = core.func_ids()["choices"]
    assert all(r["sub_func_ids"] == [] for r in rows)


def test_the_account_replaces_the_baseline_with_its_own_vocabulary():
    """...and the account's list is longer, differently named, and does not
    offer `functionalApi` at all -- which the hand-written table did."""
    client = FakeClient()
    rows = core.func_ids(client, 123456)["choices"]
    by_id = {r["id"]: r for r in rows}

    assert client.calls == [("functionalities", 123456)]
    assert "functionalApi" not in by_id
    assert by_id["functionalGui"]["label"] == "GUI Functional"
    assert by_id["tdm"]["label"] == "TDM Integration"


def test_the_vocabulary_says_which_funcids_this_tool_covers():
    """Every row says whether this tool covers it; uncovered funcIds are still listed."""
    by_id = {r["id"]: r for r in core.func_ids(FakeClient(), 123456)["choices"]}
    assert [f for f, r in by_id.items() if r["covered"]] == [
        "performance", "mockServices", "functionalGui"]
    for f in ("proxyRecorder", "tdm", "dataPublisher", "delphix",
              "secretsPrivateVault", "enableSecretsToggle"):
        assert by_id[f]["covered"] is False


def test_an_unreadable_account_is_not_an_account_with_three_functionalities():
    """An account that refuses the read raises rather than answering the baseline."""
    with pytest.raises(core.CoreError):
        core.func_ids(ExpiredClient(), 123456)


def test_a_functionality_is_one_funcid_under_blazemeter_s_own_name():
    """One functionality per covered funcId, `id` equal to it, labelled as
    BlazeMeter labels it."""
    served = core.functionalities()
    assert [(f["id"], f["label"]) for f in served] == [
        ("performance", "Performance"),
        ("functionalGui", "GUI Functional"),
        ("mockServices", "Service Virtualization")]
    # The account's own labels.
    assert not any("func_ids" in f for f in served)


def test_a_covered_funcid_and_a_functionality_are_one_table():
    """`covered` and having a functionality card are one table."""
    ids = [f["id"] for f in core.functionalities()]
    assert [r["id"] for r in core.func_ids()["choices"]] == ids
    assert all(r["covered"] for r in core.func_ids()["choices"])
    # With an account, the covered rows are still exactly the functionalities.
    rows = core.func_ids(FakeClient(), 123456)["choices"]
    assert {r["id"] for r in rows if r["covered"]} == set(ids)


def test_every_functionality_names_a_funcid_the_facts_layer_models():
    """Every functionality's funcId selects images in the facts layer. (The reverse
    does not hold: functionalApi and proxyRecorder are modelled but uncovered.)"""
    from bzm_opl_gen import facts as facts_mod
    ids = {f["id"] for f in core.functionalities()}
    assert ids <= set(facts_mod.CATEGORY_BY_FUNC)
    modelled = set(facts_mod.CATEGORY_BY_FUNC)
    assert {"functionalApi", "proxyRecorder"} <= modelled - ids
    assert "tdm" not in ids


# -- where a token lives, and that redaction still knows -----------------------

TOKEN_SHAPES = [
    ({}, "the Secret"),
    ({"use_secret": False}, "the ConfigMap, when there is no Secret"),
    ({"output_format": "helm"}, "the chart's values overlay"),
]


@pytest.mark.parametrize("opts,where", TOKEN_SHAPES, ids=lambda v: v if isinstance(v, str) else "")
def test_no_generated_file_survives_redaction_still_holding_the_token(opts, where):
    """Redaction leaves no generated file holding the token (generate.TOKEN_FIELDS)."""
    files = core.generate_bundle(
        dict(FACTS), {"namespace": "ns1", "auth_token": "TOKENVALUE", **opts},
        client=None)
    carriers = [n for n, c in files.items() if "TOKENVALUE" in c]
    assert carriers, f"nothing carried the token for {where}"
    for name in carriers:
        redacted, count = core.redact_tokens(files[name])
        assert "TOKENVALUE" not in redacted, (
            f"{name} ({where}) still holds the token after redaction -- "
            f"generate.TOKEN_FIELDS does not know the field it is under")
        assert count >= 1


def test_the_reader_and_the_redactor_know_the_same_fields():
    """They did not: the reader knew only AUTH_TOKEN, so a regenerated chart
    bundle never found the token its predecessor wrote."""
    from bzm_opl_gen import generate as gen
    for field in gen.TOKEN_FIELDS:
        line = f'  {field}: "abc123"\n'
        assert gen.AUTH_TOKEN_RE.search(line), f"reader misses {field}"
        assert core.redact_tokens(line)[1] == 1, f"redactor misses {field}"


def test_regenerating_a_chart_bundle_finds_the_token_it_wrote(tmp_path):
    """A chart bundle's token is read back on regenerate rather than re-fetched."""
    from bzm_opl_gen import generate as gen
    files = core.generate_bundle(
        dict(FACTS), {"namespace": "ns1", "output_format": "helm",
                      "auth_token": "TOKENVALUE"}, client=None)
    gen.write(files, str(tmp_path))
    assert gen.existing_auth_token(str(tmp_path)) == "TOKENVALUE"


# -- changing a location's settings after the fact ----------------------------

def _loc(**over):
    base = {"id": "h1", "name": "loc", "slots": 2, "threadsPerEngine": 500,
            "overrideCPU": None, "overrideMemory": None,
            "funcIds": ["performance"]}
    base.update(over)
    return base


def test_update_location_changes_what_it_was_asked_to():
    client = FakeClient(harbor=_loc())
    out = core.update_location(client, "h1", threads_per_engine=1000)
    assert out["changed"] == {"threads_per_engine": 1000}
    assert out["before"]["threads_per_engine"] == 500
    assert out["after"]["threads_per_engine"] == 1000
    assert out["ignored"] == []


def test_update_location_leaves_alone_what_was_not_sent():
    """A partial update. Sending only the field being changed is what stops a
    form from writing back three values a browser has been holding."""
    client = FakeClient(harbor=_loc())
    out = core.update_location(client, "h1", threads_per_engine=1000)
    assert out["after"]["slots"] == 2
    assert out["location"]["funcIds"] == ["performance"]


def test_update_location_reports_a_field_the_account_did_not_store():
    """A field the account accepted and did not store is reported as ignored."""
    client = FakeClient(harbor=_loc(), ignores={"overrideCPU"})
    out = core.update_location(client, "h1", threads_per_engine=1000,
                               override_cpu=2)
    assert out["changed"] == {"threads_per_engine": 1000}
    assert out["ignored"] == ["override_cpu"]
    assert out["after"]["override_cpu"] is None


def test_update_location_re_reads_rather_than_trusting_the_write():
    client = FakeClient(harbor=_loc())
    core.update_location(client, "h1", slots=4)
    kinds = [c[0] for c in client.calls]
    # before, the write, and after -- the last is what the answer describes.
    assert kinds == ["private_location", "update_private_location",
                     "private_location"]


def test_update_location_with_nothing_to_change_writes_nothing():
    """A form submitted unchanged is a no-op, not an error, and must not spend
    a write on the customer's account to find that out."""
    client = FakeClient(harbor=_loc())
    out = core.update_location(client, "h1")
    assert out["changed"] == {} and out["ignored"] == []
    assert [c for c in client.calls if c[0] == "update_private_location"] == []


def test_update_location_refuses_a_setting_it_does_not_own():
    """`funcIds` in particular: the PATCH replaces the list wholesale, so a
    general passthrough would drop every functionality the caller did not name."""
    client = FakeClient(harbor=_loc())
    with pytest.raises(core.BadRequest, match="funcIds"):
        core.update_location(client, "h1", funcIds=["mockServices"])
    assert [c for c in client.calls if c[0] == "update_private_location"] == []


def test_update_location_says_a_value_was_already_what_was_asked_for():
    """A value that already matched is unchanged, not ignored."""
    client = FakeClient(harbor=_loc())
    out = core.update_location(client, "h1", threads_per_engine=500)
    assert out["changed"] == {} and out["ignored"] == []


# -- what an account can generate ---------------------------------------------
# Settled live: `agents x slots` engines are enforced; threadsPerEngine is a
# rating, not a ceiling.

def _cap_loc(name, ships=1, slots=1, tpe=50, workspaces=(1,), **over):
    loc = {"id": f"h-{name}", "name": name, "slots": slots,
           "threadsPerEngine": tpe, "funcIds": ["performance"],
           "workspacesId": list(workspaces),
           "ships": [{"id": f"s{i}", "lastHeartBeat": 1} for i in range(ships)]}
    loc.update(over)
    return loc


def test_rated_capacity_is_agents_times_slots_times_threads():
    """Two agents at one slot each and 50 virtual users per engine rate 100
    (measured live)."""
    client = FakeClient(locations=[_cap_loc("Bens Linux", ships=2, slots=1, tpe=50)])
    out = core.account_capacity(client, 7)
    loc = out["locations"][0]
    assert loc["engines"] == 2
    assert loc["rated_vus"] == 100
    assert out["rated_vus"] == 100


def test_a_location_nobody_has_sized_has_no_rating_rather_than_zero():
    """`slots` or `threadsPerEngine` unset is "nobody has said", and 0 would
    read as "no capacity" -- a different claim, and a wrong one."""
    client = FakeClient(locations=[_cap_loc("new", slots=None),
                                   _cap_loc("half", tpe=None)])
    out = core.account_capacity(client, 7)
    assert [l["rated_vus"] for l in out["locations"]] == [None, None]
    assert out["unrated"] == 2
    # And it contributes nothing to the total rather than breaking the sum.
    assert out["rated_vus"] == 0


def test_a_location_in_two_workspaces_is_flagged_and_counted_once():
    """Its capacity is claimable from either, so adding it into both workspace
    totals counts engines that cannot run twice."""
    client = FakeClient(locations=[
        _cap_loc("shared", ships=2, slots=1, tpe=50, workspaces=(1, 2)),
        _cap_loc("alpha-only", ships=1, slots=1, tpe=50, workspaces=(1,))])
    out = core.account_capacity(client, 7)
    shared = next(l for l in out["locations"] if l["name"] == "shared")
    assert shared["shared"] is True
    assert shared["workspace_names"] == ["Alpha", "Beta"]
    # 100 + 50, not 100 + 100 + 50.
    assert out["rated_vus"] == 150


def test_an_agent_the_payload_says_nothing_about_is_unknown_not_absent():
    """A ship the payload says nothing about is counted unknown, not "not
    reporting"; both still count toward the rating."""
    stale = {"id": "s1", "lastHeartBeat": 1, "state": "idle"}   # present, old
    silent = {"id": "s2"}                                        # no heartbeat
    client = FakeClient(locations=[{
        "id": "h1", "name": "half-up", "slots": 1, "threadsPerEngine": 50,
        "workspacesId": [1], "funcIds": [], "ships": [stale, silent]}])
    loc = core.account_capacity(client, 7)["locations"][0]
    assert loc["agents"] == 2
    assert loc["agents_reporting"] == 0      # the stale one is a real "no"
    assert loc["agents_unknown"] == 1        # the silent one is not
    assert loc["rated_vus"] == 100


def test_a_location_with_no_agents_rates_nothing():
    """An empty location is a record in BlazeMeter with nothing behind it."""
    client = FakeClient(locations=[_cap_loc("empty", ships=0)])
    out = core.account_capacity(client, 7)
    assert out["locations"][0]["engines"] == 0
    assert out["locations"][0]["rated_vus"] == 0
