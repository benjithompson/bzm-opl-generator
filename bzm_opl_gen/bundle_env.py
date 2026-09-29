"""The agent environment a bundle writes: the proxy variables, the reserved
set, and free-form extra_env.

extra_env reaches the agent: crane's pod reads it (envFrom, or docker --env),
while the engines crane spawns get their environment from crane.
"""

import re
from urllib.parse import quote

from .ca_trust import CA_MODES


def proxy_url(url, p):
    """Embed credentials in the proxy URL (http://user:pass@host:port);
    BlazeMeter has no separate proxy-auth variables.
    """
    user = p.get("username")
    if not url or not user:
        return url
    userinfo = quote(user, safe="")
    if p.get("password"):
        userinfo += ":" + quote(p["password"], safe="")
    scheme, sep, rest = url.partition("://")
    return f"{scheme}{sep}{userinfo}@{rest}" if sep else f"{userinfo}@{url}"


DEFAULT_NO_PROXY = "kubernetes.default,127.0.0.1,localhost"


def proxy_env(o, no_proxy=DEFAULT_NO_PROXY):
    """The proxy environment as {NAME: value}, credentials embedded.

    One builder for the ConfigMap, the Secret, doctor's probe pod and the
    docker command. `no_proxy` is the fallback; docker passes its own, since
    `kubernetes.default` resolves nowhere on a host.
    """
    p = o.get("proxy") or {}
    env = {}
    for name, key in (("HTTP_PROXY", "http"), ("HTTPS_PROXY", "https")):
        if p.get(key):
            env[name] = proxy_url(p[key], p)
    if p:
        env["NO_PROXY"] = p.get("no_proxy", no_proxy)
    return env


def proxy_has_creds(o):
    """Whether the proxy URLs carry credentials, which makes them secret."""
    return bool(o["proxy"] and o["proxy"].get("username"))


# Every variable any format writes for itself, with the option that owns it
# (None: identity or fixed posture). extra_env refuses all of them in every
# format: two values for one ConfigMap key is a duplicate entry, not a merge,
# and a Kubernetes variable on a docker host would read as a setting that took.
# tests/test_generate.py holds RESERVED_ENV equal to what real bundles write;
# core.reserved_env() serves it with ENV_OWNER.
_ENV_OWNERS = {
    "HARBOR_ID": None,
    "SHIP_ID": None,
    "AUTH_TOKEN": "auth_token",
    "CONTAINER_MANAGER_TYPE": None,
    "DOCKER_REGISTRY": "private_registry",
    "IMAGE_OVERRIDES": "private_registry",
    "DOCKER_PORT_RANGE": None,
    # Commented-out stubs in the ConfigMap, but the option owns the names.
    "DOCKER_REGISTRY_USERNAME": "registry_auth",
    "DOCKER_REGISTRY_PASSWORD": "registry_auth",
    "DOCKER_REGISTRY_EMAIL": "registry_auth",
    "AUTO_KUBERNETES_UPDATE": "auto_update",
    "AUTO_UPDATE": "auto_update",
    "INHERIT_RUNNING_USER_AND_GROUP": "restrict_engines",
    "KUBERNETES_SECURITY_CONTEXT_CAP_JSON": "restrict_engines",
    "KUBERNETES_SERVICE_USE_TYPE": "service_type",
    "RUN_HEALTH_WEB_SERVICE": None,
    "KUBERNETES_WEB_EXPOSE_TYPE": "sv_ingress",
    "KUBERNETES_WEB_EXPOSE_SUB_DOMAIN": "sv_subdomain",
    "KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME": "sv_tls_secret",
    "KUBERNETES_ISTIO_GATEWAY_NAME": "sv_istio_gateway",
    "HOSTNAME_OVERRIDE": "sv_hostname",
    "TLS_CERT": "sv_tls_cert",
    "TLS_KEY": "sv_tls_key",
    "HTTP_PROXY": "proxy",
    "HTTPS_PROXY": "proxy",
    "NO_PROXY": "proxy",
    "KUBERNETES_TOLERATIONS_JSON": "engine_tolerations",
    "KUBERNETES_NODE_SELECTOR_JSON": "engine_node_selector",
    "KUBERNETES_RESOURCES_LIMITS_CPU": "engine_cpu_limit",
    "KUBERNETES_RESOURCES_LIMITS_MEMORY": "engine_mem_limit",
    "KUBERNETES_REQUESTS_EPHEMERAL_STORAGE": "engine_ephemeral_request_mb",
    "KUBERNETES_LIMITS_EPHEMERAL_STORAGE": "engine_ephemeral_limit_mb",
    # Every CA mode writes these three.
    **{name: " | ".join(CA_MODES) for name in
       ("REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "KUBERNETES_CA_BUNDLE_MOUNT")},
}
RESERVED_ENV = frozenset(_ENV_OWNERS)
ENV_OWNER = {name: owner for name, owner in _ENV_OWNERS.items() if owner}

# A variable name a process can read. ConfigMap keys allow dots and dashes, but
# such a variable would reach nothing.
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def extra_env(o):
    """The free-form agent variables this bundle carries, as {NAME: value}, in
    the order given.

    Refuses a malformed name, a non-scalar value, or a name the generator
    writes itself, naming the owning option where there is one.
    """
    raw = o.get("extra_env") or {}
    if not isinstance(raw, dict):
        raise ValueError("extra_env: expected an object of NAME: value pairs, "
                         f"got {type(raw).__name__}")
    out = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not ENV_NAME_RE.match(name):
            raise ValueError(
                f"extra_env: {name!r} is not a usable environment variable "
                f"name -- letters, digits and underscore only, and not "
                f"starting with a digit")
        if name in RESERVED_ENV:
            owner = ENV_OWNER.get(name)
            raise ValueError(
                f"extra_env: {name} is written by this generator"
                + (f" -- set it with the {owner} option instead"
                   if owner else " and cannot be overridden here"))
        if isinstance(value, (dict, list)):
            raise ValueError(
                f"extra_env: {name} must be a string, number or boolean -- an "
                f"environment variable is text, so encode it yourself")
        out[name] = "" if value is None else _env_value(value)
    return out


def _env_value(value):
    """A scalar as the container sees it: booleans lower case, like every other
    boolean the agent reads.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
