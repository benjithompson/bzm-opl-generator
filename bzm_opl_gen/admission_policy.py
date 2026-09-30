"""Admission policy engines, judged against the pods a bundle makes.

PodSecurity labels are not the only admission a corporate cluster runs.
Kyverno, Gatekeeper and ValidatingAdmissionPolicy refuse pods at creation, and
crane creates its engine pods after the agent reads online, so a refusal shows
as a run that hangs. This module says which policies enforce on the target
namespace and what each would refuse.

Pure: it judges policy objects already fetched, live or from an evidence file.
A section is a list of objects, None (nobody could read it) or
evidence.NOT_SERVED (the API server serves no such resource: not installed).
Recognition is by rule shape, constraint kind and policy name; anything
enforcing that is not recognised is named for review, never passed.
"""

import collections
import fnmatch
import json
import re

from . import evidence
from .bundle_options import (DEFAULT_OPTIONS, engine_request_quantities,
                             engine_size)
from .facts import select_images
from .footprint import (CRANE_CPU_LIMIT, CRANE_CPU_REQUEST, CRANE_MEM_LIMIT,
                        CRANE_MEM_REQUEST, PUBLIC_REGISTRY, engine_requests)
from .image_registry import composed_image_ref, crane_image
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory
from .verdict import FAIL, PASS, WARN, Check

KYVERNO = "Kyverno"
GATEKEEPER = "Gatekeeper"
VAP = "ValidatingAdmissionPolicy"

# What the platform team grants, per engine, so the namespace is left out.
EXCEPTION = {
    KYVERNO: "a Kyverno PolicyException",
    GATEKEEPER: "the namespace added to the constraint's excludedNamespaces",
    VAP: "a binding namespaceSelector that leaves the namespace out",
}

# -- what a policy can demand of a pod ---------------------------------------

LATEST = "tag"
DIGEST = "digest"
REGISTRY = "registry"
SIGNATURE = "signature"
RESOURCES = "resources"
NON_ROOT = "runAsNonRoot"
RUN_AS_USER = "runAsUser"
NO_ESCALATION = "allowPrivilegeEscalation"
DROP_ALL = "drop"
CAPS_ADD = "add"
NOT_PRIVILEGED = "privileged"
READONLY_ROOT = "readOnlyRootFilesystem"
SECCOMP = "seccomp"
NO_HOST_PATH = "hostPath"
NO_HOST_NAMESPACES = "hostNamespaces"
LABELS = "labels"
ANNOTATIONS = "annotations"

# Demands answered by a pod's security posture alone: True it complies, False
# it does not, None unknown.
TRAITS = (NON_ROOT, RUN_AS_USER, NO_ESCALATION, DROP_ALL, CAPS_ADD,
          NOT_PRIVILEGED, READONLY_ROOT, SECCOMP, NO_HOST_PATH,
          NO_HOST_NAMESPACES)

Finding = collections.namedtuple("Finding", "demand params")

# Pod Security Standards levels, for Kyverno's podSecurity rules.
PSS = {
    "baseline": (NOT_PRIVILEGED, NO_HOST_PATH, NO_HOST_NAMESPACES, CAPS_ADD),
    "restricted": (NOT_PRIVILEGED, NO_HOST_PATH, NO_HOST_NAMESPACES, CAPS_ADD,
                   NO_ESCALATION, DROP_ALL, NON_ROOT, SECCOMP),
}

# Words in a rule's body (pattern keys, deny conditions, CEL) naming a demand.
KEYWORDS = (
    (NON_ROOT, ("runAsNonRoot",)),
    (RUN_AS_USER, ("runAsUser",)),
    (NO_ESCALATION, ("allowPrivilegeEscalation",)),
    (NOT_PRIVILEGED, ("privileged",)),
    (READONLY_ROOT, ("readOnlyRootFilesystem",)),
    (SECCOMP, ("seccompProfile",)),
    (NO_HOST_PATH, ("hostPath",)),
    (NO_HOST_NAMESPACES, ("hostNetwork", "hostPID", "hostIPC", "hostPort")),
)

# Gatekeeper library constraint kinds, by what they demand.
_GK_TRAITS = {
    "K8sPSPPrivilegedContainer": NOT_PRIVILEGED,
    "K8sPSPAllowPrivilegeEscalationContainer": NO_ESCALATION,
    "K8sPSPReadOnlyRootFilesystem": READONLY_ROOT,
    "K8sPSPHostFilesystem": NO_HOST_PATH,
    "K8sPSPHostNamespace": NO_HOST_NAMESPACES,
    "K8sPSPHostNetworkingPorts": NO_HOST_NAMESPACES,
    "K8sPSPSeccomp": SECCOMP,
}

POD_KINDS = {"Pod", "Deployment", "ReplicaSet", "Job", "CronJob",
             "StatefulSet", "DaemonSet", "ReplicationController"}
# Kinds the bundle applies or crane creates besides pods; a policy on these
# is named for review rather than ignored.
OTHER_KINDS = {"Service", "Ingress", "ConfigMap", "Secret", "ServiceAccount",
               "Role", "RoleBinding"}
POD_RESOURCES = {"pods", "deployments", "replicasets", "jobs", "cronjobs",
                 "statefulsets", "daemonsets", "replicationcontrollers"}
OTHER_RESOURCES = {"services", "ingresses", "configmaps", "secrets",
                   "serviceaccounts", "roles", "rolebindings"}

# Validating webhooks of other policy engines, by a word in their name.
WEBHOOK_ENGINES = (
    ("kubewarden", "Kubewarden"), ("polaris", "Polaris"),
    ("datree", "Datree"), ("jspolicy", "jsPolicy"), ("k-rail", "k-rail"),
    ("neuvector", "NeuVector"), ("stackrox", "Red Hat Advanced Cluster "
                                              "Security"),
    ("twistlock", "Prisma Cloud"), ("prisma", "Prisma Cloud"),
    ("sysdig", "Sysdig"), ("aqua", "Aqua"), ("sigstore", "Sigstore policy-"
                                                         "controller"),
    ("connaisseur", "Connaisseur"), ("ratify", "Ratify"),
)
# Webhooks the engines above own; their policies are judged by name instead.
OWN_WEBHOOKS = ("kyverno", "gatekeeper")


# -- tri-state logic: True, False, or None for "cannot be decided" ------------

def _all(values):
    values = list(values)
    if False in values:
        return False
    return None if None in values else True


def _any(values):
    values = list(values)
    if True in values:
        return True
    return None if None in values else False


def _not(value):
    return None if value is None else not value


# -- the target namespace -----------------------------------------------------

def namespace_labels(namespace_obj, name):
    """The namespace's labels, with the one Kubernetes sets on every
    namespace; None where unknown (unread, or not created yet)."""
    if not namespace_obj:
        return None
    labels = dict((namespace_obj.get("metadata") or {}).get("labels") or {})
    labels.setdefault("kubernetes.io/metadata.name", name)
    return labels


def selector_matches(selector, labels):
    """A label selector against labels. An empty selector matches all."""
    if not selector:
        return True
    if labels is None:
        return None
    for key, value in (selector.get("matchLabels") or {}).items():
        if labels.get(key) != value:
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op = expr.get("key"), expr.get("operator")
        values = expr.get("values") or []
        if ((op == "In" and labels.get(key) not in values)
                or (op == "NotIn" and labels.get(key, object()) in values)
                or (op == "Exists" and key not in labels)
                or (op == "DoesNotExist" and key in labels)):
            return False
    return True


def _glob(patterns, name):
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


# -- the pods a bundle makes --------------------------------------------------

Pod = collections.namedtuple(
    "Pod", "role who severity images traits labels annotations resources")

CRANE_LABELS = frozenset({"role", "harbor_id", "ship_id"})

# Engines carry no runAsNonRoot, and no agent variable sets it. Measured
# 2026-09-29 on kind v1.36 with a server-side dry run of the engine spec.
ENGINE_NO_RUN_AS_NON_ROOT = (
    "restrict_engines gives them crane's non-root UID and drops every "
    "capability, but sets no runAsNonRoot, and no agent variable sets it. "
    "Measured on Kubernetes v1.36: restricted PodSecurity refuses exactly "
    "this engine spec, accepts it with runAsNonRoot true added, and baseline "
    "accepts it as it is")


def run_as_non_root_fix(namespace, exception=None):
    """What makes the engines acceptable to a runAsNonRoot demand: a looser
    PodSecurity label (`exception` None), else the policy engine's exception;
    either way, a mutating policy that adds the field."""
    first = (f"Label namespace {namespace} "
             f"pod-security.kubernetes.io/enforce=baseline, which the engines "
             f"meet, and keep warn and audit at restricted to still see "
             f"violations" if exception is None else
             f"Ask the platform team for {exception} for namespace {namespace}")
    return (f"{first}. Or add a mutating policy, such as a Kyverno mutate "
            f"rule, that sets runAsNonRoot true on the pods crane creates in "
            f"namespace {namespace}. Or exempt the namespace from the policy")

# Why a pod falls short, per (role, demand); (role, None) is the role's default.
_WHY = {
    ("engine", NON_ROOT): ENGINE_NO_RUN_AS_NON_ROOT,
    ("engine", READONLY_ROOT): "nothing sets readOnlyRootFilesystem, and an "
                               "engine writes to its own filesystem during a "
                               "run",
    ("engine-open", None): "restrict_engines is off, so they keep crane's "
                           "privileged default",
    ("crane", READONLY_ROOT): "the bundle sets no readOnlyRootFilesystem, and "
                              "crane writes to its own filesystem",
    ("test-job", DROP_ALL): "crane creates them without the capability drop",
    ("test-job", None): "crane creates them without the restrict_engines "
                        "posture, and this field was not observed set on one",
}


def _why(pod, demand):
    role = pod.role
    if role == "engine" and pod.traits[NOT_PRIVILEGED] is False:
        role = "engine-open"
    return (_WHY.get((role, demand)) or _WHY.get((role, None))
            or "this was not observed on one")


def _image_refs(facts, opts):
    """(crane, engines) image references; None where the facts name none."""
    registry = opts.get("private_registry")
    crane = (crane_image(facts, {"private_registry": registry})
             if facts.get("crane_image") else None)
    engines = None
    if facts.get("images") is not None:
        engines = [composed_image_ref(i["repo"], i["tag"],
                                      registry or PUBLIC_REGISTRY)
                   for i in select_images(facts)]
    return crane, engines


def _limitrange_defaults(limitranges):
    """(requests, limits) a LimitRange's Container defaults give a pod that
    declares none: True, False, or None where they could not be read."""
    if limitranges is None:
        return None, None
    requests = limits = False
    for lr in limitranges:
        for item in (lr.get("spec") or {}).get("limits") or []:
            if item.get("type") != "Container":
                continue
            default = item.get("default") or {}
            given = item.get("defaultRequest") or {}
            if "cpu" in default and "memory" in default:
                limits = True
                requests = True          # a request defaults to its limit
            if "cpu" in given and "memory" in given:
                requests = True
    return requests, limits


def bundle_pods(facts, opts, limitranges):
    """The pods this bundle and its crane create, as far as each is known.

    Crane's pod is the bundle's own. Engines carry the restrict_engines
    posture, read off live runs (docs/hardened-engines.md). test-job pods
    run the crane image without that posture or resources."""
    crane_ref, engine_refs = _image_refs(facts, opts)
    restricted = opts.get("restrict_engines", True)
    cpu, mem = engine_size(opts)
    req_cpu, req_mem = engine_requests(facts, engine_request_quantities(opts))
    lr_requests, lr_limits = _limitrange_defaults(limitranges)
    safe = dict.fromkeys(TRAITS, True)
    crane = Pod("crane", "crane's own pod", FAIL,
                None if crane_ref is None else [crane_ref],
                {**safe, READONLY_ROOT: False}, CRANE_LABELS, frozenset(),
                {"requests": True, "limits": True,
                 "limit": (parse_cpu(CRANE_CPU_LIMIT),
                           parse_memory(CRANE_MEM_LIMIT)),
                 "request": (parse_cpu(CRANE_CPU_REQUEST),
                             parse_memory(CRANE_MEM_REQUEST))})
    if restricted:
        engine_traits = {**safe, NON_ROOT: False, READONLY_ROOT: False}
    else:
        engine_traits = {**dict.fromkeys(TRAITS, False), CAPS_ADD: None,
                         SECCOMP: None, NO_HOST_PATH: True,
                         NO_HOST_NAMESPACES: True}
    engine = Pod("engine", "the engine pods crane starts for each run", FAIL,
                 engine_refs, engine_traits, None, None,
                 {"requests": True, "limits": True, "limit": (cpu, mem),
                  "request": (parse_cpu(req_cpu), parse_memory(req_mem))})
    # They run crane's image, so an image demand is judged on crane's pod.
    test_job = Pod("test-job", "crane's short-lived test-job pods", WARN, [],
                   {**dict.fromkeys(TRAITS, None), DROP_ALL: False,
                    CAPS_ADD: True, NOT_PRIVILEGED: True, NO_HOST_PATH: True,
                    NO_HOST_NAMESPACES: True},
                   None, None,
                   {"requests": lr_requests, "limits": lr_limits,
                    "limit": None, "request": None})
    return [crane, engine, test_job]


def pod_security_refusals(level, facts, opts):
    """[(pod, [demand, ...])] for crane and the engines: what a Pod Security
    Standards level refuses among the pods the bundle makes."""
    out = []
    for pod in bundle_pods(facts, opts, None):
        if pod.role not in ("crane", "engine"):
            continue
        refused = [d for d in PSS.get(level, ()) if pod.traits[d] is False]
        if refused:
            out.append((pod, refused))
    return out


def refusal_text(refusals):
    """The pods a level refuses and why, as one phrase."""
    parts = []
    for pod, demands in refusals:
        whys = list(dict.fromkeys(_why(pod, d) for d in demands))
        wants = ", ".join(_requirement(Finding(d, {})) for d in demands)
        parts.append(f"{pod.who}, which do not meet these demands: {wants} "
                     f"({'; '.join(whys)})")
    return "; ".join(parts)


# -- judging one demand -------------------------------------------------------

def _tag(ref):
    """An image reference's tag; '' for none, None for a digest."""
    if "@" in ref:
        return None
    name = ref.rsplit("/", 1)[-1]
    return name.split(":", 1)[1] if ":" in name else ""


def _image_outcome(finding, refs):
    """(complies, the refs that do not) for one pod's images."""
    demand, p = finding
    if demand == SIGNATURE:
        return None, []
    if demand == DIGEST:
        bad = [r for r in refs if "@sha256:" not in r]
    elif demand == LATEST:
        bad = [r for r in refs if _tag(r) in p["tags"]]
    elif p.get("allow") is None and p.get("deny") is None:
        return None, []
    elif p.get("allow") is not None:
        bad = [r for r in refs if not _glob(p["allow"], r)]
    else:
        bad = [r for r in refs if _glob(p["deny"], r)]
    return not bad, bad


def _resource_outcome(finding, pod):
    """(complies, why) for a resources demand on one pod."""
    p, r = finding.params, pod.resources
    wanted = [k for k in ("requests", "limits") if p.get(k)]
    have = _all(r[k] for k in wanted)
    if have is False:
        if pod.role == "test-job":
            return False, ("they declare no requests or limits, and no "
                           "LimitRange in the namespace gives them defaults")
        return False, "declares none"
    if have is None:
        return None, ("they declare no requests or limits, and the "
                      "namespace's LimitRanges could not be read to see "
                      "whether defaults fill them in")
    for key, field in (("max_limits", "limit"), ("max_requests", "request")):
        ceiling = p.get(key) or {}
        if not ceiling or r[field] is None:
            continue
        cpu, mem = r[field]
        over = []
        if ceiling.get("cpu") and cpu > parse_cpu(str(ceiling["cpu"])):
            over.append(f"{format_cpu(cpu)} CPU above {ceiling['cpu']}")
        if ceiling.get("memory") and mem > parse_memory(str(ceiling["memory"])):
            over.append(f"{format_memory(mem)} memory above "
                        f"{ceiling['memory']}")
        if over:
            return False, f"its {field} is {' and '.join(over)}"
    return True, ""


def _outcomes(finding, pods):
    """[(pod, complies, why)] for one demand."""
    out = []
    for pod in pods:
        demand = finding.demand
        if demand in (LATEST, DIGEST, REGISTRY, SIGNATURE):
            if pod.images is None:
                out.append((pod, None, "the facts name no images for them, "
                                       "so which they pull is unknown"))
                continue
            ok, bad = _image_outcome(finding, pod.images)
            why = (("image " if len(bad) == 1 else "images ")
                   + ", ".join(bad) if bad else
                   "whether BlazeMeter signs its images was not checked"
                   if demand == SIGNATURE else
                   "the policy's image list could not be read off the rule")
            out.append((pod, ok, "" if ok else why))
        elif demand == RESOURCES:
            out.append((pod, *_resource_outcome(finding, pod)))
        elif demand in (LABELS, ANNOTATIONS):
            keys = finding.params.get("keys")
            have = pod.labels if demand == LABELS else pod.annotations
            if keys is None:
                out.append((pod, None, f"which {demand} the policy wants "
                                       f"could not be read off the rule"))
            elif have is None:
                out.append((pod, None, f"crane chooses the {demand} on the "
                                       f"pods it creates, and nothing in "
                                       f"the bundle can add "
                                       f"{', '.join(sorted(keys))}"))
            else:
                missing = sorted(set(keys) - have)
                out.append((pod, not missing,
                            f"no {', '.join(missing)}"
                            if missing else ""))
        else:
            ok = pod.traits[demand]
            out.append((pod, ok, "" if ok else _why(pod, demand)))
    return out


def _requirement(finding):
    """What a demand asks for, as a phrase."""
    demand, p = finding
    if demand == LATEST:
        tags = sorted(p["tags"])
        if tags == [""]:
            return "a tag on every image"
        return ("no image tagged " + " or ".join(t or "(none)" for t in tags))
    if demand == REGISTRY:
        if p.get("allow") is not None:
            return "images only from " + ", ".join(p["allow"])
        if p.get("deny") is not None:
            return "no image from " + ", ".join(p["deny"])
        return "images only from an allowed registry"
    if demand == RESOURCES:
        wanted = [k for k in ("requests", "limits") if p.get(k)]
        text = " and ".join(wanted) + " on every container"
        for key, field in (("max_limits", "limits"),
                           ("max_requests", "requests")):
            ceiling = {k: v for k, v in (p.get(key) or {}).items() if v}
            if ceiling:
                text += (f", {field} at most "
                         + " ".join(f"{v} {k}" for k, v in ceiling.items()))
        return text
    if demand in (LABELS, ANNOTATIONS):
        keys = p.get("keys")
        return (f"the {demand} {', '.join(sorted(keys))}" if keys
                else f"certain {demand}")
    return {
        DIGEST: "every image named by digest",
        SIGNATURE: "signed images",
        NON_ROOT: "runAsNonRoot true",
        RUN_AS_USER: "a non-root user",
        NO_ESCALATION: "allowPrivilegeEscalation false",
        DROP_ALL: "every capability dropped",
        CAPS_ADD: "no added capability",
        NOT_PRIVILEGED: "no privileged container",
        READONLY_ROOT: "a read-only root filesystem",
        SECCOMP: "a RuntimeDefault or Localhost seccomp profile",
        NO_HOST_PATH: "no hostPath volume",
        NO_HOST_NAMESPACES: "no host network, PID or IPC namespace and no "
                            "host port",
    }[demand]


def _fix(finding, failing, engine, namespace):
    """What to do about one demand, given the pods that fall short."""
    exception = f"ask the platform team for {EXCEPTION[engine]} for namespace " \
                f"{namespace}"
    roles = {pod.role for pod, _, _ in failing}
    demand = finding.demand
    if demand == LATEST:
        return ("Regenerate the bundle from the account, whose image list "
                "pins each image to a release (bzm-opl-gen images lists "
                "them). For an image published only as latest, "
                f"{exception}")
    if demand == REGISTRY:
        return ("Set private_registry to a registry the policy allows, and "
                "copy the images into it with the bundle's mirror script")
    if demand == RESOURCES:
        if roles == {"test-job"}:
            return ("Add a LimitRange of your own whose defaults give those "
                    "pods requests and limits, sized for them and not for an "
                    f"engine, or {exception}")
        if "engine" in roles:
            return ("Lower engine_cpu_limit and engine_mem_limit to fit, or "
                    + exception)
        return exception[0].upper() + exception[1:]
    if demand in TRAITS and "engine" in roles and not any(
            pod.traits[NOT_PRIVILEGED] for pod, _, _ in failing
            if pod.role == "engine"):
        return "Turn restrict_engines back on" + (
            ". " + run_as_non_root_fix(namespace, EXCEPTION[engine])
            if demand == NON_ROOT else "")
    if demand == NON_ROOT and "engine" in roles:
        return run_as_non_root_fix(namespace, EXCEPTION[engine])
    if demand == DIGEST:
        return ("No option names an image by digest, and crane composes "
                "engine image names from registry, path and tag. "
                + exception[0].upper() + exception[1:])
    if demand == READONLY_ROOT:
        return ("No option sets readOnlyRootFilesystem, and crane and the "
                "engines write to their own filesystem while they run. "
                + exception[0].upper() + exception[1:])
    if demand in (LABELS, ANNOTATIONS):
        return (f"No option adds {demand} to the pods the bundle or crane "
                f"create. " + exception[0].upper() + exception[1:])
    if roles == {"test-job"}:
        return f"If a run stalls on them, {exception} for pods named test-job-*"
    return exception[0].upper() + exception[1:]


def _pods_text(entries):
    return "; ".join(f"{pod.who} ({why})" if why else pod.who
                     for pod, _, why in entries)


def judge(engine, title, rules, pods, namespace, description=None):
    """One Check for one enforcing policy. `rules` is [(rule name, scope,
    findings)], scope True where the policy matches the namespace and None
    where it may; findings [] for a rule this module does not recognise."""
    status, parts, fixes, unknown = PASS, [], [], []
    for rule, scope, findings in rules:
        if not findings:
            unknown.append(rule)
            continue
        for finding in findings:
            short = [(pod, ok, why) for pod, ok, why in _outcomes(finding, pods)
                     if ok is not True]
            if not short:
                continue
            refused = [s for s in short if s[1] is False and scope]
            maybe = [s for s in short if s not in refused]
            worst = FAIL if any(pod.severity == FAIL for pod, _, _ in refused) \
                else WARN
            status = _worse(status, worst)
            sentence = f"it demands {_requirement(finding)}"
            if refused:
                sentence += f", which refuses {_pods_text(refused)}"
            if maybe:
                sentence += (f"{' and' if refused else ','} may refuse "
                             f"{_pods_text(maybe)}")
            parts.append(sentence)
            fixes.append(_fix(finding, short, engine, namespace))
    if unknown:
        status = _worse(status, WARN)
        parts.append(f"it enforces {len(unknown)} rule(s) this preflight does "
                     f"not recognise ({', '.join(unknown)}). Review them "
                     f"against crane's pod, the engine pods and crane's "
                     f"test-job pods"
                     + (f". The policy describes itself as: {description}"
                        if description else ""))
    if status == PASS:
        demands = sorted({_requirement(f) for _, _, fs in rules for f in fs})
        return Check(title, PASS,
                     f"enforces {' and '.join(demands)} in namespace "
                     f"{namespace}; the pods this bundle and its crane create "
                     f"satisfy it")
    detail = ". ".join(p[0].upper() + p[1:] for p in parts) + "."
    detail = detail[0].lower() + detail[1:]
    if not all(scope for _, scope, _ in rules):
        detail += (" Whether it matches namespace " + namespace + " could not "
                   "be decided from what was read, so it may not apply.")
    fixes = list(dict.fromkeys(fixes))
    if fixes:
        detail += " " + ". ".join(fixes) + "."
    return Check(title, status, detail)


def _worse(a, b):
    order = {PASS: 0, WARN: 1, FAIL: 2}
    return a if order[a] >= order[b] else b


# -- Kyverno ------------------------------------------------------------------

_ANCHOR = re.compile(r"^[=X^<+]?\((.*)\)$")


def _strip(key):
    """A Kyverno pattern key without its anchor: `=(securityContext)`."""
    match = _ANCHOR.match(key)
    return match.group(1) if match else key


def _leaves(node, path=()):
    """(path, value) for every leaf of a pattern, anchors stripped."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, path + (_strip(key),))
    elif isinstance(node, list):
        for item in node:
            yield from _leaves(item, path)
    else:
        yield path, node


def _image_findings(value):
    """Findings from one Kyverno image pattern such as `!*:latest` or
    `eu.foo.io/* | bar.io/*`."""
    out, allow = [], []
    for alt in (a.strip() for a in str(value).split("|")):
        if alt.startswith("!"):
            negated = alt[1:]
            if negated.endswith(":latest"):
                out.append(Finding(LATEST, {"tags": {"latest"}}))
            else:
                out.append(Finding(REGISTRY, {"deny": [negated]}))
        elif alt in ("*:*", "*:?*"):
            out.append(Finding(LATEST, {"tags": {""}}))
        elif "@" in alt:
            out.append(Finding(DIGEST, {}))
        elif alt not in ("*", "?*", ""):
            allow.append(alt)
    if allow:
        out.append(Finding(REGISTRY, {"allow": allow}))
    return out


def _keys_under(patterns, section):
    """Keys a pattern demands under metadata.<section>; None if none found."""
    keys = set()
    for path, _ in _leaves(patterns):
        if len(path) >= 3 and path[-3:-1] == ("metadata", section):
            keys.add(path[-1])
    return keys or None


def _text_findings(text, found):
    """Demands named in a rule's body text, where its shape named none."""
    out = [Finding(demand, {}) for demand, words in KEYWORDS
           if demand not in found and any(w in text for w in words)]
    if "capabilities" in text and not {DROP_ALL, CAPS_ADD} & found:
        out.append(Finding(DROP_ALL if "drop" in text.lower() else CAPS_ADD,
                           {}))
    if "resources" in text and RESOURCES not in found and (
            "requests" in text or "limits" in text):
        out.append(Finding(RESOURCES, {"requests": "requests" in text,
                                       "limits": "limits" in text}))
    if REGISTRY not in found and "registry" in text:
        out.append(Finding(REGISTRY, {}))
    if LATEST not in found and "latest" in text:
        out.append(Finding(LATEST, {"tags": {"latest"}}))
    if DIGEST not in found and ("digest" in text or "sha256" in text):
        out.append(Finding(DIGEST, {}))
    return out


def _name_findings(name):
    """Demands a rule or policy name states, for a body nothing else named."""
    name = name.lower()
    for words, finding in (
            (("latest",), Finding(LATEST, {"tags": {"latest"}})),
            (("digest",), Finding(DIGEST, {})),
            (("registr", "allowed-repos"), Finding(REGISTRY, {})),
            (("nonroot", "non-root"), Finding(NON_ROOT, {})),
            (("ro-rootfs", "readonly", "read-only"),
             Finding(READONLY_ROOT, {}))):
        if any(w in name for w in words):
            return [finding]
    return []


def kyverno_findings(rule, policy_name=""):
    """What one Kyverno rule demands of a pod; [] where unrecognised."""
    if rule.get("verifyImages"):
        return [Finding(SIGNATURE, {})]
    validate = rule.get("validate") or {}
    level = (validate.get("podSecurity") or {}).get("level")
    if level in PSS:
        return [Finding(d, {}) for d in PSS[level]]
    patterns = ([validate["pattern"]] if validate.get("pattern") else []) + \
        list(validate.get("anyPattern") or [])
    findings = []
    for path, value in _leaves(patterns):
        if path and path[-1] == "image":
            findings += [f for f in _image_findings(value)
                         if f not in findings]
    resource_paths = [p for p, _ in _leaves(patterns) if "resources" in p]
    if resource_paths:
        findings.append(Finding(RESOURCES, {
            "requests": any("requests" in p for p in resource_paths),
            "limits": any("limits" in p for p in resource_paths)}))
    for section, demand in (("labels", LABELS), ("annotations", ANNOTATIONS)):
        keys = _keys_under(patterns, section)
        if keys:
            findings.append(Finding(demand, {"keys": keys}))
    body = {k: v for k, v in validate.items()
            if k not in ("message", "failureAction", "failureActionOverrides",
                         "allowExistingViolations")}
    text = json.dumps(body)
    findings += _text_findings(text, {f.demand for f in findings})
    if not findings:
        findings = (_name_findings(rule.get("name") or "")
                    or _name_findings(policy_name))
    return findings


def _kind_hits(kinds, wanted):
    """Does a Kyverno kinds list name one of `wanted` (not a subresource)?"""
    for kind in kinds:
        if kind == "*":
            return True
        parts = kind.split("/")
        named = [i for i, p in enumerate(parts) if p[:1].isupper() or p == "*"]
        if named and (parts[named[-1]] in wanted or parts[named[-1]] == "*") \
                and named[-1] == len(parts) - 1:
            return True
    return False


def _kyverno_filter(block, namespace, labels, wanted):
    """Does one resource filter match `wanted` kinds in the namespace?"""
    res = block.get("resources") or {}
    if res.get("kinds") and not _kind_hits(res["kinds"], wanted):
        return False
    results = []
    if res.get("namespaces"):
        results.append(_glob(res["namespaces"], namespace))
    if res.get("namespaceSelector"):
        results.append(selector_matches(res["namespaceSelector"], labels))
    if (res.get("selector") or res.get("names") or res.get("name")
            or res.get("operations") or block.get("subjects")
            or block.get("roles") or block.get("clusterRoles")):
        results.append(None)
    return _all(results)


def _kyverno_blocks(clause):
    """A match or exclude clause as (combine, [filters])."""
    if not clause:
        return None, []
    if clause.get("any"):
        return _any, clause["any"]
    if clause.get("all"):
        return _all, clause["all"]
    return _all, [clause]


def kyverno_scope(rule, namespace, labels, wanted=POD_KINDS):
    """Does a rule reach `wanted` kinds in the namespace? True, False, None."""
    combine, blocks = _kyverno_blocks(rule.get("match"))
    if not blocks:
        return False
    matched = combine(_kyverno_filter(b, namespace, labels, wanted)
                      for b in blocks)
    ex_combine, ex_blocks = _kyverno_blocks(rule.get("exclude"))
    if not ex_blocks:
        return matched
    excluded = ex_combine(_kyverno_filter(b, namespace, labels, wanted)
                          for b in ex_blocks)
    return _all([matched, _not(excluded)])


def _kyverno_enforces(spec, rule, namespace, labels):
    """Does a rule enforce (reject) here? True, False, None."""
    validate = rule.get("validate") or {}
    verify = (rule.get("verifyImages") or [{}])[0] if rule.get(
        "verifyImages") else {}
    action = (validate.get("failureAction") or verify.get("failureAction")
              or spec.get("validationFailureAction") or "Audit")
    overrides = (validate.get("failureActionOverrides")
                 or spec.get("validationFailureActionOverrides") or [])
    enforces = str(action).lower() == "enforce"
    for override in overrides:
        hits = []
        if override.get("namespaces"):
            hits.append(_glob(override["namespaces"], namespace))
        if override.get("namespaceSelector"):
            hits.append(selector_matches(override["namespaceSelector"],
                                         labels))
        hit = _all(hits) if hits else False
        overridden = str(override.get("action", "")).lower() == "enforce"
        if hit is True:
            return overridden
        if hit is None and overridden != enforces:
            return None
    return enforces


def kyverno_rules(policy, namespace, labels):
    """(enforcing rules that may reach the bundle's objects, audit-only
    count, out-of-scope count) for one ClusterPolicy or Policy."""
    spec = policy.get("spec") or {}
    name = (policy.get("metadata") or {}).get("name") or "?"
    rules, audit, elsewhere = [], 0, 0
    for rule in spec.get("rules") or []:
        if not (rule.get("validate") or rule.get("verifyImages")):
            continue
        rule_name = rule.get("name") or "?"
        on_pods = kyverno_scope(rule, namespace, labels)
        on_other = kyverno_scope(rule, namespace, labels, OTHER_KINDS)
        if on_pods is False and on_other is False:
            elsewhere += 1
            continue
        enforces = _kyverno_enforces(spec, rule, namespace, labels)
        if enforces is False:
            audit += 1
            continue
        scope = _all([_any([on_pods, on_other]), enforces])
        findings = kyverno_findings(rule, name) if on_pods is not False else []
        rules.append((rule_name, scope, findings))
    return rules, audit, elsewhere


# -- Gatekeeper ---------------------------------------------------------------

def gatekeeper_findings(constraint):
    """What a Gatekeeper library constraint demands; [] where unrecognised."""
    kind = constraint.get("kind") or ""
    params = (constraint.get("spec") or {}).get("parameters") or {}
    if kind in _GK_TRAITS:
        return [Finding(_GK_TRAITS[kind], {})]
    if kind == "K8sAllowedRepos":
        return [Finding(REGISTRY, {"allow": [f"{r}*" for r in
                                             params.get("repos") or []]})]
    if kind == "K8sDisallowedRepos":
        return [Finding(REGISTRY, {"deny": [f"{r}*" for r in
                                            params.get("repos") or []]})]
    if kind == "K8sDisallowedTags":
        return [Finding(LATEST, {"tags": set(params.get("tags") or [])})]
    if kind == "K8sImageDigests":
        return [Finding(DIGEST, {})]
    if kind == "K8sRequiredResources":
        both = not params.get("requests") and not params.get("limits")
        return [Finding(RESOURCES, {"requests": both or bool(params.get("requests")),
                                    "limits": both or bool(params.get("limits"))})]
    if kind == "K8sContainerLimits":
        return [Finding(RESOURCES, {"limits": True, "max_limits": {
            "cpu": params.get("cpu"), "memory": params.get("memory")}})]
    if kind == "K8sContainerRequests":
        return [Finding(RESOURCES, {"requests": True, "max_requests": {
            "cpu": params.get("cpu"), "memory": params.get("memory")}})]
    if kind == "K8sPSPCapabilities":
        drop = [str(c).upper() for c in
                params.get("requiredDropCapabilities") or []]
        return [Finding(DROP_ALL if "ALL" in drop else CAPS_ADD, {})]
    if kind == "K8sPSPAllowedUsers":
        rule = ((params.get("runAsUser") or {}).get("rule"))
        return [Finding(RUN_AS_USER, {})] if rule == "MustRunAsNonRoot" else []
    if kind == "K8sRequiredLabels":
        return [Finding(LABELS, {"keys": {l.get("key") for l in
                                          params.get("labels") or []} or None})]
    if kind == "K8sRequiredAnnotations":
        return [Finding(ANNOTATIONS, {"keys": {a.get("key") for a in
                                               params.get("annotations") or []}
                                      or None})]
    return []


def _gatekeeper_kinds_hit(match, wanted):
    kinds = match.get("kinds")
    if not kinds:
        return True
    return any(k == "*" or k in wanted
               for entry in kinds for k in entry.get("kinds") or ["*"])


def gatekeeper_scope(constraint, namespace, labels):
    """(reaches pods, reaches other bundle kinds) in the namespace."""
    match = (constraint.get("spec") or {}).get("match") or {}
    if match.get("scope") == "Cluster":
        return False, False
    results = []
    if match.get("namespaces"):
        results.append(_glob(match["namespaces"], namespace))
    if match.get("excludedNamespaces"):
        results.append(not _glob(match["excludedNamespaces"], namespace))
    if match.get("namespaceSelector"):
        results.append(selector_matches(match["namespaceSelector"], labels))
    if match.get("labelSelector") or match.get("name"):
        results.append(None)
    here = _all(results)
    return (_all([here, _gatekeeper_kinds_hit(match, POD_KINDS)]),
            _all([here, _gatekeeper_kinds_hit(match, OTHER_KINDS)]))


def gatekeeper_enforces(constraint):
    spec = constraint.get("spec") or {}
    action = spec.get("enforcementAction") or "deny"
    if action == "scoped":
        return any(a.get("action") == "deny"
                   for a in spec.get("scopedEnforcementActions") or [])
    return action == "deny"


# -- ValidatingAdmissionPolicy ------------------------------------------------

def _vap_resources_hit(match, wanted):
    """Do these resource rules reach `wanted` resources on CREATE?"""
    for rule in (match or {}).get("resourceRules") or []:
        ops = rule.get("operations") or []
        resources = rule.get("resources") or []
        if ("CREATE" in ops or "*" in ops) and (
                "*" in resources or set(resources) & wanted):
            return True
    return False


def vap_findings(policy):
    """Demands read off a policy's CEL expressions and name."""
    spec = policy.get("spec") or {}
    text = " ".join(str(v.get("expression") or "")
                    for v in spec.get("validations") or [])
    findings = []
    prefixes = re.findall(r"(!?)[\w.]*image\.startsWith\(['\"]([^'\"]+)['\"]\)",
                          text)
    if prefixes:
        allow = [f"{p}*" for neg, p in prefixes if not neg]
        deny = [f"{p}*" for neg, p in prefixes if neg]
        findings.append(Finding(REGISTRY, {"allow": allow} if allow
                                else {"deny": deny}))
    findings += _text_findings(text, {f.demand for f in findings})
    for demand, word in ((LABELS, "labels"), (ANNOTATIONS, "annotations")):
        if f"metadata.{word}" in text:
            findings.append(Finding(demand, {"keys": None}))
    if spec.get("paramKind"):
        # Values come from a parameter object; the demand is named, not judged.
        findings = [Finding(f.demand, {**f.params, "allow": None, "deny": None,
                                       "keys": None})
                    if f.demand in (REGISTRY, LABELS, ANNOTATIONS) else f
                    for f in findings]
    return findings or _name_findings((policy.get("metadata") or {})
                                      .get("name") or "")


def vap_rules(policies, bindings, namespace, labels):
    """[(policy, [(binding name, scope)])] for Deny bindings that may reach
    the bundle's objects, and the counts of the rest."""
    by_name = {(p.get("metadata") or {}).get("name"): p for p in policies}
    out, other, elsewhere = collections.OrderedDict(), 0, 0
    for binding in bindings:
        spec = binding.get("spec") or {}
        policy = by_name.get(spec.get("policyName"))
        if policy is None:
            continue
        if "Deny" not in (spec.get("validationActions") or []):
            other += 1
            continue
        constraints = (policy.get("spec") or {}).get("matchConstraints") or {}
        resources = spec.get("matchResources") or {}
        reach = _vap_resources_hit(constraints, POD_RESOURCES | OTHER_RESOURCES)
        if resources.get("resourceRules"):
            reach = reach and _vap_resources_hit(
                resources, POD_RESOURCES | OTHER_RESOURCES)
        scope = _all([
            selector_matches(constraints.get("namespaceSelector"), labels),
            selector_matches(resources.get("namespaceSelector"), labels),
            None if (constraints.get("objectSelector")
                     or resources.get("objectSelector")
                     or (policy.get("spec") or {}).get("matchConditions"))
            else True])
        if not reach or scope is False:
            elsewhere += 1
            continue
        name = (binding.get("metadata") or {}).get("name") or "?"
        out.setdefault(spec["policyName"], []).append((name, scope))
    return out, other, elsewhere


# -- other webhooks -----------------------------------------------------------

def webhooks_on_pods(configurations, labels):
    """[(configuration name, engine or None, scope)] for validating webhooks
    outside Kyverno and Gatekeeper that inspect pod creation here."""
    out = []
    for config in configurations:
        name = (config.get("metadata") or {}).get("name") or "?"
        if any(word in name.lower() for word in OWN_WEBHOOKS):
            continue
        scopes = []
        for hook in config.get("webhooks") or []:
            if not any((set(r.get("apiGroups") or []) & {"", "*"})
                       and (set(r.get("resources") or []) & {"pods", "*"})
                       and (set(r.get("operations") or [])
                            & {"CREATE", "*"})
                       for r in hook.get("rules") or []):
                continue
            scopes.append(_all([
                selector_matches(hook.get("namespaceSelector"), labels),
                None if hook.get("objectSelector") else True]))
        scope = _any(scopes) if scopes else False
        if scope is False:
            continue
        engine = next((label for word, label in WEBHOOK_ENGINES
                       if word in name.lower()), None)
        out.append((name, engine, scope))
    return out


def served(section):
    """Is a section's resource installed? False for NOT_SERVED."""
    return section != evidence.NOT_SERVED


def target_namespace(opts):
    return opts.get("namespace") or DEFAULT_OPTIONS["namespace"]


def _name(obj):
    return (obj.get("metadata") or {}).get("name") or "?"


def _annotation(obj, key):
    return ((obj.get("metadata") or {}).get("annotations") or {}).get(key)


PLURAL = {"ClusterPolicy": "ClusterPolicies", "Policy": "Policies",
          "policy": "policies"}


def _count(n, word):
    return f"{n} {word if n == 1 else PLURAL.get(word, word + 's')}"


def _tally(present, read, enforcing, audit, elsewhere):
    return (f"{present}. Read {read}. Enforcing here, judged below: "
            f"{enforcing}. Audit or warn only: {audit}. Not reaching this "
            f"namespace or the bundle's objects: {elsewhere}")


# -- one list of Checks per engine --------------------------------------------

def kyverno_checks(cluster_policies, policies, namespace_obj, limitranges,
                   facts, opts):
    """Kyverno's summary and one Check per enforcing policy. `policies` are
    the namespaced ones in the target namespace; None where unread."""
    name = "policy: Kyverno"
    if not served(cluster_policies):
        return [Check(name, PASS, "Kyverno is not installed: the API server "
                                  "serves no kyverno.io ClusterPolicy")]
    ns = target_namespace(opts)
    labels = namespace_labels(namespace_obj, ns)
    pods = bundle_pods(facts, opts, limitranges)
    sources = [("ClusterPolicy", cluster_policies)]
    if policies is not None and served(policies):
        sources.append(("Policy", policies))
    judged, audit, elsewhere = [], 0, 0
    for kind, items in sources:
        for policy in items:
            rules, audits, _ = kyverno_rules(policy, ns, labels)
            if rules:
                description = (_annotation(policy, "policies.kyverno.io/description")
                               or _annotation(policy, "policies.kyverno.io/title"))
                judged.append(judge(KYVERNO, f"{name} {kind} {_name(policy)}",
                                    rules, pods, ns, description))
            elif audits:
                audit += 1
            else:
                elsewhere += 1
    read = _count(len(cluster_policies), "ClusterPolicy")
    if policies is not None and served(policies):
        read += f" and {_count(len(policies), 'Policy')} in namespace {ns}"
    summary = _tally("Kyverno is installed", read, len(judged), audit,
                     elsewhere)
    if policies is None:
        return [Check(name, WARN, summary + f". The Policies in namespace {ns} "
                                  f"could not be read, so any there are "
                                  f"unjudged")] + judged
    return [Check(name, PASS, summary)] + judged


def gatekeeper_checks(constraints, templates, namespace_obj, limitranges,
                      facts, opts):
    """Gatekeeper's summary and one Check per enforcing constraint."""
    name = "policy: Gatekeeper"
    if not served(constraints) and not served(templates):
        return [Check(name, PASS, "Gatekeeper is not installed: the API server "
                                  "serves no templates.gatekeeper.sh "
                                  "ConstraintTemplate")]
    if not served(constraints):
        # Constraint kinds exist only once a template defines one.
        constraints = []
    ns = target_namespace(opts)
    labels = namespace_labels(namespace_obj, ns)
    pods = bundle_pods(facts, opts, limitranges)
    descriptions = {}
    for template in templates if isinstance(templates, list) else []:
        descriptions[_name(template)] = _annotation(template, "description")
    judged, audit, elsewhere = [], 0, 0
    for constraint in constraints:
        kind = constraint.get("kind") or "?"
        on_pods, on_other = gatekeeper_scope(constraint, ns, labels)
        if on_pods is False and on_other is False:
            elsewhere += 1
        elif not gatekeeper_enforces(constraint):
            audit += 1
        else:
            findings = (gatekeeper_findings(constraint)
                        if on_pods is not False else [])
            rules = [(kind, _any([on_pods, on_other]), findings)]
            judged.append(judge(GATEKEEPER,
                                f"{name} {kind} {_name(constraint)}", rules,
                                pods, ns, descriptions.get(kind.lower())))
    templates_read = (_count(len(templates), "ConstraintTemplate") + " and "
                      if isinstance(templates, list) else "")
    return [Check(name, PASS, _tally(
        "Gatekeeper is installed",
        templates_read + _count(len(constraints), "constraint"),
        len(judged), audit, elsewhere))] + judged


def vap_checks(policies, bindings, namespace_obj, limitranges, facts, opts):
    """ValidatingAdmissionPolicy's summary and one Check per policy a Deny
    binding reaches the bundle's objects with."""
    name = f"policy: {VAP}"
    if not served(policies):
        return [Check(name, PASS, "this API server does not serve "
                                  "ValidatingAdmissionPolicy (Kubernetes "
                                  "before 1.30, with the beta API off)")]
    bindings = bindings if served(bindings) else []
    ns = target_namespace(opts)
    labels = namespace_labels(namespace_obj, ns)
    pods = bundle_pods(facts, opts, limitranges)
    by_name = {_name(p): p for p in policies}
    reached, other, elsewhere = vap_rules(policies, bindings, ns, labels)
    judged = []
    for policy_name, binds in reached.items():
        policy = by_name[policy_name]
        constraints = (policy.get("spec") or {}).get("matchConstraints") or {}
        findings = (vap_findings(policy)
                    if _vap_resources_hit(constraints, POD_RESOURCES) else [])
        scope = _any(scope for _, scope in binds)
        judged.append(judge(VAP, f"{name} {policy_name}",
                            [(policy_name, scope, findings)], pods, ns))
    return [Check(name, PASS, _tally(
        f"The API server serves {VAP}",
        f"{_count(len(policies), 'policy')} and "
        f"{_count(len(bindings), 'binding')}",
        len(judged), other, elsewhere))] + judged


def webhook_checks(configurations, namespace_obj, opts):
    """Validating webhooks outside Kyverno and Gatekeeper that inspect pod
    creation here: named, since what they enforce cannot be read."""
    name = "policy: other webhooks"
    ns = target_namespace(opts)
    found = webhooks_on_pods(configurations,
                             namespace_labels(namespace_obj, ns))
    if not found:
        return [Check(name, PASS, f"no validating webhook outside Kyverno and "
                                  f"Gatekeeper inspects pod creation in "
                                  f"namespace {ns}")]
    named = ", ".join(
        hook + (f" ({engine})" if engine else "")
        + ("" if scope else " (may not match this namespace)")
        for hook, engine, scope in found)
    return [Check(name, WARN, f"these validating webhooks inspect pod creation "
                              f"in namespace {ns} and may enforce policy this "
                              f"preflight cannot read: {named}. A webhook that "
                              f"refuses the engine pods does so after the agent "
                              f"reads online, so a run hangs. Ask the platform "
                              f"team what each one enforces on pods")]
