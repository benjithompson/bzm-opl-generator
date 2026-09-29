"""The option registry against what it describes: key parity both ways, and
docs/options.md being exactly what the registry renders."""

import re

import pytest

from bzm_opl_gen import generate as gen
from bzm_opl_gen import options as opt


def test_registry_covers_every_default_option():
    missing = sorted(set(gen.DEFAULT_OPTIONS) - set(opt.BY_NAME))
    assert not missing, (
        f"new option(s) {missing} in DEFAULT_OPTIONS with no registry entry -- "
        f"add one to bzm_opl_gen/options.py so the doc, the UI help and the MCP "
        f"schema all describe it")


def test_registry_invents_no_options():
    extra = sorted(set(opt.BY_NAME) - set(gen.DEFAULT_OPTIONS))
    assert not extra, (
        f"registry entries {extra} name options generate() does not have -- a "
        f"renamed or removed key leaves a row documenting nothing")


def test_registry_entries_are_unique():
    names = [o.name for o in opt.OPTIONS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("o", opt.OPTIONS, ids=lambda o: o.name)
def test_summary_fits_the_schema_budget(o):
    """Every summary lands in every MCP session's context whether or not the
    option is used, so the limit is what keeps thirty-one of them affordable."""
    assert o.summary and o.summary[0].isupper() or o.summary[0] in "`_"
    words = len(o.summary.split())
    assert words <= opt.SUMMARY_MAX_WORDS, (
        f"{o.name}: summary is {words} words, limit {opt.SUMMARY_MAX_WORDS} -- "
        f"the long version belongs in `doc`")


@pytest.mark.parametrize("o", opt.OPTIONS, ids=lambda o: o.name)
def test_doc_says_more_than_the_summary(o):
    """A `doc` that is just the summary again means the option is undocumented
    and looks documented, which is worse than an empty cell."""
    assert len(o.doc.split()) > opt.SUMMARY_MAX_WORDS
    assert o.doc.strip() != o.summary.strip()


@pytest.mark.parametrize("o", opt.OPTIONS, ids=lambda o: o.name)
def test_declared_group_is_one_that_renders(o):
    assert o.group in [name for name, _ in opt.GROUPS], (
        f"{o.name}: group {o.group!r} is not in GROUPS, so the row renders "
        f"nowhere")


@pytest.mark.parametrize("o", opt.OPTIONS, ids=lambda o: o.name)
def test_declared_type_matches_the_default(o):
    """Each declared type matches its default, since the MCP schema carries it."""
    value = o.default
    if value is None:
        return  # unset carries no type; `nullable` is what says so
    expect = {"string": str, "boolean": bool, "integer": int,
              "object": dict, "array": list}[o.type]
    # bool is an int subclass, so check it before the numeric case.
    if o.type == "integer":
        assert isinstance(value, int) and not isinstance(value, bool)
    else:
        assert isinstance(value, expect), f"{o.name}: {value!r} is not {o.type}"


@pytest.mark.parametrize("o", [o for o in opt.OPTIONS if o.choices],
                         ids=lambda o: o.name)
def test_default_is_one_of_the_choices(o):
    assert o.default is None or o.default in o.choices


def test_choices_track_generate_enumerations():
    """The registry's choices are read from generate's enumerations."""
    assert opt.BY_NAME["sv_ingress"].choices == (
        tuple(gen.SV_INGRESS_TYPES) + (gen.SV_INGRESS_NONE,))
    # The `none` sentinel is offered but is not a backend.
    assert gen.SV_INGRESS_NONE not in gen.SV_INGRESS_TYPES


def test_secret_options_are_the_ones_profile_json_omits():
    """`secret` options are exactly the ones profile.json omits."""
    secret = {o.name for o in opt.OPTIONS if o.secret}
    assert secret == set(gen.SECRET_OPTIONS)
    written = gen._profile_json(dict(gen.DEFAULT_OPTIONS))
    for name in secret:
        assert f'"{name}"' not in written


def test_ca_options_are_the_ones_this_registry_files_under_ca_trust():
    """generate.CA_OPTIONS is exactly the registry's CA trust section."""
    ca = {o.name for o in opt.OPTIONS if o.group == "CA trust"}
    assert ca == set(gen.CA_OPTIONS)


def test_clearing_the_ca_options_leaves_no_mode_configured():
    """The other half of it: cleared to what? `no_ca()` answers with each
    option's own default, so `_ca_cfg` resolves to no CA at all."""
    assert gen._ca_cfg({**gen.DEFAULT_OPTIONS, "ca_bundle_slot": True,
                        **gen.no_ca()}) is None


def test_a_nullable_option_is_the_one_whose_default_is_none():
    """`nullable` is true exactly where the default is None."""
    for o in opt.OPTIONS:
        assert o.nullable == (o.default is None), o.name


def test_generated_table_is_what_the_doc_carries():
    """Regenerating the docs/options.md table is a no-op."""
    with open(opt.DOC_PATH, encoding="utf-8") as fh:
        text = fh.read()
    assert opt.render_table() in text, (
        "docs/options.md is out of date -- run `python -m bzm_opl_gen.options`")


def test_every_option_has_a_row_in_the_rendered_doc():
    table = opt.render_table()
    for name in gen.DEFAULT_OPTIONS:
        assert f"| `{name}` |" in table


def test_table_cells_are_single_line():
    """A newline inside a cell ends the row, which turns the rest of the prose
    into body text under the table without failing anything."""
    for line in opt.render_table().splitlines():
        if line.startswith("| `"):
            assert line.endswith(" |") and line.count("\n") == 0


def test_cell_escaping_leaves_no_bare_pipe():
    for o in opt.OPTIONS:
        cell = opt._cell(o.doc)
        assert not re.search(r"(?<!\\)\|", cell), (
            f"{o.name}: an unescaped pipe would split the row into columns")
