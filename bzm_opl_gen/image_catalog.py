"""What each BlazeMeter image is for: the functionalities that need it, what
it does, and when an agent pulls it. One table, read by `images --explain`,
`/api/images`, the MCP server and every bundle's IMAGES.md.

Rows are keyed by repository path below the public registry. `verified` is
True only where the image was seen doing that job in a live run; the rest is
inferred from its name, the location's image list or a direct probe, and says
so. No network here: registry reads are registry_client's.
"""

from . import registry_client
from .facts import (BROWSER_DIR, CATALOGUE_SOURCE, CATEGORY_BY_FUNC,
                    CRANE_REPO, ENTRY_SOURCE, FALLBACK_IMAGES,
                    INVENTORY_SOURCE, REGISTRY_NEWEST_SOURCE,
                    VERSIONS_SOURCE, image_category, pinned_tag,
                    select_images)
from .bundle_names import MIRROR_SCRIPT_FILE, PROFILE_FILE
from .bundle_options import ignored_options
from .footprint import PUBLIC_REGISTRY
from .image_registry import HOOK_IMAGE_REPO, HOOK_IMAGE_TAG, mirror_targets
from .markers import is_placeholder

# Categories beside facts.IMAGE_CATEGORY's, for the two images no funcId
# selects: the agent itself and the optional preflight check.
AGENT_CATEGORY = "agent"
HOOK_CATEGORY = "hook"

HOOK_REPO = f"{PUBLIC_REGISTRY}/{HOOK_IMAGE_REPO}"

# Each row's `source`, as facts.gather records it per image.
FROM_VERSIONS = ENTRY_SOURCE[VERSIONS_SOURCE]
FROM_INVENTORY = ENTRY_SOURCE[INVENTORY_SOURCE]
FROM_CATALOGUE = ENTRY_SOURCE[CATALOGUE_SOURCE]

# What a release tag looks like per repository, read off BlazeMeter's
# registry: version parts, the exact suffix (so `-MOB-...` branch builds never
# match), and for the mock images a last part below a bound. `latest` is not
# one: measured, it names releases far older than the newest. torero and
# richrach are absent on purpose: crane asks for them by their `latest` key.
RELEASE_SERIES = {
    "blazemeter/crane": {"parts": 3, "suffix": ""},
    "blazemeter/apm": {"parts": 3, "suffix": ""},
    # Location lists name `-reduced`; the plain 2.4.x engine tag does not exist.
    "blazemeter/v4": {"parts": 3, "suffix": "-reduced"},
    "blazemeter/doduo": {"parts": 3, "suffix": ""},
    "blazemeter/proxy-recorder": {"parts": 3, "suffix": ""},
    # A location listed 6.0.30.4 while CI builds 6.0.30.2221 and up existed,
    # so a four-digit last part is a build, not a release (inferred).
    "blazemeter/service-mock": {"parts": 4, "suffix": "", "last_below": 1000},
    "blazemeter/group-gateway": {"parts": 4, "suffix": "", "last_below": 1000},
    "blazemeter/mock-pc-service": {"parts": 4, "suffix": "", "last_below": 1000},
}


def release_rule(repo):
    """RELEASE_SERIES's rule for a repository (full or below the public
    registry), or None."""
    return RELEASE_SERIES.get(repo_path(repo))


def release_tag(path, tags):
    """The newest tag in `path`'s release series, or None when the repository
    has no series here or no tag in it."""
    return registry_client.newest_release(tags, RELEASE_SERIES.get(path))


def release_repos():
    """The full repositories that have a release series, crane first."""
    return [f"{PUBLIC_REGISTRY}/{p}" for p in RELEASE_SERIES]

UNDESCRIBED = {
    "purpose": "Named by this location's image list. No description is "
               "recorded for it.",
    "pulled_when": "Unknown.",
    "verified": False,
}

# Repository path below PUBLIC_REGISTRY -> description. Customer-facing text.
CATALOG = {
    "blazemeter/crane": {
        "purpose": "The agent itself. It registers with BlazeMeter, receives "
                   "tests and starts the pods or containers that run them.",
        "pulled_when": "When the agent is deployed, and again on each upgrade.",
        "verified": True,
    },
    "blazemeter/v4": {
        "purpose": "The test engine (Taurus with JMeter and the other "
                   "open-source tools). It runs the test script and uploads "
                   "the results.",
        "pulled_when": "Every test run. Crane starts one engine per slot the "
                       "run uses.",
        "verified": True,
    },
    "blazemeter/apm": {
        "purpose": "Application performance monitoring support for engines "
                   "(inferred from its name).",
        "pulled_when": "Not observed in a run. The location's image list "
                       "names it, so mirror it.",
        "verified": False,
    },
    "blazemeter/torero": {
        "purpose": "Not documented by BlazeMeter. Started alone, it exits "
                   "asking for a test id, so it serves a per-test task.",
        "pulled_when": "A Kubernetes agent pulls it ahead of use (it is in "
                       "the agent's image inventory). No observed run starts it.",
        "verified": False,
    },
    "blazemeter/richrach": {
        "purpose": "Not documented by BlazeMeter. Started alone, it exits "
                   "asking for a command, so it runs a task the agent gives it.",
        "pulled_when": "A Kubernetes agent pulls it ahead of use (it is in "
                       "the agent's image inventory). No observed run starts it.",
        "verified": False,
    },
    "blazemeter/service-mock": {
        "purpose": "Serves one virtual service: its transactions, on the "
                   "endpoint the location publishes.",
        "pulled_when": "When a virtual service is deployed to this location.",
        "verified": True,
    },
    "blazemeter/group-gateway": {
        "purpose": "The gateway in front of the virtual services (inferred "
                   "from its name).",
        "pulled_when": "Not observed in a run. The image list of a service "
                       "virtualization location names it.",
        "verified": False,
    },
    "blazemeter/mock-pc-service": {
        "purpose": "A companion service for virtual services (inferred from "
                   "its name only).",
        "pulled_when": "Not observed in a run or in any location's image list.",
        "verified": False,
    },
    "blazemeter/proxy-recorder": {
        "purpose": "The proxy recorder: it records HTTP traffic sent through "
                   "it into a test script.",
        "pulled_when": "When a recording is started on this location (not "
                       "observed).",
        "verified": False,
    },
    "blazemeter/doduo": {
        "purpose": "The Selenium grid proxy for GUI functional tests. It "
                   "connects the engine to the browser pods.",
        "pulled_when": "Every GUI functional run (crane creates a "
                       "doduo-r-gp-* pod).",
        "verified": True,
    },
    "cranehook": {
        "purpose": "A one-shot preflight check: node capacity, egress to "
                   "BlazeMeter and the registries, and the agent's "
                   "permissions. It exits when done.",
        "pulled_when": "Only when the bundle includes the crane-hook check "
                       "(crane_hook). Never on a docker host.",
        "verified": False,
    },
}

# The browser images are one per pinned build (`charmander/chrome_136...`).
BROWSER = {
    "purpose": "One pinned browser build that GUI functional tests drive "
               "through the grid proxy.",
    "pulled_when": "Every GUI functional run that uses this browser (crane "
                   "creates a grid-r-sg-* pod).",
}
# Only a Chrome build has been seen running a real session here.
VERIFIED_BROWSERS = ("chrome_",)


def repo_path(repo):
    """A repository below the public registry as its path (`blazemeter/v4`);
    any other repository keeps everything after its host."""
    if repo.startswith(PUBLIC_REGISTRY + "/"):
        return repo[len(PUBLIC_REGISTRY) + 1:]
    head, sep, rest = repo.partition("/")
    return rest if sep and ("." in head or ":" in head) else repo


def describe(repo):
    """{purpose, pulled_when, verified} for a repository."""
    path = repo_path(repo)
    if f"/{BROWSER_DIR}/" in f"/{path}":
        return {**BROWSER,
                "verified": any(b in path for b in VERIFIED_BROWSERS)}
    return dict(CATALOG.get(path, UNDESCRIBED))


def category(repo):
    """facts.image_category, plus the agent's own and the check's."""
    if repo == CRANE_REPO:
        return AGENT_CATEGORY
    if repo == HOOK_REPO:
        return HOOK_CATEGORY
    return image_category(repo)


def functionalities(cat):
    """The funcIds whose agent runs images of this category. The agent itself
    serves every one; the check serves none."""
    if cat == AGENT_CATEGORY:
        return list(CATEGORY_BY_FUNC)
    return [f for f, cats in CATEGORY_BY_FUNC.items() if cat in cats]


def mutable_tag(tag):
    """True for a tag with no version in it (`latest`): what it names changes
    when BlazeMeter publishes, so a mirror of it goes stale silently."""
    return not any(c.isdigit() for c in tag or "")


def _entry_source(entry, images_source):
    """Where an image entry came from. Older facts carry no per-entry source:
    an entry equal to its catalogue row is the catalogue's, anything else the
    live source the facts name first."""
    if entry.get("source"):
        return entry["source"]
    for fallback in FALLBACK_IMAGES:
        if (fallback["key"], fallback["repo"], fallback["tag"]) == \
                (entry.get("key"), entry.get("repo"), entry.get("tag")):
            return FROM_CATALOGUE
    return _live_source(images_source)


def _labels(images_source):
    """The source labels facts.gather joined into `images_source`."""
    return [p.strip() for p in (images_source or "").split(" + ") if p.strip()]


def _live_source(images_source):
    """The live source older facts name first."""
    labels = _labels(images_source)
    only_inventory = INVENTORY_SOURCE in labels and VERSIONS_SOURCE not in labels
    return FROM_INVENTORY if only_inventory else FROM_VERSIONS


def _crane_source(facts):
    if facts.get("crane_source"):
        return facts["crane_source"]
    if facts["crane_image"].endswith(":latest"):
        return FROM_CATALOGUE
    return _live_source(facts.get("images_source"))


def row(key, repo, tag, source, required):
    """One catalogue row, without anything a registry would tell."""
    cat = category(repo)
    return {"key": key, "repo": repo, "tag": tag, "ref": f"{repo}:{tag}",
            "category": cat, "functionalities": functionalities(cat),
            **describe(repo), "required": required,
            "tag_mutable": mutable_tag(tag), "source": source}


def _crane_row(facts, required):
    tag = facts["crane_image"].rsplit(":", 1)[1]
    return row(f"blazemeter/crane:{tag}", CRANE_REPO, tag, _crane_source(facts),
               required)


def _hook_row(required):
    return row(f"cranehook:{HOOK_IMAGE_TAG}", HOOK_REPO, HOOK_IMAGE_TAG,
               FROM_CATALOGUE, required)


def location_rows(facts, all_images=False):
    """Crane, then the images the location's funcIds select (`required`
    True), then with `all_images` every other image (`required` False)."""
    src = facts.get("images_source")
    selected = select_images(facts)
    chosen = {id(i) for i in selected}
    rows = [_crane_row(facts, True)]
    rows += [row(i["key"], i["repo"], i["tag"], _entry_source(i, src), True)
             for i in selected]
    if all_images:
        rows += [row(i["key"], i["repo"], i["tag"], _entry_source(i, src), False)
                 for i in facts["images"] if i.get("key") and id(i) not in chosen]
        rows.append(_hook_row(False))
    return rows


def bundle_rows(facts, o):
    """The rows one bundle's agent pulls: the location's, plus crane-hook's
    where the bundle carries the check."""
    rows = location_rows(facts)
    if o.get("crane_hook") and "crane_hook" not in ignored_options(o):
        rows.append(_hook_row(True))
    return rows


def catalogue_rows(release_pins=None):
    """Every image the catalogue knows, for no location in particular:
    `required` is None. `release_pins` moves each pinned image to the newest
    release. Browser images are pinned per location, so none."""
    def pinned(key, repo, tag):
        newest = pinned_tag(release_pins, repo)
        return (row(key, repo, newest, REGISTRY_NEWEST_SOURCE, None) if newest
                else row(key, repo, tag, FROM_CATALOGUE, None))
    rows = [pinned("blazemeter/crane:latest", CRANE_REPO, "latest")]
    rows += [pinned(i["key"], i["repo"], i["tag"]) for i in FALLBACK_IMAGES]
    rows.append(_hook_row(None))
    return rows


def functionality_cell(r):
    """A row's functionalities as a table cell."""
    if r["category"] == AGENT_CATEGORY:
        return "every"
    if r["category"] == HOOK_CATEGORY:
        return "preflight check"
    return ", ".join(f"`{f}`" for f in r["functionalities"]) or "none"


def cell(text):
    """Text safe inside a Markdown table cell."""
    return str(text).replace("|", "\\|")


# How IMAGES.md names where the versions came from.
ORIGIN_WORDS = {FROM_VERSIONS: "the location's own image list",
                FROM_INVENTORY: "a running agent's image inventory",
                REGISTRY_NEWEST_SOURCE: "the newest releases in BlazeMeter's "
                                        "registry, not your location",
                FROM_CATALOGUE: "this tool's built-in list"}


def _not_from_location(rows):
    """IMAGES.md paragraphs for versions that were not read from the
    location: pinned to the newest release, or left on a stale `latest`."""
    out = ""
    if any(r["source"] == REGISTRY_NEWEST_SOURCE for r in rows):
        out += (
            "\n**These versions were not read from your location.** They are "
            "the newest releases\nin BlazeMeter's registry when the facts were "
            "made. A location can ask for an\nolder release than the newest (a "
            "location has been seen listing an engine several\nreleases "
            "behind), and then a mirror built from this file lacks the image "
            "the\nagent asks for. Before you rely on a mirror, do one of "
            "these: connect an API key\nand re-generate (the location's own "
            "image list is exact), put a pull-through\ncache in front of "
            "BlazeMeter's registry, or run the `--verify` check below once\n"
            "the agent is online.\n")
    stale = [r for r in rows if r["tag_mutable"]
             and repo_path(r["repo"]) in RELEASE_SERIES]
    if stale:
        out += (
            "\n**`latest` is not the newest release.** On BlazeMeter's registry "
            "`latest` names\nreleases far older than the newest; for the test "
            "engine it was an earlier major\nversion when this was checked. "
            "These facts name no release for\n"
            + ", ".join(f"`{repo_path(r['repo'])}`" for r in stale)
            + ", so they keep `latest` (BlazeMeter's registry\ndid not answer "
            "when the facts were made, or was not asked). Connect an API key\n"
            "and re-generate: the "
            "location's own image list gives\nthe exact versions.\n")
    return out


def _origin(rows):
    """Where the rows' versions came from, in words, in precedence order."""
    named = {r["source"] for r in rows}
    return ", then ".join(w for src, w in ORIGIN_WORDS.items() if src in named)


def images_md(facts, o):
    """IMAGES.md: the images this bundle's agent pulls, what each is for, how
    to mirror them and how to keep a mirror current. Written from the facts
    alone -- no registry is asked, so no digest or size appears."""
    rows = bundle_rows(facts, o)
    reg = (o.get("private_registry") or "").rstrip("/")
    harbor = facts.get("harbor_id")
    harbor_arg = "<harbor-id>" if is_placeholder(harbor) else harbor
    name = facts.get("harbor_name") or harbor_arg
    funcs = ", ".join(f"`{f}`" for f in facts.get("func_ids") or []) or "none"

    table = ["| Image | What it does | Functionality | When it is pulled | Seen in a live run |",
             "|---|---|---|---|---|"]
    for r in rows:
        ref = f"`{r['ref']}`" + (" (floating tag)" if r["tag_mutable"] else "")
        table.append(f"| {ref} | {cell(r['purpose'])} | {functionality_cell(r)} "
                     f"| {cell(r['pulled_when'])} | {'yes' if r['verified'] else 'no'} |")
    floating = [r for r in rows if r["tag_mutable"]]
    floating_note = ""
    if floating:
        floating_note = (
            "\nA **floating tag** (`latest`) names whatever BlazeMeter published "
            "last. A copy in\nyour registry keeps the version it was copied at, "
            "so it goes stale without any\nerror. Re-copy these images whenever "
            "you re-copy the others.\n")

    if reg:
        dest = ["| BlazeMeter image | Push to |", "|---|---|"] + [
            f"| `{ref}` | `{target}` |" for ref, target in mirror_targets(facts, o)]
        mirror = (
            f"This bundle pulls from `{reg}`. Copy the images there before you "
            f"deploy:\n\n```\n./{MIRROR_SCRIPT_FILE}\n```\n\nIt needs docker "
            f"and a `docker login` to `{reg.split('/')[0]}`. It pushes each "
            f"image to the\nname the agent asks for, so keep these names:\n\n"
            + "\n".join(dest) + "\n")
        verify_reg = reg
    else:
        mirror = (
            f"This bundle pulls from BlazeMeter's public registry, "
            f"`{PUBLIC_REGISTRY}`, which\nneeds no credentials. To pull from "
            f"your own registry instead, re-generate with\n`--private-registry "
            f"<registry>`. The bundle then carries `{MIRROR_SCRIPT_FILE}`,\n"
            f"which copies these images to the names the agent asks for.\n")
        verify_reg = "<registry>"

    return f"""# Images for {name}

The images the BlazeMeter agent for location `{harbor_arg}` pulls. The location
is enabled for {funcs}. The versions come from {_origin(rows)}.

{chr(10).join(table)}
{floating_note}{_not_from_location(rows)}
## Mirroring

{mirror}
## Keeping a mirror current

Versioned tags follow BlazeMeter's releases. When BlazeMeter publishes a new
engine, the location asks for the new tag, and an agent that pulls from a mirror
fails until the mirror has it. After a BlazeMeter release, or after you
re-generate this bundle, check the mirror:

```
bzm-opl-gen images --api-key api-key.json --harbor-id {harbor_arg} \\
    --verify {verify_reg} --profile {PROFILE_FILE}
```

It reports each image as present, missing or unread, and exits 1 when one is
missing. Without an API key, pass the facts file this bundle was generated from
with `--facts` instead of `--api-key` and `--harbor-id`.

This file was written without network access, so it carries no digests or
sizes. `bzm-opl-gen images --explain --lookup` reads them from BlazeMeter's
registry, with the newest tag published for each image.
"""
