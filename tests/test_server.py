import io
import os
import pathlib
import re
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bzm_opl_gen import core, server  # noqa: E402
from test_generate import FACTS  # noqa: E402
# The same fakes tests/test_core.py and tests/test_cli.py drive, for the same
# reason they share them: three surfaces call the same core functions, and a
# fake per suite is how two of them end up disagreeing about what an account
# answers. `calls` is what makes "nothing was minted" assertable at all.
from test_core import FakeClient, RefusingClient  # noqa: E402

client = TestClient(server.app)


def test_generate_preview_no_key_needed():
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert names[0] == "bzm_serviceaccount.yaml"      # apply order
    assert "bzm_deployment.yaml" in names and "README.md" in names


def test_generate_zip_mirror_script_executable():
    r = client.post("/api/generate/zip", json={
        "facts": FACTS,
        "options": {"namespace": "ns1", "private_registry": "reg.local/bzm"}})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    info = z.getinfo("bzm-opl/bzm-opl-image-mirror.sh")
    assert info.external_attr >> 16 & 0o111          # executable bits
    assert "bzm-opl/bzm_configmap.yaml" in z.namelist()


def test_generate_preview_helm_format():
    """The preview leads with the values overlay -- the only file in a chart
    bundle that came from the account, and so the one worth reading first."""
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1", "output_format": "helm"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert names[0] == "bzm-opl-values.yaml"
    assert "helm/templates/deployment.yaml" in names
    assert "bzm_deployment.yaml" not in names


def test_generate_zip_helm_keeps_the_chart_directory():
    """Names carry directories in this format, and a zip that flattened them
    would download as a pile of files no helm command can install."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1", "output_format": "helm"}})
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "bzm-opl/helm/Chart.yaml" in names
    assert "bzm-opl/helm/templates/deployment.yaml" in names
    assert "bzm-opl/bzm-opl-values.yaml" in names


def test_generate_helm_rejects_service_virtualization_400():
    """The UI disables the segment for an SV location; this is the other half --
    an imported profile can arrive set to helm."""
    facts = dict(FACTS, func_ids=["mockServices"])
    r = client.post("/api/generate", json={
        "facts": facts,
        "options": {"namespace": "ns1", "output_format": "helm",
                    "sv_ingress": "nginx", "sv_subdomain": "apps.example.com",
                    "sv_tls_secret": "wildcard"}})
    assert r.status_code == 400
    assert "performance testing only" in r.json()["detail"]


def test_manual_facts_need_no_api_key():
    """The whole point: this mode exists for an account nobody here can reach,
    so requiring a key would defeat it. Every other /api route 401s."""
    r = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["performance"]})
    assert r.status_code == 200
    f = r.json()["facts"]
    assert f["harbor_id"] == "H1"
    assert f["ships"][0]["id"] == "S1"
    assert f["images_source"] == "manual entry (no account access)"
    assert r.json()["gui_images_incomplete"] is False


def test_manual_facts_flag_the_gui_image_gap():
    r = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["functionalGui"]})
    assert r.json()["gui_images_incomplete"] is True


def test_manual_facts_generate_without_a_token_fetch():
    """The typed token is the bundle's, and a key left over from an earlier
    connect must not be asked for one belonging to somebody else's agent. Free
    now that no route mints unasked, and asserted anyway: this mode is where a
    stray mint would be least visible, since there is no agent on screen."""
    facts = client.post("/api/facts/manual", json={
        "harbor_id": "H1", "ship_id": "S1", "func_ids": ["performance"]}).json()["facts"]
    r = client.post("/api/generate", json={
        "facts": facts,
        "options": {"namespace": "cust", "auth_token": "TOK", "ship_id": "S1"}})
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert "bzm_deployment.yaml" in names and "bzm_secret.yaml" in names
    assert r.json()["token"]["branch"] == core.TOKEN_GIVEN


# -- what a download does to a running agent's credential ----------------------
# The UI half of #64. Which of the four branches a token arrives by is core's
# rule and is tested in tests/test_core.py; what these pin is that no route here
# takes the one that mints unless it was asked to, and that the answer says which
# one it took. The download button was the last caller that rotated a live
# agent's credential as a side effect of being asked for a zip -- silently, and
# the pod it broke reads as a slow boot.

@pytest.fixture
def connected(monkeypatch):
    """A browser session holding an API key, counting what it was asked for.

    Holding one is the whole hazard: every assertion below is about a route that
    *could* mint and must not, so a fixture with no client would pass for the
    wrong reason.
    """
    c = FakeClient()
    monkeypatch.setitem(server._state, "client", c)
    return c


@pytest.mark.parametrize("route", ["/api/generate", "/api/generate/zip",
                                   "/api/generate/save"])
def test_no_route_mints_unless_it_was_asked_to(connected, route, tmp_path):
    """All three, parametrised: the preview was already safe, and the two that
    hand a bundle over were not. A rule that holds on one of them is the bug."""
    r = client.post(route, json={"facts": FACTS, "out_dir": str(tmp_path / "b"),
                                 "options": {"namespace": "ns1"}})
    assert r.status_code == 200
    assert connected.calls == []


def test_the_preview_can_say_a_save_would_reuse_the_folder_s_token(
        connected, tmp_path):
    """The preview took no out_dir, so its branch could never be `reused` -- and
    the page reported "placeholder, fill it in before applying" over a folder
    whose own token a save was about to keep. Misleading in the one direction
    that matters: it invites a rotation nothing needed."""
    out = str(tmp_path / "bundle")
    ship = FACTS["ships"][0]["id"]
    client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": ship,
                    "auth_token": "ALREADY-THERE"}})
    r = client.post("/api/generate", json={
        "facts": FACTS, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": ship}})
    assert r.json()["token"]["branch"] == core.TOKEN_REUSED
    assert connected.calls == []


def test_a_folder_it_will_refuse_is_refused_before_anything_is_issued(
        connected):
    """The save route resolved the token first and hit the relative-path refusal
    afterwards, so a rotation that was then thrown away had already killed the
    running agent -- the exact failure #64 exists to prevent, on the one surface
    that had no guard. `require_absolute_out_dir` says so itself, and the MCP
    already ordered it this way; this is the missing half of "one copy of the
    rule, two moments"."""
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": "some/relative/dir", "rotate_token": True,
        "options": {"namespace": "ns1", "ship_id": FACTS["ships"][0]["id"]}})
    assert r.status_code == 400
    assert connected.calls == [], "it minted a credential it then threw away"


def test_a_page_still_asking_for_the_old_fetch_mints_nothing(connected):
    """`fetch_token` is gone from the request model, and a browser holding the
    previously-shipped bundle posts it on every download. Ignored rather than
    refused: a 422 would break that page, and the field only ever meant mint."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "fetch_token": True})
    assert r.status_code == 200 and connected.calls == []


def test_rotating_mints_once_and_names_whose_credential_it_replaced(connected):
    """Once, not twice: the route resolves the token itself to report the branch
    and then generates from the same options, so a resolution that did not stick
    would issue two tokens and leave the bundle holding the older one."""
    r = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}, "rotate_token": True})
    assert connected.calls == [("auth_token", "aaa111", "bbb222")]
    token = r.json()["token"]
    assert (token["branch"], token["ship_id"]) == (core.TOKEN_ROTATED, "bbb222")
    assert "bbb222" in token["message"]
    secret = next(f for f in r.json()["files"] if f["name"] == "bzm_secret.yaml")
    assert "TOKEN-FROM-API" in secret["content"]


def test_a_bundle_with_no_token_says_so_rather_than_looking_finished(connected):
    """The default download for an agent nobody pasted a token for. It is a fine
    bundle to read and an unusable one to apply, and the only thing standing
    between those two readings is this sentence reaching the page."""
    body = client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}}).json()
    assert body["token"]["branch"] == core.TOKEN_PLACEHOLDER
    assert "create-ship" in body["token"]["message"]


def test_the_zip_says_in_its_headers_which_branch_it_took(connected):
    """A zip's body is the bundle, so the branch travels beside the filename in
    the headers -- wrapping the bytes in an envelope would mean the browser
    saving something that is not a zip."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "ns1"}})
    # The literals, because the frontend reads these two names off the response
    # and a rename on one side loses the sentence silently rather than failing.
    assert (server.TOKEN_BRANCH_HEADER, server.TOKEN_MESSAGE_HEADER) == (
        "X-Bzm-Token-Branch", "X-Bzm-Token-Message")
    assert r.headers[server.TOKEN_BRANCH_HEADER] == core.TOKEN_PLACEHOLDER
    message = r.headers[server.TOKEN_MESSAGE_HEADER]
    assert "create-ship" in message
    # One line, because a header is one line -- the recovery hint is three.
    assert "\n" not in message
    assert "bzm-opl/bzm_secret.yaml" in zipfile.ZipFile(
        io.BytesIO(r.content)).namelist()


def test_a_namespace_no_header_could_carry_does_not_fail_the_download(connected):
    """Both this route's headers quote the namespace -- the token message does and
    the zip's filename always did -- and a person types that into a browser. A
    header is latin-1 by the HTTP spec and starlette raises on anything else, so
    an unencodable character lost the whole download, which is a worse answer than
    a mangled filename. Fixed by the route, not by the namespace: a namespace a
    cluster would accept is an RFC 1123 label, so this is a typo either way."""
    r = client.post("/api/generate/zip", json={
        "facts": FACTS, "options": {"namespace": "blazemeter-平"}})
    assert r.status_code == 200
    assert r.headers[server.TOKEN_BRANCH_HEADER] == core.TOKEN_PLACEHOLDER
    assert "attachment" in r.headers["Content-Disposition"]
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist()


BRANCHES = {core.TOKEN_GIVEN, core.TOKEN_ROTATED, core.TOKEN_REUSED,
            core.TOKEN_PLACEHOLDER}


def test_the_four_branch_names_are_what_the_page_switches_on():
    """frontend/src/api.ts declares this union rather than fetching it -- a closed
    set, like Strength and MergeState -- so the four spellings are load-bearing
    across two languages. Renamed here, one arrives in a browser as a branch no
    sentence covers, and the compiler over there cannot see it."""
    assert BRANCHES == {"given", "rotated", "reused", "placeholder"}


def test_the_page_declares_the_same_four_branches_this_does():
    """Read out of the TypeScript, not restated here.

    This test used to compare core's four constants against four literals and
    say in its docstring that api.ts declared the same union -- which nothing
    checked. A rename on either side left the other compiling: TypeScript cannot
    see Python, and a literal in a Python test cannot see TypeScript. So the
    union and the map that must cover it are parsed from the files themselves,
    and this fails on whichever side moves first.
    """
    src = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src"
    union = re.search(r"export type TokenBranch\s*=\s*([^;]+);",
                      (src / "api.ts").read_text())
    assert union, "TokenBranch union not found -- was it renamed or moved?"
    assert set(re.findall(r'"([^"]+)"', union.group(1))) == BRANCHES

    # The map keyed by that union: every branch needs the sentence beside the
    # button, and TypeScript's Record<TokenBranch, string> only enforces that
    # against whatever the union happens to say.
    carries = re.search(r"const CARRIES: Record<TokenBranch, string> = \{(.*?)\}",
                        (src / "token.ts").read_text(), re.S)
    assert carries, "CARRIES not found -- was it renamed or moved?"
    assert set(re.findall(r"^\s*(\w+):", carries.group(1), re.M)) == BRANCHES


# -- issuing the credential once, where the agent is made ----------------------

def test_creating_an_agent_issues_its_credential_with_it(connected):
    """#64's point, and the reason the rest of the UI can stop minting: the token
    is captured at the one moment it costs nothing, when the ship is new and has
    no previous credential to invalidate. core.create_ship does not fetch,
    because for an *existing* ship it would rotate one on an action whose name
    says nothing about credentials; `bzm-opl-gen create-ship` fetches for exactly
    this reason, and this is the same command with a browser in front of it."""
    body = client.post("/api/ships", json={
        "harbor_id": "aaa111", "name": "agent1"}).json()
    assert body["ship"]["id"] == "s2"
    assert body["auth_token"] == "TOKEN-FROM-API"
    assert body["token_error"] is None
    # For the ship it just made, not for whatever was selected before it.
    assert connected.calls == [("auth_token", "aaa111", "s2")]


def test_an_agent_whose_credential_was_refused_is_still_reported(monkeypatch):
    """The ship exists before the fetch, and some accounts refuse the token
    endpoint outright. A 502 would leave the new agent's id nowhere but a browser
    console -- and the next click creates a second agent in the same location."""
    monkeypatch.setitem(server._state, "client", RefusingClient())
    r = client.post("/api/ships", json={"harbor_id": "aaa111", "name": "agent1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ship"]["id"] == "s2" and body["auth_token"] is None
    assert "could not be issued" in body["token_error"]
    # The way on, not just the failure: a bundle takes a token that was read off
    # the agent in the BlazeMeter UI just as happily as a fetched one.
    assert "auth_token" in body["token_error"]


def test_func_ids_mark_which_ones_change_the_images():
    """The create-location form needs every funcId; the manual form needs only
    the ones that change the answer. Both read this one response, so the
    distinction is served rather than re-derived in TypeScript."""
    rows = client.get("/api/func-ids").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id["performance"]["changes_images"] is True
    assert by_id["functionalApi"]["changes_images"] is False
    # Still offered -- creating a location with it is a real, different thing.
    assert "functionalApi" in by_id
    for f in ("mockServices", "proxyRecorder", "functionalGui"):
        assert by_id[f]["changes_images"] is True


def test_option_defaults_are_served():
    """The UI seeds its options from this response, so anything missing from
    DEFAULT_OPTIONS is a control that starts blank."""
    body = client.get("/api/option-defaults").json()
    assert body["platform"] == "openshift"
    assert body["output_format"] == "manifests"


def test_option_defaults_carry_no_metadata():
    """Every key in this response becomes an option the UI submits, so a
    description or a type added here would arrive at generate() as one."""
    from bzm_opl_gen import generate as gen_mod
    assert set(client.get("/api/option-defaults").json()) == set(gen_mod.DEFAULT_OPTIONS)


def test_option_docs_describe_every_option():
    """Field help comes from the registry rather than a copy in TypeScript --
    an option the UI renders with no description is one the registry is missing,
    and that is a test failure, not a blank tooltip."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/option-docs").json()
    assert set(body) == set(gen_mod.DEFAULT_OPTIONS)
    assert all(e["summary"] for e in body.values())
    assert body["sv_ingress"]["choices"] == list(gen_mod.SV_INGRESS_TYPES)
    assert body["private_registry"]["nullable"] is True
    # The UI must be able to tell which field not to echo back into a form it
    # might save; only the credential is marked.
    assert [k for k, e in body.items() if e["secret"]] == ["auth_token"]


def test_generate_invalid_options_400():
    facts = dict(FACTS, ships=FACTS["ships"] * 2)    # ambiguous ship
    r = client.post("/api/generate", json={
        "facts": facts, "options": {}})
    assert r.status_code == 400


def test_sv_constants_are_served_from_the_generator():
    """The UI renders the ingress picker and decides the SV group is mandatory
    from these. Served rather than copied into TypeScript, so a new backend
    cannot be added to generate() and silently miss the picker."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/sv-constants").json()
    assert body["func_ids"] == list(gen_mod.SV_FUNC_IDS)
    assert body["ingress_types"] == list(gen_mod.SV_INGRESS_TYPES)
    assert "openshift" in body["ingress_types"]     # the newest one reaches the UI
    # Kept out of option-defaults: that response is spread into the options the
    # UI submits, and these are not options.
    assert "ingress_types" not in client.get("/api/option-defaults").json()


def test_sv_constants_carry_what_each_backend_publishes():
    """The UI tells the user which Role the bundle grants and what crane creates
    with it. That is SV_INGRESS_BACKENDS -- restating it in TypeScript is the
    same duplication the funcId list was just deleted for, and it would go stale
    silently, because a wrong Role reads as plausible right up until the virtual
    service stalls."""
    from bzm_opl_gen import generate as gen_mod
    backends = client.get("/api/sv-constants").json()["backends"]
    assert set(backends) == set(gen_mod.SV_INGRESS_TYPES)
    for name, b in gen_mod.SV_INGRESS_BACKENDS.items():
        assert backends[name] == {"group": b.group, "resources": list(b.resources),
                                  "creates": b.creates, "nodeport_ok": b.nodeport_ok}
    # routes/custom-host is the one nobody would guess: OpenShift gates
    # spec.host behind it, and crane sets spec.host.
    assert "routes/custom-host" in backends["openshift"]["resources"]
    # nodeport_ok is served because the UI *decides* with it -- it greys out the
    # download rather than letting generate() refuse after the fact -- so a
    # backend added without it would silently offer a pairing that cannot serve.
    assert {n: b["nodeport_ok"] for n, b in backends.items()} == {
        "nginx": True, "openshift": True, "contour": False, "istio": False}


def test_func_id_choices_cover_the_whole_generator_vocabulary():
    """A location whose funcId the UI never offers can only be created from the
    CLI or the BlazeMeter web app -- which is what a hardcoded copy of the list
    in TypeScript caused. Serving it from the funcId vocabulary the facts layer
    already keys its image selection off means adding one there is enough to
    make it selectable, and retiring one removes it from the form."""
    from bzm_opl_gen import facts as facts_mod
    body = client.get("/api/func-ids").json()
    assert [c["id"] for c in body] == list(facts_mod.CATEGORY_BY_FUNC)
    ids = {c["id"] for c in body}
    assert {"mockServices", "proxyRecorder"} <= ids
    assert "sv-bridge" not in ids                 # retired, so not offered
    assert all(c["label"] for c in body)


def test_unlabelled_func_id_is_still_offered(monkeypatch):
    """The label map is presentation only, so a funcId added to the facts layer
    without one must still appear under its raw name -- the same deliberate
    failure mode as the SV ingress picker. Dropping it would hide the feature
    exactly like the hardcoded list did."""
    from bzm_opl_gen import facts as facts_mod
    monkeypatch.setitem(facts_mod.CATEGORY_BY_FUNC, "tdm", {"performance"})
    body = client.get("/api/func-ids").json()
    # Matched on id/label rather than the whole row: the row carries other
    # fields, and what this pins is that an unlabelled funcId is still offered
    # under its raw name.
    assert {"id": "tdm", "label": "tdm"} in [
        {"id": r["id"], "label": r["label"]} for r in body]


def test_features_are_served_with_a_label_and_a_suggested_namespace():
    """The configure step shows one feature's options at a time and offers this
    list. Served rather than written in TypeScript for the same reason as the
    funcId choices: functional testing, secrets and API monitoring are expected
    to follow, and a feature has to become selectable by being added here."""
    from bzm_opl_gen import generate as gen_mod
    body = client.get("/api/features").json()
    assert [f["id"] for f in body] == [f["id"] for f in core.FEATURES]
    assert body[0]["id"] == "performance"       # the common case is the default
    for f in body:
        assert f["label"] and f["namespace"] and f["func_ids"]
    sv = next(f for f in body if f["id"] == "sv")
    # Which funcIds mean service virtualization is generate.SV_FUNC_IDS', not a
    # second list -- the same reason /api/sv-constants exists.
    assert sv["func_ids"] == list(gen_mod.SV_FUNC_IDS)
    # Distinct namespaces are the point of suggesting one per feature: sharing a
    # namespace is what makes redeploying one agent take the other's pods down.
    assert len({f["namespace"] for f in body}) == len(body)


def test_a_feature_added_to_the_vocabulary_is_offered(monkeypatch):
    """The end-to-end shape of adding a feature: one entry here, plus a tag on
    whichever option groups it owns. Nothing in the frontend enumerates
    features, so this is the whole of the backend half."""
    monkeypatch.setattr(core, "FEATURES", core.FEATURES + [
        {"id": "secrets", "label": "Private vault", "hint": "secrets from a vault",
         "namespace": "blazemeter-vault", "func_ids": ["secretsPrivateVault"]}])
    body = client.get("/api/features").json()
    assert body[-1] == {"id": "secrets", "label": "Private vault",
                        "hint": "secrets from a vault",
                        "namespace": "blazemeter-vault",
                        "func_ids": ["secretsPrivateVault"]}


def test_create_location_forwards_every_selected_func_id(monkeypatch):
    """The funcIds the form submits must reach the API verbatim -- for several
    of them the UI is the only way in short of the BlazeMeter web app."""
    seen = {}

    class FakeClient:
        def create_private_location(self, name, account_id, workspace_ids, **kw):
            seen.update(kw, name=name, workspaces=workspace_ids)
            return {"id": "h9", "name": name, "funcIds": kw["func_ids"]}

    monkeypatch.setitem(server._state, "client", FakeClient())
    r = client.post("/api/locations", json={
        "name": "sv-loc", "account_id": 1, "workspace_id": 2,
        "func_ids": ["mockServices", "proxyRecorder"]})
    assert r.status_code == 200
    assert seen["func_ids"] == ["mockServices", "proxyRecorder"]
    assert r.json()["funcIds"] == ["mockServices", "proxyRecorder"]


def test_api_requires_key():
    assert client.get("/api/accounts").status_code == 401


def test_key_detection_sees_a_key_named_after_startup(monkeypatch, tmp_path):
    """BZM_API_KEY_FILE used to be read into a module-level list at import, so
    a value set afterwards was invisible -- and `ui --dev` sets exactly that for
    its reloader subprocess. Asserted here as well as in test_core because this
    is the route that answers the question."""
    key = tmp_path / "api-key.json"
    key.write_text('{"id": "KID", "secret": "s"}')
    monkeypatch.setenv("BZM_API_KEY_FILE", str(key))
    body = client.get("/api/key/detect").json()
    assert {"path": str(key), "key_id": "KID"} in body["candidates"]
    # The id identifies the key; the secret is what must never come back.
    assert "s" not in [c.get("secret") for c in body["candidates"]]


# The routes that carried an argued paragraph before the prose moved to core.
# Each now points /api/docs at core's docstring instead of keeping a copy;
# what this list guards is that they still point at something.
DOCUMENTED_ROUTES = [
    ("get", "/api/status"), ("post", "/api/facts/manual"),
    ("post", "/api/preflight"), ("get", "/api/sv-mocks"),
    ("get", "/api/sv-check"), ("get", "/api/option-defaults"),
    ("get", "/api/option-docs"), ("get", "/api/func-ids"),
    ("get", "/api/features"), ("get", "/api/sv-constants"),
]


def test_the_routes_that_explained_themselves_still_do():
    """These answers need prose -- what an empty sv-mocks list means, why a
    preflight reaches no cluster -- and it lives in core now. A route that
    stops pointing at it empties its own /api/docs entry, which is exactly
    where nobody would notice."""
    spec = server.app.openapi()
    bare = [f"{m} {path}" for m, path in DOCUMENTED_ROUTES
            if not (spec["paths"][path][m].get("description") or "").strip()]
    assert not bare, f"no description in /api/docs for: {bare}"


def test_a_malformed_request_is_refused_before_the_missing_key_is():
    """Neither scope given: that is wrong with or without a key, and 401 would
    send the caller off to configure one only to be refused again."""
    r = client.get("/api/locations")
    assert r.status_code == 400 and "account_id" in r.json()["detail"]


# -- reading the cluster from the server ---------------------------------------
# The one thing this server does that is not the BlazeMeter API. It is optional
# everywhere: the UI is API-only by design and most people running it have no
# kubecontext, so each way the read can fail comes back 200 with the reason
# rather than an error the browser can only print.

import json  # noqa: E402

from bzm_opl_gen import livetest  # noqa: E402
# The faked kubectl lives with the other cluster-reading tests; reused rather
# than re-declared so both layers exercise the same stand-in binary.
from test_livetest import _fake_kubectl, _sv_pod  # noqa: E402

SV_PODS = json.dumps({"items": [_sv_pod("vs1svc2", 8080, "aaa111", "bbb222"),
                                _sv_pod(None, 5000, extra={"role": "role-crane"})]})


@pytest.fixture
def fake_cluster(monkeypatch):
    """Installs a faked kubectl/oc for one test. Yields the installer so a test
    can choose what the binary does; always clears cli_tool()'s memo of which
    binary exists, which otherwise outlives the monkeypatch."""
    def install(**kw):
        _fake_kubectl(monkeypatch, **kw)
    install()
    yield install
    livetest.cli_tool.cache_clear()


def test_no_cluster_access_leaves_the_rest_of_the_api_working(fake_cluster):
    """The rule the cluster-reading routes must not break: nothing else may
    start depending on a reachable cluster. With no kubectl on the machine at
    all, every other route answers exactly as it does with one."""
    fake_cluster(tools=())
    assert client.post("/api/generate", json={
        "facts": FACTS, "options": {"namespace": "ns1"}}).status_code == 200
    assert client.get("/api/option-defaults").status_code == 200
    assert client.get("/api/sv-constants").status_code == 200


# -- how the server is bound ---------------------------------------------------

def _served(monkeypatch, **kw):
    """Run main() without actually serving, and report what it asked for."""
    import uvicorn
    calls = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, **k: calls.update(app=app, **k))
    server.main(open_browser=False, **kw)
    return calls


def test_ui_binds_loopback_unless_told_otherwise(monkeypatch, capsys):
    """The default has to stay 127.0.0.1: this server holds a BzmClient in
    process memory, so anything that can reach it can act as the API key."""
    assert _served(monkeypatch)["host"] == "127.0.0.1"
    assert "reachable" not in capsys.readouterr().out


def test_ui_warns_when_the_bind_leaves_this_machine(monkeypatch, capsys):
    """Widening the bind is a real exposure, and the one irreversible thing
    behind it is the download button: fetching an AUTH_TOKEN rotates it, so a
    stray click leaves a running agent stuck on a token that no longer works.
    Say so at startup, where it is still cheap to reconsider."""
    assert _served(monkeypatch, host="0.0.0.0")["host"] == "0.0.0.0"
    warning = capsys.readouterr().out
    assert "reachable" in warning and "AUTH_TOKEN" in warning


def test_ui_dev_mode_binds_the_same_host(monkeypatch):
    """--dev reloads through an import string, a second uvicorn.run call that
    used to hardcode its own host -- an easy place for the flag to go missing."""
    assert _served(monkeypatch, host="0.0.0.0", dev=True)["host"] == "0.0.0.0"


# -- the deployed virtual services, alongside the heartbeat --------------------
# The agent reports idle whether or not its virtual services ever became
# reachable, so a stall is invisible in the watch panel. This answers what is
# deployed and at which host, cheaply enough to sit on the existing 10s poll.

def test_sv_mocks_lists_what_is_deployed_and_where_it_answers(fake_cluster):
    fake_cluster(stdout=SV_PODS)
    body = client.get("/api/sv-mocks",
                      params={"namespace": "ns1",
                              "sv_subdomain": "apps.example.com"}).json()
    assert body["status"] == "ok"
    # The host is the one BlazeMeter advertises, so it can be pasted straight
    # into a browser -- and it is built by the generator, not restated here.
    assert body["mocks"] == [{"name": "vs1svc2", "port": 8080,
                              "host": "vs1svc2-8080-ns1.apps.example.com"}]


def test_sv_mocks_separates_deployed_nothing_from_cannot_look(fake_cluster):
    """An empty namespace and an unreadable cluster are different answers: one
    says the virtual service has not deployed yet, the other says this machine
    cannot tell you either way. The watch panel has to say which."""
    fake_cluster(stdout=json.dumps({"items": []}))
    empty = client.get("/api/sv-mocks", params={"namespace": "ns1"}).json()
    assert empty["status"] == "no_mocks" and empty["mocks"] == []
    assert empty["message"]

    fake_cluster(tools=())
    blind = client.get("/api/sv-mocks", params={"namespace": "ns1"}).json()
    assert blind["status"] == "no_cli" and blind["mocks"] == []
    assert "kubectl" in blind["message"] or "oc" in blind["message"]


def test_sv_mocks_without_a_subdomain_still_lists_the_mocks(fake_cluster):
    """The subdomain lives in the options, and the panel polls whether or not
    one is set yet. Losing the host is fine; losing the list is not."""
    fake_cluster(stdout=SV_PODS)
    body = client.get("/api/sv-mocks", params={"namespace": "ns1"}).json()
    assert [m["name"] for m in body["mocks"]] == ["vs1svc2"]
    assert body["mocks"][0]["host"] is None


def test_sv_mocks_never_errors_the_poll(fake_cluster):
    """It rides the status poll, which keeps the last good answer on failure.
    A 4xx/5xx here would either spam the console every 10s or, worse, be
    swallowed and read as 'no virtual services'."""
    fake_cluster(rc=1, stderr="error: current-context is not set")
    r = client.get("/api/sv-mocks", params={"namespace": "ns1"})
    assert r.status_code == 200 and r.json()["status"] == "no_context"


# -- does the endpoint answer? -------------------------------------------------
# The listed mocks are running pods, which says nothing about whether anything
# routes to them -- crane's nginx Ingress names a port its own Service does not
# expose, so the published endpoint 503s while the pod is healthy. This is the
# only outbound HTTP request the server makes that is not the BlazeMeter API,
# and every test of it fakes that request: a real one would pass or fail on
# whatever DNS answered that day, and the failure kinds below cannot be
# provoked from a machine that may have no network at all.

import socket  # noqa: E402
import ssl  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


class _FakeResponse:
    """The little of http.client.HTTPResponse that the probe touches."""

    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Nothing in this module reaches the network, and a test that starts to
    must say so rather than depend on the host it happened to hit."""
    def refuse(*a, **kw):
        raise AssertionError("a test made a real HTTP request")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def fake_endpoint(monkeypatch):
    """Stand in for the virtual service's endpoint. Yields (install, calls):
    `install` takes a status code to answer with or an exception to raise, and
    `calls` records what the probe asked for, so the URL and the deadline are
    assertable rather than taken on trust."""
    calls = []

    def install(answer):
        def urlopen(req, timeout=None, **kw):
            calls.append({"url": getattr(req, "full_url", req), "timeout": timeout})
            if isinstance(answer, Exception):
                raise answer
            return _FakeResponse(answer)
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    return install, calls


def test_sv_check_reports_the_status_code_when_the_endpoint_answers(fake_endpoint):
    install, calls = fake_endpoint
    install(200)
    body = client.get("/api/sv-check",
                      params={"host": "vs1-8080-ns1.apps.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 200
    assert "200" in body["message"]
    assert calls[0]["url"] == "http://vs1-8080-ns1.apps.example.com/"


def test_sv_check_probes_the_host_the_panel_already_shows(fake_cluster, fake_endpoint):
    """The one string that must not be rebuilt: what is checked has to be what
    the row above it displays, or a green tick would be vouching for an address
    nobody was given. Handed back from /api/sv-mocks untouched."""
    fake_cluster(stdout=SV_PODS)
    host = client.get("/api/sv-mocks",
                      params={"namespace": "ns1",
                              "sv_subdomain": "apps.example.com"}
                      ).json()["mocks"][0]["host"]
    install, calls = fake_endpoint
    install(200)
    client.get("/api/sv-check", params={"host": host})
    assert calls[0]["url"] == f"http://{host}/"


def test_sv_check_reads_a_503_as_a_diagnosis_not_a_failure(fake_endpoint):
    """The whole point of the button. A 503 from the ingress controller is the
    answer -- it is what crane's port mismatch looks like from outside -- so it
    reports the status it got and names the command that fixes it."""
    install, _ = fake_endpoint
    install(urllib.error.HTTPError(
        "http://vs1-8080-ns1.apps.example.com/", 503, "Service Unavailable",
        {}, None))
    r = client.get("/api/sv-check",
                   params={"host": "vs1-8080-ns1.apps.example.com"})
    assert r.status_code == 200
    body = r.json()
    # It answered: this is not one of the failure kinds.
    assert body["status"] == "ok" and body["code"] == 503
    assert "sv-expose" in body["message"]


def test_sv_check_reports_any_other_http_status_it_gets(fake_endpoint):
    """404 from the mock itself is a routed endpoint, and the panel must not
    round it up to a failure the way a 503 diagnosis would."""
    install, _ = fake_endpoint
    install(urllib.error.HTTPError("http://h/", 404, "Not Found", {}, None))
    body = client.get("/api/sv-check", params={"host": "h.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 404
    assert "sv-expose" not in body["message"]


@pytest.mark.parametrize("status, error", [
    # Four distinct answers because four distinct things went wrong, and each
    # has its own way forward: no DNS record, nothing listening, a certificate
    # this machine will not accept, or a host that never replied.
    ("dns", urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known"))),
    ("refused", urllib.error.URLError(
        ConnectionRefusedError(61, "Connection refused"))),
    ("tls", urllib.error.URLError(ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self signed certificate (_ssl.c:1000)"))),
    ("timeout", urllib.error.URLError(socket.timeout("timed out"))),
    # A read that stalls after the connect succeeds surfaces bare, not wrapped.
    ("timeout", TimeoutError("timed out")),
    # Anything unforeseen still comes back as an answer, never a traceback.
    ("error", urllib.error.URLError("<unknown>")),
])
def test_sv_check_tells_the_failure_kinds_apart(fake_endpoint, status, error):
    install, _ = fake_endpoint
    install(error)
    r = client.get("/api/sv-check", params={"host": "vs1-8080-ns1.example.com"})
    # Never an HTTP error: the browser can only print those in red, and a host
    # that does not answer is the expected outcome this button exists to find.
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == status
    assert body["code"] is None
    assert body["message"]              # says which one it was, in words
    assert body["detail"]               # ...and carries the raw reason


def test_sv_check_waits_no_longer_than_a_poll_interval(fake_endpoint):
    """A hung endpoint must not stall the panel it sits in: the watch poll comes
    round every 10s, so the deadline is below that."""
    install, calls = fake_endpoint
    install(200)
    client.get("/api/sv-check", params={"host": "h.example.com"})
    assert calls[0]["timeout"] == core.SV_CHECK_TIMEOUT_S
    assert 0 < core.SV_CHECK_TIMEOUT_S < 10


def test_sv_check_can_be_asked_for_https(fake_endpoint):
    """TLS is configured per deployment (sv_tls_secret), and probing the wrong
    scheme answers a question nobody asked -- plain http against a TLS-only
    route, or a handshake against a listener that speaks none."""
    install, calls = fake_endpoint
    install(200)
    client.get("/api/sv-check", params={"host": "h.example.com", "scheme": "https"})
    assert calls[0]["url"] == "https://h.example.com/"


@pytest.mark.parametrize("bad", [
    "h.example.com/admin",              # a path -- would fetch anything
    "user:pw@h.example.com",
    "h.example.com evil.example.com",
    "",
])
def test_sv_check_refuses_anything_that_is_not_a_host(fake_endpoint, bad):
    """The host arrives from the browser, so the guard is what keeps this from
    being a general-purpose fetcher pointed by whatever loads the page. A
    rejected request is the user getting it wrong, so unlike an endpoint that
    will not answer it is a 4xx."""
    install, calls = fake_endpoint
    install(200)
    assert client.get("/api/sv-check", params={"host": bad}).status_code == 400
    assert calls == []


@pytest.mark.parametrize("scheme", ["ftp", "file"])
def test_sv_check_refuses_a_scheme_it_does_not_speak(fake_endpoint, scheme):
    install, calls = fake_endpoint
    install(200)
    assert client.get("/api/sv-check", params={
        "host": "h.example.com", "scheme": scheme}).status_code == 400
    assert calls == []


def test_sv_check_needs_no_cluster(fake_cluster, fake_endpoint):
    """It reads nothing from kubectl -- the host was resolved when the panel
    listed the mock. With no CLI on the machine at all it still answers, which
    is what keeps the button from being a second thing that needs a cluster."""
    fake_cluster(tools=())
    install, _ = fake_endpoint
    install(200)
    body = client.get("/api/sv-check", params={"host": "h.example.com"}).json()
    assert body["status"] == "ok" and body["code"] == 200


def test_group_tags_name_features_the_server_actually_serves():
    """The frontend tags each option group with the feature ids it belongs to,
    and those ids are the join between the two halves. Nothing else checks it:
    the vitest suite tags against its own fixture, so renaming a served id
    passes both suites green and silently empties a feature's options in the
    browser. Read the tags out of the source rather than duplicating them."""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "frontend", "src", "optionGroups.ts")
    with open(src) as fh:
        text = fh.read()
    tagged = {i for line in text.splitlines() if "features:" in line
              for i in re.findall(r'"([^"]+)"', line)}
    served = {f["id"] for f in client.get("/api/features").json()}
    assert tagged, "no group tags found -- has the declaration shape changed?"
    assert tagged <= served, (
        f"option groups tag features the server does not serve: "
        f"{sorted(tagged - served)}. Either the id was renamed in "
        f"server.FEATURES, or the tag is a typo -- the group's options would "
        f"never appear.")


# -- preflight from an evidence file -------------------------------------------
# The browser half of `doctor --cluster-evidence`. Nothing here may reach for a
# cluster or for the API key: the file is the cluster read, and the case this
# exists for is the customer whose account and cluster are both out of reach.

from bzm_opl_gen import doctor, suggest  # noqa: E402
# The fixtures live with the checks that read them, so the HTTP layer is fed the
# same documents the doctor tests are -- a difference between the two would be a
# difference in what the browser is told.
from test_cluster_evidence import DEGRADED, _evidence  # noqa: E402
from test_doctor import FACTS as LOC_FACTS, SV_NGINX  # noqa: E402


def _degraded():
    with open(DEGRADED) as fh:
        return json.load(fh)


def _preflight(evidence, options=None, facts=None):
    return client.post("/api/preflight", json={
        "facts": LOC_FACTS if facts is None else facts,
        "options": {"namespace": "blazemeter", **(options or {})},
        "evidence": evidence})


def _find_check(body, needle):
    hits = [c for c in body["checks"] if needle in c["name"]]
    assert hits, f"no check matching {needle!r} in {[c['name'] for c in body['checks']]}"
    return hits[0]


def test_preflight_answers_with_the_verdicts_the_command_would_print():
    """The seam #51 built, over HTTP: the same Check list, in the same order,
    with nothing filtered on the way to the browser. Compared against
    doctor.evaluate itself, because the failure this guards against is the
    server quietly dropping or reordering a verdict nobody would then miss."""
    doc = _evidence()
    opts = {"namespace": "blazemeter", **SV_NGINX}
    r = _preflight(doc, SV_NGINX)
    assert r.status_code == 200
    imported = doctor.cluster_from_evidence(doc, "blazemeter")
    expected = doctor.evaluate(LOC_FACTS, opts, "blazemeter",
                               cluster_data=imported.cluster,
                               probes=imported.probes, extra_checks=imported.checks)
    assert [(c["status"], c["name"], c["detail"]) for c in r.json()["checks"]] \
        == [(c.status, c.name, c.detail) for c in expected]
    assert r.json()["namespace"] == "blazemeter"


def test_preflight_leads_with_where_the_answers_came_from():
    """Provenance is a verdict, not an aside: collected when, for which
    namespace, and what the script could not read -- so a thin file cannot be
    mistaken for a clean bill of health. It leads the list because it qualifies
    everything after it."""
    body = _preflight(_degraded(), {"namespace": "some-ns"}).json()
    first = body["checks"][0]
    assert "evidence" in first["name"]
    assert "2026-07-28T02:51:50Z" in first["detail"]     # collected at
    assert "some-ns" in first["detail"]                  # and for which namespace
    assert "could not read nodes" in first["detail"]     # and what it could not see
    assert first["status"] == "WARN"
    # The thin file's other half: everything it could not read is a warning, and
    # none of it a pass anyone stood behind.
    assert "FAIL" not in {c["status"] for c in body["checks"]}


def test_preflight_carries_what_the_file_says_about_itself_as_data():
    """The same three facts the leading verdict states in prose, apart from it:
    collected when, which namespace the file describes, and what the collector
    could not read. Prose in a list of ten verdicts is where a thin file passes
    for a clean bill of health (#53), so the browser gets them as fields and
    puts them in the header -- and reads them here rather than parsing that
    sentence, which would be a second opinion about the same file.
    """
    body = _preflight(_degraded(), {"namespace": "some-ns"}).json()
    assert body["evidence"] == {
        "collected_at": "2026-07-28T02:51:50Z",
        "namespace": "some-ns",
        # Every section this collector was refused, named -- one entry per
        # section, in the order the script wrote them.
        "unreadable": ["nodes", "ingressclasses", "namespace", "scoped",
                       "ingress_config", "proxy_config"]}
    # ...and it stays consistent with the verdict, which is the same file read
    # by the same function.
    detail = body["checks"][0]["detail"]
    for part in body["evidence"]["unreadable"]:
        assert part in detail


def test_preflight_says_a_readable_file_had_nothing_it_could_not_read():
    """The other half: an empty list, never a missing key. A header that has to
    guess whether "no notes" means "read everything" or "field absent" is one
    that shows the reassuring answer for both."""
    body = _preflight(_evidence()).json()
    assert body["evidence"]["unreadable"] == []
    assert body["evidence"]["collected_at"] == "2026-07-27T10:00:00Z"
    assert body["evidence"]["namespace"] == "blazemeter"


def test_preflight_needs_no_api_key_and_no_cluster(monkeypatch):
    """The same 'no access to anything' path manual facts entry serves. No key
    is configured in this process, and nothing may go looking for a kubectl --
    either being required would defeat the point. Same guard the command's own
    test uses, because this is the same claim over HTTP."""
    monkeypatch.setattr(doctor.livetest, "cli_tool",
                        lambda: pytest.fail("preflight went looking for a cluster"))
    r = _preflight(_degraded(), {"namespace": "some-ns"})
    assert r.status_code == 200 and r.json()["checks"]


def test_preflight_of_manually_entered_facts_reports_no_failures():
    """Both halves of the no-access path in one request, as the browser makes
    it: the facts the manual form produced, judged against a file someone else
    collected. The facts go back out over HTTP and come in again, so this also
    holds the marker doctor reads to surviving the round trip -- trimmed on the
    way, the location verdicts would silently be failures again."""
    facts = client.post("/api/facts/manual",
                        json={"harbor_id": "H1", "ship_id": "S1"}).json()["facts"]
    body = _preflight(_degraded(), {"namespace": "some-ns"}, facts=facts).json()
    assert "FAIL" not in {c["status"] for c in body["checks"]}
    for name in ("location slots", "location threadsPerEngine"):
        assert _find_check(body, name)["status"] == "WARN"


def test_preflight_judges_the_configuration_it_was_sent():
    """Not the defaults: an engine size no node can hold is a FAIL against the
    same evidence that passes at the documented one."""
    doc = _evidence()
    assert _find_check(_preflight(doc).json(), "capacity")["status"] == "PASS"
    huge = _preflight(doc, {"engine_cpu_limit": "64",
                            "engine_mem_limit": "256Gi"}).json()
    assert _find_check(huge, "capacity")["status"] == "FAIL"


def test_preflight_reports_evidence_collected_for_another_namespace():
    """Most of what follows is per-namespace. The file still describes the same
    nodes, so this is reported rather than refused -- and the namespace judged
    is the one being configured, never the one the file happens to name."""
    body = _preflight(_evidence("their-ns")).json()
    first = body["checks"][0]
    assert body["namespace"] == "blazemeter"
    assert first["status"] == "WARN"
    assert "their-ns" in first["detail"] and "blazemeter" in first["detail"]


def test_preflight_falls_back_to_the_namespace_the_file_was_collected_for():
    """Same precedence as the command: what is being configured wins, and the
    file's own namespace is the last resort rather than the first."""
    r = client.post("/api/preflight", json={
        "facts": LOC_FACTS, "options": {}, "evidence": _evidence("their-ns")})
    assert r.status_code == 200
    assert r.json()["namespace"] == "their-ns"


@pytest.mark.parametrize("evidence,says", [
    # The likeliest wrong file: account facts, which have no schema at all.
    ({"harbor_id": "aaa111", "ships": []}, "no 'schema' field"),
    # A file from a newer collector -- half-parsing it produces verdicts about
    # a cluster nobody described.
    ({"schema": "bzm-opl-cluster-evidence/2", "raw": {}}, "bzm-opl-cluster-evidence/2"),
    # Not an object at all. Refused by doctor rather than by a request-validation
    # error, so the message says what was found instead of naming a field.
    ([{"schema": doctor.EVIDENCE_SCHEMA}], "a JSON array"),
    # Mailed in and trimmed on the way.
    (_evidence(nodes=[{"metadata": {"name": "n1"}}]), "raw.nodes"),
])
def test_preflight_refuses_a_file_that_is_not_evidence(evidence, says):
    r = _preflight(evidence)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert says in detail
    # Every refusal names the kind of file that was wanted -- the browser has no
    # --help to fall back on, and "invalid" alone leaves the reader guessing
    # which of the two files they were asked for is the wrong one.
    assert "cluster evidence" in detail
    # Nothing to render over what the browser already has: a refusal carries no
    # verdicts, so a previously imported file's are left standing.
    assert "checks" not in r.json()


def test_preflight_refuses_options_no_bundle_could_be_generated_from():
    """A quantity that does not parse is a 400 like /api/generate's, not a
    traceback -- this re-runs on every keystroke in those fields."""
    assert _preflight(_evidence(), {"engine_cpu_limit": "two"}).status_code == 400


# -- what the same file implies about the options ------------------------------
# Carried on the preflight response rather than served from an endpoint of its
# own: it is the same file, judged against the same configuration, and both have
# to move together on every option change. Two endpoints is two round trips that
# can disagree about which options the answer describes.

from test_suggest import API_GROUPS as SUGGEST_GROUPS  # noqa: E402
from test_suggest import REGCRED, _evidence as _read_evidence  # noqa: E402


def _suggestions(evidence, options=None):
    body = _preflight(evidence, options).json()
    return {s["option"]: s for s in body["suggestions"]}


def test_preflight_carries_what_the_evidence_implies_about_the_options():
    """The same suggestions `bzm-opl-gen suggest` prints, in the same order and
    the same wire shape -- compared against suggest itself, because the failure
    to guard is the server quietly reshaping them on the way to the browser."""
    doc = _read_evidence()
    body = _preflight(doc).json()
    expected = suggest.from_evidence(doc)
    assert [s["option"] for s in body["suggestions"]] == [s.option for s in expected]
    for got, want in zip(body["suggestions"], expected):
        assert got["strength"] == want.strength
        assert got["value"] == want.value
        assert got["candidates"] == list(want.candidates)
        assert got["evidence"] == list(want.evidence)


def test_preflight_says_how_each_suggestion_stands_against_this_configuration():
    """Not against the defaults: the same evidence is a fill for a configuration
    that named no pull secret and a conflict for one that named another."""
    doc = _read_evidence(**REGCRED)
    assert _suggestions(doc)["pull_secret"]["state"] == suggest.FILL
    conflict = _suggestions(doc, {"pull_secret": "team-creds"})["pull_secret"]
    assert conflict["state"] == suggest.CONFLICT
    # Both values reach the browser, because the row shows both.
    assert conflict["current"] == "team-creds" and conflict["value"] == "regcred"


def test_preflight_never_offers_a_value_for_a_suggestive_suggestion():
    """The invariant, over HTTP, where a browser could otherwise read `value`
    off a shortlist and call it a default."""
    doc = _read_evidence(api_groups=dict(SUGGEST_GROUPS, contour=True))
    for s in _preflight(doc).json()["suggestions"]:
        if s["strength"] == suggest.SUGGESTIVE:
            assert s["value"] is None


def test_preflight_says_why_it_has_nothing_to_suggest():
    """An empty list from a file that reached no cluster reads exactly like one
    from a cluster that constrains nothing, and only the first is worth
    re-collecting for. Same sentence the command prints."""
    body = _preflight(_degraded(), {"namespace": "some-ns"}).json()
    assert body["suggestions"] == []
    assert "serverVersion" in body["why_nothing"]


def test_preflight_drops_the_reason_once_there_is_something_to_show():
    body = _preflight(_read_evidence()).json()
    assert body["suggestions"] and body["why_nothing"] is None


# -- saving a bundle to disk ---------------------------------------------------
# The zip is for handing a bundle to somebody; /api/generate/save writes the
# same files where livetest and an MCP session can pick them up.

def test_generate_save_writes_the_bundle_where_asked(tmp_path):
    out = str(tmp_path / "bundle")
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": out})
    assert r.status_code == 200
    body = r.json()
    assert body["out_dir"] == out
    names = [f["name"] for f in body["files"]]
    # profile.json is the handoff: livetest re-renders from it, and an MCP
    # session reads it to see what this bundle was configured as.
    assert "bzm_deployment.yaml" in names and "profile.json" in names
    assert os.path.isfile(os.path.join(out, "bzm_deployment.yaml"))
    assert os.path.isfile(os.path.join(out, "profile.json"))


def test_generate_save_expands_home(tmp_path, monkeypatch):
    """`~` is how a person types their home directory into a browser field."""
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": "~/bundle"})
    assert r.status_code == 200
    assert r.json()["out_dir"] == str(tmp_path / "bundle")
    assert os.path.isfile(tmp_path / "bundle" / "bzm_deployment.yaml")


def test_generate_save_refuses_a_relative_dir():
    """core's refusal (a relative path resolves against a cwd nobody chose)
    must arrive as this transport's 400, not a 500."""
    r = client.post("/api/generate/save", json={
        "facts": FACTS, "options": {"namespace": "ns1"},
        "out_dir": "some/relative/dir"})
    assert r.status_code == 400
    assert "absolute" in r.json()["detail"]


def test_saving_twice_into_the_same_folder_reuses_the_token(connected, tmp_path):
    """The reuse branch, which this route only reaches because it passes the
    directory it is about to write. Saving again -- to re-render with one option
    changed, which is what the folder handoff is for -- must leave the agent
    running from the last save alone, and produce the same bytes."""
    out = str(tmp_path / "bundle")
    first = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": out,
        "options": {"namespace": "ns1", "auth_token": "TOKENVALUE"}})
    assert first.json()["token"]["branch"] == core.TOKEN_GIVEN
    secret = os.path.join(out, "bzm_secret.yaml")
    was = open(secret).read()
    again = client.post("/api/generate/save", json={
        "facts": FACTS, "out_dir": out, "options": {"namespace": "ns1"}})
    token = again.json()["token"]
    assert (token["branch"], token["ship_id"]) == (core.TOKEN_REUSED, "bbb222")
    assert out in token["message"]
    assert open(secret).read() == was and connected.calls == []


def test_saving_a_bundle_for_another_agent_does_not_inherit_its_token(
        connected, tmp_path):
    """The loud half of the same branch, and it is a refusal rather than a
    placeholder: saving into that folder would *overwrite* the other agent's
    bundle, and no API reads an AUTH_TOKEN back, so that folder was the only copy
    of it outside a running cluster. Refused, and the file is intact after."""
    out = str(tmp_path / "bundle")
    two = dict(FACTS, ships=[dict(FACTS["ships"][0], id="b1"),
                             dict(FACTS["ships"][0], id="b2")])
    client.post("/api/generate/save", json={
        "facts": two, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": "b1", "auth_token": "B1TOKEN"}})
    again = client.post("/api/generate/save", json={
        "facts": two, "out_dir": out,
        "options": {"namespace": "ns1", "ship_id": "b2"}})
    assert again.status_code == 400
    assert "b1" in again.json()["detail"] and "b2" in again.json()["detail"]
    assert "B1TOKEN" in open(os.path.join(out, "bzm_secret.yaml")).read(), \
        "the refusal must not have destroyed the token it was protecting"
