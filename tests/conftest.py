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


@pytest.fixture(autouse=True)
def _no_registry_network():
    """No offline test reaches a real registry: every registry read answers
    `unread`, as if offline. A test that needs an answer fakes
    `registry_client.http_request` itself. Not `monkeypatch`, whose teardown
    order would change other modules' fixtures."""
    from bzm_opl_gen import registry_client

    def offline(method, url, *a, **kw):
        raise registry_client.Unreachable(f"offline test: {method} {url}")

    real = registry_client.http_request
    registry_client.http_request = offline
    yield
    registry_client.http_request = real
