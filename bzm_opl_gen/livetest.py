"""Live deployment test: apply generated manifests to a real cluster and verify
the agent comes online in the customer's BlazeMeter account.

Success criterion = BlazeMeter API reports the ship with a fresh heartbeat
(state idle/running). That proves the whole chain: image pull, RBAC, SCC,
egress to *.blazemeter.com, and credentials.

Cluster targets:
  current   -- whatever kubectl/oc context is active (CRC, remote OpenShift...)
  kind      -- create/reuse a disposable kind cluster (smoke test; engines
               won't fit laptop resources, but crane-online still validates
               the deployment itself)
  minikube  -- create/reuse a disposable minikube profile (docker driver).
               On Apple Silicon the BlazeMeter images are amd64-only: enable
               Docker Desktop's Rosetta emulation (works, but engines are slow)
"""

import glob
import os
import platform
import subprocess
import time

KIND_CLUSTER = "bzm-opl-test"
MINIKUBE_PROFILE = "bzm-opl-test"
REGISTRY_NAME = "bzm-opl-registry"
# What the minikube node calls the host's registry. generate() manifests must
# use this as --private-registry when livetesting with --local-registry.
REGISTRY_CLUSTER_HOST = "host.minikube.internal"


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


def ensure_minikube(insecure_registry=None):
    if platform.machine() in ("arm64", "aarch64"):
        print("note: BlazeMeter images are amd64-only -- Docker Desktop's Rosetta "
              "emulation must be enabled for pods to run on this machine")
    st = subprocess.run(["minikube", "status", "-p", MINIKUBE_PROFILE,
                         "--format", "{{.Host}}"], capture_output=True, text=True)
    if st.stdout.strip() != "Running":
        cmd = ["minikube", "start", "-p", MINIKUBE_PROFILE, "--driver=docker",
               "--cpus=4", "--memory=6g", "--wait=all"]
        if insecure_registry:
            cmd.append(f"--insecure-registry={insecure_registry}")
        _run(cmd)
    elif insecure_registry:
        print(f"note: reusing running minikube profile -- it must already trust "
              f"insecure registry {insecure_registry} (flag only applies at creation)")
    _run(["kubectl", "config", "use-context", MINIKUBE_PROFILE])


def ensure_registry(port):
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                          REGISTRY_NAME], capture_output=True, text=True)
    if out.stdout.strip() != "true":
        _run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
        _run(["docker", "run", "-d", "--name", REGISTRY_NAME,
              "-p", f"{port}:5000", "registry:2"])


def mirror_images(facts, port, arch="linux/amd64"):
    """Pull the location's images (amd64 -- what the engines are built for),
    push into the local registry under the names generate() writes into
    IMAGE_OVERRIDES / the crane Deployment."""
    from .facts import select_images
    refs = [facts["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in select_images(facts)
    ]
    for ref in refs:
        name = ref.rsplit("/", 1)[-1]
        target = f"localhost:{port}/{name}"
        _run(["docker", "pull", "--platform", arch, ref])
        _run(["docker", "tag", ref, target])
        _run(["docker", "push", target])
    return refs


def deploy(manifest_dir, namespace, cluster="current", insecure_registry=None):
    if cluster == "kind":
        ensure_kind()
    elif cluster == "minikube":
        ensure_minikube(insecure_registry)
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
    if cluster == "minikube":
        _run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        return
    cli = _cli_for(manifest_dir)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _run([cli, "-n", namespace, "delete", "-f", f, "--ignore-not-found"], check=False)


def run(client, manifest_dir, namespace, harbor_id, ship_id,
        cluster="current", timeout=600, keep=False,
        facts=None, local_registry=None):
    ok = False
    try:
        insecure = None
        if local_registry:
            ensure_registry(local_registry)
            refs = mirror_images(facts, local_registry)
            print(f"mirrored {len(refs)} images into localhost:{local_registry}")
            insecure = f"{REGISTRY_CLUSTER_HOST}:{local_registry}"
        deploy(manifest_dir, namespace, cluster, insecure_registry=insecure)
        print(f"waiting up to {timeout}s for agent to report online in BlazeMeter...")
        ok = wait_online(client, harbor_id, ship_id, timeout)
        print("LIVE TEST " + ("PASSED: agent online in BlazeMeter" if ok
                              else "FAILED: agent never reported online"))
    finally:
        if not keep:
            teardown(manifest_dir, namespace, cluster)
            if local_registry:
                _run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
    return ok
