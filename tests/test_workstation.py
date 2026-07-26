"""Offline counterparts for the workstation preflight.

Every check is a pure function over gather()'s dict, so the failure modes are
covered here without needing a machine that is actually missing kubectl.
"""

import pytest

from bzm_opl_gen import workstation as ws
from bzm_opl_gen.doctor import PASS, WARN, FAIL, has_failures
from bzm_opl_gen.livetest import PROXY_IMAGE, REGISTRY_IMAGE

# A workstation with everything present, on amd64. Checks below vary one field.
OK_ENV = {
    "python": (3, 12, 1),
    "arch": "x86_64",
    "kubectl": "Client Version: v1.30.0",
    "oc": None,
    "kind": "kind v0.23.0",
    "minikube": "minikube version: v1.33.1",
    "docker": "Docker version 27.0.3",
    "docker_running": True,
    "docker_context": "desktop-linux",
    "kube_context": "minikube",
    "images": {PROXY_IMAGE: True, REGISTRY_IMAGE: True},
    "port_busy": False,
    "free_gb": (120.0, "host disk"),
}

FULL_RIG = {"cluster": "minikube", "local_registry": 5001, "local_proxy": True}


def _env(**over):
    return {**OK_ENV, **over}


def _status(opts, env, name):
    return [c.status for c in
            [c for check in ws.CHECKS for c in check(opts, env)] if c.name == name]


def test_healthy_workstation_has_no_failures():
    checks = [c for check in ws.CHECKS for c in check(FULL_RIG, OK_ENV)]
    assert not has_failures(checks)
    assert all(c.status == PASS for c in checks), [c for c in checks if c.status != PASS]


# -- the tools livetest shells out to -----------------------------------------

def test_no_kube_cli_is_a_failure():
    """livetest.cli_tool() raises for this, but only after the cluster is up."""
    assert _status(FULL_RIG, _env(kubectl=None, oc=None), "kube cli") == [FAIL]


def test_kubectl_alone_passes_and_says_openshift_needs_oc():
    checks = ws.check_kube_cli(FULL_RIG, _env(oc=None))
    assert checks[0].status == PASS and "OpenShift" in checks[0].detail


def test_oc_is_preferred_when_both_present():
    detail = ws.check_kube_cli(FULL_RIG, _env(oc="Client Version: 4.15"))[0].detail
    assert detail.startswith("oc:")


def test_missing_cluster_tool_is_a_failure():
    assert _status({"cluster": "kind"}, _env(kind=None), "cluster") == [FAIL]
    assert _status({"cluster": "minikube"}, _env(minikube=None), "cluster") == [FAIL]


def test_kind_missing_does_not_fail_a_minikube_run():
    assert _status({"cluster": "minikube"}, _env(kind=None), "cluster") == [PASS]


def test_cluster_current_needs_a_kubeconfig_context():
    assert _status({"cluster": "current"}, _env(kube_context=None), "cluster") == [FAIL]


def test_docker_not_needed_without_a_rig():
    """--cluster current with no rig flags never shells out to docker."""
    c = ws.check_docker({"cluster": "current"}, _env(docker=None, docker_running=False))
    assert c[0].status == PASS


@pytest.mark.parametrize("opts", [
    {"cluster": "minikube"}, {"cluster": "kind"},
    {"cluster": "current", "local_registry": 5001},
    {"cluster": "current", "local_proxy": True},
])
def test_docker_required_by_every_rig_path(opts):
    assert ws.check_docker(opts, _env(docker=None))[0].status == FAIL


def test_docker_installed_but_daemon_down():
    c = ws.check_docker(FULL_RIG, _env(docker_running=False))
    assert c[0].status == FAIL and "unreachable" in c[0].detail


# -- environment traps ---------------------------------------------------------

def test_arm64_warns_about_emulated_engines():
    c = ws.check_arch(FULL_RIG, _env(arch="arm64"))
    assert c[0].status == WARN and "--engine-cpu 1" in c[0].detail


def test_python_below_the_pyproject_floor_fails():
    assert ws.check_python(FULL_RIG, _env(python=(3, 8, 10)))[0].status == FAIL
    assert ws.check_python(FULL_RIG, _env(python=(3, 9, 6)))[0].status == PASS


def test_occupied_registry_port_fails():
    """--local-registry publishes on the host, so a taken port kills the run."""
    c = ws.check_registry_port(FULL_RIG, _env(port_busy=True))
    assert c[0].status == FAIL and "5001" in c[0].detail


def test_registry_port_unchecked_without_the_flag():
    assert ws.check_registry_port({"cluster": "minikube"}, OK_ENV) == []


def test_uncached_rig_images_warn_not_fail():
    """They pull on first use -- slow, and needs egress, but not fatal."""
    c = ws.check_rig_images(FULL_RIG, _env(images={}))
    assert [x.status for x in c] == [WARN, WARN]
    assert PROXY_IMAGE in c[0].detail          # the pin, not :latest


def test_rig_images_only_checked_for_the_flags_passed():
    assert ws.check_rig_images({"cluster": "minikube"}, OK_ENV) == []
    assert len(ws.check_rig_images({"local_proxy": True}, OK_ENV)) == 1


def test_disk_thresholds():
    assert ws.check_disk(FULL_RIG, _env(free_gb=(3.0, "colima VM")))[0].status == FAIL
    assert ws.check_disk(FULL_RIG, _env(free_gb=(12.0, "colima VM")))[0].status == WARN
    assert ws.check_disk(FULL_RIG, _env(free_gb=(120.0, "host disk")))[0].status == PASS


def test_unreadable_disk_warns_and_names_the_minikube_error():
    c = ws.check_disk(FULL_RIG, _env(free_gb=(None, "colima VM")))
    assert c[0].status == WARN and "RSRC_DOCKER_STORAGE" in c[0].detail


def test_disk_unchecked_without_a_rig():
    assert ws.check_disk({"cluster": "current"}, OK_ENV) == []


def test_disk_silent_when_docker_is_absent():
    """gather() can't name a provider without a daemon, and check_docker has
    already failed -- reporting 'free space on the None' helps nobody."""
    assert ws.check_disk(FULL_RIG, _env(docker=None, docker_running=False,
                                        free_gb=(None, None))) == []


# -- reporting -----------------------------------------------------------------

def test_run_reports_and_returns_checks(capsys):
    checks = ws.run(FULL_RIG, env=_env(kubectl=None, oc=None))
    out = capsys.readouterr().out
    assert "toolcheck: --cluster minikube --local-registry 5001 --local-proxy" in out
    assert "livetest would not get as far as deploying" in out
    assert has_failures(checks)
