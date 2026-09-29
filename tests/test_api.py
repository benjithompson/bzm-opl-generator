import json
import os
import socket
import urllib.error
import urllib.request

import pytest

from bzm_opl_gen import api
from versions_fixtures import VERSIONS_PERFORMANCE

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
    """List calls ask for one big page: a truncated list only looks short."""
    c = FakeClient({})
    c.workspaces(123456)
    c.private_locations(account_id=123456)
    c.accounts()

    paths = [p for _, p, _ in c.calls]
    assert paths[0] == "/workspaces?accountId=123456&limit=1000"
    assert "limit=1000" in paths[1]
    assert paths[2] == "/accounts?limit=1000"


def test_the_account_is_asked_what_its_functionalities_are_called():
    """The funcId vocabulary is read from the account's functionalities endpoint
    (fixture recorded from a real account)."""
    c = FakeClient({("GET", "/accounts/123456/functionalities"): {
        "additionalSpace": 50,
        "functionalities": [
            {"funcId": "performance", "size": 5, "displayName": "Performance"},
            {"funcId": "tdm", "size": 1, "displayName": "TDM Integration"},
        ]}})
    body = c.functionalities(123456)

    assert c.calls == [("GET", "/accounts/123456/functionalities", None)]
    assert [f["displayName"] for f in body["functionalities"]] == [
        "Performance", "TDM Integration"]


def test_the_location_is_asked_which_images_its_agent_runs():
    """The image list comes from the per-agent /versions route, which needs no live
    agent; the map's own keys are resource ids nothing reads."""
    c = FakeClient({("GET", "/private-locations/H1/ships/S1/versions"):
                    VERSIONS_PERFORMANCE})
    body = c.ship_versions("H1", "S1")

    assert c.calls == [("GET", "/private-locations/H1/ships/S1/versions", None)]
    assert body["resources"]["taurusEngineDockerImage"] == {
        "dockerTag": "taurus-cloud", "type": "dockerImage",
        "version": "2.4.454-reduced", "reducedVersion": "2.4.454-reduced",
        "imageRelativePath": "blazemeter/v4", "restartPolicy": "Never",
        "minSlots": 1, "dockerRegistry": "gcr.io/verdant-bulwark-278"}


class _Body:
    def __init__(self, raw):
        self._raw = raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("answer", [
    urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
    urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
    TimeoutError("timed out"),
    _Body(b"<html>502 Bad Gateway</html>"),
    _Body(b"[1, 2]"),
])
def test_every_failure_to_answer_is_a_bzm_api_error(monkeypatch, answer):
    """Network failures and non-JSON bodies are BzmApiError too, with status None."""
    def urlopen(req, timeout=None, **kw):
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    c = api.BzmClient(credentials=("id", "secret"))
    with pytest.raises(api.BzmApiError) as e:
        c.user()
    assert e.value.status is None
    assert "GET /user" in str(e.value)


def test_update_private_location_omits_unset_fields():
    c = FakeClient({})
    c.update_private_location("h1", threads_per_engine=100)
    assert c.calls == [("PATCH", "/private-locations/h1",
                        {"threadsPerEngine": 100})]


def test_update_private_location_no_fields_is_a_read():
    c = FakeClient({})
    c.update_private_location("h1")
    assert c.calls == [("GET", "/private-locations/h1", None)]


# The reading is this module's (ValueError); the refusal built from it is
# core's (tests/test_core.py).
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
    """The constructor takes a keyword-only pair, so a path cannot be passed."""
    key = tmp_path / "api-key.json"
    key.write_text(json.dumps({"id": "abc", "secret": "s"}))
    with pytest.raises(TypeError):
        api.BzmClient(str(key))


def test_api_key_example_has_the_fields_the_client_reads(tmp_path):
    """The placeholder must stay loadable, or `cp` then fill-in breaks."""
    with open(os.path.join(EXAMPLES, "api-key.example.json")) as f:
        d = json.load(f)
    assert set(d) == {"id", "secret"} and all(d.values())
