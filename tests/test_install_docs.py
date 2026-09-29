"""The install command as stated in README.md, docs/mcp.md and
.github/release-footer.md: pasteable, the same spec everywhere, and every
footer placeholder one the release workflow substitutes."""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every file that tells a user how to install (not how to develop).
INSTALL_DOCS = ("README.md", "docs/mcp.md", ".github/release-footer.md")

REPO_URL = "git+https://github.com/benjithompson/bzm-opl-generator"

# `bzm-opl-gen[ui] @ git+https://...@v0.2.0`, with extras and ref captured.
SPEC = re.compile(
    r"bzm-opl-gen\[(?P<extras>[a-z,]+)\]\s*@\s*"
    rf"(?P<url>{re.escape(REPO_URL)})(?:@(?P<ref>\S+?))?[\"']")

# The plain PyPI form, not followed by ` @ ` (the git spec above).
PYPI_SPEC = re.compile(
    r"bzm-opl-gen\[[a-z,]+\](?:==[\w.]+|VERSION_NUMBER)?[\"'\s](?!\s*@)")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_no_install_command_carries_an_unexpandable_glob(name):
    """No install command carries a `*` inside quotes (nothing expands it)."""
    for line in read(name).splitlines():
        if "pipx install" not in line and "pip install" not in line:
            continue
        assert "*" not in line, (
            f"{name}: `{line.strip()}` cannot be pasted -- neither the shell "
            "nor pipx expands a glob inside quotes. Name the wheel's real "
            "filename, or install from the git URL.")


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_the_install_spec_is_the_same_one_everywhere(name):
    """Every file uses the same repo and `package[extras] @ git+url` form; extras
    and ref may differ."""
    found = SPEC.findall(read(name))
    assert found, f"{name} states no git install spec"
    for extras, url, _ref in found:
        assert url == REPO_URL, f"{name} installs from {url}"
        assert set(extras.split(",")) <= {"ui", "mcp"}, (
            f"{name} asks for extras {extras!r}, which pyproject has no key for")


def test_the_pinned_tag_is_this_version():
    """A pinned tag in the docs is this project's version."""
    version = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M)
    assert version, "pyproject.toml states no version"
    for name in INSTALL_DOCS:
        for _extras, _url, ref in SPEC.findall(read(name)):
            if not ref or ref == "VERSION":     # untagged, or the placeholder
                continue                        # release.yml substitutes
            assert ref == "v" + version.group(1), (
                f"{name} pins {ref}, but this is version {version.group(1)} -- "
                "bump the pin with the version, and push the tag")


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_the_pypi_spec_is_stated_before_the_git_one(name):
    """Every page stating both installs states the PyPI one first."""
    text = read(name)
    pypi = PYPI_SPEC.search(text)
    assert pypi, (
        f"{name} states no PyPI install -- `pipx install \"bzm-opl-gen[ui]\"` "
        "is the documented front door, and this page skips it")
    git = SPEC.search(text)
    if git:
        assert pypi.start() < git.start(), (
            f"{name} states the git URL before the PyPI spec, which teaches the "
            "fallback as if it were the way in")


def test_every_footer_placeholder_is_one_the_release_workflow_substitutes():
    """Every footer placeholder is one release.yml substitutes."""
    footer = read(".github/release-footer.md")
    workflow = read(".github/workflows/release.yml")
    placeholders = set(re.findall(r"\bVERSION(?:_[A-Z]+)?\b", footer))
    assert placeholders, "the footer pins no version at all"
    for p in placeholders:
        assert f"s/{p}/" in workflow, (
            f"release-footer.md carries {p} and release.yml substitutes "
            f"nothing for it -- it would publish as the literal text {p!r}")


def test_the_footer_substitutes_the_longer_placeholder_first():
    """The footer substitutes VERSION_NUMBER before its prefix VERSION."""
    workflow = read(".github/workflows/release.yml")
    assert workflow.index("s/VERSION_NUMBER/") < workflow.index("s/VERSION/"), (
        "release.yml substitutes VERSION before VERSION_NUMBER, which leaves "
        "the tag glued to a stray `_NUMBER` in the published notes")


def test_the_version_the_code_reports_is_the_one_the_project_declares():
    """The installed package reports pyproject's version (fails on a stale editable
    install; the fix is `pip install -e ".[dev]"`)."""
    from importlib.metadata import version
    declared = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M)
    assert declared, "pyproject.toml states no version"
    assert version("bzm-opl-gen") == declared.group(1), (
        f'installed metadata says {version("bzm-opl-gen")} and pyproject says '
        f'{declared.group(1)} -- reinstall with: pip install -e ".[dev]"')
