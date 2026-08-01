"""One cluster-evidence document for every test that reads one.

There used to be two builders for this schema -- one in `test_cluster_evidence`
and one in `test_suggest` -- with different defaults for the same file: one
described a cluster read whole by a collector that never reached an API server,
the other a half-read cluster on a machine that did. `test_server` imported both
and had to keep straight which of the two it was asking. Neither was wrong; they
simply disagreed, and a document that disagrees with itself is no baseline to
override one section of.

So there is one document here, and it is the honest baseline: a collector that
reached the cluster and was refused nothing. Everything a test wants to say is
said by overriding one section of it -- `document(raw=raw(nodes=None))` is a
denied `get nodes`, `document(versions=None)` is a machine that never reached
the API server -- which is the granularity the collector actually fails at.

Beside it are the files a collector really wrote, which is the other half: the
all-null degraded one, and two *half-read* ones. Half-read is what the
unread-vs-empty rule is about -- a document where some sections are null and
others are not is the one where a reader can confuse the two and still look
right -- and a fixture built by hand tends to be all of one or all of the other.

The cluster objects come from `test_doctor`, deliberately: the imported and the
live paths must be fed literally the same objects, or the parity test compares
two things that were never the same cluster.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bzm_opl_gen import evidence  # noqa: E402
from test_doctor import (LR_MATCHING, NS_BASELINE, QUOTA_ITEM,  # noqa: E402,F401
                         _big, _ingressclass)

HERE = os.path.dirname(os.path.abspath(__file__))

# The files a collector wrote, kept as files because that is what arrives: a
# customer mails one back, and `doctor --cluster-evidence` reads it off disk.
DEGRADED = os.path.join(HERE, "cluster-evidence.degraded.json")
# Namespaced RBAC only -- the common customer token. The cluster-scoped reads
# were refused; everything inside the namespace was read.
CLUSTER_SCOPED_DENIED = os.path.join(
    HERE, "cluster-evidence.cluster-scoped-denied.json")
# The mirror image: the cluster-scoped reads landed and the namespaced ones did
# not, which is what a reader elsewhere in the cluster collects.
NAMESPACE_DENIED = os.path.join(HERE, "cluster-evidence.namespace-denied.json")
FILES = (DEGRADED, CLUSTER_SCOPED_DENIED, NAMESPACE_DENIED)
# The two that carry both answers at once. Every check over these has to keep
# null and empty apart to come out right, which is not true of the all-null one.
HALF_READ = (CLUSTER_SCOPED_DENIED, NAMESPACE_DENIED)


def load(path):
    with open(path) as fh:
        return json.load(fh)


# -- the pieces --------------------------------------------------------------
#
# What kubectl really returns: whole List documents, `.items` inside. The script
# copies them into the evidence file verbatim, so both paths start from these.

NODES = {"apiVersion": "v1", "kind": "NodeList", "items": [_big("a"), _big("b")]}
CLASSES = {"apiVersion": "v1", "kind": "List", "items": [_ingressclass("nginx")]}


def sa(name):
    return {"kind": "ServiceAccount", "metadata": {"name": name}}


def scoped(*accounts):
    """`raw.scoped` -- one `get` of three kinds, which is why it is one section.
    With no accounts named it holds the LimitRange and quota alone."""
    return {"apiVersion": "v1", "kind": "List",
            "items": [dict(LR_MATCHING, kind="LimitRange"), QUOTA_ITEM,
                      *(sa(n) for n in accounts)]}


def classes(*names):
    return {"apiVersion": "v1", "kind": "List",
            "items": [_ingressclass(n) for n in names]}


# Every namespace has `default`, and nothing else here does -- a namespace whose
# accounts are somebody's decision is a thing a test says by naming them.
SCOPED = scoped("default")

PERMISSIONS = {"namespaced": {"create serviceaccounts": True,
                              "create roles": True,
                              "create rolebindings": True,
                              "create configmaps": True,
                              "create secrets": True,
                              "create deployments": True,
                              "create ingresses": True},
               "cluster_scoped": {"list nodes": True,
                                  "create clusterroles": True,
                                  "create clusterrolebindings": True}}

API_GROUPS = {"openshift_route": True, "openshift_security": True,
              "istio": False, "contour": False}

# `kubectl version -o json` carries a serverVersion only when a server answered,
# which is how `suggest` tells a cluster that said no from a command that never
# reached one. The baseline reached one.
SERVED = {"clientVersion": {"gitVersion": "v1.29.4"},
          "serverVersion": {"gitVersion": "v1.29.4"}}


def raw(**over):
    """The `raw` sections, with any of them replaced -- `scoped=None` is a
    denied read, which is the shape the collector writes for one."""
    sections = {"nodes": NODES, "ingressclasses": CLASSES,
                "namespace": NS_BASELINE, "scoped": SCOPED}
    sections.update(over)
    return sections


def document(**over):
    """An evidence file as the script emits one, with any top-level section
    replaced wholesale -- that is the granularity the collector fails at."""
    doc = {
        "schema": evidence.SCHEMA,
        "collected_at": "2026-07-27T10:00:00Z",
        "namespace": "blazemeter",
        "cli": "kubectl",
        "raw": raw(),
        "inventory": {"configmaps": [], "secrets": []},
        "permissions": PERMISSIONS,
        "api_groups": API_GROUPS,
        "openshift": {"ingress_config": None, "proxy_config": None},
        "versions": SERVED,
        "notes": [],
    }
    doc.update(over)
    return doc
