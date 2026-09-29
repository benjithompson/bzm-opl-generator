"""The built page's source fingerprint. Not in test_server.py, which skips
without fastapi."""
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
    """The committed ui_dist records the fingerprint of the committed sources, and
    the Node writer (frontend/scripts/source-fingerprint.mjs) agrees with the
    Python reader. Fails when ui_dist is committed without a rebuild."""
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
    """The fingerprint moves with content, not with mtimes."""
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
    """A `.test.ts` file is not an input."""
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
    # The toolchain is out: covering the lockfile would flip on `npm install`.
    assert "package.json" not in covered


def test_no_sources_is_not_an_empty_set_of_them(tmp_path):
    """No `frontend/src` (an installed wheel) answers None."""
    assert ui_build.source_fingerprint(str(tmp_path / "nope")) is None


def test_a_page_that_records_nothing_says_nothing(tmp_path):
    """A missing, unparseable or other-algorithm record reads as None (not read)."""
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


def _built(tmp_path, fingerprint=None, page=True):
    """A `ui_dist` directory, optionally recording a fingerprint."""
    dist = tmp_path / "ui_dist"
    dist.mkdir(parents=True, exist_ok=True)
    if page:
        (dist / ui_build.BUILT_PAGE).write_text("<html></html>")
    if fingerprint is not None:
        (dist / ui_build.FINGERPRINT_FILE).write_text(json.dumps(
            {"algorithm": ui_build.ALGORITHM, "fingerprint": fingerprint}))
    return str(dist)


def test_staleness_is_four_answers_and_not_three(tmp_path):
    """Staleness has four distinct answers: True, False, UNRECORDED, None."""
    frontend = _frontend(tmp_path / "frontend", **{"src/App.tsx": "x"})
    matching = ui_build.source_fingerprint(frontend)

    # Compared, and they match.
    assert ui_build.staleness(
        frontend, _built(tmp_path / "current", matching)) is False

    # Compared, and they do not: the one answer that is a warning.
    assert ui_build.staleness(
        frontend, _built(tmp_path / "old", "0" * 64)) is True

    # A page that records nothing about its sources.
    assert ui_build.staleness(
        frontend, _built(tmp_path / "mute")) == ui_build.UNRECORDED

    # The installed wheel: no frontend, so None whatever the dist holds.
    assert ui_build.staleness(
        str(tmp_path / "nope"), _built(tmp_path / "wheel", matching)) is None


def test_the_unrecorded_answer_cannot_be_reached_by_a_boolean_test(tmp_path):
    """UNRECORDED is a string, so `is True` and `is False` both miss it."""
    frontend = _frontend(tmp_path / "frontend", **{"src/App.tsx": "x"})
    answer = ui_build.staleness(frontend, _built(tmp_path / "mute"))
    assert answer is not True and answer is not False and answer is not None
    assert answer == ui_build.UNRECORDED


def test_a_checkout_with_no_built_page_has_nothing_to_be_stale(tmp_path):
    """Sources and no built page answer None, not UNRECORDED."""
    frontend = _frontend(tmp_path / "frontend", **{"src/App.tsx": "x"})
    empty = _built(tmp_path / "unbuilt", page=False)
    assert ui_build.staleness(frontend, empty) is None


def test_staleness_moves_with_content_and_not_with_a_clock(tmp_path):
    """Rewriting a source unchanged is not stale; a real edit is."""
    frontend = _frontend(tmp_path / "frontend", **{"src/App.tsx": "x"})
    dist = _built(tmp_path / "dist", ui_build.source_fingerprint(frontend))
    assert ui_build.staleness(frontend, dist) is False

    # What `git pull` does to a file it rewrites byte for byte.
    os.utime(os.path.join(frontend, "src", "App.tsx"), (10 ** 10, 10 ** 10))
    assert ui_build.staleness(frontend, dist) is False

    (tmp_path / "frontend" / "src" / "App.tsx").write_text("y")
    assert ui_build.staleness(frontend, dist) is True


def test_the_writer_and_the_reader_agree_about_the_algorithm():
    """The writer and the reader name the same algorithm."""
    script = (REPO / "frontend" / "scripts" / "source-fingerprint.mjs").read_text()
    assert f'ALGORITHM = "{ui_build.ALGORITHM}"' in script
    assert f'FINGERPRINT_FILE = "{ui_build.FINGERPRINT_FILE}"' in script
