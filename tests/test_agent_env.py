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
        assert isinstance(v["functionalities"], list), v["name"]
        # Stated, never guessed: a variable the page has no default for says
        # nothing rather than implying one.
        for k in ("default", "example"):
            assert v[k] is None or v[k].strip(), (v["name"], k)


def test_a_tag_names_a_functionality_this_tool_serves():
    """The same rule optionGroups' tags are held to: a tag naming anything
    else is a variable filtered out for a functionality no location can be
    found to run, which reads exactly like a variable nobody transcribed."""
    served = {f["id"] for f in core.FUNCTIONALITIES}
    for v in agent_env.AGENT_ENV:
        assert set(v["functionalities"]) <= served, v["name"]


def test_a_location_is_offered_only_the_variables_it_has_a_reader_for():
    """The platform says which agent reads a variable; the functionality says
    whether this location runs the thing that reads it. Two questions, and
    a performance location was being offered the answers to both.

    Doduo is the Selenium grid proxy, so its port and its TLS material are the
    GUI functional agent's -- `facts.IMAGE_CATEGORY` already classifies the
    `doduo` image as `gui`, and a live functionalGui location's images carry
    `blazemeter/doduo` where a performance-only location's do not.
    """
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
    # ...and the docker trio is gone from the offer, which is the *other* rule
    # doing its work rather than this one failing (#182): `core.agent_env`
    # subtracts RESERVED_ENV, and since these three are written off the
    # `sv_hostname`/`sv_tls_cert`/`sv_tls_key` options the bundle owns them. The
    # form does not go quiet about them -- EnvVars states the whole reserved
    # table beside the offered one, naming the option and the section that sets
    # each. Asserted here because the two halves are far apart and a variable
    # that stopped being offered without becoming reserved is a hole.
    assert not sv & {"HOSTNAME_OVERRIDE", "TLS_CERT", "TLS_KEY"}
    assert {"HOSTNAME_OVERRIDE", "TLS_CERT", "TLS_KEY"} <= generate.RESERVED_ENV

    # A location running both is offered both halves, rather than the
    # intersection: the tag is what reads the variable, not what claims it.
    both = {v["name"] for v in core.agent_env(["performance", "functionalGui"])}
    assert both == perf | gui


def test_nobody_having_said_is_not_a_location_that_runs_nothing():
    """`None` is the third state, and it is the one the page mounts in: no key
    pasted, no location picked. It offers everything, which is the direction
    that shows a field too many rather than hiding one somebody needs -- the
    same way `runsFunctionality` reads an unanswered enablement as yes.

    An answered *empty* set is a different sentence and gets a different answer:
    a location running nothing this tool covers still runs an agent, so the
    agent-wide variables stay and the functionality-tagged ones go."""
    assert {v["name"] for v in core.agent_env(None)} \
        == {v["name"] for v in core.agent_env()}
    assert "DODUO_PORT" in {v["name"] for v in core.agent_env()}

    none_of_ours = {v["name"] for v in core.agent_env([])}
    assert {"VERIFY_SSL", "PREFERRED_INTERFACE"} <= none_of_ours
    assert "DODUO_PORT" not in none_of_ours


def test_a_funcid_this_tool_has_no_options_for_takes_nothing_away():
    """Real accounts carry funcIds no functionality here names -- tdm,
    dataPublisher, delphix. A location with one of those and `performance` runs
    performance, and an unrecognised id must not narrow that: filtering is over
    what a tag *claims*, so an id nothing claims claims nothing."""
    assert {v["name"] for v in core.agent_env(["performance", "tdm"])} \
        == {v["name"] for v in core.agent_env(["performance"])}
    assert {v["name"] for v in core.agent_env(["tdm"])} \
        == {v["name"] for v in core.agent_env([])}


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
    # The subtraction is not the functionality filter's to skip: a location
    # that runs everything is still offered nothing an option here writes.
    everything = [f["id"] for f in core.FUNCTIONALITIES]
    assert not {v["name"] for v in core.agent_env(everything)} \
        & generate.RESERVED_ENV


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


def test_a_variable_holding_a_path_is_never_typed_pem():
    """#181. `pem` puts a certificate textarea on the page, so it is a claim
    about what the variable holds -- and the docker TLS pair holds a *path*.
    BlazeMeter's own example sets `TLS_CERT=/etc/ssl/certs/public.pem` beside
    the `-v` that mounts the file there, which is the shape REQUESTS_CA_BUNDLE
    and AWS_CA_BUNDLE already carry here: a string whose example is the path.

    Typed `pem`, the one control that invites a value invited the wrong one --
    a pasted certificate is an agent that starts, reports online and serves no
    TLS, which is the silent failure the placeholder rule exists for. So the
    type is asserted, and so is the example: the path is what the row shows in
    the empty box, and it is the only place the shape is on screen.
    """
    paths = ("REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "TLS_CERT", "TLS_KEY")
    for name in paths:
        v = agent_env.AGENT_ENV_BY_NAME[name]
        assert v["type"] == "string", name
        # Stated on the row one way or the other -- a default where BlazeMeter
        # documents one, an example where it does not.
        assert (v["default"] or v["example"]).startswith("/"), name

    # The summary is what says the path is the *container's*, which no name
    # carries and no mount here supplies (#182).
    for name in ("TLS_CERT", "TLS_KEY"):
        summary = agent_env.AGENT_ENV_BY_NAME[name]["summary"]
        assert "container" in summary and "mount" in summary, name
        assert len(summary.split()) <= 20, name

    # ...and the _GRID pair is not this ticket's. It is declared for both
    # platforms and only Docker's side is documented as a path (#186), so it
    # keeps the textarea until somebody reads what Kubernetes expects.
    for name in ("TLS_CERT_GRID", "TLS_KEY_GRID"):
        assert agent_env.AGENT_ENV_BY_NAME[name]["type"] == "pem", name
        assert agent_env.AGENT_ENV_BY_NAME[name]["platforms"] \
            == list(agent_env.BOTH), name


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
