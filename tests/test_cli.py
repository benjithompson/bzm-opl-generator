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
# The stand-in account, the one that will not issue a credential, and the one
# whose key BlazeMeter has stopped accepting, as core's suite declares them.
from test_core import (EXPIRED_401, ExpiredClient, FakeClient,  # noqa: E402
                       RefusingClient)

# Absolute, because several tests below run the command from a directory of
# their own -- a command that writes into the working directory has to be given
# one that is not this checkout.
KEY = os.path.abspath("examples/api-key.example.json")
EVIDENCE = os.path.abspath("tests/cluster-evidence.cluster-scoped-denied.json")
# Collected for a namespace that is neither the documented default nor anything
# a test configures, so which step of the precedence answered is visible.
ELSEWHERE_EVIDENCE = os.path.abspath("tests/cluster-evidence.degraded.json")

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


def _account(monkeypatch, client):
    """Install `client` as the account this command talks to.

    At `core.client_from_key`, which is where all three suites stand in since
    #95 -- this one patched `api.BzmClient` and asserted the keyword it was
    reached with, which was how it checked that a command had gone through core
    rather than reading the key file itself. That check is structural now (the
    constructor takes a keyword-only pair, and tests/test_core.py counts the
    constructions), so what is left here is the stand-in and nothing else.
    """
    monkeypatch.setattr(cli.core, "client_from_key", lambda *a, **kw: client)
    return client


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
    _account(monkeypatch, client or FakeClient())
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
    assert "create-agent" in out
    assert "kubectl -n ns1 get secret" in out and "base64 -d" in out


def test_generate_says_which_ship_it_needs_rather_than_raising(monkeypatch,
                                                               tmp_path):
    """Facts carrying no ships used to reach `len(f["ships"])` and come back a
    bare KeyError. The refusal generate() already writes names the count and
    the flag that fixes it, and is what a hand-edited facts file deserves.

    It arrives as an exit rather than as a ValueError now: the command goes
    through core.generate_bundle, where every sentence generate() writes
    becomes a BadRequest and main() prints it.
    """
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", "generate", "--facts", _facts_file(tmp_path, None),
        "-o", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert "ship_id required" in str(caught.value)


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
    _account(monkeypatch, client)
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


@pytest.mark.parametrize("spelling", ["create-agent", "create-ship"])
def test_create_agent_is_the_command_and_create_ship_still_reaches_it(
        monkeypatch, spelling):
    """One deployment inside a private location is an *agent*; `ship` is the
    account's field name and nothing else. `create-location` and `create-ship`
    sat side by side spelling the same vocabulary two ways.

    The old name stays as an argparse alias rather than going away: it is in
    the README, in `docs/live-test.md`, and in whatever scripts a customer
    copied out of them, and a rename that breaks those buys nothing. Stopped at
    the dispatch, because what is under test is which command the word reaches
    -- both spellings are the same command or one of them is a second one to
    keep working.
    """
    reached = []
    monkeypatch.setattr(cli, "cmd_create_agent", reached.append)
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", spelling, "--api-key", KEY,
        "--harbor-id", "h1", "--name", "agent1"])
    cli.main()
    assert [(a.harbor_id, a.name) for a in reached] == [("h1", "agent1")]


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
    _account(monkeypatch, client)
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
    assert "create-agent" in str(exit)


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
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key",
             "examples/api-key.example.json",
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1")
    assert "AUTH_TOKEN" in str(caught.value)


def test_livetest_refuses_a_bundle_built_for_another_agent(monkeypatch, tmp_path):
    """#107, as it happened: --manifests defaults to out/, out/ is whatever the
    last `generate` left there, and a run with --ship-id and --auth-token
    deployed a nine-day-old bundle for a different agent. Crane could not
    register, the rollout timed out saying nothing else, and the rig deleted the
    cluster. The refusal names the ship on disk and the ship asked for, and
    arrives before anything is built."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    gen.write(gen.generate(facts, {"namespace": "ns1", "auth_token": "REAL"}),
              str(manifests))
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "it deployed a bundle built for a different agent"))
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1",
             "--ship-id", "6a6f7270aaaabbbbccccdddd",
             "--auth-token", "REAL")
    msg = str(caught.value)
    assert SHIP in msg and "6a6f7270aaaabbbbccccdddd" in msg


def test_livetest_refuses_a_leftover_from_an_older_generator(monkeypatch,
                                                             tmp_path):
    """The other half of the same stale directory: bzm_limitrange.yaml, emitted
    by a version that no longer exists and applied by the rig regardless."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    gen.write(gen.generate(facts, {"namespace": "ns1", "auth_token": "REAL"}),
              str(manifests))
    (manifests / "bzm_limitrange.yaml").write_text("kind: LimitRange\n")
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "it applied a file this generator does not emit"))
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1",
             "--auth-token", "REAL")
    assert "bzm_limitrange.yaml" in str(caught.value)


def test_livetest_with_a_token_in_hand_mints_nothing(monkeypatch, tmp_path):
    """--auth-token is the way out for a caller who already holds one -- the
    token `create-agent` printed, say. Minting over it would revoke the very
    credential they passed."""
    c = FakeClient()
    regenerate, manifests, exit = _livetest(monkeypatch, tmp_path, c,
                                            "--auth-token", "HELD-ALREADY")
    assert exit.code == 0
    regenerate({})
    regenerate({"ca_bundle": "PEM"})
    assert c.calls == []
    assert "HELD-ALREADY" in (manifests / "bzm_secret.yaml").read_text()


# -- plan ---------------------------------------------------------------------
# The one command that takes neither an API key nor a facts file. What these
# defend is that it stays that way: a flag that made either mandatory would put
# the first question a customer asks behind the account they have not got.

def test_plan_needs_no_account_and_no_facts(monkeypatch, capsys):
    monkeypatch.setattr(cli.core, "client_from_key", lambda *a, **k: pytest.fail(
        "plan built an account client"))
    _run(monkeypatch, "plan", "--users", "5000")
    out = capsys.readouterr().out
    assert "10 engines" in out
    assert "10 node(s) per agent of 3 vCPU / 10Gi" in out
    assert "slots=10" in out


def test_plan_says_when_the_vus_per_engine_figure_is_assumed(monkeypatch, capsys):
    _run(monkeypatch, "plan", "--users", "5000")
    assert "assumed" in capsys.readouterr().out
    _run(monkeypatch, "plan", "--users", "5000", "--vus-per-engine", "500")
    assert "assumed" not in capsys.readouterr().out


def test_plan_assumes_from_the_engine_size(monkeypatch, capsys):
    """A Large engine carries twice what the standard one does, so a blank
    figure has to follow the size rather than sit at BlazeMeter's 500."""
    _run(monkeypatch, "plan", "--users", "10000",
         "--engine-cpu-limit", "4", "--engine-mem-limit", "16Gi")
    out = capsys.readouterr().out
    assert "10,000 virtual users at 1,000 per engine" in out
    assert "10 engines" in out


def test_plan_writes_the_request_document(monkeypatch, capsys, tmp_path):
    out_dir = tmp_path / "plan"
    _run(monkeypatch, "plan", "--users", "2500", "-o", str(out_dir))
    doc = (out_dir / "capacity-request.md").read_text()
    assert doc.startswith("# Infrastructure request: load testing\n")
    assert "5** × 3 vCPU" in doc
    assert str(out_dir) in capsys.readouterr().out


def test_plan_output_directory_may_be_relative_to_the_shell(monkeypatch, tmp_path):
    """core refuses a relative out_dir because a server's working directory is
    whatever launched it. A shell is the one caller that chose its own, so the
    command resolves the path rather than passing the refusal on."""
    monkeypatch.chdir(tmp_path)
    _run(monkeypatch, "plan", "--users", "100", "-o", "here")
    assert (tmp_path / "here" / "capacity-request.md").exists()


def test_plan_json_is_the_whole_plan(monkeypatch, capsys):
    _run(monkeypatch, "plan", "--users", "5000", "--json")
    p = json.loads(capsys.readouterr().out)
    assert p["engines"] == 10 and p["nodes"] == 10
    assert p["document"].startswith("# Infrastructure request")


def test_plan_markdown_prints_the_document_alone(monkeypatch, capsys):
    _run(monkeypatch, "plan", "--users", "5000", "--markdown")
    out = capsys.readouterr().out
    assert out.startswith("# Infrastructure request")
    assert "slots=10" not in out          # the summary, not this


def test_plan_refuses_a_target_that_is_not_a_plan(monkeypatch):
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan", "--users", "0")
    assert "at least 1" in str(caught.value)


def test_plan_engine_size_flags_match_generate_s(monkeypatch, capsys):
    """Same two flag names as `generate`, so a plan and the bundle it leads to
    are described in one vocabulary."""
    _run(monkeypatch, "plan", "--users", "1000", "--vus-per-engine", "250",
         "--engine-cpu-limit", "4", "--engine-mem-limit", "16Gi")
    out = capsys.readouterr().out
    assert "4 engines of 4 CPU / 16Gi" in out
    assert "overrideMemory=16384" in out


def test_plan_divides_the_run_across_agents(monkeypatch, capsys):
    """`slots` is engines per *agent*, so the same run on three agents is a
    third of the location's setting -- and a third of each cluster."""
    _run(monkeypatch, "plan", "--users", "10000", "--vus-per-engine", "500",
         "--agents", "3")
    out = capsys.readouterr().out
    assert "20 engines" in out
    assert "7 engines per agent across 3 agent(s)" in out
    assert "slots=7 (engines per agent)" in out


# -- the terminal answers what the other surfaces answer ------------------------
#
# #93. Every command that reaches the account or the cluster goes through
# `core`, so a refusal reads the same in a terminal as it does in the browser.
# Before this, nine commands built their own client and called the underlying
# modules directly, and `main` catches only CoreError -- so an expired key was a
# traceback where the web UI answers with a written sentence.

def _account_command(tmp_path, name):
    """One invocation per command that reaches BlazeMeter, with every file it
    needs somewhere disposable.

    `livetest` gets a real bundle and `--run-test`, because every guard it
    makes before the account is reached is a guard about the bundle on disk --
    without one it refuses the run and never gets as far as a credential.
    """
    if name == "livetest":
        facts = json.load(open("examples/facts.example.json"))
        (tmp_path / "lt.json").write_text(json.dumps(facts))
        gen.write(gen.generate(facts, {"namespace": "ns1"}),
                  str(tmp_path / "lt"))
    return {
        "locations": ("locations", "--api-key", KEY, "--account-id", "7"),
        "create-location": ("create-location", "--api-key", KEY, "--name",
                            "loc1", "--account-id", "7", "--workspace-id", "2"),
        "delete-location": ("delete-location", "--api-key", KEY,
                            "--harbor-id", "h1"),
        "create-ship": ("create-ship", "--api-key", KEY, "--harbor-id", "h1",
                        "--name", "agent1"),
        "facts": ("facts", "--api-key", KEY, "--harbor-id", "h1", "-o",
                  str(tmp_path / "facts.json")),
        "images": ("images", "--api-key", KEY, "--harbor-id", "h1"),
        "doctor": ("doctor", "--api-key", KEY, "--harbor-id", "h1",
                   "--manifests", str(tmp_path / "nothing")),
        "generate": ("generate", "--api-key", KEY, "--rotate-token", "--facts",
                     _facts_file(tmp_path, ["b1"]), "-o", str(tmp_path / "out")),
        "livetest": ("livetest", "--api-key", KEY, "--facts",
                     str(tmp_path / "lt.json"), "--manifests",
                     str(tmp_path / "lt"), "--namespace", "ns1",
                     "--run-test", "12345"),
    }[name]


ACCOUNT_COMMANDS = ("locations", "create-location", "delete-location",
                    "create-ship", "facts", "images", "doctor", "generate",
                    "livetest")


@pytest.mark.parametrize("name", ACCOUNT_COMMANDS)
def test_every_account_command_asks_core_for_its_client(monkeypatch, tmp_path,
                                                        name):
    """The construction is `core.client_from_key`, whatever the command.

    tests/test_core.py asserts nothing in the package builds a client of its
    own; this is the other half -- that each of these commands does reach the
    one that is left, with the `--api-key` it was given. A command that built
    no client at all, or reached BlazeMeter some other way, passes a source
    guard and fails here. Stopped at the construction rather than allowed to
    run on: what is under test is that it happened, and each of these would
    otherwise go on to do the command's real work.
    """
    class Constructed(Exception):
        pass

    seen = []

    def seam(*a, **kw):
        seen.append((a, kw))
        raise Constructed

    monkeypatch.setattr(cli.core, "client_from_key", seam)
    with pytest.raises(Constructed):
        _run(monkeypatch, *_account_command(tmp_path, name))
    assert seen == [((KEY,), {})], \
        f"{name} did not reach core.client_from_key with its --api-key"


@pytest.mark.parametrize("name", ACCOUNT_COMMANDS)
def test_a_key_the_account_has_stopped_accepting_is_a_sentence(monkeypatch,
                                                               tmp_path, name):
    """An expired or revoked key is only discovered on the first call, so it is
    the failure every one of these can hit. `core._upstream` turns it into an
    UpstreamError and `main` turns that into an exit -- before, it came out of
    the command as a BzmApiError nobody caught."""
    _account(monkeypatch, ExpiredClient())
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "the rig deployed against an account that answered 401"))
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, *_account_command(tmp_path, name))
    assert EXPIRED_401 in str(caught.value), \
        f"{name} did not report what BlazeMeter said"


def test_an_unreadable_key_file_is_answered_by_core(monkeypatch, tmp_path):
    """`--api-key <a directory>` is one keystroke from the path that works.

    The message is the same sentence the web UI and an MCP session get, from
    `core.client_from_key` -- and it still says how to make a key, which was
    the only thing `api.read_key_file_or_exit` added for the terminal before
    #95 deleted it. `main` turns the CoreError into an exit; the exit does not
    come from inside a constructor any more.
    """
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "locations", "--api-key", str(tmp_path),
             "--account-id", "7")
    assert str(tmp_path) in str(caught.value)
    assert "Settings -> API Keys" in str(caught.value)


def test_a_malformed_engine_size_is_a_sentence_not_a_stack(monkeypatch,
                                                           tmp_path, capsys):
    """`generate` reached `generate()` directly, so every refusal it writes --
    each one a sentence for whoever set the option -- arrived as a ValueError
    traceback. core.generate_bundle is where they become BadRequest."""
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--engine-cpu-limit", "banana")
    assert "not a Kubernetes CPU quantity" in str(caught.value)
    assert "Traceback" not in capsys.readouterr().err


# -- the location a test cannot start on ---------------------------------------

def test_create_location_warns_that_a_test_cannot_start_on_it(monkeypatch,
                                                              capsys):
    """The warning is core's now (the browser and an MCP session need it too),
    and it still arrives here, on stderr, ahead of the next step."""
    _account(monkeypatch, FakeClient())
    _run(monkeypatch, "create-location", "--api-key", KEY, "--name", "loc1",
         "--account-id", "7", "--workspace-id", "2")
    out = capsys.readouterr()
    assert "403" in out.err and "Not enough available resources" in out.err
    assert "created location" in out.out


def test_create_location_says_nothing_extra_when_the_location_is_runnable(
        monkeypatch, capsys):
    from test_core import _RunnableClient
    _account(monkeypatch, _RunnableClient())
    _run(monkeypatch, "create-location", "--api-key", KEY, "--name", "loc1",
         "--account-id", "7", "--workspace-id", "2", "--slots", "2")
    assert capsys.readouterr().err == ""


# -- which namespace a preflight is about --------------------------------------
#
# One rule, in `core.preflight_cluster`, rather than the copy this command used
# to hold: an explicit -n, then the bundle's own namespace, then the one the
# evidence was collected for. The file's is last because evidence collected
# elsewhere is reported by cluster_from_evidence rather than silently adopted.

def _doctor_namespace(monkeypatch, capsys, tmp_path, *extra):
    """The namespace `doctor` says it preflighted, off its own report."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "doctor", "--facts", _facts_file(tmp_path, ["b1"]),
             "--cluster-evidence", ELSEWHERE_EVIDENCE, *extra)
    line = next(l for l in capsys.readouterr().out.splitlines()
                if l.startswith("doctor: "))
    return line.rsplit("namespace ", 1)[1]


def test_doctor_namespace_precedence(monkeypatch, capsys, tmp_path):
    """The file was collected for `some-ns`, which is neither the documented
    default nor anything else here -- so each step is visible in the answer."""
    profile = tmp_path / "bundle"
    gen.write(gen.generate(json.load(open("examples/facts.example.json")),
                           {"namespace": "from-profile"}), str(profile))
    absent = str(tmp_path / "no-bundle")

    assert _doctor_namespace(monkeypatch, capsys, tmp_path,
                             "--manifests", absent) == "some-ns"
    assert _doctor_namespace(monkeypatch, capsys, tmp_path,
                             "--manifests", str(profile)) == "from-profile"
    assert _doctor_namespace(monkeypatch, capsys, tmp_path, "--manifests",
                             str(profile), "-n", "asked-for") == "asked-for"


def test_doctor_and_the_other_surfaces_answer_from_one_rule(monkeypatch, capsys,
                                                            tmp_path):
    """The command and `core.preflight` agree about which namespace is being
    preflighted, because they ask the same function -- this command used to
    hold its own copy of the precedence, comment included."""
    from bzm_opl_gen import doctor as doctor_mod
    doc = doctor_mod.load_evidence(ELSEWHERE_EVIDENCE)
    facts = json.load(open("examples/facts.example.json"))
    configured = tmp_path / "bundle"
    gen.write(gen.generate(facts, {"namespace": "configured"}), str(configured))
    # A bundle configured for a namespace, and no bundle at all: the two
    # inputs the browser's panel has, spelled as this command spells them.
    for options, manifests in (({"namespace": "configured"}, str(configured)),
                               ({}, str(tmp_path / "no-bundle"))):
        printed = _doctor_namespace(monkeypatch, capsys, tmp_path,
                                    "--manifests", manifests)
        assert printed == core.preflight(facts, options, doc)["namespace"]
