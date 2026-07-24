from bzm_opl_gen import api


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


def test_update_private_location_omits_unset_fields():
    c = FakeClient({})
    c.update_private_location("h1", threads_per_engine=100)
    assert c.calls == [("PATCH", "/private-locations/h1",
                        {"threadsPerEngine": 100})]


def test_update_private_location_no_fields_is_a_read():
    c = FakeClient({})
    c.update_private_location("h1")
    assert c.calls == [("GET", "/private-locations/h1", None)]
