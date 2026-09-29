"""The image catalogue, the registry reader, `images --explain/--verify`,
`/api/images`, the MCP rows and every bundle's IMAGES.md.

The registry is always faked at `registry_client.http_request`; conftest makes
every unfaked read answer as if offline.
"""

import csv
import io
import json
import re
import shlex
import urllib.parse

import pytest

from bzm_opl_gen import (bundle_names, bundle_options, cli, core,
                         facts as facts_mod, generate as gen, image_catalog,
                         image_registry, markers, registry_client)
from test_generate import FACTS, FORMAT_BASE  # noqa: E402
from versions_fixtures import VERSIONS_GUI, VERSIONS_PERFORMANCE  # noqa: E402

PUBLIC = "gcr.io/verdant-bulwark-278"

# A performance location as `facts` wrote it before entries carried a source:
# two images from the location's list, the rest from the catalogue.
OLD_FACTS = {
    "harbor_id": "6abc282f97bba688880a8834",
    "harbor_name": "sizing-scratch",
    "func_ids": ["performance"],
    "ships": [{"id": "6abc28345ad15bd2ef0454c4", "name": "a", "state": "empty",
               "installed_version": None, "last_heartbeat": None}],
    "image_list": {"state": "read", "count": 3, "detail": None},
    "crane_image": f"{PUBLIC}/blazemeter/crane:3.8.0",
    "images": [
        {"key": "apm-image:1.7.123", "repo": f"{PUBLIC}/blazemeter/apm",
         "tag": "1.7.123", "size_mb": None, "category": "performance"},
        {"key": "taurus-cloud:2.4.533-reduced", "repo": f"{PUBLIC}/blazemeter/v4",
         "tag": "2.4.533-reduced", "size_mb": None, "category": "performance"},
    ] + [dict(i, size_mb=None) for i in facts_mod.FALLBACK_IMAGES
         if i["key"] not in ("taurus-cloud:latest", "apm-image:latest")],
    "images_source": "location image list + fallback-catalogue",
}

CATALOGUE_KEYS = {"source", "location", "image_list_state", "registry_lookup",
                  "images"}
IMAGE_KEYS = {"key", "repo", "tag", "ref", "category", "functionalities",
              "purpose", "pulled_when", "verified", "required", "tag_mutable",
              "source", "registry_state", "registry_detail", "digest",
              "size_mb", "newest_tag", "update_available", "resolves_to"}


# -- a registry, faked at the transport ----------------------------------------

class FakeRegistry:
    """A Docker Registry v2 behind gcr.io's bearer challenge.

    `manifests` maps (repo path, tag or digest) to (media type, body, digest);
    `tags` maps a repo path to its tag pages. `status` answers every registry
    call with that code once authenticated; `down` answers nothing at all.
    """

    def __init__(self, manifests=None, tags=None, status=None, down=False,
                 users=None, challenge="bearer", broken=()):
        self.manifests = manifests or {}
        # (repo path, tag) whose manifest answers 500.
        self.broken = set(broken)
        self.tags = tags or {}
        self.status, self.down, self.users = status, down, users
        self.challenge = challenge
        self.calls = []

    def __call__(self, method, url, headers, timeout, context):
        self.calls.append((method, url, dict(headers)))
        if self.down:
            raise registry_client.Unreachable("timed out")
        u = urllib.parse.urlparse(url)
        auth = headers.get("Authorization")
        if u.path == "/v2/token":
            if self.users and auth != _basic(*self.users):
                return registry_client.Response(401, {}, b"")
            return registry_client.Response(
                200, {}, json.dumps({"token": "T"}).encode())
        wanted = "Bearer T" if self.challenge == "bearer" else (
            _basic(*self.users) if self.users else None)
        if auth != wanted or wanted is None:
            head = (f'Bearer realm="https://{u.netloc}/v2/token",service="{u.netloc}"'
                    if self.challenge == "bearer" else 'Basic realm="r"')
            return registry_client.Response(401, {"WWW-Authenticate": head}, b"")
        if self.status:
            return registry_client.Response(self.status, {}, b"")
        m = re.match(r"^/v2/(.+)/manifests/(.+)$", u.path)
        if m and (m.group(1), m.group(2)) in self.broken:
            return registry_client.Response(500, {}, b"")
        if m:
            found = self.manifests.get((m.group(1), m.group(2)))
            if not found:
                return registry_client.Response(404, {}, b'{"errors":[]}')
            media, body, digest = found
            return registry_client.Response(
                200, {"Content-Type": media, "Docker-Content-Digest": digest},
                b"" if method == "HEAD" else json.dumps(body).encode())
        m = re.match(r"^/v2/(.+)/tags/list$", u.path)
        if m:
            pages = self.tags.get(m.group(1))
            if pages is None:
                return registry_client.Response(404, {}, b"")
            page = int(urllib.parse.parse_qs(u.query).get("page", ["0"])[0])
            link = {}
            if page + 1 < len(pages):
                link = {"Link": f'</v2/{m.group(1)}/tags/list?page={page + 1}>; '
                                f'rel="next"'}
            return registry_client.Response(
                200, link, json.dumps({"tags": pages[page]}).encode())
        return registry_client.Response(404, {}, b"")


def _basic(user, password):
    import base64
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _manifest(*sizes):
    return ("application/vnd.oci.image.manifest.v1+json",
            {"mediaType": "application/vnd.oci.image.manifest.v1+json",
             "layers": [{"size": s} for s in sizes]}, "sha256:single")


def _index(child_digest):
    return ("application/vnd.oci.image.index.v1+json",
            {"mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [
                 {"digest": "sha256:arm", "platform": {"os": "linux",
                                                        "architecture": "arm64"}},
                 {"digest": child_digest, "platform": {"os": "linux",
                                                        "architecture": "amd64"}}]},
            "sha256:index")


@pytest.fixture
def registry(monkeypatch):
    """Install a FakeRegistry built from keyword arguments; returns it."""
    def install(**kw):
        fake = FakeRegistry(**kw)
        monkeypatch.setattr(registry_client, "http_request", fake)
        return fake
    return install


# -- references and tag series --------------------------------------------------

@pytest.mark.parametrize("ref,want", [
    (f"{PUBLIC}/blazemeter/v4:2.4.533-reduced",
     ("https", "gcr.io", "verdant-bulwark-278/blazemeter/v4", "2.4.533-reduced")),
    ("localhost:5001/bzm/crane:3.8.0",
     ("https", "localhost:5001", "bzm/crane", "3.8.0")),
    ("http://reg.local:5000/bzm/blazemeter/v4:1",
     ("http", "reg.local:5000", "bzm/blazemeter/v4", "1")),
    ("registry:2", ("https", registry_client.DOCKER_HUB, "library/registry", "2")),
    ("reg.corp/x/y", ("https", "reg.corp", "x/y", "latest")),
])
def test_a_reference_splits_into_host_path_and_tag(ref, want):
    assert registry_client.split_ref(ref) == want


def test_the_newest_tag_stays_in_its_own_series():
    """Same suffix and as many version parts; a branch build is another
    series, and `latest` has none."""
    tags = ["2.4.533-reduced", "2.4.538-reduced", "2.4.539-MOB-1_fix-22-reduced",
            "2.4.540-jmeter", "2.4.54-reduced", "latest", "master-2955"]
    assert registry_client.newest_in_series("2.4.533-reduced", tags) == "2.4.538-reduced"
    assert registry_client.newest_in_series("2.4.540-jmeter", tags) == "2.4.540-jmeter"
    assert registry_client.newest_in_series("latest", tags) is None
    assert registry_client.newest_in_series("1.7.123", ["1.7.126", "1.8", "1.7.99"]) \
        == "1.7.126"
    # The current tag need not be in the list at all.
    assert registry_client.newest_in_series("3.8.0", []) == "3.8.0"


# -- reading the public registry --------------------------------------------------

def test_a_lookup_follows_the_bearer_challenge_and_sums_the_amd64_layers(registry):
    fake = registry(
        manifests={("verdant-bulwark-278/blazemeter/crane", "3.8.0"): _index("sha256:amd"),
                   ("verdant-bulwark-278/blazemeter/crane", "sha256:amd"):
                       _manifest(40_000_000, 39_600_000)},
        tags={"verdant-bulwark-278/blazemeter/crane": [["3.7.55", "3.8.0"],
                                                         ["3.8.1", "latest"]]})
    out = registry_client.lookup(f"{PUBLIC}/blazemeter/crane:3.8.0")
    assert out == {"registry_state": "read", "registry_detail": None,
                   "digest": "sha256:index", "size_mb": 80,
                   "newest_tag": "3.8.1", "update_available": True,
                   "resolves_to": None}
    token_calls = [c for c in fake.calls if "/v2/token" in c[1]]
    assert "scope=repository%3Averdant-bulwark-278%2Fblazemeter%2Fcrane%3Apull" \
        in token_calls[0][1]
    # Anonymous: nothing but the bearer token is ever sent.
    assert all(c[2].get("Authorization") in (None, "Bearer T") for c in fake.calls)


TORERO = "verdant-bulwark-278/blazemeter/torero"


def _digest(d, *sizes):
    media, body, _ = _manifest(*sizes or (1,))
    return (media, body, d)


def test_newest_versions_order_by_version_then_the_shortest_suffix():
    tags = ["latest", "1.0.2-MOB-1-reduced", "1.0.2-reduced", "1.0.10",
            "1.0.9", "master-99"]
    assert registry_client.newest_versions(tags, 3) == [
        "1.0.10", "1.0.9", "1.0.2-reduced"]


def test_a_floating_tag_is_named_by_the_newest_version_sharing_its_digest(registry):
    fake = registry(
        manifests={(TORERO, "latest"): _digest("sha256:b", 152_000_000),
                   (TORERO, "4.6.192"): _digest("sha256:c"),
                   (TORERO, "4.6.182"): _digest("sha256:b"),
                   (TORERO, "4.6.170"): _digest("sha256:b")},
        tags={TORERO: [["4.6.170", "4.6.182", "4.6.192", "latest"]]})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    assert out["registry_state"] == "read" and out["size_mb"] == 152
    assert out["resolves_to"] == "4.6.182"
    assert out["newest_tag"] == "4.6.192" and out["update_available"] is True
    assert out["registry_detail"] is None
    # Newest first, stopping at the first match.
    heads = [urllib.parse.urlparse(c[1]).path.rsplit("/", 1)[-1]
             for c in fake.calls if c[0] == "HEAD"]
    assert "4.6.170" not in heads and heads.index("4.6.192") < heads.index("4.6.182")


def test_a_floating_tag_that_matches_nothing_is_read_and_says_so(registry):
    registry(manifests={(TORERO, "latest"): _digest("sha256:old"),
                        (TORERO, "4.6.192"): _digest("sha256:c")},
             tags={TORERO: [["4.6.192", "latest"]]})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    assert out["registry_state"] == "read" and out["resolves_to"] is None
    assert out["newest_tag"] is None and out["update_available"] is None
    assert "none of the 1 newest" in out["registry_detail"]


def test_a_comparison_that_went_unread_is_not_a_no_match(registry):
    """Invariant 1: an unread candidate is reported as unread, not as absent."""
    registry(manifests={(TORERO, "latest"): _digest("sha256:b"),
                        (TORERO, "4.6.182"): _digest("sha256:b")},
             tags={TORERO: [["4.6.182", "4.6.192", "latest"]]},
             broken={(TORERO, "4.6.192")})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    assert out["resolves_to"] == "4.6.182"
    registry(manifests={(TORERO, "latest"): _digest("sha256:b")},
             tags={TORERO: [["4.6.192", "latest"]]},
             broken={(TORERO, "4.6.192")})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    assert out["resolves_to"] is None
    assert "could not be compared" in out["registry_detail"]
    assert "none of the" not in out["registry_detail"]


def test_an_unread_tag_list_leaves_a_floating_tag_unnamed(registry):
    registry(manifests={(TORERO, "latest"): _digest("sha256:b")})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    assert out["registry_state"] == "read" and out["resolves_to"] is None
    assert "tag list could not be read" in out["registry_detail"]


def test_naming_a_floating_tag_is_bounded_in_requests_and_time(registry,
                                                                monkeypatch):
    versions = [f"1.0.{n}" for n in range(50)]
    fake = registry(manifests={(TORERO, "latest"): _digest("sha256:b")},
                    tags={TORERO: [versions + ["latest"]]})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    heads = [c for c in fake.calls if c[0] == "HEAD"]
    assert len(heads) == registry_client.RESOLVE_CANDIDATES
    assert "1.0.49 down to 1.0.40" in out["registry_detail"]
    monkeypatch.setattr(registry_client, "RESOLVE_BUDGET_S", -1)
    fake = registry(manifests={(TORERO, "latest"): _digest("sha256:b")},
                    tags={TORERO: [versions + ["latest"]]})
    out = registry_client.lookup(f"gcr.io/{TORERO}:latest")
    assert not [c for c in fake.calls if c[0] == "HEAD"]
    assert "stopped comparing" in out["registry_detail"]


def test_a_pinned_tag_never_resolves(registry):
    registry(manifests={(TORERO, "4.6.182"): _digest("sha256:b")},
             tags={TORERO: [["4.6.182", "latest"]]})
    out = registry_client.lookup(f"gcr.io/{TORERO}:4.6.182")
    assert out["resolves_to"] is None and out["newest_tag"] == "4.6.182"


def test_a_tag_the_registry_lacks_is_a_read_not_a_failure(registry):
    """Invariant 1: a 404 is an answer, and says so; it is never unread."""
    registry()
    out = registry_client.lookup(f"{PUBLIC}/blazemeter/nope:1.0")
    assert out["registry_state"] == "read"
    assert out["digest"] is None and "no such tag" in out["registry_detail"]


@pytest.mark.parametrize("kw,detail", [
    ({"down": True}, "did not answer"),
    ({"status": 403}, "refused"),
    ({"status": 500}, "500"),
])
def test_a_registry_that_does_not_answer_is_unread_with_the_reason(registry, kw,
                                                                  detail):
    registry(**kw)
    out = registry_client.lookup(f"{PUBLIC}/blazemeter/v4:2.4.533-reduced")
    assert out["registry_state"] == "unread"
    assert detail in out["registry_detail"]
    assert out["digest"] is None and out["newest_tag"] is None


def test_an_unread_tag_list_leaves_the_newest_tag_unknown_and_says_why(registry):
    registry(manifests={("verdant-bulwark-278/blazemeter/v4", "2.4.533-reduced"):
                        _manifest(1)})
    out = registry_client.lookup(f"{PUBLIC}/blazemeter/v4:2.4.533-reduced")
    assert out["registry_state"] == "read" and out["digest"] == "sha256:single"
    assert out["newest_tag"] is None and out["update_available"] is None
    assert "tag list could not be read" in out["registry_detail"]


def test_the_offline_guard_makes_every_unfaked_read_unread():
    out = registry_client.lookup(f"{PUBLIC}/blazemeter/v4:2.4.533-reduced")
    assert out["registry_state"] == "unread"
    assert "offline test" in out["registry_detail"]


# -- a customer's mirror ---------------------------------------------------------

def test_check_tells_present_missing_and_unread_apart(registry):
    """Invariant 1 again: a refusal never looks like an absence."""
    registry(manifests={("bzm/crane", "3.8.0"): _manifest(1)})
    reg = registry_client.Registry("reg.corp")
    assert reg.check("bzm/crane", "3.8.0") == {
        "state": "present", "detail": None, "digest": "sha256:single"}
    assert reg.check("bzm/crane", "9.9.9")["state"] == "missing"
    registry(status=401)
    assert registry_client.Registry("reg.corp").check("bzm/crane", "3.8.0")["state"] \
        == "unread"
    registry(down=True)
    assert registry_client.Registry("reg.corp").check("bzm/crane", "3.8.0")["state"] \
        == "unread"


def test_credentials_reach_the_token_service_and_a_basic_registry(registry):
    fake = registry(users=("u", "p"),
                    manifests={("bzm/crane", "3.8.0"): _manifest(1)})
    reg = registry_client.Registry("reg.corp", credentials=("u", "p"))
    assert reg.check("bzm/crane", "3.8.0")["state"] == "present"
    assert any(c[2].get("Authorization") == _basic("u", "p")
               for c in fake.calls if "/v2/token" in c[1])
    registry(challenge="basic", users=("u", "p"),
             manifests={("bzm/crane", "3.8.0"): _manifest(1)})
    reg = registry_client.Registry("reg.corp", credentials=("u", "p"))
    assert reg.check("bzm/crane", "3.8.0")["state"] == "present"
    # Wrong credentials are a refusal, not an absence.
    registry(users=("u", "p"))
    reg = registry_client.Registry("reg.corp", credentials=("u", "wrong"))
    assert reg.check("bzm/crane", "3.8.0")["state"] == "unread"


def test_credentials_come_from_the_environment_then_the_docker_config(tmp_path):
    env = {registry_client.USER_ENV: "u", registry_client.PASSWORD_ENV: "p"}
    assert registry_client.credentials_for("reg.corp", env)[:2] == ("u", "p")
    import base64
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"auths": {"reg.corp": {
        "auth": base64.b64encode(b"du:dp").decode()}}}))
    assert registry_client.credentials_for("reg.corp", {}, str(cfg))[:2] == ("du", "dp")
    cfg.write_text(json.dumps({"credsStore": "desktop"}))
    user, password, why = registry_client.credentials_for("reg.corp", {}, str(cfg))
    assert user is None and registry_client.USER_ENV in why
    assert registry_client.credentials_for(
        "reg.corp", {}, str(tmp_path / "absent.json"))[0] is None


def _mirror_destinations(script):
    """The destination of each `mirror <from> <to>` line the script runs."""
    return [shlex.split(line)[2] for line in script.splitlines()
            if line.startswith("mirror ")]


@pytest.mark.parametrize("fmt,extra", [
    ("manifests", {}), ("manifests", {"crane_hook": True}),
    ("helm", {}), ("docker", {}),
])
def test_verify_asks_for_exactly_what_the_mirror_script_pushes(registry, fmt,
                                                              extra):
    """One list of destinations: the script's, from its own profile."""
    o = {**FORMAT_BASE[fmt], **extra, "private_registry": "reg.corp/bzm"}
    script = gen.generate(FACTS, o)[bundle_names.MIRROR_SCRIPT_FILE]
    fake = registry()
    profile = json.loads(gen.generate(FACTS, o)[bundle_names.PROFILE_FILE])
    out = core.verify_mirror(FACTS, "reg.corp/bzm", options=profile)
    assert [i["target"] for i in out["images"]] == _mirror_destinations(script)
    asked = {urllib.parse.urlparse(c[1]).path for c in fake.calls
             if c[0] == "HEAD"}
    for target in _mirror_destinations(script):
        _, _, path, tag = registry_client.split_ref(target)
        assert f"/v2/{path}/manifests/{tag}" in asked


def _pushed(out):
    return [c.split()[-1] for c in out["commands"] if c.startswith("docker push ")]


@pytest.mark.parametrize("fmt,extra", [
    ("manifests", {}), ("manifests", {"crane_hook": True}),
    ("helm", {}), ("docker", {}),
])
def test_pull_mirror_pushes_exactly_what_the_mirror_script_pushes(fmt, extra):
    """`images --pull --mirror` reads the script's destinations, per format,
    never a rule of its own."""
    o = {**FORMAT_BASE[fmt], **extra, "private_registry": "reg.corp/bzm"}
    files = gen.generate(FACTS, o)
    profile = json.loads(files[bundle_names.PROFILE_FILE])
    out = core.mirror_images(FACTS, mirror="reg.corp/bzm", dry_run=True,
                             options=profile)
    assert _pushed(out) == _mirror_destinations(files[bundle_names.MIRROR_SCRIPT_FILE])


@pytest.mark.parametrize("fmt", ["manifests", "docker"])
def test_pull_mirror_all_names_every_image_the_way_the_agent_asks(fmt):
    """With --all the images the funcIds do not select are pushed to the
    names crane composes too, not to a bare last segment."""
    o = {**bundle_options.DEFAULT_OPTIONS, **FORMAT_BASE[fmt],
         "private_registry": "reg.corp/bzm"}
    out = core.mirror_images(FACTS, mirror="reg.corp/bzm", dry_run=True,
                             all_images=True, options=FORMAT_BASE[fmt])
    pushed = _pushed(out)
    assert pushed == [t for _, t in image_registry.mirror_targets(
        FACTS, o, all_images=True)]
    mock = next(i for i in FACTS["images"] if "service-mock" in i["repo"])
    want = ("reg.corp/bzm/blazemeter/service-mock:latest" if fmt == "docker"
            else image_registry.composed_image_ref(mock["repo"], mock["tag"],
                                                   "reg.corp/bzm"))
    assert want in pushed


def test_pull_without_a_mirror_only_pulls():
    out = core.mirror_images(FACTS, dry_run=True, all_images=True)
    assert [c.split()[1] for c in out["commands"]] == ["pull"] * len(
        facts_mod.image_refs(FACTS, all_images=True))


def test_pull_mirror_reads_the_bundle_s_profile(monkeypatch, tmp_path, capsys):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"output_format": "docker"}))
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path), "--pull",
         "--dry-run", "--mirror", "reg.corp/bzm", "--profile", str(profile))
    assert "docker push reg.corp/bzm/taurus-cloud:latest" in capsys.readouterr().out


def test_verify_counts_each_state(registry):
    registry(manifests={("bzm/crane", "3.7.55"): _manifest(1)})
    out = core.verify_mirror(FACTS, "reg.corp/bzm")
    assert out["present"] == 1 and out["missing"] == len(out["images"]) - 1
    assert out["unread"] == 0 and out["credentials"].startswith("anonymous")
    registry(status=403)
    out = core.verify_mirror(FACTS, "reg.corp/bzm")
    assert out["unread"] == len(out["images"]) and out["missing"] == 0


def test_verify_refuses_an_unusable_ca_file(tmp_path):
    with pytest.raises(core.BadRequest):
        core.verify_mirror(FACTS, "reg.corp/bzm", ca_file=str(tmp_path / "no.pem"))
    with pytest.raises(core.BadRequest):
        core.verify_mirror(FACTS, "http://")


# -- the catalogue -----------------------------------------------------------------

def test_every_catalogue_image_is_described():
    """No image the tool can name falls through to the undescribed row."""
    for i in image_catalog.catalogue_rows():
        assert i["purpose"] != image_catalog.UNDESCRIBED["purpose"], i["ref"]
        assert i["pulled_when"] and isinstance(i["verified"], bool)
    assert set(image_catalog.CATALOG) >= {
        image_catalog.repo_path(i["repo"]) for i in facts_mod.FALLBACK_IMAGES}


def test_functionalities_are_the_account_s_funcids():
    """Invariant 4: the vocabulary is funcIds, derived from the same table
    that selects a bundle's images."""
    vocabulary = set(facts_mod.CATEGORY_BY_FUNC)
    for r in image_catalog.catalogue_rows():
        assert set(r["functionalities"]) <= vocabulary, r["ref"]
    rows = {image_catalog.repo_path(r["repo"]): r for r in image_catalog.catalogue_rows()}
    assert set(rows["blazemeter/crane"]["functionalities"]) == vocabulary
    assert rows["blazemeter/doduo"]["functionalities"] == ["functionalGui"]
    assert rows["blazemeter/service-mock"]["functionalities"] == ["mockServices"]
    assert rows["cranehook"]["functionalities"] == []


def test_browser_images_are_described_by_their_pattern():
    f = facts_mod.gather(_Account(VERSIONS_GUI, ["functionalGui"]), "H1")
    rows = {r["repo"].rsplit("/", 1)[-1]: r
            for r in image_catalog.location_rows(f)}
    chrome = next(r for k, r in rows.items() if k.startswith("chrome_"))
    firefox = next(r for k, r in rows.items() if k.startswith("firefox_"))
    assert chrome["category"] == "gui" and chrome["functionalities"] == ["functionalGui"]
    assert chrome["purpose"] == firefox["purpose"] == image_catalog.BROWSER["purpose"]
    # Only a Chrome build has been seen running.
    assert chrome["verified"] and not firefox["verified"]
    assert all(r["required"] for r in rows.values())


def test_a_floating_tag_is_marked_mutable():
    assert image_catalog.mutable_tag("latest")
    assert not image_catalog.mutable_tag("2.4.533-reduced")


def test_catalogue_text_is_customer_facing():
    """Invariant 8: no issue numbers or module names reach a customer."""
    text = json.dumps([image_catalog.CATALOG, image_catalog.BROWSER,
                       image_catalog.UNDESCRIBED])
    assert not re.search(r"#\d|\.py\b|registry_client|facts\.|FALLBACK", text)


class _Account:
    """Just enough BzmClient for facts.gather: one agent, an image list."""

    def __init__(self, versions, func_ids=("performance",)):
        self._versions, self._func_ids = versions, list(func_ids)

    def private_location(self, harbor_id):
        return {"id": harbor_id, "name": "L", "funcIds": self._func_ids,
                "ships": [{"id": "S1", "state": "empty"}]}

    def ship_versions(self, harbor_id, ship_id):
        return self._versions


def test_gather_records_where_each_image_came_from():
    f = facts_mod.gather(_Account(VERSIONS_PERFORMANCE), "H1")
    by_key = {i["key"]: i["source"] for i in f["images"]}
    assert by_key["taurus-cloud:2.4.454-reduced"] == "location-versions"
    assert by_key["torero:latest"] == "catalogue"
    assert f["crane_source"] == "location-versions"
    m = facts_mod.manual("h", "s")
    assert m["crane_source"] == "catalogue"
    assert {i["source"] for i in m["images"]} == {"catalogue"}


def test_older_facts_without_sources_are_still_labelled():
    rows = {r["key"]: r for r in image_catalog.location_rows(OLD_FACTS)}
    assert rows["taurus-cloud:2.4.533-reduced"]["source"] == "location-versions"
    assert rows["torero:latest"]["source"] == "catalogue"
    assert rows["blazemeter/crane:3.8.0"]["source"] == "location-versions"


# -- core.image_catalog: the contract ------------------------------------------------

def test_the_location_answer_has_exactly_the_contract_s_fields():
    out = core.image_catalog(OLD_FACTS, lookup=False)
    assert set(out) == CATALOGUE_KEYS
    assert out["source"] == "location"
    assert out["location"] == {"harbor_id": "6abc282f97bba688880a8834",
                               "name": "sizing-scratch",
                               "func_ids": ["performance"]}
    assert out["image_list_state"] == "read"
    assert out["registry_lookup"] == {"state": "not-asked", "detail": None}
    for i in out["images"]:
        assert set(i) == IMAGE_KEYS, i["ref"]
        assert i["registry_state"] == "not-asked"
        assert i["required"] is True
    # Crane first, then what the funcIds select; nothing for SV or GUI.
    assert [i["key"] for i in out["images"]] == [
        "blazemeter/crane:3.8.0", "apm-image:1.7.123",
        "taurus-cloud:2.4.533-reduced", "torero:latest", "richrach:latest"]


def test_all_images_adds_the_rest_as_not_required():
    out = core.image_catalog(OLD_FACTS, lookup=False, all_images=True)
    extra = [i for i in out["images"] if i["required"] is False]
    assert {i["category"] for i in extra} == {"mock", "recorder", "gui", "hook"}


def test_the_catalogue_answer_names_no_location():
    out = core.image_catalog(None, lookup=False)
    assert set(out) == CATALOGUE_KEYS
    assert out["source"] == "catalogue" and out["location"] is None
    assert out["image_list_state"] == "not-asked"
    assert all(i["required"] is None and i["source"] == "catalogue"
               for i in out["images"])
    assert out["images"][0]["repo"] == facts_mod.CRANE_REPO


def test_the_lookup_summary_says_read_partial_or_unread(monkeypatch):
    answers = {"read": {"registry_state": "read", "registry_detail": None,
                        "digest": "sha256:x", "size_mb": 1, "newest_tag": None,
                        "update_available": None, "resolves_to": None},
               "unread": {"registry_state": "unread", "registry_detail": "refused (403)",
                          "digest": None, "size_mb": None, "newest_tag": None,
                          "update_available": None, "resolves_to": None}}
    monkeypatch.setattr(registry_client, "lookup", lambda ref: answers["read"])
    assert core.image_catalog(OLD_FACTS)["registry_lookup"] == {
        "state": "read", "detail": None}
    monkeypatch.setattr(registry_client, "lookup",
                        lambda ref: answers["unread" if "torero" in ref else "read"])
    summary = core.image_catalog(OLD_FACTS)["registry_lookup"]
    assert summary["state"] == "partial" and "1 of 5" in summary["detail"]
    monkeypatch.setattr(registry_client, "lookup", lambda ref: answers["unread"])
    out = core.image_catalog(OLD_FACTS)
    assert out["registry_lookup"]["state"] == "unread"
    assert all(set(i) == IMAGE_KEYS for i in out["images"])


def test_an_offline_lookup_never_raises():
    out = core.image_catalog(OLD_FACTS, lookup=True)
    assert out["registry_lookup"]["state"] == "unread"
    assert all(i["registry_state"] == "unread" for i in out["images"])


def test_a_lookup_reads_the_public_reference(registry):
    registry(manifests={("verdant-bulwark-278/blazemeter/v4", "2.4.533-reduced"):
                        _manifest(3_488_000_000)},
             tags={"verdant-bulwark-278/blazemeter/v4": [["2.4.533-reduced",
                                                          "2.4.538-reduced"]]})
    out = core.image_catalog(OLD_FACTS)
    v4 = next(i for i in out["images"] if i["key"].startswith("taurus-cloud"))
    assert (v4["size_mb"], v4["newest_tag"], v4["update_available"]) == \
        (3488, "2.4.538-reduced", True)
    # The registry answered for every image; the others it does not hold.
    assert out["registry_lookup"]["state"] == "read"
    crane = out["images"][0]
    assert crane["registry_state"] == "read" and crane["digest"] is None


# -- IMAGES.md in every bundle -------------------------------------------------------

@pytest.mark.parametrize("fmt", sorted(FORMAT_BASE))
def test_every_format_carries_images_md(fmt):
    files = gen.generate(FACTS, FORMAT_BASE[fmt])
    md = files[bundle_names.IMAGES_FILE]
    for ref in facts_mod.image_refs(FACTS):
        assert f"`{ref}`" in md
    # The images for no enabled functionality are not in it.
    assert "service-mock" not in md and "doduo" not in md
    assert "--verify <registry>" in md and "images --explain --lookup" in md
    assert "sha256:" not in md


@pytest.mark.parametrize("fmt", sorted(FORMAT_BASE))
def test_images_md_names_the_mirror_destinations(fmt):
    o = {**FORMAT_BASE[fmt], "private_registry": "reg.corp/bzm"}
    md = gen.generate(FACTS, o)[bundle_names.IMAGES_FILE]
    full = {**bundle_options.DEFAULT_OPTIONS, **o}
    for ref, target in image_registry.mirror_targets(FACTS, full):
        assert f"| `{ref}` | `{target}` |" in md
    assert "--verify reg.corp/bzm" in md


def test_images_md_warns_about_a_floating_tag():
    md = gen.generate(facts_mod.manual("h", "s"),
                      {"namespace": "n", "auth_token": "t"})[bundle_names.IMAGES_FILE]
    assert "floating tag" in md and "goes stale" in md


def test_images_md_carries_no_marker_for_a_blank_location():
    """Invariant 2: a sample in a customer's file is lower case."""
    md = gen.generate(facts_mod.manual("", ""),
                      {"namespace": "n", "auth_token": "t"})[bundle_names.IMAGES_FILE]
    assert not markers.MARKER_RE.findall(md)
    assert "--harbor-id <harbor-id>" in md


def test_images_md_is_customer_facing():
    md = gen.generate(FACTS, {"namespace": "n", "crane_hook": True,
                              "private_registry": "reg.corp/bzm"})[bundle_names.IMAGES_FILE]
    assert not re.search(r"#\d|\.py\b|fallback-catalogue|image_catalog", md)
    assert "cranehook" in md


def test_the_preview_lists_images_md_before_the_readme():
    order = gen.preview_order(gen.generate(FACTS, FORMAT_BASE["manifests"]))
    assert order.index(bundle_names.IMAGES_FILE) == order.index("README.md") - 1


# -- the command line ----------------------------------------------------------------

def _facts_file(tmp_path, facts=OLD_FACTS):
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(facts))
    return str(path)


def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    cli.main()


def test_explain_json_is_core_s_answer(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path), "--explain",
         "--format", "json")
    assert json.loads(capsys.readouterr().out) == core.image_catalog(
        OLD_FACTS, lookup=False)


def test_explain_csv_has_a_row_per_image(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path), "--explain",
         "--format", "csv", "--all")
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert len(rows) == len(core.image_catalog(OLD_FACTS, lookup=False,
                                               all_images=True)["images"])
    assert rows[0]["functionalities"].split(";")[0] == "performance"


@pytest.mark.parametrize("fmt", ["table", "md"])
def test_explain_prints_the_purpose_of_each_image(monkeypatch, tmp_path, capsys,
                                                  fmt):
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path), "--explain",
         "--format", fmt)
    out = capsys.readouterr().out
    assert image_catalog.CATALOG["blazemeter/v4"]["purpose"] in out
    assert f"{PUBLIC}/blazemeter/torero:latest" in out


def test_explain_with_no_location_is_the_catalogue(monkeypatch, capsys):
    monkeypatch.setattr(core, "client_from_key",
                        lambda *a, **k: pytest.fail("the catalogue needs no key"))
    _run(monkeypatch, "images", "--explain", "--format", "json")
    assert json.loads(capsys.readouterr().out)["source"] == "catalogue"


def test_explain_lookup_reports_an_unread_registry_and_exits_0(monkeypatch,
                                                              tmp_path, capsys):
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path), "--explain",
         "--lookup")
    captured = capsys.readouterr()
    assert "offline test" in captured.out
    assert "WARN" in captured.err


def test_verify_exits_1_when_an_image_is_missing(monkeypatch, tmp_path, capsys,
                                                 registry):
    registry(manifests={("bzm/crane", "3.8.0"): _manifest(1)})
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, "images", "--facts", _facts_file(tmp_path),
             "--verify", "reg.corp/bzm")
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "present  reg.corp/bzm/crane:3.8.0" in out
    assert "MISSING  reg.corp/bzm/blazemeter/v4:2.4.533-reduced" in out


def test_verify_warns_and_exits_0_when_the_registry_refuses(monkeypatch, tmp_path,
                                                            capsys, registry):
    """A denied read is a WARN and exits 0."""
    registry(status=403)
    _run(monkeypatch, "images", "--facts", _facts_file(tmp_path),
         "--verify", "reg.corp/bzm")
    captured = capsys.readouterr()
    assert "0 missing, 5 unread" in captured.out
    assert "WARN" in captured.err


def test_verify_reads_the_bundle_s_profile(monkeypatch, tmp_path, capsys, registry):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"output_format": "docker"}))
    registry()
    with pytest.raises(SystemExit):
        _run(monkeypatch, "images", "--facts", _facts_file(tmp_path),
             "--verify", "reg.corp/bzm", "--profile", str(profile))
    # A docker agent asks for `<registry>/blazemeter/<name>:latest`.
    assert "reg.corp/bzm/taurus-cloud:latest" in capsys.readouterr().out


def test_no_credential_is_a_flag(monkeypatch, capsys):
    """Credentials come from the environment or the docker config only."""
    with pytest.raises(SystemExit):
        _run(monkeypatch, "images", "--help")
    flags = re.findall(r"--[a-z-]+", capsys.readouterr().out)
    assert "--verify" in flags
    assert not [f for f in flags if re.search("pass|user|token|secret", f)]


# -- facts without an account: pinned to the newest release ---------------------

GCR = "verdant-bulwark-278"

# Tag lists shaped like BlazeMeter's registry: a floating `latest`, branch
# builds, CI build numbers on the mock images.
RELEASE_TAGS = {
    f"{GCR}/blazemeter/crane": ["3.7.44", "3.8.0", "3.8.1", "3.5.1-2", "1672",
                                "latest", "latest-master"],
    f"{GCR}/blazemeter/apm": ["1.7.110", "1.7.126", "MOB-46286-workload-data",
                              "1.7.115-mob52709-himanshu", "latest"],
    f"{GCR}/blazemeter/v4": ["1.24.169", "2.0.38", "2.4.538-reduced",
                             "2.4.538-jmeter",
                             "2.4.539-MOB-53689_playwright_fix-22-reduced",
                             "latest"],
    f"{GCR}/blazemeter/doduo": ["0.0.144", "0.0.145", "latest"],
    f"{GCR}/blazemeter/proxy-recorder": ["2.2.2", "1.11.63-BZTR-1689-2-16"],
    f"{GCR}/blazemeter/service-mock": ["6.0.30.4", "6.0.34.3", "6.0.35.2347",
                                       "6.0.34", "latest"],
    f"{GCR}/blazemeter/group-gateway": ["6.0.34.3", "6.0.35.2347"],
    f"{GCR}/blazemeter/mock-pc-service": ["6.0.34.3", "6.0.35.2347"],
}
PINNED = {"blazemeter/crane": "3.8.1", "blazemeter/apm": "1.7.126",
          "blazemeter/v4": "2.4.538-reduced", "blazemeter/doduo": "0.0.145",
          "blazemeter/proxy-recorder": "2.2.2",
          "blazemeter/service-mock": "6.0.34.3",
          "blazemeter/group-gateway": "6.0.34.3",
          "blazemeter/mock-pc-service": "6.0.34.3"}


@pytest.fixture
def releases(registry):
    """BlazeMeter's registry with RELEASE_TAGS, overridable per repo."""
    def install(**over):
        tags = {**RELEASE_TAGS, **over}
        return registry(tags={p: [t] for p, t in tags.items() if t is not None})
    return install


@pytest.mark.parametrize("path,tags,want", [
    ("blazemeter/crane", RELEASE_TAGS[f"{GCR}/blazemeter/crane"], "3.8.1"),
    ("blazemeter/v4", RELEASE_TAGS[f"{GCR}/blazemeter/v4"], "2.4.538-reduced"),
    ("blazemeter/service-mock", RELEASE_TAGS[f"{GCR}/blazemeter/service-mock"],
     "6.0.34.3"),
    ("blazemeter/apm", ["latest", "1.7.115-mob52709-himanshu"], None),
    ("blazemeter/torero", ["4.6.192", "latest"], None),
])
def test_a_release_is_the_repository_s_own_release_shape(path, tags, want):
    """Branch builds, CI build numbers and `latest` never count as a release,
    and a repository with no series here has none."""
    assert image_catalog.release_tag(path, tags) == want


def test_crane_asks_for_torero_and_richrach_by_latest_so_neither_is_pinned():
    assert "blazemeter/torero" not in image_catalog.RELEASE_SERIES
    assert "blazemeter/richrach" not in image_catalog.RELEASE_SERIES


def test_release_pins_tell_pinned_no_release_and_unread_apart(releases):
    """Invariant 1: three answers, three states."""
    releases(**{f"{GCR}/blazemeter/apm": ["latest"],
                f"{GCR}/blazemeter/doduo": None})
    pins = core.release_pins()
    by = {image_catalog.repo_path(r): p for r, p in pins["images"].items()}
    assert by["blazemeter/crane"] == {"state": "pinned", "tag": "3.8.1",
                                      "detail": None}
    assert by["blazemeter/apm"]["state"] == "no-release"
    assert by["blazemeter/doduo"]["state"] == "unread"
    assert by["blazemeter/doduo"]["detail"]
    assert pins["state"] == "partial" and "1 of 8" in pins["detail"]


def test_release_pins_never_wait_past_their_budget(monkeypatch):
    import time as time_mod

    def slow(method, url, *a, **kw):
        time_mod.sleep(0.5)
        raise registry_client.Unreachable("slow")
    monkeypatch.setattr(registry_client, "http_request", slow)
    started = time_mod.monotonic()
    pins = core.release_pins(budget_s=0.05)
    assert time_mod.monotonic() - started < 0.4
    assert pins["state"] == "unread"
    assert all("no answer within" in p["detail"] for p in pins["images"].values())


def test_manual_facts_pin_every_image_with_a_release(releases):
    releases()
    made = core.manual_facts("h", "s", func_ids=["performance", "mockServices"])
    f = made["facts"]
    assert f["crane_image"] == f"{PUBLIC}/blazemeter/crane:3.8.1"
    assert f["crane_source"] == "registry-newest"
    for i in f["images"]:
        path = image_catalog.repo_path(i["repo"])
        if path in PINNED:
            assert (i["tag"], i["source"]) == (PINNED[path], "registry-newest")
        else:
            assert (i["tag"], i["source"]) == ("latest", "catalogue"), path
    # The key crane resolves an override by is the catalogue's, unchanged.
    assert {i["key"] for i in f["images"]} == {
        i["key"] for i in facts_mod.FALLBACK_IMAGES}
    said = " ".join(made["warnings"])
    assert "not read from your location" in said
    assert "could not be read" not in said


def test_manual_facts_keep_the_old_tags_and_say_so_when_the_registry_is_down():
    made = core.manual_facts("h", "s")
    f = made["facts"]
    assert f["crane_image"].endswith(":latest") and f["crane_source"] == "catalogue"
    assert {i["source"] for i in f["images"]} == {"catalogue"}
    assert f["release_pins"]["state"] == "unread"
    assert {p["state"] for p in f["release_pins"]["images"].values()} == {"unread"}
    said = " ".join(made["warnings"])
    assert "could not be read" in said and "far older than the newest" in said


def test_pin_warnings_are_plain_prose(releases):
    """Shown in Markdown and in a terminal alike."""
    releases(**{f"{GCR}/blazemeter/doduo": None})
    for w in core.manual_facts("h", "s")["warnings"]:
        assert not re.search(r"`|--|->|\*", w), w


def test_gathered_facts_record_that_nothing_was_pinned():
    f = facts_mod.gather(_Account(VERSIONS_PERFORMANCE), "H1")
    assert f["release_pins"]["state"] == "not-asked"
    assert not [w for w in core.facts_warnings(f) if "BlazeMeter's registry" in w]


def test_generating_from_pinned_facts_asks_no_registry(releases, monkeypatch):
    """The pins live in the facts; generate never reaches the network."""
    releases()
    f = core.manual_facts("h", "s")["facts"]
    monkeypatch.setattr(registry_client, "http_request",
                        lambda *a, **k: pytest.fail("generate read a registry"))
    files = gen.generate(f, {"namespace": "n", "auth_token": "t"})
    assert "blazemeter/crane:3.8.1" in files["bzm_deployment.yaml"]
    md = files[bundle_names.IMAGES_FILE]
    assert "not read from your location" in md
    assert "is not the newest release" not in md


def test_images_md_says_latest_is_old_when_nothing_was_pinned():
    md = gen.generate(facts_mod.manual("h", "s"),
                      {"namespace": "n", "auth_token": "t"})[bundle_names.IMAGES_FILE]
    assert "is not the newest release" in md
    assert "`blazemeter/v4`" in md and "`blazemeter/torero`" not in md.split(
        "is not the newest release")[1].split("##")[0]


def test_images_md_from_a_location_s_own_list_carries_neither_note():
    md = gen.generate(FACTS, FORMAT_BASE["manifests"])[bundle_names.IMAGES_FILE]
    assert "not read from your location" not in md
    assert "is not the newest release" not in md


def test_the_catalogue_view_pins_only_when_it_asks_the_registry(releases):
    fake = releases()
    rows = core.image_catalog(None, lookup=False)["images"]
    assert {r["source"] for r in rows} == {"catalogue"}
    assert not fake.calls
    rows = {image_catalog.repo_path(r["repo"]): r
            for r in core.image_catalog(None, lookup=True)["images"]}
    assert rows["blazemeter/crane"]["tag"] == "3.8.1"
    assert rows["blazemeter/crane"]["source"] == "registry-newest"
    assert rows["blazemeter/torero"]["source"] == "catalogue"
