"""Run the web UI as a macOS LaunchAgent, so no terminal has to stay open.

A LaunchAgent rather than a container: the UI writes bundles where `kubectl
apply` can see them, and a container would put a filesystem boundary there.

stdlib only: the CLI imports this before the `[ui]` extra is known to exist.
"""

import os
import plistlib
import subprocess
import sys

LABEL = "com.blazemeter.bzm-opl-gen.ui"


class ServiceError(Exception):
    pass


def plist_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def log_path():
    return os.path.expanduser("~/Library/Logs/bzm-opl-gen-ui.log")


def build_plist(port=8765, host="127.0.0.1", api_key_path=None):
    """The agent definition, as a dict for plistlib.

    Runs sys.executable, so the venv that installed it serves (reinstall if it
    moves). Always --no-browser (launchd restarts would open tabs) and --dev
    (a local install tracks the working tree; `ui_dist` still needs a rebuild,
    see server.build_state).
    """
    args = [sys.executable, "-m", "bzm_opl_gen", "ui", "--no-browser", "--dev",
            "--port", str(port), "--host", host]
    if api_key_path:
        # Absolute: launchd starts this in a working directory nobody chose.
        args += ["--api-key", os.path.abspath(os.path.expanduser(api_key_path))]
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log_path(),
        "StandardErrorPath": log_path(),
    }


def _domain():
    return f"gui/{os.getuid()}"


def _require_darwin():
    if sys.platform != "darwin":
        raise ServiceError(
            "LaunchAgents are macOS only. On Linux, a systemd user unit is "
            "the equivalent: `systemctl --user` running the same "
            f"`{sys.executable} -m bzm_opl_gen ui --no-browser` command.")


def install(port=8765, host="127.0.0.1", api_key_path=None):
    """Write the plist and load it. Returns {plist, log, url}.

    bootout first, result ignored: bootstrap refuses a label already loaded,
    and when nothing was loaded bootout's failure is expected.
    """
    _require_darwin()
    path = plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.dirname(log_path()), exist_ok=True)
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"],
                   capture_output=True)
    with open(path, "wb") as fh:
        plistlib.dump(build_plist(port, host, api_key_path), fh)
    r = subprocess.run(["launchctl", "bootstrap", _domain(), path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ServiceError(f"launchctl bootstrap failed: "
                           f"{r.stderr.strip() or r.stdout.strip()}")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return {"plist": path, "log": log_path(), "url": f"http://{shown}:{port}"}


def uninstall():
    """Unload and remove. Returns {"removed": path}, or None for the path when
    nothing was installed."""
    _require_darwin()
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"],
                   capture_output=True)
    path = plist_path()
    if os.path.exists(path):
        os.unlink(path)
        return {"removed": path}
    return {"removed": None}
