"""One TLS pair for the docker agent's virtual-service certificate.

Real material, because `generate()` parses the certificate to check
`sv_hostname` against it. Built at import rather than checked in, so no private
key is ever committed (secret scanners would flag it). One RSA key signs every
certificate here; nothing asks whether two certificates share a key.

Equivalent to:

    openssl req -x509 -newkey rsa:2048 -keyout sv-tls.key -out sv-tls.crt \
        -days 7300 -nodes -subj "/CN=mocks.example.com/O=bzm-opl-gen test fixture" \
        -addext "subjectAltName=DNS:mocks.example.com,DNS:*.mocks.example.com"

`cert.py` reads names only, so validity dates do not matter.
"""

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# The names the certificate carries (the SAN's two entries; the Common Name
# duplicates the first), stated so a test asserts the reader against them.
SV_HOST = "mocks.example.com"
SV_WILDCARD_HOST = "anything.mocks.example.com"
SV_NAMES = ["mocks.example.com", "*.mocks.example.com"]
# A host the certificate does not cover, at the depth a wildcard cannot reach.
SV_WRONG_HOST = "mocks.example.org"

_ORG = "bzm-opl-gen test fixture"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_FROM = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
_TO = _FROM + datetime.timedelta(days=7300)


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _self_signed(subject, san):
    """A certificate over the one key, with `san` as its dNSNames (None: no
    SAN extension at all)."""
    name = x509.Name(subject)
    builder = (x509.CertificateBuilder()
               .subject_name(name)
               .issuer_name(name)
               .public_key(_KEY.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(_FROM)
               .not_valid_after(_TO)
               .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                              critical=True))
    if san is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in san]),
            critical=False)
    return _pem(builder.sign(_KEY, hashes.SHA256()))


SV_CERT = _self_signed(
    [x509.NameAttribute(NameOID.COMMON_NAME, SV_HOST),
     x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORG)],
    SV_NAMES)

# PKCS#8 -- `-----BEGIN PRIVATE KEY-----`, the syntax BlazeMeter require.
SV_KEY = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()).decode()

# The same key as PKCS#1 (`-----BEGIN RSA PRIVATE KEY-----`), which
# `generate()` refuses; only the syntax differs.
SV_KEY_PKCS1 = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()).decode()

# No SAN and no Common Name: it parses, and covers no host -- `[]`, not None.
SV_CERT_NO_NAMES = _self_signed(
    [x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORG)], None)
