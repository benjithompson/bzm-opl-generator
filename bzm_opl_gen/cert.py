"""Certificates read offline: the names one covers, and what a CA bundle holds.

The only importer of `cryptography`, the package's one runtime dependency: the
standard library cannot parse a certificate that did not arrive over a live
connection. Callers import this module lazily, so `plan` stays light.

`dns_names` serves the docker agent's virtual services: the certificate in
TLS_CERT must cover HOSTNAME_OVERRIDE. It has three answers: a list of names,
`[]` (read, names nothing), and None (the PEM did not load -- not read).

`lint` reads a CA trust bundle -- PEM, DER or PKCS#7, CRLF or not -- and says
what is wrong with each certificate: a server certificate where a CA belongs,
expiry, an intermediate without its issuer, duplicates, and blocks that do not
parse. It judges the file alone; `ca_check` judges it against a network.
"""

import collections
import datetime
import re

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import ExtensionOID, NameOID

from .verdict import FAIL, WARN

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


# -- CA bundles ----------------------------------------------------------------

# Below this many days to expiry a CA is a WARN: time to ask for the new one.
EXPIRY_WARN_DAYS = 30

# A lint finding worth knowing that changes nothing a client does.
NOTE = "NOTE"

# Any armoured block, so a key or a CRL in a trust bundle is named rather than
# skipped.
_ANY_BLOCK = re.compile(
    r"-----BEGIN ([A-Z0-9 #]+)-----(.*?)-----END \1-----", re.S)
_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 #]+-----")

# What a bundle file held: its certificates, one sentence per block that did not
# become a certificate, and the form it arrived in (pem, der, pkcs7 or None).
Parsed = collections.namedtuple("Parsed", "certs problems form")


def _text(data):
    """`data` as text with LF line ends and no byte-order mark."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    return data.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _is_pem(data):
    return (b"-----BEGIN" if isinstance(data, bytes) else "-----BEGIN") in data


def read_bundle(data):
    """Every certificate in `data` (bytes or text), and what did not parse.

    PEM may mix CERTIFICATE and PKCS7 blocks; a file with no armour is tried
    as one DER certificate, then as DER PKCS#7 (a Windows .cer or .p7b export).
    Nothing is dropped silently: each block that yields no certificate is a
    sentence in `problems`.
    """
    data = data or b""
    if _is_pem(data):
        return _read_pem(_text(data))
    raw = data if isinstance(data, bytes) else data.encode("utf-8", "replace")
    if not raw.strip():
        return Parsed([], [], None)
    try:
        return Parsed([x509.load_der_x509_certificate(raw)], [], "der")
    except Exception:      # noqa: BLE001
        pass
    try:
        return Parsed(list(pkcs7.load_der_pkcs7_certificates(raw)), [], "pkcs7")
    except Exception:      # noqa: BLE001
        pass
    return Parsed([], ["The file is not PEM, not a DER certificate and not a "
                       "DER PKCS#7 bundle, so nothing in it could be read."],
                  None)


def _read_pem(text):
    certs, problems = [], []
    blocks = list(_ANY_BLOCK.finditer(text))
    for n, block in enumerate(blocks, 1):
        label = block.group(1)
        pem = block.group(0).encode()
        try:
            if label == "CERTIFICATE":
                certs.append(x509.load_pem_x509_certificate(pem))
            elif label == "PKCS7":
                certs.extend(pkcs7.load_pem_pkcs7_certificates(pem))
            elif "PRIVATE KEY" in label:
                problems.append(
                    f"Block {n} is a private key, which a trust bundle never "
                    f"needs. Remove it before the bundle is shared or "
                    f"deployed.")
            else:
                problems.append(
                    f"Block {n} is a {label} block, not a certificate, so no "
                    f"client trusts anything because of it.")
        except Exception:      # noqa: BLE001
            problems.append(
                f"Block {n} ({label}) does not parse, so no client trusts it. "
                f"The file may be truncated or edited by hand.")
    unclosed = len(_BEGIN.findall(text)) - len(blocks)
    if unclosed > 0:
        problems.append(
            f"{unclosed} block(s) have a BEGIN line and no matching END line, "
            f"so they were not read. The file may be truncated.")
    return Parsed(certs, problems, "pem")


def _pem_of(certificate):
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def normalise(data):
    """`data` as the PEM text a bundle carries.

    PEM keeps its comments and order, with CRLF made LF and each PKCS7 block
    replaced by its certificates. DER and PKCS#7 files become PEM. Anything
    else comes back as text, for the lint to say what is wrong with it.
    """
    data = data or b""
    if not _is_pem(data):
        parsed = read_bundle(data)
        if parsed.certs:
            return "".join(_pem_of(c) for c in parsed.certs)
        return _text(data)

    def expand(block):
        if block.group(1) != "PKCS7":
            return block.group(0)
        try:
            certs = pkcs7.load_pem_pkcs7_certificates(block.group(0).encode())
        except Exception:      # noqa: BLE001
            return block.group(0)      # left in place; the lint names it
        return "".join(_pem_of(c) for c in certs).rstrip("\n")
    return _ANY_BLOCK.sub(expand, _text(data))


def verify_pem(data):
    """The certificates of `data` alone, as PEM an SSL context can load.

    Comments and unparseable blocks are left out: cadata must be ASCII, and a
    block OpenSSL cannot read refuses the whole load.
    """
    return "".join(_pem_of(c) for c in read_bundle(data).certs)


def _name(name):
    try:
        return name.rfc4514_string() or "(empty name)"
    except Exception:      # noqa: BLE001
        return "(unreadable name)"


def _is_ca(certificate):
    """True, False, or None where basicConstraints is present and unreadable.

    With no basicConstraints a certificate is a CA only when it is
    self-signed: an old X.509 v1 root has no extensions at all.
    """
    try:
        return certificate.extensions.get_extension_for_oid(
            ExtensionOID.BASIC_CONSTRAINTS).value.ca
    except x509.ExtensionNotFound:
        return certificate.subject == certificate.issuer
    except Exception:      # noqa: BLE001
        return None


def describe(certificate, now=None):
    """One certificate as data: names, role, validity and fingerprint."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    after = certificate.not_valid_after_utc
    self_signed = certificate.subject == certificate.issuer
    is_ca = _is_ca(certificate)
    return {"subject": _name(certificate.subject),
            "issuer": _name(certificate.issuer),
            "is_ca": is_ca, "self_signed": self_signed,
            "role": ("unknown" if is_ca is None else
                     "root" if is_ca and self_signed else
                     "intermediate" if is_ca else "leaf"),
            "not_before": certificate.not_valid_before_utc.isoformat(),
            "not_after": after.isoformat(),
            "days_left": (after - now).days,
            "sha256": certificate.fingerprint(hashes.SHA256()).hex(":").upper()}


def describe_der(der, now=None):
    """describe() for one DER certificate, or None where it does not parse."""
    try:
        return describe(x509.load_der_x509_certificate(der), now)
    except Exception:      # noqa: BLE001
        return None


def lint(data, now=None):
    """What is wrong with a CA trust bundle, judged from the file alone.

    Returns {form, certificates, findings}; a finding is {severity, cert,
    message}, `cert` being the 1-based certificate it is about, or None. No
    certificate at all is a FAIL. A file that could not be opened never
    reaches here: that is the caller's unread state.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    parsed = read_bundle(data)
    described = [describe(c, now) for c in parsed.certs]
    findings = [{"severity": WARN, "cert": None, "message": p}
                for p in parsed.problems]

    def say(severity, n, message):
        findings.append({"severity": severity, "cert": n, "message": message})

    if not parsed.certs:
        say(FAIL, None, "The CA bundle holds no certificate, so a client that "
                        "reads it trusts nothing and every TLS connection "
                        "fails.")
    subjects = {c.subject for c in parsed.certs}
    seen = {}
    for n, (c, d) in enumerate(zip(parsed.certs, described), 1):
        who = f"Certificate {n} ({d['subject']})"
        if d["sha256"] in seen:
            say(NOTE, n, f"{who} is a copy of certificate {seen[d['sha256']]}. "
                         f"The copy does no harm and can be removed.")
            continue
        seen[d["sha256"]] = n
        if d["is_ca"] is None:
            say(WARN, n, f"{who} has a basic constraints extension that does "
                         f"not parse, so whether it is a CA is unknown.")
        elif not d["is_ca"]:
            say(FAIL, n, f"{who} is not a CA. It is a server certificate, and "
                         f"a trust bundle needs the CA that signs server "
                         f"certificates. Ask for the root CA named in its "
                         f"issuer: {d['issuer']}.")
        if d["days_left"] < 0:
            say(FAIL, n, f"{who} expired on {d['not_after'][:10]}. Nothing it "
                         f"signed verifies; ask for its replacement.")
        elif d["days_left"] < EXPIRY_WARN_DAYS:
            say(WARN, n, f"{who} expires on {d['not_after'][:10]}, in "
                         f"{d['days_left']} days. Ask for its replacement now.")
        if c.not_valid_before_utc > now:
            say(WARN, n, f"{who} is not valid until {d['not_before'][:10]}, "
                         f"so nothing it signed verifies before then.")
        if d["is_ca"] and not d["self_signed"] and c.issuer not in subjects:
            say(WARN, n, f"{who} is an intermediate CA, and its issuer "
                         f"({d['issuer']}) is not in the bundle. A client "
                         f"that trusts only this bundle cannot complete the "
                         f"chain. That is fine only where the root is also in "
                         f"a system store the client reads. Crane reads this "
                         f"bundle in place of its own store, so add the root.")
    return {"form": parsed.form, "certificates": described,
            "findings": findings}


def missing_issuer(chain_der, data):
    """The subject of the CA a bundle lacks to verify `chain_der` (the DER
    certificates a server presented, leaf first), or None where the path ends
    at a self-signed root the bundle holds, or nothing readable was presented.

    The path climbs from the leaf through the presented certificates and the
    bundle's. Verification needs a root in the bundle, so a presented root the
    bundle lacks is named, and so is the first issuer found nowhere.
    """
    held = read_bundle(data).certs
    presented = []
    for der in chain_der:
        try:
            presented.append(x509.load_der_x509_certificate(der))
        except Exception:      # noqa: BLE001
            continue
    if not presented:
        return None
    # The bundle's copy first: it is the one a client trusts.
    by_subject = {}
    for c in held + presented:
        by_subject.setdefault(c.subject, c)
    current = presented[0]
    for _ in range(len(by_subject) + 1):
        if current.subject == current.issuer:
            return None if current in held else _name(current.subject)
        issuer = by_subject.get(current.issuer)
        if issuer is None:
            return _name(current.issuer)
        current = issuer
    return None     # a loop of certificates naming each other
