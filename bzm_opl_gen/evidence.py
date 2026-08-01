"""The cluster-evidence document's shape, stated once.

`scripts/bzm-cluster-evidence.sh` writes a JSON document on a cluster nobody
here can reach. `doctor` normalises it into verdicts, `suggest` reads the same
file for what it implies about the generate options, and `core.preflight` reads
the namespace it was collected for. Four readers, one shape -- and the shape
used to be written out longhand in every one of them, the collector included.

That is worse than it sounds, because of what a renamed section does. Every
reader here treats a section it cannot find as one nobody could read, which is
the right answer for a file whose collector was refused and the wrong one for a
file whose collector wrote the same data under another name: the report says
"could not read nodes" about a section sitting right there. Nothing fails, and
the sentence is indistinguishable from an honest one. So the names live here,
and a rename is a change to this table.

What holds the *shell* script to it is `tests/test_cluster_evidence.py`, which
parses the script's emitting half and compares the keys it writes with
DOCUMENT. A shell script cannot import a Python table, and a comment in each
file claiming the two agree is exactly what was there before.

This states the shape of the *file*, and nothing else. What `doctor` normalises
it into -- the cluster mapping the checks read -- is a different shape with its
own contract (`doctor.reads`), and folding the two together would make a rename
in the collector look like a change to what a check may ask for.

Imports nothing: it is a statement about a file format, and everything that
touches the format depends on it.
"""

SCHEMA = "bzm-opl-cluster-evidence/1"
SCRIPT = "scripts/bzm-cluster-evidence.sh"


class UnknownSection(LookupError):
    """A reader named a path this document has no key for.

    Not the same thing as a *file* that does not carry the path -- files come
    back trimmed and every reader here takes that as "nobody answered", which is
    the whole point of the null-vs-empty rule. This is the other case: code
    asking for a section the format does not define, which no file will ever
    satisfy and which every reader would otherwise report as unread forever.
    """


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

# `kubectl version -o json` copied whole, so the keys below it are kubectl's
# rather than the collector's. This one is named because `suggest` reads it: it
# is present only when a server actually answered, which is the only thing in
# the file that tells "the cluster said no" from "the command never reached
# one" (`auth can-i` and `api-resources` both report failure as no).
SERVER_VERSION = "serverVersion"


# Every key, and what is under it. A leaf is `{}` -- a scalar, an array of
# names, or a kubectl document whose insides are not ours to name.
#
# No key here may contain a dot: a path is these keys joined with one, so a
# dotted key would split into two that are not in the table, and cite() would
# refuse a path the document does define. (Spaces are fine, and the permission
# probes are `auth can-i`'s own words.)
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

# Sections the collector writes the key of and not the keys inside. There is
# one, and the test that holds the script to this table excludes it: expecting
# the script to write `serverVersion` would be expecting it to rewrite kubectl's
# document, which is the one thing it promises a reviewer it does not do.
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
    """The dotted path, checked as it is built.

    Every suggestion names the evidence behind it so a reader can go and look,
    and a path that no longer exists sends them to a section that is not there
    -- which reads, from the file's side, exactly like a collector that was
    refused it. Cheap to check here, and it makes a rename fail in the rules
    that cite the old name rather than in the reader who went looking.
    """
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
    """The paths the collector script itself writes -- everything but the
    insides of a document it copied. What the script is held to."""
    inside = tuple(f"{section}." for section in COPIED)
    return tuple(p for p in paths() if not p.startswith(inside))
