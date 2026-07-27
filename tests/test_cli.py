"""The command line, driven the way a user drives it.

Everything here goes through `cli.main()` with a faked argv, so the flags are
exercised alongside the function behind them -- a renamed or dropped flag fails
here rather than in whatever calls the command function directly.

`sv-expose` is the one covered so far, and deliberately: it is the fallback for
a cluster where crane's own Ingress will not route, it is the only way to set
the ingress class now that the web UI does not offer one, and its output used to
be checked only through the browser endpoint that has since been removed.
"""

import json
import os

import pytest
import yaml

from bzm_opl_gen import cli, generate as gen, livetest
# The faked kubectl and pod shapes live with the cluster-reading tests; reused
# rather than re-declared so every layer exercises the same stand-in binary.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402

SV_PODS = json.dumps({"items": [
    _sv_pod("vs1svc2", 8080, "aaa111", "bbb222"),
    # crane's own pod shares the namespace and carries none of the identity
    # labels -- rendering a Service for it would point at nothing.
    _sv_pod(None, 5000, extra={"role": "role-crane"})]})


@pytest.fixture
def fake_cluster(monkeypatch):
    """Installs a faked kubectl/oc for one test. Yields the installer so a test
    can choose what the binary answers; always clears cli_tool()'s memo of which
    binary exists, which otherwise outlives the monkeypatch."""
    def install(**kw):
        _fake_kubectl(monkeypatch, **kw)
    install()
    yield install
    livetest.cli_tool.cache_clear()


def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    cli.main()


def _docs(path):
    return [d for d in yaml.safe_load_all(open(path).read()) if d]


def test_sv_expose_writes_a_pair_per_deployed_mock(fake_cluster, monkeypatch,
                                                   tmp_path, capsys):
    """The whole command, end to end: read the namespace, render, write, and say
    how to apply it. The mocks come off the running pods because the v4 API
    exposes no virtual-service endpoint."""
    fake_cluster(stdout=SV_PODS)
    out = tmp_path / gen.SV_EXPOSE_FILE
    _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
         "--sv-subdomain", "apps.example.com",
         "--sv-tls-secret", "wildcard-tls", "-o", str(out))
    docs = _docs(out)
    assert [d["kind"] for d in docs] == ["Service", "Ingress"]
    assert {d["metadata"]["namespace"] for d in docs} == {"ns1"}
    assert (docs[1]["spec"]["rules"][0]["host"]
            == "vs1svc2-8080-ns1.apps.example.com")
    printed = capsys.readouterr().out
    assert "1 virtual service(s) -- vs1svc2:8080" in printed
    assert f"kubectl apply -n ns1 -f {out}" in printed


def test_sv_expose_ingress_class_reaches_the_ingress(fake_cluster, monkeypatch,
                                                     tmp_path):
    """We own this Ingress, so `--ingress-class` can name the class the cluster
    really has: on OpenShift that is `openshift-default`, and naming it is what
    removes the need for a cluster-admin `nginx` IngressClass alias. Unset, it
    falls back to nginx, which is what most clusters register."""
    fake_cluster(stdout=SV_PODS)

    def ing(*extra):
        out = tmp_path / f"{len(extra)}-{gen.SV_EXPOSE_FILE}"
        _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
             "--sv-subdomain", "apps.example.com", "-o", str(out), *extra)
        return next(d for d in _docs(out) if d["kind"] == "Ingress")

    assert (ing("--ingress-class", "openshift-default")
            ["spec"]["ingressClassName"] == "openshift-default")
    assert (ing()["spec"]["ingressClassName"]
            == gen.SV_EXPOSE_DEFAULT_INGRESS_CLASS)


def test_sv_expose_reads_the_bundles_profile_rather_than_repeating_flags(
        fake_cluster, monkeypatch, tmp_path):
    """--manifests defaults to the generated bundle, and the namespace, wildcard
    domain, TLS secret and ingress class all come back out of its profile.json:
    the documented way to run this without restating what `generate` was already
    told. `sv_ingress_class` reaches it as a plain option -- nothing about it
    goes to the agent, so a bundle generated without one is unchanged."""
    from test_generate import FACTS
    gen.write(gen.generate(FACTS, {
        "namespace": "ns1", "sv_ingress": "nginx",
        "sv_subdomain": "apps.example.com", "sv_tls_secret": "wildcard-tls",
        "sv_ingress_class": "openshift-default"}), str(tmp_path))
    fake_cluster(stdout=SV_PODS)
    out = tmp_path / gen.SV_EXPOSE_FILE
    _run(monkeypatch, "sv-expose", "--manifests", str(tmp_path), "-o", str(out))
    ing = next(d for d in _docs(out) if d["kind"] == "Ingress")
    assert ing["metadata"]["namespace"] == "ns1"
    assert ing["spec"]["rules"][0]["host"] == "vs1svc2-8080-ns1.apps.example.com"
    assert ing["spec"]["tls"][0]["secretName"] == "wildcard-tls"
    assert ing["spec"]["ingressClassName"] == "openshift-default"


def test_sv_expose_runs_from_anywhere_with_no_profile_at_all(fake_cluster,
                                                             monkeypatch,
                                                             tmp_path):
    """`--manifests ''` skips the profile entirely, so every option can come
    from the command line. That is the shape to hand someone who has cluster
    access but never downloaded a bundle -- it must not go looking for out/."""
    fake_cluster(stdout=SV_PODS)
    monkeypatch.chdir(tmp_path)
    assert not os.path.exists("out")
    _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
         "--sv-subdomain", "apps.example.com", "--ingress-class", "nginx")
    ing = next(d for d in _docs(gen.SV_EXPOSE_FILE) if d["kind"] == "Ingress")
    assert ing["spec"]["rules"][0]["host"] == "vs1svc2-8080-ns1.apps.example.com"


def test_sv_expose_says_to_deploy_first_when_the_namespace_is_empty(
        fake_cluster, monkeypatch, tmp_path):
    """Nothing to render is not an error to swallow: an empty file would apply
    cleanly and leave the endpoint 503ing. The mocks exist only once the virtual
    service has been deployed from BlazeMeter."""
    fake_cluster(stdout=json.dumps({"items": []}))
    out = tmp_path / gen.SV_EXPOSE_FILE
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
             "--sv-subdomain", "apps.example.com", "-o", str(out))
    assert "no virtual-service pods in namespace ns1" in str(e.value)
    assert not out.exists()


def test_sv_expose_refuses_without_a_wildcard_domain(fake_cluster, monkeypatch,
                                                     tmp_path):
    """The endpoint host is <mock>-<port>-<namespace>.<domain>, so without a
    domain there is no host to route: sv_publish_cfg refuses, and it does so
    before anything is written rather than leaving a half-rendered file."""
    fake_cluster(stdout=SV_PODS)
    out = tmp_path / gen.SV_EXPOSE_FILE
    with pytest.raises(ValueError, match="sv_subdomain"):
        _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
             "-o", str(out))
    assert not out.exists()
