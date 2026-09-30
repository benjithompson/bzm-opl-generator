"""Carrying images to an air-gapped site: `images --save` and `images --load`.

skopeo and docker are faked at `subprocess.run` (conftest refuses a real one),
the registry at `registry_client.http_request`, and which tools are on PATH
at `image_transfer.available`.
"""

import hashlib
import json
import os
import re
import shlex
import stat

import pytest

from bzm_opl_gen import (bundle_names, bundle_options, cli, core,
                         generate as gen, image_registry, image_transfer,
                         registry_client)
from test_generate import FACTS, FORMAT_BASE  # noqa: E402
from test_images import _manifest, registry  # noqa: E402,F401

GCR = "verdant-bulwark-278"


@pytest.fixture
def tools(monkeypatch):
    """Set which transfer tools are on PATH; none until a test says."""
    def install(*names):
        monkeypatch.setattr(image_transfer, "available", lambda: set(names))
    install()
    return install


class FakeTools:
    """skopeo and docker as `subprocess.run` sees them: each copy writes its
    archive, `fail_on` makes the command naming that text fail."""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, argv, *a, **kw):
        import subprocess
        self.calls.append({"argv": list(argv), "env": kw.get("env") or {}})
        out = None
        if argv[0] == "skopeo" and argv[-1].startswith("oci-archive:"):
            out = argv[-1][len("oci-archive:"):]
        elif argv[:2] == ["docker", "save"]:
            out = argv[argv.index("-o") + 1]
        if out:
            with open(out, "wb") as fh:
                fh.write(f"archive of {' '.join(argv)}".encode())
        if self.fail_on and self.fail_on in " ".join(argv):
            # The archive written so far stays behind, as a failed copy's does.
            return subprocess.CompletedProcess(argv, 1, "",
                                               "manifest unknown: boom")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def texts(self):
        return [" ".join(c["argv"]) for c in self.calls]


@pytest.fixture
def run(monkeypatch):
    def install(**kw):
        fake = FakeTools(**kw)
        monkeypatch.setattr("subprocess.run", fake)
        return fake
    return install


def _public(registry, sizes=(10, 20)):
    """BlazeMeter's registry, holding every image FACTS names."""
    refs = image_registry.mirror_targets(
        FACTS, {**bundle_options.DEFAULT_OPTIONS, "private_registry": "r",
                "crane_hook": True}, all_images=True)
    manifests = {}
    for ref, _ in refs:
        _, _, path, tag = registry_client.split_ref(ref)
        media, body, _ = _manifest(*sizes)
        manifests[(path, tag)] = (media, body, "sha256:" + hashlib.sha256(
            ref.encode()).hexdigest())
    return registry(manifests=manifests)


def _save(tmp_path, **kw):
    return core.save_images(FACTS, str(tmp_path / "save"), **kw)


# -- save ------------------------------------------------------------------------

def test_skopeo_saves_each_image_by_digest_with_a_manifest_and_sums(
        tmp_path, tools, run, registry):
    _public(registry, sizes=(1_000_000, 2_000_000))
    tools("skopeo", "docker")
    fake = run()
    out = _save(tmp_path)
    save = tmp_path / "save"
    assert out["tool"] == "skopeo" and out["archive_format"] == "oci-archive"
    refs = [r for r, _ in image_transfer.target_paths(
        FACTS, bundle_options.DEFAULT_OPTIONS)]
    assert [i["ref"] for i in out["images"]] == refs
    for call, ref in zip(fake.calls, refs):
        # Pinned: the file holds exactly what the manifest's digest names.
        digest = "sha256:" + hashlib.sha256(ref.encode()).hexdigest()
        assert call["argv"][:5] == ["skopeo", "--override-os", "linux",
                                    "--override-arch", "amd64"]
        assert f"docker://{ref.rsplit(':', 1)[0]}@{digest}" in call["argv"]
        assert call["env"]["TMPDIR"] == str(save)
    manifest = json.loads((save / image_transfer.MANIFEST_FILE).read_text())
    sums = image_transfer.parse_sums((save / image_transfer.SUMS_FILE).read_text())
    for i in manifest["images"]:
        body = (save / i["archive"]).read_bytes()
        assert i["sha256"] == hashlib.sha256(body).hexdigest() == sums[i["archive"]]
        assert i["bytes"] == len(body)
        assert i["size_mb"] == 3 and i["digest"].startswith("sha256:")
        assert i["source"] and i["target_path"]
    assert manifest["facts"] == FACTS and manifest["tool"] == "skopeo"


def test_docker_is_the_fallback_and_pulls_amd64(tmp_path, tools, run, registry):
    _public(registry)
    tools("docker")
    fake = run()
    out = _save(tmp_path)
    assert out["tool"] == "docker" and out["archive_format"] == "docker-archive"
    crane = FACTS["crane_image"]
    assert fake.texts()[:2] == [
        f"docker pull --platform linux/amd64 {crane}",
        f"docker save -o {tmp_path / 'save' / 'blazemeter_crane_3.7.55.docker.tar'} {crane}"]


def test_the_manifest_never_carries_a_secret(tmp_path, tools, run, registry):
    _public(registry)
    tools("skopeo")
    run()
    _save(tmp_path, options={**FORMAT_BASE["manifests"], "sv_tls_key": "KEY"})
    text = (tmp_path / "save" / image_transfer.MANIFEST_FILE).read_text()
    assert "de" * 32 not in text and "KEY" not in text


def test_a_dry_run_writes_nothing_and_runs_nothing(tmp_path, tools, monkeypatch,
                                                   registry):
    _public(registry)
    tools("skopeo")
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: pytest.fail("a dry run ran a command"))
    out = _save(tmp_path, dry_run=True)
    assert not (tmp_path / "save").exists()
    assert out["manifest"] is None
    assert len(out["commands"]) == len(out["images"])
    assert all(c.startswith(f"TMPDIR={tmp_path / 'save'} skopeo ")
               for c in out["commands"])


def test_a_missing_tool_refuses_and_a_dry_run_says_so(tmp_path, tools, registry):
    _public(registry)
    tools()
    with pytest.raises(core.ToolMissing, match="skopeo or docker"):
        _save(tmp_path)
    out = _save(tmp_path, dry_run=True)
    assert out["tool"] == "skopeo"
    assert any("not on PATH" in w for w in out["warnings"])
    with pytest.raises(core.ToolMissing, match="docker"):
        _save(tmp_path, tool="docker")


def test_an_interrupted_save_leaves_no_manifest_and_no_partial_file(
        tmp_path, tools, run, registry):
    _public(registry)
    tools("skopeo")
    run(fail_on="blazemeter/v4")
    with pytest.raises(core.UpstreamError, match="boom") as e:
        _save(tmp_path)
    assert "No images-manifest.json was written" in str(e.value)
    left = os.listdir(tmp_path / "save")
    assert image_transfer.MANIFEST_FILE not in left
    assert not [f for f in left if "v4" in f]


def test_a_finished_save_is_never_overwritten(tmp_path, tools, run, registry):
    _public(registry)
    tools("skopeo")
    run()
    _save(tmp_path)
    with pytest.raises(core.BadRequest, match="already holds a finished save"):
        _save(tmp_path)


def test_an_image_the_registry_lacks_refuses_before_any_pull(tmp_path, tools,
                                                             run, registry):
    registry()   # answers 404 for every tag
    tools("skopeo")
    fake = run()
    with pytest.raises(core.NotFound, match="has no"):
        _save(tmp_path)
    assert fake.calls == []


# -- disk space ------------------------------------------------------------------

class _Usage:
    def __init__(self, free):
        self.free = free


def test_short_space_refuses_and_names_both_numbers(tmp_path, tools, run,
                                                    registry, monkeypatch):
    _public(registry, sizes=(1_000_000_000,))   # 1000 MB each
    tools("skopeo")
    fake = run()
    monkeypatch.setattr(core.shutil, "disk_usage", lambda p: _Usage(500_000_000))
    with pytest.raises(core.DiskShort) as e:
        _save(tmp_path)
    # Three images, and skopeo holds the largest twice for a moment.
    assert "500 MB free" in str(e.value) and "4000 MB" in str(e.value)
    assert fake.calls == []
    assert _save(tmp_path, dry_run=True)["space"]["state"] == "short"


def test_enough_space_is_said_only_when_every_size_was_read(
        tmp_path, tools, run, registry, monkeypatch):
    _public(registry, sizes=(1_000_000,))
    tools("skopeo")
    monkeypatch.setattr(core.shutil, "disk_usage",
                        lambda p: _Usage(10 ** 12))
    space = _save(tmp_path, dry_run=True)["space"]
    assert space["state"] == "enough" and space["sizes_read"] == 3


def test_unread_sizes_warn_and_the_save_goes_on(tmp_path, tools, run):
    """The registry is offline (conftest): nothing is claimed about the
    need, and the save still runs."""
    tools("skopeo")
    fake = run()
    out = _save(tmp_path)
    assert out["space"]["state"] == registry_client.UNREAD
    assert out["space"]["sizes_read"] == 0
    assert "need is unknown" in out["space"]["detail"]
    assert fake.calls and out["manifest"]
    # No digest was read, so each image is copied by its tag.
    assert f"docker://{FACTS['crane_image']}" in fake.calls[0]["argv"]


def test_docker_space_is_a_lower_bound_and_never_enough(tmp_path, tools,
                                                        registry, monkeypatch):
    _public(registry, sizes=(1_000_000,))
    tools("docker")
    monkeypatch.setattr(core.shutil, "disk_usage", lambda p: _Usage(10 ** 12))
    space = _save(tmp_path, dry_run=True)["space"]
    assert space["state"] == registry_client.UNREAD
    assert "uncompressed" in space["detail"]


def test_unreadable_free_space_is_unread(tmp_path, tools, registry, monkeypatch):
    _public(registry, sizes=(1_000_000,))
    tools("skopeo")

    def denied(path):
        raise PermissionError("denied")
    monkeypatch.setattr(core.shutil, "disk_usage", denied)
    space = _save(tmp_path, dry_run=True)["space"]
    assert space["state"] == registry_client.UNREAD and space["free_mb"] is None


# -- load ------------------------------------------------------------------------

def _saved(tmp_path, tools, run, registry, tool="skopeo", **kw):
    _public(registry)
    tools(tool)
    run()
    _save(tmp_path, **kw)
    return str(tmp_path / "save")


@pytest.mark.parametrize("fmt,extra", [
    ("manifests", {}), ("manifests", {"crane_hook": True}),
    ("helm", {}), ("docker", {}),
])
def test_load_pushes_to_exactly_what_the_mirror_script_pushes(
        tmp_path, tools, run, registry, fmt, extra):
    """One list of destinations for every mirror path: mirror_targets'."""
    o = {**FORMAT_BASE[fmt], **extra, "private_registry": "reg.corp/bzm"}
    files = gen.generate(FACTS, o)
    profile = json.loads(files[bundle_names.PROFILE_FILE])
    directory = _saved(tmp_path, tools, run, registry, options=profile)
    script = files[bundle_names.MIRROR_SCRIPT_FILE]
    want = [shlex.split(line)[2] for line in script.splitlines()
            if line.startswith("mirror ")]
    out = core.load_images(directory, "reg.corp/bzm", dry_run=True)
    assert [i["target"] for i in out["images"]] == want
    assert [c.split()[-1] for c in out["commands"]] == [f"docker://{t}"
                                                         for t in want]
    # The manifest's target_path is the same name below the registry.
    manifest = core.read_saved_images(directory)
    assert [f"reg.corp/bzm/{i['target_path']}" for i in manifest["images"]] == want


def test_load_checks_every_archive_then_pushes_then_verifies(
        tmp_path, tools, run, registry, monkeypatch):
    directory = _saved(tmp_path, tools, run, registry)
    fake = run()
    checked = {}
    monkeypatch.setattr(core, "verify_mirror", lambda *a, **k: checked.update(
        args=a, kw=k) or {"registry": "reg.corp/bzm", "images": [],
                          "credentials": "x", "present": 3, "missing": 0,
                          "unread": 0})
    out = core.load_images(directory, "reg.corp/bzm")
    assert out["checksums"] == "verified"
    targets = [t for _, t in image_registry.mirror_targets(
        FACTS, {**bundle_options.DEFAULT_OPTIONS,
                "private_registry": "reg.corp/bzm"})]
    assert [c["argv"][-1] for c in fake.calls] == [f"docker://{t}"
                                                  for t in targets]
    assert all(c["argv"][-2].startswith(f"oci-archive:{directory}/")
               for c in fake.calls)
    assert checked["args"][:2] == (FACTS, "reg.corp/bzm")
    assert out["verify"]["present"] == 3


def test_a_checksum_mismatch_refuses_before_any_push_and_names_the_file(
        tmp_path, tools, run, registry):
    directory = _saved(tmp_path, tools, run, registry)
    manifest = core.read_saved_images(directory)
    victim = manifest["images"][1]["archive"]
    with open(os.path.join(directory, victim), "r+b") as fh:
        fh.write(b"X")
    fake = run()
    with pytest.raises(core.ChecksumMismatch) as e:
        core.load_images(directory, "reg.corp/bzm")
    assert f"{victim}: sha256 differs" in str(e.value)
    assert str(e.value).startswith("FAIL")
    assert fake.calls == []


@pytest.mark.parametrize("damage,said", [
    ("remove", "missing"), ("truncate", "bytes"), ("sums", "disagree"),
])
def test_every_kind_of_damaged_archive_is_named(tmp_path, tools, run, registry,
                                                damage, said):
    directory = _saved(tmp_path, tools, run, registry)
    name = core.read_saved_images(directory)["images"][0]["archive"]
    path = os.path.join(directory, name)
    if damage == "remove":
        os.remove(path)
    elif damage == "truncate":
        with open(path, "ab") as fh:
            fh.write(b"more")
    else:
        sums = os.path.join(directory, image_transfer.SUMS_FILE)
        text = open(sums).read()
        with open(sums, "w") as fh:
            fh.write(re.sub(r"^[0-9a-f]{64}", "0" * 64, text))
    with pytest.raises(core.ChecksumMismatch, match=f"{re.escape(name)}: .*{said}"):
        core.load_images(directory, "reg.corp/bzm")


def test_an_oci_archive_needs_skopeo_to_load(tmp_path, tools, run, registry):
    directory = _saved(tmp_path, tools, run, registry)
    tools("docker")
    with pytest.raises(core.ToolMissing, match="skopeo"):
        core.load_images(directory, "reg.corp/bzm")
    with pytest.raises(core.BadRequest, match="docker cannot"):
        core.load_images(directory, "reg.corp/bzm", tool="docker")


def test_a_docker_archive_loads_with_docker_when_skopeo_is_absent(
        tmp_path, tools, run, registry, monkeypatch):
    directory = _saved(tmp_path, tools, run, registry, tool="docker")
    monkeypatch.setattr(core, "verify_mirror", lambda *a, **k: None)
    fake = run()
    out = core.load_images(directory, "reg.corp/bzm")
    crane = FACTS["crane_image"]
    assert fake.texts()[:3] == [
        f"docker load -i {directory}/blazemeter_crane_3.7.55.docker.tar",
        f"docker tag {crane} reg.corp/bzm/crane:3.7.55",
        "docker push reg.corp/bzm/crane:3.7.55"]
    assert any("docker login reg.corp" in n for n in out["notes"])
    tools("skopeo", "docker")
    plan = core.load_images(directory, "reg.corp/bzm", dry_run=True)
    assert plan["tool"] == "skopeo"
    assert plan["commands"][0].split()[-2].startswith("docker-archive:")


def test_load_refuses_a_profile_that_needs_an_image_the_save_lacks(
        tmp_path, tools, run, registry):
    directory = _saved(tmp_path, tools, run, registry)
    with pytest.raises(core.BadRequest, match="cranehook"):
        core.load_images(directory, "reg.corp/bzm", dry_run=True,
                         options={"crane_hook": True})


def test_load_defaults_to_the_profile_the_save_recorded(tmp_path, tools, run,
                                                        registry):
    directory = _saved(tmp_path, tools, run, registry,
                       options=FORMAT_BASE["docker"])
    out = core.load_images(directory, "reg.corp/bzm", dry_run=True)
    assert "reg.corp/bzm/taurus-cloud:latest" in [i["target"]
                                                  for i in out["images"]]


def test_an_unfinished_save_is_refused(tmp_path):
    (tmp_path / "half").mkdir()
    with pytest.raises(core.NotFound, match="did not finish"):
        core.load_images(str(tmp_path / "half"), "reg.corp/bzm")


def test_credentials_reach_skopeo_in_a_private_file_never_the_command(
        tmp_path, tools, run, registry, monkeypatch):
    directory = _saved(tmp_path, tools, run, registry)
    monkeypatch.setenv(registry_client.USER_ENV, "robot")
    monkeypatch.setenv(registry_client.PASSWORD_ENV, "s3cret-pw")
    monkeypatch.setattr(core, "verify_mirror", lambda *a, **k: None)
    seen = {}

    def spy(argv, *a, **kw):
        import subprocess
        path = argv[argv.index("--dest-authfile") + 1]
        seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        seen["auth"] = json.load(open(path))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr("subprocess.run", spy)
    out = core.load_images(directory, "http://reg.corp:5000/bzm")
    assert "s3cret-pw" not in " ".join(out["commands"])
    assert seen["mode"] == 0o600
    # A dry run writes no auth file and names the one a real run would.
    plan = core.load_images(directory, "reg.corp/bzm", dry_run=True)
    assert all("--dest-authfile '<temporary-auth-file>'" in c
               for c in plan["commands"])
    assert list(seen["auth"]["auths"]) == ["reg.corp:5000"]
    assert all("--dest-tls-verify=false" in c for c in out["commands"])


def test_a_ca_file_reaches_skopeo_as_a_cert_dir(tmp_path, tools, run, registry,
                                               monkeypatch):
    directory = _saved(tmp_path, tools, run, registry)
    ca = tmp_path / "ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setattr(core, "verify_mirror", lambda *a, **k: None)
    seen = []

    def spy(argv, *a, **kw):
        import subprocess
        d = argv[argv.index("--dest-cert-dir") + 1]
        seen.append(open(os.path.join(d, "ca.crt")).read())
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr("subprocess.run", spy)
    core.load_images(directory, "reg.corp/bzm", ca_file=str(ca))
    assert seen and seen[0] == ca.read_text()


def test_short_space_for_skopeo_to_unpack_refuses_the_load(
        tmp_path, tools, run, registry, monkeypatch):
    directory = _saved(tmp_path, tools, run, registry)
    manifest_path = os.path.join(directory, image_transfer.MANIFEST_FILE)
    manifest = json.load(open(manifest_path))
    # Measured lengths, as the save recorded them.
    largest = max(i["bytes"] for i in manifest["images"])
    monkeypatch.setattr(core.shutil, "disk_usage", lambda p: _Usage(0))
    monkeypatch.setattr(core, "_check_archives", lambda *a: "verified")
    for i in manifest["images"]:
        i["bytes"] = largest * 10 ** 7
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh)
    fake = run()
    with pytest.raises(core.DiskShort, match="unpacks"):
        core.load_images(directory, "reg.corp/bzm")
    assert fake.calls == []


# -- the command line ------------------------------------------------------------

def _cli(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    cli.main()


def test_the_cli_plans_a_save_and_a_load(tmp_path, tools, run, registry,
                                         monkeypatch, capsys):
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps(FACTS))
    _public(registry)
    tools("skopeo")
    _cli(monkeypatch, "images", "--facts", str(facts), "--save",
         str(tmp_path / "save"), "--dry-run")
    out = capsys.readouterr().out
    assert "DRY-RUN: TMPDIR=" in out and "oci-archive:" in out
    run()
    _cli(monkeypatch, "images", "--facts", str(facts), "--save",
         str(tmp_path / "save"))
    out = capsys.readouterr().out
    assert out.count("+ TMPDIR=") == 3 and "wrote " in out
    _cli(monkeypatch, "images", "--load", str(tmp_path / "save"),
         "--mirror", "reg.corp/bzm", "--dry-run")
    out = capsys.readouterr().out
    assert "DRY-RUN: " in out and "docker://reg.corp/bzm/crane:3.7.55" in out
    assert "checksums not checked (dry run)" in out


def test_the_cli_load_needs_a_mirror(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="--mirror"):
        _cli(monkeypatch, "images", "--load", str(tmp_path))


def test_the_cli_load_exits_1_when_the_check_finds_an_image_missing(
        tmp_path, tools, run, registry, monkeypatch, capsys):
    directory = _saved(tmp_path, tools, run, registry)
    run()
    registry()   # the private registry answers 404: nothing arrived
    with pytest.raises(SystemExit) as e:
        _cli(monkeypatch, "images", "--load", directory, "--mirror",
             "reg.corp/bzm")
    assert e.value.code == 1
    assert "MISSING  reg.corp/bzm/crane:3.7.55" in capsys.readouterr().out


# -- the registry's scheme -------------------------------------------------------

@pytest.mark.parametrize("given,want", [
    ("http://reg.corp:5000/bzm", "http"),
    ("https://localhost:5055", "https"),
    ("localhost:5055", "http"),
    ("localhost/bzm", "http"),
    ("127.0.0.1:5000/bzm", "http"),
    ("127.200.3.4", "http"),
    ("reg.corp/bzm", "https"),
    ("reg.corp:5000", "https"),
    ("128.0.0.1:5000", "https"),
    ("localhost.corp:5000", "https"),
    ("127.0.0.1.example.com", "https"),
])
def test_the_scheme_is_the_registry_s_own_else_docker_s_rule(given, want):
    assert registry_client.registry_scheme(given) == want


def _asked(fake):
    """The scheme and host of every registry call except the token's."""
    return {c[1].split("/v2/")[0] for c in fake.calls
            if "/v2/token" not in c[1]}


@pytest.mark.parametrize("given,asked", [
    ("localhost:5055", "http://localhost:5055"),
    ("http://reg.corp:5000/bzm", "http://reg.corp:5000"),
    ("https://localhost:5055", "https://localhost:5055"),
    ("reg.corp:5000/bzm", "https://reg.corp:5000"),
])
def test_verify_speaks_the_scheme_the_rule_gives(registry, given, asked):
    fake = registry()
    out = core.verify_mirror(FACTS, given)
    assert _asked(fake) == {asked}
    assert not any(t["target"].startswith("http") for t in out["images"])


@pytest.mark.parametrize("given,host,note", [
    ("localhost:5055", "localhost:5055", False),
    ("http://localhost:5055", "localhost:5055", False),
    ("http://127.0.0.1:5055/bzm", "127.0.0.1:5055/bzm", False),
    ("http://reg.corp:5000/bzm", "reg.corp:5000/bzm", True),
])
def test_a_docker_load_pushes_bare_names_and_checks_over_the_same_scheme(
        tmp_path, tools, run, registry, given, host, note):
    directory = _saved(tmp_path, tools, run, registry, tool="docker")
    fake = run()
    reg = registry(manifests={})   # the private registry, after the push
    out = core.load_images(directory, given)
    pushed = [t.split()[-1] for t in fake.texts() if t.startswith("docker push")]
    assert pushed[0] == f"{host}/crane:3.7.55"
    assert not any("://" in t for t in fake.texts())
    assert _asked(reg) == {"http://" + host.split("/")[0]}
    assert out["verify"]["registry"] == host
    said = any("insecure-registries" in n for n in out["notes"])
    assert said is note


def test_a_skopeo_load_to_a_plain_http_registry_skips_tls(tmp_path, tools, run,
                                                         registry):
    directory = _saved(tmp_path, tools, run, registry)
    for given, off in (("localhost:5055", True), ("http://reg.corp/bzm", True),
                       ("reg.corp/bzm", False)):
        plan = core.load_images(directory, given, dry_run=True)
        assert all(("--dest-tls-verify=false" in c) is off
                   for c in plan["commands"])
        assert all(c.split()[-1].startswith("docker://" +
                                            registry_client.strip_scheme(given))
                   for c in plan["commands"])


def test_a_real_image_copier_is_refused_offline():
    import subprocess
    for tool in ("skopeo", "crane", "oras", "regctl"):
        with pytest.raises(AssertionError, match="offline test ran"):
            subprocess.run([tool, "--version"])
