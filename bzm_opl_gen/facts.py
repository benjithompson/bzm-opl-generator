"""Facts about a private location that come from the BlazeMeter account: the
location and its funcIds, its agents, and the images it runs.

Images come from three sources, per key and in order: the location's own image
list, a running agent's inventory, and the catalogue below. `images_source`
names which contributed; `image_list` says how the account read went.
"""

import json

from .api import DEFAULT_FUNC_IDS, BzmApiError
from .markers import or_marker

# Where the version-pinned browser images live (`charmander/chrome_136...`);
# no catalogue can carry a default for them.
BROWSER_DIR = "charmander"

# Repo substring -> category; anything unmatched is a performance image.
IMAGE_CATEGORY = {
    "doduo": "gui",            # grid proxy (GUI functional / Selenium)
    BROWSER_DIR: "gui",        # browser image (GUI functional)
    "service-mock": "mock",
    "mock-pc-service": "mock",
    "group-gateway": "mock",   # mock services gateway
    "proxy-recorder": "recorder",
}

# funcId -> the image categories its agent runs, read off real locations'
# /versions. Other funcIds (tdm, delphix, "chrome:default", ...) add none.
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
    """One funcId per distinct image set (declaration order picks it), for a
    form where the choice only decides which images a bundle names."""
    seen, out = set(), []
    for func, cats in CATEGORY_BY_FUNC.items():
        key = frozenset(cats)
        if key not in seen:
            seen.add(key)
            out.append(func)
    return out


ENGINE_CATEGORY = "performance"


def runs_engine(func_id):
    """Does this functionality's agent carry a taurus engine? An unknown
    funcId (tdm, delphix, ...) answers False."""
    return ENGINE_CATEGORY in CATEGORY_BY_FUNC.get(func_id, set())


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


# Images for keys neither live source names. Keys are what crane resolves
# IMAGE_OVERRIDES by; repos were read off live inventories and follow no naming
# rule (taurus-cloud is `v4`, apm-image is `apm`).
FALLBACK_IMAGES = [
    # performance: the taurus engine and its APM sidecar.
    {"key": "taurus-cloud:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/v4", "tag": "latest", "category": "performance"},
    {"key": "apm-image:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/apm", "tag": "latest", "category": "performance"},
    # Kubernetes crane pulls these, and no /versions response names them.
    {"key": "torero:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/torero", "tag": "latest", "category": "performance"},
    {"key": "richrach:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/richrach", "tag": "latest", "category": "performance"},
    # mock services.
    {"key": "blazemeter/service-mock:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/service-mock", "tag": "latest", "category": "mock"},
    {"key": "blazemeter/group-gateway:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/group-gateway", "tag": "latest", "category": "mock"},
    # Not observed live; follows the regular `blazemeter/<name>` shape.
    {"key": "blazemeter/mock-pc-service:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/mock-pc-service", "tag": "latest", "category": "mock"},
    # proxy recorder.
    {"key": "blazemeter/proxy-recorder:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/proxy-recorder", "tag": "latest", "category": "recorder"},
    # GUI functional: the grid proxy (browsers come from the image list).
    {"key": "blazemeter/doduo:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/doduo", "tag": "latest", "category": "gui"},
]

CRANE_REPO = "gcr.io/verdant-bulwark-278/blazemeter/crane"
BLAZEMETER_PROJECT = "gcr.io/verdant-bulwark-278/blazemeter"

# Keys, seen in live inventories, whose repo is not their own name.
KEY_REPO_EXCEPTIONS = {
    "blazemeter": "v3",
    "secrets-image": "secrets",
}


def key_base(key):
    """A crane image key without its tag. The tag follows the last colon only
    if no slash comes after it (`localhost:5001/v4` has a port, not a tag)."""
    head, sep, tail = key.rpartition(":")
    return head if sep and "/" not in tail else key


def repo_for_key(key):
    """The repo a crane image key resolves to: the catalogue, then the
    exceptions, then `<project>/<path>` (the whole path, minus a redundant
    leading `blazemeter/`). A name starting with a host is returned as is."""
    name = key_base(key)
    for i in FALLBACK_IMAGES:
        if i["key"].split(":", 1)[0] == name:
            return i["repo"]
    first = name.split("/", 1)[0]
    if "." in first or ":" in first:
        return name
    if name.startswith("blazemeter/"):
        name = name[len("blazemeter/"):]
    return f"{BLAZEMETER_PROJECT}/{KEY_REPO_EXCEPTIONS.get(name, name)}"


# How the read of the location's image list (`GET .../ships/{s}/versions`)
# went. All four leave the same fallback images, so the state tells them apart:
#   read      the account answered; `count` may be 0.
#   unread    the request failed or was refused; `detail` says how. Never a count.
#   no-agent  the route is per agent and the location has none.
#   not-asked nothing asked -- manual entry, or an older facts file.
IMAGE_LIST_READ = "read"
IMAGE_LIST_UNREAD = "unread"
IMAGE_LIST_NO_AGENT = "no-agent"
IMAGE_LIST_NOT_ASKED = "not-asked"

# The image sources, highest precedence first.
VERSIONS_SOURCE = "location image list"
INVENTORY_SOURCE = "live agent inventory"
CATALOGUE_SOURCE = "fallback-catalogue"


def image_list_state(facts):
    """Which image-list state these facts record; absent is `not-asked`."""
    return (facts.get("image_list") or {}).get("state") or IMAGE_LIST_NOT_ASKED


def _image_list_entries(body):
    """The image entries a /versions payload names, keyed `dockerTag:version`
    (crane's key), repo from `dockerRegistry/imageRelativePath`."""
    out = []
    for r in (body or {}).get("resources", {}).values():
        tag, version = r.get("dockerTag"), r.get("version")
        if not tag or not version:
            continue
        registry, path = r.get("dockerRegistry"), r.get("imageRelativePath")
        repo = f"{registry.rstrip('/')}/{path}" if registry and path \
            else repo_for_key(tag)
        out.append({"key": f"{tag}:{version}", "repo": repo, "tag": version,
                    "size_mb": None, "category": image_category(repo)})
    return out


# Statuses that answer for the whole location, so later agents are not asked;
# other failures are retried on the next agent.
IMAGE_LIST_SETTLED_BY = (401, 403, 404)


def _read_image_list(client, harbor_id, ships):
    """(entries, state, detail) for the location's image list, asked of the
    first agent that answers. `entries` is None unless the state is `read`."""
    if not ships:
        return None, IMAGE_LIST_NO_AGENT, ("the image list is served per agent "
                                           "and this location has none")
    refusal = None
    for ship in ships:
        try:
            body = client.ship_versions(harbor_id, ship["id"])
        except BzmApiError as e:
            refusal = str(e)
            if e.status in IMAGE_LIST_SETTLED_BY:
                break
            continue
        return _image_list_entries(body), IMAGE_LIST_READ, None
    return None, IMAGE_LIST_UNREAD, refusal


def _inventory_entries(ships):
    """What the agents report holding: registry-qualified tags on Docker, bare
    keys (repo looked up, Size 0) on Kubernetes."""
    out, seen = [], set()
    for ship in ships:
        info = (ship.get("hostInfo") or {}).get("containerManager", {}).get("info", {})
        for img in info.get("images", []):
            tags = img.get("RepoTags") or []
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
            out.append({
                # None for crane's own image on Docker: nothing to override,
                # but its tag still pins the Deployment.
                "key": key,
                "repo": repo,
                "tag": tag,
                # Kubernetes reports 0 for every image: unknown.
                "size_mb": round(img["Size"] / 1e6) if img.get("Size") else None,
                "category": image_category(repo),
            })
    return out


def gather(client, harbor_id):
    harbor = client.private_location(harbor_id)
    ships = harbor.get("ships", [])
    facts = {
        "harbor_id": harbor["id"],
        "harbor_name": harbor.get("name"),
        "func_ids": harbor.get("funcIds", []),
        "slots": harbor.get("slots"),
        # Unset, every test start 403s; doctor FAILs on it.
        "threads_per_engine": harbor.get("threadsPerEngine"),
        # The engine's requests (whole cores; memory carried verbatim, read as
        # Mi), from which unset engine limits are derived.
        "override_cpu": harbor.get("overrideCPU"),
        "override_memory": harbor.get("overrideMemory"),
        # The engine's JVM heap, which doctor checks against its limit.
        "engine_xmx_mb": harbor.get("engineXmx"),
        "engine_xms_mb": harbor.get("engineXms"),
        "ships": [{
            "id": ship["id"],
            "name": ship.get("name"),
            "state": ship.get("state"),
            "installed_version": ship.get("installedVersion"),
            "last_heartbeat": ship.get("lastHeartBeat"),
        } for ship in ships],
    }
    resources, state, detail = _read_image_list(client, harbor_id, ships)
    facts["image_list"] = {
        "state": state,
        "count": len(resources) if resources is not None else None,
        "detail": detail,
    }

    # Per key, the first source to name it keeps it.
    entries, sources = {}, []
    inventory = _inventory_entries(ships)

    def take(label, items):
        taken = False
        for e in items:
            # Crane's image is not overridden, and a keyless entry cannot be.
            if not e["key"] or e["repo"] == CRANE_REPO:
                continue
            base = key_base(e["key"])
            if base in entries:
                continue
            entries[base] = e
            taken = True
        if taken:
            sources.append(label)

    take(VERSIONS_SOURCE, resources or [])
    take(INVENTORY_SOURCE, inventory)
    take(CATALOGUE_SOURCE, [dict(i, size_mb=None) for i in FALLBACK_IMAGES])

    # Crane is pinned from a live source, matched by the public repo.
    facts["crane_image"] = next(
        (f"{CRANE_REPO}:{e['tag']}"
         for source in (resources or [], inventory)
         for e in source if e["repo"] == CRANE_REPO),
        f"{CRANE_REPO}:latest")
    facts["images"] = list(entries.values())
    facts["images_source"] = " + ".join(sources)
    return facts


MANUAL_SOURCE = "manual entry (no account access)"


def from_manual_entry(facts):
    """True when these facts were typed in rather than read from the account
    (doctor's way to tell "not asked" from "unset"; generation never asks)."""
    return facts.get("images_source") == MANUAL_SOURCE


def image_refs(facts, all_images=False):
    """Every image reference this location's bundle will pull, crane first
    (it must exist before anything else can)."""
    return [facts["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in select_images(facts, all_images=all_images)]


def browser_images(facts):
    """The version-pinned browser images this bundle names, if any."""
    return [i for i in select_images(facts) if BROWSER_DIR in i.get("repo", "")]


def gui_images_incomplete(facts):
    """True when this bundle runs browser tests and names no browser image --
    a caveat that bites only behind a private registry."""
    return bool("gui" in needed_categories(facts.get("func_ids"))
                and not browser_images(facts))


def manual(harbor_id, ship_id, func_ids=None, harbor_name=None):
    """Facts from typed ids, for an account this tool cannot reach: the shape
    `gather` returns, with catalogue images and crane `:latest`. Ids are not
    validated; a blank one becomes its marker (`<HARBOR_ID>`, `<SHIP_ID>`)."""
    return {
        "harbor_id": or_marker(harbor_id, "harbor_id"),
        "harbor_name": harbor_name or None,
        "func_ids": list(func_ids or DEFAULT_FUNC_IDS),
        # Unknown without the account; doctor reports them as such.
        "slots": None,
        "threads_per_engine": None,
        "override_cpu": None,
        "override_memory": None,
        "engine_xmx_mb": None,
        "engine_xms_mb": None,
        "ships": [{"id": or_marker(ship_id, "ship_id"), "name": None,
                   "state": None, "installed_version": None,
                   "last_heartbeat": None}],
        "images": [dict(i, size_mb=None) for i in FALLBACK_IMAGES],
        "images_source": MANUAL_SOURCE,
        "image_list": {"state": IMAGE_LIST_NOT_ASKED, "count": None,
                       "detail": "no account access, so the location's image "
                                 "list was never asked for"},
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
            f"  bzm-opl-gen facts --api-key api-key.json --harbor-id <harbor-id>\n"
            f"or drive the generator off the checked-in sample, no account needed:\n"
            f"  bzm-opl-gen generate --facts examples/facts.example.json "
            f"--namespace demo -o out/")
    except json.JSONDecodeError as e:
        raise SystemExit(f"facts file '{path}' is not valid JSON: {e}")
