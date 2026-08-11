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

from bzm_opl_gen import api, cli, core, generate as gen, livetest, plan
# The faked kubectl and pod shapes live with the cluster-reading tests; reused
# rather than re-declared so every layer exercises the same stand-in binary.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402
# The stand-in account, the one that will not issue a credential, and the one
# whose key BlazeMeter has stopped accepting, as core's suite declares them.
from test_core import (EXPIRED_401, ExpiredClient, FakeClient,  # noqa: E402
                       RefusingClient)
from versions_fixtures import VERSIONS_PERFORMANCE  # noqa: E402

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


# -- gathering facts ----------------------------------------------------------
#
# The command prints where the images came from, and one state deserves a line
# of its own: a refused image list leaves the catalogue's images behind, and the
# count says nothing about that. An empty answer is a different sentence -- the
# location runs nothing -- so it does not get this one.

def _gathered(monkeypatch, tmp_path, capsys, client, *extra):
    _account(monkeypatch, client)
    _run(monkeypatch, "facts", "--api-key", KEY, "--harbor-id", "H1",
         "--output", str(tmp_path / "facts.json"), *extra)
    return capsys.readouterr()


HARBOR = {"id": "H1", "name": "loc", "funcIds": ["performance"], "slots": 1,
          "threadsPerEngine": 500,
          "ships": [{"id": "S1", "name": "a", "state": "empty"}]}


def test_facts_says_when_the_image_list_could_not_be_read(monkeypatch, tmp_path,
                                                          capsys):
    """The refusal names itself. Without it the line above reads as a location
    that carries these images, when they are a catalogue's guess at any
    location's."""
    out = _gathered(monkeypatch, tmp_path, capsys, FakeClient(
        harbor=HARBOR,
        versions=api.BzmApiError("GET /private-locations/H1/ships/S1/versions "
                                 "-> HTTP 403: forbidden")))
    assert "could not be read" in out.err and "403" in out.err


def test_facts_says_nothing_extra_when_the_image_list_was_read(monkeypatch,
                                                               tmp_path, capsys):
    out = _gathered(monkeypatch, tmp_path, capsys,
                    FakeClient(harbor=HARBOR, versions=VERSIONS_PERFORMANCE))
    assert "could not be read" not in out.err
    assert "location image list" in out.out


def test_manual_facts_take_neither_id_and_name_what_is_missing(monkeypatch,
                                                              tmp_path, capsys):
    """The location that does not exist yet. `--manual --ship-id` used to be
    required, and nothing here can look an id up -- but a customer whose location
    has not been created has none to give, and the manifests are what gets it
    approved. So the facts carry the markers, and the command says so: this
    output is what the person running it has in front of them, where the bundle's
    README is three steps away.

    Nothing is patched, because nothing is reached -- which is the other half of
    the manual path.
    """
    out = tmp_path / "facts.json"
    _run(monkeypatch, "facts", "--manual", "--output", str(out))
    f = json.loads(out.read_text())
    assert f["harbor_id"] == gen.marker("harbor_id")
    assert f["ships"][0]["id"] == gen.marker("ship_id")
    err = capsys.readouterr().err
    assert "harbor_id (<HARBOR_ID>) and ship_id (<SHIP_ID>)" in err
    assert "not a legal label value" in err


def test_gathering_facts_still_needs_the_location_it_reads(monkeypatch):
    """--harbor-id stopped being argparse-required so that --manual could take it
    blank, which leaves the gather branch to say it for itself. Both halves of
    that message matter: which flag is missing, and that the other branch takes
    it blank."""
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "facts", "--api-key", KEY)
    assert "--harbor-id" in str(caught.value)


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


def test_generate_says_a_ca_slot_out_loud(monkeypatch, tmp_path, capsys):
    """#241: three moments could have said something about a CA slot and none
    did. This is the first of them, and it is the one where the person who chose
    the slot is still at the keyboard. Beside the token line, on stdout, because
    both say what the bundle about to be written cannot do yet."""
    _generate(monkeypatch, tmp_path, "--ca-placeholder")
    out = capsys.readouterr().out
    # The file mode names a file rather than shipping a slot, so what the line
    # has to carry is the name (here nobody supplied one) and who builds the
    # ConfigMap from it.
    assert gen.marker("ca_cert_file") in out and gen.CA_CONFIGMAP in out
    assert out.index("AUTH_TOKEN") < out.index("CA certificate") < out.index("wrote ")


def test_generate_says_nothing_about_a_slot_nobody_asked_for(monkeypatch,
                                                             tmp_path, capsys):
    _generate(monkeypatch, tmp_path)
    assert "CA certificate" not in capsys.readouterr().out


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


# -- the CA mode a rig run deploys (#251) -------------------------------------
# The rig re-renders the CA only where it has one to inject, and until this it
# re-rendered to `inline` whatever the bundle carried: a file-mode bundle run
# under --local-proxy was deployed as an inline one and passed, having proved a
# configuration nobody had generated. Without the proxy the same bundle names a
# ConfigMap nothing in the run creates, which is a pod that never starts and a
# whole timeout spent saying only that the agent never came online.

CA_FILE_MODE = {"ca_bundle_slot": True, "ca_cert_file": "corp-root.pem"}


def _ca_livetest(monkeypatch, tmp_path, client, options, *extra):
    """`livetest` over a bundle generated with `options`, the rig faked. Hands
    back what livetest.run was given, so a test can read the mode it resolved."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    gen.write(gen.generate(facts, {"namespace": "ns1", **options}),
              str(manifests))
    captured = {}
    monkeypatch.setattr(cli.livetest, "run",
                        lambda *a, **kw: captured.update(kw) or True)
    _account(monkeypatch, client)
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1",
             "--run-test", "12345", *extra)
    return captured, caught.value


def test_livetest_refuses_a_ca_configmap_nothing_in_the_run_creates(monkeypatch,
                                                                    tmp_path):
    """And refuses it before the mint, like every guard around it: a run that is
    about to be refused must not rotate the credential the deployed agent is
    holding."""
    c = FakeClient()
    captured, exit = _ca_livetest(monkeypatch, tmp_path, c, CA_FILE_MODE)
    assert captured == {}
    assert gen.CA_CONFIGMAP in str(exit.code) and "--ca-mode file" in str(exit.code)
    assert c.calls == []


def test_livetest_deploys_the_ca_mode_the_bundle_was_generated_for(monkeypatch,
                                                                   tmp_path):
    """The default is the bundle's own answer rather than `inline`. The run
    tests what is on disk unless somebody asks for something else."""
    captured, exit = _ca_livetest(monkeypatch, tmp_path, FakeClient(),
                                  CA_FILE_MODE, "--local-proxy",
                                  "--cluster", "kind")
    assert exit.code == 0
    assert captured["ca_mode"] == "file"


def test_livetest_says_when_the_flag_replaces_the_bundles_ca_mode(monkeypatch,
                                                                 tmp_path,
                                                                 capsys):
    """Still allowed -- --ca-mode is how somebody deliberately tests another
    configuration -- but never the quiet answer, and said before the cluster is
    built."""
    captured, exit = _ca_livetest(monkeypatch, tmp_path, FakeClient(),
                                  CA_FILE_MODE, "--local-proxy",
                                  "--cluster", "kind", "--ca-mode", "inline")
    assert exit.code == 0 and captured["ca_mode"] == "inline"
    out = capsys.readouterr().out
    assert "file" in out and "--ca-mode inline replaces it" in out


@pytest.mark.parametrize("options,says", [
    ({}, "configures no CA trust"),
    ({"ca_openshift_inject": True}, "OpenShift trust injection"),
])
def test_livetest_defaults_to_inline_where_the_bundle_has_no_rig_mode(
        monkeypatch, tmp_path, capsys, options, says):
    """Two bundles answer "no mode this rig can build" and they are not the same
    bundle: one configures no CA at all, one is OpenShift injection, which the
    cluster network operator performs and nothing here does. The rig has to
    configure a CA of its own under interception, so both get inline -- with the
    sentence that says which of the two happened."""
    captured, exit = _ca_livetest(monkeypatch, tmp_path, FakeClient(), options,
                                  "--local-proxy", "--cluster", "kind")
    assert exit.code == 0 and captured["ca_mode"] == "inline"
    assert says in capsys.readouterr().out


# -- livetest on a docker bundle ----------------------------------------------
# `--format docker` had never been live-tested at all: the rig applies YAML to a
# cluster, so a docker bundle was refused outright. #179 gave it the cheapest
# live proof this repo can have -- up, online, down, on a docker daemon. Which
# rig a run gets is read off the bundle rather than asked for, and these are
# what hold that: a wrong answer either way is the silent run (nothing created,
# the whole timeout waited out) that every guard on this command is about.

def _compose_bundle(tmp_path, **opts):
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    out = tmp_path / "out"
    out.mkdir()
    gen.write(gen.generate(facts, {"output_format": "docker",
                                   "auth_token": "REAL", **opts}), str(out))
    return facts, out


def _compose_livetest(monkeypatch, tmp_path, *extra, ok=True):
    """`livetest` over a docker bundle with the compose rig faked. The cluster
    rig fails the test if it is reached at all -- that is the whole question."""
    _facts, out = _compose_bundle(tmp_path)
    seen = {}
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "a docker bundle was handed to the cluster rig"))

    def fake(client, manifests, harbor_id, ship_id, **kw):
        seen.update(manifests=manifests, harbor_id=harbor_id,
                    ship_id=ship_id, **kw)
        return ok

    monkeypatch.setattr(cli.livetest, "run_compose", fake)
    client = _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(out), *extra)
    return seen, client, caught.value


def test_livetest_reads_the_platform_off_the_bundle(monkeypatch, tmp_path):
    """No --namespace and no --cluster, and no flag saying which rig either: the
    bundle already knows what it is, and a flag would be a second place to get
    it wrong."""
    seen, _client, exit = _compose_livetest(monkeypatch, tmp_path)
    assert exit.code == 0
    assert seen["ship_id"] == SHIP and seen["harbor_id"]


def test_livetest_compose_reports_a_failure_as_a_non_zero_exit(monkeypatch,
                                                               tmp_path):
    _seen, _client, exit = _compose_livetest(monkeypatch, tmp_path, ok=False)
    assert exit.code == 1


def test_livetest_compose_mints_no_credential(monkeypatch, tmp_path):
    """Nothing re-renders on this path, so the bundle deployed is the bundle on
    disk -- and minting would revoke the credential it is carrying while
    deploying it anyway."""
    _seen, client, _exit = _compose_livetest(monkeypatch, tmp_path)
    assert client.calls == []


def test_livetest_compose_says_a_namespace_reaches_nothing(monkeypatch,
                                                           tmp_path, capsys):
    """Named rather than refused -- it is somebody's habit from the other rig,
    not a claim about this run."""
    _compose_livetest(monkeypatch, tmp_path, "--namespace", "ns1")
    assert "--namespace ns1 reaches nothing" in capsys.readouterr().out


@pytest.mark.parametrize("flags, named", [
    (["--cluster", "minikube"], "--cluster minikube"),
    (["--local-registry", "5001"], "--local-registry"),
    (["--local-proxy"], "--local-proxy"),
    (["--run-test", "12345"], "--run-test"),
])
def test_livetest_compose_refuses_the_cluster_shaped_flags(monkeypatch,
                                                           tmp_path, flags,
                                                           named):
    """Refused, not ignored: these are the flags whose absence makes a pass mean
    less than the person reading it thinks, so a run that quietly dropped one
    would claim something it never tested."""
    _facts, out = _compose_bundle(tmp_path)
    monkeypatch.setattr(cli.livetest, "run_compose", lambda *a, **kw: pytest.fail(
        "it ran with a flag that reaches nothing on this platform"))
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(out), *flags)
    assert named in str(caught.value)


def test_livetest_compose_refuses_a_bundle_for_another_agent(monkeypatch,
                                                             tmp_path):
    """#107 on the other platform, and the container name is what carries the
    identity there. Before anything is started, because what would otherwise
    happen is a container registering against a real account under somebody
    else's ship id."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    out = tmp_path / "out"
    out.mkdir()
    gen.write(gen.generate(facts, {"output_format": "docker",
                                   "ship_id": "6a6f7270aaaabbbbccccdddd",
                                   "auth_token": "REAL"}), str(out))
    monkeypatch.setattr(cli.livetest, "run_compose", lambda *a, **kw: pytest.fail(
        "it started a bundle built for a different agent"))
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(out), "--ship-id", SHIP)
    assert gen.docker_container_name(SHIP) in str(caught.value)


def test_livetest_still_requires_a_namespace_for_a_manifests_bundle(monkeypatch,
                                                                    tmp_path):
    """--namespace stopped being argparse-required so a compose run is not asked
    for a value it has no use for. The cluster rig still creates one and deploys
    into it, so it is required here instead -- never defaulted, or the rig
    creates a namespace nobody chose."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    out = tmp_path / "out"
    out.mkdir()
    gen.write(gen.generate(facts, {"namespace": "ns1", "auth_token": "REAL"}),
              str(out))
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "it deployed with no namespace to deploy into"))
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"), "--manifests", str(out))
    assert "--namespace" in str(caught.value)


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


def test_plan_sizes_browsers_without_a_load_target(monkeypatch, capsys):
    """--users is the performance model's target, not the only one there is.
    A GUI Functional customer has no load target to give."""
    _run(monkeypatch, "plan", "--browsers", "20")
    out = capsys.readouterr().out
    assert "20 browser instances at 4 per engine" in out
    assert "5 engines" in out


def test_plan_names_the_sizing_the_pool_came_from(monkeypatch, capsys):
    _run(monkeypatch, "plan", "--users", "5000", "--browsers", "20")
    out = capsys.readouterr().out
    assert "10 engines" in out
    assert "from the performance sizing" in out


def test_plan_says_service_virtualization_is_not_sized(monkeypatch, capsys):
    _run(monkeypatch, "plan", "--users", "5000", "--requests-per-second", "2000")
    out = capsys.readouterr().out
    assert "2,000 requests per second" in out
    assert "has not been measured" in out


def test_plan_refuses_a_sizing_with_nothing_to_size_it_by(monkeypatch):
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan", "--requests-per-second", "2000")
    assert "has not been measured" in str(caught.value)


def test_plan_still_needs_a_sizing_of_some_kind(monkeypatch):
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan")
    assert "users" in str(caught.value)


def test_plan_refuses_a_target_that_is_not_a_plan(monkeypatch):
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan", "--users", "0")
    assert "at least 1" in str(caught.value)


def test_plan_offers_a_flag_for_every_sizing_model(monkeypatch, capsys):
    """The flags are `plan.SIZING_MODELS`, walked -- not three strings written
    out beside it.

    Nothing held the command to that table, and the MCP server and the route
    were already generated from it: a fourth model would have reached both of
    them and not this. `--users` is the exception and stays one, because it is
    capacity_plan's own argument rather than a row's target field."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "plan", "--help")
    out = capsys.readouterr().out
    for m in plan.SIZING_MODELS.values():
        for field in (m["target_field"], m["figure_field"]):
            # A model with no measured figure has no figure flag, for the same
            # reason the card offers it no box: there is nothing to default.
            if field:
                assert "--" + field.replace("_", "-") in out


def test_plan_sizes_every_model_it_offers(monkeypatch, capsys):
    """...and each flag reaches the planner as its own model, in its own unit.
    The help text above says the flag exists; this says it is wired to the row
    the table names."""
    flags = []
    for fid, m in plan.SIZING_MODELS.items():
        if fid != plan.PERFORMANCE:
            flags += ["--" + m["target_field"].replace("_", "-"), "20"]
    _run(monkeypatch, "plan", "--users", "5000", *flags)
    out = capsys.readouterr().out
    for m in plan.SIZING_MODELS.values():
        assert m["unit"] in out


def test_plan_refuses_a_target_of_zero_by_name(monkeypatch):
    """Blank, absent and zero are three things. `if a.browsers:` read a typed 0
    as a flag nobody passed, so a browser suite sized at zero silently planned
    for the load test beside it."""
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan", "--users", "5000", "--browsers", "0")
    assert "browsers must be at least 1" in str(caught.value)


def test_plan_refuses_a_per_pod_figure_with_no_target(monkeypatch):
    """`--browsers-per-engine 6` alone used to be dropped on the floor: the row
    was built from the target, so a figure with nothing to apply it to sized
    nothing and said nothing."""
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan", "--users", "5000",
             "--browsers-per-engine", "6")
    assert "browsers" in str(caught.value)


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


# -- the location BlazeMeter would not have made -------------------------------
#
# #159. `--slots` defaults to 1, and a GUI Functional location at 1 is a 400.

def test_create_location_refuses_gui_functional_at_the_default_slots(
        monkeypatch, capsys):
    """core's refusal, reaching the terminal through `main`'s one exit --
    ahead of the POST, so the account is untouched and nothing is printed as
    though it had been created."""
    client = FakeClient()
    _account(monkeypatch, client)
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "create-location", "--api-key", KEY, "--name",
             "loc1", "--account-id", "7", "--workspace-id", "2",
             "--func-ids", "performance", "functionalGui")
    assert "Parallel engine runs must be greater than 1" in str(caught.value)
    assert "slots=2" in str(caught.value)
    assert "created location" not in capsys.readouterr().out
    assert not [c for c in client.calls if c[0] == "create_private_location"]


def test_create_location_makes_a_gui_functional_location_at_two_slots(
        monkeypatch, capsys):
    from test_core import _RunnableClient
    _account(monkeypatch, _RunnableClient())
    _run(monkeypatch, "create-location", "--api-key", KEY, "--name", "loc1",
         "--account-id", "7", "--workspace-id", "2", "--func-ids",
         "functionalGui", "--slots", "2")
    assert "created location" in capsys.readouterr().out


def test_the_slots_flag_says_which_functionality_needs_more_than_one(
        monkeypatch, capsys):
    """On the flag, not only in the refusal: `--help` is where somebody reads
    what to type before typing it. Built from core.SLOT_MINIMUMS rather than
    written out, so a second entry reaches the terminal without an edit."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "create-location", "--help")
    out = capsys.readouterr().out
    for rule in core.SLOT_MINIMUMS.values():
        assert rule["label"] in out and str(rule["minimum"]) in out


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


def test_ca_mode_needs_the_proxy_that_owns_the_ca(monkeypatch, tmp_path):
    """The CA under test is the MITM proxy's, so a run with no proxy configures
    no CA trust at all -- and would report a pass having deployed neither mode.
    Named rather than ignored, like every other flag combination this rig
    refuses."""
    _, _, exit = _livetest(monkeypatch, tmp_path, FakeClient(),
                           "--ca-mode", "existing")
    assert "--ca-mode needs --local-proxy" in str(exit)


def test_the_default_ca_mode_asks_for_no_proxy(monkeypatch, tmp_path):
    """inline is the default and the rig's long-standing behaviour, so it must
    not start refusing runs that never mentioned a CA."""
    regenerate, _, exit = _livetest(monkeypatch, tmp_path, FakeClient())
    assert exit.code == 0 and regenerate is not None
