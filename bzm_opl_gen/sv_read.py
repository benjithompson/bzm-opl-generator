"""Virtual services deployed in a namespace, read off the running pods.

Read from the pods rather than BlazeMeter's mock API, which lives on another
host, needs a workspace id facts.json does not carry, and reports what
BlazeMeter believes rather than what is running. The pod labels also carry the
harbor/ship ids profile.json omits.
"""

import collections
import json
import subprocess

from . import kube
from . import service_virt


def sv_mocks(cli, namespace):
    """Deployed virtual services as [{"name", "port", "harbor", "ship"}]; empty
    when the namespace cannot be read."""
    return _sv_mocks(kube.kget(cli, namespace, "pods").get("items", []))


def _sv_mocks(pods):
    # Keyed by name: a mid-rollout namespace can hold two pods for one mock.
    found = {}
    for pod in pods:
        labels = (pod.get("metadata") or {}).get("labels") or {}
        name = labels.get(service_virt.SV_POD_NAME_LABEL)
        if not name:
            continue                      # crane itself, engines, test jobs
        ports = [p.get("containerPort")
                 for c in (pod.get("spec") or {}).get("containers") or []
                 for p in c.get("ports") or [] if p.get("containerPort")]
        if ports:
            found[name] = {"name": name, "port": ports[0],
                           "harbor": labels.get(service_virt.SV_POD_HARBOR_LABEL),
                           "ship": labels.get(service_virt.SV_POD_SHIP_LABEL)}
    return [found[n] for n in sorted(found)]


# Why a namespace could not be read. The web UI needs these where sv_mocks()
# folds every failure into "no mocks": many of its users have no kubecontext.
SV_READ_OK = "ok"
SV_READ_NO_CLI = "no_cli"
SV_READ_NO_CONTEXT = "no_context"
SV_READ_DENIED = "denied"
SV_READ_NO_MOCKS = "no_mocks"

SvClusterRead = collections.namedtuple("SvClusterRead", "status mocks detail")


def sv_read(namespace, timeout=15):
    """sv_mocks() for a caller that may have no cluster: the same read, plus
    which way it failed. `timeout` because kubectl retries an unreachable API
    server rather than failing, and a browser request cannot wait that out."""
    try:
        cli = kube.cli_tool()
    except RuntimeError as e:
        return SvClusterRead(SV_READ_NO_CLI, [], str(e))
    try:
        out = kube.quiet([cli, "get", "pods", "-n", namespace, "-o", "json"],
                         timeout=timeout)
    except subprocess.TimeoutExpired:
        return SvClusterRead(SV_READ_NO_CONTEXT, [],
                             f"{cli} did not answer within {timeout}s")
    if out.returncode != 0:
        err = (out.stderr or out.stdout or "").strip()
        return SvClusterRead(_sv_read_reason(err), [], err)
    try:
        pods = json.loads(out.stdout or "{}").get("items", [])
    except ValueError:
        # A zero exit with unparseable output (a wrapper or plugin printed
        # first) is an unreadable cluster, not a traceback.
        return SvClusterRead(SV_READ_NO_CONTEXT, [], (out.stdout or "").strip())
    mocks = _sv_mocks(pods)
    return SvClusterRead(SV_READ_OK if mocks else SV_READ_NO_MOCKS, mocks, "")


def _sv_read_reason(stderr):
    """Classify a failed `get pods` by its message: kubectl and oc exit 1 for
    all of these, so the text is the only signal."""
    e = stderr.lower()
    if "forbidden" in e or "unauthorized" in e or "must be logged in" in e:
        return SV_READ_DENIED
    if "not found" in e or "notfound" in e:
        # A missing namespace answers like an empty one: nothing to expose yet.
        return SV_READ_NO_MOCKS
    # No kubeconfig, no context, refused, DNS, moved server: one way forward,
    # and the raw message travels as the detail.
    return SV_READ_NO_CONTEXT
