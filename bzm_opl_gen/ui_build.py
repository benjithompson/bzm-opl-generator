"""What the built page was built from (#237).

A production build writes a fingerprint of its source inputs into `ui_dist`
beside `index.html`, and this module is the reader: it recomputes the same
number from the sources on disk, so anything that wants to know whether the
built page matches the checkout can compare two strings instead of two clocks.

**Content, never mtimes.** The question was answered by timestamps until now --
newest file under `frontend/src` against `ui_dist/index.html` -- and a timestamp
answers a different question than the one being asked. `git pull`, `git
checkout` and a branch switch all rewrite the files they touch, so a
fast-forward through two merged pull requests raised the flag with the built
output byte-identical (measured 2026-08-08). Hashing the bytes cannot do that:
the same content is the same fingerprint whatever the filesystem thinks.

Stdlib only, and its own module rather than a few lines inside `server`, for
two reasons. The writer is Node and the reader is Python, so the covered set
and the hashing rule exist twice by necessity, and what holds them equal is a
test that recomputes one and compares it with the other -- a test that must not
sit in `tests/test_server.py`, which skips its whole module without fastapi and
would report a clean pass having checked nothing.
"""
import hashlib
import json
import os

#: The file a build writes beside `index.html`. Named for what it is about
#: rather than for the build that wrote it: the next reader of `ui_dist` is
#: somebody wondering what these files came from.
FINGERPRINT_FILE = "source-fingerprint.json"

#: Written into the file and checked when it is read. A page fingerprinted by
#: an older rule cannot be compared with one computed by this one, and the
#: honest answer to that is "not recorded" rather than a mismatch reported as a
#: stale page -- so the algorithm travels with the number.
ALGORITHM = "sha256-paths-v1"

#: Named files outside `src` that the page is compiled from. `index.html` is
#: vite's entry document and ships as the served page; `vite.config.ts` decides
#: what the bundle is. Deliberately *not* here: `package.json`, its lockfile
#: and `tsconfig.json`. A dependency bump does change the output, but it
#: changes it under a command somebody just ran, while the failure this exists
#: for is a checkout whose sources moved under a page nobody rebuilt. Widening
#: the set to the toolchain would flip the fingerprint on every `npm install`,
#: which is the crying-wolf defect one directory along.
EXTRA_SOURCES = ("index.html", "vite.config.ts")

#: A test is a source file that reaches no bundle. Including one would ask for
#: a rebuild that changes nothing -- the same false alarm, in the one directory
#: this repo edits most.
TEST_SUFFIXES = (".test.ts", ".test.tsx")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(REPO, "frontend")


def source_files(frontend=FRONTEND):
    """The covered files, as sorted POSIX paths relative to `frontend`.

    Sorted and relative because the fingerprint is over this list as well as
    over the bytes: a rename with no edit has to change the answer, and a
    checkout at a different absolute path must not.
    """
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
    """The fingerprint of the sources on disk, or None where there are none.

    None is the installed wheel: it ships a built `ui_dist` and no `frontend`,
    so there is nothing to compare and no fingerprint to compute. Structural
    rather than left to each caller to remember -- an empty string here would
    be a real answer about a directory nobody has.
    """
    if not os.path.isdir(os.path.join(frontend, "src")):
        return None
    digest = hashlib.sha256()
    for rel in source_files(frontend):
        with open(os.path.join(frontend, rel), "rb") as fh:
            body = hashlib.sha256(fh.read()).hexdigest()
        # Path and content both, separated by a byte no path may contain, so
        # that concatenation cannot make two different file lists agree.
        digest.update(rel.encode() + b"\0" + body.encode() + b"\n")
    return digest.hexdigest()


def recorded_fingerprint(ui_dist):
    """What the built page records it was built from, or None for nothing.

    None is *not read*, and it covers every way this file fails to answer: a
    page built before #237, a file somebody deleted, one that will not parse,
    one written by a rule this version does not know. All four are "rebuild and
    the question can be answered", which is a different sentence from both "the
    page matches" and "the page is stale" -- so the caller gets a value it
    cannot mistake for either.
    """
    try:
        with open(os.path.join(ui_dist, FINGERPRINT_FILE)) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("algorithm") != ALGORITHM:
        return None
    found = doc.get("fingerprint")
    return found if isinstance(found, str) and found else None
