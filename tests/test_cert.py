"""cert.py against real certificates (`tls_fixtures.py`)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from bzm_opl_gen import cert  # noqa: E402
from tls_fixtures import (  # noqa: E402
    SV_CERT, SV_CERT_NO_NAMES, SV_HOST, SV_KEY, SV_NAMES, SV_WILDCARD_HOST,
    SV_WRONG_HOST)


def test_the_names_are_the_san_then_the_common_name():
    """The names are the SAN's dNSNames, then the Common Name."""
    assert cert.dns_names(SV_CERT) == SV_NAMES


def test_could_not_read_and_covers_nothing_are_different_answers():
    """None (not read) and [] (read, names nothing) are different answers."""
    assert cert.dns_names(SV_CERT_NO_NAMES) == []
    assert cert.dns_names("not a certificate at all") is None
    assert cert.dns_names(SV_KEY) is None
    assert cert.dns_names("") is None
    assert cert.dns_names(None) is None
    # A certificate envelope with rubbish inside is not read.
    corrupt = SV_CERT.replace(SV_CERT.splitlines()[3], "AAAA")
    assert cert.dns_names(corrupt) is None


def test_a_missing_san_extension_is_not_a_read_failure():
    """A certificate with no SAN falls through to its Common Name."""
    assert cert.dns_names(SV_CERT_NO_NAMES) is not None


def test_is_certificate_pem_is_a_separate_question():
    """"Not a certificate" is a separate question from "could not read its names"."""
    assert cert.is_certificate_pem(SV_CERT)
    assert not cert.is_certificate_pem(SV_KEY)
    assert not cert.is_certificate_pem("")


def test_a_wildcard_covers_one_label_and_no_more():
    """A wildcard covers exactly one label."""
    assert cert.matches(SV_HOST, SV_NAMES)
    assert cert.matches(SV_WILDCARD_HOST, SV_NAMES)
    assert not cert.matches("a.b." + SV_HOST, SV_NAMES)
    assert not cert.matches("example.com", SV_NAMES)
    assert not cert.matches(SV_WRONG_HOST, SV_NAMES)


def test_matching_is_case_insensitive_and_ignores_a_trailing_dot():
    """Both are the same host to DNS and to every client, and a check that
    disagreed would refuse a bundle that works."""
    assert cert.matches(SV_HOST.upper(), SV_NAMES)
    assert cert.matches(SV_HOST + ".", SV_NAMES)
    assert cert.matches(SV_HOST, [n.upper() for n in SV_NAMES])


def test_nothing_matches_nothing():
    """An empty name list matches nothing, and an empty hostname matches nothing."""
    assert not cert.matches(SV_HOST, [])
    assert not cert.matches("", SV_NAMES)
    assert not cert.matches(None, SV_NAMES)


def test_a_wildcard_in_the_middle_is_a_literal():
    """Nothing accepts `w*.example.com`, so honouring it here would pass a
    bundle every client rejects."""
    assert not cert.matches("web.example.com", ["w*.example.com"])
    assert cert.matches("w*.example.com", ["w*.example.com"])
