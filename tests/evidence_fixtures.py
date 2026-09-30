"""One cluster-evidence document for every test that reads one.

`document()` is a collector that reached the cluster and was refused
nothing; a test overrides one section (`document(raw=raw(nodes=None))` is a
denied `get nodes`, `document(versions=None)` a machine that never reached
the API server). Beside it are the collected files: the all-null degraded
one and two half-read ones, the case where unread and empty are easiest to
confuse. Cluster objects come from `test_doctor`, so the live and imported
paths are fed the same objects."""

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


# A cluster that runs no admission policy engine: Kyverno's and Gatekeeper's
# resources are not served, and the built-in policy kinds hold nothing.
EMPTY_LIST = {"apiVersion": "v1", "kind": "List", "items": []}
NO_POLICY_ENGINE = {
    evidence.KYVERNO_CLUSTERPOLICIES: evidence.NOT_SERVED,
    evidence.KYVERNO_POLICIES: evidence.NOT_SERVED,
    evidence.GATEKEEPER_TEMPLATES: evidence.NOT_SERVED,
    evidence.GATEKEEPER_CONSTRAINTS: evidence.NOT_SERVED,
    evidence.ADMISSION_POLICIES: EMPTY_LIST,
    evidence.ADMISSION_POLICY_BINDINGS: EMPTY_LIST,
    evidence.VALIDATING_WEBHOOKS: EMPTY_LIST,
}


def raw(**over):
    """The `raw` sections, with any of them replaced -- `scoped=None` is a
    denied read, which is the shape the collector writes for one."""
    sections = {"nodes": NODES, "ingressclasses": CLASSES,
                "namespace": NS_BASELINE, "scoped": SCOPED,
                **NO_POLICY_ENGINE}
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
