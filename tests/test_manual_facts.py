"""Facts built from the three values BlazeMeter shows, with no account access.

The case: producing manifests for a customer whose BlazeMeter account and
cluster you cannot reach. What matters is that the resulting facts are the same
*shape* gather() returns, so nothing downstream learns which way they arrived --
and that the image catalogue actually covers the functionalities a location can
be
told it has, because a missing key is the silent failure (crane resolves it
against the public registry).
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import api  # noqa: E402
from bzm_opl_gen import facts as facts_mod  # noqa: E402
from bzm_opl_gen import generate as gen  # noqa: E402

from test_generate import FACTS  # noqa: E402
from versions_fixtures import (VERSIONS_GUI, VERSIONS_PERFORMANCE,  # noqa: E402
                               VERSIONS_SV)

H, S = "0a1b2c3d4e5f60718293a4b5", "6c5b4a39281706f5e4d3c2b1"


def _cm(files):
    return yaml.safe_load(files["bzm_configmap.yaml"])["data"]


def _gen(f, **opts):
    return gen.generate(f, {"platform": "k8s", "namespace": "cust",
                            "auth_token": "TOK", "ship_id": f["ships"][0]["id"],
                            **opts})


# -- shape --------------------------------------------------------------------

class _OneLocationClient:
    """Just enough of BzmClient for gather(): the location, and no agent that
    has ever reported an inventory."""

    def __init__(self, harbor):
        self._h = harbor

    def private_location(self, harbor_id):
        return self._h


def test_manual_facts_match_the_shape_gather_returns():
    """Same keys, so every consumer downstream is indifferent to the source.

    Compared against what gather() *actually returns*, not against a stored
    fixture plus a list of keys the fixture predates: that list had to grow
    every time a location field was read, which is the drift this test exists
    to catch doing the catching itself.
    """
    gathered = facts_mod.gather(
        _OneLocationClient({"id": H, "name": "L", "funcIds": ["performance"],
                            "ships": []}), H)
    assert set(facts_mod.manual(H, S)) == set(gathered)


def test_how_the_facts_arrived_is_readable_from_the_marker_they_already_carry():
    """Doctor has to tell "there was no account to ask" from "the account said
    no slots", and the facts already record which -- `images_source`. One
    predicate over it, rather than a second field (which would be a second
    shape) or a source test spelled out at each call site."""
    assert facts_mod.from_manual_entry(facts_mod.manual(H, S))
    assert not facts_mod.from_manual_entry(
        facts_mod.gather(_FakeClient([_ship([])]), "H1"))     # catalogue fallback
    assert not facts_mod.from_manual_entry(FACTS)             # a fixture, no marker


def test_the_three_values_reach_the_manifests():
    files = _gen(facts_mod.manual(H, S))
    cm = _cm(files)
    assert cm["HARBOR_ID"] == H
    assert cm["SHIP_ID"] == S
    assert yaml.safe_load(files["bzm_secret.yaml"])["stringData"]["AUTH_TOKEN"] == "TOK"


def test_a_full_bundle_generates_from_nothing_else():
    """The point of the feature: three values in, deployable manifests out."""
    files = _gen(facts_mod.manual(H, S))
    for name in ("bzm_serviceaccount.yaml", "bzm_configmap.yaml", "bzm_secret.yaml",
                 "bzm_role.yaml", "bzm_rolebinding.yaml", "bzm_deployment.yaml"):
        assert name in files, name
    for name, content in files.items():
        if name.endswith(".yaml"):
            assert yaml.safe_load(content)["kind"]


def test_it_also_generates_a_chart():
    files = _gen(facts_mod.manual(H, S), output_format="helm")
    assert gen.HELM_VALUES_FILE in files
    v = yaml.safe_load(files[gen.HELM_VALUES_FILE])
    assert v["harborId"] == H and v["shipId"] == S and v["authToken"] == "TOK"


def test_crane_image_floats_because_nothing_can_pin_it():
    """gather() pins the tag the account advertises. Manually there is no
    account, so it is `latest` -- which is also what a location with no live
    agent reports."""
    assert facts_mod.manual(H, S)["crane_image"].endswith(":latest")


def test_no_validation_of_the_ids():
    """Deliberate: there is nothing here to check them against, and a format
    guess would reject input that is correct."""
    m = facts_mod.manual("  not-an-id  ", "also/not/one")
    assert m["harbor_id"] == "  not-an-id  "
    assert m["ships"][0]["id"] == "also/not/one"


# -- the image catalogue ------------------------------------------------------

# The performance set is four, not two: torero and richrach were missing until a
# live Kubernetes agent was read properly. Neither is pulled by an ordinary run,
# but crane lists both for a performance-only location.
PERF_KEYS = {"taurus-cloud:latest", "apm-image:latest",
             "torero:latest", "richrach:latest"}


@pytest.mark.parametrize("func_ids,expect_keys", [
    (["performance"], PERF_KEYS),
    (["functionalApi"], PERF_KEYS),
    (["mockServices"], {"blazemeter/service-mock:latest",
                        "blazemeter/group-gateway:latest",
                        "blazemeter/mock-pc-service:latest"}),
    (["proxyRecorder"], {"blazemeter/proxy-recorder:latest"}),
    (["functionalGui"], PERF_KEYS | {"blazemeter/doduo:latest"}),
])
def test_every_selectable_functionality_names_its_images(func_ids, expect_keys):
    """A functionality whose category the catalogue does not cover produces an
    empty
    or partial IMAGE_OVERRIDES, and crane then resolves the missing keys against
    the public registry without logging anything -- which looks fine until the
    cluster is actually sealed."""
    sv = ({"sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
           "sv_tls_secret": "wc"} if "mockServices" in func_ids else {})
    f = facts_mod.manual(H, S, func_ids=func_ids)
    cm = _cm(_gen(f, private_registry="reg.io/bzm", **sv))
    assert set(yaml.safe_load(cm["IMAGE_OVERRIDES"])) == expect_keys


def test_catalogue_covers_every_category_the_funcid_vocabulary_can_ask_for():
    """Guards the pairing directly: adding a funcId that needs a new category
    without adding images for it should fail here, not on a sealed cluster."""
    have = {i["category"] for i in facts_mod.FALLBACK_IMAGES}
    want = set()
    for cats in facts_mod.CATEGORY_BY_FUNC.values():
        want |= cats
    assert want <= have, f"no fallback images for {want - have}"


def test_alias_funcids_are_not_offered_where_they_change_nothing():
    """functionalApi and performance are both "the taurus engine", so the manual
    form offers one of them -- a choice that cannot change the output is noise.
    Creating a location is a different question and keeps the full vocabulary."""
    offered = facts_mod.image_distinct_funcs()
    assert "performance" in offered
    assert "functionalApi" not in offered
    # ...and it is genuinely an alias, not merely hidden.
    assert (facts_mod.needed_categories(["functionalApi"])
            == facts_mod.needed_categories(["performance"]))
    # The ones that do change the images all survive.
    for f in ("mockServices", "proxyRecorder", "functionalGui"):
        assert f in offered, f


def test_image_distinct_funcs_keeps_one_per_category_set():
    """Derived, not a hand-kept exclusion list: a funcId added with a new
    category set is offered automatically, and one added as an alias is not."""
    seen = set()
    for f in facts_mod.image_distinct_funcs():
        cats = frozenset(facts_mod.CATEGORY_BY_FUNC[f])
        assert cats not in seen, f"{f} duplicates a category set already offered"
        seen.add(cats)
    assert seen == {frozenset(c) for c in facts_mod.CATEGORY_BY_FUNC.values()}


def test_dropping_the_alias_does_not_change_what_generates():
    """The reason it is safe to hide: picking performance produces exactly what
    picking functionalApi would have."""
    a = _gen(facts_mod.manual(H, S, func_ids=["performance"]),
             private_registry="reg.io/bzm")
    b = _gen(facts_mod.manual(H, S, func_ids=["functionalApi"]),
             private_registry="reg.io/bzm")
    assert _cm(a)["IMAGE_OVERRIDES"] == _cm(b)["IMAGE_OVERRIDES"]


def test_gui_browser_images_are_the_gap_where_nothing_named_one():
    """The catalogue carries no browser image and cannot: the account holds 60+
    version-pinned repos. So a bundle built from it alone runs browser tests
    with no browser image, whatever else it got right.

    Read off the images rather than off where they came from. Provenance was a
    proxy for the question -- it said "complete" of any facts with an
    inventory, including one that happened to carry no browser -- and now it
    would say it of an image list too, which is the one source that can close
    the gap and can also fail to.
    """
    assert facts_mod.gui_images_incomplete(
        facts_mod.manual(H, S, func_ids=["functionalGui"]))
    assert not facts_mod.gui_images_incomplete(
        facts_mod.manual(H, S, func_ids=["performance"]))
    # A live inventory with no browser in it is the same gap, not a closed one.
    assert facts_mod.gui_images_incomplete(
        dict(FACTS, func_ids=["functionalGui"], images_source="live agent inventory"))


def test_the_image_list_closes_the_browser_gap():
    """The gap existed because only a live agent was thought to say which
    version-pinned browser a location runs. The account says it, off an agent
    that has never started -- so this is a fact about the location now, not a
    caveat carried beside the bundle."""
    f = facts_mod.gather(
        _FakeClient([{"id": "S1", "state": "empty"}], VERSIONS_GUI,
                    func_ids=["functionalGui", "chrome:default"]), "H1")

    assert not facts_mod.gui_images_incomplete(f)
    by_key = {i["key"]: i for i in f["images"]}
    assert (by_key["blazemeter/charmander/chrome_136.0.7103.113:2.10.45"]["repo"]
            == "gcr.io/verdant-bulwark-278/blazemeter/charmander/"
               "chrome_136.0.7103.113")
    assert (by_key["blazemeter/charmander/chrome_136.0.7103.113:2.10.45"]
            ["category"] == "gui")


def test_a_gui_bundle_names_the_browser_the_location_pins():
    """What it is all for: IMAGE_OVERRIDES carries the exact build, so a sealed
    cluster's mirror has the key crane asks for."""
    f = facts_mod.gather(
        _FakeClient([{"id": "S1", "state": "empty"}], VERSIONS_GUI,
                    func_ids=["functionalGui", "chrome:default"]), "H1")
    overrides = yaml.safe_load(
        _cm(_gen(f, private_registry="reg.io/bzm"))["IMAGE_OVERRIDES"])

    assert (overrides["blazemeter/charmander/chrome_136.0.7103.113:2.10.45"]
            == "reg.io/bzm/blazemeter/charmander/chrome_136.0.7103.113:2.10.45")
    assert (overrides["blazemeter/doduo:0.0.144"]
            == "reg.io/bzm/blazemeter/doduo:0.0.144")
    assert (overrides["taurus-cloud:2.4.454-reduced"]
            == "reg.io/bzm/blazemeter/v4:2.4.454-reduced")


def test_fallback_catalogue_repos_are_all_under_the_blazemeter_project():
    """These were read off live inventories rather than derived from the keys --
    taurus-cloud is `v4` and apm-image is `apm`, so a repo guessed from its key
    is one that does not exist."""
    for i in facts_mod.FALLBACK_IMAGES:
        assert i["repo"].startswith("gcr.io/verdant-bulwark-278/blazemeter/"), i
        assert i["key"] and i["tag"] and i["category"]


# -- interaction with the rest of the generator -------------------------------

def test_service_virtualization_still_refuses_without_an_ingress():
    """Manual entry does not become a way around the validations -- the failure
    it prevents is on the cluster, not in the account."""
    f = facts_mod.manual(H, S, func_ids=["mockServices"])
    with pytest.raises(ValueError, match="WAITING_FOR_DOMAIN"):
        _gen(f)


def test_helm_format_still_refuses_a_mock_location():
    f = facts_mod.manual(H, S, func_ids=["mockServices"])
    with pytest.raises(ValueError, match="performance testing only"):
        _gen(f, output_format="helm", sv_ingress="nginx",
             sv_subdomain="apps.example.com", sv_tls_secret="wc")


# -- reading a real agent's inventory -----------------------------------------

def _ship(images):
    return {"id": "S1", "name": "a", "state": "idle", "installedVersion": "3.7.55",
            "lastHeartBeat": 0,
            "hostInfo": {"containerManager": {"info": {"images": images}}}}


class _FakeClient:
    """Just enough BzmClient for gather(): a location, its agents' reported
    inventories, and the image list served per agent.

    `versions` is what `GET .../versions` does -- a recorded payload, or an
    exception to raise. It defaults to raising, because most of these tests are
    about the inventory and an endpoint that answers would decide their result
    instead; a test about the image list says so by passing one.
    """

    def __init__(self, ships, versions=None, func_ids=("performance",)):
        self._ships = ships
        self._versions = versions if versions is not None else api.BzmApiError(
            "GET /private-locations/H1/ships/S1/versions -> HTTP 404: not found",
            status=404)
        self._func_ids = list(func_ids)
        self.versions_calls = []

    def private_location(self, harbor_id):
        return {"id": harbor_id, "name": "L", "funcIds": self._func_ids,
                "slots": 1, "threadsPerEngine": 500, "ships": self._ships}

    def ship_versions(self, harbor_id, ship_id):
        self.versions_calls.append((harbor_id, ship_id))
        if isinstance(self._versions, Exception):
            raise self._versions
        return self._versions


def test_kubernetes_agent_inventory_is_read():
    """A Kubernetes agent reports bare keys with no registry and Size 0 --
    `taurus-cloud:latest`, not `gcr.io/.../v4:2.4.444`. Only the Docker shape
    used to be handled, so every k8s agent produced no inventory at all and
    fell through to the catalogue: `images_source` could never say otherwise
    for the very agents this tool generates."""
    f = facts_mod.gather(_FakeClient([_ship([
        {"RepoTags": ["taurus-cloud:latest"], "Size": 0},
        {"RepoTags": ["torero:4.6.182"], "Size": 0},
        {"RepoTags": ["blazemeter/crane:3.7.55"], "Size": 0},
    ])]), "H1")
    assert "live agent inventory" in f["images_source"]
    by_key = {i["key"]: i for i in f["images"]}
    # The key names the image; the repo has to be looked up, and does not match.
    assert by_key["taurus-cloud:latest"]["repo"].endswith("/v4")
    # An exact version, where the catalogue could only have said `latest`.
    assert by_key["torero:4.6.182"]["tag"] == "4.6.182"
    # crane is pulled out of the list and pinned, not left floating.
    assert f["crane_image"].endswith("/crane:3.7.55")
    assert "crane" not in by_key


def test_docker_agent_inventory_still_read():
    """The Docker shape is registry-qualified and carries the key alongside."""
    f = facts_mod.gather(_FakeClient([_ship([
        {"RepoTags": ["gcr.io/verdant-bulwark-278/blazemeter/v4:2.4.444",
                      "taurus-cloud:latest"], "Size": 7_900_000_000},
    ])]), "H1")
    assert "live agent inventory" in f["images_source"]
    img = f["images"][0]
    assert img["key"] == "taurus-cloud:latest"
    assert img["tag"] == "2.4.444" and img["size_mb"] == 7900


def test_crane_is_pinned_from_a_reference_carrying_no_key():
    """A Docker agent reports crane as `blazemeter/crane:3.7.56` beside its
    registry-qualified reference and *no* `:latest` tag -- read off a live one.
    There is no key there to override crane by, and there does not need to be:
    it is the image the Deployment runs, so the version is still the answer."""
    f = facts_mod.gather(_FakeClient([_ship([
        {"RepoTags": ["blazemeter/crane:3.7.56",
                      "gcr.io/verdant-bulwark-278/blazemeter/crane:3.7.56"],
         "Size": 0}])]), "H1")
    assert f["crane_image"].endswith("/crane:3.7.56")
    assert all(i["key"] for i in f["images"])


def test_no_inventory_still_falls_back():
    f = facts_mod.gather(_FakeClient([_ship([])]), "H1")
    assert f["images_source"].startswith("fallback-catalogue")
    assert len(f["images"]) == len(facts_mod.FALLBACK_IMAGES)


# -- the location's own image list --------------------------------------------
#
# GET /private-locations/{h}/ships/{s}/versions. It answers for an agent that
# has never been online, so it reaches the case the inventory never could: the
# bundle is generated before anything is deployed, which is every first
# install.

def test_the_image_list_answers_for_an_agent_that_has_never_run():
    """The recording is from an agent in state `empty`, with no hostInfo at all
    -- so there is no inventory here, and the images are still exact."""
    f = facts_mod.gather(
        _FakeClient([{"id": "S1", "state": "empty"}], VERSIONS_PERFORMANCE), "H1")

    by_key = {i["key"]: i for i in f["images"]}
    assert by_key["taurus-cloud:2.4.454-reduced"]["repo"].endswith("/v4")
    assert by_key["apm-image:1.7.112"]["tag"] == "1.7.112"
    # crane comes out of the same list, pinned to what the account advertises
    # rather than floating on :latest as an agentless location used to.
    assert f["crane_image"].endswith("/crane:3.7.56")
    assert "location image list" in f["images_source"]


def test_the_image_list_outranks_a_live_inventory():
    """Two live sources, and they can disagree: the inventory is what the agent
    pulled, the image list is what the location is configured to run now. The
    bundle is being generated for the next start, so the configuration wins."""
    f = facts_mod.gather(_FakeClient(
        [_ship([{"RepoTags": ["taurus-cloud:2.4.444-reduced"], "Size": 0},
                {"RepoTags": ["blazemeter/crane:3.7.55"], "Size": 0}])],
        VERSIONS_PERFORMANCE), "H1")

    keys = {i["key"] for i in f["images"]}
    assert "taurus-cloud:2.4.454-reduced" in keys
    assert "taurus-cloud:2.4.444-reduced" not in keys
    assert f["crane_image"].endswith("/crane:3.7.56")


def test_a_key_no_image_list_names_is_still_carried():
    """The image list is the location's resources, not everything crane pulls.

    A live Kubernetes agent reports `torero` and `richrach` beside them and no
    /versions response names either, so the two earlier sources are not
    replaced -- they fill keys the image list is silent about. Dropping them
    would put the ImagePullBackOff the catalogue exists to prevent back into
    every bundle generated before an agent has ever started.
    """
    f = facts_mod.gather(_FakeClient(
        [_ship([{"RepoTags": ["richrach:1.0.81"], "Size": 0}])],
        VERSIONS_PERFORMANCE), "H1")

    by_key = {i["key"]: i for i in f["images"]}
    assert by_key["richrach:1.0.81"]["tag"] == "1.0.81"     # the inventory's
    assert "torero:latest" in by_key                        # the catalogue's
    assert f["images_source"] == ("location image list + live agent inventory "
                                  "+ fallback-catalogue")


def test_the_image_list_is_asked_once_per_location():
    """Every agent in a location answered identically, so the first that answers
    ends it -- the list is a property of the location's funcIds."""
    c = _FakeClient([{"id": "S1", "state": "empty"}, {"id": "S2", "state": "idle"}],
                    VERSIONS_PERFORMANCE)
    facts_mod.gather(c, "H1")
    assert c.versions_calls == [("H1", "S1")]


def test_a_refusal_that_answers_for_the_location_is_asked_once():
    """A 403 is about the token and this location, not about the agent it was
    asked through, so the remaining agents are not asked.

    The loop used to re-issue it per agent: one real account holds 221 agents
    and 17-agent locations are ordinary, so a dead key or a location this key
    may not read cost a sequential round trip each before the same `unread`
    came back."""
    c = _FakeClient([{"id": f"S{i}", "state": "idle"} for i in range(5)],
                    api.BzmApiError("GET /private-locations/H1/ships/S0/"
                                    "versions -> HTTP 403: forbidden",
                                    status=403))
    f = facts_mod.gather(c, "H1")
    assert c.versions_calls == [("H1", "S0")]
    # ...and it is still a denied read rather than an empty location.
    assert facts_mod.image_list_state(f) == facts_mod.IMAGE_LIST_UNREAD
    assert "403" in f["image_list"]["detail"]


def test_a_refusal_that_could_be_this_agents_is_worth_the_next():
    """A 5xx is not an answer about the location, so the next agent is asked.
    Keeping the retry for these is why the rule is on the status rather than on
    "a refusal ends it"."""
    c = _FakeClient([{"id": f"S{i}", "state": "idle"} for i in range(3)],
                    api.BzmApiError("GET ... -> HTTP 502: bad gateway",
                                    status=502))
    facts_mod.gather(c, "H1")
    assert len(c.versions_calls) == 3


def test_a_service_virtualization_location_carries_no_engine():
    """Read off a real mockServices-only location: crane, the gateway and the
    mock, and no `v4` or `apm` anywhere."""
    f = facts_mod.gather(
        _FakeClient([{"id": "S1", "state": "empty"}], VERSIONS_SV,
                    func_ids=["mockServices"]), "H1")

    selected = {i["key"] for i in facts_mod.select_images(f)}
    assert selected == {"blazemeter/service-mock:6.0.30.4",
                        "blazemeter/group-gateway:6.0.30.4",
                        # the catalogue's, which no /versions response names
                        "blazemeter/mock-pc-service:latest"}


# -- "could not read" is not "there is nothing there" -------------------------
#
# Four answers, and they have to stay four. `images` alone cannot carry them:
# a refused read, a location with no agent to ask, an answer with nothing in it
# and a set of facts nobody ever asked for all leave the same fallback images
# behind. The state is what tells them apart.

def test_an_image_list_that_was_read_says_how_much_it_held():
    f = facts_mod.gather(
        _FakeClient([{"id": "S1", "state": "empty"}], VERSIONS_PERFORMANCE), "H1")
    assert facts_mod.image_list_state(f) == facts_mod.IMAGE_LIST_READ
    assert f["image_list"]["count"] == 3


def test_an_empty_image_list_is_read_and_empty():
    """The account answered and named nothing. That is a fact about the
    location, and the bundle falls back to the catalogue knowing it."""
    f = facts_mod.gather(
        _FakeClient([{"id": "S1", "state": "empty"}], {"resources": {}}), "H1")
    assert facts_mod.image_list_state(f) == facts_mod.IMAGE_LIST_READ
    assert f["image_list"]["count"] == 0
    assert "location image list" not in f["images_source"]


def test_a_refused_image_list_is_unread_and_says_so():
    """A 403 is not an empty location. The images are the catalogue's either
    way, so nothing downstream could tell them apart from the list itself."""
    f = facts_mod.gather(_FakeClient(
        [{"id": "S1", "state": "empty"}],
        api.BzmApiError("GET /private-locations/H1/ships/S1/versions -> "
                        "HTTP 403: forbidden")), "H1")

    assert facts_mod.image_list_state(f) == facts_mod.IMAGE_LIST_UNREAD
    # Never a count: a number here would be the empty answer's.
    assert f["image_list"]["count"] is None
    assert "403" in f["image_list"]["detail"]
    # ...and the refusal does not fail the gather. The location was read fine.
    assert f["harbor_id"] == "H1" and f["images"]


def test_a_location_with_no_agent_was_never_in_a_position_to_be_asked():
    """The route is per agent, so there is no request to refuse. Not a denied
    read and not an empty one."""
    f = facts_mod.gather(_FakeClient([]), "H1")
    assert facts_mod.image_list_state(f) == facts_mod.IMAGE_LIST_NO_AGENT
    assert f["image_list"]["count"] is None


def test_manually_entered_facts_never_asked_at_all():
    """No account, so no read to be refused and no location to be empty."""
    assert (facts_mod.image_list_state(facts_mod.manual(H, S))
            == facts_mod.IMAGE_LIST_NOT_ASKED)


def test_the_four_answers_are_four_distinct_values():
    """Stated over the constants so that collapsing two of them -- the bug this
    whole rule is about -- fails here rather than at a call site that reads one
    and means the other."""
    states = {facts_mod.IMAGE_LIST_READ, facts_mod.IMAGE_LIST_UNREAD,
              facts_mod.IMAGE_LIST_NO_AGENT, facts_mod.IMAGE_LIST_NOT_ASKED}
    assert len(states) == 4


def test_facts_that_predate_the_field_never_claim_a_read():
    """An older facts.json, or the checked-in example: nothing asked, and the
    absent field must not read as an answer."""
    assert (facts_mod.image_list_state({"func_ids": ["performance"]})
            == facts_mod.IMAGE_LIST_NOT_ASKED)


def test_key_to_repo_covers_the_irregular_names():
    """Four keys do not name their own repo and cannot be derived. The rest
    follow the regular rule, which is what lets an unknown key still resolve."""
    r = facts_mod.repo_for_key
    assert r("taurus-cloud:latest").endswith("/v4")
    assert r("apm-image:latest").endswith("/apm")
    assert r("blazemeter:latest").endswith("/v3")
    assert r("secrets-image:latest").endswith("/secrets")
    # regular, including one nobody has added to the catalogue yet
    assert r("torero:4.6.182").endswith("/torero")
    assert r("blazemeter/service-mock:latest").endswith("/service-mock")
    assert r("not-invented-yet:9").endswith("/not-invented-yet")


# What a live functionalGui agent reported (crane 3.7.55), against the repos
# those keys were confirmed to be pullable from. The catalogue carries no
# browser images and cannot -- the account holds 60+ version-pinned repos and
# only an agent says which one a location uses -- so this pairing is the only
# place their real shape is written down.
BROWSER_IMAGES = [
    ("blazemeter/charmander/chrome_136.0.7103.113:2.10.45",
     "gcr.io/verdant-bulwark-278/blazemeter/charmander/chrome_136.0.7103.113"),
    ("blazemeter/charmander/firefox_139.0.4:2.10.45",
     "gcr.io/verdant-bulwark-278/blazemeter/charmander/firefox_139.0.4"),
    ("blazemeter/charmander/microsoftedge_137.0.3296.83:2.10.45",
     "gcr.io/verdant-bulwark-278/blazemeter/charmander/microsoftedge_137.0.3296.83"),
    ("blazemeter/charmander/safari_15.0:2.10.45",
     "gcr.io/verdant-bulwark-278/blazemeter/charmander/safari_15.0"),
]
BROWSER_KEYS = [k for k, _ in BROWSER_IMAGES]


@pytest.mark.parametrize("key,repo", BROWSER_IMAGES)
def test_browser_keys_resolve_to_the_repo_that_serves_them(key, repo):
    """Both sides are written out rather than derived: deriving the expected
    repo would apply the rule under test to produce the answer it is checked
    against. Each of these four was pulled from the registry by hand. The
    category rides along because it is read off the repo string, so the two
    fail together -- see repo_for_key for what that cost."""
    assert facts_mod.repo_for_key(key) == repo
    assert facts_mod.image_category(repo) == "gui"


@pytest.mark.parametrize("ref,repo", [
    ("reg.corp.com/bzm/v4:2.4.444", "reg.corp.com/bzm/v4"),
    # A port is not a tag: splitting on the first colon leaves `localhost`, and
    # the rig's own mirror is addressed exactly this way.
    ("host.minikube.internal:5001/v4:2.4.444", "host.minikube.internal:5001/v4"),
    ("localhost:5001/v4", "localhost:5001/v4"),
])
def test_a_key_that_already_names_its_registry_is_left_alone(ref, repo):
    """A Docker agent pulling from a private mirror reports a full reference,
    not a crane key. Prefixing the project onto it, or reducing it to its last
    segment, both claim the image lives somewhere it does not."""
    assert facts_mod.repo_for_key(ref) == repo


def test_a_gui_agents_browser_inventory_survives_gather():
    """End to end on the shape that produced the defect: the entry crane is
    handed has to carry the repo and the category, not just resolve them."""
    f = facts_mod.gather(_FakeClient([_ship(
        [{"RepoTags": [k], "Size": 0} for k in BROWSER_KEYS]
        + [{"RepoTags": ["taurus-cloud:2.4.444-reduced"], "Size": 0}])]), "H1")
    by_key = {i["key"]: i for i in f["images"]}
    for key, repo in BROWSER_IMAGES:
        assert by_key[key]["repo"] == repo
        assert by_key[key]["category"] == "gui"
    assert by_key["taurus-cloud:2.4.444-reduced"]["category"] == "performance"


def test_a_performance_location_does_not_select_browser_images():
    """The category is what keeps four browsers out of a bundle that has no
    browser to run them in."""
    f = {"func_ids": ["performance"],
         "images": [{"key": k, "repo": r} for k, r in BROWSER_IMAGES]
                   + [{"key": "taurus-cloud:latest",
                       "repo": f"{facts_mod.BLAZEMETER_PROJECT}/v4"}]}
    assert [i["key"] for i in facts_mod.select_images(f)] == ["taurus-cloud:latest"]
