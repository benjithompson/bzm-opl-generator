"""The agent-environment reference: every record is one a form can build a
control from, and the reserved names are subtracted where it is served."""
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
        assert isinstance(v["functionalities"], list), v["name"]
        # A default is stated or None, never guessed.
        for k in ("default", "example"):
            assert v[k] is None or v[k].strip(), (v["name"], k)


def test_a_tag_names_a_functionality_this_tool_serves():
    """Every functionality tag names one this tool serves."""
    served = {f["id"] for f in core.FUNCTIONALITIES}
    for v in agent_env.AGENT_ENV:
        assert set(v["functionalities"]) <= served, v["name"]


def test_a_location_is_offered_only_the_variables_it_has_a_reader_for():
    """A location is offered only the variables its functionalities read
    (the Doduo grid variables are GUI functional's)."""
    perf = {v["name"] for v in core.agent_env(["performance"])}
    assert {"VERIFY_SSL", "PREFERRED_INTERFACE", "KUBERNETES_LABELS"} <= perf
    assert not perf & {"DODUO_PORT", "TLS_CERT_GRID", "TLS_KEY_GRID"}
    assert not perf & {"KUBERNETES_USE_APIPA", "HOSTNAME_OVERRIDE",
                       "KUBERNETES_SERVICES_BLOCKING_GET",
                       "KUBERNETES_WEB_EXPOSE_SHORT_URL",
                       "TLS_CERT", "TLS_KEY"}

    gui = {v["name"] for v in core.agent_env(["functionalGui"])}
    assert {"DODUO_PORT", "TLS_CERT_GRID", "TLS_KEY_GRID"} <= gui
    assert "KUBERNETES_USE_APIPA" not in gui

    sv = {v["name"] for v in core.agent_env(["mockServices"])}
    assert {"KUBERNETES_USE_APIPA", "KUBERNETES_SERVICES_BLOCKING_GET",
            "KUBERNETES_WEB_EXPOSE_SHORT_URL"} <= sv
    assert "DODUO_PORT" not in sv
    # The docker SV trio is reserved (owned by sv_hostname/sv_tls_*), so it
    # is not offered; a variable neither offered nor reserved would be a hole.
    assert not sv & {"HOSTNAME_OVERRIDE", "TLS_CERT", "TLS_KEY"}
    assert {"HOSTNAME_OVERRIDE", "TLS_CERT", "TLS_KEY"} <= generate.RESERVED_ENV

    # A location running both is offered both halves.
    both = {v["name"] for v in core.agent_env(["performance", "functionalGui"])}
    assert both == perf | gui


def test_nobody_having_said_is_not_a_location_that_runs_nothing():
    """None (nobody has said) offers everything; an empty list offers only the
    untagged variables."""
    assert {v["name"] for v in core.agent_env(None)} \
        == {v["name"] for v in core.agent_env()}
    assert "DODUO_PORT" in {v["name"] for v in core.agent_env()}

    none_of_ours = {v["name"] for v in core.agent_env([])}
    assert {"VERIFY_SSL", "PREFERRED_INTERFACE"} <= none_of_ours
    assert "DODUO_PORT" not in none_of_ours


def test_a_funcid_this_tool_has_no_options_for_takes_nothing_away():
    """A funcId no tag claims (tdm, delphix) narrows nothing."""
    assert {v["name"] for v in core.agent_env(["performance", "tdm"])} \
        == {v["name"] for v in core.agent_env(["performance"])}
    assert {v["name"] for v in core.agent_env(["tdm"])} \
        == {v["name"] for v in core.agent_env([])}


def test_names_are_declared_once():
    """Each variable is declared once, with every platform it applies to."""
    names = [v["name"] for v in agent_env.AGENT_ENV]
    assert len(names) == len(set(names))


def test_the_reference_is_whole_and_the_subtraction_is_at_the_serving_end():
    """The table includes reserved names; core.agent_env() subtracts them."""
    declared = {v["name"] for v in agent_env.AGENT_ENV}
    # The identity and the credential are documented, and are the generator's.
    assert {"AUTH_TOKEN", "SHIP_ID", "HARBOR_ID"} <= declared
    assert declared & generate.RESERVED_ENV

    offered = {v["name"] for v in core.agent_env()}
    assert not offered & generate.RESERVED_ENV
    assert offered == declared - generate.RESERVED_ENV
    # ...and something is left to offer.
    assert {"VERIFY_SSL", "KUBERNETES_LABELS", "PREFERRED_INTERFACE"} <= offered
    # The subtraction applies whatever the functionality filter says.
    everything = [f["id"] for f in core.FUNCTIONALITIES]
    assert not {v["name"] for v in core.agent_env(everything)} \
        & generate.RESERVED_ENV


def test_a_reserved_name_is_refused_with_the_same_words_wherever_it_arrives():
    """A reserved name in the reference is not offered, and is refused if it
    arrives in extra_env anyway."""
    for name in ("HTTP_PROXY", "IMAGE_OVERRIDES"):
        assert name in agent_env.AGENT_ENV_BY_NAME
        assert name not in {v["name"] for v in core.agent_env()}
        try:
            generate.extra_env({"extra_env": {name: "x"}})
        except ValueError as e:
            assert name in str(e)
        else:
            raise AssertionError(f"{name} was accepted")


def test_a_variable_holding_a_path_is_never_typed_pem():
    """The docker TLS_CERT/TLS_KEY pair holds a path, so it is typed string with a
    path example."""
    paths = ("REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "TLS_CERT", "TLS_KEY")
    for name in paths:
        v = agent_env.AGENT_ENV_BY_NAME[name]
        assert v["type"] == "string", name
        # A default where BlazeMeter documents one, else an example.
        assert (v["default"] or v["example"]).startswith("/"), name

    # The summary says the path is the container's.
    for name in ("TLS_CERT", "TLS_KEY"):
        summary = agent_env.AGENT_ENV_BY_NAME[name]["summary"]
        assert "container" in summary and "mount" in summary, name
        assert len(summary.split()) <= 20, name

    # The _GRID pair keeps `pem`: only its Docker side is documented as a path.
    for name in ("TLS_CERT_GRID", "TLS_KEY_GRID"):
        assert agent_env.AGENT_ENV_BY_NAME[name]["type"] == "pem", name
        assert agent_env.AGENT_ENV_BY_NAME[name]["platforms"] \
            == list(agent_env.BOTH), name


def test_the_json_object_types_are_ones_a_key_value_table_can_write():
    """A `json_object` variable is an object of scalars (tolerations is an array
    and so typed string)."""
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
