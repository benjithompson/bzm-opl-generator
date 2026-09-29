"""Does a CA bundle verify the TLS chain this network actually presents?

A TLS-inspecting proxy re-signs every connection with the customer's own CA, so
whether a bundle is right is a property of their network, not of the file. For
each host this opens TLS (through an HTTP CONNECT proxy where one is named),
keeps the chain the far end presented, and verifies against the bundle alone.

Verification is OpenSSL's default, as curl does it: the chain must reach a
self-signed root in the bundle. Python 3.13's create_default_context would also
accept an intermediate alone, which crane's clients do not.
"""

import base64
import collections
import os
import socket
import ssl
import urllib.parse

# Three answers that must not blur: the bundle verified the chain, it did not,
# or no chain was seen at all.
VERIFIED = "verified"
NOT_VERIFIED = "not_verified"
UNREACHABLE = "unreachable"

DEFAULT_PORT = 443
TIMEOUT_S = 10

# A proxy, parsed. `auth` is the Proxy-Authorization value or None; `shown` is
# the URL with any credentials removed, the only form ever printed.
Proxy = collections.namedtuple("Proxy", "host port auth shown")


class ProxyRefused(OSError):
    """The proxy answered CONNECT with something other than 200."""


def parse_proxy(url):
    """A Proxy from `http://[user:pass@]host[:port]`; ValueError otherwise.

    Only a plain-HTTP proxy is supported: CONNECT over TLS to the proxy would
    need a second trust decision this check does not make.
    """
    if "://" not in url:
        url = "http://" + url
    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or not u.hostname:
        raise ValueError(
            f"the proxy must be an http:// URL with a host, not "
            f"{redact(url)!r}")
    auth = None
    if u.username is not None:
        pair = (f"{urllib.parse.unquote(u.username)}:"
                f"{urllib.parse.unquote(u.password or '')}")
        auth = "Basic " + base64.b64encode(pair.encode()).decode()
    port = u.port or 3128
    return Proxy(u.hostname, port, auth, f"http://{u.hostname}:{port}"
                 + (" (with credentials)" if auth else ""))


def redact(url):
    """`url` with any user:password removed, for an error message."""
    u = urllib.parse.urlsplit(url)
    if "@" not in u.netloc:
        return url
    return urllib.parse.urlunsplit(
        u._replace(netloc="<credentials>@" + u.netloc.rsplit("@", 1)[1]))


def proxy_for(host, explicit=None, env=None):
    """The Proxy to reach `host` through, or None for a direct connection.

    An explicit URL wins. Otherwise HTTPS_PROXY from `env`, unless NO_PROXY
    names the host, as curl reads them.
    """
    if explicit:
        return parse_proxy(explicit)
    env = os.environ if env is None else env
    url = env.get("HTTPS_PROXY") or env.get("https_proxy")
    if not url:
        return None
    no_proxy = env.get("NO_PROXY") or env.get("no_proxy") or ""
    if no_proxy and _bypassed(host, no_proxy):
        return None
    return parse_proxy(url)


def _bypassed(host, no_proxy):
    names = [n.strip().lstrip(".").lower() for n in no_proxy.split(",")]
    host = host.lower()
    return any(n == "*" or host == n or host.endswith("." + n)
               for n in names if n)


def split_host(target):
    """(host, port) from `host`, `host:port` or `[v6]:port`."""
    host, sep, port = target.rpartition(":")
    bare_v6 = target.count(":") > 1 and not target.startswith("[")
    if sep and port.isdigit() and not bare_v6:
        return host.strip("[]"), int(port)
    return target.strip("[]"), DEFAULT_PORT


def _connect(host, port, proxy, timeout):
    """A TCP socket to host:port, tunnelled through `proxy` when given."""
    if proxy is None:
        return socket.create_connection((host, port), timeout=timeout)
    sock = socket.create_connection((proxy.host, proxy.port), timeout=timeout)
    try:
        request = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
                   f"Host: {host}:{port}\r\n")
        if proxy.auth:
            request += f"Proxy-Authorization: {proxy.auth}\r\n"
        sock.sendall((request + "\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(4096)
            if not chunk or len(reply) > 65536:
                raise ProxyRefused(
                    f"the proxy at {proxy.shown} closed the connection "
                    f"before answering CONNECT")
            reply += chunk
        status = reply.split(b"\r\n", 1)[0].decode("latin-1").strip()
        if status.split(" ")[1:2] != ["200"]:
            raise ProxyRefused(f"the proxy at {proxy.shown} answered CONNECT "
                               f"with {status!r}")
        return sock
    except BaseException:
        sock.close()
        raise


def presented_chain(tls):
    """The DER certificates the far end sent, leaf first.

    Public from Python 3.13; 3.10 to 3.12 carry it on the private _sslobj.
    Where neither exists, the leaf alone.
    """
    public = getattr(tls, "get_unverified_chain", None)
    if public is not None:
        return list(public() or [])
    private = getattr(getattr(tls, "_sslobj", None),
                      "get_unverified_chain", None)
    if private is not None:
        return [c.public_bytes(ssl._ssl.ENCODING_DER) for c in private() or []]
    leaf = tls.getpeercert(binary_form=True)
    return [leaf] if leaf else []


def _capture_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def verify_context(pem):
    """A context that trusts `pem` alone, and verifies as OpenSSL does by
    default: no partial chains, no strict mode."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_flags &= ~(ssl.VERIFY_X509_PARTIAL_CHAIN
                          | ssl.VERIFY_X509_STRICT)
    ctx.load_verify_locations(cadata=pem)
    return ctx


def _handshake(host, port, proxy, ctx, timeout):
    sock = _connect(host, port, proxy, timeout)
    try:
        return ctx.wrap_socket(sock, server_hostname=host)
    except BaseException:
        sock.close()
        raise


def check_host(target, pem, proxy=None, timeout=TIMEOUT_S):
    """Open TLS to `target` and judge its chain against `pem`.

    Returns {host, port, status, chain, detail}: `chain` is the DER list the
    far end presented. With `pem` empty nothing can verify, so the answer is
    NOT_VERIFIED once a chain was seen.
    """
    host, port = split_host(target)
    out = {"host": host, "port": port, "chain": [], "detail": ""}
    try:
        with _handshake(host, port, proxy, _capture_context(), timeout) as tls:
            out["chain"] = presented_chain(tls)
    except (OSError, ValueError) as e:
        return {**out, "status": UNREACHABLE, "detail": _reason(e)}
    if not pem:
        return {**out, "status": NOT_VERIFIED,
                "detail": "the bundle holds no certificate to verify with"}
    try:
        with _handshake(host, port, proxy, verify_context(pem), timeout):
            pass
    except ssl.SSLCertVerificationError as e:
        return {**out, "status": NOT_VERIFIED,
                "detail": e.verify_message or _reason(e)}
    except (OSError, ValueError) as e:
        # The chain was seen a moment ago; a second connection that fails is
        # still no verdict.
        return {**out, "status": UNREACHABLE,
                "detail": f"the verifying connection failed: {_reason(e)}"}
    return {**out, "status": VERIFIED, "detail": ""}


def _reason(e):
    if isinstance(e, socket.gaierror):
        return f"the name does not resolve ({e.strerror or e})"
    if isinstance(e, TimeoutError):
        return "the connection timed out"
    if isinstance(e, ConnectionRefusedError):
        return "the connection was refused"
    return str(e) or type(e).__name__
