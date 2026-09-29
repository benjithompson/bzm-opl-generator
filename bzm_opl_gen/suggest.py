"""What a cluster's evidence implies about how the bundle should be configured.

`doctor` asks whether a deployment would survive a cluster; the same evidence
answers how it should have been configured. Each suggestion carries the
evidence behind it and how strongly it holds:

  DECISIVE    the evidence settles it; `value` is the answer (the namespace
              already holds the ServiceAccount, so the bundle must not create it)
  SUGGESTIVE  it narrows without choosing; `value` is None and `candidates` is
              the shortlist a person picks from

Nothing here writes to a configuration. Two rules:

  * Nothing is suggested from a section the collector could not read (null is
    "we did not look", never "there are none"); see _reached_cluster() for the
    boolean maps.
  * Evidence that eliminates values says so, in `ruled_out`, rather than
    handing back the survivor as if chosen.
"""

import collections
import json

from . import doctor
# Aliased: `evidence` is what a Suggestion's paths are called.
from . import evidence as evidence_mod
from .doctor import CRANE_INGRESS_CLASS
from .bundle_options import DEFAULT_OPTIONS
from .ca_trust import CA_MODES
from .service_virt import SV_INGRESS_NONE, SV_INGRESS_TYPES

# option:     the generate option this is about
# strength:   DECISIVE | SUGGESTIVE
# value:      the settled value, or None for a suggestive one
# candidates: what the evidence leaves open -- (value,) when decisive, possibly
#             empty when everything was ruled out
# ruled_out:  values this evidence eliminates, named so a reader can disagree
# evidence:   dotted paths into the evidence file, e.g. "api_groups.istio"
# detail:     why, in the terms of someone reading a customer's cluster
Suggestion = collections.namedtuple(
    "Suggestion", "option strength value candidates ruled_out evidence detail")

DECISIVE, SUGGESTIVE = "DECISIVE", "SUGGESTIVE"


def _decisive(option, value, evidence, detail):
    return Suggestion(option, DECISIVE, value, (value,), (), tuple(evidence), detail)


def _suggestive(option, candidates, evidence, detail, ruled_out=()):
    return Suggestion(option, SUGGESTIVE, None, tuple(candidates),
                      tuple(ruled_out), tuple(evidence), detail)


# Where the rules read, as the dotted paths they also cite -- built through
# evidence.cite at import, so a renamed section fails here by name. A rule may
# read one probe and cite the map it is in, sending the reader to the whole map.
_cite = evidence_mod.cite

API_GROUPS_OPENSHIFT_SECURITY = _cite(evidence_mod.API_GROUPS,
                                      evidence_mod.OPENSHIFT_SECURITY)
RAW_INGRESSCLASSES = _cite(evidence_mod.RAW, evidence_mod.INGRESSCLASSES)
RAW_SCOPED = _cite(evidence_mod.RAW, evidence_mod.SCOPED)
CAN_CREATE_SERVICEACCOUNTS = _cite(evidence_mod.PERMISSIONS,
                                   evidence_mod.NAMESPACED,
                                   evidence_mod.CREATE_SERVICEACCOUNTS)
NAMESPACED_PERMISSIONS = _cite(evidence_mod.PERMISSIONS,
                               evidence_mod.NAMESPACED)
CAN_CREATE_CLUSTERROLES = _cite(evidence_mod.PERMISSIONS,
                                evidence_mod.CLUSTER_SCOPED,
                                evidence_mod.CREATE_CLUSTERROLES)
CAN_CREATE_CLUSTERROLEBINDINGS = _cite(evidence_mod.PERMISSIONS,
                                       evidence_mod.CLUSTER_SCOPED,
                                       evidence_mod.CREATE_CLUSTERROLEBINDINGS)
CLUSTER_PERMISSIONS = _cite(evidence_mod.PERMISSIONS,
                            evidence_mod.CLUSTER_SCOPED)
INVENTORY_SECRETS = _cite(evidence_mod.INVENTORY, evidence_mod.SECRETS)
INVENTORY_CONFIGMAPS = _cite(evidence_mod.INVENTORY, evidence_mod.CONFIGMAPS)
OPENSHIFT_INGRESS_CONFIG = _cite(evidence_mod.OPENSHIFT, evidence_mod.INGRESS_CONFIG)
OPENSHIFT_PROXY_CONFIG = _cite(evidence_mod.OPENSHIFT, evidence_mod.PROXY_CONFIG)
VERSIONS_SERVER_VERSION = _cite(evidence_mod.VERSIONS, evidence_mod.SERVER_VERSION)


def _read(doc, path, kind):
    """One nested value out of the evidence file, or None where nothing said.

    `path` is one of the dotted paths above. Absent, null and wrong-typed are
    all None ("nobody answered"); a path the document does not define raises
    evidence.UnknownSection. `kind=bool` coerces only a present value, so a
    refused probe never arrives as False.
    """
    keys = path.split(".")
    if not evidence_mod.known(*keys):
        raise evidence_mod.UnknownSection(
            f"'{path}' is not a path in the cluster evidence document "
            f"({evidence_mod.SCHEMA}); its sections are stated in "
            f"bzm_opl_gen/evidence.py")
    value = doc
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if value is None:
        return None
    if kind is bool:
        return bool(value)
    return value if isinstance(value, kind) else None


def _normalised(doc, key):
    """One `raw` section in gather_cluster()'s shape, through doctor's
    normalisation (raw.scoped is three kinds in one List). Null stays None."""
    return doctor.cluster_from_evidence(doc).cluster[key]


# -- platform ----------------------------------------------------------------

def _platform(doc):
    """security.openshift.io is served by OpenShift and nothing else. Decides
    whether crane's pod pins a runAsUser or leaves it to an SCC."""
    served = _read(doc, API_GROUPS_OPENSHIFT_SECURITY, kind=bool)
    if served is None:
        return []
    if served:
        return [_decisive("platform", "openshift", [API_GROUPS_OPENSHIFT_SECURITY],
                          "security.openshift.io is served, which only OpenShift "
                          "does. Crane's pod leaves runAsUser to the SCC, and "
                          "engines inherit the UID it assigns")]
    return [_decisive("platform", "k8s", [API_GROUPS_OPENSHIFT_SECURITY],
                      "security.openshift.io is not served, so this is plain "
                      "Kubernetes: crane's pod pins runAsUser itself, and the "
                      "namespace's PodSecurity level is what decides whether "
                      "engine pods are admitted")]


# -- the service account -----------------------------------------------------

DEFAULT_SA = DEFAULT_OPTIONS["service_account_name"]


def _service_account(doc):
    """Which account crane runs as, and whether the bundle creates it. Two
    routes to `service_account_create: false` (it exists; it cannot be
    created) are one suggestion, not two verdicts about one field."""
    accounts, out = _normalised(doc, "serviceaccounts"), []
    ns = doc.get(evidence_mod.NAMESPACE) or "the namespace"
    names = []
    if accounts is not None:
        # Never `default`: that would bind crane's Role to every pod's account.
        names = sorted({(sa.get("metadata") or {}).get("name")
                        for sa in accounts} - {"default", None})
    if DEFAULT_SA in names:
        out.append(_decisive(
            "service_account_create", False, [RAW_SCOPED],
            f"ServiceAccount '{DEFAULT_SA}' already exists in {ns}, which is the "
            f"name the bundle references by default -- so it has one to run as "
            f"and no reason to emit the object over somebody else's"))
    elif _read(doc, CAN_CREATE_SERVICEACCOUNTS, kind=bool) is False:
        out.append(_decisive(
            "service_account_create", False, [NAMESPACED_PERMISSIONS],
            f"this token cannot create ServiceAccounts in {ns} (auth can-i said "
            f"no), so a bundle carrying the object does not apply at all. The "
            f"account has to exist first -- name it with service_account_name"))
    if names:
        out.append(_suggestive(
            "service_account_name", names, [RAW_SCOPED],
            f"{ns} already holds {', '.join(names)} besides `default`. Which one "
            f"crane should run as is the platform team's call, not a cluster "
            f"fact -- and an account named for another workload would quietly "
            f"gain crane's Role"))
    return out


# -- service virtualization ---------------------------------------------------

# What makes each sv_ingress value usable, and where the file records it. nginx
# is decided by the IngressClass crane hardcodes, since networking.k8s.io is
# served everywhere.
_SV_API_GROUPS = {
    "istio": ("networking.istio.io", evidence_mod.ISTIO),
    "contour": ("projectcontour.io", evidence_mod.CONTOUR),
    "openshift": ("route.openshift.io", evidence_mod.OPENSHIFT_ROUTE),
}


def _sv_ingress(doc):
    """Which backends could publish virtual services -- never which should:
    crane uses exactly one, and choosing is a decision about the platform."""
    open_, ruled_out, why, evidence = [], [], [], []
    for value in SV_INGRESS_TYPES:
        if value == "nginx":
            state, key, reason = _nginx_state(doc)
        else:
            group, key = _SV_API_GROUPS[value]
            key = _cite(evidence_mod.API_GROUPS, key)
            state, reason = _read(doc, key, kind=bool), f"{group} is not served"
        if state is None:
            continue                      # not collected: neither open nor out
        evidence.append(key)
        if state:
            open_.append(value)
        else:
            ruled_out.append(value)
            why.append(f"{value} ({reason})")
    if not evidence:
        return []
    if open_:
        detail = (f"the cluster can serve {', '.join(open_)}"
                  + (f", and rules out {'; '.join(why)}" if why else "")
                  + ". crane publishes through exactly one of these and which "
                    "one is a decision about the platform, not a cluster fact")
    else:
        # sv_ingress is mandatory for a mockServices location, so "none" is
        # the finding.
        detail = (f"nothing this cluster serves can publish a virtual service: "
                  f"{'; '.join(why)}. A mockServices location deployed as-is "
                  f"stalls at WAITING_FOR_DOMAIN with the mock pod healthy -- "
                  f"install one of these controllers first")
    return [_suggestive("sv_ingress", open_, evidence, detail, ruled_out)]


def _nginx_state(doc):
    """Is there an IngressClass named exactly what crane hardcodes?"""
    classes = _normalised(doc, "ingressclasses")
    reason = (f"no IngressClass named '{CRANE_INGRESS_CLASS}', which crane "
              f"hardcodes on the Ingress it creates")
    if classes is None:
        return None, RAW_INGRESSCLASSES, reason
    names = {(c.get("metadata") or {}).get("name") for c in classes}
    return CRANE_INGRESS_CLASS in names, RAW_INGRESSCLASSES, reason


def _sv_subdomain(doc):
    """The OpenShift router's wildcard. Suggestive: another ingress may answer
    on another domain."""
    # Null on plain Kubernetes.
    cfg = _read(doc, OPENSHIFT_INGRESS_CONFIG, kind=dict) or {}
    domain = (cfg.get("spec") or {}).get("domain")
    if not domain:
        return []
    return [_suggestive(
        "sv_subdomain", [domain], [OPENSHIFT_INGRESS_CONFIG],
        f"the cluster publishes applications under *.{domain}. sv_subdomain has "
        f"to be a wildcard the controller you pick actually serves, which is "
        f"this one only if the virtual services go through the OpenShift router")]


# -- registry, proxy and trust ------------------------------------------------

DOCKERCONFIGJSON = "kubernetes.io/dockerconfigjson"


def _pull_secret(doc):
    """The imagePullSecret for a private registry (the bundle never creates
    one). Decisive at exactly one: a Secret's type is the API server's answer,
    not a guess off its name."""
    secrets = _read(doc, INVENTORY_SECRETS, kind=list)
    if secrets is None:
        return []
    names = sorted(s["name"] for s in secrets
                   if isinstance(s, dict) and s.get("type") == DOCKERCONFIGJSON
                   and s.get("name"))
    ns = doc.get(evidence_mod.NAMESPACE) or "the namespace"
    if len(names) == 1:
        return [_decisive("pull_secret", names[0], [INVENTORY_SECRETS],
                          f"'{names[0]}' is the only {DOCKERCONFIGJSON} Secret "
                          f"in {ns}, so it is the only thing pull_secret could "
                          f"name. Nothing in the bundle creates one")]
    if names:
        return [_suggestive("pull_secret", names, [INVENTORY_SECRETS],
                            f"{ns} holds {len(names)} {DOCKERCONFIGJSON} "
                            f"Secrets; which of them can pull the BlazeMeter "
                            f"images is a question about the registry, and this "
                            f"file carries no secret values to answer it with")]
    return []           # none: the option's default is already "no secret"


# Names a trust bundle is conventionally given. Only names are collected, never
# contents, so this can only produce candidates.
_TRUST_BUNDLE_HINTS = ("ca-bundle", "cabundle", "ca-certs", "cacert",
                       "trusted-ca", "trust-bundle")
# In every namespace, carrying the cluster's own CA rather than a corporate one.
_NOT_TRUST_BUNDLES = ("kube-root-ca.crt", "openshift-service-ca.crt")


def _ca_configmap(doc):
    names = _read(doc, INVENTORY_CONFIGMAPS, kind=list)
    if names is None:
        return []
    hits = sorted(n for n in names
                  if isinstance(n, str) and n not in _NOT_TRUST_BUNDLES
                  and any(h in n.lower() for h in _TRUST_BUNDLE_HINTS))
    if not hits:
        return []
    ns = doc.get(evidence_mod.NAMESPACE) or "the namespace"
    return [_suggestive(
        "ca_existing_configmap", hits, [INVENTORY_CONFIGMAPS],
        f"{ns} holds {', '.join(hits)}, named the way a trust bundle usually is. "
        f"Only names were collected, never contents, so this cannot go further "
        f"than a shortlist -- confirm the key ({', '.join(hits[:1])} holding a "
        f"PEM) before pointing the bundle at one")]


def _proxy(doc):
    """The cluster's own egress proxy. status (effective, with the expanded
    noProxy) wins over spec."""
    cfg = _read(doc, OPENSHIFT_PROXY_CONFIG, kind=dict) or {}
    spec = cfg.get("status") or cfg.get("spec") or {}
    http, https = spec.get("httpProxy"), spec.get("httpsProxy")
    out = []
    if http or https:
        value = {key: v for key, v in (("http", http), ("https", https),
                                       ("no_proxy", spec.get("noProxy"))) if v}
        out.append(_decisive(
            "proxy", value, [OPENSHIFT_PROXY_CONFIG],
            f"the cluster declares an egress proxy ({https or http}). Pods that "
            f"reach BlazeMeter go through it and nothing propagates it into a "
            f"pod's env for you -- without HTTP(S)_PROXY the agent never comes "
            f"online"))
    # trustedCA lives in openshift-config, not the agent's namespace: it says
    # egress is TLS-intercepted, and injection is the supported answer.
    trusted = ((cfg.get("spec") or {}).get("trustedCA") or {}).get("name")
    if trusted:
        out.append(_suggestive(
            "ca_openshift_inject", [True], [OPENSHIFT_PROXY_CONFIG],
            f"the cluster proxy carries a trusted CA bundle ('{trusted}' in "
            f"openshift-config), so egress is TLS-intercepted and crane will not "
            f"reach BlazeMeter without that CA. On OpenShift a labelled empty "
            f"ConfigMap the cluster injects into is the supported way; naming a "
            f"bundle already in the namespace (ca_existing_configmap) is the "
            f"alternative"))
    return out


def _cluster_rbac(doc):
    """Whether the optional cluster-scoped RBAC can be applied. Only the
    constraining direction is reported; permitted narrows nothing."""
    roles = _read(doc, CAN_CREATE_CLUSTERROLES, kind=bool)
    bindings = _read(doc, CAN_CREATE_CLUSTERROLEBINDINGS, kind=bool)
    if roles is None or bindings is None or (roles and bindings):
        return []
    missing = [n for n, ok in (("ClusterRoles", roles),
                               ("ClusterRoleBindings", bindings)) if not ok]
    return [_decisive(
        "cluster_rbac", False, [CLUSTER_PERMISSIONS],
        f"this token cannot create {' or '.join(missing)}, so a bundle carrying "
        f"them does not apply. Note this constrains nothing else: crane resolves "
        f"its advertised address from its own network interfaces rather than "
        f"from the Node object, and a namespaced-only install has run green")]


# A rule takes the evidence file only; the two needing normalised sections ask
# for them (_normalised). Reporting order: platform first because it frames the
# rest, then referenced objects, cluster-wide posture, and what cannot apply.
RULES = (_platform, _service_account, _sv_ingress, _sv_subdomain, _pull_secret,
         _ca_configmap, _proxy, _cluster_rbac)


def from_evidence(doc):
    """Every suggestion an evidence file supports, in reporting order.
    Validation is doctor's: a file that is not evidence is refused by name."""
    doctor.cluster_from_evidence(doc)
    if not _reached_cluster(doc):
        return []
    return [s for rule in RULES for s in rule(doc)]


def _reached_cluster(doc):
    """Did the collector talk to an API server at all?

    `api_groups` and `permissions` come from shell commands that turn an error
    into false, so a file collected with no kubeconfig reads as a locked-down
    plain-Kubernetes cluster. `kubectl version`'s serverVersion is present only
    when a server answered; `notes` cannot decide this, since a collector
    denied one read still describes a real cluster.
    """
    return bool(_read(doc, VERSIONS_SERVER_VERSION, kind=dict))


# -- reporting ---------------------------------------------------------------

def headline(s):
    """The verdict without the reasoning: what a caller would apply, or what it
    would have to choose between."""
    if s.strength == DECISIVE:
        return _fmt(s.value)
    out = (", ".join(_fmt(c) for c in s.candidates) if s.candidates
           else "nothing this evidence can name")
    if s.ruled_out:
        out += "; rules out " + ", ".join(_fmt(v) for v in s.ruled_out)
    return out


def _fmt(value):
    """Values as profile.json writes them: `false`, not `False`."""
    return value if isinstance(value, str) else json.dumps(value)


def shown(value):
    """_fmt, with unset (None, or the "" a form seeds) said in words."""
    return "not set" if value is None or value == "" else _fmt(value)


def as_dict(s):
    return {"option": s.option, "strength": s.strength, "value": s.value,
            "candidates": list(s.candidates), "ruled_out": list(s.ruled_out),
            "evidence": list(s.evidence), "detail": s.detail}


# -- how a suggestion stands against a configuration --------------------------
# Still writes nothing. A value somebody set is never handed back as a fill:
# where the evidence disagrees, that is a CONFLICT showing both values.

# SETTLED   the option already holds what the evidence says (any candidate, for
#           a suggestive one); nothing to apply
# FILL      decisive, and the option still holds the generator's default; safe
#           to offer as a one-click default
# CHOOSE    suggestive and nothing chosen; never carries a value, even at one
#           candidate -- narrowing to one is still not choosing
# CONFLICT  the configuration holds something else; `value` is what a replace
#           would write, None for a suggestive suggestion
SETTLED, FILL, CHOOSE, CONFLICT = "SETTLED", "FILL", "CHOOSE", "CONFLICT"

# current: what the configuration holds now, shown in every state
# value:   what one click would write, or None where a person has to pick
Merge = collections.namedtuple("Merge", "option state current value")


def merge(s, options):
    """How suggestion `s` stands against `options`."""
    current = options.get(s.option, DEFAULT_OPTIONS.get(s.option))
    value = s.value if s.strength == DECISIVE else None
    if _holds(s, current):
        return Merge(s.option, SETTLED, current, None)
    if _chosen(s.option, current):
        return Merge(s.option, CONFLICT, current, value)
    return Merge(s.option, FILL if s.strength == DECISIVE else CHOOSE,
                 current, value)


def _holds(s, current):
    """Is this option already answered the way the evidence would answer it?"""
    # sv_ingress=none (performance only) is an answer the cluster cannot
    # contradict.
    if s.option == "sv_ingress" and current == SV_INGRESS_NONE:
        return True
    return current == s.value if s.strength == DECISIVE \
        else current in s.candidates


# What an option holds when nobody touched it; "" is what the web UI seeds
# into a field a group reveals.
_UNSET = (None, "", {}, [])


def _chosen(option, current):
    """Did somebody set this, as far as anything can tell?

    A departure from the default is the only test available: profile.json and
    the web UI both carry every default resolved. So a deliberate choice equal
    to the default reads as untouched (FILL rather than CONFLICT) -- safe,
    because nothing applies without a click on a row showing both values.
    """
    return current not in _UNSET and current != DEFAULT_OPTIONS.get(option)


def blocked_by(option, options):
    """Why writing `option` cannot be offered against `options`, or None.

    Only CA trust so far: generate takes one CA mode, and clearing the other is
    the silent overwrite this module may not make. Truthiness is the test (""
    and False are unset); a mode never blocks itself."""
    if option not in CA_MODES:
        return None
    held = next((k for k in CA_MODES if (options or {}).get(k)), None)
    if not held or held == option:
        return None
    return (f"custom CA trust already uses {CA_MODES[held]} — clear it first, "
            f"because a bundle carrying two CA modes does not generate")


def merged_as_dict(s, options):
    """as_dict plus how the suggestion stands, as one wire object. The `_shown`
    fields are the display values (see shown); the raw ones are what applies."""
    m = merge(s, options)
    return dict(as_dict(s), state=m.state, current=m.current,
                current_shown=shown(m.current), value_shown=shown(s.value),
                candidates_shown=[shown(c) for c in s.candidates],
                ruled_out_shown=[shown(v) for v in s.ruled_out],
                blocked=blocked_by(s.option, options))


def report(doc, suggestions):
    """Print the suggestions, and -- when there are none -- why."""
    print(f"suggestions from cluster evidence collected "
          f"{doc.get(evidence_mod.COLLECTED_AT) or 'at an unrecorded time'} "
          f"for namespace {doc.get(evidence_mod.NAMESPACE) or '(unnamed)'}")
    if not suggestions:
        print(f"  {why_nothing(doc)}")
        return
    width = max(len(s.option) for s in suggestions)
    for s in suggestions:
        print(f"{s.strength:<10}  {s.option:<{width}}  {headline(s)}\n"
              f"{'':<10}  {'':<{width}}  {s.detail} "
              f"[{', '.join(s.evidence)}]")
    print("Nothing has been applied: these are what the cluster implies, for "
          "you to pass to `generate`.")


def why_nothing(doc):
    if not _reached_cluster(doc):
        return (f"the collector never reached the cluster's API server: this "
                f"file carries no {VERSIONS_SERVER_VERSION}, so its permission and "
                f"api-group answers are all `false` because the commands failed "
                f"rather than because the cluster said no. Nothing here "
                f"describes a cluster to suggest from -- re-collect with "
                f"{evidence_mod.SCRIPT} pointed at it")
    return ("nothing in this evidence constrains the generate options -- the "
            "collector read the cluster, and none of what it saw decides or "
            "narrows one")
