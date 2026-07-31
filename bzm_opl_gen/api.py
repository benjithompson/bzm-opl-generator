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


KEY_FILE_SHAPE = ('a JSON object with the id and secret of a BlazeMeter API '
                  'key: {"id": "...", "secret": "..."}')


def read_key_file(path):
    """The (id, secret) in an api-key.json, or ValueError saying what was wrong.

    Separate from the SystemExit wrapper below so a caller that must not exit
    -- a server -- can report the same three problems its own way.
    """
    try:
        with open(path) as f:
            d = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"no API key file at '{path}'. It is {KEY_FILE_SHAPE}")
    except json.JSONDecodeError as e:
        raise ValueError(f"API key file '{path}' is not valid JSON: {e}")
    if not isinstance(d, dict) or not d.get("id") or not d.get("secret"):
        raise ValueError(f"API key file '{path}' needs both \"id\" and "
                         f"\"secret\" (see examples/api-key.example.json)")
    return d["id"], d["secret"]


def read_key_file_or_exit(path):
    """read_key_file, as a command wants it: the same three messages, plus how
    to create the file, and exit rather than traceback."""
    try:
        return read_key_file(path)
    except ValueError as e:
        raise SystemExit(
            f"{e}\nCreate the key under Settings -> API Keys in BlazeMeter, "
            f"then:\n  cp examples/api-key.example.json {path}   # and fill it in")


class BzmClient:
    def __init__(self, api_key_path=None, credentials=None):
        """Either a path to an api-key.json, or an (id, secret) pair already
        read from somewhere else.

        `credentials` exists because reading the file here raises SystemExit,
        which is right for a command and fatal for a long-running server -- it
        is a BaseException, so a tool wrapper's `except Exception` does not stop
        it taking the process down with it. A caller that has its own way to
        report a bad credential reads the file itself and passes the pair.
        """
        if credentials is None:
            credentials = read_key_file_or_exit(api_key_path)
        key_id, secret = credentials
        self._auth = base64.b64encode(f"{key_id}:{secret}".encode()).decode()

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

    def _upload(self, path, filename, content):
        """multipart/form-data POST -- the file endpoints do not take JSON."""
        boundary = "----bzmoplgen" + base64.b32encode(filename.encode()).decode().strip("=")
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + content.encode() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(API_BASE + path, data=body, method="POST")
        req.add_header("Authorization", "Basic " + self._auth)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                parsed = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise BzmApiError(f"POST {path} -> HTTP {e.code}: "
                              f"{e.read().decode(errors='replace')[:300]}") from e
        if parsed.get("error"):
            raise BzmApiError(f"POST {path} -> API error: {parsed['error']}")
        return parsed.get("result")

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
                                threads_per_engine=None, func_ids=None,
                                override_cpu=None, override_memory=None):
        """PATCH the location's settings. Only what is passed is sent.

        `override_cpu` / `override_memory` are the engine pod's CPU and memory
        *requests* (memory in MB), which the scheduler and the autoscaler place
        on -- see generate.ENGINE_DEFAULT_REQUEST_CPU for why they matter more
        than they look. They are read back from the location by facts.gather,
        so the field names are known; that BlazeMeter accepts them on a PATCH
        is not something this repo has proved on every account, which is why
        core.update_location re-reads and reports what actually changed rather
        than assuming the body was honoured.
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
        # The whole list, because PATCH replaces it: sending one funcId is how a
        # location that ran performance and mocks comes back running only mocks.
        # Callers add to what the location already has -- see core.add_func_id.
        if func_ids is not None:
            body["funcIds"] = list(func_ids)
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
        uploaded YAML: patching executions there is silently ignored, so
        pretending it worked would be a lie the caller acts on."""
        t = self.test(test_id)
        if not t.get("executions"):
            return None
        before = {"executions": t.get("executions"),
                  "overrideExecutions": t.get("overrideExecutions")}
        loc = {f"harbor-{harbor_id}": concurrency}
        pct = {f"harbor-{harbor_id}": 100}

        def repoint(execs):
            out = []
            for e in execs or []:
                e = dict(e, locations=loc, locationsPercents=pct,
                         concurrency=concurrency)
                out.append(e)
            return out

        self.update_test(test_id, {
            "executions": repoint(before["executions"]),
            "overrideExecutions": repoint(before["overrideExecutions"]),
        })
        return before

    # A 1-VU Taurus scenario that makes real HTTP requests. A dummy-sampler
    # script exercises none of the engine's egress, so it cannot show whether
    # engines can reach a target at all -- or whether they honour the proxy.
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

        The location goes in the YAML, not in the test's `executions`: for a
        taurus-script test the API silently drops an executions PATCH, because
        the script is the load configuration."""
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

    def master(self, master_id):
        return self.get(f"/masters/{master_id}")

    def stop_master(self, master_id):
        return self.post(f"/masters/{master_id}/stop")

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
