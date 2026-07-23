"""Minimal BlazeMeter API client (stdlib only).

Auth: api-key JSON file {"id": ..., "secret": ...} -> HTTP Basic.
"""

import base64
import json
import urllib.error
import urllib.request

API_BASE = "https://a.blazemeter.com/api/v4"


class BzmApiError(RuntimeError):
    pass


class BzmClient:
    def __init__(self, api_key_path):
        with open(api_key_path) as f:
            d = json.load(f)
        self._auth = base64.b64encode(f"{d['id']}:{d['secret']}".encode()).decode()

    def get(self, path):
        req = urllib.request.Request(API_BASE + path)
        req.add_header("Authorization", "Basic " + self._auth)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            raise BzmApiError(f"GET {path} -> HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        if body.get("error"):
            raise BzmApiError(f"GET {path} -> API error: {body['error']}")
        return body["result"]

    # -- convenience wrappers -------------------------------------------------
    def user(self):
        return self.get("/user")

    def private_location(self, harbor_id):
        return self.get(f"/private-locations/{harbor_id}")

    def private_locations(self, account_id):
        return self.get(f"/private-locations?accountId={account_id}&limit=100")
