"""The install command is stated in three places, and nothing else notices one
of them going wrong.

`README.md`, `docs/mcp.md` and `.github/release-footer.md` each carry a `pipx
install` line, and the footer's is the one pasted out of every GitHub Release's
notes -- the copy most people actually use, and the one nobody in a checkout
ever reads. All three drifted into `pipx install './bzm_opl_gen-*.whl[ui]'`,
which does not work at all: the quotes stop the shell expanding the glob and
pipx does not expand one itself, so the documented first command of the
documented first step exits `Unable to parse package spec`. It shipped that way
because the release workflow verifies the *wheel* thoroughly and the prose not
at all.

So the rule this file holds is the narrow one that failure teaches: a command in
the docs must be one somebody can paste. A `*` inside quotes is the specific
shape that is not, and the three files must agree on the spec so a fix to one is
a fix to all.

The tag substitution is the other half. The footer is static and appended to
every release, so it cannot name a version -- it carries `VERSION`, and
`release.yml` replaces it with the tag being published. A placeholder the
workflow does not substitute reaches a real user as literal text, which is the
same class of defect one layer along, so the placeholders are checked against
the workflow that resolves them.
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every file that tells somebody how to install this, and nothing that tells
# somebody how to work on it: `CONTRIBUTING.md` and the README's own checkout
# section are an editable install from a clone, which is a different command
# for a different person.
INSTALL_DOCS = ("README.md", "docs/mcp.md", ".github/release-footer.md")

REPO_URL = "git+https://github.com/benjithompson/bzm-opl-generator"

# `bzm-opl-gen[ui] @ git+https://...@v0.2.0` -- extras and ref captured so the
# files can be compared on the parts that have to match rather than verbatim
# (mcp.md installs `[mcp]`, and the footer's ref is a placeholder).
SPEC = re.compile(
    r"bzm-opl-gen\[(?P<extras>[a-z,]+)\]\s*@\s*"
    rf"(?P<url>{re.escape(REPO_URL)})(?:@(?P<ref>\S+?))?[\"']")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_no_install_command_carries_an_unexpandable_glob(name):
    """The bug itself. A `*` in a quoted pipx argument is expanded by nobody:
    not the shell (it is quoted) and not pipx (it parses a package spec, not a
    path pattern). Naming the file explicitly is the only form that works, so a
    wheel path in the docs carries a version."""
    for line in read(name).splitlines():
        if "pipx install" not in line and "pip install" not in line:
            continue
        assert "*" not in line, (
            f"{name}: `{line.strip()}` cannot be pasted -- neither the shell "
            "nor pipx expands a glob inside quotes. Name the wheel's real "
            "filename, or install from the git URL.")


@pytest.mark.parametrize("name", INSTALL_DOCS)
def test_the_install_spec_is_the_same_one_everywhere(name):
    """Same repo, same `package[extras] @ git+url` form. The extras differ on
    purpose -- mcp.md installs the MCP server -- and the ref differs because
    the footer's is substituted at release time, so neither is compared."""
    found = SPEC.findall(read(name))
    assert found, f"{name} states no git install spec"
    for extras, url, _ref in found:
        assert url == REPO_URL, f"{name} installs from {url}"
        assert set(extras.split(",")) <= {"ui", "mcp"}, (
            f"{name} asks for extras {extras!r}, which pyproject has no key for")


def test_the_pinned_tag_is_this_version():
    """A pin is a promise that the tag carries what the page describes, and the
    two came apart silently: `pyproject` was bumped to 0.3.0 and the tag never
    pushed, so the newest release was 0.2.0 -- which has no `mcp` subcommand at
    all, while `docs/mcp.md` was telling people to install it. `release.yml`
    already refuses a tag disagreeing with `pyproject`; this is the same
    equality one step earlier, where the version is written down for a reader
    rather than for a workflow.

    It fails on the bump commit rather than at install time, which is the point:
    the fix is to pin the version being released, and then to push the tag."""
    version = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M)
    assert version, "pyproject.toml states no version"
    for name in INSTALL_DOCS:
        for _extras, _url, ref in SPEC.findall(read(name)):
            if not ref or ref == "VERSION":     # untagged, or the placeholder
                continue                        # release.yml substitutes
            assert ref == "v" + version.group(1), (
                f"{name} pins {ref}, but this is version {version.group(1)} -- "
                "bump the pin with the version, and push the tag")


def test_setup_git_is_stated_wherever_the_git_url_is():
    """`gh auth login` alone leaves no credential where `git` looks, so a
    private-repo `pip install git+https://...` fails on authentication with an
    error about the URL. `gh auth setup-git` is the command that fixes it and
    it is one word away from being forgotten, so it travels with the spec."""
    for name in INSTALL_DOCS:
        text = read(name)
        if REPO_URL not in text:
            continue
        assert "gh auth setup-git" in text, (
            f"{name} installs from the private git URL without telling anyone "
            "to run `gh auth setup-git` first")


def test_every_footer_placeholder_is_one_the_release_workflow_substitutes():
    """The footer cannot name a version -- it is appended to every release --
    so it carries placeholders that `release.yml` resolves against the tag. One
    it does not resolve reaches a real user as the literal word."""
    footer = read(".github/release-footer.md")
    workflow = read(".github/workflows/release.yml")
    placeholders = set(re.findall(r"\bVERSION(?:_[A-Z]+)?\b", footer))
    assert placeholders, "the footer pins no version at all"
    for p in placeholders:
        assert f"s/{p}/" in workflow, (
            f"release-footer.md carries {p} and release.yml substitutes "
            f"nothing for it -- it would publish as the literal text {p!r}")


def test_the_footer_substitutes_the_longer_placeholder_first():
    """`VERSION` is a prefix of `VERSION_NUMBER`, so substituting it first
    turns the second into `v0.2.0_NUMBER`. Ordering is the whole correctness of
    that sed, and it is invisible in the output until a release is cut."""
    workflow = read(".github/workflows/release.yml")
    assert workflow.index("s/VERSION_NUMBER/") < workflow.index("s/VERSION/"), (
        "release.yml substitutes VERSION before VERSION_NUMBER, which leaves "
        "the tag glued to a stray `_NUMBER` in the published notes")
