"""Pre-flight the *workstation*, not the cluster.

`doctor` asks whether the customer's cluster can run the location. This asks
the question that comes first for anyone working on the code: does this laptop
have what `livetest` shells out to, before a 12-20 minute run discovers it the
hard way. The rig calls kubectl/oc, docker, kind or minikube, and pulls two
pinned images; every one of those was a bare FileNotFoundError partway through
a run until this existed.

Same shape as doctor: gather() is the only impure part, every check is a pure
function over what it returned, so the whole thing is testable offline.

FAIL = the run cannot start. WARN = it starts and then bites you.
"""

import os
import platform
import shutil
import socket
import subprocess
import sys

from .doctor import Check, PASS, WARN, FAIL
from .livetest import PROXY_IMAGE, REGISTRY_IMAGE

MIN_PYTHON = (3, 9)             # the floor declared in pyproject.toml

# minikube pulls the engine image (~2GB) plus crane and the mirror copies; the
# VM disk filling up surfaces as minikube's RSRC_DOCKER_STORAGE, which does not
# mention disk at all.
DISK_WARN_GB = 20
DISK_FAIL_GB = 5


def _version(cmd):
    """First line of `cmd --version`, or None if the tool is not runnable."""
    if not shutil.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return out[0].strip() if out else ""


def _port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _free_gb():
    """Free space docker can still grow into, and where that number came from.

    Which number binds depends on how the provider stores its disk, not on
    which provider it is. A preallocated VM disk (colima and friends) is capped
    regardless of host free space, so the VM's own df is the answer; a sparse
    disk image on the host filesystem (Docker Desktop, and the default for most
    others) grows until the host fills, so host free space is. Host free space
    is the fallback because it is right for every provider we can't interrogate
    directly -- and it is never an *under*-estimate of the constraint.
    """
    if shutil.which("colima"):
        r = subprocess.run(["colima", "ssh", "--", "df", "-Pk", "/"],
                           capture_output=True, text=True, timeout=60)
        lines = r.stdout.strip().splitlines() if r.returncode == 0 else []
        if len(lines) >= 2:
            try:
                return int(lines[1].split()[3]) * 1024 / 10 ** 9, "colima VM"
            except (IndexError, ValueError):
                pass
        return None, "colima VM"
    return shutil.disk_usage(os.sep).free / 10 ** 9, "host disk"


def gather(opts):
    """Probe the workstation. The only impure function in this module."""
    docker_ok, docker_ctx = False, None
    if shutil.which("docker"):
        r = subprocess.run(["docker", "info", "--format", "{{.Name}}"],
                           capture_output=True, text=True, timeout=60)
        docker_ok = r.returncode == 0
        docker_ctx = r.stdout.strip() or None

    images = {}
    if docker_ok:
        for ref in (PROXY_IMAGE, REGISTRY_IMAGE):
            r = subprocess.run(["docker", "image", "inspect", ref],
                               capture_output=True, text=True)
            images[ref] = r.returncode == 0

    port = opts.get("local_registry")
    return {
        "python": sys.version_info[:3],
        "arch": platform.machine(),
        "kubectl": _version(["kubectl", "version", "--client"]),
        "oc": _version(["oc", "version", "--client"]),
        "kind": _version(["kind", "version"]),
        "minikube": _version(["minikube", "version"]),
        "docker": _version(["docker", "--version"]),
        "docker_running": docker_ok,
        "docker_context": docker_ctx,
        "kube_context": _version(["kubectl", "config", "current-context"]),
        "images": images,
        "port_busy": _port_busy(port) if port else None,
        "free_gb": _free_gb() if docker_ok else (None, None),
    }


# -- checks -------------------------------------------------------------------

def check_python(opts, env):
    got = ".".join(str(n) for n in env["python"])
    want = ".".join(str(n) for n in MIN_PYTHON)
    if env["python"][:2] < MIN_PYTHON:
        return [Check("python", FAIL, f"{got} -- pyproject requires >= {want}")]
    return [Check("python", PASS, got)]


def check_kube_cli(opts, env):
    """livetest.cli_tool() prefers oc, falls back to kubectl, and raises if
    neither is there -- after the cluster is already up."""
    for name in ("oc", "kubectl"):
        if env[name]:
            other = "kubectl" if name == "oc" else "oc"
            note = "" if env[other] else f" (no {other}; fine unless you target OpenShift)"
            return [Check("kube cli", PASS, f"{name}: {env[name]}{note}")]
    return [Check("kube cli", FAIL,
                  "neither oc nor kubectl on PATH -- livetest and doctor both "
                  "shell out to one of them")]


def _needs_docker(opts):
    return (opts.get("cluster") in ("kind", "minikube")
            or opts.get("local_registry") or opts.get("local_proxy"))


def check_docker(opts, env):
    if not _needs_docker(opts):
        return [Check("docker", PASS, "not needed for --cluster current "
                                      "without --local-registry/--local-proxy")]
    if not env["docker"]:
        return [Check("docker", FAIL, "no docker on PATH -- needed for the "
                                      "cluster driver and the rig containers")]
    if not env["docker_running"]:
        return [Check("docker", FAIL,
                      f"{env['docker']} present but the daemon is unreachable "
                      f"-- start whatever provides it on this host")]
    ctx = f", daemon '{env['docker_context']}'" if env["docker_context"] else ""
    return [Check("docker", PASS, f"{env['docker']}{ctx}")]


def check_cluster_tool(opts, env):
    cluster = opts.get("cluster") or "current"
    if cluster == "current":
        if not env["kube_context"]:
            return [Check("cluster", FAIL,
                          "--cluster current uses the active kubeconfig "
                          "context, and none is set")]
        return [Check("cluster", PASS, f"current context: {env['kube_context']}")]
    if not env[cluster]:
        return [Check("cluster", FAIL,
                      f"--cluster {cluster} needs {cluster} on PATH")]
    return [Check("cluster", PASS, f"{cluster}: {env[cluster]}")]


def check_arch(opts, env):
    """BlazeMeter publishes amd64 only. Pods still run under emulation, but
    engines are slow enough to sit Pending at the documented sizing."""
    if env["arch"] not in ("arm64", "aarch64"):
        return [Check("arch", PASS, env["arch"])]
    return [Check("arch", WARN,
                  f"{env['arch']} -- BlazeMeter images are amd64-only and run "
                  f"under emulation here; size engines down "
                  f"(--engine-cpu 1 --engine-mem 4Gi) or they stay Pending")]


def check_registry_port(opts, env):
    port = opts.get("local_registry")
    if not port:
        return []
    if env["port_busy"]:
        return [Check("registry port", FAIL,
                      f"something is already listening on {port} -- "
                      f"--local-registry publishes the registry there and the "
                      f"docker run will fail")]
    return [Check("registry port", PASS, f"{port} free")]


def check_rig_images(opts, env):
    """Both are pinned. mitmproxy in particular: >= 12 dies with SIGILL on
    arm64 VMs, so a cached `latest` is not a substitute."""
    out = []
    wanted = []
    if opts.get("local_proxy"):
        wanted.append(PROXY_IMAGE)
    if opts.get("local_registry"):
        wanted.append(REGISTRY_IMAGE)
    for ref in wanted:
        if env["images"].get(ref):
            out.append(Check("rig image", PASS, f"{ref} cached"))
        else:
            out.append(Check("rig image", WARN,
                             f"{ref} not cached -- first run pulls it "
                             f"(needs registry egress)"))
    return out


def check_disk(opts, env):
    # Nothing to say about the docker VM's disk when there is no reachable
    # docker -- check_docker already reported the failure that matters.
    if not _needs_docker(opts) or not env["docker_running"]:
        return []
    free, where = env["free_gb"]
    if free is None:
        return [Check("docker disk", WARN,
                      f"could not read free space on the {where} -- if it fills "
                      f"up, minikube fails with RSRC_DOCKER_STORAGE, which does "
                      f"not mention disk")]
    detail = f"{free:.0f}GB free on the {where}"
    if free < DISK_FAIL_GB:
        return [Check("docker disk", FAIL, detail + " -- minikube will fail to start")]
    if free < DISK_WARN_GB:
        return [Check("docker disk", WARN,
                      detail + f" -- under {DISK_WARN_GB}GB, mirroring the "
                               f"engine image may fill it")]
    return [Check("docker disk", PASS, detail)]


CHECKS = (check_python, check_kube_cli, check_docker, check_cluster_tool,
          check_arch, check_registry_port, check_rig_images, check_disk)


def run(opts, env=None):
    """Run every check and print the verdict list. Returns the Check list; the
    caller decides the exit code (doctor.has_failures)."""
    opts = dict(opts or {})
    env = gather(opts) if env is None else env
    checks = [c for check in CHECKS for c in check(opts, env)]
    _report(opts, checks)
    return checks


def _report(opts, checks):
    intent = [f"--cluster {opts.get('cluster') or 'current'}"]
    if opts.get("local_registry"):
        intent.append(f"--local-registry {opts['local_registry']}")
    if opts.get("local_proxy"):
        intent.append("--local-proxy")
    print("toolcheck: " + " ".join(intent))
    width = max((len(c.name) for c in checks), default=0)
    for c in checks:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
    fails = sum(1 for c in checks if c.status == FAIL)
    warns = sum(1 for c in checks if c.status == WARN)
    print(f"{len(checks) - fails - warns} passed, {warns} warning(s), "
          f"{fails} failure(s)"
          + ("" if not fails else " -- livetest would not get as far as deploying"))
