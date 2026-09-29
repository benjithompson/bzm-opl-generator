"""Where a bundle's images are pulled from, and the script that mirrors them.

Crane composes image names itself, per platform: on Kubernetes
`${DOCKER_REGISTRY}/<repo path>:<tag>` (measured: it does not resolve
IMAGE_OVERRIDES for the engine), on docker `${DOCKER_REGISTRY}/<key without its
tag>:latest`. The mirror pushes exactly those names and IMAGE_OVERRIDES carries
them, so one bundle's files cannot disagree. Crane's own image is named by the
bundle, not composed.
"""

import shlex

from .bundle_options import ignored_options
from .facts import image_refs, key_base, select_images
from .footprint import PUBLIC_REGISTRY


# crane-hook's image, from BlazeMeter's registry, so a private-registry bundle
# mirrors it too.
HOOK_IMAGE_REPO = "cranehook"
HOOK_IMAGE_TAG = "latest"


def crane_image(facts, o):
    """Crane's own image: the location's, moved to the private registry where
    one is set. The bundle names it itself, so nothing composes it.
    """
    if not o["private_registry"]:
        return facts["crane_image"]
    tag = facts["crane_image"].rsplit(":", 1)[1]
    return f"{o['private_registry'].rstrip('/')}/crane:{tag}"


def image_overrides(facts, o):
    """IMAGE_OVERRIDES as {crane key: composed reference}, for the images the
    location's funcIds select.
    """
    targets = cluster_composed_targets(facts, o)
    return {i["key"]: targets[f"{i['repo']}:{i['tag']}"]
            for i in select_images(facts)}


def composed_image_ref(repo, tag, registry):
    """Where a Kubernetes crane looks for one image mirrored into `registry`.

    The registry replaces PUBLIC_REGISTRY and the whole path below it is kept
    (`blazemeter/`, `charmander/`, ...): every segment is a real directory. A
    repo outside PUBLIC_REGISTRY keeps everything after its host, which is a
    shape rather than a measured behaviour.
    """
    if repo.startswith(PUBLIC_REGISTRY + "/"):
        path = repo[len(PUBLIC_REGISTRY) + 1:]
    else:
        head, sep, rest = repo.partition("/")
        path = rest if sep and ("." in head or ":" in head) else repo
    return f"{registry.rstrip('/')}/{path}:{tag}"


def cluster_composed_targets(facts, o):
    """{pinned public ref: composed reference} for every image a Kubernetes
    crane pulls. Empty for docker or without a private registry.

    Tags stay pinned, since IMAGE_OVERRIDES names one. Only the engine
    reference was observed live; the value is the composed name, which pulls
    whether crane ignores the override or looks it up under another key.
    """
    if o.get("output_format") == "docker" or not o["private_registry"]:
        return {}
    return {f"{i['repo']}:{i['tag']}":
            composed_image_ref(i["repo"], i["tag"], o["private_registry"])
            for i in select_images(facts)}


def docker_composed_targets(facts, o):
    """{pinned public ref: composed reference} for every image a docker crane
    creates. Empty for the cluster formats or without a private registry.

    Crane composes from the key and asks for `latest`; there is no
    IMAGE_OVERRIDES on this platform. The pinned ref records which version the
    pushed `latest` is. Without a registry crane's default prefix is
    unmeasured, so nothing is guessed.
    """
    if o.get("output_format") != "docker" or not o["private_registry"]:
        return {}
    registry = o["private_registry"].rstrip("/")
    return {f"{i['repo']}:{i['tag']}": f"{registry}/{key_base(i['key'])}:latest"
            for i in select_images(facts)}


def mirror_script(facts, o):
    """A script that pulls BlazeMeter's images and pushes them into the private
    registry.

    It stands alone: pulling needs no credentials, pushing uses the docker
    login already in place. Crane goes first because it is small, so a registry
    that refuses the push fails fast.
    """
    refs = image_refs(facts)
    # The hook's image is not in the location's inventory. Docker ignores
    # crane_hook, so it is not mirrored there.
    if o["crane_hook"] and "crane_hook" not in ignored_options(o):
        refs = refs + [f"{PUBLIC_REGISTRY}/{HOOK_IMAGE_REPO}:{HOOK_IMAGE_TAG}"]
    reg = o["private_registry"].rstrip("/")
    host = reg.split("/")[0]
    # Only the `mirror` calls run anything, and they quote what they are given;
    # the rest is comments and echo text.
    lines = [
        "#!/usr/bin/env bash",
        f"# Mirror the images this BlazeMeter private location needs into {reg}.",
        "#",
        f"# Location: {facts.get('harbor_name') or facts['harbor_id']} ({facts['harbor_id']})",
        f"# Images from: {facts.get('images_source')}",
        "#",
        "# Pulling needs no credentials. Pushing uses your existing docker login:",
        f"#     docker login {host}",
        "#",
        "# The images are amd64-only, hence --platform.",
        "set -euo pipefail",
        "",
        'command -v docker >/dev/null || { echo "docker not found on PATH" >&2; exit 1; }',
        "",
        "# crane goes first and is the smallest, so a refused push fails fast.",
        "mirror() {",
        '  echo "--> $2"',
        '  docker pull --platform linux/amd64 "$1"',
        '  docker tag "$1" "$2"',
        '  if ! docker push "$2"; then',
        "    echo >&2",
        f'    echo "push to {reg} failed (the real error is above)." >&2',
        f'    echo "if it is an authentication error:  docker login {host}" >&2',
        "    exit 1",
        "  fi",
        "}",
        "",
    ]
    # The destination is what crane composes on this platform (see the module
    # docstring).
    composed = docker_composed_targets(facts, o)
    if composed:
        lines += [
            "# Destinations:",
            "#   crane               <registry>/crane:<version>",
            "#   everything else     <registry>/blazemeter/<name>:latest",
            "# Keep these names: crane builds them from DOCKER_REGISTRY, and",
            "# README.md tells you to `docker pull` them on the agent host.",
            "",
        ]
    else:
        composed = cluster_composed_targets(facts, o)
        lines += [
            "# Destinations:",
            "#   crane, crane-hook   <registry>/<name>:<version>",
            "#   everything else     <registry>/blazemeter/<path>:<version>",
            "# Keep these names: crane builds them from DOCKER_REGISTRY, and they",
            "# match IMAGE_OVERRIDES in this bundle.",
            "",
        ]
    for ref in refs:
        name = ref.rsplit("/", 1)[-1]
        dest = composed.get(ref, f"{reg}/{name}")
        lines.append(f"mirror {shlex.quote(ref)} {shlex.quote(dest)}")
    lines += ["", f'echo "done -- {len(refs)} images in {reg}"']
    return "\n".join(lines) + "\n"
