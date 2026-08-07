"""What a certificate says its names are, and the three answers it can give.

Every test here is against real material (`tls_fixtures.py`), because the whole
value of this module is that it reads certificates rather than strings that look
like them.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from bzm_opl_gen import cert  # noqa: E402
from tls_fixtures import (  # noqa: E402
    SV_CERT, SV_CERT_NO_NAMES, SV_HOST, SV_KEY, SV_NAMES, SV_WILDCARD_HOST,
    SV_WRONG_HOST)


def test_the_names_are_the_san_then_the_common_name():
    """Both, rather than the SAN alone. RFC 6125 says a CN is ignored once a
    SAN is present; BlazeMeter's own requirement is worded as an either/or, and
    where the two differ this takes the wider set -- the narrow reading would
    refuse a bundle whose certificate BlazeMeter say covers the hostname, and a
    false refusal is the expensive mistake."""
    assert cert.dns_names(SV_CERT) == SV_NAMES


def test_could_not_read_and_covers_nothing_are_different_answers():
    """The rule this module exists under. `None` is "this was not read" and
    nothing may be concluded from it; `[]` is a certificate that parsed and
    carries no host at all, which is a real answer a hostname cannot match.

    Collapsed into one, a PEM nobody could parse would be refused as a
    certificate covering nothing -- turning "we did not look" into "it is
    wrong", about a certificate that may well be fine.
    """
    assert cert.dns_names(SV_CERT_NO_NAMES) == []
    assert cert.dns_names("not a certificate at all") is None
    assert cert.dns_names(SV_KEY) is None
    assert cert.dns_names("") is None
    assert cert.dns_names(None) is None
    # ...and a certificate with the right envelope and rubbish inside it. This
    # is the case a header check alone would call read.
    corrupt = SV_CERT.replace(SV_CERT.splitlines()[3], "AAAA")
    assert cert.dns_names(corrupt) is None


def test_a_missing_san_extension_is_not_a_read_failure():
    """`ExtensionNotFound` is the certificate answering, not the reader giving
    up: BlazeMeter's own example hostname (`C123ABCXYZ`) is exactly the shape of
    thing somebody issues with a Common Name and no SAN at all, so the answer
    falls through to the subject rather than to None."""
    assert cert.dns_names(SV_CERT_NO_NAMES) is not None


def test_is_certificate_pem_is_a_separate_question():
    """"That is not a certificate" and "I could not read that certificate" have
    different fixes, so they are different calls: the first is refused outright
    and the second is reported as unchecked."""
    assert cert.is_certificate_pem(SV_CERT)
    assert not cert.is_certificate_pem(SV_KEY)
    assert not cert.is_certificate_pem("")


def test_a_wildcard_covers_one_label_and_no_more():
    """The rule every TLS client applies. Covering two labels here would pass a
    bundle that every client then rejects, which is the failure this check
    exists to catch, arrived at from the other side."""
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
    """A certificate that covers no host covers this one too -- and an empty
    hostname matches nothing, rather than matching the first name by being
    falsy at the wrong moment."""
    assert not cert.matches(SV_HOST, [])
    assert not cert.matches("", SV_NAMES)
    assert not cert.matches(None, SV_NAMES)


def test_a_wildcard_in_the_middle_is_a_literal():
    """Nothing accepts `w*.example.com`, so honouring it here would pass a
    bundle every client rejects."""
    assert not cert.matches("web.example.com", ["w*.example.com"])
    assert cert.matches("w*.example.com", ["w*.example.com"])
