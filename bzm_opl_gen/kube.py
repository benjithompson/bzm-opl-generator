"""Subprocess and kubectl primitives shared by the live rig, doctor and core.

Callers use module attributes (`kube.run(...)`), never `from .kube import run`,
so a test replaces one attribute and every caller sees the fake.
"""

import functools
import json
import os
import subprocess
import time

from . import evidence

# Over this, apply server-side: client-side apply copies the object into the
# last-applied-configuration annotation, which the API server caps at 256KB.
LARGE_MANIFEST_BYTES = 200_000


def run(cmd, check=True, capture=False):
    """Run a command, echoing it first."""
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def quiet(cmd, timeout=None, input=None):
    """Run a command without echoing it; the CompletedProcess, never raising on
    a non-zero exit. `input` is written to its stdin."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          input=input)


def poll_until(fn, timeout, interval, clock=time.monotonic, sleep=time.sleep):
    """Call `fn` until it returns something truthy or `timeout` seconds pass.

    `fn` always runs at least once. Returns its last result, so a caller can
    tell a timeout (falsy) from success without a second call."""
    deadline = clock() + timeout
    while True:
        result = fn()
        if result or clock() >= deadline:
            return result
        sleep(interval)


@functools.cache
def cli_tool():
    """oc if available (OpenShift-friendly), else kubectl. Cached: PATH does not
    change mid-run."""
    for c in ("oc", "kubectl"):
        try:
            subprocess.run([c, "version", "--client"], capture_output=True, check=True)
            return c
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("neither oc nor kubectl found on PATH")


def kget(cli, namespace, kind, name=None):
    """`get -o json` parsed, or {} for any failure. Omit `name` for a list.

    {} is unambiguous for a list (a served list always has `items`) but not for
    one named object, where it also means "there is none"; use `kget_named`
    where those two must be told apart."""
    return kget_named(cli, namespace, kind, name) or {}


def kget_named(cli, namespace, kind, name=None, timeout=None):
    """`kget`, but {} only when the API server answered NotFound and None for
    every other failure (Forbidden, no cluster, no binary, no answer within
    `timeout` seconds)."""
    cmd = [cli, "get", kind, "-o", "json"]
    if name:
        cmd.insert(3, name)
    if namespace:
        cmd[1:1] = ["-n", namespace]
    try:
        out = quiet(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode == 0 and out.stdout.strip():
        return json.loads(out.stdout)
    return {} if "(NotFound)" in (out.stderr or "") else None


def kget_served(cli, namespace, kind, timeout=None):
    """A list `get -o json` of a resource an add-on serves (a policy engine's
    CRD): the document; evidence.NOT_SERVED when the API server has no such
    resource type, which is "not installed"; None for every other failure."""
    cmd = [cli, "get", kind, "-o", "json"]
    if namespace:
        cmd[1:1] = ["-n", namespace]
    try:
        out = quiet(cmd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode == 0 and out.stdout.strip():
        return json.loads(out.stdout)
    if evidence.NOT_SERVED_ERROR in (out.stderr or ""):
        return evidence.NOT_SERVED
    return None


def crane_exec(cli, namespace, sh):
    """Run `sh -c` in the crane Deployment's pod; its stdout."""
    return quiet([cli, "-n", namespace, "exec", "deploy/crane", "--",
                  "sh", "-c", sh]).stdout


def pod_images(cli, namespace):
    """Images the crane pods run."""
    return quiet([cli, "-n", namespace, "get", "pods", "-l", "role=role-crane",
                  "-o", "jsonpath={.items[*].spec.containers[*].image}"]).stdout.split()


def apply(cli, namespace, path):
    """kubectl apply, server-side for a manifest too large for the
    last-applied annotation (a full CA trust bundle is the usual cause)."""
    cmd = [cli, "-n", namespace, "apply", "-f", path]
    if os.path.getsize(path) > LARGE_MANIFEST_BYTES:
        cmd += ["--server-side", "--force-conflicts"]
    run(cmd)


def ensure_namespace(cli, namespace):
    """Create the namespace unless it exists. True if this call created it,
    which is what licenses a teardown to delete it."""
    if quiet([cli, "get", "ns", namespace]).returncode == 0:
        print(f"reusing the existing namespace '{namespace}' -- this run will "
              f"not delete it")
        return False
    run([cli, "create", "ns", namespace], check=False)
    return True
