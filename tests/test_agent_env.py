"""The agent-environment reference, and what is left of it once the options
this generator already has are taken out.

The table is a transcription, so what can be asserted about it is shape rather
than content: that every record is one a form can build a control from, and
that the subtraction is done where it is served rather than in the table.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from bzm_opl_gen import agent_env, core, generate  # noqa: E402


def test_every_record_is_one_a_form_can_render():
    """Name, type, platforms and a summary -- a row missing any of them is a
    row with nothing to show or no way to show it."""
    for v in agent_env.AGENT_ENV:
        assert generate.ENV_NAME_RE.match(v["name"]), v["name"]
        assert v["type"] in agent_env.TYPES, v
        assert v["platforms"], v["name"]
        assert set(v["platforms"]) <= set(agent_env.BOTH), v["name"]
        assert v["summary"].strip(), v["name"]
        # Stated, never guessed: a variable the page has no default for says
        # nothing rather than implying one.
        for k in ("default", "example"):
            assert v[k] is None or v[k].strip(), (v["name"], k)


def test_names_are_declared_once():
    """A variable documented in both of BlazeMeter's tables is one record
    carrying both platforms -- two records would give the form two rows for one
    variable, and the second would silently win."""
    names = [v["name"] for v in agent_env.AGENT_ENV]
    assert len(names) == len(set(names))


def test_the_reference_is_whole_and_the_subtraction_is_at_the_serving_end():
    """The table carries the variables this generator writes for itself, and
    core.agent_env() is what takes them out.

    Which is what makes the offered list follow the options: a variable is
    offered exactly while no control owns it, so an option removed later hands
    its variable back without anyone editing this table."""
    declared = {v["name"] for v in agent_env.AGENT_ENV}
    # The identity and the credential are documented, and are the generator's.
    assert {"AUTH_TOKEN", "SHIP_ID", "HARBOR_ID"} <= declared
    assert declared & generate.RESERVED_ENV

    offered = {v["name"] for v in core.agent_env()}
    assert not offered & generate.RESERVED_ENV
    assert offered == declared - generate.RESERVED_ENV
    # ...and something is actually left, or the area this feeds is empty and
    # the form has nothing to offer at all.
    assert {"VERIFY_SSL", "KUBERNETES_LABELS", "PREFERRED_INTERFACE"} <= offered


def test_a_reserved_name_is_refused_with_the_same_words_wherever_it_arrives():
    """The reference and the refusal agree: a name that is in the table and in
    RESERVED_ENV is not offered, and is still refused if it arrives anyway --
    an imported profile carries options, not a form."""
    for name in ("HTTP_PROXY", "IMAGE_OVERRIDES"):
        assert name in agent_env.AGENT_ENV_BY_NAME
        assert name not in {v["name"] for v in core.agent_env()}
        try:
            generate.extra_env({"extra_env": {name: "x"}})
        except ValueError as e:
            assert name in str(e)
        else:
            raise AssertionError(f"{name} was accepted")


def test_the_json_object_types_are_ones_a_key_value_table_can_write():
    """`json_object` is the form's key/value table, so a variable typed that
    way has to be an object of scalars rather than an array -- the tolerations
    variable is an array and is typed as a string for exactly that reason."""
    objects = [v["name"] for v in agent_env.AGENT_ENV
               if v["type"] == "json_object"]
    assert "KUBERNETES_LABELS" in objects
    assert "KUBERNETES_TOLERATIONS_JSON" not in objects
    for name in objects:
        example = agent_env.AGENT_ENV_BY_NAME[name]["example"]
        if example:
            import json
            parsed = json.loads(example)
            assert isinstance(parsed, dict), name
            assert all(isinstance(x, str) for x in parsed.values()), name
