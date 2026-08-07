"""One TLS pair, for the docker agent's own way of publishing a virtual service.

Real material rather than a plausible-looking string: `generate()` reads the
certificate to check `sv_hostname` against it, so a fixture that only looked
like PEM would exercise the refusal path in every test that meant to exercise
the accepting one.

**Built here rather than checked in**, which is the whole point of this file.
The pasted PEM this replaced was a self-signed key for a domain nobody owns --
genuinely useless to steal -- and it still had to go: this repo is public, its
`.gitignore` opens with "credentials -- never commit", and a committed
`-----BEGIN PRIVATE KEY-----` is a secret-scanner failure on every pull request
that touches it. A check people learn to scroll past is worse than no check, so
the fixture stopped being the thing that trips it. Nothing is stored, nothing
is in git history from here on, and there is no key to rotate.

Generating it costs one RSA keygen at import, which is why there is **one** key
and both certificates are signed with it: nothing here asks whether two
certificates share a key, and the second keygen would buy only symmetry.

One builder, for the same reason `evidence_fixtures.py` is one: two fixtures for
one schema, with different defaults, is how a test ends up asserting against
something no other test would recognise.

The `cryptography` import is the same one `bzm_opl_gen/cert.py` makes, so this
adds no dependency the package does not already declare (#182).

Equivalent to, and reproducible by:

    openssl req -x509 -newkey rsa:2048 -keyout sv-tls.key -out sv-tls.crt \
        -days 7300 -nodes -subj "/CN=mocks.example.com/O=bzm-opl-gen test fixture" \
        -addext "subjectAltName=DNS:mocks.example.com,DNS:*.mocks.example.com"

Validity is twenty years from whenever the suite runs, and nothing here would
care if it were yesterday -- `cert.py` reads names and asks nothing about
validity, which is a scope decision rather than an oversight.
"""

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# The names the certificate carries: the SAN's two entries, then the Common
# Name, which duplicates the first. Stated so a test can assert the *reader*
# rather than assert against whatever the reader happened to return.
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
    """A certificate over the one key, with `san` as its dNSNames.

    `san=None` and an empty SAN are different certificates and both are wanted:
    the reader has to say `[]` about a certificate carrying no names, and the
    two ways of carrying none must not be the only case it is ever shown.
    """
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

# The same key as PKCS#1 -- `-----BEGIN RSA PRIVATE KEY-----`, which is the
# common `openssl genrsa` export and the one `generate()` refuses by name. Same
# key deliberately: the only difference a test may see is the syntax.
SV_KEY_PKCS1 = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption()).decode()

# A certificate with no dNSName in its SAN and no Common Name -- subject `/O=`
# and nothing else. The case the two easy readings collapse: it parses, so it is
# *read*, and what it says is that it covers no host at all. `[]`, never None.
SV_CERT_NO_NAMES = _self_signed(
    [x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORG)], None)
