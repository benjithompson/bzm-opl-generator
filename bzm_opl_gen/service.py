"""Run the web UI as a macOS LaunchAgent, so no terminal has to stay open.

launchd rather than docker, deliberately: the UI's one job that touches the
world outside the browser is writing bundles somewhere `kubectl apply` can see
them, and a container puts a filesystem boundary exactly there. A LaunchAgent
is the same native process `bzm-opl-gen ui` starts by hand -- same paths, same
key discovery -- just started at login and restarted if it dies.

stdlib only. This module is imported by the CLI before anyone has asked for
the `[ui]` extra's dependencies, so an ImportError here would break `--help`
for everything.
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

    ProgramArguments starts with sys.executable: the venv that ran
    --install-service is the one that serves, which is what makes an editable
    checkout's UI the one you get. It also means moving or deleting that venv
    silently kills the service -- reinstall after either.

    --no-browser always: launchd starts this at login and on every crash, and
    each start popping a browser tab would turn a restart loop into a tab
    storm.
    """
    args = [sys.executable, "-m", "bzm_opl_gen", "ui", "--no-browser",
            "--port", str(port), "--host", host]
    if api_key_path:
        # Absolute for the same reason core refuses relative out_dirs: launchd
        # starts this process in a working directory nobody chose.
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

    bootout first, ignoring its result: `launchctl bootstrap` refuses a label
    that is already loaded, so a reinstall (new port, new key) has to unload
    the old definition -- and when nothing was loaded, bootout's failure is
    the expected case, not a problem.
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
    """Unload and remove. Returns what it removed, or names what was absent --
    'there was nothing to remove' is an answer, not an error."""
    _require_darwin()
    subprocess.run(["launchctl", "bootout", f"{_domain()}/{LABEL}"],
                   capture_output=True)
    path = plist_path()
    if os.path.exists(path):
        os.unlink(path)
        return {"removed": path}
    return {"removed": None}
