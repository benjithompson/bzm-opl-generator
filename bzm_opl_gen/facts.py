"""Gather deployment-relevant facts about a customer's private location.

Facts = everything the generator needs that comes from the BlazeMeter account
rather than from the customer's cluster team:
  - harbor (location) id/name, funcIds (which features are enabled)
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
# account actually reports. Most keys map to a repo of the same name, but four
# do not -- taurus-cloud is `v4`, blazemeter is `v3`, apm-image is `apm`,
# secrets-image is `secrets` -- so this is a table read off live inventories
# across the account rather than a naming rule.
FALLBACK_IMAGES = [
    # performance: the taurus engine and its APM sidecar.
    {"key": "taurus-cloud:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/v4", "tag": "latest", "category": "performance"},
    {"key": "apm-image:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/apm", "tag": "latest", "category": "performance"},
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
            gcr = [t for t in tags if t.startswith("gcr.io/")]
            local = [t for t in tags if not t.startswith("gcr.io/") and t.endswith(":latest")]
            if not gcr or gcr[0] in seen:
                continue
            seen.add(gcr[0])
            repo, tag = gcr[0].rsplit(":", 1)
            entry = {
                "key": local[0] if local else None,  # crane's IMAGE_OVERRIDES key
                "repo": repo,
                "tag": tag,
                "size_mb": round((img.get("Size") or 0) / 1e6),
                "category": image_category(gcr[0]),
            }
            if repo == CRANE_REPO:
                facts["crane_image"] = f"{repo}:{tag}"
            else:
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


def gui_images_incomplete(facts):
    """True when this bundle needs GUI browser images that no catalogue can
    supply -- a functionalGui location built without a live agent inventory.

    Separate from generation because it is a caveat, not an error: the manifests
    are correct for everything else, and crane resolves a missing key against
    the public registry. That is fine until the cluster is genuinely sealed,
    which is exactly when a private registry is in play -- so callers surface
    this alongside the private-registry options rather than refusing."""
    return bool(
        facts.get("images_source") == MANUAL_SOURCE
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
        # Unknown without the API, and only doctor reads them -- it reports
        # "unknown" rather than passing a check it could not make.
        "slots": None,
        "threads_per_engine": None,
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
