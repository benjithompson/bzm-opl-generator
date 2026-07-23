"""Gather deployment-relevant facts about a customer's private location.

Facts = everything the generator needs that comes from the BlazeMeter account
rather than from the customer's cluster team:
  - harbor (location) id/name, funcIds (which features are enabled)
  - ships (agents): id, name, installed crane version
  - actual image inventory reported by running agents (ground truth for
    private-registry mirroring), classified performance vs other
"""

import json

# Image classification: substring -> reason it is NOT needed for pure
# performance testing.
NON_PERFORMANCE = {
    "doduo": "grid proxy (GUI functional / Selenium)",
    "charmander": "browser image (GUI functional)",
    "service-mock": "mock services",
    "mock-pc-service": "mock services",
    "sv-bridge": "service virtualization",
    "proxy-recorder": "proxy recorder",
    "group-gateway": "mock services gateway",
}

# Fallback catalogue when no agent has run yet (no inventory to read).
# Keys are the local tags crane expects; repos are under gcr.io/verdant-bulwark-278.
FALLBACK_IMAGES = [
    {"key": "taurus-cloud:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/v4", "tag": "latest", "performance": True},
    {"key": "apm-image:latest", "repo": "gcr.io/verdant-bulwark-278/blazemeter/apm", "tag": "latest", "performance": True},
]

CRANE_REPO = "gcr.io/verdant-bulwark-278/blazemeter/crane"


def _classify(ref):
    for key, why in NON_PERFORMANCE.items():
        if key in ref:
            return why
    return None


def gather(client, harbor_id):
    harbor = client.private_location(harbor_id)
    facts = {
        "harbor_id": harbor["id"],
        "harbor_name": harbor.get("name"),
        "func_ids": harbor.get("funcIds", []),
        "slots": harbor.get("slots"),
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
                "performance": _classify(gcr[0]) is None,
                "excluded_reason": _classify(gcr[0]),
            }
            if repo == CRANE_REPO:
                facts["crane_image"] = f"{repo}:{tag}"
            else:
                facts["images"].append(entry)
    if not facts["images"]:
        facts["images"] = [dict(i, size_mb=None, excluded_reason=None) for i in FALLBACK_IMAGES]
        facts["images_source"] = "fallback-catalogue (no agent inventory yet)"
    else:
        facts["images_source"] = "live agent inventory"
    if not facts["crane_image"]:
        facts["crane_image"] = f"{CRANE_REPO}:latest"
    return facts


def save(facts, path):
    with open(path, "w") as f:
        json.dump(facts, f, indent=2)


def load(path):
    with open(path) as f:
        return json.load(f)
