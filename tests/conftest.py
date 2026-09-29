"""Suite-wide guard: no offline test may reach a real cluster or container tool.

Tests fake these at `subprocess.run` (or at `kube.*`); one that forgets would
otherwise talk to whatever cluster this machine has. Other binaries (`sh`,
which the generated-script tests run) pass through.
"""

import os
import subprocess

import pytest

_BLOCKED = {"kubectl", "oc", "docker", "minikube", "kind", "helm", "colima"}


@pytest.fixture(autouse=True, scope="session")
def _no_cluster_binaries():
    real = subprocess.run

    def guarded(cmd, *a, **kw):
        argv = cmd.split() if isinstance(cmd, str) else list(cmd)
        if argv and os.path.basename(str(argv[0])) in _BLOCKED:
            raise AssertionError(f"offline test ran a real {argv[0]}: {argv[:4]}")
        return real(cmd, *a, **kw)

    subprocess.run = guarded
    yield
    subprocess.run = real
