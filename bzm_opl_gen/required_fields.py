"""Which fields a bundle requires, which it carries a marker for, and where
each value comes from.
"""

from .bundle_options import ignored_options
from .markers import is_placeholder, marker
from .service_virt import SV_INGRESS_TYPES


# Text options a bundle is unusable without, and when each applies. Only
# requirements visible in the options are here: a registry, proxy or CA is
# configured by having a value, so the page marks those itself.
# placeholder_options() reads the markers, so both halves report alike.
REQUIRED_TEXT = {
    "namespace": lambda o: True,
    "service_account_name": lambda o: True,
    # Not for a chart: its README passes the token with --set-string at install
    # time, so an empty value is the recommended state.
    "auth_token": lambda o: o.get("output_format") != "helm",
    # Without a subdomain a virtual service stalls at WAITING_FOR_DOMAIN, and
    # crane will not start without the TLS secret name even for HTTP.
    "sv_subdomain": lambda o: o.get("sv_ingress") in SV_INGRESS_TYPES,
    "sv_tls_secret": lambda o: o.get("sv_ingress") in SV_INGRESS_TYPES,
    # The docker pair. A hostname alone works; a certificate needs a hostname
    # to serve and its key, and the key its certificate.
    "sv_hostname": lambda o: bool(o.get("sv_tls_cert") or o.get("sv_tls_key")),
    "sv_tls_cert": lambda o: bool(o.get("sv_tls_key")),
    "sv_tls_key": lambda o: bool(o.get("sv_tls_cert")),
}


def fill_placeholders(o):
    """Set every required-but-blank text option to its own marker, in place.
    Fields the format ignores are skipped.
    """
    ignored = ignored_options(o)
    for key, applies in REQUIRED_TEXT.items():
        if key in ignored or not applies(o):
            continue
        if not str(o.get(key) or "").strip():
            o[key] = marker(key)
    return o


def _reportable(o, key):
    """Whether a marker on `key` is this bundle's problem, rather than a field
    the format lacks or supplies elsewhere.
    """
    if key in ignored_options(o):
        return False
    applies = REQUIRED_TEXT.get(key)
    return applies is None or applies(o)


def placeholder_options(o):
    """The options this bundle carries a marker for, sorted; proxy and
    extra_env entries are reported dotted.

    Reads the values rather than REQUIRED_TEXT, because the page marks fields
    too. Options only: profile.json has no harbor_id (see placeholder_fields).
    """
    found = [k for k, v in o.items()
             if is_placeholder(v) and _reportable(o, k)]
    for sub, v in (o.get("proxy") or {}).items():
        if is_placeholder(v):
            found.append(f"proxy.{sub}")
    for name, v in (o.get("extra_env") or {}).items():
        if is_placeholder(v):
            found.append(f"extra_env.{name}")
    return sorted(found)


def placeholder_fields(facts, o):
    """Every field a rendered bundle carries a marker for: the options plus
    `harbor_id`, the one marked field that is a fact.
    """
    facts_found = ["harbor_id"] if is_placeholder(facts.get("harbor_id")) else []
    return sorted(placeholder_options(o) + facts_found)


# Where the value for each blank field comes from. Plain prose (no backticks,
# `--`, emphasis or `->`): it is a Markdown cell in the README and plain text
# on the download step (/api/placeholders).
PLACEHOLDER_SOURCE = {
    "auth_token": "the agent's own token: BlazeMeter → Settings → Private "
                  "Locations → this agent → Docker Command, or re-generate "
                  "with --auth-token",
    # The identity may not exist yet, so the answer is to create it.
    "harbor_id": "the private location: BlazeMeter → Settings → Private "
                 "Locations → this location. If it does not exist yet, create "
                 "it first; BlazeMeter issues the id",
    "ship_id": "the agent inside that location, from the same page. Create "
               "the agent if needed, and take its id and its AUTH_TOKEN "
               "together",
    "namespace": "the namespace the agent is deployed into",
    "service_account_name": "the account crane runs as. It has no default, "
                            "because the namespace's default account would "
                            "give every pod there crane's permissions",
    "sv_subdomain": "the DNS suffix virtual-service endpoints are published "
                    "under, e.g. apps.example.com — it must resolve to your "
                    "ingress",
    "sv_tls_secret": "a wildcard TLS secret in the agent's own namespace, not "
                     "default; required even for HTTP virtual services",
    "sv_hostname": "the hostname this agent advertises its virtual services "
                   "under — it has to resolve to this host, and to match the "
                   "certificate below",
    "sv_tls_cert": "the X509 certificate for that hostname, in PEM",
    "sv_tls_key": "its private key, in PEM with PKCS#8 syntax. Not stored "
                  "in profile.json, so a bundle regenerated from a profile "
                  "asks for it again",
    "private_registry": "the registry the BlazeMeter images were mirrored into",
    "ca_existing_configmap": "the ConfigMap your platform team keeps the trust "
                             "bundle in",
    "ca_bundle": "the PEM itself — your CA and the public roots together",
    "proxy.http": "the proxy URL, e.g. http://proxy:3128",
    "proxy.https": "the proxy URL, e.g. http://proxy:3128",
}

# Marked fields the API server itself refuses, measured with `kubectl apply
# --dry-run=server`: a marker in an object name, or in a label value (the
# identity). Every other marker lands in a value and applies cleanly.
PLACEHOLDER_REFUSED_BY_API = frozenset((
    "namespace", "service_account_name", "harbor_id", "ship_id"))
