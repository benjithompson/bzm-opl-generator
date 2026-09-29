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

# The funcIds a location is created with, or manual facts are built for, when
# the caller names none.
DEFAULT_FUNC_IDS = ("performance",)

# Hosts only an engine talks to (results and artifact upload); crane itself uses
# a.blazemeter.com. The planner, doctor and the live rig all name these.
ENGINE_UPLOAD_HOSTS = ("data.blazemeter.com", "storage.blazemeter.com")


class BzmApiError(RuntimeError):
    """A call BlazeMeter refused, with the HTTP status where there was one.

    `status` is None for failures that carry no status (an `{"error": ...}`
    body, a network failure); do not read that as a code.
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def parse_auth_token(docker_command):
    """Extract AUTH_TOKEN from the docker-command endpoint's command string."""
    m = re.search(r"AUTH_TOKEN=([^\s\"']+)", docker_command)
    if not m:
        raise BzmApiError("no AUTH_TOKEN found in docker command: " + docker_command[:200])
    return m.group(1)


KEY_FILE_SHAPE = ('a JSON object with the id and secret of a BlazeMeter API '
                  'key: {"id": "...", "secret": "..."}')


def read_key_file(path):
    """The (id, secret) in an api-key.json, or ValueError saying what was wrong.

    Every failure is a ValueError: `core.client_from_key` turns exactly that
    into a refusal, and any other type would escape a route as a 500.
    """
    try:
        with open(path) as f:
            d = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"no API key file at '{path}'. It is {KEY_FILE_SHAPE}")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # A binary file fails to decode before it fails to parse.
        raise ValueError(f"API key file '{path}' is not valid JSON: {e}")
    except OSError as e:
        # A directory, an unreadable mode, a dead symlink.
        raise ValueError(f"could not read API key file '{path}': {e}")
    if not isinstance(d, dict) or not d.get("id") or not d.get("secret"):
        raise ValueError(f"API key file '{path}' needs both \"id\" and "
                         f"\"secret\" (see examples/api-key.example.json)")
    return d["id"], d["secret"]


class BzmClient:
    def __init__(self, *, credentials):
        """An (id, secret) pair. Keyword-only so a path cannot be passed:
        `core.client_from_key` is the one construction that reads key files."""
        key_id, secret = credentials
        self.key_id = key_id
        self._auth = base64.b64encode(f"{key_id}:{secret}".encode()).decode()

    def _send(self, req, label, timeout):
        """One round trip; every failure is a BzmApiError.

        Network failures and a non-JSON body (a proxy's HTML page) included,
        with `status` None, so nothing escapes `core._upstream` as its own type.
        """
        req.add_header("Authorization", "Basic " + self._auth)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise BzmApiError(
                f"{label} -> HTTP {e.code}: "
                f"{e.read().decode(errors='replace')[:300]}", status=e.code) from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise BzmApiError(f"{label} -> could not reach BlazeMeter: {reason}") from e
        if not raw:
            return None  # e.g. DELETE returns an empty body
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise BzmApiError(f"{label} -> answer was not JSON: "
                              f"{raw[:120].decode(errors='replace')!r}") from e
        if not isinstance(parsed, dict):
            raise BzmApiError(f"{label} -> unexpected answer: {str(parsed)[:120]}")
        if parsed.get("error"):
            raise BzmApiError(f"{label} -> API error: {parsed['error']}")
        return parsed.get("result")

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API_BASE + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return self._send(req, f"{method} {path}", timeout=30)

    def _upload(self, path, filename, content):
        """multipart/form-data POST -- the file endpoints do not take JSON."""
        boundary = "----bzmoplgen" + base64.b32encode(filename.encode()).decode().strip("=")
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content.encode() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(API_BASE + path, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        return self._send(req, f"POST {path}", timeout=60)

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
        return self.get("/accounts?limit=1000")

    def workspaces(self, account_id):
        """The account's workspaces in one big page: real accounts hold more
        than 100, and a truncated list only looks short."""
        return self.get(f"/workspaces?accountId={account_id}&limit=1000")

    def functionalities(self, account_id):
        """The account's funcId vocabulary and display names:
        `{"additionalSpace": N, "functionalities": [{"funcId", "size",
        "displayName", "subFunctionalities"?}]}`. Not paginated."""
        return self.get(f"/accounts/{account_id}/functionalities")

    def private_location(self, harbor_id):
        return self.get(f"/private-locations/{harbor_id}")

    def ship_versions(self, harbor_id, ship_id):
        """The images this location is configured to run, and their versions.

        `{"resources": {<resource id>: {dockerTag, version, ...}}}`. The call
        crane makes at startup, so it is what the agent will pull; it answers
        for an agent that has never been online.
        """
        return self.get(f"/private-locations/{harbor_id}/ships/{ship_id}/versions")

    def private_locations(self, account_id=None, workspace_id=None):
        """All private locations for a workspace or account. The endpoint
        ignores `offset`, so ask for one big page instead of paginating."""
        scope = (f"workspaceId={workspace_id}" if workspace_id
                 else f"accountId={account_id}")
        return self.get(f"/private-locations?{scope}&limit=1000")

    def create_private_location(self, name, account_id, workspace_ids,
                                func_ids=DEFAULT_FUNC_IDS, slots=1,
                                threads_per_engine=DEFAULT_THREADS_PER_ENGINE):
        h = self.post("/private-locations", {
            "name": name,
            "accountId": account_id,
            "workspacesId": list(workspace_ids),
            "funcIds": list(func_ids),
            "slots": slots,
        })
        # POST ignores threadsPerEngine, and a location without it 403s every
        # test start, so PATCH it in before handing the location back.
        return self.update_private_location(
            h["id"], slots=slots, threads_per_engine=threads_per_engine)

    def update_private_location(self, harbor_id, slots=None,
                                threads_per_engine=None,
                                override_cpu=None, override_memory=None):
        """PATCH the location's settings. Only what is passed is sent.

        Takes no `funcIds`: this PATCH replaces that list wholesale, and what a
        location runs is changed in BlazeMeter's own UI. `override_cpu` /
        `override_memory` are the engine pod's requests (memory in MB).
        """
        body = {}
        if slots is not None:
            body["slots"] = slots
        if threads_per_engine is not None:
            body["threadsPerEngine"] = threads_per_engine
        if override_cpu is not None:
            body["overrideCPU"] = override_cpu
        if override_memory is not None:
            body["overrideMemory"] = override_memory
        if not body:
            return self.private_location(harbor_id)
        return self.patch(f"/private-locations/{harbor_id}", body)

    def delete_private_location(self, harbor_id):
        return self.delete(f"/private-locations/{harbor_id}")

    # -- tests / executions (used by livetest --run-test) ----------------------
    def test(self, test_id):
        return self.get(f"/tests/{test_id}")

    def update_test(self, test_id, body):
        return self.patch(f"/tests/{test_id}", body)

    def point_test_at_location(self, test_id, harbor_id, concurrency=1):
        """Repoint a test's executions at a private location, returning the
        previous executions so the caller can put them back. BlazeMeter keys
        private locations as 'harbor-<harborId>'.

        Returns None for a taurus-script test, whose locations live in the
        uploaded YAML: an executions PATCH there is silently ignored."""
        t = self.test(test_id)
        if not t.get("executions"):
            return None
        before = {"executions": t.get("executions"),
                  "overrideExecutions": t.get("overrideExecutions")}
        loc = {f"harbor-{harbor_id}": concurrency}
        pct = {f"harbor-{harbor_id}": 100}

        def repoint(execs):
            return [dict(e, locations=loc, locationsPercents=pct,
                         concurrency=concurrency) for e in execs or []]

        self.update_test(test_id, {
            "executions": repoint(before["executions"]),
            "overrideExecutions": repoint(before["overrideExecutions"]),
        })
        return before

    # A 1-VU Taurus scenario that makes real HTTP requests: a dummy-sampler
    # script exercises none of the engine's egress or its proxy settings.
    SMOKE_SCRIPT = """execution:
- concurrency: 1
  hold-for: 60s
  ramp-up: 0s
  scenario: opl-smoke
  locations:
    harbor-{harbor_id}: 1
scenarios:
  opl-smoke:
    think-time: 1s
    requests:
    - url: {url}
      label: home
"""

    def create_smoke_test(self, project_id, harbor_id, name, url="https://blazedemo.com/",
                          filename="opl-smoke.yml"):
        """Create a runnable 1-VU/1-min Taurus test on a private location.

        The location goes in the YAML: for a taurus-script test the API
        silently drops an executions PATCH."""
        t = self.post("/tests", {
            "name": name,
            "projectId": project_id,
            "configuration": {"type": "taurus", "scriptType": "taurus",
                              "testMode": "script", "executionType": "taurusCloud",
                              "enableLoadConfiguration": True, "filename": filename},
        })
        test_id = t["id"]
        self.upload_test_file(test_id, filename,
                              self.SMOKE_SCRIPT.format(url=url, harbor_id=harbor_id))
        return test_id

    def upload_test_file(self, test_id, filename, content):
        return self._upload(f"/tests/{test_id}/files", filename, content)

    def delete_test(self, test_id):
        return self.delete(f"/tests/{test_id}")

    def master_summary(self, master_id):
        """Aggregate report for the run -- how many samples the engine actually
        produced. 'ENDED' alone does not distinguish real work from no work."""
        return self.get(f"/masters/{master_id}/reports/main/summary")

    def start_test(self, test_id):
        """Returns the master (report) id of the run."""
        r = self.post(f"/tests/{test_id}/start")
        return r["id"] if isinstance(r, dict) else r

    def master_status(self, master_id):
        return self.get(f"/masters/{master_id}/status")

    def stop_master(self, master_id):
        return self.post(f"/masters/{master_id}/stop")

    def create_ship(self, harbor_id, name):
        return self.post(f"/private-locations/{harbor_id}/servers", {"name": name})

    def delete_ship(self, harbor_id, ship_id):
        return self.delete(f"/private-locations/{harbor_id}/servers/{ship_id}")

    def docker_command(self, harbor_id, ship_id):
        return self.post(f"/private-locations/{harbor_id}/ships/{ship_id}/docker-command")

    def auth_token(self, harbor_id, ship_id):
        """The AUTH_TOKEN the agent needs, extracted from the install command.
        Issuing it revokes the previous one."""
        r = self.docker_command(harbor_id, ship_id)
        cmd = r["dockerCommand"] if isinstance(r, dict) else r
        return parse_auth_token(cmd)
