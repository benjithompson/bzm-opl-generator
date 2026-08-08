"""The built page records what it was built from (#237).

Its own module rather than a few cases inside `tests/test_server.py`, which
skips entirely without fastapi: the first test here is the one that catches
`ui_dist` committed without a rebuild, and a check that reports a clean pass
having not run is exactly the failure it is about.
"""
import json
import os
import pathlib

from bzm_opl_gen import ui_build

REPO = pathlib.Path(__file__).resolve().parent.parent


def _frontend(tmp_path, **files):
    """A frontend directory with the named files in it, `src/` included."""
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return str(tmp_path)


def test_the_built_page_records_the_sources_it_was_built_from():
    """The committed fingerprint against the committed sources.

    Worth more than the fingerprint itself. It fails when `ui_dist` is
    committed without a rebuild, which is the mistake #224 was about and which
    nothing in this suite caught before: the built page and the sources beside
    it went out of step in a commit, and the first sign was a route answering
    404 to a page that read the 404 as "not read yet".

    It is also what holds the two halves equal. The writer is a vite plugin in
    `frontend/scripts/source-fingerprint.mjs` and the reader is
    `bzm_opl_gen/ui_build.py`; the covered set and the hashing rule are stated
    twice, in two languages, because a Node build cannot import a Python table.
    Nothing else notices one of them drifting.
    """
    dist = REPO / "bzm_opl_gen" / "ui_dist"
    recorded = ui_build.recorded_fingerprint(str(dist))
    assert recorded, (
        f"{dist}/{ui_build.FINGERPRINT_FILE} records nothing this version can "
        "read -- rebuild it with `cd frontend && npm run build`")
    assert recorded == ui_build.source_fingerprint(), (
        "the built page under bzm_opl_gen/ui_dist was not built from the "
        "sources in frontend/ -- rebuild and commit it with "
        "`cd frontend && npm run build`")


def test_the_fingerprint_is_content_and_not_a_clock(tmp_path):
    """The whole point of #238, one layer down. A `git pull`, a `git checkout`
    and a branch switch all rewrite the mtime of every file they touch, and the
    build is unaffected -- so a fingerprint that moved with a timestamp would
    reproduce the defect it replaces."""
    frontend = _frontend(tmp_path, **{"src/App.tsx": "x", "index.html": "<p>"})
    first = ui_build.source_fingerprint(frontend)

    os.utime(os.path.join(frontend, "src", "App.tsx"), (10 ** 9, 10 ** 9))
    assert ui_build.source_fingerprint(frontend) == first

    (tmp_path / "src" / "App.tsx").write_text("y")
    assert ui_build.source_fingerprint(frontend) != first


def test_a_source_that_moves_changes_it(tmp_path):
    """The fingerprint is over the file list as well as over the bytes: an
    import renamed with no edit is a different page."""
    frontend = _frontend(tmp_path, **{"src/App.tsx": "x"})
    first = ui_build.source_fingerprint(frontend)
    (tmp_path / "src" / "App.tsx").rename(tmp_path / "src" / "Page.tsx")
    assert ui_build.source_fingerprint(frontend) != first


def test_a_test_file_is_not_an_input(tmp_path):
    """A `.test.ts` reaches no bundle, so an edit to one must not ask for a
    rebuild that would change nothing. This directory is the one this repo
    edits most, and a false alarm there is the crying-wolf failure again."""
    frontend = _frontend(tmp_path, **{"src/App.tsx": "x"})
    first = ui_build.source_fingerprint(frontend)
    (tmp_path / "src" / "App.test.tsx").write_text("expect(1).toBe(1)")
    (tmp_path / "src" / "sv.test.ts").write_text("expect(1).toBe(1)")
    assert ui_build.source_fingerprint(frontend) == first
    assert not [p for p in ui_build.source_files(frontend) if ".test." in p]


def test_the_page_is_compiled_from_more_than_src(tmp_path):
    """`index.html` is vite's entry document and `vite.config.ts` decides what
    the bundle is; both change the served page with no `src` edit at all."""
    frontend = _frontend(tmp_path, **{
        "src/App.tsx": "x", "index.html": "<p>", "vite.config.ts": "export {}",
        "package.json": "{}"})
    covered = ui_build.source_files(frontend)
    assert "index.html" in covered and "vite.config.ts" in covered
    # The toolchain is deliberately out: a dependency bump changes the output,
    # but it changes it under a command somebody just ran, and covering the
    # lockfile would flip the fingerprint on every `npm install`.
    assert "package.json" not in covered


def test_no_sources_is_not_an_empty_set_of_them(tmp_path):
    """The installed wheel. It ships a built `ui_dist` and no `frontend`, so
    there is nothing to compute -- and a hash of no files would be a real
    answer about a directory nobody has."""
    assert ui_build.source_fingerprint(str(tmp_path / "nope")) is None


def test_a_page_that_records_nothing_says_nothing(tmp_path):
    """Every way the file fails to answer is one answer: not read. A page built
    before #237 records nothing, a deleted file records nothing, and one
    written by a rule this version does not know cannot be compared with a
    number this version computes -- reporting that last one as a mismatch would
    call every page built by the next algorithm stale."""
    assert ui_build.recorded_fingerprint(str(tmp_path)) is None

    doc = tmp_path / ui_build.FINGERPRINT_FILE
    doc.write_text("{not json")
    assert ui_build.recorded_fingerprint(str(tmp_path)) is None

    doc.write_text(json.dumps({"algorithm": "sha256-paths-v0",
                               "fingerprint": "abc"}))
    assert ui_build.recorded_fingerprint(str(tmp_path)) is None

    doc.write_text(json.dumps({"algorithm": ui_build.ALGORITHM}))
    assert ui_build.recorded_fingerprint(str(tmp_path)) is None

    doc.write_text(json.dumps({"algorithm": ui_build.ALGORITHM,
                               "fingerprint": "abc"}))
    assert ui_build.recorded_fingerprint(str(tmp_path)) == "abc"


def test_the_writer_and_the_reader_agree_about_the_algorithm():
    """Two implementations of one rule, so the name that travels with the
    number is stated twice as well. A build writing `sha256-paths-v2` while
    this reads `v1` is every page reading as unrecorded, which is quiet."""
    script = (REPO / "frontend" / "scripts" / "source-fingerprint.mjs").read_text()
    assert f'ALGORITHM = "{ui_build.ALGORITHM}"' in script
    assert f'FINGERPRINT_FILE = "{ui_build.FINGERPRINT_FILE}"' in script
