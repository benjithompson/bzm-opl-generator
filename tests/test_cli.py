"""The command line, driven the way a user drives it: through `cli.main()` with
a faked argv, so a renamed or dropped flag fails here."""

import json
import os

import pytest
import yaml

from bzm_opl_gen import api, cli, core, generate as gen, kube, plan
# The faked kubectl and pod shapes shared with the cluster-reading tests.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402
# The stand-in accounts declared by core's suite.
from test_core import (EXPIRED_401, ExpiredClient, FakeClient,  # noqa: E402
                       RefusingClient)
from versions_fixtures import VERSIONS_PERFORMANCE  # noqa: E402
from bzm_opl_gen import (bundle_names, bundle_options, markers, service_virt)  # noqa: E402

# Absolute: several tests run the command from a directory of their own.
KEY = os.path.abspath("examples/api-key.example.json")
EVIDENCE = os.path.abspath("tests/cluster-evidence.cluster-scoped-denied.json")
# A namespace neither the default nor configured, so the precedence step that
# answered is visible.
ELSEWHERE_EVIDENCE = os.path.abspath("tests/cluster-evidence.degraded.json")

SV_PODS = json.dumps({"items": [
    _sv_pod("vs1svc2", 8080, "aaa111", "bbb222"),
    # crane's own pod carries no identity labels and gets no Service.
    _sv_pod(None, 5000, extra={"role": "role-crane"})]})


@pytest.fixture
def fake_cluster(monkeypatch):
    """Install a faked kubectl/oc for one test, clearing cli_tool()'s memo after."""
    def install(**kw):
        _fake_kubectl(monkeypatch, **kw)
    install()
    yield install
    kube.cli_tool.cache_clear()


def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    cli.main()


def _account(monkeypatch, client):
    """Install `client` as the account this command talks to, at
    `core.client_from_key` (where every suite stands in)."""
    monkeypatch.setattr(cli.core, "client_from_key", lambda *a, **kw: client)
    return client


def _docs(path):
    return [d for d in yaml.safe_load_all(open(path).read()) if d]


def test_sv_expose_writes_a_pair_per_deployed_mock(fake_cluster, monkeypatch,
                                                   tmp_path, capsys):
    """sv-expose reads the namespace's mocks, renders, writes and says how to apply."""
    fake_cluster(stdout=SV_PODS)
    out = tmp_path / bundle_names.SV_EXPOSE_FILE
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
    """`--ingress-class` reaches the Ingress; unset it is nginx."""
    fake_cluster(stdout=SV_PODS)

    def ing(*extra):
        out = tmp_path / f"{len(extra)}-{bundle_names.SV_EXPOSE_FILE}"
        _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
             "--sv-subdomain", "apps.example.com", "-o", str(out), *extra)
        return next(d for d in _docs(out) if d["kind"] == "Ingress")

    assert (ing("--ingress-class", "openshift-default")
            ["spec"]["ingressClassName"] == "openshift-default")
    assert (ing()["spec"]["ingressClassName"]
            == service_virt.SV_EXPOSE_DEFAULT_INGRESS_CLASS)


def test_sv_expose_reads_the_bundles_profile_rather_than_repeating_flags(
        fake_cluster, monkeypatch, tmp_path):
    """sv-expose reads namespace, domain, TLS secret and ingress class from the
    bundle's profile.json."""
    from test_generate import FACTS
    gen.write(gen.generate(FACTS, {
        "namespace": "ns1", "sv_ingress": "nginx",
        "sv_subdomain": "apps.example.com", "sv_tls_secret": "wildcard-tls",
        "sv_ingress_class": "openshift-default"}), str(tmp_path))
    fake_cluster(stdout=SV_PODS)
    out = tmp_path / bundle_names.SV_EXPOSE_FILE
    _run(monkeypatch, "sv-expose", "--manifests", str(tmp_path), "-o", str(out))
    ing = next(d for d in _docs(out) if d["kind"] == "Ingress")
    assert ing["metadata"]["namespace"] == "ns1"
    assert ing["spec"]["rules"][0]["host"] == "vs1svc2-8080-ns1.apps.example.com"
    assert ing["spec"]["tls"][0]["secretName"] == "wildcard-tls"
    assert ing["spec"]["ingressClassName"] == "openshift-default"


def test_sv_expose_runs_from_anywhere_with_no_profile_at_all(fake_cluster,
                                                             monkeypatch,
                                                             tmp_path):
    """`--manifests ''` skips the profile, so every option comes from flags."""
    fake_cluster(stdout=SV_PODS)
    monkeypatch.chdir(tmp_path)
    assert not os.path.exists("out")
    _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
         "--sv-subdomain", "apps.example.com", "--ingress-class", "nginx")
    ing = next(d for d in _docs(bundle_names.SV_EXPOSE_FILE) if d["kind"] == "Ingress")
    assert ing["spec"]["rules"][0]["host"] == "vs1svc2-8080-ns1.apps.example.com"


def test_sv_expose_says_to_deploy_first_when_the_namespace_is_empty(
        fake_cluster, monkeypatch, tmp_path):
    """An empty namespace is a refusal saying to deploy the virtual service first."""
    fake_cluster(stdout=json.dumps({"items": []}))
    out = tmp_path / bundle_names.SV_EXPOSE_FILE
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
             "--sv-subdomain", "apps.example.com", "-o", str(out))
    assert "no virtual-service pods in namespace ns1" in str(e.value)
    assert not out.exists()


def test_sv_expose_refuses_without_a_wildcard_domain(fake_cluster, monkeypatch,
                                                     tmp_path):
    """No wildcard domain is a refusal before anything is written."""
    fake_cluster(stdout=SV_PODS)
    out = tmp_path / bundle_names.SV_EXPOSE_FILE
    with pytest.raises(ValueError, match="sv_subdomain"):
        _run(monkeypatch, "sv-expose", "--manifests", "", "-n", "ns1",
             "-o", str(out))
    assert not out.exists()


# -- generate: which token the bundle gets, and what minted it -----------------
# Driven through the flags: nothing but --rotate-token reaches the endpoint.

def test_images_pull_plans_the_mirror_without_crashing(monkeypatch, tmp_path,
                                                       capsys):
    """`images --pull --dry-run` prints the pull, tag and push plan."""
    monkeypatch.setattr(core, "_docker", lambda args, dry_run: " ".join(
        ["docker"] + args))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path, ["b1"]),
         "--pull", "--dry-run", "--mirror", "reg.local/bzm")
    out = capsys.readouterr().out
    assert "DRY-RUN: docker pull" in out and "DRY-RUN: docker push" in out


def test_listing_images_touches_no_docker_at_all(monkeypatch, tmp_path, capsys):
    """Listing images runs no docker at all."""
    monkeypatch.setattr(core, "_docker", lambda *a, **k: pytest.fail(
        "listing images ran docker"))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail(
        "listing images shelled out"))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path, ["b1"]))
    assert "gcr.io/" in capsys.readouterr().out, "it listed nothing"


def test_the_cli_never_runs_docker_itself(monkeypatch, tmp_path):
    """The CLI never shells out itself; core.mirror_images runs docker."""
    ran = []
    monkeypatch.setattr(core, "_docker",
                        lambda args, dry_run: ran.append(args) or "docker")
    # Patched on the module, so any path that starts shelling out is caught.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail(
        "the CLI ran docker itself; core.mirror_images is the only path"))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path, ["b1"]),
         "--pull", "--mirror", "reg.local/bzm")
    verbs = [a[0] for a in ran]
    assert verbs, "nothing was mirrored at all"
    assert verbs.count("pull") == verbs.count("tag") == verbs.count("push"), \
        f"one pull, tag and push per image; got {verbs}"


# -- gathering facts ----------------------------------------------------------
# A refused image list gets its own note; an empty one does not.

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
    """A refused image list is named, so the catalogue's images are not mistaken
    for the location's."""
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
    """`facts --manual` takes neither id, writes the markers and names them.
    Nothing is patched because nothing is reached."""
    out = tmp_path / "facts.json"
    _run(monkeypatch, "facts", "--manual", "--output", str(out))
    f = json.loads(out.read_text())
    assert f["harbor_id"] == markers.marker("harbor_id")
    assert f["ships"][0]["id"] == markers.marker("ship_id")
    err = capsys.readouterr().err
    assert "harbor_id (<HARBOR_ID>) and ship_id (<SHIP_ID>)" in err
    assert "not a legal label value" in err


def test_gathering_facts_still_needs_the_location_it_reads(monkeypatch):
    """Gathering without --harbor-id says which flag is missing and that --manual
    takes it blank."""
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
    """`--api-key` without `--rotate-token` mints nothing and says it has no effect."""
    c = FakeClient()
    _generate(monkeypatch, tmp_path, "--api-key",
              "examples/api-key.example.json", client=c)
    assert c.calls == []
    out = capsys.readouterr()
    assert "--api-key has no effect" in out.err
    assert "--rotate-token" in out.err
    assert (bundle_options.DEFAULT_OPTIONS["auth_token"]
            in (tmp_path / "out" / "bzm_secret.yaml").read_text())


def test_generate_says_a_ca_slot_out_loud(monkeypatch, tmp_path, capsys):
    """generate names a CA slot beside the token line, before `wrote`."""
    _generate(monkeypatch, tmp_path, "--ca-placeholder")
    out = capsys.readouterr().out
    # File mode: the line carries the file name (a marker here) and who builds
    # the ConfigMap from it.
    assert markers.marker("ca_cert_file") in out and bundle_names.CA_CONFIGMAP in out
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
    """The rotation warning is printed before the mint, on the same stream."""
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
    """Generating twice into the same directory is byte-identical."""
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
    """A placeholder bundle names where a real token comes from."""
    _generate(monkeypatch, tmp_path, "--namespace", "ns1")
    out = capsys.readouterr().out
    assert "create-agent" in out
    assert "kubectl -n ns1 get secret" in out and "base64 -d" in out


def test_generate_says_which_ship_it_needs_rather_than_raising(monkeypatch,
                                                               tmp_path):
    """Facts with no ships exit with generate()'s sentence, not a KeyError."""
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
    assert bundle_options.DEFAULT_OPTIONS["auth_token"] in (out / "bzm_secret.yaml").read_text()


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
    """create-agent says its token is the thing to keep, and the next step takes it."""
    _create_ship(monkeypatch, FakeClient())
    out = capsys.readouterr().out
    assert "Keep" in out and "durable" in out
    assert "generate" in out and "--auth-token" in out
    assert "generate --api-key" not in out


def test_create_ship_reports_a_refused_credential_and_the_ship_it_made(
        monkeypatch, capsys):
    """A refused credential exits with the reason after printing the agent it made."""
    with pytest.raises(SystemExit) as caught:
        _create_ship(monkeypatch, RefusingClient())
    assert "could not be issued" in str(caught.value)
    assert "--auth-token" in str(caught.value)
    assert "s2" in capsys.readouterr().out


@pytest.mark.parametrize("spelling", ["create-agent", "create-ship"])
def test_create_agent_is_the_command_and_create_ship_still_reaches_it(
        monkeypatch, spelling):
    """`create-agent` is the command and `create-ship` is an alias for it."""
    reached = []
    monkeypatch.setattr(cli, "cmd_create_agent", reached.append)
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", spelling, "--api-key", KEY,
        "--harbor-id", "h1", "--name", "agent1"])
    cli.main()
    assert [(a.harbor_id, a.name) for a in reached] == [("h1", "agent1")]


def test_generate_reports_a_refused_credential_rather_than_tracebacking(
        monkeypatch, tmp_path, capsys):
    """A refused credential under --rotate-token is an exit sentence, not a traceback."""
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--api-key",
                  "examples/api-key.example.json", "--rotate-token",
                  client=RefusingClient())
    assert "could not be issued" in str(caught.value)
    assert "--auth-token" in str(caught.value)
    assert "Traceback" not in capsys.readouterr().err


def test_generate_never_asks_which_of_two_ships(monkeypatch, tmp_path):
    """Two ships and no --ship-id: nothing is minted and both are named."""
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--api-key",
                  "examples/api-key.example.json", "--rotate-token",
                  ships=("b1", "b2"))
    assert "b1" in str(caught.value) and "b2" in str(caught.value)


# -- livetest's one credential ------------------------------------------------
# The rig mints once per run, never per render: each render would revoke the
# token the previous deploy holds.

SHIP = "6c5b4a39281706f5e4d3c2b1"        # the sole agent in examples/facts


def _livetest(monkeypatch, tmp_path, client, *extra):
    """`livetest` with the rig faked; returns the regenerate callback it was given
    and the SystemExit."""
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
             # --run-test makes the rig want a regenerator; livetest.run is faked.
             "--run-test", "12345", *extra)
    return captured.get("regenerate"), manifests, caught.value


def test_livetest_mints_one_credential_for_a_whole_run(monkeypatch, tmp_path):
    """Four renders, one mint, one token."""
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
    """The rotated ship is named before the deploy."""
    _livetest(monkeypatch, tmp_path, FakeClient())
    out = capsys.readouterr().out
    assert SHIP in out
    assert "ROTATING" in out and out.index("ROTATING") < out.index("rotated")


def test_livetest_refuses_to_deploy_a_placeholder_token(monkeypatch, tmp_path):
    """--auth-token given the placeholder string is refused."""
    _, _, exit = _livetest(monkeypatch, tmp_path, FakeClient(), "--auth-token",
                           bundle_options.DEFAULT_OPTIONS["auth_token"])
    assert "create-agent" in str(exit)


def test_livetest_refuses_a_placeholder_bundle_with_nothing_to_re_render(
        monkeypatch, tmp_path):
    """A run that re-renders nothing refuses a bundle carrying the placeholder."""
    facts = json.load(open("examples/facts.example.json"))
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    # No auth_token: the bundle carries the placeholder.
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
    """A bundle built for another agent is refused before anything is built,
    naming both ships."""
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
    """A `*.yaml` this generator does not emit (an old bzm_limitrange.yaml) is
    refused."""
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
    """--auth-token is used as is and nothing is minted."""
    c = FakeClient()
    regenerate, manifests, exit = _livetest(monkeypatch, tmp_path, c,
                                            "--auth-token", "HELD-ALREADY")
    assert exit.code == 0
    regenerate({})
    regenerate({"ca_bundle": "PEM"})
    assert c.calls == []
    assert "HELD-ALREADY" in (manifests / "bzm_secret.yaml").read_text()


# -- what a private-registry run does not cover --------------------------------
# --local-registry without --run-test warns: nothing pulls the engine image.

# The sentence the warning is judged by.
NO_ENGINE = "does not cover the engine image"


def _registry_livetest(monkeypatch, tmp_path, *extra, func_ids=None):
    """`livetest` over a finished bundle with the rig faked. Hands back whether
    livetest.run was reached, so a warning can be told from a refusal."""
    facts = json.load(open("examples/facts.example.json"))
    if func_ids is not None:
        facts = {**facts, "func_ids": func_ids}
    (tmp_path / "facts.json").write_text(json.dumps(facts))
    manifests = tmp_path / "out"
    manifests.mkdir()
    # A real token, since a run that re-renders nothing refuses a placeholder;
    # an SV location declares `sv_ingress: none` to generate at all.
    opts = {"namespace": "ns1", "auth_token": "REAL"}
    if func_ids is not None:
        opts["sv_ingress"] = "none"
    gen.write(gen.generate(facts, opts), str(manifests))
    reached = []
    monkeypatch.setattr(cli.livetest, "run",
                        lambda *a, **kw: reached.append(True) or True)
    _account(monkeypatch, FakeClient())
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "livetest", "--api-key", KEY,
             "--facts", str(tmp_path / "facts.json"),
             "--manifests", str(manifests), "--namespace", "ns1",
             "--auth-token", "REAL", *extra)
    return bool(reached), caught.value


def test_livetest_says_a_registry_run_without_a_test_never_pulls_an_engine(
        monkeypatch, tmp_path, capsys):
    """--local-registry without --run-test warns, and the rig still deploys."""
    reached, exit = _registry_livetest(monkeypatch, tmp_path,
                                       "--local-registry", "5001")
    out = capsys.readouterr().out
    assert "warning" in out and "--run-test" in out
    assert NO_ENGINE in out and "cannot fail here" in out
    assert reached and exit.code == 0


def test_livetest_says_nothing_where_a_test_covers_the_engine(monkeypatch,
                                                              tmp_path, capsys):
    """The pair the flag is meant to be run as. There is nothing left unread,
    so there is nothing to say."""
    reached, exit = _registry_livetest(monkeypatch, tmp_path,
                                       "--local-registry", "5001",
                                       "--run-test", "12345")
    assert NO_ENGINE not in capsys.readouterr().out
    assert reached and exit.code == 0


def test_livetest_says_nothing_about_a_registry_nobody_asked_for(monkeypatch,
                                                                 tmp_path,
                                                                 capsys):
    """A run with neither flag pulls from wherever the bundle points and makes
    no claim about a private registry, so this warning is not its subject."""
    reached, exit = _registry_livetest(monkeypatch, tmp_path)
    assert NO_ENGINE not in capsys.readouterr().out
    assert reached and exit.code == 0


def test_livetest_says_nothing_to_a_location_that_runs_no_engine(monkeypatch,
                                                                 tmp_path,
                                                                 capsys):
    """A location with no engine gets no engine-image warning."""
    reached, exit = _registry_livetest(monkeypatch, tmp_path,
                                       "--local-registry", "5001",
                                       func_ids=["mockServices"])
    assert NO_ENGINE not in capsys.readouterr().out
    assert reached and exit.code == 0


def test_livetest_does_not_narrate_a_gap_a_refused_run_will_never_reach(
        monkeypatch, tmp_path, capsys):
    """A run refused for another flag prints no registry warning first."""
    reached, exit = _registry_livetest(monkeypatch, tmp_path,
                                       "--local-registry", "5001",
                                       "--contain-egress")
    assert NO_ENGINE not in capsys.readouterr().out
    assert not reached and "--contain-egress" in str(exit.code)


# -- the CA mode a rig run deploys -------------------------------------------
# Unsaid, the rig deploys the bundle's own CA mode; without the proxy, a mode
# naming a ConfigMap nothing creates is refused.

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
    """A CA ConfigMap nothing in the run creates is refused, before the mint."""
    c = FakeClient()
    captured, exit = _ca_livetest(monkeypatch, tmp_path, c, CA_FILE_MODE)
    assert captured == {}
    assert bundle_names.CA_CONFIGMAP in str(exit.code) and "--ca-mode file" in str(exit.code)
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
    """--ca-mode replacing the bundle's mode is allowed and said before the build."""
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
    """A bundle with no mode the rig can build (none, or OpenShift injection) gets
    inline, and the notice says which."""
    captured, exit = _ca_livetest(monkeypatch, tmp_path, FakeClient(), options,
                                  "--local-proxy", "--cluster", "kind")
    assert exit.code == 0 and captured["ca_mode"] == "inline"
    assert says in capsys.readouterr().out


# -- livetest on a docker bundle ----------------------------------------------
# The bundle, not a flag, picks the rig: up, online, down with docker compose.

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
    """A docker bundle runs with no --namespace and no --cluster."""
    seen, _client, exit = _compose_livetest(monkeypatch, tmp_path)
    assert exit.code == 0
    assert seen["ship_id"] == SHIP and seen["harbor_id"]


def test_livetest_compose_reports_a_failure_as_a_non_zero_exit(monkeypatch,
                                                               tmp_path):
    _seen, _client, exit = _compose_livetest(monkeypatch, tmp_path, ok=False)
    assert exit.code == 1


def test_livetest_compose_mints_no_credential(monkeypatch, tmp_path):
    """The compose path mints no credential."""
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
    """The cluster-shaped flags are refused on a compose run."""
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
    """A compose bundle for another agent (by container name) is refused first."""
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
    assert bundle_names.docker_container_name(SHIP) in str(caught.value)


def test_livetest_still_requires_a_namespace_for_a_manifests_bundle(monkeypatch,
                                                                    tmp_path):
    """A manifests bundle still requires --namespace."""
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
# Takes neither an API key nor a facts file.

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
    """`plan -o` resolves a relative directory against the shell's."""
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
    """There is a flag per SIZING_MODELS target and figure field (plus --users)."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "plan", "--help")
    out = capsys.readouterr().out
    for m in plan.SIZING_MODELS.values():
        for field in (m["target_field"], m["figure_field"]):
            # A model with no measured figure has no figure flag.
            if field:
                assert "--" + field.replace("_", "-") in out


def test_plan_sizes_every_model_it_offers(monkeypatch, capsys):
    """Each sizing flag reaches the planner as its own model."""
    flags = []
    for fid, m in plan.SIZING_MODELS.items():
        if fid != plan.PERFORMANCE:
            flags += ["--" + m["target_field"].replace("_", "-"), "20"]
    _run(monkeypatch, "plan", "--users", "5000", *flags)
    out = capsys.readouterr().out
    for m in plan.SIZING_MODELS.values():
        assert m["unit"] in out


def test_plan_refuses_a_target_of_zero_by_name(monkeypatch):
    """A target of zero is refused by name, not read as absent."""
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "plan", "--users", "5000", "--browsers", "0")
    assert "browsers must be at least 1" in str(caught.value)


def test_plan_refuses_a_per_pod_figure_with_no_target(monkeypatch):
    """A per-pod figure with no target is refused."""
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
# Every command reaching the account goes through core, so a refusal reads the
# same in a terminal as in the browser.

def _account_command(tmp_path, name):
    """One invocation per command that reaches BlazeMeter, with its files somewhere
    disposable (livetest gets a real bundle so it gets past its bundle guards)."""
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
    """Every account command builds its client through core.client_from_key with
    the --api-key it was given."""
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
    """An expired key is an exit sentence from every account command."""
    _account(monkeypatch, ExpiredClient())
    monkeypatch.setattr(cli.livetest, "run", lambda *a, **kw: pytest.fail(
        "the rig deployed against an account that answered 401"))
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, *_account_command(tmp_path, name))
    assert EXPIRED_401 in str(caught.value), \
        f"{name} did not report what BlazeMeter said"


def test_an_unreadable_key_file_is_answered_by_core(monkeypatch, tmp_path):
    """An unreadable key file is core's sentence, including how to make a key."""
    with pytest.raises(SystemExit) as caught:
        _run(monkeypatch, "locations", "--api-key", str(tmp_path),
             "--account-id", "7")
    assert str(tmp_path) in str(caught.value)
    assert "Settings -> API Keys" in str(caught.value)


def test_a_malformed_engine_size_is_a_sentence_not_a_stack(monkeypatch,
                                                           tmp_path, capsys):
    """A malformed engine size is an exit sentence, not a traceback."""
    with pytest.raises(SystemExit) as caught:
        _generate(monkeypatch, tmp_path, "--engine-cpu-limit", "banana")
    assert "not a Kubernetes CPU quantity" in str(caught.value)
    assert "Traceback" not in capsys.readouterr().err


# -- the location a test cannot start on ---------------------------------------

def test_create_location_warns_that_a_test_cannot_start_on_it(monkeypatch,
                                                              capsys):
    """core's unrunnable-location warning reaches stderr, ahead of the next step."""
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

def test_create_location_refuses_gui_functional_at_the_default_slots(
        monkeypatch, capsys):
    """A GUI Functional location at the default one slot is refused before the POST."""
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
    """--slots' help names the functionality that needs more than one."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "create-location", "--help")
    out = capsys.readouterr().out
    for rule in core.SLOT_MINIMUMS.values():
        assert rule["label"] in out and str(rule["minimum"]) in out


# -- which namespace a preflight is about --------------------------------------
# core.preflight_cluster: -n, then the bundle's, then the evidence's.

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
    """doctor and core.preflight agree on the namespace."""
    from bzm_opl_gen import doctor as doctor_mod
    doc = doctor_mod.load_evidence(ELSEWHERE_EVIDENCE)
    facts = json.load(open("examples/facts.example.json"))
    configured = tmp_path / "bundle"
    gen.write(gen.generate(facts, {"namespace": "configured"}), str(configured))
    # A bundle configured for a namespace, and no bundle at all.
    for options, manifests in (({"namespace": "configured"}, str(configured)),
                               ({}, str(tmp_path / "no-bundle"))):
        printed = _doctor_namespace(monkeypatch, capsys, tmp_path,
                                    "--manifests", manifests)
        assert printed == core.preflight(facts, options, doc)["namespace"]


def test_ca_mode_needs_the_proxy_that_owns_the_ca(monkeypatch, tmp_path):
    """--ca-mode without --local-proxy is refused."""
    _, _, exit = _livetest(monkeypatch, tmp_path, FakeClient(),
                           "--ca-mode", "existing")
    assert "--ca-mode needs --local-proxy" in str(exit)


def test_the_default_ca_mode_asks_for_no_proxy(monkeypatch, tmp_path):
    """inline is the default and the rig's long-standing behaviour, so it must
    not start refusing runs that never mentioned a CA."""
    regenerate, _, exit = _livetest(monkeypatch, tmp_path, FakeClient())
    assert exit.code == 0 and regenerate is not None


@pytest.mark.parametrize("flags, expected", [
    ((), False), (("--openshift",), True), (("--not-openshift",), False)])
def test_the_cluster_defaults_to_kubernetes_and_openshift_has_a_flag(
        monkeypatch, tmp_path, flags, expected):
    """The default is plain Kubernetes, so the positive needs a flag of its own;
    `--not-openshift` alone could only restate the default."""
    _generate(monkeypatch, tmp_path, *flags)
    profile = json.loads((tmp_path / "out" / "profile.json").read_text())
    assert profile["openshift_cluster"] is expected
    readme = (tmp_path / "out" / "README.md").read_text()
    assert ("\noc -n " in readme) is expected


def test_openshift_and_not_openshift_are_exclusive(monkeypatch, tmp_path):
    with pytest.raises(SystemExit):
        _generate(monkeypatch, tmp_path, "--openshift", "--not-openshift")


def test_update_location_sets_the_engine_requests_and_prints_before_and_after(
        monkeypatch, capsys):
    """The one command that fixes an existing location's engine requests."""
    seen = {}

    def update(client, harbor_id, **settings):
        seen.update(settings, harbor_id=harbor_id)
        return {"before": {k: None for k in core.LOCATION_SETTINGS},
                "after": {"slots": None, "threads_per_engine": None,
                          "override_cpu": 2, "override_memory": 8192},
                "ignored": []}
    _account(monkeypatch, FakeClient())
    monkeypatch.setattr(cli.core, "update_location", update)
    _run(monkeypatch, "update-location", "--api-key", "k.json", "--harbor-id",
         "h1", "--override-cpu", "2", "--override-memory", "8192")
    assert seen["override_cpu"] == 2 and seen["override_memory"] == 8192
    assert seen["slots"] is None
    out = capsys.readouterr().out
    assert "overrideCPU       not set -> 2  (changed)" in out
