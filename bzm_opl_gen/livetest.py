"""Live deployment test: apply generated manifests to a real cluster and verify
the agent comes online in the customer's BlazeMeter account.

Success criterion = BlazeMeter API reports the ship with a fresh heartbeat
(state idle/running). That proves the whole chain: image pull, RBAC, SCC,
egress to *.blazemeter.com, and credentials.

Cluster targets:
  current  -- whatever kubectl/oc context is active (CRC, remote OpenShift...)
  kind     -- create/reuse a disposable kind cluster (smoke test; engines
              won't fit laptop resources, but crane-online still validates
              the deployment itself)
"""

import glob
import os
import subprocess
import time

KIND_CLUSTER = "bzm-opl-test"


def _run(cmd, check=True, capture=False):
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True,
                          capture_output=capture)


def _cli_for(manifest_dir):
    """oc if available (OpenShift-friendly), else kubectl."""
    for c in ("oc", "kubectl"):
        try:
            subprocess.run([c, "version", "--client"], capture_output=True, check=True)
            return c
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("neither oc nor kubectl found on PATH")


def ensure_kind():
    out = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True)
    if KIND_CLUSTER not in out.stdout.split():
        _run(["kind", "create", "cluster", "--name", KIND_CLUSTER, "--wait", "120s"])
    _run(["kubectl", "config", "use-context", f"kind-{KIND_CLUSTER}"])


def deploy(manifest_dir, namespace, cluster="current"):
    if cluster == "kind":
        ensure_kind()
    cli = _cli_for(manifest_dir)
    _run([cli, "get", "ns", namespace], check=False)
    _run([cli, "create", "ns", namespace], check=False)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _run([cli, "-n", namespace, "apply", "-f", f])
    _run([cli, "-n", namespace, "rollout", "status", "deploy/crane", "--timeout=300s"])
    return cli


def wait_online(client, harbor_id, ship_id, timeout=600, poll=15):
    """Poll BlazeMeter until the ship heartbeats fresher than test start."""
    start = time.time()
    while time.time() - start < timeout:
        harbor = client.private_location(harbor_id)
        ship = next((s for s in harbor.get("ships", []) if s["id"] == ship_id), None)
        if ship:
            hb, state = ship.get("lastHeartBeat") or 0, ship.get("state")
            fresh = hb >= start - 60
            print(f"  ship={ship_id} state={state} heartbeat_age={time.time()-hb:.0f}s")
            if fresh and state in ("idle", "running"):
                return True
        time.sleep(poll)
    return False


def teardown(manifest_dir, namespace, cluster="current"):
    if cluster == "kind":
        _run(["kind", "delete", "cluster", "--name", KIND_CLUSTER], check=False)
        return
    cli = _cli_for(manifest_dir)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _run([cli, "-n", namespace, "delete", "-f", f, "--ignore-not-found"], check=False)


def run(client, manifest_dir, namespace, harbor_id, ship_id,
        cluster="current", timeout=600, keep=False):
    ok = False
    try:
        deploy(manifest_dir, namespace, cluster)
        print(f"waiting up to {timeout}s for agent to report online in BlazeMeter...")
        ok = wait_online(client, harbor_id, ship_id, timeout)
        print("LIVE TEST " + ("PASSED: agent online in BlazeMeter" if ok
                              else "FAILED: agent never reported online"))
    finally:
        if not keep:
            teardown(manifest_dir, namespace, cluster)
    return ok
