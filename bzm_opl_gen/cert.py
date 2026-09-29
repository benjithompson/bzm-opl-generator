"""What DNS names a certificate says it is for -- and when it will not say.

Used at generate time for a docker agent's virtual services: the certificate in
TLS_CERT must cover HOSTNAME_OVERRIDE (a SAN dNSName or the Common Name), or
the agent comes online and every client rejects the endpoint.

The only importer of `cryptography`, the package's one runtime dependency: the
standard library cannot parse a certificate that did not arrive over a live
connection. `dns_names` has three answers: a list of names, `[]` (read, names
nothing), and None (the PEM did not load -- not read, so nothing may be
concluded). This is not a validator: expiry, chain and trust are not checked.
"""

import re

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

_PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def is_certificate_pem(text):
    """Does this look like a PEM certificate at all? (A different failure from
    not being able to read its names.)"""
    return bool(_PEM_BLOCK.search(text or ""))


def dns_names(pem):
    """The DNS names `pem` says it is for, or None where they could not be read.

    The SAN's dNSName entries, then the Common Name. Both, because BlazeMeter
    documents either as sufficient and a false refusal is the costly mistake.
    Other SAN types (IPs, URIs) are not names a hostname can match.
    """
    block = _PEM_BLOCK.search(pem or "")
    if not block:
        return None
    try:
        certificate = x509.load_pem_x509_certificate(block.group(0).encode())
    except Exception:      # noqa: BLE001
        # Any load failure means not read; never `[]`, which would be a refusal.
        return None
    names = []
    try:
        san = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    except x509.ExtensionNotFound:
        # No SAN is the certificate answering; the Common Name decides.
        pass
    except Exception:      # noqa: BLE001
        # A SAN that is present but unparseable: not read.
        return None
    else:
        names += [n for n in san.get_values_for_type(x509.DNSName)
                  if isinstance(n, str)]
    for attribute in certificate.subject.get_attributes_for_oid(
            NameOID.COMMON_NAME):
        # A bytes value has no text reading to compare against.
        if isinstance(attribute.value, str) and attribute.value not in names:
            names.append(attribute.value)
    return names


def matches(hostname, names):
    """Does `hostname` match any of `names`, case-insensitively?

    A leading `*.` matches exactly one label, as TLS clients do; a wildcard
    anywhere else is a literal.
    """
    host = (hostname or "").strip().rstrip(".").lower()
    if not host:
        return False
    for name in names:
        want = (name or "").strip().rstrip(".").lower()
        if not want:
            continue
        if want == host:
            return True
        if want.startswith("*.") and host.count(".") == want.count("."):
            if host.split(".", 1)[1:] == want.split(".", 1)[1:]:
                return True
    return False
