"""The orchestration layer, driven directly rather than over HTTP.

This module imports no fastapi and skips nothing. That is the point of the
split: the decisions worth testing -- when a token is fetched, which namespace
a preflight is for, what counts as an agent being online -- used to live inside
route handlers, so the only way to reach them was a TestClient, and a venv
without fastapi tested none of them while reporting a clean pass.

tests/test_server.py still covers the same behaviour through the HTTP layer.
That is deliberate: these are the decisions and those are the status codes, and
a change that moves one without the other should fail somewhere.
"""

import ast
import base64
import inspect
import io
import json
import os
import time
import zipfile

import pytest

from bzm_opl_gen import api, core, generate as gen
from test_generate import FACTS


# What a real account answers to GET /accounts/{id}/functionalities, trimmed to
# the entries that decide something here and otherwise verbatim: the display
# names are BlazeMeter's, and `functionalApi` is absent because the account no
# longer serves it while locations created before its removal still carry it.
#
# `functionalGui` carries three of its 117 `subFunctionalities` -- a parent with
# several of its pins is the shape #160 is about, and one pin would not show a
# reader keeping them in a list. They are the browser a GUI Functional location
# is pinned to, which is a *parameter* of that funcId rather than a funcId of
# its own; a location carrying one carries the parent beside it.
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
    """Enough BzmClient to exercise the paths that reach for one.

    Shared with tests/test_mcp.py rather than written twice: both suites drive
    the same core functions, and two fakes answering `private_location`
    differently would let the two layers disagree about what an account looks
    like. Methods no core test calls are here for that reason.
    """

    def __init__(self, token="TOKEN-FROM-API", harbor=None, locations=None,
                 ignores=(), versions=None):
        self._token = token
        # What GET .../versions does -- a recorded payload, or an exception to
        # raise. It defaults to raising, because a stand-in account that answers
        # would decide the images for every test here rather than the one asking
        # about them.
        self._versions = versions if versions is not None else api.BzmApiError(
            "GET /private-locations/h/ships/s/versions -> HTTP 404: not found")
        self._harbor = harbor if harbor is not None else {}
        self._locations = locations
        self._workspaces = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}]
        # BlazeMeter fields this stand-in account accepts on a PATCH and then
        # does not store, which is a real behaviour rather than an invented
        # one: POST /private-locations does exactly that with threadsPerEngine.
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
        # The write lands on the harbor this fake hands back, so a caller that
        # re-reads sees what it wrote -- which is the whole point of
        # core.update_location's second GET, and untestable against a fake
        # whose state never moves.
        sent = {"slots": slots, "threadsPerEngine": threads_per_engine,
                "overrideCPU": override_cpu, "overrideMemory": override_memory}
        for field, value in sent.items():
            if value is not None and field not in self._ignores:
                self._harbor[field] = value
        return dict(self._harbor)


# -- nothing here turns a functionality on for a location ---------------------
#
# core.add_func_id was here, additive by construction, behind the configure
# page's "Enable on this location…". Both went in #113: what funcIds a location
# carries is what the location *is*, and BlazeMeter's own UI is where that
# changes. The client cannot send them any more either -- see
# test_the_client_cannot_replace_a_location_s_functionalities.


def test_the_client_cannot_replace_a_location_s_functionalities():
    """BlazeMeter's PATCH replaces `funcIds` wholesale, so a caller meaning to
    add one drops the rest. With nothing left that adds them additively, the
    parameter would be that hazard with nothing guarding it."""
    assert "func_ids" not in inspect.signature(
        api.BzmClient.update_private_location).parameters
    assert not hasattr(core, "add_func_id")


# -- creating one, and whether it can start a test -----------------------------
#
# #93. The warning lived in `cli.py` alone, so the two surfaces that create
# locations without a terminal -- the web page and an MCP session -- made one
# that 403s every test start and said nothing about it.

class _RunnableClient(FakeClient):
    """An account that stores threadsPerEngine, as the PATCH after the POST
    makes it. FakeClient's own create answers the shape a location comes back
    in before that lands, which is the unrunnable one."""

    def create_private_location(self, name, account_id, workspace_ids,
                                func_ids=("performance",), slots=1,
                                threads_per_engine=None):
        return dict(super().create_private_location(
            name, account_id, workspace_ids, func_ids=func_ids, slots=slots),
            threadsPerEngine=threads_per_engine)


def test_a_location_that_cannot_start_a_test_says_so_when_it_is_created():
    """The 403 it produces -- "Not enough available resources" -- names neither
    field and reads as a busy account, so the moment to say it is the one where
    the location is made."""
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
    """Either field missing is the same 403, so the verdict reads both -- one
    that looked only at the field this tool tends to lose would vouch for the
    other."""
    made = core.create_location(_RunnableClient(), "loc", 7, 2, slots=0,
                                threads_per_engine=500)
    assert made["runnable"] is False and made["warning"]


# -- the slots a functionality needs before BlazeMeter will make the location --
#
# #159. Found on a live POST, because nothing offline could have found it: the
# rule is not in BlazeMeter's private-location documentation, and every fixture
# here answers a create the account never saw.

def test_gui_functional_cannot_be_created_at_the_default_one_slot():
    """The POST 400s, so the refusal is here -- before the write, on every
    surface at once, rather than three renderings of BlazeMeter's error."""
    client = FakeClient()
    with pytest.raises(core.BadRequest) as e:
        core.create_location(client, "loc", 7, 2,
                             func_ids=["performance", "functionalGui"])
    assert "Parallel engine runs must be greater than 1" in str(e.value)
    assert "GUI Functional" in str(e.value)
    # Nothing reached the account: a refusal that POSTs first is the 400 with
    # extra steps.
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
    does -- the DOCKER_IGNORED rule, one vocabulary along."""
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


def test_issue_auth_token_mints_for_the_ship_it_was_given():
    client = FakeClient()
    assert core.issue_auth_token(client, "h1", "s1") == "TOKEN-FROM-API"
    assert client.calls == [("auth_token", "h1", "s1")]


def test_issue_auth_token_reports_a_refused_endpoint_as_such():
    """Same refusal as everywhere else the token endpoint is called: it names
    the ship and says a token read off the BlazeMeter UI works as well."""
    with pytest.raises(core.TokenRefused) as e:
        core.issue_auth_token(RefusingClient(), "h1", "s1")
    assert "could not be issued" in str(e.value)


# -- the split itself ---------------------------------------------------------

def _imports(path):
    """Every top-level name a file imports, read from the parsed source.

    Parsed rather than taken from sys.modules: another test module in the same
    session imports fastapi, so by the time these run it is loaded whatever
    core does.
    """
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from bzm_opl_gen import core, server` -- the interesting name is
            # what was imported, not the package it came from.
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
    """An import of `server` anywhere in this file would put every test in it
    behind the optional dependency -- which is the whole thing the split was
    for. It happened once already, in a test that only wanted to read server.py
    as text; open it by path instead.
    """
    assert "server" not in _imports(__file__)


@pytest.mark.parametrize("exc,status", [
    (core.BadRequest, 400),
    (core.NotFound, 404),
    (core.UpstreamError, 502),
])
def test_errors_carry_the_status_the_web_layer_answers_with(exc, status):
    """The web layer translates rather than re-deciding, so the code is part of
    the error and not of the route -- otherwise the two drift and the same
    refusal answers 400 on one endpoint and 500 on another."""
    e = exc("nope")
    assert isinstance(e, core.CoreError) and e.status == status
    assert str(e) == "nope"


def test_an_unclassified_failure_does_not_blame_the_caller():
    """The base carries 500 so that a subclass which forgets to name a status
    reports a bug in here, rather than inheriting 400 and reading as bad
    input from whoever called."""
    assert core.CoreError.status == 500
    assert all(e.status != core.CoreError.status
               for e in (core.BadRequest, core.NotFound, core.UpstreamError))


# -- where a bundle's AUTH_TOKEN comes from -----------------------------------
# Four branches, one rule, and only one of them mints. Minting *rotates*: the
# previous token dies and the agent holding it sits at 0/1 Running, so a bundle
# regenerated to look at it used to revoke a working agent's credential (#64).

def _bundle(tmp_path, **opts):
    """A written bundle, as a predecessor for the reuse branch to read back."""
    files = gen.generate(dict(FACTS), {"namespace": "ns1", **opts})
    gen.write(files, str(tmp_path))
    return files


def test_generate_mints_nothing_by_default_even_holding_a_key():
    """The whole of #64: a client is no longer permission to rotate. Generating
    twice against an account used to hand back two different tokens, the second
    of which quietly killed the agent running on the first."""
    c = FakeClient()
    files = core.generate_bundle(FACTS, {"namespace": "ns1"}, client=c)
    assert c.calls == []
    assert gen.DEFAULT_OPTIONS["auth_token"] in files["bzm_secret.yaml"]


def test_a_token_in_the_options_wins_outright_and_a_rotation_is_not_silent():
    """Both flags together is a contradiction with one safe reading -- minting
    and then writing the supplied value over it revokes the token that was
    passed and puts nothing usable in the bundle. So the rotation loses, and
    says it lost: a flag quietly dropped is the shape of this whole bug."""
    c = FakeClient()
    opts = {"namespace": "ns1", "auth_token": "MINE"}
    src = core.resolve_auth_token(FACTS, opts, client=c, rotate=True)
    assert src.branch == core.TOKEN_GIVEN
    assert opts["auth_token"] == "MINE" and c.calls == []
    assert "--rotate-token was NOT acted on" in src.message
    assert "--rotate-token" not in core.resolve_auth_token(
        FACTS, dict(opts)).message


def test_rotating_mints_and_names_the_ship_it_was_for():
    """`rotate=True` is the one branch that calls the endpoint, and the ship
    comes back so the caller can say whose credential just changed -- which the
    MCP surface answered as `warnings: []` before this."""
    c = FakeClient()
    opts = {"namespace": "ns1"}
    src = core.resolve_auth_token(FACTS, opts, client=c, rotate=True)
    assert c.calls == [("auth_token", "aaa111", "bbb222")]
    assert (src.branch, src.ship_id) == (core.TOKEN_ROTATED, "bbb222")
    assert "bbb222" in src.message
    assert opts["auth_token"] == "TOKEN-FROM-API"


def test_rotating_warns_before_it_acts_not_after():
    """After is a report: the agent's credential is already dead by then. The
    announcement has to reach the caller ahead of the call, which is why
    `announce` is a parameter of the resolution rather than something the
    caller is trusted to remember."""
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
    """`generate.existing_auth_token` was written for exactly this and had no
    production caller at all -- so every regenerate fell through to a mint."""
    _bundle(tmp_path, auth_token="TOKENVALUE")
    c = FakeClient()
    opts = {"namespace": "ns1"}
    src = core.resolve_auth_token(FACTS, opts, client=c, out_dir=str(tmp_path))
    assert (src.branch, src.ship_id) == (core.TOKEN_REUSED, "bbb222")
    assert opts["auth_token"] == "TOKENVALUE" and c.calls == []


def test_regenerating_a_bundle_twice_is_byte_identical(tmp_path):
    """#64's own acceptance criterion. It holds because the second render reads
    the first one's token back instead of issuing a new one -- and it covers
    profile.json too, which is the file a reviewer would diff."""
    first = _bundle(tmp_path, auth_token="TOKENVALUE")
    opts = {"namespace": "ns1"}
    core.resolve_auth_token(FACTS, opts, client=FakeClient(),
                            out_dir=str(tmp_path))
    second = core.generate_bundle(FACTS, opts, out_dir=str(tmp_path))
    assert second == first


def test_a_bundle_for_another_ship_is_refused_rather_than_overwritten(tmp_path):
    """Reusing across ships would write another location's credential into this
    bundle. Warning and carrying on is not enough, because carrying on
    *overwrites that directory* -- and the API only mints, so the bundle was the
    only copy of that token outside a running cluster. Refused, and the file is
    still there afterwards."""
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
    """The escape from the refusal above, and why it is not a dead end: a token
    passed for *this* ship never looks at the directory at all, so replacing
    another ship's bundle stays possible for whoever means it."""
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                               dict(FACTS["ships"][0], id="b2")])
    gen.write(gen.generate(facts, {"namespace": "ns1", "ship_id": "b1",
                                   "auth_token": "B1TOKEN"}), str(tmp_path))
    opts = {"namespace": "ns1", "ship_id": "b2", "auth_token": "B2TOKEN"}
    src = core.resolve_auth_token(facts, opts, out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_GIVEN and opts["auth_token"] == "B2TOKEN"


def test_the_refusal_never_names_a_ship_called_None(tmp_path):
    """Two agents and no --ship-id: `want` is None, and the sentence came out as
    "holds a bundle for ship b1, not None" -- naming a ship that does not exist
    and burying the actual remedy, which is to say which ship this bundle is
    for. The ambiguity is the thing to report, not the directory."""
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
    """An older bundle, or a hand-assembled directory: there is a token in it
    and nothing that says whose. Refused on the same ground as the mismatch --
    writing over it destroys a credential nothing can re-read -- but the reason
    given differs, because so does the remedy: pass the token rather than go
    looking at another directory."""
    _bundle(tmp_path, auth_token="TOKENVALUE")
    os.remove(os.path.join(str(tmp_path), gen.PROFILE_FILE))
    opts = {"namespace": "ns1"}
    with pytest.raises(core.BadRequest) as caught:
        core.resolve_auth_token(FACTS, opts, out_dir=str(tmp_path))
    assert gen.PROFILE_FILE in str(caught.value)
    assert "auth_token" not in opts
    assert "TOKENVALUE" in (tmp_path / "bzm_secret.yaml").read_text()


def test_an_empty_directory_is_not_a_bundle_to_protect(tmp_path):
    """The refusals above must not have turned a first run into an error: a
    directory with no token in it is the ordinary case, and generating into a
    fresh or nonexistent path stays the placeholder branch."""
    src = core.resolve_auth_token(FACTS, {"namespace": "ns1"},
                                  out_dir=str(tmp_path / "nothing-here"))
    assert src.branch == core.TOKEN_PLACEHOLDER


def test_no_token_anywhere_says_where_a_real_one_comes_from(tmp_path):
    """The placeholder is a fine bundle to read and an unusable one to apply,
    so the branch that produces it has to name both sources of a real token.
    The kubectl is *named*, never run: nothing here reads a cluster."""
    src = core.resolve_auth_token(FACTS, {"namespace": "ns1"},
                                  out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_PLACEHOLDER
    assert "kubectl -n ns1 get secret" in src.message
    assert "base64 -d" in src.message


def test_the_placeholder_message_reads_on_every_surface_that_shows_it():
    """This sentence is not the CLI's. The web UI renders it verbatim under the
    download button and an MCP session quotes it, so a tail that named only
    `--auth-token` and `--rotate-token` told a browser to type flags it has no
    prompt for. It names the option and both registers, or it is wrong somewhere.
    """
    msg = core.token_recovery_hint({"namespace": "ns1"})
    assert "auth_token" in msg, "the option itself, which every surface has"
    assert "--auth-token" in msg, "the command line"
    assert "field" in msg.lower(), "the page"


def test_one_sentence_names_every_place_a_token_can_be_got_from():
    """There were two of these -- one naming the BlazeMeter UI's install command,
    one naming create-agent and a deployed Secret -- and `resolve_auth_token` used
    each in a different branch. Three real sources, so one sentence carries all
    three; a caller who never ran create-agent still has somewhere to go."""
    msg = core.token_recovery_hint({"namespace": "ns1"})
    assert "create-agent" in msg, "what was printed when the agent was made"
    assert "Private Locations" in msg, "the BlazeMeter UI's install command"
    assert "kubectl -n ns1 get secret" in msg, "an agent already deployed"


def test_a_refused_endpoint_says_where_else_a_token_lives():
    """The refusal path used the other sentence, so it named the BlazeMeter UI
    and not the agent already running -- which is the source that needs no
    account access at all, and the account is precisely what just refused."""
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
    """The refusal this replaced was unanswerable from the one surface that
    could reach it: the page had already let the field be emptied, so the
    download failed naming a field the person was looking at. It is a bundle
    now, and the bundle says so."""
    files = core.generate_bundle(FACTS, {"service_account_name": ""},
                                 client=None)
    assert "not finished" in files["README.md"]
    assert "service_account_name" in files["README.md"]


# -- a credential the account will not issue ----------------------------------

# What a restricted account really answers, in the wording api.BzmClient hands
# on: the token endpoint is allowed only from BlazeMeter's own gateway, so every
# attempt fails and no argument to it would have helped.
TOKEN_403 = ('POST /private-locations/aaa111/ships/bbb222/docker-command -> '
             'HTTP 403: {"error": {"code": 403, "message": "Forbidden: Should '
             'access from Private-Data gateway"}}')


class RefusingClient(FakeClient):
    """An account whose token endpoint is closed. Shared with tests/test_cli.py,
    which drives the other surviving caller of the fetch."""

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        raise api.BzmApiError(TOKEN_403)


# A key BlazeMeter has stopped accepting -- expired, revoked, or typed wrong.
# Nothing about it is visible until something is asked of the account, which is
# why it is the failure every surface has to be able to report: it arrives on
# the first call each command makes.
EXPIRED_401 = ('GET /user -> HTTP 401: {"error": {"code": 401, "message": '
               '"Unauthorized: invalid API key"}}')


class ExpiredClient(FakeClient):
    """An account that answers 401 to everything. Shared with tests/test_cli.py.

    Every read and every write, because which call a command makes first is the
    command's business -- what has to be true is that whichever it is comes
    back as a sentence rather than as a BzmApiError nobody caught.
    """

    def _refuse(self, *a, **kw):
        raise api.BzmApiError(EXPIRED_401)

    user = accounts = workspaces = functionalities = _refuse
    private_locations = private_location = _refuse
    create_private_location = update_private_location = _refuse
    delete_private_location = create_ship = auth_token = _refuse


# Every path that still reaches the endpoint. Parametrised rather than tested
# once through the fetch helper: the point of the refusal is that it arrives
# whole at whoever asked, and a caller that unwrapped it on the way -- turning
# it into a BadRequest, or letting the raw body past -- would pass a test that
# only drove the helper.
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
    """The 403 body names no ship and does not say which of the two things went
    wrong -- the credential, or the operation the caller asked for. On an
    account that restricts the endpoint every attempt fails, so a message that
    reads as "your request was wrong" sends the reader to look at their
    arguments."""
    msg = str(_refusal(name))
    assert "bbb222" in msg              # the ship, which the body never names
    assert "AUTH_TOKEN" in msg
    assert "could not be issued" in msg


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_says_the_token_can_be_supplied_instead(name):
    """A refusal with no way forward dead-ends the whole operation, and this one
    has one: the token is on the agent in the BlazeMeter UI, and every generate
    takes it as an option."""
    msg = str(_refusal(name))
    assert "auth_token" in msg and "--auth-token" in msg
    assert "BlazeMeter UI" in msg


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_keeps_the_upstream_reason(name):
    """Written over, not swallowed: "Should access from Private-Data gateway" is
    the only clue that the account is configured this way deliberately, so it
    stays both in the message and reachable on the error."""
    e = _refusal(name)
    assert e.upstream == TOKEN_403
    assert TOKEN_403 in str(e)
    assert str(e) != TOKEN_403          # ...but is no longer the whole message


@pytest.mark.parametrize("name", list(REFUSED_CALLS))
def test_a_refused_credential_is_not_a_malformed_request(name):
    """An upstream refusal, so 502: nothing the caller sent was wrong, and a 400
    would have the web UI and the MCP session both blame the person asking."""
    e = _refusal(name)
    assert isinstance(e, core.UpstreamError)
    assert e.status == 502
    assert not isinstance(e, core.BadRequest)


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
    """The archive and the folder it extracts to are one string. They were two,
    so every bundle whatever its location extracted to `bzm-opl/` -- and a
    second download merged into the first rather than sitting beside it."""
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


# -- the rule three call sites applied ----------------------------------------
# `generate --api-key`, `livetest` and the UI's download button each decided
# which ship they were about, in their own copy of the same clause. They agreed,
# which is the only reason it was not already a bug -- and for the two that
# fetch a token, disagreeing means rotating a credential belonging to an agent
# the user never mentioned.

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
    """Read out of the source: each duplicate was a single line that looked
    obviously right, which is how three of them survived. `ships[0]` is the
    shape of the mistake -- taking a ship by position.

    Opened by path rather than imported: `server` imports fastapi at module
    scope, and importing it here would put this suite behind the optional
    dependency it exists to be independent of.
    """
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


# The half of preflight() a caller that prints its own report needs on its own.
# `doctor --cluster-evidence` is that caller -- doctor.run writes to stdout and
# core is not a terminal -- and it had its own copy of this precedence, comment
# included, so a change to one was a change to one.

def test_preflight_cluster_decides_the_same_namespace_preflight_does():
    doc = _evidence()
    for options in ({"namespace": "elsewhere"}, {}):
        _, namespace = core.preflight_cluster(doc, options)
        assert namespace == core.preflight(LOC_FACTS, options, doc)["namespace"]


def test_an_explicitly_asked_for_namespace_wins_over_both():
    """`doctor -n` is the one input the options cannot carry: the bundle's
    namespace is what it was generated for, and -n is what is being preflighted
    now."""
    doc = _evidence()
    _, namespace = core.preflight_cluster(doc, {"namespace": "elsewhere"},
                                          namespace="asked-for")
    assert namespace == "asked-for"


def test_no_evidence_at_all_is_an_empty_read_rather_than_a_refusal():
    """A `doctor` run against a cluster it can reach passes no file. Empty says
    exactly what doctor's own defaults say: no cluster data, no probes, no
    verdicts reached before the checks ran."""
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
    monkeypatch.setattr(core.livetest, "cli_tool",
                        lambda *a, **k: pytest.fail("preflight ran a cluster CLI"))
    assert core.preflight(LOC_FACTS, {"namespace": "blazemeter"}, _evidence())["checks"]


# -- evidence as the file it is -----------------------------------------------
# The collector's output is an artifact somebody sends, so a caller may name it
# rather than restate it. The two refusals below are the point of the helper:
# a file that could not be read and a file that was read and is not evidence
# have different remedies, and one message covering both hides which happened.

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
    """Pointing this at a facts file is the likely mistake. It parsed, so the
    refusal names the document rather than the read -- and must not arrive as
    the type that means the file could not be opened."""
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
    """The whole point of the numbers: a caller handed 2 of 5 with no count
    reads the account as having 2, and then reports a location that is there as
    missing."""
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
    """The listing endpoint is not the per-location read, and a payload that
    never carried a heartbeat cannot be evidence that an agent is dead --
    "could not tell" and "not reporting" want different next steps."""
    assert core.ship_reporting({"id": "s1", "state": "idle"}) is None
    assert core.ship_reporting({"id": "s1", "state": "idle",
                                "lastHeartBeat": 0}) is False
    assert core.ship_reporting({"id": "s1", "state": "idle",
                                "lastHeartBeat": time.time()}) is True


# -- where a key might be -----------------------------------------------------

def test_key_candidates_read_the_environment_when_asked(monkeypatch, tmp_path):
    """The one deliberate behaviour change in the extraction. This was a
    module-level list, so BZM_API_KEY_FILE froze at import -- and `ui --dev`
    sets that variable *after* startup for its reloader subprocess, which only
    worked because the subprocess re-imports. Read per call, it is right
    whether or not anything re-imports."""
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
    """The contract the server leans on. `api.BzmClient(path)` used to read the
    file and raise SystemExit, a BaseException that walks past every `except
    Exception` between here and the top of the process -- fine for a command,
    fatal for a server. This is the construction that does not, and since #95
    it is the only one: the constructor takes a keyword-only pair, so there is
    no exiting read left for a caller to reach by accident."""
    bad = tmp_path / "api-key.json"
    bad.write_text("not json")
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key(str(bad))
    assert "not valid JSON" in str(e.value)
    with pytest.raises(TypeError):
        api.BzmClient(str(bad))


# -- one construction for the client ------------------------------------------
#
# #92. Thirteen places built a client and three suites stood in at three
# different points, so `client_from_key` widened to take all three inputs a
# caller can have: a path, an id and secret, or nothing and the environment.
# #95 then moved every caller onto it and deleted the rest, which is what the
# guard at the end of this section keeps true.
#
# Every one of these asserts a CoreError and none asserts SystemExit -- an
# escaping SystemExit is not caught by pytest.raises(CoreError), so each of the
# refusal tests below fails rather than passes if an exiting constructor ever
# creeps back in underneath.

@pytest.fixture
def no_key_env(monkeypatch):
    """No key in the environment of whoever is running the suite.

    The developer running this very likely has BZM_API_KEY_FILE set (the MCP
    server wants it) and an api-key.json in the checkout, and a test that
    reads either would pass here and fail in CI, or worse the other way round.
    """
    for var in (core.KEY_FILE_ENV, core.KEY_ID_ENV, core.KEY_SECRET_ENV):
        monkeypatch.delenv(var, raising=False)


def credential_of(client):
    """The (id, secret) a built client will authenticate as.

    Past the underscore deliberately: which credential a client ended up
    holding is the whole question this section asks, and the only other way to
    ask it is an HTTP request, which does not belong in an offline suite.
    """
    return tuple(base64.b64decode(client._auth).decode().split(":", 1))


def test_a_client_is_built_from_a_key_file(no_key_env, tmp_path):
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "SHHH"}')
    client = core.client_from_key(str(key))
    assert isinstance(client, api.BzmClient)
    assert credential_of(client) == ("KID", "SHHH")


def test_a_client_is_built_from_an_id_and_secret(no_key_env, monkeypatch):
    """The UI's input, which arrives pasted into a form and has no file behind
    it. `key_set` used to write it to a temp file purely to have a path to hand
    to a constructor that only took one, then unlink it -- a secret on disk for
    the duration of a call, to satisfy an argument list. It passes the pair
    since #95, and this is the half that says the pair reaches no disk at all;
    tests/test_server.py has the half about the route."""
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
    """Not every unreadable file is a missing or malformed one: a path that is
    a directory (a `--api-key ~/.config/bzm-opl-gen` away), or one with the
    wrong mode, or a binary file, all reach `open()` and none of them raised
    ValueError. They came back as OSError and UnicodeDecodeError -- bare
    exceptions, straight past a route's `except CoreError` into a 500 with a
    traceback in it."""
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key(str(tmp_path))            # a directory
    assert str(tmp_path) in str(e.value)

    binary = tmp_path / "api-key.json"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    with pytest.raises(core.NotConfigured, match="not valid JSON"):
        core.client_from_key(str(binary))


def test_a_path_and_a_pasted_pair_together_are_refused(no_key_env, tmp_path):
    """Two credentials and no way to tell which one the caller meant. Taking
    the first silently is how a bundle gets generated against the wrong
    account and nothing anywhere says so."""
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "FILE", "secret": "s"}')
    with pytest.raises(core.BadRequest) as e:
        core.client_from_key(str(key), key_id="PASTED", secret="s")
    assert "not both" in str(e.value)


def test_half_a_pasted_pair_names_the_half_that_is_missing(no_key_env):
    """Not the "no API key anywhere" sentence: a caller that sent one of the
    two plainly has a key and is one field away, and telling it to go set an
    environment variable is an answer to a different question."""
    with pytest.raises(core.BadRequest, match="secret"):
        core.client_from_key(key_id="KID")
    with pytest.raises(core.BadRequest, match="id"):
        core.client_from_key(secret="SHHH")


def test_the_environment_is_the_last_place_looked(no_key_env, monkeypatch,
                                                  tmp_path):
    """Both env forms, and the argument beating each. Precedence matters to
    the UI more than anywhere: the server it runs in may have been started
    with a key in its environment, and a key typed into the page has to win."""
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
    """And does not go looking in the working directory -- see the comment at
    the refusal: a server's cwd is wherever a client launched it, quite
    possibly a customer's checkout holding an api-key.json that is theirs."""
    monkeypatch.setattr(core, "detect_keys", lambda: pytest.fail(
        "the construction discovered a key from the working directory"))
    with pytest.raises(core.NotConfigured) as e:
        core.client_from_key()
    for var in (core.KEY_FILE_ENV, core.KEY_ID_ENV, core.KEY_SECRET_ENV):
        assert var in str(e.value)


SEAM = f"core.{core.client_from_key.__name__}"


def _client_constructions(path):
    """Every `BzmClient(...)` a source file builds, by line.

    Parsed rather than grepped: the construction is argued about in half a
    dozen docstrings and comments here, and a guard that counted those would
    be turned off within a week. An `ast.Call` is a construction whichever way
    the class was reached -- `api.BzmClient(...)` or a bare `BzmClient(...)`
    after `from .api import BzmClient`.
    """
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
    """The contract half of #92-#93-#95, and the only thing that keeps it.

    Thirteen constructions became one, and nothing about the code stops a
    fourteenth: `api.BzmClient(credentials=...)` is two lines and works. What it
    costs is invisible at the site that writes it -- a caller building its own
    decides for itself what a missing key, a directory instead of a file, or a
    revoked credential means, and the three suites stand in at a point it does
    not pass through, so it is untested as well as inconsistent.

    Package sources only. Tests build clients directly on purpose: that is what
    a stand-in account is.
    """
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
    """What "one construction" is worth: the suites stand in at that one point
    by handing back this fake, so it has to answer as the real client does.

    Names first -- a method here that BzmClient does not have is a fake
    account answering a call no real one would -- then the parameters of the
    shared ones, because a fake whose argument names have drifted lets a core
    change that renames a keyword pass here and fail against BlazeMeter.
    Defaults are excluded on purpose: what a real client does when a field is
    omitted is its own behaviour, and this fake only records the call.
    """
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
    from bzm_opl_gen import generate as gen_mod
    assert core.option_defaults() == gen_mod.DEFAULT_OPTIONS


def test_option_docs_cover_every_option():
    from bzm_opl_gen import generate as gen_mod
    assert set(core.option_docs()) == set(gen_mod.DEFAULT_OPTIONS)


# -- the funcId vocabulary, and where it comes from ----------------------------
#
# #148. It was `core.FUNC_ID_LABELS`, five funcIds written by hand, and the
# account disagreed with it in both directions.


def test_the_keyless_vocabulary_is_the_funcids_this_tool_covers():
    """Asked with no account, the answer is the three this tool configures.

    Not a stand-in for the account's list and not a guess at it: the page
    fetches this on mount, before there is a key let alone an account, and
    manual entry never has an account at all. So there has to be an answer with
    nothing connected, and the only honest one is what this tool covers -- with
    BlazeMeter's own display names, so the words do not change when an account
    arrives and replaces it.
    """
    rows = core.func_ids()["choices"]
    assert [(r["id"], r["label"]) for r in rows] == [
        ("performance", "Performance"),
        ("functionalGui", "GUI Functional"),
        ("mockServices", "Service Virtualization")]
    assert all(r["covered"] for r in rows)


def test_the_vocabulary_says_whether_it_is_the_account_s_or_the_baseline():
    """The two answers are told apart on the answer, not by remembering which
    call was made (#160).

    A funcId in neither the vocabulary nor any parent's pins means two
    different things: read against the account it is *retired* -- BlazeMeter
    stopped serving it and locations created before that still carry it -- and
    read against the baseline it means nothing at all, because the baseline is
    three funcIds and every other one the account has is missing from it too.
    A reader that could not tell would have to guess, and this repo's oldest
    rule is that it must not have to.
    """
    assert core.func_ids()["source"] == "baseline"
    assert core.func_ids(FakeClient(), 291446)["source"] == "account"


def test_a_browser_pin_is_a_parameter_of_its_parent_not_a_funcid_of_its_own():
    """`functionalGui` carries its browser pins, and no pin is a row (#160).

    A location's `funcIds` mixes the two -- `functionalGui` arrives with
    `chrome:default` and `firefox:81` beside it -- and 43% of one account's 171
    locations carry at least one pin, 41 on the worst. Tested against the
    top-level vocabulary alone they all fall through as funcIds this tool has
    no options for, which is a true sentence about nothing: a pin says *which
    browser* GUI Functional uses, and there is no world in which it gets
    options of its own.

    Served under the parent for that reason, rather than flattened in beside
    it: a list of every pin loses which functionality each is a parameter of,
    and the row that knows is the one that has to say.
    """
    by_id = {r["id"]: r for r in core.func_ids(FakeClient(), 291446)["choices"]}
    assert by_id["functionalGui"]["sub_func_ids"] == [
        "chrome:default", "firefox:139", "safari:15"]
    assert not any(":" in f for f in by_id)
    # Every other row carries an empty list rather than leaving the key off: a
    # caller reading `.get("sub_func_ids", ...)` would have to invent what its
    # absence meant, and "this functionality has no pins" is a real answer.
    assert all(r["sub_func_ids"] == []
               for f, r in by_id.items() if f != "functionalGui")


def test_the_baseline_claims_no_pins_rather_than_guessing_at_them():
    """...and with no account there are none to serve. Only the account knows
    which funcIds are pins, so the baseline says so by carrying none -- and
    `source` is what stops that reading as "this account's GUI Functional has
    no browsers"."""
    rows = core.func_ids()["choices"]
    assert all(r["sub_func_ids"] == [] for r in rows)


def test_the_account_replaces_the_baseline_with_its_own_vocabulary():
    """...and the account's list is longer, differently named, and does not
    offer `functionalApi` at all -- which the hand-written table did."""
    client = FakeClient()
    rows = core.func_ids(client, 291446)["choices"]
    by_id = {r["id"]: r for r in rows}

    assert client.calls == [("functionalities", 291446)]
    assert "functionalApi" not in by_id
    assert by_id["functionalGui"]["label"] == "GUI Functional"
    assert by_id["tdm"]["label"] == "TDM Integration"


def test_the_vocabulary_says_which_funcids_this_tool_covers():
    """A funcId this tool has options for and one it can only name are both
    served, and the difference is on the row. Silence would read as coverage:
    a page that listed `delphix` beside `performance` with nothing to tell them
    apart is a page offering to configure something it cannot."""
    by_id = {r["id"]: r for r in core.func_ids(FakeClient(), 291446)["choices"]}
    assert [f for f, r in by_id.items() if r["covered"]] == [
        "performance", "mockServices", "functionalGui"]
    for f in ("proxyRecorder", "tdm", "dataPublisher", "delphix",
              "secretsPrivateVault", "enableSecretsToggle"):
        assert by_id[f]["covered"] is False


def test_an_unreadable_account_is_not_an_account_with_three_functionalities():
    """The baseline is the keyless answer, never a fallback for a read that
    failed. Falling back would answer "this account offers exactly what we
    cover" to a 401 -- could-not-read wearing there-is-nothing-else, about the
    one question whose whole point is that the account knows better."""
    with pytest.raises(core.CoreError):
        core.func_ids(ExpiredClient(), 291446)


def test_a_functionality_is_one_funcid_under_blazemeter_s_own_name():
    """One entry per covered funcId, `id` equal to the funcId (#149).

    It was two entries and `performance` claimed four funcIds, so its label had
    to name all of them at once -- "Performance & functional testing", printed
    over a location whose only funcId is `performance`. A list of funcIds per
    functionality is a translation table between this tool's ids and
    BlazeMeter's, and the 1:1 mapping exists so that there is not one: the
    funcId a location carries *is* the id, in both directions, with nothing to
    look up.
    """
    served = core.functionalities()
    assert [(f["id"], f["label"]) for f in served] == [
        ("performance", "Performance"),
        ("functionalGui", "GUI Functional"),
        ("mockServices", "Service Virtualization")]
    # The labels are the account's own words (from
    # GET /accounts/{id}/functionalities), so a customer reading their own
    # location settings does not have to translate -- and nothing here has a
    # `func_ids` to keep them apart from.
    assert not any("func_ids" in f for f in served)


def test_a_covered_funcid_and_a_functionality_are_one_table():
    """`covered` on the funcId vocabulary and having a card on the configure
    step are the same fact, so they are the same declaration. Kept twice they
    are free to disagree about the one thing a row exists to say -- and the row
    that says it is the one telling a funcId this tool configures from a funcId
    it can only name."""
    ids = [f["id"] for f in core.functionalities()]
    assert [r["id"] for r in core.func_ids()["choices"]] == ids
    assert all(r["covered"] for r in core.func_ids()["choices"])
    # ...and with an account, whose vocabulary is longer, the covered rows are
    # still exactly the functionalities.
    rows = core.func_ids(FakeClient(), 291446)["choices"]
    assert {r["id"] for r in rows if r["covered"]} == set(ids)


def test_every_functionality_names_a_funcid_the_facts_layer_models():
    """A functionality whose funcId the facts layer does not model would select
    no images for the bundle it declares -- which is the one thing manual entry
    reads a declaration for.

    The reverse is deliberately *not* asserted, and #149 is where it stopped
    holding: `functionalApi` and `proxyRecorder` are modelled here and covered
    by nothing, because BlazeMeter has retired one and this tool has no options
    for either. A location carrying only those claims no functionality, which
    the page reads as nobody having said -- and names them, rather than folding
    them into a card whose label would then have to mean four things."""
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
    """The sentinel for generate.TOKEN_FIELDS.

    A template that renames its key, or a fourth file that starts carrying the
    token, would return verbatim with `redacted_fields: 0` -- which reads to a
    caller as "no secret in this file", the quiet direction. This fails instead.
    """
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
    """`existing_auth_token` reads the bundle back rather than re-fetching,
    because fetching mints a new one. It only looked in the two manifest files,
    so a chart bundle re-fetched every time -- rotating the token of whatever
    was running."""
    from bzm_opl_gen import generate as gen
    files = core.generate_bundle(
        dict(FACTS), {"namespace": "ns1", "output_format": "helm",
                      "auth_token": "TOKENVALUE"}, client=None)
    gen.write(files, str(tmp_path))
    assert gen.existing_auth_token(str(tmp_path)) == "TOKENVALUE"


# -- changing a location's settings after the fact ----------------------------
# The case: the location and agent are set up, and the virtual users per engine
# turns out to be 1,000 rather than 500. None of these four values is in a
# manifest, so this is the whole of that change -- nothing to regenerate.

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
    """The failure this guards: POST /private-locations accepts
    threadsPerEngine and drops it, and the location then 403s every test start.
    A UI that echoed the request back would show the number the user typed
    while the account held the old one."""
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
    """Unchanged because it already matched is not the same as refused, and the
    two must not both come back as `ignored` -- one is a no-op, the other is an
    account that would not take the value."""
    client = FakeClient(harbor=_loc())
    out = core.update_location(client, "h1", threads_per_engine=500)
    assert out["changed"] == {} and out["ignored"] == []


# -- what an account can generate ---------------------------------------------
# The numbers here were settled by a live run rather than by reading: on a
# location with 2 agents, slots=1 and threadsPerEngine=50, a 100 virtual user
# test started and ran on two engines, one per agent. Asking for three engines
# allocated two. 101 virtual users also started, packed onto the same two -- so
# the engine count is enforced and the virtual users per engine is a rating.

def _cap_loc(name, ships=1, slots=1, tpe=50, workspaces=(1,), **over):
    loc = {"id": f"h-{name}", "name": name, "slots": slots,
           "threadsPerEngine": tpe, "funcIds": ["performance"],
           "workspacesId": list(workspaces),
           "ships": [{"id": f"s{i}", "lastHeartBeat": 1} for i in range(ships)]}
    loc.update(over)
    return loc


def test_rated_capacity_is_agents_times_slots_times_threads():
    """The Bens Linux case, which is the one that was measured: two agents at
    one engine each, fifty virtual users an engine, so a hundred."""
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
    """A locations listing need not carry `lastHeartBeat`, and ship_reporting
    answers None there. Counting that as "not reporting" would print a claim
    about an agent nothing had looked at -- the same "could not read" versus
    "there is nothing there" collapse this package keeps making.

    Both are in the rating either way: the location advertises the agents, and
    whether one is up today is a different question from what it is sized for.
    """
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
