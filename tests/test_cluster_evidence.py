"""Preflighting from an evidence file: the imported and live paths give
identical Check lists, and a null section stays an unverified WARN, never a
FAIL."""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import (cli, doctor, evidence, facts as facts_mod, kube)  # noqa: E402
# One document for every test that reads one, and the files a collector really
# wrote. The cluster objects inside it are `test_doctor`'s, so the imported and
# the live paths are fed literally the same objects.
from evidence_fixtures import (CLASSES, CLUSTER_SCOPED_DENIED,  # noqa: E402
                               DEGRADED, FILES as FIXTURES, HALF_READ,
                               NAMESPACE_DENIED, NODES, SCOPED, document,
                               load, raw)
from test_doctor import (FACTS, NS_BASELINE, SV_NGINX, _big,  # noqa: E402
                         _find, _statuses)

EXAMPLE_FACTS = os.path.join(os.path.dirname(__file__), "..", "examples",
                             "facts.example.json")

OPTS = {"platform": "k8s"}


def _live(monkeypatch, **served):
    """gather_cluster() against a kubectl that answers with the same documents.
    `served` overrides a kind with kget's failure shape ({})."""
    answers = {"nodes": NODES, "ingressclass": CLASSES, "ns": NS_BASELINE,
               "limitrange,resourcequota,serviceaccount": SCOPED}
    answers.update(served)
    monkeypatch.setattr(kube, "kget",
                        lambda cli, ns, kind, name=None: answers[kind])
    monkeypatch.setattr(kube, "kget_named",
                        lambda cli, ns, kind, name=None: answers[kind])
    return doctor.gather_cluster("kubectl", "blazemeter")


# -- the prefactor: verdicts as data ----------------------------------------

def test_evaluate_returns_the_verdicts_without_printing(capsys):
    """The seam the web UI needs: the same Check list `run` reports, with no
    stdout side effect at all."""
    checks = doctor.evaluate(FACTS, OPTS, "blazemeter",
                             cluster_data=_evidence_cluster(), probes={})
    assert checks and all(isinstance(c, doctor.Check) for c in checks)
    assert capsys.readouterr().out == ""


def test_run_reports_exactly_what_evaluate_decided(capsys):
    """`run` stays the CLI's entry point -- evaluate, then print -- so splitting
    the two cannot change what the command says or exits with."""
    cluster = _evidence_cluster()
    checks = doctor.run(FACTS, OPTS, "blazemeter", cluster_data=cluster, probes={})
    out = capsys.readouterr().out
    assert checks == doctor.evaluate(FACTS, OPTS, "blazemeter",
                                     cluster_data=cluster, probes={})
    assert out.startswith("doctor: location Test Location")
    assert "location slots" in out
    # The summary the browser shows too, printed rather than composed again.
    assert doctor.summary_line(checks) in out


def test_run_prints_extra_checks_the_caller_already_made(capsys):
    """extra_checks lead the printed report."""
    mine = doctor.Check("cluster evidence", doctor.WARN, "collected elsewhere")
    checks = doctor.run(FACTS, OPTS, "blazemeter", cluster_data=_evidence_cluster(),
                        probes={}, extra_checks=[mine])
    assert checks[0] == mine
    assert "collected elsewhere" in capsys.readouterr().out


def test_an_evidence_is_passed_whole_rather_than_taken_apart():
    """evidence= and the three spelled-out parts give the same result."""
    imported = doctor.cluster_from_evidence(document(), "blazemeter")
    assert doctor.evaluate(FACTS, OPTS, "blazemeter", evidence=imported) == \
        doctor.evaluate(FACTS, OPTS, "blazemeter", cluster_data=imported.cluster,
                        probes=imported.probes, extra_checks=imported.checks)


def test_an_evidence_and_the_parts_it_carries_are_not_combined():
    """Both spellings at once would have one set silently win, and which is not
    something a reader of the call site could tell."""
    imported = doctor.cluster_from_evidence(document(), "blazemeter")
    with pytest.raises(TypeError):
        doctor.evaluate(FACTS, OPTS, "blazemeter", evidence=imported,
                        extra_checks=[doctor.Check("x", doctor.WARN, "y")])


def test_an_empty_evidence_still_means_go_and_look(monkeypatch):
    """An Evidence of Nones still reads the live cluster."""
    monkeypatch.setattr(kube, "cli_tool", lambda: "kubectl")
    monkeypatch.setattr(doctor, "gather_cluster",
                        lambda cli, ns: _evidence_cluster())
    monkeypatch.setattr(doctor, "probe_egress",
                        lambda cli, ns, opts: {doctor.API_PROBE_URL: 0})
    assert doctor.evaluate(FACTS, OPTS, "blazemeter",
                           evidence=doctor.Evidence(None, None, ())) == \
        doctor.evaluate(FACTS, OPTS, "blazemeter")


def _evidence_cluster():
    return doctor.cluster_from_evidence(document()).cluster


# -- parity with the live path ----------------------------------------------

def test_imported_evidence_normalises_to_what_gather_cluster_returns(monkeypatch):
    assert _evidence_cluster() == _live(monkeypatch)


def test_the_same_cluster_produces_the_same_verdicts_either_way(monkeypatch):
    """The same cluster gives identical Check lists live and from evidence."""
    live = doctor.evaluate(FACTS, SV_NGINX, "blazemeter",
                           cluster_data=_live(monkeypatch), probes={})
    imported = doctor.evaluate(FACTS, SV_NGINX, "blazemeter",
                               cluster_data=_evidence_cluster(), probes={})
    assert imported == live


# -- null vs [] --------------------------------------------------------------

@pytest.mark.parametrize("section,check,live_kind", [
    ("nodes", "capacity", "nodes"),
    ("ingressclasses", "ingress class", "ingressclass"),
    ("scoped", "limitrange", "limitrange,resourcequota,serviceaccount"),
])
def test_a_section_that_could_not_be_read_warns_rather_than_failing(
        monkeypatch, section, check, live_kind):
    """A null section WARNs on both paths."""
    imported = doctor.evaluate(FACTS, SV_NGINX, "blazemeter", probes={},
                               cluster_data=doctor.cluster_from_evidence(
                                   document(raw=raw(**{section: None}))).cluster)
    live = doctor.evaluate(FACTS, SV_NGINX, "blazemeter", probes={},
                           cluster_data=_live(monkeypatch, **{live_kind: {}}))
    assert imported == live
    assert _find(imported, check).status == doctor.WARN
    assert doctor.FAIL not in _statuses(imported)


def test_an_empty_section_still_fails_where_it_should(monkeypatch):
    """The other half of the distinction: the cluster served the kind and has
    none of it. Nothing will claim crane's Ingress, and that is a FAIL."""
    empty = {"apiVersion": "v1", "kind": "List", "items": []}
    cluster = doctor.cluster_from_evidence(
        document(raw=raw(ingressclasses=empty))).cluster
    assert cluster["ingressclasses"] == []
    assert _find(doctor.evaluate(FACTS, SV_NGINX, "blazemeter", probes={},
                                 cluster_data=cluster),
                 "ingress class").status == doctor.FAIL


def test_a_namespace_nobody_could_read_is_not_reported_as_one_that_is_absent():
    """`raw.namespace: null` is unread, not "does not exist yet"."""
    doc = document(raw=raw(namespace=None),
                   notes=["namespace: Error from server (Forbidden)"])
    imported = doctor.cluster_from_evidence(doc, "blazemeter")
    assert imported.cluster["namespace"] is None
    c = _find(doctor.evaluate(FACTS, OPTS, "blazemeter", probes={},
                              cluster_data=imported.cluster), "admission")
    assert c.status == doctor.WARN
    assert "does not exist" not in c.detail
    assert "could not be read" in c.detail


def test_an_unreadable_quota_is_not_a_pass(monkeypatch):
    """`no ResourceQuota in the namespace` is a claim, and a denied read is no
    basis for making it -- that is the one place an empty list PASSes."""
    denied = doctor.cluster_from_evidence(document(raw=raw(scoped=None))).cluster
    assert denied["quotas"] is None and denied["limitranges"] is None
    assert _find(doctor.evaluate(FACTS, OPTS, "blazemeter", probes={},
                                 cluster_data=denied), "quota").status == doctor.WARN


# -- the fully degraded file -------------------------------------------------

def test_the_script_output_from_a_machine_with_no_cluster_is_usable():
    """The all-null collector output parses and only warns."""
    with open(DEGRADED) as fh:
        doc = json.load(fh)
    imported = doctor.cluster_from_evidence(doc, "some-ns")
    assert imported.cluster == {"nodes": None, "ingressclasses": None,
                                "limitranges": None, "quotas": None,
                                "serviceaccounts": None, "namespace": None}
    checks = doctor.evaluate(FACTS, OPTS, "some-ns", cluster_data=imported.cluster,
                             probes=imported.probes, extra_checks=imported.checks)
    assert doctor.FAIL not in _statuses(checks)
    for name in ("capacity", "limitrange", "quota", "admission", "egress"):
        assert _find(checks, name).status == doctor.WARN
    # Status alone is not the whole verdict: this file's collector had no
    # kubeconfig at all, so every WARN above has to say it did not look --
    # "the namespace does not exist yet, create it" would be a WARN stating a
    # fact this file cannot support.
    assert "could not be read" in _find(checks, "admission").detail
    # The script's own errors explain every null above; dropping them would
    # leave the reader with six WARNs and no reason for any of them.
    assert "Missing or incomplete configuration" in _find(checks, "evidence").detail


def test_no_account_and_no_cluster_reports_nothing_as_broken():
    """Hand-entered facts plus an all-null evidence file report no failures."""
    with open(DEGRADED) as fh:
        imported = doctor.cluster_from_evidence(json.load(fh), "some-ns")
    checks = doctor.evaluate(facts_mod.manual("aaa111", "bbb222"), OPTS, "some-ns",
                             cluster_data=imported.cluster, probes=imported.probes,
                             extra_checks=imported.checks)
    assert not doctor.has_failures(checks)
    for name in ("location slots", "location threadsPerEngine"):
        assert _find(checks, name).status == doctor.WARN


# -- the half-read files -----------------------------------------------------
#
# Some sections read, some refused (a real customer token's shape): what was
# read reaches a verdict and what was not a WARN, in the same report.

def _checks(path, opts, namespace="blazemeter"):
    return doctor.evaluate(FACTS, opts, namespace,
                           evidence=doctor.cluster_from_evidence(load(path),
                                                                 namespace))


@pytest.mark.parametrize("path", HALF_READ, ids=os.path.basename)
def test_a_half_read_file_fails_on_nothing_it_could_not_read(path):
    checks = _checks(path, {**OPTS, **SV_NGINX})
    assert doctor.FAIL not in _statuses(checks)
    # ...and every refusal is accounted for: what the collector recorded as
    # unreadable is what the leading verdict names.
    unreadable = doctor.evidence_summary(load(path))["unreadable"]
    assert unreadable
    for section in unreadable:
        assert section in _find(checks, "evidence").detail


def test_a_token_with_namespaced_rbac_only_still_judges_the_namespace():
    """Cluster-scoped reads refused: those verdicts WARN, namespaced ones are
    real."""
    checks = _checks(CLUSTER_SCOPED_DENIED, {**OPTS, **SV_NGINX})
    for name in ("capacity", "engine packing", "node disk", "sv ingress class"):
        c = _find(checks, name)
        assert c.status == doctor.WARN
        assert "could not be read" in c.detail
    for name in ("limitrange", "quota", "admission"):
        assert _find(checks, name).status == doctor.PASS


def test_a_reader_outside_the_namespace_does_not_report_it_as_absent():
    """Namespace refused with nodes read: the namespace is unread, not absent."""
    checks = _checks(NAMESPACE_DENIED, OPTS)
    admission = _find(checks, "admission")
    assert admission.status == doctor.WARN
    assert "could not be read" in admission.detail
    assert "does not exist" not in admission.detail
    for name in ("limitrange", "resourcequota"):
        assert _find(checks, name).status == doctor.WARN
    # The nodes were read, so the capacity verdicts are the cluster's own.
    assert _find(checks, "capacity").status == doctor.PASS


def test_egress_is_reported_unavailable_rather_than_guessed():
    """Egress is the one thing an evidence file cannot carry: it needs a pod in
    the namespace to curl from. Unknown, never an assumed PASS."""
    imported = doctor.cluster_from_evidence(document())
    assert imported.probes == {}
    checks = doctor.evaluate(FACTS, OPTS, "blazemeter", cluster_data=imported.cluster,
                             probes=imported.probes)
    assert _find(checks, "egress").status == doctor.WARN


# -- refusals and provenance -------------------------------------------------

@pytest.mark.parametrize("doc,found", [
    ({"raw": {}}, "no 'schema' field"),
    ({"schema": "bzm-opl-cluster-evidence/2", "raw": {}}, "bzm-opl-cluster-evidence/2"),
    ({"schema": "something else"}, "something else"),
    ([], "a JSON array"),
])
def test_an_unrecognised_schema_is_refused_by_name(doc, found):
    """A file from a newer script, or the wrong file entirely. Half-parsing one
    produces verdicts about a cluster nobody described."""
    with pytest.raises(ValueError) as e:
        doctor.cluster_from_evidence(doc)
    assert found in str(e.value)
    assert evidence.SCHEMA in str(e.value)     # and what was expected


def test_a_hand_edited_section_is_refused_by_name():
    """A raw section that is neither a document nor null is a named ValueError."""
    with pytest.raises(ValueError) as e:
        doctor.cluster_from_evidence(document(raw=raw(nodes=[_big("a")])))
    assert "raw.nodes" in str(e.value) and "list" in str(e.value)


def test_evidence_for_another_namespace_is_reported_not_quietly_used():
    """Evidence for another namespace WARNs rather than refusing."""
    imported = doctor.cluster_from_evidence(document(namespace="their-ns"),
                                            "blazemeter")
    c = _find(imported.checks, "evidence")
    assert c.status == doctor.WARN
    assert "their-ns" in c.detail and "blazemeter" in c.detail


def test_matching_namespace_just_says_where_the_data_came_from():
    c = _find(doctor.cluster_from_evidence(document(), "blazemeter").checks,
              "evidence")
    assert c.status == doctor.PASS
    assert "2026-07-27T10:00:00Z" in c.detail       # how stale the verdicts are
    assert "blazemeter" in c.detail


def test_the_summary_reports_the_same_mismatch_the_verdict_states():
    """evidence_summary's `elsewhere` agrees with the verdict."""
    doc = document(namespace="their-ns")
    assert doctor.evidence_summary(doc, "blazemeter")["elsewhere"] is True
    assert doctor.evidence_summary(doc, "their-ns")["elsewhere"] is False
    # ...and it agrees with the verdict, which is the same file read by the same
    # function.
    c = _find(doctor.cluster_from_evidence(doc, "blazemeter").checks, "evidence")
    assert c.status == doctor.WARN


def test_a_file_naming_no_namespace_is_not_a_mismatch():
    """A file naming no namespace is not a mismatch."""
    doc = document(namespace=None)
    summary = doctor.evidence_summary(doc, "blazemeter")
    assert summary["elsewhere"] is False
    assert summary["namespace"] is None
    c = _find(doctor.cluster_from_evidence(doc, "blazemeter").checks, "evidence")
    assert c.status == doctor.PASS


def test_a_summary_nobody_named_a_namespace_for_claims_no_mismatch():
    """`evidence_summary` is also asked without one -- there is no preflight to
    compare against, which is not the same as agreeing."""
    assert doctor.evidence_summary(document(namespace="their-ns"))["elsewhere"] \
        is False


def test_notes_reach_the_report_because_they_explain_the_nulls():
    """Collector notes list the unreadable sections and each distinct reason
    once."""
    imported = doctor.cluster_from_evidence(document(
        raw=raw(nodes=None, ingressclasses=None),
        notes=["nodes: forbidden", "ingressclasses: forbidden"]))
    c = _find(imported.checks, "evidence")
    assert c.status == doctor.WARN
    assert "could not read nodes, ingressclasses" in c.detail
    assert c.detail.count("forbidden") == 1


# -- the command -------------------------------------------------------------

def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    # Nothing may reach for a cluster on this path: the point is a preflight
    # from a laptop with no kubeconfig at all.
    monkeypatch.setattr(kube, "cli_tool",
                        lambda: pytest.fail("doctor went looking for a cluster"))
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_doctor_runs_from_an_evidence_file_with_no_cluster(monkeypatch, capsys,
                                                           tmp_path):
    """`doctor --cluster-evidence` over an all-null file exits 0 with warnings."""
    monkeypatch.chdir(tmp_path)            # no out/profile.json anywhere near
    code = _run(monkeypatch, "doctor", "--facts", EXAMPLE_FACTS,
                "--cluster-evidence", DEGRADED)
    out = capsys.readouterr().out
    assert code == 0
    assert "cluster evidence" in out and "some-ns" in out
    assert "WARN" in out and "FAIL" not in out
    # The count pins that every null section was noticed (@reads and the source
    # guards in test_doctor.py name the check). Update it when a check is added.
    assert out.count("WARN") == 9, out


def test_doctor_takes_the_namespace_from_the_evidence(monkeypatch, capsys):
    """The script was run against a namespace; restating it on the command line
    is a chance to get it wrong."""
    _run(monkeypatch, "doctor", "--facts", EXAMPLE_FACTS,
         "--manifests", "", "--cluster-evidence", DEGRADED)
    assert "namespace some-ns" in capsys.readouterr().out


def test_doctor_reports_a_namespace_the_evidence_does_not_cover(monkeypatch,
                                                                capsys):
    _run(monkeypatch, "doctor", "--facts", EXAMPLE_FACTS, "--manifests", "",
         "--cluster-evidence", DEGRADED, "-n", "elsewhere")
    out = capsys.readouterr().out
    assert "namespace elsewhere" in out
    assert "some-ns" in _find_line(out, "evidence")


def test_doctor_refuses_a_file_that_is_not_cluster_evidence(monkeypatch,
                                                            tmp_path):
    """The likeliest wrong file is facts.json, and it must not traceback."""
    code = _run(monkeypatch, "doctor", "--facts", EXAMPLE_FACTS, "--manifests", "",
                "--cluster-evidence", EXAMPLE_FACTS)
    assert evidence.SCHEMA in str(code)
    assert "no 'schema' field" in str(code)


def test_doctor_says_where_to_get_an_evidence_file_it_cannot_find(monkeypatch,
                                                                  tmp_path):
    code = _run(monkeypatch, "doctor", "--facts", EXAMPLE_FACTS, "--manifests", "",
                "--cluster-evidence", str(tmp_path / "nope.json"))
    assert "nope.json" in str(code) and "bzm-cluster-evidence.sh" in str(code)


def _find_line(out, needle):
    return next(line for line in out.splitlines() if needle in line)


# -- the collector and the table it writes to --------------------------------
#
# The shell script and the fixtures are held to `evidence.DOCUMENT`; a script
# cannot import a Python table, so it is parsed.

COLLECTOR = os.path.join(os.path.dirname(__file__), "..", "scripts",
                         "bzm-cluster-evidence.sh")
# The line the script's emitting half opens with. Everything above it is
# argument parsing and the four helpers; everything below writes the document.
DOCUMENT_MARKER = "# -- the document"

# One key per line, at an indent that is its depth: two spaces for a top-level
# section, four for a key inside one, six for a permission probe. The three
# helpers emit theirs from the argument they are called with, always at the
# same depth -- get_json/get_names inside a section, can_i inside a permission
# group.
_PRINTF_KEY = re.compile(r"""^\s*printf '(\s*)"([^"%]+)":""")
_COLLECT = re.compile(r"^(?:get_json|get_names)\s+(\S+)")
_PROBE = re.compile(r'^can_i\s+"([^"]+)"')


def _collector_paths():
    """Every key the collector writes, as dotted paths, read off the script."""
    with open(COLLECTOR) as fh:
        script = fh.read()
    assert DOCUMENT_MARKER in script, (
        f"{DOCUMENT_MARKER!r} is what says where the document starts in "
        f"{COLLECTOR}; without it nothing holds the script to evidence.DOCUMENT")
    paths, trail = set(), [None, None, None]
    for line in script[script.index(DOCUMENT_MARKER):].splitlines():
        key = _PROBE.match(line) or _COLLECT.match(line)
        if key:
            indent = 6 if line.startswith("can_i") else 4
            key = key.group(1)
        elif _PRINTF_KEY.match(line):
            indent, key = (len(_PRINTF_KEY.match(line).group(1)),
                           _PRINTF_KEY.match(line).group(2))
        else:
            continue
        assert indent in (2, 4, 6), f"unexpected depth in {line!r}"
        depth = indent // 2 - 1
        trail[depth] = key
        paths.add(".".join(trail[:depth + 1]))
    return paths


def test_the_collector_writes_exactly_the_document_the_table_states():
    """The collector script writes exactly evidence.DOCUMENT's keys, compared
    both ways."""
    written, stated = _collector_paths(), set(evidence.collector_paths())
    assert not stated - written, (
        f"bzm_opl_gen/evidence.py states {sorted(stated - written)}, which "
        f"{evidence.SCRIPT} does not write -- so every reader of those paths "
        f"reads a section that is never collected")
    assert not written - stated, (
        f"{evidence.SCRIPT} writes {sorted(written - stated)}, which "
        f"bzm_opl_gen/evidence.py does not state -- so nothing reads them, and "
        f"a reader that renamed one would report it as unreadable instead")


def test_no_key_in_the_document_carries_a_dot():
    """No document key contains a dot."""
    for path in evidence.paths():
        assert evidence.known(*path.split(".")), path


@pytest.mark.parametrize("path", sorted(FIXTURES), ids=os.path.basename)
def test_every_evidence_fixture_carries_the_document_the_table_states(path):
    """Every collected fixture file matches evidence.DOCUMENT."""
    with open(path) as fh:
        doc = json.load(fh)
    assert _paths_in(doc) == set(evidence.collector_paths())


def test_the_built_document_carries_the_same_shape_as_a_collected_one():
    """The builder is a fixture like the files are, and a section renamed in
    the table leaves it building the old shape just as quietly."""
    assert _paths_in(document()) == set(evidence.collector_paths())


def _paths_in(doc, prefix=()):
    """Every dotted path a fixture carries, as deep as the table goes."""
    stated = set(evidence.collector_paths())
    paths = set()
    for key, value in doc.items():
        here = prefix + (key,)
        dotted = ".".join(here)
        paths.add(dotted)
        if isinstance(value, dict) and any(p.startswith(dotted + ".")
                                           for p in stated):
            paths |= _paths_in(value, here)
    return paths
