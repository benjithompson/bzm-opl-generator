"""The MCP server, driven over the SDK's in-memory transport.

A real client talking to the real server, with no subprocess and no socket, so
this stays inside the ~1s offline suite. What it is checking is mostly not
"does the call work" -- core is tested next door -- but the things only this
layer decides: what a model is told, what it is allowed to do, and what must
never come back in a response.

Nothing here skips. `mcp` is in the `[test]` extra and CI asserts it imports,
for the same reason fastapi is: a suite that skips when a dependency is missing
reports a clean pass having tested nothing.
"""

import json
import os
import time

import anyio
import mcp
import pytest

from bzm_opl_gen import core, generate as gen_mod, mcp_server
from test_core import FakeClient, RefusingClient
from test_generate import FACTS

# The one ship in FACTS, and the ship a rotation therefore names.
SHIP = FACTS["ships"][0]["id"]

# What each tool promises a client about side effects. Asserted as a whole
# table rather than a few hand-picked cells: a client that asks before running
# something reads every row, and the interesting property is how they compare --
# that the read-only ones really are, and that the two that can change a
# customer's account say so.
EXPECTED_ANNOTATIONS = {
    "opl_location":  {"read_only": False, "destructive": True},
    "opl_facts":     {"read_only": True,  "destructive": False},
    "opl_bundle":    {"read_only": False, "destructive": True},
    "opl_plan":      {"read_only": True,  "destructive": False},
    "opl_preflight": {"read_only": True,  "destructive": False},
    "opl_agent":     {"read_only": False, "destructive": False},
}

_SERVER = []      # the module's one server, installed by the fixture below


# Building a server costs ~5ms of pydantic schema generation and buys nothing
# per test: both env gates are read when an action runs, not when the server is
# built, so a shared instance sees a monkeypatched variable exactly as a fresh
# one would -- which test_the_gates_are_read_when_called_not_when_built is what
# holds true.
@pytest.fixture(scope="module")
def server():
    return mcp_server.build()


@pytest.fixture(autouse=True)
def _shared_server(server):
    _SERVER[:] = [server]


def call(tool, action, args=None):
    """One tool call, as a client makes it."""
    async def go():
        async with mcp.Client(_SERVER[0]) as c:
            return await c.call_tool(tool, {"action": action, "args": args or {}})
    return anyio.run(go)


def ok(tool, action, args=None):
    """A call that must have succeeded, with its JSON body parsed."""
    r = call(tool, action, args)
    assert not r.is_error, r.content[0].text
    return json.loads(r.content[0].text)


def err(tool, action, args=None):
    r = call(tool, action, args)
    assert r.is_error, f"expected a refusal, got: {r.content[0].text[:200]}"
    return r.content[0].text


def listing():
    async def go():
        async with mcp.Client(_SERVER[0]) as c:
            return {"instructions": c.instructions,
                    "tools": {t.name: t for t in (await c.list_tools()).tools}}
    return anyio.run(go)


@pytest.fixture
def fake_account(monkeypatch):
    c = FakeClient(token="SECRET-TOKEN-VALUE",
                   harbor={"id": "h1", "name": "loc",
                           "ships": [{"id": "s1", "state": "idle",
                                      "installedVersion": "3.7.55",
                                      "lastHeartBeat": 0}]})
    monkeypatch.setattr(core, "client_from_key", lambda *a, **k: c)
    return c


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    """Nothing here may pick up the developer's own api-key.json. A test that
    silently used a real key would talk to a real account."""
    for var in (core.KEY_FILE_ENV, core.KEY_ID_ENV, core.KEY_SECRET_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(core, "detect_keys", lambda: [])


# -- what a session is handed -------------------------------------------------

def test_the_tools_are_the_agreed_surface():
    assert sorted(listing()["tools"]) == sorted(EXPECTED_ANNOTATIONS)


def test_every_tool_enumerates_its_actions_in_the_schema():
    """The action list is machine-readable, not prose: a wrong one is then
    refused by the client's own validation, naming the valid ones, instead of
    arriving here to be guessed at.

    A one-action tool schematises as `const` rather than `enum` -- pydantic
    collapses a single-member Literal -- and that is the same guarantee said in
    the narrower way, not a tool that failed to declare itself. Both are
    accepted; neither being present is the failure.
    """
    for name, tool in listing()["tools"].items():
        action = tool.input_schema["properties"]["action"]
        assert action.get("enum") or action.get("const"), \
            f"{name} does not enumerate its actions"


def test_every_tool_says_whether_it_changes_anything():
    """Annotations are how a client decides what to confirm before running. A
    tool with none is treated as unknown, which in practice means treated as
    safe -- so the whole table is asserted, not a few cells of it."""
    tools = listing()["tools"]
    assert set(tools) == set(EXPECTED_ANNOTATIONS)
    got = {name: {"read_only": t.annotations and t.annotations.read_only_hint,
                  "destructive": t.annotations and t.annotations.destructive_hint}
           for name, t in tools.items()}
    assert got == EXPECTED_ANNOTATIONS


def test_the_instructions_name_the_sibling_servers():
    """A session that has them should use them rather than this server's
    guesses; one that does not should be told they exist."""
    text = listing()["instructions"]
    assert "blazemeter" in text.lower()
    for name in ("blazemeter_tests", "virtual_services"):
        assert name in text, f"{name} not named in the instructions"


def test_the_instructions_forbid_inventing_a_sibling_s_answer():
    """The failure this prevents is a plausible run report for a test that was
    never started -- unfalsifiable unless it was forbidden up front."""
    text = listing()["instructions"].lower()
    assert "simulat" in text or "invent" in text or "fabricat" in text


def test_the_instructions_say_where_the_docs_are():
    assert "bzm-opl://" in listing()["instructions"]


# -- credentials --------------------------------------------------------------

def test_a_missing_key_says_which_variable_to_set():
    """And does not take the server down with it: the file-reading constructor
    raises SystemExit, which no `except Exception` in the way would catch."""
    text = err("opl_location", "whoami")
    assert core.KEY_FILE_ENV in text and core.KEY_ID_ENV in text


def test_the_server_survives_a_missing_key():
    assert err("opl_location", "whoami")
    assert ok("opl_bundle", "options")["platform"]["default"] == "openshift"


def test_a_credential_passed_as_an_option_is_refused(fake_account, tmp_path):
    """The rule, enforced rather than stated. `args` is an open object, so a
    schema check could never fail -- and `auth_token` is a real generate option
    the UI sets, so it is not an impossible argument, just one that must not
    arrive this way."""
    text = err("opl_bundle", "generate",
               {"facts": FACTS, "out_dir": str(tmp_path),
                "options": {"auth_token": "PASTED-BY-A-MODEL"}})
    assert "credential" in text and "reveal_token" in text
    assert not list(tmp_path.iterdir()), "it wrote the bundle anyway"


def test_the_refused_set_is_the_generator_s_own():
    """Not a list restated here: generate.SECRET_OPTIONS is what profile.json
    omits, and the two must mean the same thing."""
    assert gen_mod.SECRET_OPTIONS
    for name in gen_mod.SECRET_OPTIONS:
        with pytest.raises(core.BadRequest):
            mcp_server._no_secrets({name: "x"})


def test_only_a_path_may_name_a_key(fake_account, tmp_path):
    """api_key_file names a file to read; it is not the credential itself."""
    key = tmp_path / "k.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    # Accepted as an argument, and it is a path -- the fixture stands in for
    # what client_from_key would build from it.
    assert ok("opl_location", "whoami", {"api_key_file": str(key)})["email"]


# -- the token, which is the one thing that must not leak ---------------------

def test_generating_a_bundle_never_returns_the_token(fake_account, tmp_path):
    """The Secret is written to disk with the token in it. The *response* is
    file names and sizes, so a token cannot end up in a transcript.

    `rotate_token` is what puts a real token in the bundle at all now, so it is
    what this has to pass to have anything to leak.
    """
    body = ok("opl_bundle", "generate",
              {"facts": FACTS, "out_dir": str(tmp_path), "rotate_token": True,
               "options": {"namespace": "ns1"}})
    assert "SECRET-TOKEN-VALUE" not in json.dumps(body)
    assert "SECRET-TOKEN-VALUE" in (tmp_path / "bzm_secret.yaml").read_text()


def test_the_returned_profile_carries_no_token(fake_account, tmp_path):
    body = ok("opl_bundle", "generate",
              {"facts": FACTS, "out_dir": str(tmp_path)})
    assert "auth_token" not in body["profile"]


def test_only_reveal_token_reveals_the_token(fake_account):
    body = ok("opl_location", "reveal_token", {"harbor_id": "h1", "ship_id": "s1"})
    assert body["auth_token"] == "SECRET-TOKEN-VALUE"
    # And says what it just did, because it rotated the previous one.
    assert "invalidated" in body["warning"]


def test_a_refused_token_reaches_the_session_with_a_way_forward(monkeypatch):
    """This is the caller the raw 403 stranded: a session with no checkout to
    read, whose whole view of the failure is the text of the tool error. So the
    refusal has to carry the alternative itself -- ask for the token, and pass
    it as an option -- or the model has nowhere to go."""
    monkeypatch.setattr(core, "client_from_key",
                        lambda *a, **k: RefusingClient())
    text = err("opl_location", "reveal_token",
               {"harbor_id": "h1", "ship_id": "s1"})
    assert "could not be issued" in text
    assert "auth_token" in text and "BlazeMeter UI" in text


def test_reading_the_secret_back_does_not_hand_over_the_token(fake_account,
                                                             tmp_path):
    """`read bzm_secret.yaml` was a second, quieter way to get the credential:
    it does not look like asking for one, which is exactly why reveal_token is
    a whole action. The file is readable, the value is not."""
    ok("opl_bundle", "generate",
       {"facts": FACTS, "out_dir": str(tmp_path), "rotate_token": True})
    body = ok("opl_bundle", "read",
              {"out_dir": str(tmp_path), "name": "bzm_secret.yaml"})
    assert "SECRET-TOKEN-VALUE" not in body["content"]
    assert "AUTH_TOKEN" in body["content"]        # the shape still reads
    assert body["redacted_fields"] == 1
    assert "reveal_token" in body["note"]
    # ...and it is still whole on disk, which is what gets applied.
    assert "SECRET-TOKEN-VALUE" in (tmp_path / "bzm_secret.yaml").read_text()


def test_the_chart_overlay_hides_its_token_too(fake_account, tmp_path):
    """The helm format carries the token in bzm-opl-values.yaml instead, under
    a different key -- so redacting only the Secret would have missed it."""
    ok("opl_bundle", "generate",
       {"facts": FACTS, "out_dir": str(tmp_path), "rotate_token": True,
        "options": {"output_format": "helm"}})
    body = ok("opl_bundle", "read",
              {"out_dir": str(tmp_path), "name": "bzm-opl-values.yaml"})
    assert "SECRET-TOKEN-VALUE" not in body["content"]
    assert body["redacted_fields"] == 1


def test_a_file_with_no_token_is_returned_untouched(fake_account, tmp_path):
    ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    body = ok("opl_bundle", "read",
              {"out_dir": str(tmp_path), "name": "bzm_deployment.yaml"})
    assert body["redacted_fields"] == 0 and "note" not in body
    assert "kind: Deployment" in body["content"]


# -- and the one thing that must not happen as a side effect ------------------
# #64 on this surface. The harm is not secrecy -- crane logs the token in
# plaintext -- it is that minting *revokes* the previous one, and a session with
# no terminal to read gets no hint that it happened. So the default must be the
# branch that mints nothing, and the argument that does is named for the effect.

def test_generate_mints_nothing_unless_a_session_asks_to_rotate(fake_account,
                                                                tmp_path):
    """Zero calls to the token endpoint, counted rather than inferred. Holding
    an API key is no longer permission to replace a running agent's credential;
    the bundle comes out with the placeholder in it and says so."""
    body = ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    assert fake_account.calls == []
    assert (gen_mod.DEFAULT_OPTIONS["auth_token"]
            in (tmp_path / "bzm_secret.yaml").read_text())
    assert body["token_source"]["branch"] == core.TOKEN_PLACEHOLDER
    # And where a real one comes from, since this bundle cannot be applied yet.
    assert "create-ship" in body["token_source"]["message"]


def test_rotating_names_the_ship_whose_credential_it_replaced(fake_account,
                                                              tmp_path):
    """The explicit ask on the issue: the one action that silently revokes a
    running agent's credential was the one that said least about it, answering
    `warnings: []`. The token still never travels -- the ship does."""
    body = ok("opl_bundle", "generate",
              {"facts": FACTS, "out_dir": str(tmp_path), "rotate_token": True})
    assert [c[0] for c in fake_account.calls] == ["auth_token"]
    assert "SECRET-TOKEN-VALUE" not in json.dumps(body)
    assert body["token_source"]["branch"] == core.TOKEN_ROTATED
    assert body["token_source"]["ship_id"] == SHIP
    # In the warnings too, not only in a field a reader has to know to look at.
    assert any(SHIP in w and "0/1" in w for w in body["warnings"]), body["warnings"]


def test_generating_twice_into_the_same_directory_mints_once(fake_account,
                                                            tmp_path):
    """The branch this surface could not reach at all: regenerating into a
    directory that already holds this ship's bundle reads that token back. One
    mint across two calls, and the second bundle is byte-identical -- so an
    agent deployed from the first keeps working."""
    ok("opl_bundle", "generate",
       {"facts": FACTS, "out_dir": str(tmp_path), "rotate_token": True})
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    body = ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    assert len(fake_account.calls) == 1, fake_account.calls
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == first
    assert body["token_source"]["branch"] == core.TOKEN_REUSED
    assert "SECRET-TOKEN-VALUE" not in json.dumps(body)


def test_the_old_fetch_token_argument_is_refused_rather_than_ignored(
        fake_account, tmp_path):
    """A session working from a cached description would otherwise send
    `fetch_token: true`, get a placeholder bundle, and have been told nothing.
    Refused by name, because the caller meant to mint and needs to know the
    word changed -- and refused before anything is written or issued."""
    text = err("opl_bundle", "generate",
               {"facts": FACTS, "out_dir": str(tmp_path), "fetch_token": True})
    assert "fetch_token" in text and "rotate_token" in text
    assert fake_account.calls == []
    assert not list(tmp_path.iterdir()), "it wrote the bundle anyway"


def test_a_relative_out_dir_is_refused_before_anything_is_issued(fake_account):
    """The refusal already existed, at the write -- by which point the mint had
    happened and a running agent was already broken, for a mistake in an argument
    that had nothing to do with the credential. Checked first now."""
    text = err("opl_bundle", "generate",
               {"facts": FACTS, "out_dir": "out", "rotate_token": True})
    assert "absolute" in text
    assert fake_account.calls == []


def test_the_generate_description_names_the_argument_that_rotates():
    """The argument name is the whole warning a model gets -- it reads JSON, not
    a terminal -- so the description has to carry the new one and not the old."""
    text = listing()["tools"]["opl_bundle"].description
    assert "rotate_token" in text and "fetch_token" not in text


# -- listing an account somebody actually has ---------------------------------
# The account this was built against holds two locations. A customer's holds
# 171 with 221 ships between them, which came back as 84,779 characters: past
# the caller's tool-result ceiling, truncated to a file, never read -- and step
# 1 of the documented path is the one that broke, so everything after it was
# blocked too (#78).

LOCATION_COUNT, SHIP_COUNT = 171, 221

# What a client will accept in one tool result, in characters. Well under any
# real ceiling on purpose: this is a listing a session reads and then keeps in
# context while it does five more calls.
RESULT_CEILING = 25_000


def _big_account():
    """171 locations, 221 ships, with ids and names the length real ones are --
    most of the 84,779 characters was per-ship detail on locations the caller
    was never going to pick."""
    locs = []
    for i in range(LOCATION_COUNT):
        # The first 50 have two agents each, which is what makes 221.
        n_ships = 2 if i < SHIP_COUNT - LOCATION_COUNT else 1
        locs.append({
            "id": f"harbor-6{i:023d}",
            "name": f"acme-{'eu-west' if i % 2 else 'us-east'}-perf-{i:03d}",
            "slots": 10, "funcIds": ["performance"],
            "workspacesId": [123456],
            "ships": [{"id": f"ship-7{i:02d}{j:021d}",
                       "name": f"agent-{i:03d}-{j}", "state": "idle",
                       "installedVersion": "3.7.55",
                       "lastHeartBeat": 1700000000 if j else 0}
                      for j in range(n_ships)],
        })
    return locs


@pytest.fixture
def big_account(monkeypatch):
    c = FakeClient(locations=_big_account())
    monkeypatch.setattr(core, "client_from_key", lambda *a, **k: c)
    return c


def test_a_real_account_s_listing_fits_in_a_result_and_says_what_it_left_out(
        big_account):
    """Both halves together, because either alone is a bug: a response that
    fits by stopping early reads as the whole account, and acting on "that
    location does not exist" when it was merely omitted is worse than the size
    problem this fixes."""
    r = call("opl_location", "list")
    assert not r.is_error, r.content[0].text
    text = r.content[0].text
    assert len(text) < RESULT_CEILING, f"{len(text)} characters"

    body = json.loads(text)
    assert body["total"] == LOCATION_COUNT
    assert body["returned"] == len(body["locations"]) < LOCATION_COUNT
    assert (body["omitted_by_limit"]
            == LOCATION_COUNT - body["returned"]) > 0
    # And in prose as well as in a field, because the number is the thing a
    # session has to act on rather than one key of many.
    assert str(body["omitted_by_limit"]) in body["note"]


def test_a_listing_entry_carries_what_choosing_a_location_needs(big_account):
    """id to pass on, name to recognise, funcIds to know a bundle can be
    generated at all, and whether there is an agent there and it is alive.
    Not the ships themselves -- 221 of those is the size problem."""
    entry = ok("opl_location", "list")["locations"][0]
    assert entry["harbor_id"].startswith("harbor-")
    assert entry["name"].startswith("acme-")
    assert entry["func_ids"] == ["performance"] and entry["slots"] == 10
    assert entry["ship_count"] == 2
    assert entry["ships_reporting"] == 0     # both heartbeats are from 2023
    assert not isinstance(entry.get("ships"), list)


def test_a_listing_with_no_heartbeats_reports_unknown_rather_than_none_alive(
        fake_account):
    """A listing payload without `lastHeartBeat` cannot say an agent is dead.
    Reported as null, so nobody redeploys an agent that was working."""
    entry = ok("opl_location", "list")["locations"][0]
    assert entry["ship_count"] == 1 and entry["ships_reporting"] is None


def test_an_explicit_null_limit_still_gets_the_default_cap(big_account):
    """`{"limit": null}` is an ordinary way for a client to say "unset".

    `args.get("limit", DEFAULT)` only defaults an *absent* key, so an explicit
    null arrived as `limit=None`, which core reads as "no cap" -- handing back
    the whole 171-location account this tool exists to keep out of a result.
    """
    body = ok("opl_location", "list", {"limit": None})
    assert body["returned"] == core.DEFAULT_LOCATION_LIMIT
    assert body["omitted_by_limit"] == 171 - core.DEFAULT_LOCATION_LIMIT


def test_a_limit_that_is_not_a_number_is_refused_not_a_crash(big_account):
    """A model writing `"10"` is likelier than a model writing 10.

    Compared against 1, a string raises TypeError, which is not a CoreError and
    so escapes `_answer` as an SDK internal error rather than a sentence."""
    assert "limit" in err("opl_location", "list", {"limit": "10"}).lower()
    assert "limit" in err("opl_location", "list", {"limit": 1.5}).lower()


def test_one_live_agent_is_not_hidden_by_one_of_unknown_state(monkeypatch):
    """`ships_reporting` went null if *any* ship lacked a heartbeat, so a
    location with a working agent and a heartbeat-less record reported wholly
    unknown -- losing the "one of two" signal the count exists to give."""
    c = FakeClient(locations=[{"id": "h9", "name": "mixed", "slots": 2,
                               "funcIds": ["performance"], "ships": [
        {"id": "live", "state": "idle", "lastHeartBeat": int(time.time())},
        {"id": "nohb", "state": "idle"}]}])
    monkeypatch.setattr(core, "client_from_key", lambda *a, **k: c)
    entry = ok("opl_location", "list")["locations"][0]
    assert entry["ship_count"] == 2
    assert entry["ships_reporting"] == 1, "the live agent must still show"
    assert entry["ships_unknown"] == 1, "and the one nobody can vouch for"


def test_never_reported_and_gone_quiet_get_different_next_steps(monkeypatch):
    """Both answer `online: false`, so the pair with `heartbeat_age_s` is what
    keeps them apart -- and only a test keeps that structural. Redeploying is
    the answer to one and not the other."""
    def status_of(ship):
        c = FakeClient(harbor={"id": "h1", "name": "loc", "ships": [ship]})
        monkeypatch.setattr(core, "client_from_key", lambda *a, **k: c)
        return ok("opl_agent", "status", {"harbor_id": "h1", "ship_id": "s1"})

    never = status_of({"id": "s1", "state": "created"})
    quiet = status_of({"id": "s1", "state": "idle", "lastHeartBeat": 1})
    assert never["online"] is False and quiet["online"] is False
    assert never["heartbeat_age_s"] is None and quiet["heartbeat_age_s"]
    assert "not reached BlazeMeter" in " ".join(never["next"])
    assert "gone quiet" in " ".join(quiet["next"])


def test_narrowing_by_name_counts_what_the_filter_removed(big_account):
    """Somebody who knows the name should not receive 170 others -- and must
    still be told the account holds them."""
    body = ok("opl_location", "list", {"name_contains": "eu-west"})
    assert body["locations"], "the filter matched nothing"
    assert all("eu-west" in l["name"] for l in body["locations"])
    assert body["total"] == LOCATION_COUNT
    assert body["omitted_by_filter"] == LOCATION_COUNT - body["matched"]
    assert "eu-west" in body["note"]


def test_a_caller_can_raise_the_cap_and_is_told_when_nothing_is_missing(
        big_account):
    body = ok("opl_location", "list", {"limit": LOCATION_COUNT})
    assert body["returned"] == LOCATION_COUNT
    assert body["omitted_by_limit"] == 0 and body["omitted_by_filter"] == 0
    assert "note" not in body, "announced an omission that did not happen"


def test_a_cap_that_returns_nothing_is_refused(big_account):
    assert "limit" in err("opl_location", "list", {"limit": 0})


def test_creating_a_location_still_answers_in_full(fake_account):
    """`create` returns one location, so it has no size problem, and the detail
    is the confirmation that what exists is what was asked for. It happens to
    have shared a helper with the listing -- compacting that must not reach
    here, hence the shape is pinned rather than left to the helper."""
    body = ok("opl_location", "create", {"name": "scratch", "account_id": 7,
                                         "workspace_id": 99})
    loc = body["location"]
    assert loc["harbor_id"] == "h9" and loc["name"] == "scratch"
    assert loc["func_ids"] == ["performance"] and loc["slots"] == 1
    # The per-ship list itself, not a count of one.
    assert loc["ships"] == [] and "ship_count" not in loc


def test_a_location_a_test_cannot_start_on_says_so_here_too(fake_account):
    """The warning was the terminal's alone, so a session that created a
    location this way got one that 403s every start with nothing anywhere
    saying why. It comes from core.create_location now, like the location
    itself, and rides beside the summary rather than inside it -- present only
    when it applies, as the listing's `note` is."""
    body = ok("opl_location", "create", {"name": "scratch", "account_id": 7,
                                         "workspace_id": 99})
    assert "403" in body["warning"]
    assert "Not enough available resources" in body["warning"]


def test_the_listing_names_the_account_it_actually_listed(fake_account):
    """Neither id given means the key's default account, and that default is
    easy to be wrong about -- the key to hand defaults to a two-location
    account while the one wanted holds 171. An empty list has to be
    distinguishable from the wrong account being read."""
    assert ok("opl_location", "list")["account_id"] == 7


def test_per_ship_detail_is_reachable_for_the_location_that_was_picked(
        fake_account):
    """What `list` stopped paying for on all 171. `show` is one location, so
    the detail costs what it is worth."""
    body = ok("opl_location", "show", {"harbor_id": "h1"})
    ships = body["location"]["ships"]
    assert [s["ship_id"] for s in ships] == ["s1"]
    assert ships[0]["state"] == "idle"


# -- the bundle on disk -------------------------------------------------------

def test_generate_returns_names_and_sizes_not_yaml(fake_account, tmp_path):
    body = ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    assert body["out_dir"] == str(tmp_path)
    assert {f["name"] for f in body["files"]} >= {"bzm_deployment.yaml", "README.md"}
    assert all(isinstance(f["bytes"], int) and f["bytes"] > 0 for f in body["files"])
    assert "apiVersion" not in json.dumps(body)


def test_generate_refuses_a_relative_out_dir(fake_account):
    """A server's working directory is whatever launched it, so a relative path
    puts the bundle somewhere the caller cannot then describe."""
    text = err("opl_bundle", "generate", {"facts": FACTS, "out_dir": "out"})
    assert "absolute" in text


def test_read_refuses_a_path_out_of_the_bundle(fake_account, tmp_path):
    ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    text = err("opl_bundle", "read",
               {"out_dir": str(tmp_path), "name": "../../etc/passwd"})
    assert "not inside" in text


def test_generate_warns_about_the_gap_it_cannot_close(tmp_path):
    """A GUI location's browser image is version-pinned and only a live agent
    says which one. Manually-entered facts cannot know it, so the bundle is
    generated and the gap is carried as a warning rather than guessed at."""
    facts = ok("opl_facts", "manual",
               {"harbor_id": "H1", "ship_id": "S1",
                "func_ids": ["performance", "functionalGui"]})
    assert facts["warnings"], "the facts themselves did not flag the gap"
    body = ok("opl_bundle", "generate",
              {"facts": facts["facts"], "out_dir": str(tmp_path)})
    assert any("browser" in w.lower() for w in body["warnings"]), body["warnings"]


# -- what needs no account and no cluster -------------------------------------

def test_options_are_described_without_any_credential():
    """Default and meaning together: a session choosing options needs both,
    and two calls to get them is two chances to pair them up wrong."""
    body = ok("opl_bundle", "options")
    assert body["namespace"]["default"] == "blazemeter"
    assert body["namespace"]["summary"]
    # The four backends, and the third state that says "this location runs
    # mockServices and I want the performance bundle anyway" -- a session with
    # no checkout has only this schema to learn that from.
    assert body["sv_ingress"]["choices"] == ["nginx", "istio", "contour",
                                             "openshift", "none"]


def test_option_help_is_available_as_a_resource():
    async def go():
        async with mcp.Client(_SERVER[0]) as c:
            res = (await c.list_resources()).resources
            uris = [str(r.uri) for r in res]
            assert any(u.endswith("options.md") for u in uris), uris
            got = await c.read_resource(
                [u for u in uris if u.endswith("options.md")][0])
            return got.contents[0].text
    assert "# Options and profiles" in anyio.run(go)


def test_the_docs_ship_as_resources():
    """A session with no checkout of this repo has these and the tool
    descriptions, and nothing else."""
    async def go():
        async with mcp.Client(_SERVER[0]) as c:
            return [str(r.uri) for r in (await c.list_resources()).resources]
    uris = anyio.run(go)
    assert len(uris) >= 5
    assert all(u.startswith("bzm-opl://") for u in uris)


def test_manual_facts_need_no_account():
    body = ok("opl_facts", "manual", {"harbor_id": "H1", "ship_id": "S1"})
    assert body["facts"]["harbor_id"] == "H1"


def test_preflight_reads_an_evidence_file_without_a_cluster(monkeypatch):
    from evidence_fixtures import document as _evidence
    from test_doctor import FACTS as LOC_FACTS
    monkeypatch.setattr(core.livetest, "cli_tool",
                        lambda *a, **k: pytest.fail("preflight ran a cluster CLI"))
    body = ok("opl_preflight", "doctor",
              {"facts": LOC_FACTS, "options": {"namespace": "blazemeter"},
               "evidence": _evidence()})
    assert body["checks"] and body["ok"] in (True, False)


def test_suggest_answers_the_other_question_about_the_same_file():
    from evidence_fixtures import document as _evidence
    body = ok("opl_preflight", "suggest", {"evidence": _evidence()})
    assert "suggestions" in body


# -- evidence as the file the customer sent -----------------------------------
# The artifact is a file, and this is the surface whose audience was *sent* one
# and has no checkout. Inlining it means several KB of node lists and permission
# maps travelling through the model to reach a check that only needed the path,
# which is why `api_key_file` is a path here too.

def _evidence_file(tmp_path, **kw):
    from evidence_fixtures import document as _evidence
    path = tmp_path / "cluster-evidence.json"
    path.write_text(json.dumps(_evidence(**kw)))
    return str(path)


def test_doctor_takes_the_evidence_file_as_the_path_it_is(tmp_path, monkeypatch):
    from test_doctor import FACTS as LOC_FACTS
    monkeypatch.setattr(core.livetest, "cli_tool",
                        lambda *a, **k: pytest.fail("preflight ran a cluster CLI"))
    body = ok("opl_preflight", "doctor",
              {"facts": LOC_FACTS, "options": {"namespace": "blazemeter"},
               "evidence": _evidence_file(tmp_path)})
    assert body["checks"] and body["evidence"]["namespace"] == "blazemeter"


def test_a_path_and_the_object_it_holds_give_the_same_preflight(tmp_path):
    """Both forms stay accepted, and neither is a second opinion about the
    file: a caller with the document in hand loses nothing by inlining it."""
    from evidence_fixtures import document as _evidence
    from test_doctor import FACTS as LOC_FACTS
    args = {"facts": LOC_FACTS, "options": {"namespace": "blazemeter"}}
    from_path = ok("opl_preflight", "doctor",
                   dict(args, evidence=_evidence_file(tmp_path)))
    inlined = ok("opl_preflight", "doctor", dict(args, evidence=_evidence()))
    assert from_path == inlined


def test_suggest_takes_the_path_too(tmp_path):
    """The other half of the same file. A path accepted by one action and
    refused by the next is worse than neither accepting it."""
    body = ok("opl_preflight", "suggest",
              {"evidence": _evidence_file(tmp_path)})
    assert "suggestions" in body


def test_a_path_with_nothing_there_says_the_file_could_not_be_read(tmp_path):
    """And says how to get one, because the likely cause is that it was never
    sent -- not that what was sent is the wrong document."""
    from test_doctor import FACTS as LOC_FACTS
    text = err("opl_preflight", "doctor",
               {"facts": LOC_FACTS, "evidence": str(tmp_path / "absent.json")})
    assert "absent.json" in text and "cluster-evidence" in text
    assert "schema" not in text, "a file nobody read cannot be the wrong schema"


def test_a_file_that_is_not_json_names_the_read_rather_than_the_schema(tmp_path):
    path = tmp_path / "cluster-evidence.json"
    path.write_text("kind: NodeList\n")           # somebody sent YAML
    text = err("opl_preflight", "suggest", {"evidence": str(path)})
    assert "not valid JSON" in text
    assert "schema" not in text


def test_a_facts_file_is_refused_as_the_wrong_document(tmp_path):
    """The likely mistake, and the one where half-parsing would produce
    verdicts about a cluster nobody described. Distinct from the refusals
    above: this file was read, and what it says is that it is not evidence."""
    from test_cluster_evidence import EXAMPLE_FACTS
    from test_doctor import FACTS as LOC_FACTS
    text = err("opl_preflight", "doctor",
               {"facts": LOC_FACTS, "evidence": EXAMPLE_FACTS})
    assert "schema" in text
    assert "could not" not in text and "not valid JSON" not in text


def test_an_inlined_value_that_is_no_document_at_all_is_still_refused():
    """The refusal the issue was raised against must survive for values that
    are neither a path nor an object."""
    text = err("opl_preflight", "suggest", {"evidence": [1, 2, 3]})
    assert "JSON object" in text


def test_asking_for_evidence_names_a_collector_that_exists():
    """This refusal used to send the session to `bzm-opl-gen doctor --collect`,
    a flag that appeared in that one string and nowhere else in the tool. With
    no checkout there is nothing to check it against, so the name is asserted
    against doctor's own constant and the invented flag against its absence."""
    from test_doctor import FACTS as LOC_FACTS
    text = err("opl_preflight", "doctor", {"facts": LOC_FACTS})
    assert core.doctor.EVIDENCE_SCRIPT in text
    assert "--collect" not in text
    assert "path" in text, "and it should say the file may be named, not pasted"
    assert os.path.isfile(os.path.join(os.path.dirname(__file__), "..",
                                       core.doctor.EVIDENCE_SCRIPT))


def test_the_preflight_description_offers_both_forms():
    """The description is the whole documentation this session has, so one that
    calls `evidence` a file while refusing a path is the bug itself (#77).
    Both forms named, since a session told only about the path would read a
    document it already holds back out to a file to pass one."""
    text = listing()["tools"]["opl_preflight"].description.lower()
    assert "path" in text and "object" in text, text


# -- what a session is told to do next ----------------------------------------

def test_gathering_facts_points_at_the_next_call(fake_account):
    body = ok("opl_facts", "manual", {"harbor_id": "H1", "ship_id": "S1"})
    assert body["next"], "no next step offered"
    assert "opl_bundle" in json.dumps(body["next"])


def test_a_generated_bundle_says_how_to_apply_it(fake_account, tmp_path):
    body = ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    assert any("kubectl apply" in str(n) for n in body["next"]), body["next"]


def test_the_next_step_carries_the_ids_already_known(fake_account, tmp_path):
    """A hint the session has to fill in by hand is a hint it gets wrong."""
    body = ok("opl_facts", "manual", {"harbor_id": "H1", "ship_id": "S1"})
    assert "H1" in json.dumps(body["next"])


# -- the gates ----------------------------------------------------------------

def test_deleting_a_location_is_refused_by_default(fake_account):
    text = err("opl_location", "delete", {"harbor_id": "h1"})
    assert mcp_server.ALLOW_DESTRUCTIVE_ENV in text
    assert fake_account.calls == [], "it deleted before being allowed to"


def test_deleting_a_location_works_once_allowed(fake_account, monkeypatch):
    monkeypatch.setenv(mcp_server.ALLOW_DESTRUCTIVE_ENV, "1")
    body = ok("opl_location", "delete", {"harbor_id": "h1"})
    assert body["deleted"] == "h1" and body["ships_deleted"] == ["s1"]


def test_livetest_is_refused_by_default():
    text = err("opl_agent", "livetest", {"manifests": "/tmp/x", "namespace": "n",
                                         "harbor_id": "h", "ship_id": "s"})
    assert mcp_server.ENABLE_LIVETEST_ENV in text


def test_the_gates_are_read_when_called_not_when_built(fake_account, monkeypatch):
    """Otherwise a client that sets the variable still has to restart the
    server, and the refusal message would be a lie about what is needed."""
    monkeypatch.setenv(mcp_server.ALLOW_DESTRUCTIVE_ENV, "1")
    assert ok("opl_location", "delete", {"harbor_id": "h1"})
    monkeypatch.delenv(mcp_server.ALLOW_DESTRUCTIVE_ENV)
    assert err("opl_location", "delete", {"harbor_id": "h1"})


# -- refusals a model can act on ----------------------------------------------

def test_an_unknown_action_names_the_ones_that_exist():
    r = call("opl_bundle", "frobnicate")
    assert r.is_error
    assert "generate" in r.content[0].text


def test_a_missing_argument_names_what_was_missing(fake_account):
    text = err("opl_facts", "gather", {})
    assert "harbor_id" in text


def test_status_of_a_ship_that_is_not_there_says_so(fake_account):
    text = err("opl_agent", "status", {"harbor_id": "h1", "ship_id": "nope"})
    assert "nope" in text


# -- the docs, which are half of what a session is given ----------------------

def test_the_docs_resolve_in_a_checkout():
    """Two locations, because there are two installs: a wheel carries them
    inside the package, a checkout has them at the repo root. The wheel half is
    asserted by the release workflow, which is the only place a wheel exists."""
    assert os.path.isdir(mcp_server.docs_dir())
    assert "options.md" in mcp_server.doc_files()


def test_no_summary_describes_a_doc_that_is_not_there():
    """A renamed page leaves its description behind, pointing at nothing. The
    other direction is allowed on purpose -- a new doc should ship undescribed
    rather than not ship."""
    stale = [n for n in mcp_server.DOC_SUMMARIES if n not in mcp_server.doc_files()]
    assert not stale, f"summaries for missing docs: {stale}"


def test_every_doc_the_instructions_name_is_actually_served():
    """The instructions list pages by name. One that has been renamed sends a
    session to a resource that 404s, which it then has to recover from."""
    served = set(mcp_server.doc_files())
    named = {w.strip(",.") for w in mcp_server.INSTRUCTIONS.split()
             if w.strip(",.").endswith(".md")}
    assert named, "the instructions name no docs at all"
    assert named <= served, f"named but not served: {sorted(named - served)}"


def test_mirroring_images_is_annotated_rather_than_gated(fake_account):
    """Unlike `delete`. Mirroring adds images to a registry the caller named;
    the tool's destructiveHint is what makes a client confirm it. A gate here
    would put a routine private-registry setup behind an env var."""
    body = ok("opl_bundle", "images",
              {"facts": FACTS, "mirror": "reg.local/bzm", "dry_run": True})
    assert body["dry_run"] is True
    assert any("push reg.local/bzm/" in c for c in body["commands"])


def test_listing_images_runs_no_docker(fake_account, monkeypatch):
    """The default is a list. Only `pull`/`mirror` shell out, so a session
    asking what a bundle needs cannot start pulling gigabytes by accident."""
    monkeypatch.setattr(core, "_docker",
                        lambda *a, **k: pytest.fail("listing ran docker"))
    assert ok("opl_bundle", "images", {"facts": FACTS})["images"]


def test_only_the_gated_action_can_reach_a_cluster_write(monkeypatch):
    """The claim the instructions make. Everything but `livetest` is a read or
    a local write, and `livetest` is the one that has to be switched on."""
    called = []
    monkeypatch.setattr(core.livetest, "run",
                        lambda *a, **k: called.append(True) or True)
    assert err("opl_agent", "livetest", {"manifests": "/tmp/x", "namespace": "n",
                                         "harbor_id": "h", "ship_id": "s"})
    assert called == [], "it deployed before being allowed to"


# -- the JSON-RPC channel -----------------------------------------------------
# On stdio, stdout *is* the protocol. A stray print desynchronises the session,
# and to the client that does not look like a print -- it looks like the server
# died. Two defences, and they are tested at different depths on purpose.

@pytest.fixture
def quiet_workstation(monkeypatch):
    """A probed workstation without the probing -- gather() shells out to
    docker, kubectl and helm, which is seconds the offline suite must not
    spend. The same fixture the workstation tests use for the same reason."""
    from test_workstation import OK_ENV
    monkeypatch.setattr(core.workstation, "gather", lambda opts: OK_ENV)


def test_the_toolcheck_path_prints_nothing_at_all(quiet_workstation, capsys):
    """Asserted against core directly, *outside* the server's redirect --
    inside it, this could not fail however much anything printed.

    core is not a terminal, and `workstation.run` prints a seven-line report,
    so the fix was to call `workstation.evaluate` rather than to hide the
    output. If that regresses, the redirect would mask it everywhere else."""
    checks = core.toolcheck(cluster="minikube")
    assert checks["checks"]
    assert capsys.readouterr().out == ""


def test_the_redirect_still_catches_a_layer_that_does_print(monkeypatch,
                                                            capsys):
    """The belt, for `livetest.run` -- which narrates a deployment for minutes
    and is not worth unwinding. Proved with something that deliberately prints,
    so removing the redirect fails this."""
    def noisy(*a, **k):
        print("a line that would desynchronise the session")
        return {"ok": True}
    monkeypatch.setattr(mcp_server, "_bundle", noisy)
    assert ok("opl_bundle", "options") == {"ok": True}
    captured = capsys.readouterr()
    assert captured.out == "", f"reached stdout: {captured.out!r}"
    assert "desynchronise" in captured.err, "and it was swallowed, not moved"


def test_toolcheck_answers_rather_than_exiting(quiet_workstation):
    """The command exits non-zero on failures. A server has no exit code, and
    SystemExit here would take the process down past any except Exception."""
    body = ok("opl_preflight", "toolcheck", {"cluster": "minikube"})
    assert body["checks"] and isinstance(body["ok"], bool)


# -- opl_plan ------------------------------------------------------------------

def test_plan_needs_no_credential_at_all(monkeypatch):
    """Every other tool that answers something useful either holds a client or
    reads a file the customer sent. This one is reached by a session that has
    neither, which is the whole reason it is a tool rather than a note in the
    instructions."""
    monkeypatch.setattr(core, "client_from_key", lambda *a, **k: pytest.fail(
        "opl_plan asked for a BlazeMeter client"))
    body = ok("opl_plan", "capacity", {"users": 5000})
    assert body["engines"] == 10 and body["nodes"] == 10


def test_plan_hands_back_the_document_to_send_on():
    """The deliverable is the request, not the arithmetic -- a session with no
    checkout has nothing else to turn these numbers into."""
    body = ok("opl_plan", "capacity", {"users": 5000})
    assert body["document"].startswith("# Infrastructure request")
    assert "5,000 virtual users" in body["document"]


def test_plan_marks_the_assumption_a_model_would_otherwise_report_as_fact():
    body = ok("opl_plan", "capacity", {"users": 5000})
    assert body["vus_per_engine_assumed"] is True
    supplied = ok("opl_plan", "capacity",
                  {"users": 5000, "vus_per_engine": 250})
    assert supplied["vus_per_engine_assumed"] is False
    assert supplied["engines"] == 20


def test_plan_refuses_a_target_it_cannot_plan():
    r = call("opl_plan", "capacity", {"users": 0})
    assert r.is_error and "at least 1" in r.content[0].text


def test_plan_names_the_argument_it_is_missing():
    r = call("opl_plan", "capacity", {})
    assert r.is_error and "users" in r.content[0].text


def test_the_instructions_offer_planning_before_the_account():
    """A session whose customer has no cluster has to be able to find this
    without knowing the tool exists."""
    text = listing()["instructions"]
    assert "opl_plan" in text
    assert "no cluster" in text or "before there is a cluster" in text
