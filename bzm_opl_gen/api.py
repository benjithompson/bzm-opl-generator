"""Minimal BlazeMeter API client (stdlib only).

Auth: api-key JSON file {"id": ..., "secret": ...} -> HTTP Basic.
"""

import base64
import json
import re
import urllib.error
import urllib.request

API_BASE = "https://a.blazemeter.com/api/v4"

# Max threads one engine will run. A location with this unset cannot start a
# test at all; 500 matches BlazeMeter's own default for a 2 CPU / 8Gi engine.
DEFAULT_THREADS_PER_ENGINE = 500


class BzmApiError(RuntimeError):
    pass


def parse_auth_token(docker_command):
    """Extract AUTH_TOKEN from the docker-command endpoint's command string."""
    m = re.search(r"AUTH_TOKEN=([^\s\"']+)", docker_command)
    if not m:
        raise BzmApiError("no AUTH_TOKEN found in docker command: " + docker_command[:200])
    return m.group(1)


class BzmClient:
    def __init__(self, api_key_path):
        with open(api_key_path) as f:
            d = json.load(f)
        self._auth = base64.b64encode(f"{d['id']}:{d['secret']}".encode()).decode()

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API_BASE + path, data=data, method=method)
        req.add_header("Authorization", "Basic " + self._auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise BzmApiError(f"{method} {path} -> HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        if not raw:
            return None  # e.g. DELETE returns an empty body
        parsed = json.loads(raw)
        if parsed.get("error"):
            raise BzmApiError(f"{method} {path} -> API error: {parsed['error']}")
        return parsed["result"]

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, body=None):
        return self._request("POST", path, body if body is not None else {})

    def patch(self, path, body):
        return self._request("PATCH", path, body)

    def delete(self, path):
        return self._request("DELETE", path)

    # -- convenience wrappers -------------------------------------------------
    def user(self):
        return self.get("/user")

    def accounts(self):
        return self.get("/accounts?limit=100")

    def workspaces(self, account_id):
        return self.get(f"/workspaces?accountId={account_id}&limit=100")

    def private_location(self, harbor_id):
        return self.get(f"/private-locations/{harbor_id}")

    def private_locations(self, account_id=None, workspace_id=None):
        """All private locations for a workspace or account. The endpoint
        ignores `offset`, so ask for one big page instead of paginating."""
        scope = (f"workspaceId={workspace_id}" if workspace_id
                 else f"accountId={account_id}")
        return self.get(f"/private-locations?{scope}&limit=1000")

    def create_private_location(self, name, account_id, workspace_ids,
                                func_ids=("performance",), slots=1,
                                threads_per_engine=DEFAULT_THREADS_PER_ENGINE):
        h = self.post("/private-locations", {
            "name": name,
            "accountId": account_id,
            "workspacesId": list(workspace_ids),
            "funcIds": list(func_ids),
            "slots": slots,
        })
        # POST ignores threadsPerEngine, so a freshly created location has it
        # null and every test start fails with 403 "Not enough available
        # resources". PATCH it into a runnable state before handing it back.
        return self.update_private_location(
            h["id"], slots=slots, threads_per_engine=threads_per_engine)

    def update_private_location(self, harbor_id, slots=None,
                                threads_per_engine=None):
        body = {}
        if slots is not None:
            body["slots"] = slots
        if threads_per_engine is not None:
            body["threadsPerEngine"] = threads_per_engine
        if not body:
            return self.private_location(harbor_id)
        return self.patch(f"/private-locations/{harbor_id}", body)

    def delete_private_location(self, harbor_id):
        return self.delete(f"/private-locations/{harbor_id}")

    def create_ship(self, harbor_id, name):
        return self.post(f"/private-locations/{harbor_id}/servers", {"name": name})

    def delete_ship(self, harbor_id, ship_id):
        return self.delete(f"/private-locations/{harbor_id}/servers/{ship_id}")

    def docker_command(self, harbor_id, ship_id):
        return self.post(f"/private-locations/{harbor_id}/ships/{ship_id}/docker-command")

    def auth_token(self, harbor_id, ship_id):
        """The AUTH_TOKEN the agent needs, extracted from the install command."""
        r = self.docker_command(harbor_id, ship_id)
        cmd = r["dockerCommand"] if isinstance(r, dict) else r
        return parse_auth_token(cmd)
