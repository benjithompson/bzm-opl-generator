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

from bzm_opl_gen import cli, core, generate as gen, livetest
# The faked kubectl and pod shapes live with the cluster-reading tests; reused
# rather than re-declared so every layer exercises the same stand-in binary.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402
# The stand-in account, and the one that will not issue a credential, as core's
# suite declares them.
from test_core import FakeClient, RefusingClient  # noqa: E402

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


# -- generate: which token the bundle gets, and what minted it -----------------
# The rule is core.resolve_auth_token so the four callers of it cannot disagree.
# These drive it the way a user hits it, through the flags -- including the one
# that matters most, which is that no flag combination except --rotate-token
# reaches the endpoint at all.

def test_images_pull_plans_the_mirror_without_crashing(monkeypatch, tmp_path,
                                                       capsys):
    """`images --pull` raised `NameError: name 'dry' is not defined` on every
    invocation -- the guard at the end of the command tested a name that never
    existed, and it is evaluated unconditionally once --pull is given. Nothing
    covered this path, which is how a crash on the happy path survived."""
    monkeypatch.setattr(core, "_docker", lambda args, dry_run: " ".join(
        ["docker"] + args))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path, ["b1"]),
         "--pull", "--dry-run", "--mirror", "reg.local/bzm")
    out = capsys.readouterr().out
    assert "DRY-RUN: docker pull" in out and "DRY-RUN: docker push" in out


def test_listing_images_touches_no_docker_at_all(monkeypatch, tmp_path, capsys):
    """Without --pull this only lists, and must reach nothing. It returns before
    the mirror block, so the crash that block used to raise never reached anyone
    listing -- which is also why nobody noticed the crash."""
    monkeypatch.setattr(core, "_docker", lambda *a, **k: pytest.fail(
        "listing images ran docker"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail(
        "listing images shelled out"))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path, ["b1"]))
    assert "gcr.io/" in capsys.readouterr().out, "it listed nothing"


def test_the_cli_never_runs_docker_itself(monkeypatch, tmp_path):
    """core.mirror_images does the pull/tag/push, and the loop here only prints
    what it did -- the command's own comment says core owns it so this and the
    MCP tool cannot disagree. The old code also called subprocess on the loop
    variable *after* the loop, re-running just the last command."""
    ran = []
    monkeypatch.setattr(core, "_docker",
                        lambda args, dry_run: ran.append(args) or "docker")
    # Patched on the module, not on cli's reference to it: cli no longer imports
    # subprocess at all, and this way the assertion holds for any path that
    # starts shelling out later, wherever it lives.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail(
        "the CLI ran docker itself; core.mirror_images is the only path"))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path, ["b1"]),
         "--pull", "--mirror", "reg.local/bzm")
    verbs = [a[0] for a in ran]
    assert verbs, "nothing was mirrored at all"
    assert verbs.count("pull") == verbs.count("tag") == verbs.count("push"), \
        f"one pull, tag and push per image; got {verbs}"


def _facts_file(tmp_path, ships):
    f = json.load(open("examples/facts.example.json"))
    if ships is None:
        f.pop("ships")
    else:
        f["ships"] = [dict(f["ships"][0], id=s) for s in ships]
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(f))
    return str(path)


def _generate(monkeypatch, tmp_path, *extra, ships=("b1",), out=None,
              client=None):
    """`generate` with a faked account, run the way a user runs it."""
    monkeypatch.setattr(cli.api, "BzmClient",
                        lambda *a, **k: client or FakeClient())
    _run(monkeypatch, "generate", "--facts", _facts_file(tmp_path, list(ships)),
         "-o", str(out or (tmp_path / "out")), *extra)


def test_generate_with_an_api_key_alone_mints_nothing(monkeypatch, tmp_path,
                                                      capsys):
    """The break in #64. `--api-key` used to be enough to rotate the credential
    of a running agent, which is why regenerating a bundle to look at it took an
    agent down. The flag now means only "the credential for --rotate-token", and
    on its own it says so rather than doing nothing quietly."""
    c = FakeClient()
    _generate(monkeypatch, tmp_path, "--api-key",
              "examples/api-key.example.json", client=c)
    assert c.calls == []
    out = capsys.readouterr()
    assert "--api-key has no effect" in out.err
    assert "--rotate-token" in out.err
    assert (gen.DEFAULT_OPTIONS["auth_token"]
            in (tmp_path / "out" / "bzm_secret.yaml").read_text())


def test_generate_rotates_only_when_told_to(monkeypatch, tmp_path, capsys):
    c = FakeClient()
    _generate(monkeypatch, tmp_path, "--api-key",
              "examples/api-key.example.json", "--rotate-token", client=c)
    assert [(name, ship) for name, _, ship in c.calls] == [("auth_token", "b1")]
    assert "rotated" in capsys.readouterr().out
    assert ("TOKEN-FROM-API"
            in (tmp_path / "out" / "bzm_secret.yaml").read_text())


def test_generate_warns_before_it_rotates(monkeypatch, tmp_path, capsys):
    """Ordering, not wording: printed afterwards this is a post-mortem. The
    warning and the mint both go to stdout so that the order survives a pipe --
    across two streams it does not."""
    class Loud(FakeClient):
        def auth_token(self, harbor_id, ship_id):
            print("MINTED")
            return super().auth_token(harbor_id, ship_id)

    _generate(monkeypatch, tmp_path, "--api-key",
              "examples/api-key.example.json", "--rotate-token", client=Loud())
    out = capsys.readouterr().out
    assert "0/1" in out and "re-appl" in out.split("MINTED")[0]
    assert out.index("ROTATING") < out.index("MINTED")


def test_rotate_token_needs_the_key_that_does_it(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--rotate-token")
    assert "--api-key" in str(caught.value)


def test_generating_twice_into_the_same_directory_changes_nothing(
        monkeypatch, tmp_path, capsys):
    """#64's acceptance criterion, at the surface a user drives: the second run
    reads the first run's token back rather than issuing one, so the bundle --
    profile.json included -- comes out byte for byte the same and the agent
    deployed from it keeps working."""
    out = tmp_path / "out"
    _generate(monkeypatch, tmp_path, "--auth-token", "REALTOKEN", out=out)
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    c = FakeClient()
    _generate(monkeypatch, tmp_path, "--api-key",
              "examples/api-key.example.json", out=out, client=c)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == first
    assert c.calls == []
    assert "reused" in capsys.readouterr().out


def test_generate_says_where_a_real_token_comes_from(monkeypatch, tmp_path,
                                                    capsys):
    """A placeholder bundle reads fine and cannot be applied, so the run that
    produces one has to name both places a real token comes from. Neither is
    this command reading a cluster -- the kubectl is printed, not run."""
    _generate(monkeypatch, tmp_path, "--namespace", "ns1")
    out = capsys.readouterr().out
    assert "create-ship" in out
    assert "kubectl -n ns1 get secret" in out and "base64 -d" in out


def test_generate_says_which_ship_it_needs_rather_than_raising(monkeypatch,
                                                               tmp_path):
    """Facts carrying no ships used to reach `len(f["ships"])` and come back a
    bare KeyError. The refusal generate() already writes names the count and
    the flag that fixes it, and is what a hand-edited facts file deserves."""
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", "generate", "--facts", _facts_file(tmp_path, None),
        "-o", str(tmp_path / "out")])
    with pytest.raises(ValueError, match="ship_id required"):
        cli.main()


def test_generate_mints_nothing_without_an_api_key(monkeypatch, tmp_path,
                                                   capsys):
    """No --api-key is not a degraded run: the manifests come out with the
    placeholder, which is the whole no-account path."""
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", "generate", "--facts", _facts_file(tmp_path, ["b1"]),
        "-o", str(out)])
    cli.main()
    assert "rotated" not in capsys.readouterr().out
    assert gen.DEFAULT_OPTIONS["auth_token"] in (out / "bzm_secret.yaml").read_text()


def _create_ship(monkeypatch, client):
    monkeypatch.setattr(cli.api, "BzmClient", lambda *a, **k: client)
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", "create-ship", "--api-key",
        "examples/api-key.example.json", "--harbor-id", "aaa111",
        "--name", "agent1"])
    cli.main()


def test_create_ship_prints_the_agent_and_its_token(monkeypatch, capsys):
    """The working path, kept alongside the refused one: the ids are printed
    before the fetch is attempted, and the token still arrives with them."""
    _create_ship(monkeypatch, FakeClient())
    out = capsys.readouterr().out
    assert "ship_id:    s2" in out
    assert "auth_token: TOKEN-FROM-API" in out


def test_create_ship_says_its_token_is_the_thing_to_keep(monkeypatch, capsys):
    """This is now the only command that issues a credential as a matter of
    course, and its output is the only copy: nothing here stores it, and
    `generate` will not go and get another one. So the durability has to be said
    out loud, and the next step has to be one that takes the token rather than
    one that mints a second -- `generate --api-key` used to be printed here and
    would now write a placeholder bundle."""
    _create_ship(monkeypatch, FakeClient())
    out = capsys.readouterr().out
    assert "Keep" in out and "durable" in out
    assert "generate" in out and "--auth-token" in out
    assert "generate --api-key" not in out


def test_create_ship_reports_a_refused_credential_and_the_ship_it_made(
        monkeypatch, capsys):
    """`create-ship` is one of the two callers that keep fetching a token, and
    the ship exists before the fetch. On an account that refuses the endpoint,
    printing only the failure loses the id -- and the next attempt creates a
    second agent for the same location."""
    with pytest.raises(SystemExit) as caught:
        _create_ship(monkeypatch, RefusingClient())
    assert "could not be issued" in str(caught.value)
    assert "--auth-token" in str(caught.value)
    assert "s2" in capsys.readouterr().out


def test_generate_reports_a_refused_credential_rather_than_tracebacking(
        monkeypatch, tmp_path, capsys):
    """The refusal is only worth writing if it is what the caller sees.

    `generate --rotate-token` is the other caller that mints, and it had no
    guard: against a real account that refuses the endpoint the sentence arrived
    at the foot of a seventy-line traceback, which is a worse answer than the raw
    403 it replaced. Found by running the command, not by a test -- every test
    until now called core directly.
    """
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--api-key",
                  "examples/api-key.example.json", "--rotate-token",
                  client=RefusingClient())
    assert "could not be issued" in str(caught.value)
    assert "--auth-token" in str(caught.value)
    assert "Traceback" not in capsys.readouterr().err


def test_generate_never_asks_which_of_two_ships(monkeypatch, tmp_path):
    """Two ships and no --ship-id: rotating the wrong one revokes a token
    belonging to an agent nobody named, so nothing is minted and the command
    names both instead."""
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--api-key",
                  "examples/api-key.example.json", "--rotate-token",
                  ships=("b1", "b2"))
    assert "b1" in str(caught.value) and "b2" in str(caught.value)


# -- livetest's one credential ------------------------------------------------
# The rig is the one command that is *supposed* to mint: bringing an agent
# online is its whole purpose. What it must not do is mint per render. Its
# regenerator called the endpoint on every invocation, and a run makes several
# -- the negative control renders twice, then --run-test and --local-proxy each
# do -- so every render revoked the credential the previous deploy was holding,
# and the agent sat 0/1 for a reason nothing in the rig reports. That is
# plausibly where the intermittent failures came from.

SHIP = "6c5b4a39281706f5e4d3c2b1"        # the sole agent in examples/facts


def _livetest(monkeypatch, tmp_path, client, *extra):
    """`livetest` with the rig itself faked, handing back the regenerate
    callback it was given so a test can drive it as many times as a real run
    would -- plus the SystemExit, since one of these runs is a refusal."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    gen.write(gen.generate(facts, {"namespace": "ns1"}), str(manifests))
    captured = {}

    def fake_run(*a, **kw):
        captured["regenerate"] = kw["regenerate"]
        return True

    monkeypatch.setattr(cli.livetest, "run", fake_run)
    monkeypatch.setattr(cli.api, "BzmClient", lambda *a, **k: client)
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key",
             "examples/api-key.example.json",
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1",
             # --run-test is what makes the rig want a regenerator at all; the
             # test id is never used, because livetest.run is faked.
             "--run-test", "12345", *extra)
    return captured.get("regenerate"), manifests, caught.value


def test_livetest_mints_one_credential_for_a_whole_run(monkeypatch, tmp_path):
    """The defect, as a count. Four renders, one mint -- and every render writes
    the same token, so an agent deployed from any of them is still holding a live
    credential when the next one lands."""
    c = FakeClient()
    regenerate, manifests, exit = _livetest(monkeypatch, tmp_path, c)
    assert exit.code == 0
    for overlay in ({"ca_bundle": None}, {"ca_bundle": "PEM"},
                    {"engine_cpu_limit": "1"}, {}):
        regenerate(overlay)
    assert [(name, ship) for name, _, ship in c.calls] == [("auth_token", SHIP)]
    assert "TOKEN-FROM-API" in (manifests / "bzm_secret.yaml").read_text()


def test_livetest_says_which_ship_it_rotated_before_it_deploys(monkeypatch,
                                                               tmp_path, capsys):
    """Named, and named up front: the run is about to replace the credential of
    whatever is already deployed against that agent, and afterwards the sentence
    is a post-mortem."""
    _livetest(monkeypatch, tmp_path, FakeClient())
    out = capsys.readouterr().out
    assert SHIP in out
    assert "ROTATING" in out and out.index("ROTATING") < out.index("rotated")


def test_livetest_refuses_to_deploy_a_placeholder_token(monkeypatch, tmp_path):
    """The one way past the mint that lands nothing usable: --auth-token given
    the placeholder string. The rig would deploy it, wait out its whole timeout
    and report only that the agent never came online -- so it is a sentence here
    instead."""
    _, _, exit = _livetest(monkeypatch, tmp_path, FakeClient(), "--auth-token",
                           gen.DEFAULT_OPTIONS["auth_token"])
    assert "create-ship" in str(exit)


def test_livetest_refuses_a_placeholder_bundle_with_nothing_to_re_render(
        monkeypatch, tmp_path):
    """A plain run -- no --local-proxy, no --run-test -- renders nothing and
    deploys the bundle exactly as it sits on disk. The placeholder check lived
    inside the branch that mints, so this path had none: every object applies,
    the agent can never come online, and the run waits out its whole 12-20
    minutes to report only that it did not. Same guard as the Helm and
    ServiceAccount ones, for the same reason.

    Every other livetest test passes --run-test, which is why this went unseen.
    """
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    # No auth_token, so the bundle carries the placeholder -- what `generate`
    # writes when nobody supplied one.
    gen.write(gen.generate(facts, {"namespace": "ns1"}), str(manifests))
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "it deployed a bundle whose token can never authenticate"))
    monkeypatch.setattr(cli.api, "BzmClient", lambda *a, **k: FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key",
             "examples/api-key.example.json",
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1")
    assert "AUTH_TOKEN" in str(caught.value)


def test_livetest_with_a_token_in_hand_mints_nothing(monkeypatch, tmp_path):
    """--auth-token is the way out for a caller who already holds one -- the
    token `create-ship` printed, say. Minting over it would revoke the very
    credential they passed."""
    c = FakeClient()
    regenerate, manifests, exit = _livetest(monkeypatch, tmp_path, c,
                                            "--auth-token", "HELD-ALREADY")
    assert exit.code == 0
    regenerate({})
    regenerate({"ca_bundle": "PEM"})
    assert c.calls == []
    assert "HELD-ALREADY" in (manifests / "bzm_secret.yaml").read_text()
