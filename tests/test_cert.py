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


def _zero_serial_root():
    """A root whose serial is zero, as some public roots' are. The builder
    refuses a non-positive serial, so serial 1 is written and its DER byte
    changed; nothing here checks the signature."""
    import base64
    import warnings

    import ca_fixtures as F
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    key = F._key()
    der = (x509.CertificateBuilder().subject_name(F._name("Zero Serial Root"))
           .issuer_name(F._name("Zero Serial Root"))
           .public_key(key.public_key()).serial_number(1)
           .not_valid_before(F.NOW - F.DAY).not_valid_after(F.NOW + F.DAY)
           .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                          critical=True)
           .sign(key, hashes.SHA256())
           .public_bytes(serialization.Encoding.DER))
    # TBSCertificate: version [0] v3, then serial INTEGER 1.
    before = b"\xa0\x03\x02\x01\x02\x02\x01\x01"
    assert der.count(before) == 1
    der = der.replace(before, before[:-1] + b"\x00")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        x509.load_der_x509_certificate(der)
    # The fixture is only worth having if cryptography warns about it.
    assert caught, "cryptography no longer warns about a zero serial"
    body = base64.encodebytes(der).decode()
    return der, ("-----BEGIN CERTIFICATE-----\n" + body
                 + "-----END CERTIFICATE-----\n")


def test_a_zero_serial_root_loads_without_a_warning_escaping(recwarn):
    """Every load in cert.py silences cryptography's deprecation warning, and
    the certificate still reads."""
    der, pem = _zero_serial_root()
    recwarn.clear()
    assert cert.read_bundle(pem).certs and cert.read_bundle(der).certs
    assert cert.dns_names(pem) == ["Zero Serial Root"]
    assert cert.describe_der(der)["role"] == "root"
    assert cert.normalise(der).startswith("-----BEGIN CERTIFICATE-----")
    assert cert.missing_issuer([der], pem) is None
    assert cert.lint(pem) is not None
    assert [str(w.message) for w in recwarn] == []
