"""Service virtualization: how an agent publishes virtual services.

On Kubernetes crane creates one ingress object per virtual service through a
backend (KUBERNETES_WEB_EXPOSE_*); on a docker host it serves the endpoint
itself under HOSTNAME_OVERRIDE, with a TLS_CERT/TLS_KEY pair. Each option set
is the other format's ignored options. sv_expose renders a Service and Ingress
of our own for a deployed virtual service.
"""

import collections

from .bundle_options import ignored_options, is_openshift
from .markers import is_placeholder


# The funcId of a location that serves virtual services, which makes the
# ingress options mandatory. A tuple because it is served as a set.
SV_FUNC_IDS = ("mockServices",)


class SvBackend(collections.namedtuple(
        "SvBackend",
        "group resources creates via_ingress_class nodeport_ok tls_secret_read")):
    """A crane web-expose backend, and the one API group the Role grants it.

    `via_ingress_class`: whether an IngressClass claims the object (doctor
    preflights it; Istio, Contour and the OpenShift router register none).
    `nodeport_ok`: whether it survives service_type=NODEPORT. nginx and
    openshift write a constant port 8080, which stays valid; contour and istio
    take the Service's nodePort, which no client reaches the ingress on
    (measured on all four). `tls_secret_read`: whether the object reads
    sv_tls_secret at all. Crane requires the name regardless, and where it is
    read the secret must be in the agent's namespace.
    """
    __slots__ = ()


SV_INGRESS_BACKENDS = {
    "nginx": SvBackend("networking.k8s.io", ["ingresses"], "Ingress", True,
                       nodeport_ok=True, tls_secret_read=True),
    # PASSTHROUGH on :443 with no credentialName: an HTTPS virtual service
    # terminates TLS in the mock pod.
    "istio": SvBackend("networking.istio.io", ["gateways", "virtualservices"],
                       "Gateway + VirtualService", False, nodeport_ok=False,
                       tls_secret_read=False),
    "contour": SvBackend("projectcontour.io", ["httpproxies"],
                         "HTTPProxy", False, nodeport_ok=False,
                         tls_secret_read=True),
    # routes/custom-host: OpenShift gates spec.host behind it and crane sets
    # spec.host; without it the Route create fails with 422 and the virtual
    # service stalls. `auth can-i` answers yes either way. Routes are
    # edge/Allow, terminated with the router's default certificate.
    "openshift": SvBackend("route.openshift.io",
                           ["routes", "routes/custom-host"], "Route", False,
                           nodeport_ok=True, tls_secret_read=False),
}

# Crane's four real web-expose implementations. BlazeMeter's documented
# `INGRESS` value is absent on purpose: it creates nothing and stalls at
# WAITING_FOR_DOMAIN.
SV_INGRESS_TYPES = tuple(SV_INGRESS_BACKENDS)

# sv_ingress answered with no ingress: a location carrying mockServices,
# generated for performance testing alone. Unset means unanswered and is
# refused for such a location. Nothing else changes; virtual services deployed
# there stall at WAITING_FOR_DOMAIN.
SV_INGRESS_NONE = "none"
assert SV_INGRESS_NONE not in SV_INGRESS_BACKENDS  # a backend may not claim it


def sv_cfg(facts, o):
    """Resolve the Kubernetes service-virtualization options, or None.

    Each refusal is a combination that fails silently on a cluster (the agent
    reports idle, the mock runs, the endpoint never serves), so each message
    names the fix. None where the format ignores sv_ingress.
    """
    if "sv_ingress" in ignored_options(o):
        return None
    ingress = o["sv_ingress"]
    sv_funcs = [f for f in (facts.get("func_ids") or []) if f in SV_FUNC_IDS]
    if ingress == SV_INGRESS_NONE:
        return None
    if not ingress:
        if sv_funcs:
            raise ValueError(
                f"location advertises funcId(s) {', '.join(sv_funcs)} but no "
                "service-virtualization ingress was configured. Pass sv_ingress "
                f"({'|'.join(SV_INGRESS_TYPES)}) + sv_subdomain + sv_tls_secret, "
                "or virtual services will deploy and stall at WAITING_FOR_DOMAIN. "
                f"To generate this location for performance testing alone, pass "
                f"sv_ingress={SV_INGRESS_NONE}: the bundle is then the same as a "
                "non-SV location's, and virtual services deployed to it stall."
            )
        return None
    if ingress not in SV_INGRESS_TYPES:
        raise ValueError(f"sv_ingress must be one of {SV_INGRESS_TYPES}, got {ingress!r}")
    missing = [n for n, v in (("sv_subdomain", o["sv_subdomain"]),
                              ("sv_tls_secret", o["sv_tls_secret"])) if not v]
    if missing:
        raise ValueError(
            f"sv_ingress={ingress} also requires {' and '.join(missing)}. "
            "The TLS secret is mandatory even for HTTP virtual services -- crane "
            "refuses to start without it."
        )
    # The port crane writes into the object, per backend (see
    # SvBackend.nodeport_ok).
    if o["service_type"] != "CLUSTERIP" and not SV_INGRESS_BACKENDS[ingress].nodeport_ok:
        raise ValueError(
            f"sv_ingress={ingress} requires service_type=CLUSTERIP, got "
            f"{o['service_type']}. Crane fills this backend's port field from "
            f"the Service's nodePort, which nothing reaches the ingress on: the "
            f"{SV_INGRESS_BACKENDS[ingress].creates} is written, the mock runs "
            "1/1, BlazeMeter advertises the endpoint, and the endpoint does not "
            "serve. Measured live -- contour reports `unresolved service "
            "reference` and answers 503; istio's gateway ends up listening on "
            "the nodePort alone and nothing answers at all. Fix: use "
            "service_type=CLUSTERIP, which is the default and changes nothing "
            f"else about a {ingress} deployment. (sv_ingress=nginx and "
            "openshift do work on NODEPORT -- they write a constant port -- but "
            "switching backend to get there means switching ingress controller, "
            "which is the bigger change of the two.)")
    if ingress == "openshift" and not is_openshift(o):
        raise ValueError(
            f"sv_ingress=openshift requires an OpenShift cluster, got "
            f"platform={o['platform']} openshift_cluster="
            f"{bool(o.get('openshift_cluster', False))}. That backend publishes a "
            "route.openshift.io Route, which a plain Kubernetes API server does "
            "not serve -- the agent would deploy cleanly and then stall with "
            "nothing to create."
        )
    if o["sv_istio_gateway"] and ingress != "istio":
        raise ValueError(
            f"sv_istio_gateway is only meaningful with sv_ingress=istio, not "
            f"{ingress}. Crane reads KUBERNETES_ISTIO_GATEWAY_NAME in the istio "
            "backend alone, so setting it here would silently do nothing."
        )
    return {"type": ingress, "subdomain": o["sv_subdomain"],
            "tls_secret": o["sv_tls_secret"],
            "istio_gateway": o["sv_istio_gateway"]}


# BlazeMeter requires a PEM private key in PKCS#8 syntax. PKCS#1 (`openssl
# genrsa` on many builds) and SEC1 (`openssl ecparam -genkey`) are common
# exports; crane starts with either and fails at the first TLS handshake, so
# each is refused with its conversion.
PKCS8_HEADER = "-----BEGIN PRIVATE KEY-----"

_KEY_HEADERS = {
    "-----BEGIN RSA PRIVATE KEY-----":
        "a PKCS#1 RSA key. Convert it: openssl pkcs8 -topk8 -nocrypt "
        "-in key.pem -out key.pk8.pem",
    "-----BEGIN EC PRIVATE KEY-----":
        "a SEC1 EC key. Convert it: openssl pkcs8 -topk8 -nocrypt "
        "-in key.pem -out key.pk8.pem",
    # Nothing in the bundle or BlazeMeter's environment reference supplies a
    # passphrase.
    "-----BEGIN ENCRYPTED PRIVATE KEY-----":
        "an encrypted key, and nothing here can give the agent a passphrase. "
        "Decrypt it: openssl pkcs8 -topk8 -nocrypt -in key.pem -out key.pk8.pem",
}


def _sv_tls_key(o):
    """Refuse a private key the agent cannot read, by name. A blank key is the
    marker's to report.
    """
    key = o["sv_tls_key"]
    if not key or is_placeholder(key):
        return
    if PKCS8_HEADER in key:
        return
    for header, what in _KEY_HEADERS.items():
        if header in key:
            raise ValueError(
                f"sv_tls_key is {what}. BlazeMeter requires a private key in "
                f"PEM format with PKCS#8 syntax ({PKCS8_HEADER}).")
    raise ValueError(
        f"sv_tls_key does not look like a PEM private key -- it must carry "
        f"{PKCS8_HEADER}. BlazeMeter requires PKCS#8 syntax; convert a key of "
        f"any other shape with openssl pkcs8 -topk8 -nocrypt.")


def sv_docker_cfg(o):
    """Resolve how a docker agent publishes virtual services, or None.

    The hostname alone works (endpoints get a name instead of an IP); the TLS
    pair makes them HTTPS. `names` is what the certificate covers, and None
    there means not read, never covers nothing (see cert.dns_names).
    """
    # cert imports cryptography, a compiled extension; only a bundle with a
    # certificate to check loads it.
    from . import cert

    if "sv_hostname" in ignored_options(o):
        return None
    hostname = str(o["sv_hostname"] or "").strip()
    cert_pem = o["sv_tls_cert"] or ""
    key_pem = o["sv_tls_key"] or ""
    if not (hostname or cert_pem or key_pem):
        return None
    _sv_tls_key(o)
    names = None
    if cert_pem and not is_placeholder(cert_pem):
        if not cert.is_certificate_pem(cert_pem):
            raise ValueError(
                "sv_tls_cert does not look like a PEM certificate -- it must "
                "carry -----BEGIN CERTIFICATE-----. BlazeMeter requires an "
                "X509 compatible public certificate in PEM format; a private "
                "key or a DER file is refused here rather than mounted into an "
                "agent that then serves nothing.")
        names = cert.dns_names(cert_pem)
        # A mismatch is silent from the agent's end: it reports online and
        # every client rejects the endpoint. An unread certificate refuses
        # nothing; the README says it was not checked.
        if names is not None and hostname and not is_placeholder(hostname):
            if not cert.matches(hostname, names):
                raise ValueError(
                    f"sv_hostname={hostname!r} is not covered by sv_tls_cert. "
                    f"The certificate carries "
                    + (f"{', '.join(names)}" if names
                       else "no DNS name at all -- no dNSName in its Subject "
                            "Alternative Name extension and no Common Name")
                    + ". BlazeMeter requires the hostname to match a DNSName "
                      "entry in the SAN extension or the Common Name field, so "
                      "every client would reject the endpoint this agent "
                      "publishes. Re-issue the certificate for that hostname, "
                      "or set sv_hostname to a name it covers (a wildcard "
                      "covers one label).")
    return {"hostname": hostname, "cert": cert_pem, "key": key_pem,
            "names": names}


# Labels crane stamps on a mock pod. Unlike its Services' hashed names they
# survive redeploys, so a Service of our own can select on them.
SV_POD_NAME_LABEL = "BZM_CONTAINER_NAME"
SV_POD_HARBOR_LABEL = "BZM_HARBOR_ID"
SV_POD_SHIP_LABEL = "BZM_SHIP_ID"

# The class on the Ingress sv_expose emits when none is named. Not crane's own
# hardcoded class (doctor.CRANE_INGRESS_CLASS); on OpenShift pass
# openshift-default.
SV_EXPOSE_DEFAULT_INGRESS_CLASS = "nginx"


class SvPublish(collections.namedtuple(
        "SvPublish", "subdomain tls_secret ingress_class")):
    """Where `sv-expose` publishes: the wildcard host, an optional TLS secret,
    and the IngressClass that should claim the Ingress.
    """
    __slots__ = ()


def sv_publish_cfg(o):
    """Resolve a profile into what `sv_expose` needs.

    Not sv_cfg, which checks what crane needs and so requires the TLS secret
    name. This Ingress is ours, and TLS on it is optional.
    """
    subdomain = o.get("sv_subdomain")
    if not subdomain:
        raise ValueError(
            "sv-expose needs sv_subdomain -- the endpoint host is "
            "<mock>-<port>-<namespace>.<subdomain>. Generate the manifests with "
            "--sv-subdomain so the profile carries it, or pass --sv-subdomain "
            "here.")
    return SvPublish(subdomain, o.get("sv_tls_secret"),
                     o.get("sv_ingress_class") or SV_EXPOSE_DEFAULT_INGRESS_CLASS)


def sv_endpoint_host(name, port, namespace, subdomain):
    """The host BlazeMeter advertises for a deployed virtual service, or None
    without a subdomain. sv_expose and the UI both use this exact string.
    """
    return f"{name}-{port}-{namespace}.{subdomain}" if subdomain else None


def sv_expose(mocks, namespace, publish):
    """Render a Service + Ingress per deployed virtual service.

    Under CLUSTERIP crane's own Ingress points at port 8080 while its Service
    exposes 80, so the advertised endpoint answers 503. This pair sets port ==
    targetPort and selects on the pod's identity labels, leaving crane's
    objects alone. `mocks` are {name, port, harbor, ship}, read off the running
    pods (sv_read.sv_mocks).
    """
    ns, docs = namespace, []
    for m in mocks:
        name, port = m["name"], m["port"]
        obj = f"bzm-sv-{name}"
        host = sv_endpoint_host(name, port, ns, publish.subdomain)
        tls = ""
        if publish.tls_secret:
            tls = ("  tls:\n"
                   f"    - hosts: [{host}]\n"
                   f"      secretName: {publish.tls_secret}\n")
        docs.append(
            "apiVersion: v1\n"
            "kind: Service\n"
            f"metadata: {{ name: {obj}, namespace: {ns} }}\n"
            "spec:\n"
            "  selector:\n"
            f"    {SV_POD_NAME_LABEL}: {name}\n"
            f"    {SV_POD_HARBOR_LABEL}: \"{m['harbor']}\"\n"
            f"    {SV_POD_SHIP_LABEL}: \"{m['ship']}\"\n"
            "  ports:\n"
            f"    - {{ name: http, port: {port}, targetPort: {port}, protocol: TCP }}\n"
            "---\n"
            "apiVersion: networking.k8s.io/v1\n"
            "kind: Ingress\n"
            f"metadata: {{ name: {obj}, namespace: {ns} }}\n"
            "spec:\n"
            f"  ingressClassName: {publish.ingress_class}\n"
            f"{tls}"
            "  rules:\n"
            f"    - host: {host}\n"
            "      http:\n"
            "        paths:\n"
            "          - path: /\n"
            "            pathType: Prefix\n"
            "            backend:\n"
            f"              service: {{ name: {obj}, port: {{ number: {port} }} }}\n")
    return "---\n".join(docs)
