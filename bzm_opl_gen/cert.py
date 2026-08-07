"""What DNS names a certificate says it is for -- and when it will not say.

This exists for one question, asked at generate time: a docker agent publishes
its virtual services under `HOSTNAME_OVERRIDE`, serving them with the
`TLS_CERT`/`TLS_KEY` pair beside it, and BlazeMeter's own requirement is that
"the hostname of the request has to match one of the DNSName entries in the
Subject Alternative Name extension" or "the Common Name field of the owner
Subject". Get that wrong and the agent starts, reports online, publishes the
endpoint, and every client rejects the certificate -- a failure with nothing in
the agent's own log to lead you to it.

**`cryptography` is this package's one runtime dependency, and this is what it
is for.** The standard library has no public API for parsing a certificate that
did not arrive over a live connection -- `getpeercert()` needs a connection, an
`SSLContext` will load a chain and tell you nothing about it, and the one
function that decodes a file is `ssl._ssl._test_decode_cert`, which is private,
undocumented and takes a *path*, so calling it would have `generate()` write a
customer's certificate to a temporary file as a side effect of rendering a
string. The alternative to the dependency was a hand-rolled DER walk; the
alternative to both is no check, or a check that pretends. Nothing else here
imports it, and `plan.py` -- which is asserted to reach nothing -- must go on
not reaching this module.

**What this refuses to do is guess.** Three answers, and they are three values
rather than two, because that is this repo's standing rule and this is exactly
the case it is about:

  `[...]`  read: these are the DNS names the certificate carries.
  `[]`     read, and it carries none -- no dNSName in the SAN, no CN. A real
           answer about a real certificate, and a hostname cannot match it.
  `None`   **not read.** The PEM did not decode. Nothing may be concluded from
           it, and the caller must say so rather than passing a bundle off as
           checked.

A certificate with no SAN extension at all is the case the two easy readings
collapse: `ExtensionNotFound` is not a parse failure, it is the certificate
answering, so it falls through to the Common Name and can legitimately end at
`[]`. Only a PEM that will not load is unread.

This is emphatically **not** a certificate validator. Expiry, chains and trust
are all reachable from here now and none of them is asked: an expired
certificate, a self-signed one and one signed by a CA nobody has all read the
same, because none of those is the question, and a module that answered some of
them would be read as answering all of them.
"""

import re

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

_PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def is_certificate_pem(text):
    """Does this look like a PEM certificate at all?

    Separate from reading the names, because the two are different failures with
    different fixes: this one is "that is not a certificate" (a key pasted into
    the certificate box, a DER file, a chain in the wrong format), and the
    caller refuses it outright. Not reading the *names* out of something that is
    plainly a certificate is the honest gap this module's docstring is about.
    """
    return bool(_PEM_BLOCK.search(text or ""))


def dns_names(pem):
    """The DNS names `pem` says it is for, or None where they could not be read.

    Order: the SAN's dNSName entries, then the Common Name. Both, rather than
    the SAN alone -- RFC 6125 says a CN is to be ignored once a SAN is present,
    and BlazeMeter's own requirement is worded as an either/or ("one of the
    DNSName entries ... or the Common Name field"). Where the two rules differ
    this takes the wider set on purpose: the narrow reading would have this
    *refuse* a bundle whose certificate BlazeMeter's own documentation says
    covers the hostname, and a false refusal is the expensive mistake here.

    Only dNSName entries are taken from the SAN. The other members -- IP
    addresses, URIs -- are real names for other questions, and counting one as
    something a hostname could match is the near-miss that makes a check worse
    than none.
    """
    block = _PEM_BLOCK.search(pem or "")
    if not block:
        return None
    try:
        certificate = x509.load_pem_x509_certificate(block.group(0).encode())
    except Exception:      # noqa: BLE001
        # Every way a PEM fails to load is the same answer -- not read -- and
        # the library's exception types are not a vocabulary this cares about.
        # It must not become `[]`: that would be "this certificate covers
        # nothing", which is a refusal, over a certificate nobody looked at.
        return None
    names = []
    try:
        san = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    except x509.ExtensionNotFound:
        # NOT a read failure. A certificate may simply have no SAN -- the
        # BlazeMeter example value, `C123ABCXYZ`, is exactly the shape of thing
        # somebody issues with a Common Name and nothing else -- so the answer
        # comes from the subject below, and `[]` at the end of it is honest.
        pass
    except Exception:      # noqa: BLE001
        # A SAN that is there and unparseable is the unread case again: the
        # certificate does carry names and this could not read them.
        return None
    else:
        names += [n for n in san.get_values_for_type(x509.DNSName)
                  if isinstance(n, str)]
    for attribute in certificate.subject.get_attributes_for_oid(
            NameOID.COMMON_NAME):
        # `value` is bytes for the encodings that have no text reading, and a
        # name nothing can compare against is one this does not claim to have.
        if isinstance(attribute.value, str) and attribute.value not in names:
            names.append(attribute.value)
    return names


def matches(hostname, names):
    """Does `hostname` match any of `names`, wildcards included?

    Case-insensitive, and a leading `*.` matches exactly one label -- the rule
    every TLS client applies, so `*.example.com` covers `mocks.example.com` and
    neither `example.com` nor `a.b.example.com`. A wildcard anywhere else in the
    name is treated as the literal it is: nothing accepts `w*.example.com`, and
    honouring it here would pass a bundle every client then rejects.
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
