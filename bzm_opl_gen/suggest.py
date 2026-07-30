"""What a cluster's evidence implies about how the bundle should be configured.

`doctor` asks whether a deployment would survive a cluster. The same evidence
answers the question that comes first: how the deployment should have been
configured at all. That reasoning happens today in someone's head while they
read a customer's cluster description, and it is lost the moment the call ends.

Every suggestion carries the evidence behind it and how strongly it holds:

  DECISIVE    the evidence settles it. `value` is the answer and a caller may
              offer it as a default -- the namespace already holds the
              ServiceAccount the bundle would create, so it must not create one.
  SUGGESTIVE  the evidence narrows the choice without making it. `value` is
              None and `candidates` is the shortlist a person still has to pick
              from -- the cluster serves projectcontour.io and not
              networking.istio.io, which rules some sv_ingress values out
              without choosing among the rest.

Nothing here writes to a configuration. Producing the reasoning and applying it
are separate acts, and only the first is honest without a person in the loop.

Two rules hold the whole module together:

  * Nothing is suggested from evidence the collector recorded as unreadable. A
    null section is "we did not look", never "there are none" -- the same
    distinction `doctor` keeps -- and the boolean maps need the extra care
    described at _reached_cluster().
  * Evidence that eliminates values says so, in `ruled_out`, rather than quietly
    handing back the survivor. A cluster serving exactly one ingress backend has
    narrowed the choice; it has not made it.
"""

import collections
import json

from . import doctor
from .doctor import CRANE_INGRESS_CLASS
from .generate import DEFAULT_OPTIONS, SV_INGRESS_NONE, SV_INGRESS_TYPES

# option:     the generate option this is about
# strength:   DECISIVE | SUGGESTIVE (see the module docstring)
# value:      the settled value, or None for a suggestive one
# candidates: what the evidence leaves open -- (value,) when decisive, and
#             possibly empty when the evidence ruled everything out
# ruled_out:  values this evidence eliminates, named so a reader can disagree
# evidence:   dotted paths into the evidence file, e.g. "api_groups.istio"
# detail:     why, in the terms the person reading a customer's cluster uses
Suggestion = collections.namedtuple(
    "Suggestion", "option strength value candidates ruled_out evidence detail")

DECISIVE, SUGGESTIVE = "DECISIVE", "SUGGESTIVE"


def _decisive(option, value, evidence, detail):
    return Suggestion(option, DECISIVE, value, (value,), (), tuple(evidence), detail)


def _suggestive(option, candidates, evidence, detail, ruled_out=()):
    return Suggestion(option, SUGGESTIVE, None, tuple(candidates),
                      tuple(ruled_out), tuple(evidence), detail)


def _read(doc, *path, kind):
    """One nested value out of the evidence file, or None where nothing said.

    Every section is optional and each can arrive wrong: the collector's maps
    grew over time, so a file from an older script does not carry the newer
    keys, and files come back by mail and are sometimes trimmed on the way.
    Absent, null and a section of the wrong type are all "nobody answered" --
    which is the one thing this module may never confuse with the cluster
    answering `false`, so it is decided here once rather than four times over.

    `kind` is what a well-formed value looks like there, and anything else is
    unanswered. bool is the exception and coerces rather than checks: the
    boolean maps are `auth can-i` and `api-resources` read through shell, which
    is error-to-false, so what reaches the file is whatever the script wrote --
    but only a *present* value is coerced, and null stays None. That a False
    here is the cluster's own answer rather than a failed command is
    _reached_cluster()'s doing, past which every rule is only ever called.
    """
    value = doc
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if value is None:
        return None
    if kind is bool:
        return bool(value)
    return value if isinstance(value, kind) else None


def _normalised(doc, key):
    """One `raw` section in gather_cluster()'s shape, for the two rules that
    need it (see the note at RULES for why they fetch it rather than take it).

    A `raw` section is the whole kubectl document as collected -- and
    raw.scoped is three kinds in one List -- so the rules that read one go
    through `doctor`'s normalisation rather than restating it here, for the same
    reason from_evidence() defers validation to it. Null survives that trip, so
    "not collected" still arrives as None.
    """
    return doctor.cluster_from_evidence(doc).cluster[key]


# -- platform ----------------------------------------------------------------

def _platform(doc):
    """security.openshift.io is served by OpenShift and by nothing else, which
    settles an option with exactly two values.

    It used to decide more than its name suggests -- the engine security envs
    rode on it, so choosing k8s silently gave up the restricted engine pod.
    They are on by default on both platforms now (restrict_engines), and this
    is back to deciding only what it says: whether crane's own pod pins a
    runAsUser or leaves it to an SCC."""
    served = _read(doc, "api_groups", "openshift_security", kind=bool)
    if served is None:
        return []
    if served:
        return [_decisive("platform", "openshift", ["api_groups.openshift_security"],
                          "security.openshift.io is served, which only OpenShift "
                          "does. Crane's pod leaves runAsUser to the SCC, and "
                          "engines inherit the UID it assigns")]
    return [_decisive("platform", "k8s", ["api_groups.openshift_security"],
                      "security.openshift.io is not served, so this is plain "
                      "Kubernetes: crane's pod pins runAsUser itself, and the "
                      "namespace's PodSecurity level is what decides whether "
                      "engine pods are admitted")]


# -- the service account -----------------------------------------------------

DEFAULT_SA = DEFAULT_OPTIONS["service_account_name"]


def _service_account(doc):
    """Which account crane runs as, and whether this bundle creates it.

    Two independent routes to the same `service_account_create: false`: the
    account is already there, or this token could not create one anyway. They
    are reported as one suggestion, because two verdicts about one field is a
    contradiction the reader would have to arbitrate.
    """
    accounts, out = _normalised(doc, "serviceaccounts"), []
    ns = doc.get("namespace") or "the namespace"
    names = []
    if accounts is not None:
        # `default` is in every namespace, and generate refuses to fall back to
        # it for the reason spelled out there: it would bind crane's Role to the
        # account every other pod in the namespace runs as.
        names = sorted({(sa.get("metadata") or {}).get("name")
                        for sa in accounts} - {"default", None})
    if DEFAULT_SA in names:
        out.append(_decisive(
            "service_account_create", False, ["raw.scoped"],
            f"ServiceAccount '{DEFAULT_SA}' already exists in {ns}, which is the "
            f"name the bundle references by default -- so it has one to run as "
            f"and no reason to emit the object over somebody else's"))
    elif _read(doc, "permissions", "namespaced",
               "create serviceaccounts", kind=bool) is False:
        out.append(_decisive(
            "service_account_create", False, ["permissions.namespaced"],
            f"this token cannot create ServiceAccounts in {ns} (auth can-i said "
            f"no), so a bundle carrying the object does not apply at all. The "
            f"account has to exist first -- name it with service_account_name"))
    if names:
        out.append(_suggestive(
            "service_account_name", names, ["raw.scoped"],
            f"{ns} already holds {', '.join(names)} besides `default`. Which one "
            f"crane should run as is the platform team's call, not a cluster "
            f"fact -- and an account named for another workload would quietly "
            f"gain crane's Role"))
    return out


# -- service virtualization ---------------------------------------------------

# The one thing that makes each sv_ingress value usable, and where the evidence
# file records it. nginx is the odd one out: networking.k8s.io is served
# everywhere, so what decides it is the IngressClass crane hardcodes the name of.
_SV_API_GROUPS = {"istio": ("networking.istio.io", "istio"),
                  "contour": ("projectcontour.io", "contour"),
                  "openshift": ("route.openshift.io", "openshift_route")}


def _sv_ingress(doc):
    """Which backend could publish the virtual services -- and, deliberately,
    never which one should.

    crane selects exactly one implementation and never touches the others, so
    this is a real choice about the customer's platform. The cluster only says
    which are possible; narrowing to one is still not making it.
    """
    open_, ruled_out, why, evidence = [], [], [], []
    for value in SV_INGRESS_TYPES:
        if value == "nginx":
            state, key, reason = _nginx_state(doc)
        else:
            group, key = _SV_API_GROUPS[value]
            state = _read(doc, "api_groups", key, kind=bool)
            key, reason = f"api_groups.{key}", f"{group} is not served"
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
        # An empty shortlist is the finding here, unlike the inventory rules
        # below: sv_ingress is mandatory for a mockServices location, so
        # "none of them" is what somebody needs to hear early.
        detail = (f"nothing this cluster serves can publish a virtual service: "
                  f"{'; '.join(why)}. A mockServices location deployed as-is "
                  f"stalls at WAITING_FOR_DOMAIN with the mock pod healthy -- "
                  f"install one of these controllers first")
    return [_suggestive("sv_ingress", open_, evidence, detail, ruled_out)]


def _nginx_state(doc):
    """crane writes `ingressClassName: nginx` on the Ingress it creates and
    BlazeMeter exposes no env to change it, so the class existing by that exact
    name is what makes the value usable -- the same fact doctor FAILs on once
    the choice has already been made."""
    classes = _normalised(doc, "ingressclasses")
    reason = (f"no IngressClass named '{CRANE_INGRESS_CLASS}', which crane "
              f"hardcodes on the Ingress it creates")
    if classes is None:
        return None, "raw.ingressclasses", reason
    names = {(c.get("metadata") or {}).get("name") for c in classes}
    return CRANE_INGRESS_CLASS in names, "raw.ingressclasses", reason


def _sv_subdomain(doc):
    """The wildcard the OpenShift router already serves. Suggestive because it
    is the *default* router's domain: virtual services published through an
    nginx, Contour or Istio ingress may well answer on another."""
    # Null on plain Kubernetes, where neither config kind exists at all.
    cfg = _read(doc, "openshift", "ingress_config", kind=dict) or {}
    domain = (cfg.get("spec") or {}).get("domain")
    if not domain:
        return []
    return [_suggestive(
        "sv_subdomain", [domain], ["openshift.ingress_config"],
        f"the cluster publishes applications under *.{domain}. sv_subdomain has "
        f"to be a wildcard the controller you pick actually serves, which is "
        f"this one only if the virtual services go through the OpenShift router")]


# -- registry, proxy and trust ------------------------------------------------

DOCKERCONFIGJSON = "kubernetes.io/dockerconfigjson"


def _pull_secret(doc):
    """The imagePullSecret the bundle references for a private registry. It
    never creates one, so a name that is not there is an ImagePullBackOff.

    Decisive at exactly one, unlike the CA ConfigMap below, because the secret's
    *type* is the API server's own answer about what a thing is rather than a
    guess off its name.
    """
    secrets = _read(doc, "inventory", "secrets", kind=list)
    if secrets is None:
        return []
    names = sorted(s["name"] for s in secrets
                   if isinstance(s, dict) and s.get("type") == DOCKERCONFIGJSON
                   and s.get("name"))
    ns = doc.get("namespace") or "the namespace"
    if len(names) == 1:
        return [_decisive("pull_secret", names[0], ["inventory.secrets"],
                          f"'{names[0]}' is the only {DOCKERCONFIGJSON} Secret "
                          f"in {ns}, so it is the only thing pull_secret could "
                          f"name. Nothing in the bundle creates one")]
    if names:
        return [_suggestive("pull_secret", names, ["inventory.secrets"],
                            f"{ns} holds {len(names)} {DOCKERCONFIGJSON} "
                            f"Secrets; which of them can pull the BlazeMeter "
                            f"images is a question about the registry, and this "
                            f"file carries no secret values to answer it with")]
    # Read, and there are none. Unlike sv_ingress that is not a finding: the
    # option's own default is already "no pull secret".
    return []


# Names a trust bundle is conventionally given. Contents are never collected --
# a CA bundle is ~300KB nobody needs here, and not reading one is a promise the
# collector script makes to whoever reviews it -- so this can only ever produce
# candidates.
_TRUST_BUNDLE_HINTS = ("ca-bundle", "cabundle", "ca-certs", "cacert",
                       "trusted-ca", "trust-bundle")
# In every namespace, and carrying the cluster's own CA rather than the
# corporate one an intercepting proxy needs. Offering these would send someone
# to trust the wrong issuer.
_NOT_TRUST_BUNDLES = ("kube-root-ca.crt", "openshift-service-ca.crt")


def _ca_configmap(doc):
    names = _read(doc, "inventory", "configmaps", kind=list)
    if names is None:
        return []
    hits = sorted(n for n in names
                  if isinstance(n, str) and n not in _NOT_TRUST_BUNDLES
                  and any(h in n.lower() for h in _TRUST_BUNDLE_HINTS))
    if not hits:
        return []
    ns = doc.get("namespace") or "the namespace"
    return [_suggestive(
        "ca_existing_configmap", hits, ["inventory.configmaps"],
        f"{ns} holds {', '.join(hits)}, named the way a trust bundle usually is. "
        f"Only names were collected, never contents, so this cannot go further "
        f"than a shortlist -- confirm the key ({', '.join(hits[:1])} holding a "
        f"PEM) before pointing the bundle at one")]


def _proxy(doc):
    """The cluster's own egress posture, which is the customer's real one.

    status is what the operators publish as effective -- and its noProxy is the
    expanded list a pod actually needs -- so it wins over the spec it came from.
    """
    cfg = _read(doc, "openshift", "proxy_config", kind=dict) or {}
    spec = cfg.get("status") or cfg.get("spec") or {}
    http, https = spec.get("httpProxy"), spec.get("httpsProxy")
    out = []
    if http or https:
        value = {key: v for key, v in (("http", http), ("https", https),
                                       ("no_proxy", spec.get("noProxy"))) if v}
        out.append(_decisive(
            "proxy", value, ["openshift.proxy_config"],
            f"the cluster declares an egress proxy ({https or http}). Pods that "
            f"reach BlazeMeter go through it and nothing propagates it into a "
            f"pod's env for you -- without HTTP(S)_PROXY the agent never comes "
            f"online"))
    # trustedCA lives in openshift-config, not in the agent's namespace, so it
    # is not a ca_existing_configmap candidate -- what it says is that egress is
    # TLS-intercepted, which the injected bundle is the supported answer to.
    trusted = ((cfg.get("spec") or {}).get("trustedCA") or {}).get("name")
    if trusted:
        out.append(_suggestive(
            "ca_openshift_inject", [True], ["openshift.proxy_config"],
            f"the cluster proxy carries a trusted CA bundle ('{trusted}' in "
            f"openshift-config), so egress is TLS-intercepted and crane will not "
            f"reach BlazeMeter without that CA. On OpenShift a labelled empty "
            f"ConfigMap the cluster injects into is the supported way; naming a "
            f"bundle already in the namespace (ca_existing_configmap) is the "
            f"alternative"))
    return out


def _cluster_rbac(doc):
    """Whether the optional cluster-scoped RBAC can be applied at all.

    Only the constraining direction is reported. Permitted narrows nothing --
    whether the location wants a ClusterRole is a decision about the location --
    and a line saying "either value is fine" is noise in a list whose point is
    that every line carries information.
    """
    roles = _read(doc, "permissions", "cluster_scoped",
                  "create clusterroles", kind=bool)
    bindings = _read(doc, "permissions", "cluster_scoped",
                     "create clusterrolebindings", kind=bool)
    if roles is None or bindings is None or (roles and bindings):
        return []
    missing = [n for n, ok in (("ClusterRoles", roles),
                               ("ClusterRoleBindings", bindings)) if not ok]
    return [_decisive(
        "cluster_rbac", False, ["permissions.cluster_scoped"],
        f"this token cannot create {' or '.join(missing)}, so a bundle carrying "
        f"them does not apply. Note this constrains nothing else: crane resolves "
        f"its advertised address from its own network interfaces rather than "
        f"from the Node object, and a namespaced-only install has run green")]


# A rule takes the evidence file and nothing else. It used to take `doctor`'s
# normalised cluster beside it, and six of the eight never referenced it (#59):
# the two that did read `doc` as well, and cited `raw.*` paths while doing so, so
# the normalised/raw separation the second parameter implied was never one the
# rules kept. Making that split real was the alternative and it is not available:
# `permissions`, `inventory`, `api_groups`, `openshift` and the namespace name
# are not in gather_cluster()'s shape at all, and widening that shape to suit
# this signature would change what the *live* path returns for every check in
# `doctor`. So the parameter went, and the two rules that need normalised data
# ask for it where they read it (_normalised) -- which also keeps a rule's cited
# evidence paths checkable against its own body rather than against its caller.
#
# Order is the order they are reported in: the platform first because it frames
# the rest, then the objects the bundle references, then the cluster-wide
# posture, then what may not be applied at all.
RULES = (_platform, _service_account, _sv_ingress, _sv_subdomain, _pull_secret,
         _ca_configmap, _proxy, _cluster_rbac)


def from_evidence(doc):
    """Every suggestion an evidence file supports, in reporting order.

    Validation and normalisation are `doctor`'s, deliberately: this reads the
    same file, and a second opinion about what a well-formed one looks like is
    a second thing to keep in step.
    """
    # Validate first, gate second: a file that is not evidence at all is refused
    # by name either way, rather than coming back as a quiet empty list -- and
    # refused here rather than from inside whichever rule happened to normalise
    # first, which is why the result is discarded and not threaded through.
    doctor.cluster_from_evidence(doc)
    if not _reached_cluster(doc):
        return []
    return [s for rule in RULES for s in rule(doc)]


def _reached_cluster(doc):
    """Did the collector talk to an API server at all?

    Every `raw` section is null-when-unreadable, so the rules that read one stay
    quiet on their own. The boolean maps cannot: `api_groups` comes from
    `api-resources` and `permissions` from `auth can-i`, and both are
    error-to-false in shell, so a machine with no kubeconfig produces a file
    that reads as a plain-Kubernetes cluster where nothing may be created.
    Taken at face value that is four suggestions about a cluster nobody
    described -- `doctor` reports the same file as six warnings, which is the
    honest reading, and a *configuration* guessed from it is not.

    `kubectl version -o json` is the discriminator, and costs nothing: it
    carries a serverVersion only when a server answered. `notes` cannot do this
    job -- a collector denied one namespaced read writes a note and is still
    describing a real cluster.
    """
    return bool((doc.get("versions") or {}).get("serverVersion"))


# -- reporting ---------------------------------------------------------------

def headline(s):
    """The verdict itself, without the reasoning: what a caller would apply, or
    what it would have to choose between."""
    if s.strength == DECISIVE:
        return _fmt(s.value)
    # No "one of" or "maybe": the strength column already says this is a
    # shortlist, and a single candidate dressed up as prose is exactly the
    # reading that turns into a default somewhere downstream.
    out = (", ".join(_fmt(c) for c in s.candidates) if s.candidates
           else "nothing this evidence can name")
    if s.ruled_out:
        out += "; rules out " + ", ".join(_fmt(v) for v in s.ruled_out)
    return out


def _fmt(value):
    """Options are written as JSON in profile.json, so show values the way the
    file that would carry them does -- `false`, not `False`."""
    return value if isinstance(value, str) else json.dumps(value)


def as_dict(s):
    return {"option": s.option, "strength": s.strength, "value": s.value,
            "candidates": list(s.candidates), "ruled_out": list(s.ruled_out),
            "evidence": list(s.evidence), "detail": s.detail}


# -- how a suggestion stands against a configuration --------------------------
# Producing the reasoning and applying it are separate acts (see the module
# docstring); this is the second one's rules, and it still writes nothing. A
# caller hands in the options as they are and gets back what applying would
# mean -- fill an option nobody moved, replace one somebody did, or nothing.
#
# The rule that outranks the convenience: a value somebody set is never handed
# back as a fill. Where the evidence disagrees with it, that is a CONFLICT for
# the caller to show -- both values, and the evidence behind the suggestion --
# because the bundle is what somebody deploys and one that changed under them
# is worse than one they filled in twice.

# SETTLED   the option already holds what the evidence says (a candidate of it,
#           for a suggestive one). Nothing to apply, and the only state in which
#           "the cluster confirms this" is truthful.
# FILL      decisive, and the option still holds what the generator would have
#           used anyway. The one state safe to offer as a one-click default.
# CHOOSE    suggestive, and nothing chosen yet: `candidates` is the shortlist and
#           picking from it is the user's act. Never carries a value, even at one
#           candidate -- narrowing to one is still not choosing.
# CONFLICT  the configuration holds something else. `value` is what a replace the
#           user asks for by name would write, and is None for a suggestive
#           suggestion, which has nothing single to replace it with.
SETTLED, FILL, CHOOSE, CONFLICT = "SETTLED", "FILL", "CHOOSE", "CONFLICT"

# option:  the generate option, as on the Suggestion
# state:   SETTLED | FILL | CHOOSE | CONFLICT
# current: what the configuration holds for it now, shown whatever the state --
#          applying is always a value replacing a value, and the one being
#          replaced is never left off screen
# value:   what a single click would write, or None where there is no single
#          value to write (SETTLED, CHOOSE, and any suggestive conflict). The
#          same invariant the strengths carry: a None here means a person picks.
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
    """Is this option already answered the way the evidence would answer it?
    A suggestive suggestion is satisfied by any of its candidates: the shortlist
    is the whole of what it has to say, so a configuration already holding one
    is neither something to nag about nor a disagreement."""
    # Declined counts as answered, though it is in no shortlist: sv_ingress=none
    # says this location is being generated for performance alone, and which
    # backends the cluster could serve has nothing to add to that. Without this
    # it lands in _chosen and reports CONFLICT -- the cluster contradicting a
    # decision it knows nothing about.
    if s.option == "sv_ingress" and current == SV_INGRESS_NONE:
        return True
    return current == s.value if s.strength == DECISIVE \
        else current in s.candidates


# What an option holds when nobody has touched it. "" is in here because the web
# UI seeds one into the field a group reveals -- switching CA trust on shows an
# empty ConfigMap name -- and reading that as a value would make every group the
# user opened a conflict with nothing in it.
_UNSET = (None, "", {}, [])


def _chosen(option, current):
    """Did somebody set this, as far as anything can tell?

    A departure from the generator's own default is the test, and it is the only
    one available. A field holding the default is indistinguishable from one
    nobody touched: profile.json carries every option resolved, and the web UI
    seeds /api/option-defaults into its options on load, so `platform` reads
    "openshift" for every caller from the first render. Only a record of which
    keys were *typed* could separate them, and keeping one would move this
    promise out of here and into whichever caller remembered to keep it.

    So this is knowingly wrong in one direction: a deliberate choice that equals
    the default is read as untouched, and gets FILL where CONFLICT would be
    truer. Erring the other way is worse -- drop the comparison and every
    unmoved default becomes an amber disagreement, on every import, which is how
    a panel stops being read. What makes either safe is that nothing is applied
    without a click on a row showing both values: this decides how loudly to
    ask, not whether to. Pinned in
    test_a_deliberate_choice_that_matches_the_default_reads_as_untouched.
    """
    return current not in _UNSET and current != DEFAULT_OPTIONS.get(option)


def merged_as_dict(s, options):
    """A suggestion plus how it stands, as one wire object. Deliberately
    `as_dict` extended rather than a second shape beside it: the browser reads
    these two facts about the same suggestion in one row, and a second envelope
    is how the two start disagreeing about which suggestion they describe."""
    m = merge(s, options)
    return dict(as_dict(s), state=m.state, current=m.current)


def report(doc, suggestions):
    """Print the suggestions, and -- when there are none -- why."""
    print(f"suggestions from cluster evidence collected "
          f"{doc.get('collected_at') or 'at an unrecorded time'} for namespace "
          f"{doc.get('namespace') or '(unnamed)'}")
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
                f"file carries no versions.serverVersion, so its permission and "
                f"api-group answers are all `false` because the commands failed "
                f"rather than because the cluster said no. Nothing here "
                f"describes a cluster to suggest from -- re-collect with "
                f"{doctor.EVIDENCE_SCRIPT} pointed at it")
    return ("nothing in this evidence constrains the generate options -- the "
            "collector read the cluster, and none of what it saw decides or "
            "narrows one")
