"""The marker a required field left blank resolves to: `<KEY>`, the option key
upper-cased.

No Kubernetes name or label value may contain `<` or `>`, so the API server
refuses an object carrying one and names the field. Where a marker lands in a
value instead, the chart's validation, the docker script and compose refuse it.
"""

import re


# The shape of any marker. Readers match any marker, never one option's own:
# older profiles carry the shared `<PLACEHOLDER>`, and the page fills some
# fields itself. The chart restates it (held equal by tests/test_helm.py) and
# the docker script greps for it.
MARKER_PATTERN = "<[A-Z][A-Z0-9_]*>"
MARKER_RE = re.compile(MARKER_PATTERN)


def marker(key):
    """The marker for one option key: upper case, dots joined by underscores.

    `auth_token` gives `<AUTH_TOKEN>`, `proxy.https` `<PROXY_HTTPS>`,
    `extra_env.FOO` `<EXTRA_ENV_FOO>`.
    """
    return "<%s>" % key.replace(".", "_").upper()


def is_placeholder(v):
    """True for a value that is a marker and nothing else, surrounding
    whitespace aside.
    """
    return isinstance(v, str) and MARKER_RE.fullmatch(v.strip()) is not None


def or_marker(value, key):
    """`value`, or the marker for `key` where it is blank.

    For the identity (harbor_id, ship_id), which is not in REQUIRED_TEXT and is
    blank when a bundle is wanted before the location exists.
    """
    return value if str(value or "").strip() else marker(key)


def marker_in(text):
    """The first marker inside a rendered value, or None.

    A value can contain a marker without being one: proxy_url builds
    `user:pass@<PROXY_HTTPS>` from a blank host. Deliberately wide: a false
    refusal is loud and names the variable, a miss starts a container on a
    blank credential.
    """
    m = MARKER_RE.search(str(text))
    return m.group(0) if m else None


def helm_token_at_install(o):
    """Whether a chart bundle takes its AUTH_TOKEN at `helm install` time.

    An absent token is the recommended state for a chart, whose values file
    gets committed: the overlay writes an empty value, the README adds
    --set-string, and nothing carries a marker.
    """
    return not o.get("auth_token") or is_placeholder(o["auth_token"])
