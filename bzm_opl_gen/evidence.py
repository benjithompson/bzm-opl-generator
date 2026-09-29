"""The cluster-evidence document's shape, stated once.

`scripts/bzm-cluster-evidence.sh` writes it on a cluster nobody here can reach;
`doctor`, `suggest` and `core.preflight` read it. Every reader treats a missing
section as one nobody could read, so a section renamed in one place alone
would silently read as "could not read" -- hence one table.
`tests/test_cluster_evidence.py` holds the shell script to DOCUMENT.

This is the shape of the *file*; the cluster mapping `doctor` normalises it
into is a separate contract (`doctor.reads`). Imports nothing.
"""

SCHEMA = "bzm-opl-cluster-evidence/1"
SCRIPT = "scripts/bzm-cluster-evidence.sh"


class UnknownSection(LookupError):
    """A reader named a path this document does not define -- a code error,
    unlike a *file* that lacks the path (read as "nobody answered")."""


# -- the top level -----------------------------------------------------------

SCHEMA_FIELD = "schema"
COLLECTED_AT = "collected_at"
# The namespace the collector was run for -- and, inside `raw`, the Namespace
# object itself. One word for both because the document uses one.
NAMESPACE = "namespace"
CLI = "cli"
RAW = "raw"
INVENTORY = "inventory"
PERMISSIONS = "permissions"
API_GROUPS = "api_groups"
OPENSHIFT = "openshift"
VERSIONS = "versions"
NOTES = "notes"

# -- raw: kubectl documents as collected, or null where the command failed ----

NODES = "nodes"
INGRESSCLASSES = "ingressclasses"
SCOPED = "scoped"                 # limitrange, resourcequota and serviceaccount
                                  # from one `get`, hence one section

# -- inventory: names only, never contents ------------------------------------

CONFIGMAPS = "configmaps"
SECRETS = "secrets"

# -- permissions: what `auth can-i` said --------------------------------------

NAMESPACED = "namespaced"
CLUSTER_SCOPED = "cluster_scoped"

CREATE_SERVICEACCOUNTS = "create serviceaccounts"
CREATE_ROLES = "create roles"
CREATE_ROLEBINDINGS = "create rolebindings"
CREATE_CONFIGMAPS = "create configmaps"
CREATE_SECRETS = "create secrets"
CREATE_DEPLOYMENTS = "create deployments"
CREATE_INGRESSES = "create ingresses"
LIST_NODES = "list nodes"
CREATE_CLUSTERROLES = "create clusterroles"
CREATE_CLUSTERROLEBINDINGS = "create clusterrolebindings"

# -- api_groups: which ingress backends the cluster could serve ---------------

OPENSHIFT_ROUTE = "openshift_route"
OPENSHIFT_SECURITY = "openshift_security"
ISTIO = "istio"
CONTOUR = "contour"

# -- openshift: cluster-level config, where there is any ----------------------

INGRESS_CONFIG = "ingress_config"
PROXY_CONFIG = "proxy_config"

# -- versions -----------------------------------------------------------------

# `kubectl version -o json` copied whole. serverVersion is named because it is
# present only when a server answered: the one way to tell "the cluster said
# no" from "the command never reached one" (`auth can-i` and `api-resources`
# report failure as no).
SERVER_VERSION = "serverVersion"


# Every key, and what is under it. A leaf is `{}`. No key may contain a dot,
# since a path is these keys joined with dots; spaces are fine.
DOCUMENT = {
    SCHEMA_FIELD: {},
    COLLECTED_AT: {},
    NAMESPACE: {},
    CLI: {},
    RAW: {NODES: {}, INGRESSCLASSES: {}, NAMESPACE: {}, SCOPED: {}},
    INVENTORY: {CONFIGMAPS: {}, SECRETS: {}},
    PERMISSIONS: {
        NAMESPACED: {CREATE_SERVICEACCOUNTS: {}, CREATE_ROLES: {},
                     CREATE_ROLEBINDINGS: {}, CREATE_CONFIGMAPS: {},
                     CREATE_SECRETS: {}, CREATE_DEPLOYMENTS: {},
                     CREATE_INGRESSES: {}},
        CLUSTER_SCOPED: {LIST_NODES: {}, CREATE_CLUSTERROLES: {},
                         CREATE_CLUSTERROLEBINDINGS: {}},
    },
    API_GROUPS: {OPENSHIFT_ROUTE: {}, OPENSHIFT_SECURITY: {}, ISTIO: {},
                 CONTOUR: {}},
    OPENSHIFT: {INGRESS_CONFIG: {}, PROXY_CONFIG: {}},
    VERSIONS: {SERVER_VERSION: {}},
    NOTES: {},
}

# Sections the collector copies whole from kubectl, so it writes the key but
# not the keys inside.
COPIED = (VERSIONS,)


def known(*parts):
    """Does the document define this path?"""
    node = DOCUMENT
    for part in parts:
        if part not in node:
            return False
        node = node[part]
    return bool(parts)


def cite(*parts):
    """The dotted path, checked as it is built, so a renamed section fails at
    the rule citing it rather than sending a reader to a missing section."""
    if not known(*parts):
        raise UnknownSection(
            f"'{'.'.join(parts)}' is not a path in the cluster evidence "
            f"document ({SCHEMA}). Its sections are stated in "
            f"bzm_opl_gen/evidence.py, and {SCRIPT} is held to the same table "
            f"-- add it in both, or fix the spelling here")
    return ".".join(parts)


def paths(node=None, prefix=()):
    """Every dotted path the document defines, sections and leaves alike."""
    node = DOCUMENT if node is None else node
    out = []
    for key, children in node.items():
        here = prefix + (key,)
        out.append(".".join(here))
        out.extend(paths(children, here))
    return tuple(out)


def collector_paths():
    """The paths the collector script itself writes: all but the insides of a
    document it copied."""
    inside = tuple(f"{section}." for section in COPIED)
    return tuple(p for p in paths() if not p.startswith(inside))
