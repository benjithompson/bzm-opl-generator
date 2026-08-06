"""Gather deployment-relevant facts about a customer's private location.

Facts = everything the generator needs that comes from the BlazeMeter account
rather than from the customer's cluster team:
  - harbor (location) id/name, funcIds (which functionalities are enabled)
  - ships (agents): id, name, installed crane version
  - the images this location runs (ground truth for private-registry
    mirroring), classified performance vs other

Three sources answer that last one and none of them replaces another -- the
location's own image list, a running agent's inventory, and the catalogue below.
`gather` says in `images_source` which of them contributed, and in `image_list`
what happened when the account was asked.
"""

import json

from .api import BzmApiError

# The one directory under the project that holds browser images. Every one of
# them is version-pinned (`charmander/chrome_136.0.7103.113`), which is why no
# catalogue can carry a default for them.
#
# Declared here because it is the key `IMAGE_CATEGORY` classifies browsers by,
# and `browser_images()` picks them out with. It was written twice, and a
# rename on one side left `browser_images()` and `gui_images_incomplete()`
# answering empty while `image_category()` still said `gui` -- a bundle that
# names no browser image and does not flag it.
BROWSER_DIR = "charmander"

# Image classification: substring -> functional category. Anything unmatched
# is a core performance/engine image.
IMAGE_CATEGORY = {
    "doduo": "gui",            # grid proxy (GUI functional / Selenium)
    BROWSER_DIR: "gui",        # browser image (GUI functional)
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


# The category the taurus engine is in, so "does this functionality's agent
# carry an engine?" is a lookup rather than a second list of funcIds.
ENGINE_CATEGORY = "performance"


def runs_engine(func_id):
    """Does this functionality's agent carry a taurus engine?

    Read off CATEGORY_BY_FUNC, which is where the answer already is: the table
    above was read off real single-functionality locations' /versions --
    performance and functionalGui both carry v4, and a mockServices agent
    carries crane, group-gateway and service-mock and no engine at all. The
    frontend kept the same two ids as a literal of its own, which is the copy
    `IGNORED_BY_FORMAT` and the funcId vocabulary are served to avoid; it is on
    each served functionality now.

    A funcId this table does not name -- tdm, delphix, the account's others --
    answers False rather than defaulting, unlike `needed_categories`: this asks
    what an agent carries, and "nothing here knows" is not "it carries an
    engine".
    """
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

# The keys neither live source named. Two sources outrank this one -- the
# location's own image list and a running agent's inventory -- so what is left
# for the catalogue is manual entry, where there is no account to ask, and the
# handful of keys below that *no* /versions response carries.
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
    # Crane's own, and the reason this catalogue is a backstop rather than a
    # fallback. A live *Kubernetes* agent reports both beside the location's
    # images (`torero:4.6.185`, `richrach:1.0.81`, crane 3.7.56) and no
    # /versions response names either -- not the performance location's, not
    # the twelve-resource one's. Every Docker agent read in the same account
    # reports neither, so they belong to the Kubernetes container manager, and
    # Kubernetes is what this tool generates for.
    #
    # So they are not the location's resources and the image list is right not
    # to carry them; they are still keys crane may ask for, and a key it cannot
    # find in a sealed cluster is an ImagePullBackOff mid-test rather than a
    # warning. Left in the performance category, which is where the one live
    # Kubernetes agent that reports them runs its tests: no SV-only Kubernetes
    # agent has been read, so whether an agent with no engine carries them is
    # not something this repo knows.
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
    # comes from its browser funcIds, so there is no defensible default *here*.
    # The location's own image list has one, off the funcIds themselves and
    # without an agent ever starting, which is why a bundle generated with an
    # API key no longer needs this to have an answer; see
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


def key_base(key):
    """A crane image key without its tag -- the part three sources have to agree
    on before one of them can outrank another.

    The tag is what follows the colon *after* the last slash: `localhost:5001/v4`
    is a port, not a tag, and splitting on the first colon leaves `localhost`.
    """
    head, sep, tail = key.rpartition(":")
    return head if sep and "/" not in tail else key


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




# How the read of the location's own image list went -- BzmClient.ship_versions,
# `GET /private-locations/{h}/ships/{s}/versions`. Four answers, and they must
# stay four, because every one of them leaves the same fallback images behind
# and `images` alone cannot say which happened:
#
#   read      the account answered. `count` says how much it named, and 0 is a
#             real answer -- a location whose resources are none.
#   unread    the request was made and failed or was refused. `detail` carries
#             what came back. Never a count: a number here would be the empty
#             answer's.
#   no-agent  the route is per agent and the location has none, so there was
#             nothing to ask. Not a refusal and not an empty answer.
#   not-asked nothing put the question -- manual entry, or a facts file written
#             before this was read at all.
IMAGE_LIST_READ = "read"
IMAGE_LIST_UNREAD = "unread"
IMAGE_LIST_NO_AGENT = "no-agent"
IMAGE_LIST_NOT_ASKED = "not-asked"

# The three sources an image set is built from, named in `images_source` in the
# order they outrank each other.
VERSIONS_SOURCE = "location image list"
INVENTORY_SOURCE = "live agent inventory"
CATALOGUE_SOURCE = "fallback-catalogue"


def image_list_state(facts):
    """Which of the four above these facts record.

    Absent is `not-asked` -- an older facts.json, or the checked-in example, and
    both are honestly "nobody put the question". What it must never become is
    `read`: that would have a bundle report a location as carrying no images
    when nothing had looked.
    """
    return (facts.get("image_list") or {}).get("state") or IMAGE_LIST_NOT_ASKED


def _image_list_entries(body):
    """The image entries a /versions payload names.

    The payload's own map keys are BlazeMeter's resource ids --
    `apmDockerImage`, `blazemeter/charmander/chrome/136` -- and crane resolves
    an override by none of them. `dockerTag` is the key it does resolve by, and
    `dockerTag:version` is exactly the form a live Kubernetes agent reports the
    same image in, which is what lets the two sources be compared at all.

    The repo is the account's own `dockerRegistry` + `imageRelativePath` rather
    than `repo_for_key`'s lookup: this is the source that table was read off, so
    where it speaks it is the authority. An entry naming no path falls back to
    the lookup rather than being dropped -- a key with a resolvable repo is
    still an override crane can use.
    """
    out = []
    for r in (body or {}).get("resources", {}).values():
        tag, version = r.get("dockerTag"), r.get("version")
        if not tag or not version:
            continue
        registry, path = r.get("dockerRegistry"), r.get("imageRelativePath")
        repo = f"{registry.rstrip('/')}/{path}" if registry and path \
            else repo_for_key(tag)
        out.append({"key": f"{tag}:{version}", "repo": repo, "tag": version,
                    # The account states versions, not sizes.
                    "size_mb": None, "category": image_category(repo)})
    return out


# Statuses that settle the image list for the whole location, so the next agent
# is not asked. The route is per agent but the answer is not: a token that may
# not read this location is refused for every agent in it, and a location or a
# key that is gone is gone for all of them. Retried per agent, a dead key costs
# one sequential round trip per agent before the same `unread` comes back --
# and 17-agent locations are ordinary while one real account holds 221 agents,
# which is where per-agent work over a location stops completing at all.
#
# Anything else -- a 5xx, an `{"error": ...}` body with no status behind it --
# is left retryable, because those are the ones a second agent could plausibly
# answer differently.
IMAGE_LIST_SETTLED_BY = (401, 403, 404)


def _read_image_list(client, harbor_id, ships):
    """(entries, state, detail) for the location's own image list.

    `entries` is None wherever the state is not `read`, so that "nothing there"
    has a representation -- the empty list -- that "could not read" cannot
    borrow. A caller iterating it without looking at the state gets a
    TypeError, which is the point.

    Asked of the first agent that answers: every agent in a location answered
    identically, because the set follows the location's funcIds. A refusal from
    all of them is a note, never a failure, since the location itself was read
    fine -- and a refusal that answers for the location (IMAGE_LIST_SETTLED_BY)
    is not re-asked of the rest.
    """
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
    """What the agents themselves report they are holding.

    Two shapes, because the two container managers report differently:

      Docker      ['gcr.io/.../blazemeter/v4:2.4.444', 'taurus-cloud:latest']
                  -- registry-qualified, so the repo is read off it.
      Kubernetes  ['taurus-cloud:latest']
                  -- the bare key only, and Size 0. This is crane's configured
                  image set rather than a listing of what is on the node, so
                  the repo has to be looked up.

    Only the Docker shape used to be handled, which meant every Kubernetes agent
    -- the kind this tool generates for -- silently produced no inventory at all
    and fell through to the catalogue.
    """
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
                # crane's IMAGE_OVERRIDES key, and None where the agent reports
                # a reference with no local tag to read one off. Kept rather
                # than dropped because crane's own image arrives that way on a
                # Docker agent -- there is nothing to override, and the version
                # is still the one to pin the Deployment to.
                "key": key,
                "repo": repo,
                "tag": tag,
                # Kubernetes reports 0 for every image; None says "unknown"
                # rather than claiming an empty image.
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
        # Only a read has a count. `unread` with a 0 in it would be the empty
        # answer wearing the refused one's clothes, which is the whole rule.
        "count": len(resources) if resources is not None else None,
        "detail": detail,
    }

    # Three sources, and the first to name a key keeps it. They are not the same
    # question, which is why none of them replaces another:
    #
    #   the image list  what the *location* is configured to run, exact versions
    #                   included, and answerable before an agent has ever
    #                   started. Where it speaks it is the account itself.
    #   the inventory   what crane on this agent is actually holding. It reaches
    #                   past the location's resources: a live Kubernetes agent
    #                   reports `torero` and `richrach` and no /versions
    #                   response names either.
    #   the catalogue   what any agent carries, read off live inventories. The
    #                   only one of the three that answers with no account at
    #                   all, and the only source for a key the other two are
    #                   silent about -- see FALLBACK_IMAGES.
    entries, sources = {}, []
    inventory = _inventory_entries(ships)

    def take(label, items):
        taken = False
        for e in items:
            # Crane's own image is not one a bundle overrides -- it is the one
            # the Deployment runs -- and an entry with no key is nothing crane
            # could resolve an override by.
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

    # Crane is pinned from the same two live sources in the same order, and
    # identified by its repo: a Docker agent pulling crane from a private mirror
    # reports a reference that is not this one, and that reference is not what a
    # fresh bundle should run.
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


def browser_images(facts):
    """The version-pinned browser images this bundle names, if any."""
    return [i for i in select_images(facts) if BROWSER_DIR in i.get("repo", "")]


def gui_images_incomplete(facts):
    """True when this bundle runs browser tests and names no browser image.

    Read off the images rather than off where they came from. Provenance used to
    stand in for the question -- manual entry meant incomplete, anything else
    meant fine -- and it was a proxy in both directions: a live inventory
    carrying no browser passed, and the location's own image list, which is the
    source that closes the gap, would have passed by simply existing. The
    account names the exact build a location pins, so this is now a fact about
    the images and not about their provenance.

    Separate from generation because it is a caveat, not an error: the manifests
    are correct for everything else, and crane resolves a missing key against
    the public registry. That is fine until the cluster is genuinely sealed,
    which is exactly when a private registry is in play -- so callers surface
    this alongside the private-registry options rather than refusing."""
    return bool("gui" in needed_categories(facts.get("func_ids"))
                and not browser_images(facts))


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
        # No account, so the location's image list was never asked for. Not a
        # refused read and not an empty location -- see image_list_state.
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
            f"  bzm-opl-gen facts --api-key api-key.json --harbor-id <HARBOR_ID>\n"
            f"or drive the generator off the checked-in sample, no account needed:\n"
            f"  bzm-opl-gen generate --facts examples/facts.example.json "
            f"--namespace demo -o out/")
    except json.JSONDecodeError as e:
        raise SystemExit(f"facts file '{path}' is not valid JSON: {e}")
