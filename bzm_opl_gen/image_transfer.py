"""Carrying BlazeMeter's images to a site that cannot reach gcr.io: each image
saved to an archive file on a connected machine, the files carried across,
then pushed on the far side to the names the agent asks for.

This module is the plan: the commands, the archive names and the manifest.
It runs nothing and reads no network; core does both.

skopeo is preferred: it copies registry to file and file to registry without
a daemon, takes a CA directory and an auth file per command, and keeps the
layers compressed (`oci-archive`). docker is the fallback (`docker save`,
which writes the layers uncompressed, so its archives are larger).
"""

import hashlib
import json
import re
import shlex
import shutil

from . import generate
from .footprint import PUBLIC_REGISTRY
from .image_registry import mirror_targets

MANIFEST_FILE = "images-manifest.json"
SUMS_FILE = "SHA256SUMS"
MANIFEST_FORMAT = "bzm-opl-gen/images/1"

SKOPEO = "skopeo"
DOCKER = "docker"
TOOLS = (SKOPEO, DOCKER)

OCI_ARCHIVE = "oci-archive"
DOCKER_ARCHIVE = "docker-archive"
ARCHIVE_FORMAT = {SKOPEO: OCI_ARCHIVE, DOCKER: DOCKER_ARCHIVE}
SUFFIX = {OCI_ARCHIVE: ".oci.tar", DOCKER_ARCHIVE: ".docker.tar"}
# The tools that can push each kind of archive, preferred first.
LOADERS = {OCI_ARCHIVE: (SKOPEO,), DOCKER_ARCHIVE: (SKOPEO, DOCKER)}

# BlazeMeter's images are amd64-only.
PLATFORM = "linux/amd64"

# skopeo retries a failed blob this many times before it gives up.
RETRY_TIMES = "3"

# A host nobody resolves, stripped again to leave the name below the registry.
_NO_REGISTRY = "registry.invalid"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def available():
    """The transfer tools on PATH."""
    return {t for t in TOOLS if shutil.which(t)}


def choose(wanted, have, allowed=TOOLS):
    """The tool to use, or None. `wanted` is a tool name or None for the
    first of `allowed` that is on PATH."""
    if wanted:
        return wanted if wanted in have and wanted in allowed else None
    return next((t for t in allowed if t in have), None)


# -- names -----------------------------------------------------------------------

def target_paths(facts, o, all_images=False):
    """[(public ref, name below the registry)] from mirror_targets, in its
    order. The registry prefix is added when the images are pushed."""
    probe = {**o, "private_registry": _NO_REGISTRY}
    head = _NO_REGISTRY + "/"
    return [(ref, target[len(head):])
            for ref, target in mirror_targets(facts, probe, all_images)]


def archive_name(ref, fmt, taken=()):
    """A file name for one image's archive: its path and tag with every
    character a file system might refuse replaced, unique within `taken`."""
    if ref.startswith(PUBLIC_REGISTRY + "/"):
        path = ref[len(PUBLIC_REGISTRY) + 1:]
    else:
        head, _, rest = ref.partition("/")
        path = rest if rest and ("." in head or ":" in head) else ref
    stem = _UNSAFE.sub("_", path.replace(":", "_")).strip("._") or "image"
    name, n = stem + SUFFIX[fmt], 2
    while name in taken:
        name, n = f"{stem}-{n}{SUFFIX[fmt]}", n + 1
    return name


def public_options(options):
    """The options the manifest records, without a secret."""
    return {k: v for k, v in (options or {}).items()
            if k not in generate.SECRET_OPTIONS}


# -- commands --------------------------------------------------------------------

def command(argv, env=None):
    """One command to run: argv, extra environment, and the text printed."""
    env = env or {}
    text = " ".join([f"{k}={shlex.quote(v)}" for k, v in env.items()]
                    + [shlex.join(argv)])
    return {"argv": argv, "env": env, "text": text}


def _skopeo(*args, work_dir):
    # TMPDIR: skopeo unpacks an archive there, so it lands on the disk that
    # was checked rather than on a small /var/tmp.
    os_, arch = PLATFORM.split("/")
    return command([SKOPEO, "--override-os", os_, "--override-arch", arch,
                    "copy", "--retry-times", RETRY_TIMES, *args],
                   {"TMPDIR": work_dir})


def save_commands(tool, ref, digest, archive, work_dir):
    """The commands that copy one image into `archive`. skopeo pulls by
    digest when one was read, so the file holds what the manifest names."""
    if tool == SKOPEO:
        source = f"{ref.rsplit(':', 1)[0]}@{digest}" if digest else ref
        return [_skopeo(f"docker://{source}", f"{OCI_ARCHIVE}:{archive}",
                        work_dir=work_dir)]
    return [command([DOCKER, "pull", "--platform", PLATFORM, ref]),
            command([DOCKER, "save", "-o", archive, ref])]


def load_commands(tool, fmt, ref, archive, target, work_dir, dest=None):
    """The commands that push one archive to `target`. `dest` (skopeo only):
    authfile, cert_dir, tls_verify."""
    dest = dest or {}
    if tool == SKOPEO:
        flags = []
        if dest.get("authfile"):
            flags += ["--dest-authfile", dest["authfile"]]
        if dest.get("cert_dir"):
            flags += ["--dest-cert-dir", dest["cert_dir"]]
        if dest.get("tls_verify") is False:
            flags.append("--dest-tls-verify=false")
        return [_skopeo(*flags, f"{fmt}:{archive}", f"docker://{target}",
                        work_dir=work_dir)]
    return [command([DOCKER, "load", "-i", archive]),
            command([DOCKER, "tag", ref, target]),
            command([DOCKER, "push", target])]


# -- files -----------------------------------------------------------------------

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sums_text(images):
    """SHA256SUMS in the layout `sha256sum -c` reads."""
    return "".join(f"{i['sha256']}  {i['archive']}\n" for i in images)


def parse_sums(text):
    """{file name: sha256} from SHA256SUMS text."""
    out = {}
    for line in text.splitlines():
        digest, sep, name = line.strip().partition("  ")
        if sep:
            out[name.lstrip("*")] = digest
    return out


def manifest_text(manifest):
    return json.dumps(manifest, indent=2) + "\n"
