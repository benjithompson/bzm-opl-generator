"""The LaunchAgent installer, without launchd.

Everything launchctl-shaped is recorded rather than run: what these tests pin
is the plist that lands on disk and the order of the calls around it, which is
the part a refactor can silently break. Whether launchd itself accepts the
plist is only provable on a Mac with a login session, and a test that skips
off-macOS is the fastapi problem again -- so nothing here needs darwin.
"""

import os
import plistlib
import sys

import pytest

from bzm_opl_gen import service


class _Done:
    def __init__(self, rc=0, stderr=""):
        self.returncode = rc
        self.stderr = stderr
        self.stdout = ""


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A darwin-shaped environment with launchctl recorded."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # bootout of a label that is not loaded fails; install must not care.
        return _Done(rc=36 if cmd[1] == "bootout" else 0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    monkeypatch.setattr(service.sys, "platform", "darwin")
    monkeypatch.setattr(service, "plist_path",
                        lambda: str(tmp_path / "agents" / "ui.plist"))
    monkeypatch.setattr(service, "log_path",
                        lambda: str(tmp_path / "logs" / "ui.log"))
    return calls


def test_plist_serves_with_this_python_and_never_opens_a_browser():
    p = service.build_plist(port=9001, host="127.0.0.1",
                            api_key_path="~/keys/api-key.json")
    args = p["ProgramArguments"]
    assert args[0] == sys.executable          # the installing venv serves
    assert "--no-browser" in args             # a crash loop must not tab-storm
    assert args[args.index("--port") + 1] == "9001"
    key = args[args.index("--api-key") + 1]
    assert os.path.isabs(key) and "~" not in key  # launchd's cwd is nobody's
    assert p["KeepAlive"] is True and p["RunAtLoad"] is True
    # #224: this process serves the checkout that installed it, and without
    # reload it serves whatever that checkout was at login -- for days, with
    # nothing on the page to say so. Releases are the distribution; a local
    # install exists to test the working tree.
    assert "--dev" in args


def test_install_writes_the_plist_and_boots_out_before_bootstrapping(rig):
    out = service.install(port=8765)
    bootout, bootstrap = rig
    assert bootout[:2] == ["launchctl", "bootout"]
    assert bootstrap[:2] == ["launchctl", "bootstrap"]
    assert bootstrap[3] == service.plist_path()
    with open(service.plist_path(), "rb") as fh:
        assert plistlib.load(fh)["Label"] == service.LABEL
    assert out["url"] == "http://127.0.0.1:8765"
    assert out["log"] == service.log_path()


def test_bootstrap_failure_surfaces_launchctls_words(rig, monkeypatch):
    def fail_bootstrap(cmd, **kw):
        rig.append(cmd)
        return _Done(rc=5, stderr="Bootstrap failed: 5: Input/output error") \
            if cmd[1] == "bootstrap" else _Done(rc=36)
    monkeypatch.setattr(service.subprocess, "run", fail_bootstrap)
    with pytest.raises(service.ServiceError, match="Input/output error"):
        service.install()


def test_uninstall_says_when_there_was_nothing(rig):
    assert service.uninstall() == {"removed": None}


def test_uninstall_removes_what_install_wrote(rig):
    service.install()
    out = service.uninstall()
    assert out["removed"] == service.plist_path()
    assert not os.path.exists(service.plist_path())


def test_off_macos_is_a_refusal_that_names_the_alternative(monkeypatch):
    monkeypatch.setattr(service.sys, "platform", "linux")
    with pytest.raises(service.ServiceError, match="systemd"):
        service.install()
    with pytest.raises(service.ServiceError):
        service.uninstall()
