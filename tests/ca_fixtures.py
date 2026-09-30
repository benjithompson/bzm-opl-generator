"""A corporate CA chain, the mistakes customers make with it, and a local TLS
server and CONNECT proxy to check a bundle against.

Built at import, so no private key is committed. The chain is a root, an
intermediate and a leaf for 127.0.0.1 and localhost; the server presents leaf
and intermediate, as a TLS-inspecting proxy does. Nothing leaves this machine.
"""

import base64
import contextlib
import datetime
import ipaddress
import os
import socket
import ssl
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

NOW = datetime.datetime.now(datetime.timezone.utc)
DAY = datetime.timedelta(days=1)


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                                         "bzm-opl-gen test fixture"),
                      x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(cn, key, issuer=None, issuer_key=None, ca=True, start=None,
          end=None, san=None):
    issuer_name = issuer.subject if issuer is not None else _name(cn)
    builder = (x509.CertificateBuilder()
               .subject_name(_name(cn))
               .issuer_name(issuer_name)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(start or NOW - DAY)
               .not_valid_after(end or NOW + 3650 * DAY)
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None),
                              critical=True))
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=False,
                          key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=True,
                          crl_sign=True, encipher_only=False,
                          decipher_only=False), critical=True)
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName(san),
                                        critical=False)
    return builder.sign(issuer_key or key, hashes.SHA256())


def pem(*certs):
    return "".join(c.public_bytes(serialization.Encoding.PEM).decode()
                   for c in certs)


ROOT_KEY, INTERMEDIATE_KEY, LEAF_KEY = _key(), _key(), _key()
ROOT = _cert("Corp Root CA", ROOT_KEY)
INTERMEDIATE = _cert("Corp Issuing CA", INTERMEDIATE_KEY, ROOT, ROOT_KEY)
LEAF = _cert("127.0.0.1", LEAF_KEY, INTERMEDIATE, INTERMEDIATE_KEY, ca=False,
             san=[x509.DNSName("localhost"),
                  x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])

# A root nobody on this network uses: a bundle of it alone verifies nothing.
OTHER_ROOT = _cert("Some Other Root", _key())
EXPIRED_ROOT = _cert("Expired Root", _key(), start=NOW - 800 * DAY,
                     end=NOW - 10 * DAY)
EXPIRING_ROOT = _cert("Expiring Root", _key(), end=NOW + 10 * DAY)

ROOT_PEM = pem(ROOT)
CHAIN_PEM = pem(INTERMEDIATE, ROOT)
LEAF_PEM = pem(LEAF)
INTERMEDIATE_PEM = pem(INTERMEDIATE)

# How Windows hands a certificate over: DER, and a PKCS#7 bundle.
ROOT_DER = ROOT.public_bytes(serialization.Encoding.DER)
CHAIN_P7B_DER = pkcs7.serialize_certificates([INTERMEDIATE, ROOT],
                                             serialization.Encoding.DER)
CHAIN_P7B_PEM = pkcs7.serialize_certificates([INTERMEDIATE, ROOT],
                                             serialization.Encoding.PEM)
CHAIN_PEM_CRLF = CHAIN_PEM.replace("\n", "\r\n").encode()


def _key_pem(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


@contextlib.contextmanager
def tls_server(presented=(LEAF, INTERMEDIATE), key=LEAF_KEY):
    """A TLS server on 127.0.0.1 presenting `presented`; yields its port.

    It completes each handshake and closes. A handshake the client aborts is
    not an error here.
    """
    with tempfile.TemporaryDirectory() as d:
        chain = os.path.join(d, "chain.pem")
        keyfile = os.path.join(d, "key.pem")
        with open(chain, "w") as fh:
            fh.write(pem(*presented))
        with open(keyfile, "w") as fh:
            fh.write(_key_pem(key))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(chain, keyfile)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            try:
                conn.settimeout(5)
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except (OSError, ssl.SSLError):
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(2)
        listener.close()


@contextlib.contextmanager
def connect_proxy(credentials=None):
    """An HTTP CONNECT proxy on 127.0.0.1; yields (port, seen).

    `seen` collects each CONNECT request's header block. With `credentials`
    ("user:pass") a request without that Basic auth is answered 407.
    """
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    seen = []
    stop = threading.Event()
    want = ("Basic " + base64.b64encode(credentials.encode()).decode()
            if credentials else None)

    def pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle(conn):
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            head += chunk
        text = head.decode("latin-1")
        seen.append(text)
        if want and f"Proxy-Authorization: {want}" not in text:
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
            conn.close()
            return
        target = text.split(" ", 2)[1]
        host, _, tport = target.rpartition(":")
        try:
            upstream = socket.create_connection((host, int(tport)), timeout=5)
        except OSError:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            conn.close()
            return
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        threading.Thread(target=pipe, args=(conn, upstream), daemon=True).start()
        pipe(upstream, conn)

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, seen
    finally:
        stop.set()
        thread.join(2)
        listener.close()


def closed_port():
    """A local port nothing listens on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
