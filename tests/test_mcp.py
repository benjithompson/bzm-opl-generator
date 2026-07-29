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

import anyio
import pytest

from bzm_opl_gen import core, mcp_server
from test_generate import FACTS

TOOLS = ["opl_location", "opl_facts", "opl_bundle", "opl_preflight", "opl_agent"]


def _run(coro_fn):
    return anyio.run(coro_fn)


def call(tool, action, args=None):
    """One tool call against a freshly built server, as a client makes it."""
    async def go():
        import mcp
        async with mcp.Client(mcp_server.build()) as c:
            return await c.call_tool(tool, {"action": action, "args": args or {}})
    return _run(go)


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
        import mcp
        async with mcp.Client(mcp_server.build()) as c:
            tools = (await c.list_tools()).tools
            return {"instructions": c.instructions,
                    "tools": {t.name: t for t in tools}}
    return _run(go)


class FakeClient:
    """Enough BzmClient for the paths that reach for one."""

    def __init__(self):
        self.calls = []

    def user(self):
        return {"email": "se@example.com", "displayName": "SE",
                "defaultProject": {"accountId": 7}}

    def private_locations(self, account_id=None, workspace_id=None):
        self.calls.append(("private_locations", account_id, workspace_id))
        return [{"id": "h1", "name": "loc", "slots": 2,
                 "funcIds": ["performance"],
                 "ships": [{"id": "s1", "name": "agent1", "state": "idle"}]}]

    def private_location(self, harbor_id):
        return {"id": harbor_id, "name": "loc",
                "ships": [{"id": "s1", "state": "idle",
                           "installedVersion": "3.7.55", "lastHeartBeat": 0}]}

    def create_ship(self, harbor_id, name):
        return {"id": "s2", "name": name}

    def auth_token(self, harbor_id, ship_id):
        self.calls.append(("auth_token", harbor_id, ship_id))
        return "SECRET-TOKEN-VALUE"

    def delete_private_location(self, harbor_id):
        self.calls.append(("delete", harbor_id))


@pytest.fixture
def fake_account(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(core, "client_from_env", lambda *a, **k: c)
    return c


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    """Nothing here may pick up the developer's own api-key.json. A test that
    silently used a real key would talk to a real account."""
    for var in (core.KEY_FILE_ENV, core.KEY_ID_ENV, core.KEY_SECRET_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(core, "detect_keys", lambda: [])


# -- what a session is handed -------------------------------------------------

def test_the_five_tools_are_the_agreed_surface():
    assert sorted(listing()["tools"]) == sorted(TOOLS)


def test_every_tool_enumerates_its_actions_in_the_schema():
    """The action list is an enum, not prose: a wrong one is then refused by
    the client's own validation, naming the valid ones, instead of arriving
    here to be guessed at."""
    for name, tool in listing()["tools"].items():
        action = tool.input_schema["properties"]["action"]
        assert action.get("enum"), f"{name} does not enumerate its actions"


def test_every_tool_says_whether_it_changes_anything():
    """Annotations are how a client decides what to confirm before running.
    A tool with none is treated as unknown, which in practice means treated as
    safe."""
    for name, tool in listing()["tools"].items():
        assert tool.annotations is not None, f"{name} carries no annotations"
        assert tool.annotations.read_only_hint is not None, name


def test_the_read_only_tools_are_marked_read_only():
    tools = listing()["tools"]
    assert tools["opl_preflight"].annotations.read_only_hint is True
    assert tools["opl_facts"].annotations.read_only_hint is True


def test_the_tools_that_change_an_account_are_marked_destructive():
    """opl_location creates and deletes; opl_bundle can push to a registry."""
    tools = listing()["tools"]
    assert tools["opl_location"].annotations.destructive_hint is True
    assert tools["opl_bundle"].annotations.destructive_hint is True


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
    from bzm_opl_gen import generate as gen_mod
    assert gen_mod.SECRET_OPTIONS
    for name in gen_mod.SECRET_OPTIONS:
        with pytest.raises(core.BadRequest):
            mcp_server._no_secrets({name: "x"})


def test_only_a_path_may_name_a_key(fake_account, tmp_path):
    """api_key_file names a file to read; it is not the credential itself."""
    key = tmp_path / "k.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    # Accepted as an argument, and it is a path -- the fixture stands in for
    # what client_from_env would build from it.
    assert ok("opl_location", "whoami", {"api_key_file": str(key)})["email"]


# -- the token, which is the one thing that must not leak ---------------------

def test_generating_a_bundle_never_returns_the_token(fake_account, tmp_path):
    """The Secret is written to disk with the token in it. The *response* is
    file names and sizes, so a token cannot end up in a transcript."""
    body = ok("opl_bundle", "generate",
              {"facts": FACTS, "out_dir": str(tmp_path), "options": {"namespace": "ns1"}})
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


def test_reading_the_secret_back_does_not_hand_over_the_token(fake_account,
                                                             tmp_path):
    """`read bzm_secret.yaml` was a second, quieter way to get the credential:
    it does not look like asking for one, which is exactly why reveal_token is
    a whole action. The file is readable, the value is not."""
    ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
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
       {"facts": FACTS, "out_dir": str(tmp_path),
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
              {"facts": facts["facts"], "out_dir": str(tmp_path),
               "fetch_token": False})
    assert any("browser" in w.lower() for w in body["warnings"]), body["warnings"]


# -- what needs no account and no cluster -------------------------------------

def test_options_are_described_without_any_credential():
    """Default and meaning together: a session choosing options needs both,
    and two calls to get them is two chances to pair them up wrong."""
    body = ok("opl_bundle", "options")
    assert body["namespace"]["default"] == "blazemeter"
    assert body["namespace"]["summary"]
    assert body["sv_ingress"]["choices"] == ["nginx", "istio", "contour",
                                             "openshift"]


def test_option_help_is_available_as_a_resource():
    async def go():
        import mcp
        async with mcp.Client(mcp_server.build()) as c:
            res = (await c.list_resources()).resources
            uris = [str(r.uri) for r in res]
            assert any(u.endswith("options.md") for u in uris), uris
            got = await c.read_resource(
                [u for u in uris if u.endswith("options.md")][0])
            return got.contents[0].text
    assert "# Options and profiles" in _run(go)


def test_the_docs_ship_as_resources():
    """A session with no checkout of this repo has these and the tool
    descriptions, and nothing else."""
    async def go():
        import mcp
        async with mcp.Client(mcp_server.build()) as c:
            return [str(r.uri) for r in (await c.list_resources()).resources]
    uris = _run(go)
    assert len(uris) >= 5
    assert all(u.startswith("bzm-opl://") for u in uris)


def test_manual_facts_need_no_account():
    body = ok("opl_facts", "manual", {"harbor_id": "H1", "ship_id": "S1"})
    assert body["facts"]["harbor_id"] == "H1"


def test_preflight_reads_an_evidence_file_without_a_cluster(monkeypatch):
    from test_cluster_evidence import _evidence
    from test_doctor import FACTS as LOC_FACTS
    monkeypatch.setattr(core.livetest, "cli_tool",
                        lambda *a, **k: pytest.fail("preflight ran a cluster CLI"))
    body = ok("opl_preflight", "doctor",
              {"facts": LOC_FACTS, "options": {"namespace": "blazemeter"},
               "evidence": _evidence()})
    assert body["checks"] and body["ok"] in (True, False)


def test_suggest_answers_the_other_question_about_the_same_file():
    from test_cluster_evidence import _evidence
    body = ok("opl_preflight", "suggest", {"evidence": _evidence()})
    assert "suggestions" in body


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
# died. The layers underneath were written for a command line and print freely.

def test_a_tool_call_writes_nothing_to_stdout(fake_account, tmp_path, capsys):
    """toolcheck reaches workstation.run, which prints a seven-line report;
    livetest.run narrates a whole deployment. Both would have corrupted the
    channel. Guarded once in _answer rather than hunted down one call at a
    time, so this covers whatever gets called next as well."""
    ok("opl_preflight", "toolcheck", {"cluster": "minikube"})
    ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    ok("opl_facts", "manual", {"harbor_id": "H1", "ship_id": "S1"})
    captured = capsys.readouterr()
    assert captured.out == "", f"tool calls wrote to stdout: {captured.out!r}"


def test_what_the_underlying_layer_printed_is_kept_on_stderr(fake_account,
                                                             capsys):
    """Redirected, not swallowed: those lines are the only diagnostics some of
    these paths produce, and a client shows stderr as the server's log."""
    ok("opl_preflight", "toolcheck", {"cluster": "minikube"})
    assert capsys.readouterr().err.strip(), "the report went nowhere"


def test_toolcheck_answers_rather_than_exiting(fake_account):
    """The command exits non-zero on failures. A server has no exit code, and
    SystemExit here would take the process down past any except Exception."""
    body = ok("opl_preflight", "toolcheck", {"cluster": "minikube"})
    assert body["checks"] and isinstance(body["ok"], bool)


def test_every_action_that_leads_somewhere_says_where(fake_account, tmp_path):
    """A `next` on the ones a session moves on from. Not on `options` or
    `images`, which are lookups -- a hint there is noise on a call whose whole
    answer is the thing that was asked for."""
    from test_cluster_evidence import _evidence
    from test_doctor import FACTS as LOC_FACTS
    ok("opl_bundle", "generate", {"facts": FACTS, "out_dir": str(tmp_path)})
    cases = [
        ("opl_location", "whoami", {}),
        ("opl_location", "list", {}),
        ("opl_facts", "manual", {"harbor_id": "H1", "ship_id": "S1"}),
        ("opl_bundle", "read", {"out_dir": str(tmp_path),
                                "name": "bzm_deployment.yaml"}),
        ("opl_preflight", "doctor", {"facts": LOC_FACTS,
                                     "options": {"namespace": "blazemeter"},
                                     "evidence": _evidence()}),
        ("opl_preflight", "suggest", {"evidence": _evidence()}),
        ("opl_location", "reveal_token", {"harbor_id": "h1", "ship_id": "s1"}),
    ]
    for tool, action, args in cases:
        assert ok(tool, action, args).get("next"), f"{tool} {action}"


def test_a_failing_preflight_points_at_fixing_it_not_at_generating(fake_account):
    """The moment a session is most likely to carry on regardless, so the
    verdict carries the alternative rather than leaving it to be inferred."""
    from test_cluster_evidence import DEGRADED
    with open(DEGRADED) as fh:
        degraded = json.load(fh)
    from test_doctor import FACTS as LOC_FACTS
    body = ok("opl_preflight", "doctor",
              {"facts": LOC_FACTS, "options": {"namespace": "blazemeter"},
               "evidence": degraded})
    hints = json.dumps(body["next"])
    if not body["ok"]:
        assert "suggest" in hints and "generate" not in hints
