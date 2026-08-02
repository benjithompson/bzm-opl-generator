import json
import os

import pytest

from bzm_opl_gen import api

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class FakeClient(api.BzmClient):
    """BzmClient with the HTTP layer swapped for a call recorder."""

    def __init__(self, responses):
        self.calls = []
        self._responses = responses

    def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return self._responses.get((method, path), {"id": "h1"})


def test_create_location_patches_threads_per_engine():
    """POST ignores threadsPerEngine; without the follow-up PATCH the location
    can't start tests (403 'Not enough available resources')."""
    c = FakeClient({("PATCH", "/private-locations/h1"):
                    {"id": "h1", "slots": 2, "threadsPerEngine": 500}})
    h = c.create_private_location("loc", 1, [2], slots=2)

    assert [m for m, _, _ in c.calls] == ["POST", "PATCH"]
    assert c.calls[1] == ("PATCH", "/private-locations/h1",
                          {"slots": 2, "threadsPerEngine": 500})
    assert h["threadsPerEngine"] == 500        # returns the runnable location


def test_create_location_threads_per_engine_override():
    c = FakeClient({})
    c.create_private_location("loc", 1, [2], threads_per_engine=50)
    assert c.calls[1][2]["threadsPerEngine"] == 50


def test_list_calls_ask_for_more_than_one_page():
    """A truncated list only looks short.

    The workspace limit was 100, and SE Demo has 166: the 66 that fell off held
    40% of the account's rated VUs, attributed on screen to no workspace at all.
    Locations were already asking for 1000 for the same reason.
    """
    c = FakeClient({})
    c.workspaces(291446)
    c.private_locations(account_id=291446)

    paths = [p for _, p, _ in c.calls]
    assert paths[0] == "/workspaces?accountId=291446&limit=1000"
    assert "limit=1000" in paths[1]


def test_update_private_location_omits_unset_fields():
    c = FakeClient({})
    c.update_private_location("h1", threads_per_engine=100)
    assert c.calls == [("PATCH", "/private-locations/h1",
                        {"threadsPerEngine": 100})]


def test_update_private_location_no_fields_is_a_read():
    c = FakeClient({})
    c.update_private_location("h1")
    assert c.calls == [("GET", "/private-locations/h1", None)]


# A missing or half-filled key file is the first thing a new contributor hits;
# it used to surface as a FileNotFoundError/KeyError traceback. The reading is
# this module's; deciding what a bad file means is the caller's, which is why
# these assert a ValueError and tests/test_core.py asserts the refusal built
# from it.
def test_missing_api_key_file_names_the_path(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(ValueError) as e:
        api.read_key_file(str(missing))
    assert str(missing) in str(e.value)


def test_malformed_api_key_file(tmp_path):
    p = tmp_path / "api-key.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        api.read_key_file(str(p))


def test_api_key_file_missing_secret(tmp_path):
    p = tmp_path / "api-key.json"
    p.write_text(json.dumps({"id": "abc"}))
    with pytest.raises(ValueError, match='"id" and "secret"'):
        api.read_key_file(str(p))


def test_a_path_cannot_be_handed_to_the_constructor_at_all(tmp_path):
    """The construction takes a pair, keyword-only. It used to take a path and
    read it, and that read raised SystemExit -- a BaseException, straight past
    a route's error handling and out through the top of the server process.
    Keyword-only is what makes "one construction" structural rather than a rule
    each caller has to keep: a path does not fit here any more.
    """
    key = tmp_path / "api-key.json"
    key.write_text(json.dumps({"id": "abc", "secret": "s"}))
    with pytest.raises(TypeError):
        api.BzmClient(str(key))


def test_api_key_example_has_the_fields_the_client_reads(tmp_path):
    """The placeholder must stay loadable, or `cp` then fill-in breaks."""
    with open(os.path.join(EXAMPLES, "api-key.example.json")) as f:
        d = json.load(f)
    assert set(d) == {"id", "secret"} and all(d.values())
