"""What the built page was built from.

A production build writes a fingerprint of its source inputs into `ui_dist`
beside `index.html`; this module recomputes it from the sources on disk so the
two can be compared. Content, never mtimes: `git pull` and branch switches
rewrite files without changing them.

The writer is Node and the reader is Python, so the covered set and hashing
rule exist twice; tests/test_ui_build.py holds them equal (not test_server.py,
which skips without fastapi).
"""
import hashlib
import json
import os

#: The file a build writes beside `index.html`.
FINGERPRINT_FILE = "source-fingerprint.json"

#: The document vite writes beside the fingerprint, and the page served.
BUILT_PAGE = "index.html"

#: Sources and a built page exist, but the page records nothing readable: not
#: read. A string so `is True` and `is False` both miss it.
UNRECORDED = "unrecorded"

#: Written into the file and checked on read, so a fingerprint made by another
#: rule reads as unrecorded rather than stale.
ALGORITHM = "sha256-paths-v1"

#: Files outside `src` the page is compiled from. The toolchain (package.json,
#: the lockfile, tsconfig.json) is excluded: covering it would flag every
#: `npm install`.
EXTRA_SOURCES = ("index.html", "vite.config.ts")

#: Tests reach no bundle, so they are not covered.
TEST_SUFFIXES = (".test.ts", ".test.tsx")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(REPO, "frontend")


def source_files(frontend=FRONTEND):
    """The covered files, as sorted POSIX paths relative to `frontend` (so a
    rename changes the fingerprint and a different checkout path does not)."""
    found = []
    src = os.path.join(frontend, "src")
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(TEST_SUFFIXES):
                continue
            found.append(os.path.join(root, name))
    paths = [os.path.relpath(p, frontend).replace(os.sep, "/") for p in found]
    paths += [n for n in EXTRA_SOURCES
              if os.path.isfile(os.path.join(frontend, n))]
    return sorted(paths)


def source_fingerprint(frontend=FRONTEND):
    """The fingerprint of the sources on disk, or None where there are none (an
    installed wheel ships no `frontend`)."""
    if not os.path.isdir(os.path.join(frontend, "src")):
        return None
    digest = hashlib.sha256()
    for rel in source_files(frontend):
        with open(os.path.join(frontend, rel), "rb") as fh:
            body = hashlib.sha256(fh.read()).hexdigest()
        # A separator no path contains, so concatenation cannot collide.
        digest.update(rel.encode() + b"\0" + body.encode() + b"\n")
    return digest.hexdigest()


def recorded_fingerprint(ui_dist):
    """What the built page records it was built from, or None when nothing can
    be read (absent, unparseable, or another algorithm)."""
    try:
        with open(os.path.join(ui_dist, FINGERPRINT_FILE)) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("algorithm") != ALGORITHM:
        return None
    found = doc.get("fingerprint")
    return found if isinstance(found, str) and found else None


def staleness(frontend, ui_dist):
    """Whether the built page in `ui_dist` was built from `frontend`.

    ``True``        the sources differ from the ones the page was built from.
    ``False``       compared, and they match.
    ``UNRECORDED``  the page records no readable fingerprint: not checked.
    ``None``        not applicable -- no sources (an installed wheel) or no
                    built page.
    """
    sources = source_fingerprint(frontend)
    if sources is None:
        return None
    if not os.path.isfile(os.path.join(ui_dist, BUILT_PAGE)):
        return None
    recorded = recorded_fingerprint(ui_dist)
    if recorded is None:
        return UNRECORDED
    return recorded != sources
