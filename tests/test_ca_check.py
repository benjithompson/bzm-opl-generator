"""CA trust checked before deploying: the lint of a bundle file, and the chain
check against a network (a local TLS server and CONNECT proxy, never the
internet)."""

import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from bzm_opl_gen import (bundle_names, ca_check, cert, cli, core,  # noqa: E402
                         footprint)
import ca_fixtures as F  # noqa: E402


def _severities(data):
    return [(f["severity"], f["cert"]) for f in cert.lint(data)["findings"]]


# -- the lint ---------------------------------------------------------------

def test_a_root_and_its_intermediate_have_nothing_to_say():
    lint = cert.lint(F.CHAIN_PEM)
    assert lint["findings"] == []
    assert [c["role"] for c in lint["certificates"]] == ["intermediate", "root"]
    root = lint["certificates"][1]
    assert root["subject"] == "CN=Corp Root CA,O=bzm-opl-gen test fixture"
    assert root["self_signed"] and root["is_ca"]
    assert root["days_left"] > 3000
    assert len(root["sha256"].split(":")) == 32


def test_a_server_certificate_where_a_ca_belongs_fails_and_names_its_issuer():
    """The commonest mistake: the leaf exported from a browser padlock."""
    lint = cert.lint(F.LEAF_PEM)
    assert _severities(F.LEAF_PEM) == [("FAIL", 1)]
    assert "Corp Issuing CA" in lint["findings"][0]["message"]
    assert lint["certificates"][0]["role"] == "leaf"


def test_an_expired_ca_fails_and_one_about_to_expire_warns():
    assert _severities(F.pem(F.EXPIRED_ROOT)) == [("FAIL", 1)]
    assert _severities(F.pem(F.EXPIRING_ROOT)) == [("WARN", 1)]
    # The 30-day line is measured from `now`, so a later now crosses it.
    later = F.NOW + datetime.timedelta(days=3640)
    assert _severities(F.pem(F.ROOT)) == []
    assert ("WARN", 1) in [(f["severity"], f["cert"])
                           for f in cert.lint(F.pem(F.ROOT), later)["findings"]]


def test_an_intermediate_without_its_root_warns_and_says_why():
    lint = cert.lint(F.INTERMEDIATE_PEM)
    assert _severities(F.INTERMEDIATE_PEM) == [("WARN", 1)]
    said = lint["findings"][0]["message"]
    assert "Corp Root CA" in said and "system store" in said


def test_a_duplicate_is_a_note_not_a_warning():
    assert _severities(F.pem(F.ROOT, F.ROOT)) == [(cert.NOTE, 2)]


def test_no_certificate_is_a_fail_and_every_bad_block_is_named():
    """Nothing is dropped silently: a key, a truncated block and a block that
    does not parse are each reported, beside the certificate that did."""
    key = F._key_pem(F.ROOT_KEY)
    broken = F.ROOT_PEM.replace(F.ROOT_PEM.splitlines()[2], "AAAA")
    truncated = F.ROOT_PEM.split("-----END")[0]
    lint = cert.lint(F.ROOT_PEM + key + broken + truncated)
    assert len(lint["certificates"]) == 1
    said = " ".join(f["message"] for f in lint["findings"])
    assert "private key" in said
    assert "does not parse" in said
    assert "no matching END" in said
    assert all(f["severity"] == "WARN" for f in lint["findings"])

    for nothing in (b"", "", "not a certificate", key):
        assert ("FAIL", None) in _severities(nothing), nothing


@pytest.mark.parametrize("data,form", [
    (F.ROOT_DER, "der"),
    (F.CHAIN_P7B_DER, "pkcs7"),
    (F.CHAIN_P7B_PEM, "pem"),
    (F.CHAIN_PEM_CRLF, "pem"),
])
def test_windows_exports_are_read(data, form):
    lint = cert.lint(data)
    assert lint["form"] == form
    assert lint["certificates"] and not lint["findings"]


def test_normalise_gives_pem_and_keeps_a_pem_bundle_as_written():
    assert cert.normalise(F.ROOT_DER) == F.ROOT_PEM
    # PKCS#7 holds a SET, so the order is the encoder's; the certificates are
    # the same ones.
    for p7b in (F.CHAIN_P7B_DER, F.CHAIN_P7B_PEM):
        pem = cert.normalise(p7b)
        assert "PKCS7" not in pem
        assert (set(cert.read_bundle(pem).certs)
                == {F.INTERMEDIATE, F.ROOT})
    assert cert.normalise(F.CHAIN_PEM_CRLF) == F.CHAIN_PEM
    # Comments survive: a public bundle labels each root with one.
    commented = "# Corp Root CA\n" + F.ROOT_PEM
    assert cert.normalise(commented.replace("\n", "\r\n")) == commented
    assert cert.normalise(b"\xef\xbb\xbf" + F.ROOT_PEM.encode()) == F.ROOT_PEM


def test_verify_pem_carries_the_certificates_alone():
    commented = "# Főtanúsítvány\n" + F.ROOT_PEM + "junk\n"
    assert cert.verify_pem(commented) == F.ROOT_PEM
    assert cert.verify_pem(b"") == ""


@pytest.mark.parametrize("bundle,missing", [
    (F.ROOT_PEM, None),
    (F.CHAIN_PEM, None),
    (F.INTERMEDIATE_PEM, "Corp Root CA"),        # no root to end on
    (F.LEAF_PEM, "Corp Root CA"),
    (F.pem(F.OTHER_ROOT), "Corp Root CA"),
    ("", "Corp Root CA"),
])
def test_the_missing_issuer_is_the_root_the_path_needs(bundle, missing):
    chain = [c.public_bytes(cert.serialization.Encoding.DER)
             for c in (F.LEAF, F.INTERMEDIATE)]
    found = cert.missing_issuer(chain, bundle)
    assert (found is None) if missing is None else missing in found
    assert cert.missing_issuer([], bundle) is None


def test_a_presented_root_the_bundle_lacks_is_named():
    chain = [c.public_bytes(cert.serialization.Encoding.DER)
             for c in (F.LEAF, F.INTERMEDIATE, F.ROOT)]
    assert "Corp Root CA" in cert.missing_issuer(chain, F.pem(F.OTHER_ROOT))
    assert cert.missing_issuer(chain, F.ROOT_PEM) is None


# -- the chain check --------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    with F.tls_server() as port:
        yield f"127.0.0.1:{port}"


def _check(data, target, **kw):
    return core.ca_check(data, hosts=[target], env={}, timeout=5, **kw)


@pytest.mark.parametrize("bundle", [F.ROOT_PEM, F.CHAIN_PEM, F.ROOT_DER,
                                    F.CHAIN_P7B_DER, F.CHAIN_PEM_CRLF])
def test_a_bundle_holding_the_root_verifies_the_chain(server, bundle):
    r = _check(bundle, server)
    host = r["hosts"][0]
    assert host["status"] == ca_check.VERIFIED and r["ok"]
    assert host["missing_issuer"] is None
    # The chain the far end presented, leaf first, as a customer reads it.
    assert [d["subject"].split(",")[0] for d in host["chain"]] == [
        "CN=127.0.0.1", "CN=Corp Issuing CA"]


@pytest.mark.parametrize("bundle", [F.INTERMEDIATE_PEM, F.LEAF_PEM,
                                    F.pem(F.OTHER_ROOT), b""])
def test_a_bundle_without_the_root_does_not_verify_and_names_it(server,
                                                                  bundle):
    """An intermediate alone is refused, as OpenSSL and curl refuse it, even
    where Python's own default context would accept a partial chain."""
    r = _check(bundle, server)
    host = r["hosts"][0]
    assert host["status"] == ca_check.NOT_VERIFIED and not r["ok"]
    assert "Corp Root CA" in host["missing_issuer"]
    assert host["chain"], "the presented chain is shown even when it fails"


def test_an_unreachable_host_is_no_verdict(server):
    r = _check(F.ROOT_PEM, f"127.0.0.1:{F.closed_port()}")
    host = r["hosts"][0]
    assert host["status"] == ca_check.UNREACHABLE
    assert host["chain"] == [] and host["missing_issuer"] is None
    assert "refused" in host["detail"]
    # Not judged is not failed.
    assert r["ok"]


def test_a_lint_fail_alone_makes_the_check_fail(server):
    r = _check(F.ROOT_PEM + F.LEAF_PEM, server)
    assert r["hosts"][0]["status"] == ca_check.VERIFIED
    assert not r["ok"]


def test_the_check_goes_through_a_connect_proxy_with_credentials(server):
    with F.connect_proxy("corp user:s3cret") as (port, seen):
        r = _check(F.ROOT_PEM,
                   server, proxy=f"http://corp%20user:s3cret@127.0.0.1:{port}")
        host = r["hosts"][0]
        assert host["status"] == ca_check.VERIFIED
        assert seen and seen[0].startswith(f"CONNECT {server} HTTP/1.1")
        # What is reported names the proxy, never its password.
        assert "s3cret" not in json.dumps(r)
        assert host["proxy"].startswith(f"http://127.0.0.1:{port}")


def test_a_proxy_that_refuses_is_unreachable_not_unverified(server):
    with F.connect_proxy("u:p") as (port, _):
        host = _check(F.ROOT_PEM, server,
                      proxy=f"http://127.0.0.1:{port}")["hosts"][0]
    assert host["status"] == ca_check.UNREACHABLE
    assert "407" in host["detail"]


def test_https_proxy_from_the_environment_and_no_proxy(server):
    with F.connect_proxy() as (port, seen):
        env = {"HTTPS_PROXY": f"http://127.0.0.1:{port}"}
        core.ca_check(F.ROOT_PEM, hosts=[server], env=env, timeout=5)
        tunnelled = len(seen)
        assert tunnelled
        core.ca_check(F.ROOT_PEM, hosts=[server], timeout=5,
                      env=dict(env, NO_PROXY="localhost,127.0.0.1"))
        assert len(seen) == tunnelled, "NO_PROXY named the host"


def test_a_bad_proxy_url_is_refused_before_any_connection():
    with pytest.raises(core.BadRequest) as e:
        core.ca_check(F.ROOT_PEM, hosts=["127.0.0.1:1"], env={},
                      proxy="https://u:secret@proxy.corp:8443")
    assert "secret" not in str(e.value)


def test_the_default_hosts_are_the_api_and_the_engine_upload_hosts():
    hosts = core.ca_check_hosts()
    assert hosts[0] == "a.blazemeter.com"
    assert hosts[1:] == list(footprint.ENGINE_UPLOAD_HOSTS)


def test_a_registry_prefix_is_checked_as_its_host(monkeypatch):
    seen = []
    monkeypatch.setattr(ca_check, "check_host", lambda target, pem, proxy,
                        timeout: seen.append(target) or {
                            "host": target, "port": 443, "chain": [],
                            "status": ca_check.UNREACHABLE, "detail": "x"})
    core.ca_check(F.ROOT_PEM, env={}, registry="reg.corp:5001/blazemeter")
    assert seen == [*core.ca_check_hosts(), "reg.corp:5001"]


def test_split_host():
    assert ca_check.split_host("a.example.com") == ("a.example.com", 443)
    assert ca_check.split_host("reg.corp:5001") == ("reg.corp", 5001)
    assert ca_check.split_host("[::1]:8443") == ("::1", 8443)
    assert ca_check.split_host("::1") == ("::1", 443)
    assert ca_check.split_host("[::1]") == ("::1", 443)


# -- the file, unread versus empty --------------------------------------------

def test_a_file_that_cannot_be_opened_is_not_a_bundle_with_no_certificate(
        tmp_path):
    with pytest.raises(core.CaBundleUnreadable):
        core.read_ca_bundle(str(tmp_path / "missing.pem"))
    empty = tmp_path / "empty.pem"
    empty.write_text("")
    assert ("FAIL", None) in _severities(core.read_ca_bundle(str(empty)))


# -- the command ------------------------------------------------------------

def _run(monkeypatch, *args):
    monkeypatch.setattr("sys.argv", ["bzm-opl-gen", *args])
    with pytest.raises(SystemExit) as e:
        cli.main()
    return e.value.code


def test_ca_check_prints_the_chain_and_exits_by_verdict(monkeypatch, capsys,
                                                        tmp_path, server):
    good = tmp_path / "corp-root.cer"
    good.write_bytes(F.ROOT_DER)
    assert _run(monkeypatch, "ca-check", "--ca-bundle", str(good),
                "--host", server) == 0
    out = capsys.readouterr().out
    assert "read as a DER certificate" in out
    assert f"{server}: VERIFIED" in out
    assert "presented CN=127.0.0.1" in out

    bad = tmp_path / "leaf.pem"
    bad.write_text(F.LEAF_PEM)
    assert _run(monkeypatch, "ca-check", "--ca-bundle", str(bad),
                "--host", server) == 1
    out = capsys.readouterr().out
    assert "NOT VERIFIED" in out and "missing: CN=Corp Root CA" in out
    assert "FAIL  Certificate 1" in out


def test_ca_check_json_and_an_unreadable_file(monkeypatch, capsys, tmp_path,
                                              server):
    good = tmp_path / "root.pem"
    good.write_text(F.ROOT_PEM)
    assert _run(monkeypatch, "ca-check", "--ca-bundle", str(good), "--host",
                server, "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] and body["hosts"][0]["status"] == "verified"
    code = _run(monkeypatch, "ca-check", "--ca-bundle",
                str(tmp_path / "nope.pem"), "--host", server)
    assert "could not read the CA bundle" in str(code)


def test_a_large_bundle_lists_only_what_is_worth_reading(capsys):
    roots = [F._cert(f"Public Root {n}", F.ROOT_KEY) for n in range(30)]
    cli.print_ca_lint(cert.lint(F.pem(*roots, F.LEAF)))
    out = capsys.readouterr().out
    # Thirty roots with nothing to say are counted, not listed.
    assert "and 30 more root(s)" in out
    assert "Public Root 7," not in out
    assert "leaf (not a CA)" in out
    cli.print_ca_lint(cert.lint(F.pem(*roots)), verbose=True)
    assert "more root(s)" not in capsys.readouterr().out


# -- at generate time -------------------------------------------------------

def test_an_inline_bundle_is_linted_and_every_other_mode_is_not():
    said = core.ca_bundle_warnings({"ca_bundle": F.LEAF_PEM})
    assert len(said) == 1 and said[0].startswith("CA bundle FAIL: ")
    assert "written anyway" in said[0]
    assert core.ca_bundle_warnings({"ca_bundle": F.CHAIN_PEM}) == []
    assert core.ca_bundle_warnings(
        {"ca_bundle": F.INTERMEDIATE_PEM})[0].startswith("CA bundle WARN: ")
    for other in ({}, {"ca_bundle_slot": True},
                  {"ca_existing_configmap": "corp-ca"},
                  {"ca_openshift_inject": True},
                  # Two modes: generate refuses that itself.
                  {"ca_bundle": F.LEAF_PEM, "ca_bundle_slot": True},
                  {"ca_bundle": "<CA_BUNDLE>"}):
        assert core.ca_bundle_warnings(other) == [], other


def test_generate_warnings_are_plain_prose():
    """They reach the page and the MCP client as well as a terminal."""
    for data in (F.LEAF_PEM, F.pem(F.EXPIRED_ROOT, F.EXPIRED_ROOT),
                 F.INTERMEDIATE_PEM, "garbage", F._key_pem(F.ROOT_KEY)):
        said = core.ca_bundle_warnings({"ca_bundle": data})
        assert said, data
        for w in said:
            assert not any(t in w for t in ("`", "--", "->", "**")), w


def _generate(monkeypatch, tmp_path, bundle_file):
    facts = tmp_path / "facts.json"
    facts.write_text(open("examples/facts.example.json").read())
    monkeypatch.setattr("sys.argv", [
        "bzm-opl-gen", "generate", "--facts", str(facts), "-o",
        str(tmp_path / "out"), "--ca-bundle", str(bundle_file)])
    cli.main()
    return (tmp_path / "out" / bundle_names.CA_CONFIGMAP_FILE).read_text()


def test_generate_reads_a_windows_export_as_pem(monkeypatch, tmp_path,
                                                capsys):
    """A .p7b from the Windows certificate export wizard used to stop generate
    with a decode error; it arrives in the bundle as PEM."""
    p7b = tmp_path / "corp.p7b"
    p7b.write_bytes(F.CHAIN_P7B_DER)
    written = _generate(monkeypatch, tmp_path, p7b)
    assert written.count("BEGIN CERTIFICATE") == 2
    assert "PKCS7" not in written
    assert "CA bundle" not in capsys.readouterr().err


def test_generate_warns_loudly_and_still_writes(monkeypatch, tmp_path, capsys):
    leaf = tmp_path / "server.crt"
    leaf.write_text(F.LEAF_PEM)
    written = _generate(monkeypatch, tmp_path, leaf)
    assert "BEGIN CERTIFICATE" in written
    err = capsys.readouterr().err
    assert "CA bundle FAIL: Certificate 1" in err
