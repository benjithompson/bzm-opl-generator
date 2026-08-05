"""Gather deployment-relevant facts about a customer's private location.

Facts = everything the generator needs that comes from the BlazeMeter account
rather than from the customer's cluster team:
  - harbor (location) id/name, funcIds (which functionalities are enabled)
  - ships (agents): id, name, installed crane version
  - actual image inventory reported by running agents (ground truth for
    private-registry mirroring), classified performance vs other
"""

import json

# Image classification: substring -> functional category. Anything unmatched
# is a core performance/engine image.
IMAGE_CATEGORY = {
    "doduo": "gui",            # grid proxy (GUI functional / Selenium)
    "charmander": "gui",       # browser image (GUI functional)
    "service-mock": "mock",
    "mock-pc-service": "mock",
    "group-gateway": "mock",   # mock services gateway
    "proxy-recorder": "recorder",
}

# Location funcIds -> image categories that functionality needs.
# (browser-version funcIds like "chrome:default" ride along with functionalGui;
# tdm/dataPublisher/delphix need no engine images of their own.)
CATEGORY_BY_FUNC = {
    "performance": {"performance"},
    "functionalApi": {"performance"},          # API tests run in the taurus engine
    "functionalGui": {"performance", "gui"},
    "mockServices": {"mock"},
    "proxyRecorder": {"recorder"},
}


def image_category(ref):
    for key, cat in IMAGE_CATEGORY.items():
        if key in ref:
            return cat
    return "performance"


def image_distinct_funcs():
    """The funcIds worth offering when the only thing the choice decides is
    which images the bundle names.

    Two funcIds needing the same image categories generate byte-identical
    manifests, so offering both is a choice with no consequence: `functionalApi`
    and `performance` are both "runs in the taurus engine". Declaration order
    picks the representative, which is why `performance` is listed first.

    Creating a *location* is a different question and keeps the full vocabulary
    -- BlazeMeter does distinguish them there, and a location created without
    functionalApi cannot run functional API tests. Only the manual-entry form,
    where funcIds exist purely to select images, uses this reduced list.
    """
    seen, out = set(), []
    for func, cats in CATEGORY_BY_FUNC.items():
        key = frozenset(cats)
        if key not in seen:
            seen.add(key)
            out.append(func)
    return out


def needed_categories(func_ids):
    needed = set()
    for f in func_ids or []:
        needed |= CATEGORY_BY_FUNC.get(f, set())
    return needed or {"performance"}


def select_images(facts, all_images=False):
    """The images this location actually needs, based on its enabled funcIds."""
    needed = needed_categories(facts.get("func_ids"))
    return [
        i for i in facts["images"]
        if i.get("key") and (all_images or image_category(i["repo"]) in needed)
    ]

# Fallback catalogue when there is no agent inventory to read -- either no agent
# has run yet, or nobody has account access at all (see `manual`).
#
# Keys are the local tags crane resolves IMAGE_OVERRIDES by; repos are what the
# account actually reports. Most keys map to a repo of the same name, but not
# all -- taurus-cloud is `v4` and apm-image is `apm` here, and the account has
# others in the same shape (blazemeter is `v3`, secrets-image is `secrets`).
# So this is a table read off live agent inventories, not a naming rule: derive
# a repo from its key and you get one that does not exist.
FALLBACK_IMAGES = [
    # performance: the taurus engine and its APM sidecar.
    {"key": "taurus-cloud:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/v4", "tag": "latest", "category": "performance"},
    {"key": "apm-image:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/apm", "tag": "latest", "category": "performance"},
    # Also in crane's image set for a performance-only location -- found by
    # reading what a live Kubernetes agent reports, which is where the two
    # below had been missing from. Neither is pulled by an ordinary
    # performance run (a full test on CRC fetched only crane and v4), but
    # crane may ask for them, and a key it cannot find in a sealed cluster is
    # an ImagePullBackOff mid-test rather than a warning.
    {"key": "torero:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/torero", "tag": "latest", "category": "performance"},
    {"key": "richrach:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/richrach", "tag": "latest", "category": "performance"},
    # mock services.
    {"key": "blazemeter/service-mock:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/service-mock", "tag": "latest", "category": "mock"},
    {"key": "blazemeter/group-gateway:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/group-gateway", "tag": "latest", "category": "mock"},
    # Not observed live in the account this table was read from, but the key ->
    # repo shape is the regular one every other `blazemeter/<name>:latest` key
    # follows. Listed because omitting it is the silent failure (crane falls
    # back to the public registry for a key it cannot find).
    {"key": "blazemeter/mock-pc-service:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/mock-pc-service", "tag": "latest", "category": "mock"},
    # proxy recorder.
    {"key": "blazemeter/proxy-recorder:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/proxy-recorder", "tag": "latest", "category": "recorder"},
    # GUI functional: the grid proxy. The browser images (charmander/chrome_*,
    # firefox_*, microsoftedge_*, safari_*) are deliberately absent -- the
    # account carries 60+ version-pinned repos and which one a location needs
    # comes from its browser funcIds, so there is no defensible default. A GUI
    # location bound for a private registry needs a live inventory; see
    # `gui_images_incomplete`.
    {"key": "blazemeter/doduo:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/doduo", "tag": "latest", "category": "gui"},
]

CRANE_REPO = "gcr.io/verdant-bulwark-278/blazemeter/crane"
BLAZEMETER_PROJECT = "gcr.io/verdant-bulwark-278/blazemeter"

# Crane's image keys mostly name their own repo, but not always, and the
# exceptions cannot be derived -- `taurus-cloud` lives at `v4`, `blazemeter` at
# `v3`. The catalogue above carries the ones it lists; these are the rest,
# observed in live inventories across the account.
KEY_REPO_EXCEPTIONS = {
    "blazemeter": "v3",
    "secrets-image": "secrets",
}


def repo_for_key(key):
    """The repo a crane image key resolves to.

    A Kubernetes agent reports its images as bare keys -- `taurus-cloud:latest`,
    `torero:4.6.182` -- with no registry to read the repo off, so it has to be
    looked up. Known keys come from the catalogue, then the exceptions, and
    anything else follows the regular rule (`<name>` -> `blazemeter/<name>`),
    which is what every key added since has done.

    A key may carry a path of its own, and all of it is repo. Browser images
    arrive as `blazemeter/charmander/chrome_136.0.7103.113`, where only
    `blazemeter/` is redundant with the project prefix -- `charmander/` is a
    real directory under it. Keeping just the last segment resolved them to
    `.../blazemeter/chrome_136.0.7103.113`, which does not exist, and dropped
    `charmander` from the repo, which is the substring `image_category` reads,
    so browser images also stopped being GUI images.

    The prefix is spelled out rather than taken off BLAZEMETER_PROJECT: that a
    key's first segment matches the project's last one is a coincidence of two
    separately-observed facts, and a renamed project should not silently stop
    the stripping.

    A name whose first segment looks like a host is not a key at all -- it is a
    reference that already names its own repo, which is what a Docker agent
    pulling from a private mirror reports. Prefixing the project onto one gives
    `.../blazemeter/reg.corp.com/bzm/v4`; taking its last segment instead gives
    `.../blazemeter/v4`, which is worse for being plausible -- it says the image
    lives somewhere the agent is not pulling it from.
    """
    # The tag is what follows the colon *after* the last slash: `localhost:5001/v4`
    # is a port, not a tag, and splitting on the first colon leaves `localhost`.
    head, sep, tail = key.rpartition(":")
    name = head if sep and "/" not in tail else key
    for i in FALLBACK_IMAGES:
        if i["key"].split(":", 1)[0] == name:
            return i["repo"]
    first = name.split("/", 1)[0]
    if "." in first or ":" in first:
        return name
    if name.startswith("blazemeter/"):
        name = name[len("blazemeter/"):]
    return f"{BLAZEMETER_PROJECT}/{KEY_REPO_EXCEPTIONS.get(name, name)}"




def gather(client, harbor_id):
    harbor = client.private_location(harbor_id)
    facts = {
        "harbor_id": harbor["id"],
        "harbor_name": harbor.get("name"),
        "func_ids": harbor.get("funcIds", []),
        "slots": harbor.get("slots"),
        # Max threads one engine will run. Null on a location created via the
        # API (POST ignores it) -- and then every test start 403s, so the
        # doctor treats it as a hard failure rather than a detail.
        "threads_per_engine": harbor.get("threadsPerEngine"),
        # The location's own engine sizing: the engine pod's *requests*, and
        # since #132 the source generate.resolve_engine_limits derives the
        # bundle's KUBERNETES_RESOURCES_LIMITS_CPU/_MEMORY from when no
        # explicit option names them -- the two are one figure, and deriving
        # is what makes them agree by construction. (They set different pod
        # fields, settled on a live run: overrides -> requests, the bundle's
        # env -> limits.)
        # Read off a real account: overrideCPU is whole cores; the heaps are MB
        # (4096 on 160 of 171 locations). overrideMemory's unit is *not*
        # reliable -- the same account holds 32, 4000 and 8196 -- so it is
        # carried verbatim here and read as Mi where it is derived.
        "override_cpu": harbor.get("overrideCPU"),
        "override_memory": harbor.get("overrideMemory"),
        # The JVM heap inside that container. A limit the heap never reaches is
        # node capacity nobody consumes; a heap above the limit is an OOMKill
        # mid-run, which reads as a test that stopped rather than a resource
        # error. Neither is visible to a scheduler, so nothing but this pairing
        # catches them.
        "engine_xmx_mb": harbor.get("engineXmx"),
        "engine_xms_mb": harbor.get("engineXms"),
        "ships": [],
        "images": [],
        "crane_image": None,  # set from inventory if an agent is live
    }
    seen = set()
    for ship in harbor.get("ships", []):
        facts["ships"].append({
            "id": ship["id"],
            "name": ship.get("name"),
            "state": ship.get("state"),
            "installed_version": ship.get("installedVersion"),
            "last_heartbeat": ship.get("lastHeartBeat"),
        })
        info = (ship.get("hostInfo") or {}).get("containerManager", {}).get("info", {})
        for img in info.get("images", []):
            tags = img.get("RepoTags") or []
            # Two shapes, because the two container managers report differently:
            #
            #   Docker      ['gcr.io/.../blazemeter/v4:2.4.444', 'taurus-cloud:latest']
            #               -- registry-qualified, so the repo is read off it.
            #   Kubernetes  ['taurus-cloud:latest']
            #               -- the bare key only, and Size 0. This is crane's
            #               configured image set rather than a listing of what
            #               is on the node, so the repo has to be looked up.
            #
            # Only the Docker shape used to be handled, which meant every
            # Kubernetes agent -- the kind this tool generates for -- silently
            # produced no inventory at all and fell through to the catalogue.
            gcr = [t for t in tags if t.startswith("gcr.io/")]
            local = [t for t in tags if not t.startswith("gcr.io/")]
            if gcr:
                ref = gcr[0]
                key = next((t for t in local if t.endswith(":latest")), None)
                repo, tag = ref.rsplit(":", 1)
            elif local:
                key = local[0]
                repo, tag = repo_for_key(key), key.rsplit(":", 1)[-1]
                ref = f"{repo}:{tag}"
            else:
                continue
            if ref in seen:
                continue
            seen.add(ref)
            entry = {
                "key": key,                          # crane's IMAGE_OVERRIDES key
                "repo": repo,
                "tag": tag,
                # Kubernetes reports 0 for every image; None says "unknown"
                # rather than claiming an empty image.
                "size_mb": round(img["Size"] / 1e6) if img.get("Size") else None,
                "category": image_category(repo),
            }
            if repo == CRANE_REPO:
                facts["crane_image"] = ref
            elif entry["key"]:
                facts["images"].append(entry)
    if not facts["images"]:
        facts["images"] = [dict(i, size_mb=None) for i in FALLBACK_IMAGES]
        facts["images_source"] = "fallback-catalogue (no agent inventory yet)"
    else:
        facts["images_source"] = "live agent inventory"
    if not facts["crane_image"]:
        facts["crane_image"] = f"{CRANE_REPO}:latest"
    return facts


MANUAL_SOURCE = "manual entry (no account access)"


def from_manual_entry(facts):
    """True when these facts were typed in rather than read from the account.

    Not a second shape and not a second field: `images_source` already records
    where they came from, and reading it is what lets a consumer tell "there was
    no account to ask" from "the account answered, and the answer was nothing".
    Both arrive as None, and only the second is ever a misconfiguration -- see
    doctor.check_location, the one place that distinction changes a verdict.

    Nothing that *generates* asks this, and nothing should: the manifests are
    identical either way, which is the property manual() exists to preserve.
    """
    return facts.get("images_source") == MANUAL_SOURCE


def image_refs(facts, all_images=False):
    """Every image reference this location's bundle will pull, crane first.

    Crane's own image leads because it is the one that must exist before
    anything else can, and the one a private-registry mirror is most often
    missing. Three callers had their own copy of this two-line expression --
    `images`, the MCP bundle tool, and the live rig's mirror -- which meant the
    crane-first rule and the all_images flag were three edits, and the rig's
    copy is the one no offline test exercises.
    """
    return [facts["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in select_images(facts, all_images=all_images)]


def gui_images_incomplete(facts):
    """True when this bundle needs GUI browser images that no catalogue can
    supply -- a functionalGui location built without a live agent inventory.

    Separate from generation because it is a caveat, not an error: the manifests
    are correct for everything else, and crane resolves a missing key against
    the public registry. That is fine until the cluster is genuinely sealed,
    which is exactly when a private registry is in play -- so callers surface
    this alongside the private-registry options rather than refusing."""
    return bool(from_manual_entry(facts)
                and "gui" in needed_categories(facts.get("func_ids")))


def manual(harbor_id, ship_id, func_ids=None, harbor_name=None):
    """Facts for someone who has the three values BlazeMeter shows them and no
    API access at all -- generating for a customer's cluster from their harbor
    id, ship id and token.

    Deliberately the same shape `gather` returns, so nothing downstream has to
    know which way the facts arrived. What cannot be known is filled from the
    documented defaults rather than left blank: the image catalogue above, and
    crane on `:latest`, which is what a location with no live agent reports
    anyway.

    No validation, by design. The ids are opaque to us, the token is only ever
    written into a Secret, and the whole point is to produce manifests for an
    account nobody here can reach -- so there is nothing to check them against,
    and a check that could only ever be a guess would reject correct input.
    """
    return {
        "harbor_id": harbor_id,
        "harbor_name": harbor_name or None,
        "func_ids": list(func_ids or ["performance"]),
        # Unknown without the API, and only doctor reads them. None is what
        # says so: doctor reports both unknown (a WARN naming where to look)
        # rather than passing a check it could not make -- or failing one the
        # customer could not have satisfied. It tells this case from a real
        # location with the fields unset via from_manual_entry() above.
        "slots": None,
        "threads_per_engine": None,
        # Same reason as the two above -- and the heap especially: unknown here
        # is not "the default 4096", because the one location in an account that
        # has been retuned is exactly the one someone is generating a bundle for.
        "override_cpu": None,
        "override_memory": None,
        "engine_xmx_mb": None,
        "engine_xms_mb": None,
        "ships": [{"id": ship_id, "name": None, "state": None,
                   "installed_version": None, "last_heartbeat": None}],
        "images": [dict(i, size_mb=None) for i in FALLBACK_IMAGES],
        "images_source": MANUAL_SOURCE,
        "crane_image": f"{CRANE_REPO}:latest",
    }


def save(facts, path):
    with open(path, "w") as f:
        json.dump(facts, f, indent=2)


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(
            f"no facts file at '{path}'. Gather one from the account:\n"
            f"  bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>\n"
            f"or drive the generator off the checked-in sample, no account needed:\n"
            f"  bzm-opl-gen generate --facts examples/facts.example.json "
            f"--namespace demo -o out/")
    except json.JSONDecodeError as e:
        raise SystemExit(f"facts file '{path}' is not valid JSON: {e}")
