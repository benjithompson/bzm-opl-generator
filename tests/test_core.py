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

import io
import json
import os
import time
import zipfile

import pytest

from bzm_opl_gen import api, core, generate as gen
from test_generate import FACTS


class FakeClient:
    """Enough BzmClient to exercise the paths that reach for one.

    Shared with tests/test_mcp.py rather than written twice: both suites drive
    the same core functions, and two fakes answering `private_location`
    differently would let the two layers disagree about what an account looks
    like. Methods no core test calls are here for that reason.
    """

    def __init__(self, token="TOKEN-FROM-API", harbor=None, locations=None):
        self._token = token
        self._harbor = harbor if harbor is not None else {}
        self._locations = locations
        self.calls = []

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        return self._token

    def private_location(self, harbor_id):
        self.calls.append(("private_location", harbor_id))
        return self._harbor

    def user(self):
        return {"email": "se@example.com", "displayName": "SE",
                "defaultProject": {"accountId": 7}}

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


# -- the split itself ---------------------------------------------------------

def _imports(path):
    """Every top-level name a file imports, read from the parsed source.

    Parsed rather than taken from sys.modules: another test module in the same
    session imports fastapi, so by the time these run it is loaded whatever
    core does.
    """
    import ast
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


def test_a_bundle_for_another_ship_is_not_reused_and_says_so(tmp_path):
    """Reusing across ships writes another location's credential into this
    bundle, and the agent then sits at 0/1 with a token that was never its own.
    Loud rather than silent: the directory is the only place the mistake shows."""
    facts = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                               dict(FACTS["ships"][0], id="b2")])
    gen.write(gen.generate(facts, {"namespace": "ns1", "ship_id": "b1",
                                   "auth_token": "B1TOKEN"}), str(tmp_path))
    opts = {"namespace": "ns1", "ship_id": "b2"}
    src = core.resolve_auth_token(facts, opts, out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_PLACEHOLDER
    assert "b1" in src.message and "b2" in src.message
    assert "B1TOKEN" not in opts.get("auth_token", "")


def test_a_bundle_whose_ship_cannot_be_confirmed_is_not_reused(tmp_path):
    """An older bundle, or a hand-assembled directory: there is a token in it
    and nothing that says whose. Not the same as a mismatch -- the remedy is to
    pass the token rather than to look at another directory -- but it is equally
    not a reuse."""
    _bundle(tmp_path, auth_token="TOKENVALUE")
    os.remove(os.path.join(str(tmp_path), gen.PROFILE_FILE))
    opts = {"namespace": "ns1"}
    src = core.resolve_auth_token(FACTS, opts, out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_PLACEHOLDER
    assert "auth_token" not in opts
    assert gen.PROFILE_FILE in src.message


def test_no_token_anywhere_says_where_a_real_one_comes_from(tmp_path):
    """The placeholder is a fine bundle to read and an unusable one to apply,
    so the branch that produces it has to name both sources of a real token.
    The kubectl is *named*, never run: nothing here reads a cluster."""
    src = core.resolve_auth_token(FACTS, {"namespace": "ns1"},
                                  out_dir=str(tmp_path))
    assert src.branch == core.TOKEN_PLACEHOLDER
    assert "create-ship" in src.message
    assert "kubectl -n ns1 get secret" in src.message
    assert "base64 -d" in src.message


def test_the_placeholder_branch_needs_no_output_directory():
    """The MCP and UI callers generate before they have anywhere to write."""
    assert (core.resolve_auth_token(FACTS, {"namespace": "ns1"}).branch
            == core.TOKEN_PLACEHOLDER)


def test_generate_needs_no_client_at_all():
    files = core.generate_bundle(FACTS, {"namespace": "ns1"}, client=None)
    assert "bzm_deployment.yaml" in files


def test_generate_refuses_options_it_cannot_render():
    with pytest.raises(core.BadRequest):
        core.generate_bundle(FACTS, {"service_account_name": ""}, client=None)


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
    z = zipfile.ZipFile(io.BytesIO(core.zip_bundle(files)))
    info = z.getinfo("bzm-opl/bzm-opl-image-mirror.sh")
    assert info.external_attr >> 16 & 0o111


def test_zip_keeps_the_chart_directory():
    """Names carry directories in the helm format, and a flattened archive is
    a pile of files no helm command can install."""
    files = core.generate_bundle(
        FACTS, {"namespace": "ns1", "output_format": "helm"}, client=None)
    names = zipfile.ZipFile(io.BytesIO(core.zip_bundle(files))).namelist()
    assert "bzm-opl/helm/templates/deployment.yaml" in names


def test_zip_filename_names_the_namespace():
    assert core.zip_filename({"namespace": "ns1"}) == "bzm-opl-ns1.zip"
    assert core.zip_filename({}) == "bzm-opl-blazemeter.zip"


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

from test_cluster_evidence import _evidence          # noqa: E402
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


def test_every_modelled_func_id_belongs_to_a_feature():
    """A funcId the facts layer models but no feature claims would leave a
    location carrying only that one with no feature to start on. The reverse is
    deliberately allowed: a feature may claim a funcId that needs no images of
    its own (tdm and delphix are already in that position), and the funcIds the
    tool does not model at all stay unclaimed -- the selector reads those as no
    signal rather than as an error."""
    from bzm_opl_gen import facts as facts_mod
    claimed = {f for feat in core.FEATURES for f in feat["func_ids"]}
    assert set(facts_mod.CATEGORY_BY_FUNC) <= claimed
    assert "tdm" not in claimed


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
