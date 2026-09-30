"""After deploying: read the agent's namespace and name the known failures in it.

Most failures after an apply show only in Kubernetes events, container statuses
and crane's log, while BlazeMeter shows a run stuck at BOOT_STARTING or an agent
offline. This reads those, matches every line against RULES, and names the
option or action that fixes each match. A warning no rule knows is listed as
found, grouped, never dropped.

gather() is the impure layer; evaluate() is a pure function over its result, so
every rule is testable offline. A section is None when it could not be read
(the reason is in `unread`) and []/"" when it was read and holds nothing.
"""

import collections
import datetime
import json
import re
import subprocess
import textwrap
import urllib.parse

from . import kube, service_virt
from .admission_policy import ENGINE_NO_RUN_AS_NON_ROOT, run_as_non_root_fix
from .verdict import FAIL, WARN

# Below WARN: something the report explains that needs no action now, such as
# a finding from a pod that no longer exists.
NOTE = "NOTE"
STATUS_RANK = {FAIL: 0, WARN: 1, NOTE: 2}

# kubectl retries an unreachable API server rather than failing; both bounds
# keep a dead context from hanging the command.
REQUEST_TIMEOUT = "20s"
READ_TIMEOUT_S = 60

DEFAULT_SINCE = "1h"
DEFAULT_LOG_LINES = 500
EVIDENCE_CHARS = 400

CRANE_LABEL = ("role", "role-crane")

# Crane by its label, then the names crane gives the pods it creates
# (engines r-v4-<id>-0-0-c-<suffix>, housekeeping test-job-<suffix>).
ENGINE_NAME = re.compile(r"^r-[a-z0-9]+-")
TEST_JOB_NAME = re.compile(r"^test-job-")
CRANE_HOOK_NAME = re.compile(r"^crane-hook")
CRANE_NAME = re.compile(r"^crane-[a-z0-9]+-[a-z0-9]{5}$")

# Waiting reasons every healthy start passes through.
BENIGN_WAITING = {"ContainerCreating", "PodInitializing"}

# A crane log line with no rule is listed only if it looks like an error;
# the rest of the log is ordinary progress.
ERRORISH = re.compile(r"\b(ERROR|CRITICAL|FATAL|Traceback)\b|\w(Error|Exception):")
TRACEBACK_HEADER = re.compile(r"^Traceback \(most recent call last\):?$")

# Crane logs its first call to BlazeMeter before making it. A crane whose log
# ends there after HANG_AFTER_S of running is hung: its readiness probe does
# not call BlazeMeter, so the pod still shows Ready.
STARTUP_CHECK = re.compile(r"Checking startup connectivity to URL")
HANG_AFTER_S = 60
HANG_TAIL = 200
PROXY_IN_LOG = re.compile(r"HTTPS Proxy: ([^\s\"',]+)")

# Crane 3.8 logs its AUTH_TOKEN at startup. The value is replaced as the log
# is read, as are credentials in a URL, so no report carries either.
SECRET_VALUE = re.compile(
    r"(AUTH_TOKEN[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.I)
URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@", re.I)

# A pod the ReplicaSet could not create because the ServiceAccount was not
# there yet: the files are applied in alphabetical order, Deployment first.
SA_LOOKUP = re.compile(r"error looking up service account [\w.\-]+/([\w.\-]+): "
                       r"serviceaccount \"[^\"]+\" not found")

# A probe that raced its container's exit, on a pod that has since finished.
PROBE_RACED_EXIT = re.compile(r"probe errored.*(CONTAINER_EXITED|container not "
                              r"found|container is not running)", re.I | re.S)

# kubectl's answer for the log of a container that has not started: there is
# no log yet, which is not a refused read.
NOT_STARTED = re.compile(r"is waiting to start")
NO_LOG_YET = "no log yet: the container has not started"


def redact(text):
    """`text` with every AUTH_TOKEN value and URL credential replaced."""
    if not text:
        return text
    return URL_CREDENTIALS.sub(r"\1<redacted>@",
                               SECRET_VALUE.sub(r"\1<redacted>", text))


# -- the rule table -------------------------------------------------------------

# One known failure. `sources` are where it can appear: event, pod (status and
# scheduling condition), container (waiting or terminated state), log (crane),
# namespace (what the reads themselves imply), startup (crane's log ending at
# its first call to BlazeMeter). Every regex in `patterns` must
# match "<reason>: <text>". Within one `family` a signal takes the first rule
# that matches; a `generic` rule folds into the finding of its family for the
# same subject. `options` are the bundle options the fix names.
Rule = collections.namedtuple(
    "Rule", "id status title sources patterns subject roles family generic "
            "finding fix options")


def _rule(id, status, title, sources, patterns, finding, fix, subject=None,
          roles=None, family=None, generic=False, options=()):
    if isinstance(patterns, str):
        patterns = (patterns,)
    return Rule(id, status, title, frozenset(sources.split()),
                tuple(re.compile(p, re.I | re.S) for p in patterns),
                subject, frozenset(roles.split()) if roles else None,
                family or id, generic, finding, fix, tuple(options))


def _first_group(pattern):
    """A subject reader: the regex's first group, or None."""
    rx = re.compile(pattern, re.I | re.S)

    def read(text, signal):
        m = rx.search(text)
        return m.group(1) if m else None
    return read


def _image(text, signal):
    return signal.subject


def _resources(text, signal):
    found = re.findall(r"Insufficient ([\w\-/]+(?:\.[\w\-/]+)*)", text)
    if re.search(r"Too many pods", text):
        found.append("pods")
    return ", ".join(sorted(set(found))) or None


def _taints(text, signal):
    found = re.findall(r"taint \{([^}]*)\}", text)
    return ", ".join(sorted(set(t.strip() for t in found))) or None


def _webhook(text, signal):
    m = re.search(r'admission webhook "([^"]+)"', text)
    if not m:
        return None
    # Gatekeeper puts the constraint in brackets; Kyverno puts the policy on
    # the line after "policies" (1.10+) or "violation:" (earlier).
    policy = (re.search(r"denied the request:\s*\[([^\]]+)\]", text)
              or re.search(r"(?:policies|violation:)\s*\n+\s*([\w.\-]+):", text))
    return m.group(1) + (f", policy {policy.group(1)}" if policy else "")


def _rbac(text, signal):
    m = re.search(r'User "([^"]+)" cannot (\w+) resource "([^"]+)"', text)
    return f"{m.group(1)} cannot {m.group(2)} {m.group(3)}" if m else None


def _reference(text, signal):
    m = re.search(r'(configmap|secret)s? "([^"]+)" not found', text)
    if m:
        return f"{m.group(1).lower()} {m.group(2)}"
    m = re.search(r"image pull secrets? \(([^)]+)\)", text)
    return f"pull secret {m.group(1)}" if m else None


def _evicted(text, signal):
    m = re.search(r"low on resource: ([\w\-]+)", text)
    if m:
        return m.group(1)
    return "ephemeral-storage" if re.search(r"ephemeral", text) else None


def _role(text, signal):
    return signal.role


def _given(text, signal):
    """The subject the signal already carries (a proxy, a service account)."""
    return signal.subject


PULL = (r"ErrImagePull|ImagePullBackOff|Failed to pull image|"
        r"Back-off pulling image|pull access denied")
CONNECT = (r"Connection refused|timed out|Name or service not known|"
           r"Temporary failure in name resolution|Network is unreachable|"
           r"No route to host|Max retries exceeded|Connection reset|"
           r"RemoteDisconnected|Failed to establish a new connection|"
           r"nodename nor servname")
PROXY_FAIL = (r"ProxyError|Cannot connect to proxy|Unable to connect to proxy|"
              r"Tunnel connection failed")
BZM_HOST = r"blazemeter\.com|/api/v4/"
CA_OPTIONS = ("ca_bundle", "ca_bundle_slot", "ca_cert_file",
              "ca_existing_configmap", "ca_openshift_inject")

RULES = (
    _rule("namespace-missing", FAIL, "the namespace does not exist",
          "namespace", r"^NamespaceNotFound:",
          "The namespace does not exist on the cluster this context points at, "
          "so nothing of the bundle runs there.",
          "Check the namespace you passed and the current kubectl context. If "
          "the bundle was never applied, create the namespace and apply it; "
          "the bundle README has both commands.", options=("namespace",)),
    _rule("crane-missing", FAIL, "no crane pod in the namespace",
          "namespace", r"^NoCranePod:",
          "No crane pod is in the namespace, so no agent runs here and "
          "BlazeMeter shows the agent offline.",
          "Apply the bundle to this namespace. If it was applied, the crane "
          "Deployment could not create its pod, and the findings and warnings "
          "in this report name why (quota, admission, service account); "
          "kubectl describe deploy/crane in the namespace shows the same."),

    # Image pulls. The node pulls, so node trust and node egress decide.
    _rule("image-pull-registry-tls", FAIL, "the node does not trust the registry",
          "event container",
          (PULL, r"x509|certificate signed by unknown authority|"
                 r"tls: failed to verify|certificate verify failed"),
          "The node could not verify the registry's TLS certificate while "
          "pulling this image.",
          "The node's container runtime must trust the registry's CA. That is "
          "node configuration (containerd registry hosts, or the cluster image "
          "configuration on OpenShift), not a bundle option: the CA trust "
          "options reach crane and its engines, never the image pull.",
          subject=_image, family="image-pull"),
    _rule("image-pull-auth", FAIL, "the registry refused the pull",
          "event container",
          (PULL, r"unauthorized|authentication required|no basic auth "
                 r"credentials|pull access denied|denied:|403 Forbidden|"
                 r"access to the resource is denied"),
          "The registry refused the pull as unauthorised. Docker Hub gives the "
          "same answer for a repository that does not exist.",
          "For the crane image, pull_secret names a docker-registry Secret in "
          "the namespace; create it with kubectl create secret "
          "docker-registry. Engine pods run as the namespace's default "
          "ServiceAccount and get no pull secret from crane, so add the same "
          "Secret to that ServiceAccount's imagePullSecrets; the bundle's "
          "README has the command.",
          subject=_image, family="image-pull",
          options=("pull_secret",)),
    _rule("image-not-found", FAIL, "the registry has no such image",
          "event container",
          (PULL, r"manifest unknown|not found|NotFound|name unknown"),
          "The registry has no image by this name and tag.",
          "With private_registry set, every image the location needs must be "
          "mirrored under that prefix: bzm-opl-gen images lists them, and "
          "bzm-opl-gen images --mirror copies them. Tags follow BlazeMeter "
          "releases, so a mirror made earlier goes stale when BlazeMeter "
          "ships a new version; mirror again. Without private_registry, check "
          "the image name in the bundle against the location's facts.",
          subject=_image, family="image-pull", options=("private_registry",)),
    _rule("image-pull-unreachable", FAIL, "the node cannot reach the registry",
          "event container",
          (PULL, r"dial tcp|i/o timeout|no such host|connection refused|"
                 r"context deadline exceeded|TLS handshake timeout|"
                 r"network is unreachable"),
          "The node could not reach the registry to pull this image.",
          "The nodes need egress to the registry, which is gcr.io for "
          "BlazeMeter's public images. Where they have none, mirror the images "
          "to a registry they can reach (bzm-opl-gen images --mirror) and set "
          "private_registry.",
          subject=_image, family="image-pull", options=("private_registry",)),
    _rule("image-pull", FAIL, "an image could not be pulled",
          "event container", (PULL + r"|InvalidImageName",),
          "An image could not be pulled. The evidence line gives the "
          "registry's own reason.",
          "bzm-opl-gen images lists what the location needs. private_registry, "
          "pull_secret and registry_auth decide where images come from and "
          "with which credentials. Tags follow BlazeMeter releases, so a "
          "mirror goes stale when BlazeMeter ships a new version.",
          subject=_image, family="image-pull", generic=True,
          options=("private_registry", "pull_secret", "registry_auth")),

    _rule("missing-reference", FAIL, "a ConfigMap or Secret is missing",
          "event container",
          r'(configmap|secret)s? "[^"]+" not found|'
          r"Unable to retrieve some image pull secrets",
          "A pod names a ConfigMap or Secret that is not in the namespace, so "
          "its container cannot start (or, for a pull secret, pulls without "
          "credentials).",
          "Create the object, or correct the option that names it. The CA "
          "ConfigMap comes from the bundle (ca_bundle, or the ca_cert_file "
          "file you supply) or from your platform team (ca_existing_configmap, "
          "ca_openshift_inject). The AUTH_TOKEN Secret comes from the bundle "
          "when use_secret is on. pull_secret and sv_tls_secret name Secrets "
          "you create yourself.",
          subject=_reference,
          options=("ca_bundle", "ca_cert_file", "ca_existing_configmap",
                   "ca_openshift_inject", "use_secret", "pull_secret",
                   "sv_tls_secret")),

    # Crane's own connection to BlazeMeter, read from its log.
    _rule("tls-trust", FAIL, "crane does not trust a certificate on the way out",
          "log",
          r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|"
          r"x509: certificate signed by unknown authority|"
          r"unable to get local issuer certificate|"
          r"self[- ]signed certificate in certificate chain|"
          r"SSLCertVerificationError",
          "Crane could not verify a TLS certificate on its way to BlazeMeter. "
          "Something in the path, usually a TLS-inspecting proxy or firewall, "
          "presents a certificate from a CA crane does not trust.",
          "Give the agent that CA with one CA trust option: ca_bundle (inline "
          "PEM), ca_bundle_slot with ca_cert_file (a file you supply), "
          "ca_existing_configmap (a ConfigMap your platform team owns), or "
          "ca_openshift_inject (the OpenShift cluster trust bundle). "
          "Regenerate and apply the bundle.",
          family="crane-connection", options=CA_OPTIONS),
    _rule("proxy-kube-api", FAIL, "Kubernetes API calls go through the proxy",
          "log",
          (PROXY_FAIL + r"|407",
           r"/api/v1/|/apis/|kubernetes\.default|:6443\b"),
          "Crane's calls to the Kubernetes API were sent to the proxy.",
          "The no_proxy key of the proxy option must cover the address crane "
          "reaches the API server at: kubernetes.default, and the API "
          "service's IP (kubectl get svc kubernetes -n default).",
          family="crane-connection", options=("proxy",)),
    _rule("proxy-auth", FAIL, "the proxy refused crane's credentials",
          "log", r"\b407\b|Proxy Authentication Required",
          "The proxy answered HTTP 407: it refused crane's credentials, or "
          "crane sent none.",
          "Set username and password on the proxy option; they are URL-encoded "
          "into the proxy URL. If they are set, check them with the proxy's "
          "owner. With use_secret on they live in the Secret, so apply it again "
          "after a change and restart crane.",
          family="crane-connection", options=("proxy", "use_secret")),
    _rule("proxy-unreachable", FAIL, "crane cannot connect through the proxy",
          "log", PROXY_FAIL,
          "Crane could not connect through the proxy.",
          "Check the http and https keys of the proxy option against the "
          "proxy's real address and port, and that pods in this namespace may "
          "reach it.",
          family="crane-connection", options=("proxy",)),
    _rule("egress-blocked", FAIL, "crane cannot reach BlazeMeter",
          "log", (CONNECT, BZM_HOST),
          "Crane could not reach BlazeMeter.",
          "Pods in this namespace need HTTPS egress to a.blazemeter.com (crane) "
          "and to data.blazemeter.com and storage.blazemeter.com (engines). "
          "Where the cluster must use a proxy, set the proxy option; where a "
          "NetworkPolicy or firewall filters egress, allow those hosts. "
          "bzm-opl-gen doctor probes the same hosts from the crane pod.",
          family="crane-connection", options=("proxy",)),
    _rule("crane-hung-proxy", FAIL, "crane cannot reach BlazeMeter: it hangs "
          "at its first call", "startup", (r"^CraneStartupHang:", r"HTTPS Proxy"),
          "Crane's first call to BlazeMeter has not returned, and its log names "
          "a proxy, the first suspect: nothing answers at that address, or the "
          "proxy refuses CONNECT to a.blazemeter.com. The pod still shows "
          "Ready, because its readiness probe does not call BlazeMeter, so "
          "kubectl get pods looks healthy while BlazeMeter shows the agent "
          "offline.",
          "Test from inside the namespace: run a throwaway pod with curl there "
          "and fetch https://a.blazemeter.com through the proxy. Check the http "
          "and https keys of the proxy option against the proxy's real address "
          "and port, and that its no_proxy key does not name a.blazemeter.com. "
          "Regenerate and apply the bundle, then restart crane.",
          subject=_given, family="crane-hung", options=("proxy",)),
    _rule("crane-hung", FAIL, "crane cannot reach BlazeMeter: it hangs at its "
          "first call", "startup", r"^CraneStartupHang:",
          "Crane's first call to BlazeMeter has not returned. Something between "
          "the pod and a.blazemeter.com drops the connection without refusing "
          "it: a firewall, a NetworkPolicy, or DNS. The pod still shows Ready, "
          "because its readiness probe does not call BlazeMeter, so kubectl get "
          "pods looks healthy while BlazeMeter shows the agent offline.",
          "Test from inside the namespace: run a throwaway pod with curl there "
          "and fetch https://a.blazemeter.com. Pods in this namespace need "
          "HTTPS egress to a.blazemeter.com; where the cluster must use a "
          "proxy, set the proxy option. bzm-opl-gen doctor probes the same "
          "hosts from the crane pod.",
          family="crane-hung", options=("proxy",)),
    _rule("auth-token", FAIL, "BlazeMeter refused the agent's token",
          "log",
          (r"\b(401|404) (Client Error|Unauthorized|Not Found)|"
           r"(status|code|HTTP/[\d.]+)[ =:]*(401|404)\b|Unauthori[sz]ed|"
           r"invalid token|token (is )?(invalid|expired|revoked)", BZM_HOST),
          "BlazeMeter refused crane's AUTH_TOKEN, or no longer knows this "
          "agent. A token is revoked when a new one is issued for the same "
          "agent; a deleted agent or location gives the same answer.",
          "Check in BlazeMeter that the agent still exists. If it does, apply "
          "the bundle's Secret (or ConfigMap, with use_secret off) holding the "
          "agent's current token, then restart crane. Issue a new token "
          "(generate --rotate-token) only when nothing else runs on this "
          "agent: issuing one revokes the previous token everywhere.",
          options=("use_secret",)),

    # Scheduling. One message can carry several causes, so each is a family.
    _rule("disk-pressure", FAIL, "a node is short of disk",
          "event pod",
          r"DiskPressure|disk-pressure|disk pressure|FreeDiskSpaceFailed|"
          r"ImageGCFailed|EvictionThresholdMet",
          "A node is short of disk. Kubernetes stops placing pods there and "
          "evicts running ones.",
          "Each concurrent engine needs about 60GB of node disk, 40GB of it "
          "for /tmp. Give the engine nodes larger disks, or run fewer engines "
          "per node (engines_per_node, and slots on the location). "
          "engine_ephemeral_request_mb reserves disk per engine so the "
          "scheduler accounts for it.",
          family="schedule-taint",
          options=("engines_per_node", "engine_ephemeral_request_mb")),
    _rule("schedule-taint", FAIL, "no node tolerated",
          "event pod",
          r"untolerated taint|had taint .*that the pod didn't tolerate",
          "Every node the pod could use carries a taint the pod does not "
          "tolerate.",
          "tolerations applies to crane and every engine; engine_tolerations "
          "to engines only, for a tainted engine pool. Add a toleration that "
          "matches the taint in the evidence, and a node_selector or "
          "engine_node_selector that points at the same pool.",
          subject=_taints, family="schedule-taint",
          options=("tolerations", "engine_tolerations", "node_selector",
                   "engine_node_selector")),
    _rule("schedule-resources", FAIL, "no node has room",
          "event pod", r"Insufficient [\w.\-/]+|Too many pods",
          "No node has room for the pod: the scheduler reports insufficient "
          "resources.",
          "Each engine requests what it is limited to: 2 CPU and 8Gi by "
          "default, or engine_cpu_limit and engine_mem_limit (a location's "
          "overrideCPU and overrideMemory replace them). Use nodes large enough "
          "for one engine plus system overhead, lower the engine size, or "
          "lower slots on the location. For ephemeral-storage, lower "
          "engine_ephemeral_request_mb or use larger disks. bzm-opl-gen doctor "
          "compares node capacity with slots times engine size. Where a "
          "cluster autoscaler adds a node, this clears on its own.",
          subject=_resources,
          options=("engine_cpu_limit", "engine_mem_limit",
                   "engine_ephemeral_request_mb")),
    _rule("schedule-selector", FAIL, "no node matches the selector",
          "event pod",
          r"didn't match Pod's node affinity|didn't match node selector|"
          r"didn't match pod affinity|didn't match pod anti-affinity",
          "No node carries the labels the pod's node selector asks for.",
          "node_selector (crane and engines) and engine_node_selector "
          "(engines only) must match labels the nodes really carry; kubectl "
          "get nodes --show-labels lists them. Correct the label, or remove "
          "the selector.",
          options=("node_selector", "engine_node_selector")),

    # Admission. Crane creates engine pods itself, so a refusal of one reaches
    # crane's log as an API error rather than an event.
    _rule("quota", FAIL, "a ResourceQuota refused a pod",
          "event log", r"exceeded quota|failed quota",
          "A ResourceQuota in the namespace refused a pod.",
          "Each engine requests its full limit (2 CPU and 8Gi by default), so "
          "the quota must hold slots times the engine size, plus crane (1 CPU "
          "and 2Gi limit). Raise the quota, lower slots on the location, or "
          "lower engine_cpu_limit and engine_mem_limit. If the message says "
          "must specify, the quota requires limits on every pod: crane's "
          "housekeeping test-job pods carry none, and a LimitRange default "
          "covers them. bzm-opl-gen doctor compares the quota with the "
          "location.",
          subject=_first_group(r"(?:exceeded|failed) quota: ([\w.\-]+)"),
          options=("engine_cpu_limit", "engine_mem_limit")),
    _rule("pod-security", FAIL, "Pod Security admission refused a pod",
          "event log", r"violates PodSecurity",
          "Pod Security admission refused a pod.",
          "Crane's own pod meets the restricted level, but the engines meet "
          "baseline, not restricted: " + ENGINE_NO_RUN_AS_NON_ROOT + ". "
          + run_as_non_root_fix("<namespace>") + ". The level on the "
          "namespace is a decision for your platform team. Keep "
          "restrict_engines on (the default): without it the engines are "
          "privileged and even baseline refuses them. On plain Kubernetes, "
          "run_as_user must be a non-root UID.",
          subject=_first_group(r'violates PodSecurity "([^"]+)"'),
          options=("restrict_engines", "run_as_user", "platform")),
    _rule("openshift-scc", FAIL, "no SecurityContextConstraints admitted a pod",
          "event log",
          r"unable to validate against any security context constraint",
          "OpenShift admitted the pod under no SecurityContextConstraints.",
          "Generate with platform openshift, so the UID is left to the SCC "
          "rather than pinned, and keep restrict_engines on. If it persists, "
          "your platform team grants an SCC to the agent's service account "
          "(service_account_name): oc adm policy add-scc-to-user <scc> -z "
          "<service-account> -n <namespace>.",
          options=("platform", "restrict_engines", "service_account_name")),
    _rule("admission-webhook", FAIL, "a policy webhook refused a pod",
          "event log", r'admission webhook "[^"]+" denied the request',
          "A policy admission webhook (Kyverno, Gatekeeper or similar) refused "
          "a pod. The subject names the webhook and, where the message carries "
          "it, the policy.",
          "The message says which rule failed. Rules on privilege, "
          "capabilities or the user are met by restrict_engines (on by "
          "default) and run_as_user; rules on allowed registries by "
          "private_registry; rules on resources by engine_cpu_limit and "
          "engine_mem_limit. Anything else is an exception to ask your "
          "platform team for, naming the policy.",
          subject=_webhook,
          options=("restrict_engines", "run_as_user", "private_registry",
                   "engine_cpu_limit", "engine_mem_limit")),
    _rule("service-account-missing", FAIL, "the service account does not exist",
          "event", r"^ServiceAccountMissing:",
          "The ReplicaSet cannot create crane's pod: the ServiceAccount the "
          "pod names is not in the namespace.",
          "Apply the bundle's ServiceAccount, or, where your platform team "
          "owns the account (service_account_create off), ask them to create "
          "the one service_account_name names.",
          subject=_given,
          options=("service_account_create", "service_account_name")),
    _rule("service-account-unread", WARN, "a pod waited for a service account "
          "that could not be checked", "event", r"^ServiceAccountUnread:",
          "The ReplicaSet could not create a pod because the ServiceAccount "
          "was missing at the time. Whether it exists now could not be read; "
          "the unread section of this report says why.",
          "Check with kubectl get serviceaccount in the namespace. If it is "
          "missing, apply the bundle's ServiceAccount, or ask your platform "
          "team for the one service_account_name names.",
          subject=_given, options=("service_account_name",)),
    _rule("service-account-late", NOTE, "a pod waited for its service account",
          "event", r"^ServiceAccountCreatedLater:",
          "The ReplicaSet tried to create a pod before the ServiceAccount "
          "existed, which happens when the files are applied in alphabetical "
          "order. The ServiceAccount exists now, so the ReplicaSet's next "
          "attempt creates the pod.",
          "Nothing, if crane is running. Otherwise the other findings in this "
          "report say why it is not.",
          subject=_given),
    # Measured on crane 3.8.0: on every engine of runs that returned all their
    # results, so alone it explains nothing.
    _rule("engine-prestop", NOTE, "an engine's preStop hook failed as it ended",
          "event", r"^FailedPreStopHook:",
          "The preStop hook crane gives each engine pod failed as the pod "
          "ended. It fails on engines whose runs return all their results, so "
          "on its own it does not explain a failed run.",
          "Nothing on its own. If a run's results are incomplete, the other "
          "findings in this report say why.",
          subject=_role, roles="engine"),
    _rule("rbac", FAIL, "the API refused crane's service account",
          "event log", r'forbidden: User "[^"]+" cannot',
          "The Kubernetes API refused crane's service account an action it "
          "needs.",
          "The bundle's Role and RoleBinding (role-crane) grant what crane "
          "needs in the namespace: check both were applied and bind "
          "service_account_name. Where your platform team owns the account "
          "(service_account_create off), they bind the same Role. Reading "
          "nodes needs cluster_rbac.",
          subject=_rbac,
          options=("service_account_name", "service_account_create",
                   "cluster_rbac")),

    # What happens to a pod once it runs.
    _rule("oom-engine", FAIL, "an engine ran out of memory",
          "container", r"OOMKilled",
          "An engine ran out of memory and was killed. BlazeMeter reports it "
          "as a test that stopped or lost an engine.",
          "Raise engine_mem_limit, or lower threadsPerEngine on the location so "
          "each engine drives fewer virtual users. bzm-opl-gen doctor checks "
          "engine memory against threadsPerEngine.",
          subject=_role, roles="engine", family="oom",
          options=("engine_mem_limit",)),
    _rule("oom", FAIL, "a pod ran out of memory",
          "container", r"OOMKilled",
          "A container in the namespace ran out of memory and was killed.",
          "Crane's own limit is 2Gi, set in the bundle's Deployment. If crane "
          "is the pod named here and it recurs, raise that limit and send the "
          "crane log to BlazeMeter support.",
          subject=_role, family="oom"),
    _rule("evicted", FAIL, "a pod was evicted",
          "event pod",
          r"\bEvicted\b|low on resource|ephemeral local storage usage exceeds|"
          r"exceeded its local ephemeral storage limit",
          "A pod was evicted. BlazeMeter reports an evicted engine as a test "
          "that stopped.",
          "For ephemeral-storage: engine_ephemeral_request_mb and "
          "engine_ephemeral_limit_mb size each engine's disk, "
          "crane_ephemeral_storage sizes crane's, and nodes need about 60GB "
          "per concurrent engine. For memory: engine_mem_limit, and nodes with "
          "room for every engine's request.",
          subject=_evicted,
          options=("engine_ephemeral_request_mb", "engine_ephemeral_limit_mb",
                   "crane_ephemeral_storage", "engine_mem_limit")),
    _rule("crash-loop", WARN, "a container keeps restarting",
          "event container",
          r"CrashLoopBackOff|Back-off restarting failed container",
          "A container keeps restarting.",
          "The crane log findings in this report, read from the previous run "
          "too, usually say why. If none match, read kubectl logs --previous "
          "for the pod.",
          subject=_role),
)

RULES_BY_ID = {r.id: r for r in RULES}


# -- signals: every observation in one shape -------------------------------------

# One observation. `object` is "<Kind>/<name>"; `subject` the image, where one
# is known; `role` crane, engine, test-job, crane-hook, mock or other.
Signal = collections.namedtuple(
    "Signal", "source reason text object subject role count warning")


def pod_role(pod):
    meta = pod.get("metadata") or {}
    name = meta.get("name") or ""
    labels = meta.get("labels") or {}
    if CRANE_HOOK_NAME.match(name):
        return "crane-hook"
    if labels.get(CRANE_LABEL[0]) == CRANE_LABEL[1] or CRANE_NAME.match(name):
        return "crane"
    if TEST_JOB_NAME.match(name):
        return "test-job"
    if ENGINE_NAME.match(name):
        return "engine"
    if labels.get(service_virt.SV_POD_NAME_LABEL):
        return "mock"
    return "other"


def _name_role(kind, name):
    """The role an event's object implies from its name alone."""
    return pod_role({"metadata": {"name": name or ""}}) if kind in (
        "Pod", "ReplicaSet", "Deployment", "Job") else "other"


def parse_since(since):
    """'1h', '30m', '1h30m', '90s', '2d' -> seconds. ValueError for anything
    else."""
    text = str(since or "").strip().lower()
    parts = re.findall(r"(\d+)([smhd])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"--since {since!r}: expected a duration such as 30m, "
                         f"1h or 1h30m")
    unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = sum(int(n) * unit[u] for n, u in parts)
    if seconds <= 0:
        raise ValueError(f"--since {since!r}: must be longer than zero")
    return seconds


def _when(text):
    """An RFC 3339 timestamp as an aware datetime, or None."""
    if not text:
        return None
    t = str(text).replace("Z", "+00:00")
    # Python 3.10 accepts three or six fraction digits only.
    t = re.sub(r"\.(\d{1,6})\d*", lambda m: "." + m.group(1).ljust(6, "0"), t)
    try:
        return datetime.datetime.fromisoformat(t)
    except ValueError:
        return None


def _event_time(event):
    series = event.get("series") or {}
    for text in (series.get("lastObservedTime"), event.get("lastTimestamp"),
                 event.get("eventTime"),
                 (event.get("metadata") or {}).get("creationTimestamp")):
        when = _when(text)
        if when:
            return when
    return None


def _images_by_container(pods):
    """{(pod name, container name): image}, to name the image an event about
    a container leaves out."""
    found = {}
    for pod in pods or []:
        name = (pod.get("metadata") or {}).get("name")
        spec = pod.get("spec") or {}
        for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
            found[(name, c.get("name"))] = c.get("image")
    return found


IMAGE_IN_TEXT = re.compile(r'image "([^"]+)"')


def _finished(pods, name):
    """True where the pod has Succeeded or is gone; None where pods are unread."""
    if pods is None:
        return None
    phase = next(((p.get("status") or {}).get("phase") for p in pods
                  if (p.get("metadata") or {}).get("name") == name), "gone")
    return phase in ("Succeeded", "gone")


def event_signals(events, pods, since_s, now):
    images = _images_by_container(pods)
    cutoff = now - datetime.timedelta(seconds=since_s)
    out = []
    for e in events:
        when = _event_time(e)
        if when is not None and when < cutoff:
            continue
        obj = e.get("involvedObject") or {}
        kind, name = obj.get("kind") or "?", obj.get("name") or "?"
        text = e.get("message") or ""
        if (kind == "Pod" and e.get("reason") == "Unhealthy"
                and PROBE_RACED_EXIT.search(text) and _finished(pods, name)):
            continue
        image = None
        m = IMAGE_IN_TEXT.search(text)
        if m:
            image = m.group(1)
        else:
            c = re.match(r"spec\.(?:init)?[cC]ontainers\{([^}]+)\}",
                         obj.get("fieldPath") or "")
            if c:
                image = images.get((name, c.group(1)))
        count = (e.get("series") or {}).get("count") or e.get("count") or 1
        out.append(Signal("event", e.get("reason") or "", text,
                          f"{kind}/{name}", image, _name_role(kind, name),
                          int(count), e.get("type") == "Warning"))
    return out


def pod_signals(pods, since_s, now):
    cutoff = now - datetime.timedelta(seconds=since_s)
    out = []
    for pod in pods:
        meta, status = pod.get("metadata") or {}, pod.get("status") or {}
        obj, role = f"Pod/{meta.get('name')}", pod_role(pod)
        if status.get("reason"):
            # Evicted, and the other pod-level terminal reasons.
            out.append(Signal("pod", status["reason"], status.get("message") or "",
                              obj, None, role, 1, True))
        for cond in status.get("conditions") or []:
            if (cond.get("type") == "PodScheduled"
                    and cond.get("status") == "False"
                    and cond.get("reason") == "Unschedulable"):
                out.append(Signal("pod", "FailedScheduling",
                                  cond.get("message") or "", obj, None, role,
                                  1, True))
        statuses = ((status.get("initContainerStatuses") or [])
                    + (status.get("containerStatuses") or []))
        for cs in statuses:
            image = cs.get("image")
            where = f"container {cs.get('name')}"
            state = cs.get("state") or {}
            waiting = state.get("waiting") or {}
            if waiting.get("reason") and waiting["reason"] not in BENIGN_WAITING:
                out.append(Signal("container", waiting["reason"],
                                  f"{where}: {waiting.get('message') or waiting['reason']}",
                                  obj, image, role, 1, True))
            for label, term in (("is", state.get("terminated")),
                                ("last", (cs.get("lastState") or {}).get("terminated"))):
                if not term or term.get("reason") in (None, "Completed"):
                    continue
                finished = _when(term.get("finishedAt"))
                if label == "last" and finished is not None and finished < cutoff:
                    continue
                text = (f"{where} {'terminated' if label == 'is' else 'last terminated'}: "
                        f"{term['reason']}, exit code {term.get('exitCode')}"
                        + (f", restarts {cs.get('restartCount')}"
                           if cs.get("restartCount") else "")
                        + (f": {term['message']}" if term.get("message") else ""))
                out.append(Signal("container", term["reason"], text, obj, image,
                                  role, 1, True))
    return out


def log_signals(logs):
    out = []
    for log in logs:
        if not log.text:
            continue
        obj = f"Pod/{log.pod}" + (" (previous run)" if log.previous else "")
        for line in redact(log.text).splitlines():
            # An API error body arrives JSON-escaped inside the log line.
            line = line.strip().replace('\\"', '"')
            if line:
                out.append(Signal("log", "", line, obj, None, "crane", 1,
                                  bool(ERRORISH.search(line))))
    return out


def _proxy_host(url):
    """The proxy's address without its credentials."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or url
        return f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return "an unparseable URL"


def _started(pod):
    """When the pod's newest running container started, or None."""
    times = [_when(((cs.get("state") or {}).get("running") or {}).get("startedAt"))
             for cs in (pod.get("status") or {}).get("containerStatuses") or []]
    times = [t for t in times if t]
    return max(times) if times else None


def startup_hang_signals(logs, pods, now):
    """A crane whose current log ends at its first call to BlazeMeter, running
    for HANG_AFTER_S or more. The failure is a line that never came, so no
    RULES pattern over single lines can see it."""
    by_name = {(p.get("metadata") or {}).get("name"): p for p in pods or []}
    out = []
    for log in logs:
        pod = by_name.get(log.pod)
        text = redact(log.text or log.last)
        if log.previous or not text or pod is None:
            continue
        started = _started(pod)
        if started is None:
            continue
        running_s = int((now - started).total_seconds())
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if running_s < HANG_AFTER_S or not lines or not STARTUP_CHECK.search(lines[-1]):
            continue
        proxies = [p for p in PROXY_IN_LOG.findall(text)
                   if p.lower() not in ("none", "null")]
        proxy = _proxy_host(proxies[-1]) if proxies else None
        text = (f"crane has run for {running_s}s and its log ends at its first "
                f"call to BlazeMeter: {lines[-1]}"
                + (f"; its log names HTTPS Proxy {proxy}" if proxy else ""))
        out.append(Signal("startup", "CraneStartupHang", text, f"Pod/{log.pod}",
                          proxy, "crane", 1, True))
    return out


def service_account_signals(signals, service_accounts):
    """Each event naming a ServiceAccount that was missing, renamed by whether
    it exists now: {name: True} exists, False is absent, None or no entry
    could not be read (gather puts the reason in `unread`)."""
    out = []
    for s in signals:
        m = SA_LOOKUP.search(s.text) if s.source == "event" else None
        if not m:
            out.append(s)
            continue
        exists = (service_accounts or {}).get(m.group(1))
        reason = {True: "ServiceAccountCreatedLater",
                  False: "ServiceAccountMissing"}.get(exists, "ServiceAccountUnread")
        out.append(s._replace(reason=reason, subject=m.group(1),
                              warning=exists is not True))
    return out


def namespace_signals(namespace_obj, pods):
    """What the reads themselves imply: a missing namespace, or no crane."""
    if namespace_obj == {}:
        return [Signal("namespace", "NamespaceNotFound", "", "Namespace", None,
                       "other", 1, True)]
    if pods is not None and not any(pod_role(p) == "crane" for p in pods):
        return [Signal("namespace", "NoCranePod",
                       f"{len(pods)} pod(s) in the namespace, none of them crane",
                       "Deployment/crane", None, "crane", 1, True)]
    return []


# -- matching ---------------------------------------------------------------------

# `history`: every pod it was seen on is gone. `also`: container states on
# the same pod that the finding explains.
Finding = collections.namedtuple(
    "Finding", "rule subject objects count evidence history also",
    defaults=(False, ()))
Unrecognised = collections.namedtuple(
    "Unrecognised", "source reason count objects example")


def matches(rule, signal):
    if signal.source not in rule.sources:
        return False
    if rule.roles is not None and signal.role not in rule.roles:
        return False
    haystack = f"{signal.reason}: {signal.text}"
    return all(p.search(haystack) for p in rule.patterns)


def rules_for(signal):
    """Every rule the signal matches, the first per family."""
    taken, hits = set(), []
    for rule in RULES:
        if rule.family in taken or not matches(rule, signal):
            continue
        taken.add(rule.family)
        hits.append(rule)
    return hits


def _clean(text):
    """One line, its middle elided when long: pull and API errors put the
    object first and the cause last, and both matter."""
    one_line = " ".join(str(text).replace("\\n", "\n").split())
    if len(one_line) <= EVIDENCE_CHARS:
        return one_line
    head = EVIDENCE_CHARS * 2 // 5
    return one_line[:head] + " ... " + one_line[-(EVIDENCE_CHARS - head - 5):]


def _shape(text):
    """A message with its varying parts blanked, to group repeats."""
    t = re.sub(r"\b[0-9a-f]{8,}\b|\d+", "#", _clean(text))
    return re.sub(r"-[a-z0-9]{5}\b", "-#", t)[:160]


def classify(signals):
    """(findings, unrecognised) from signals; findings in RULES order."""
    groups = collections.OrderedDict()
    unknown = collections.OrderedDict()
    matched = [(s, rules_for(s)) for s in signals]
    for s, hits in matched:
        for rule in hits:
            subject = rule.subject(s.text, s) if rule.subject else None
            key = (rule.id, subject)
            g = groups.setdefault(key, {"objects": [], "count": 0, "also": [],
                                        "evidence": s.text or s.reason})
            g["count"] += s.count
            if s.object not in g["objects"]:
                g["objects"].append(s.object)

    # A bare traceback header in a log a rule already explained is the same
    # failure; a container state on a crash-looping pod is that crash loop.
    explained_logs = {s.object for s, hits in matched if hits and s.source == "log"}
    crash_loops = {o: g for (rid, _), g in groups.items() if rid == "crash-loop"
                   for o in g["objects"]}
    for s, hits in matched:
        if hits or not s.warning:
            continue
        if (s.source == "log" and s.object in explained_logs
                and TRACEBACK_HEADER.match(s.text)):
            continue
        if s.source == "container" and s.object in crash_loops:
            also = crash_loops[s.object]["also"]
            if _clean(s.text) not in also:
                also.append(_clean(s.text))
            continue
        key = (s.source, s.reason, _shape(s.text))
        u = unknown.setdefault(key, {"objects": [], "count": 0,
                                     "example": s.text})
        u["count"] += s.count
        if s.object not in u["objects"]:
            u["objects"].append(s.object)

    # A generic group folds into the specific one for its subject (or, with no
    # subject, the family's first): the same failure, seen without its cause.
    specific = {}
    for (rid, subject), g in groups.items():
        rule = RULES_BY_ID[rid]
        if not rule.generic:
            specific.setdefault((rule.family, subject), g)
            specific.setdefault((rule.family, None), g)
    for (rid, subject), g in list(groups.items()):
        rule = RULES_BY_ID[rid]
        into = rule.generic and specific.get((rule.family, subject))
        if into:
            into["count"] += g["count"]
            into["objects"] += [o for o in g["objects"] if o not in into["objects"]]
            del groups[(rid, subject)]
    order = {r.id: i for i, r in enumerate(RULES)}
    findings = [Finding(RULES_BY_ID[rid], subject, g["objects"], g["count"],
                        _clean(g["evidence"]), False, tuple(g["also"]))
                for (rid, subject), g in groups.items()]
    findings.sort(key=lambda f: (STATUS_RANK[f.rule.status], order[f.rule.id]))
    unrecognised = [Unrecognised(src, reason, u["count"], u["objects"],
                                 _clean(u["example"]))
                    for (src, reason, _), u in unknown.items()]
    unrecognised.sort(key=lambda u: -u.count)
    return findings, unrecognised


# -- the impure layer -------------------------------------------------------------

# One crane log stream: `text` None when it could not be read, with `detail`;
# "" with NO_LOG_YET as `detail` for a container that has not started. `last`
# is the log's last lines regardless of --since, read only when `text` is "".
LogRead = collections.namedtuple("LogRead", "pod previous text detail last",
                                 defaults=(None,))

# What gather() read. Each section is None where it could not be read; `unread`
# holds (section, reason) for each of those. `service_accounts` holds
# {name: exists} for each ServiceAccount an event said was missing: True,
# False for NotFound, None where it could not be read.
Gathered = collections.namedtuple(
    "Gathered", "namespace_obj events pods logs unread service_accounts",
    defaults=(None,))


def _read(cmd):
    """(stdout, None) for a read that worked, (None, reason) otherwise."""
    try:
        out = kube.quiet(cmd, timeout=READ_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, f"{cmd[0]} did not answer within {READ_TIMEOUT_S}s"
    except OSError as e:
        return None, str(e)
    if out.returncode != 0:
        why = (out.stderr or out.stdout or "").strip()
        return None, (why or f"{cmd[0]} exited {out.returncode}")[:EVIDENCE_CHARS]
    return out.stdout or "", None


def _read_items(cmd):
    text, why = _read(cmd)
    if text is None:
        return None, why
    try:
        return json.loads(text or "{}").get("items", []), None
    except (ValueError, AttributeError):
        return None, f"unparseable output: {text.strip()[:120]}"


def _restarted(pod):
    return any((cs.get("restartCount") or 0) > 0
               for cs in (pod.get("status") or {}).get("containerStatuses") or [])


def gather(cli, namespace, since_s, log_lines):
    """Read the namespace: events, pods, and crane's log (current, and the
    previous run where crane restarted)."""
    base = [cli, "-n", namespace]
    timeout = f"--request-timeout={REQUEST_TIMEOUT}"
    unread = []
    events, why = _read_items(base + ["get", "events", "-o", "json", timeout])
    if events is None:
        unread.append(("events", why))
    pods, why = _read_items(base + ["get", "pods", "-o", "json", timeout])
    if pods is None:
        unread.append(("pods", why))

    logs = []
    if pods is None:
        # No pod names to read by; the Deployment still names crane's pod.
        targets = [("deploy/crane", False)]
    else:
        targets = [(p["metadata"]["name"], prev) for p in pods
                   if pod_role(p) == "crane"
                   for prev in ((False, True) if _restarted(p) else (False,))]
    for target, previous in targets:
        cmd = base + ["logs", target, "--all-containers", f"--tail={log_lines}",
                      timeout]
        # The previous run is read whole: its crash is the point, however old.
        cmd += ["--previous"] if previous else [f"--since={since_s}s"]
        text, why = _read(cmd)
        name = target.split("/")[-1]
        if text is None and NOT_STARTED.search(why or ""):
            text, why = "", NO_LOG_YET
        last = None
        if text == "" and not previous and why is None:
            # Nothing within --since: a crane hung for longer than that
            # shows only in its last lines, however old.
            last, _ = _read(base + ["logs", target, "--all-containers",
                                    f"--tail={HANG_TAIL}", timeout])
        logs.append(LogRead(name, previous, redact(text), redact(why),
                            redact(last)))
        if text is None:
            unread.append((f"crane log ({name}"
                           + (", previous run)" if previous else ")"), redact(why)))

    accounts = {}
    for e in events or []:
        m = SA_LOOKUP.search(e.get("message") or "")
        if m and m.group(1) not in accounts:
            text, why = _read(base + ["get", "serviceaccount", m.group(1),
                                      "-o", "name", timeout])
            accounts[m.group(1)] = (True if text is not None
                                    else False if "(NotFound)" in (why or "")
                                    else None)
            if accounts[m.group(1)] is None:
                unread.append((f"service account {m.group(1)}", why))

    # {} NotFound, None unread. Namespaced roles often cannot read the object,
    # so that only matters where the namespace looked empty.
    ns_obj = kube.kget_named(cli, None, "ns", namespace, timeout=READ_TIMEOUT_S)
    if ns_obj is None and pods == []:
        unread.append(("namespace", "could not read the Namespace object, so "
                       "an empty namespace and a missing one look the same"))
    return Gathered(ns_obj, events, pods, logs, unread, accounts)


# -- the verdict --------------------------------------------------------------------

Triage = collections.namedtuple(
    "Triage", "namespace since read unread findings unrecognised")


def evaluate(gathered, namespace, since="1h", now=None):
    """Findings from what gather() read; nothing is printed or fetched."""
    since_s = parse_since(since)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    g = gathered
    signals = []
    if g.events is not None:
        signals += service_account_signals(
            event_signals(g.events, g.pods, since_s, now), g.service_accounts)
    if g.pods is not None:
        signals += pod_signals(g.pods, since_s, now)
    signals += log_signals(g.logs)
    signals += startup_hang_signals(g.logs, g.pods, now)
    signals += namespace_signals(g.namespace_obj, g.pods)
    findings, unrecognised = classify(signals)
    findings = mark_history(findings, g.pods)

    read = []
    if g.events is not None:
        read.append(f"events ({len(g.events)})")
    if g.pods is not None:
        read.append(f"pods ({len(g.pods)})")
    for log in g.logs:
        if log.text is not None:
            about = (log.detail if log.detail == NO_LOG_YET
                     else f"{len(log.text.splitlines())} lines")
            read.append(f"crane log {log.pod}"
                        + (" previous run" if log.previous else "")
                        + f" ({about})")
    return Triage(namespace, since, read, list(g.unread), findings, unrecognised)


def mark_history(findings, pods):
    """Findings seen only on pods that are gone, marked as history and moved
    after the current ones. Events outlive their pod by about an hour, and a
    finding from a replaced pod does not describe the namespace now. Events
    about a ReplicaSet or Deployment are not about one pod, so stay current."""
    if pods is None:
        return findings
    current = {f"Pod/{(p.get('metadata') or {}).get('name')}" for p in pods}
    out = [f._replace(history=all(o.startswith("Pod/")
                                  and o.split(" ")[0] not in current
                                  for o in f.objects))
           for f in findings]
    return sorted(out, key=lambda f: f.history)


def status(finding):
    return NOTE if finding.history else finding.rule.status


def has_failures(result):
    return any(status(f) == FAIL for f in result.findings)


def summary_line(result):
    fails = sum(status(f) == FAIL for f in result.findings)
    warns = sum(status(f) == WARN for f in result.findings)
    notes = sum(status(f) == NOTE for f in result.findings)
    parts = [f"{fails} known failure{'s' if fails != 1 else ''}"]
    if warns:
        parts.append(f"{warns} warning{'s' if warns != 1 else ''}")
    if notes:
        parts.append(f"{notes} note{'s' if notes != 1 else ''}")
    parts.append(f"{len(result.unrecognised)} unrecognised warning"
                 f"{'s' if len(result.unrecognised) != 1 else ''}")
    if result.unread:
        parts.append(f"{len(result.unread)} section"
                     f"{'s' if len(result.unread) != 1 else ''} unread")
    line = ", ".join(parts)
    if not result.read:
        line += ". Nothing could be read, so this report cannot show a failure"
    return line


def as_dict(result):
    return {
        "namespace": result.namespace,
        "since": result.since,
        "read": result.read,
        "unread": [{"section": s, "detail": d} for s, d in result.unread],
        "findings": [{"rule": f.rule.id, "status": status(f),
                      "title": f.rule.title, "subject": f.subject,
                      "objects": f.objects, "count": f.count,
                      "evidence": f.evidence, "also": list(f.also),
                      "history": f.history, "finding": f.rule.finding,
                      "fix": f.rule.fix, "options": list(f.rule.options)}
                     for f in result.findings],
        "unrecognised": [u._asdict() for u in result.unrecognised],
        "summary": summary_line(result),
        "ok": not has_failures(result),
    }


def _wrap(text, indent=6):
    return textwrap.fill(text, width=100, initial_indent=" " * indent,
                         subsequent_indent=" " * indent)


def report(doc):
    """Print as_dict()'s document for a person."""
    print(f"triage: namespace {doc['namespace']}, the last {doc['since']}")
    print(f"read: {', '.join(doc['read']) if doc['read'] else 'nothing'}")
    for f in doc["findings"]:
        times = "once" if f["count"] == 1 else f"{f['count']} times"
        gone = " (pod gone; history)" if f.get("history") else ""
        print(f"\n{f['status']:<4}  {f['rule']}: {f['title']} ({times}){gone}")
        if f["subject"]:
            print(_wrap(f"about: {f['subject']}"))
        objects = f["objects"]
        shown = ", ".join(objects[:5]) + (
            f" and {len(objects) - 5} more" if len(objects) > 5 else "")
        print(_wrap(f"seen on: {shown}"))
        print(_wrap(f"evidence: {f['evidence']}"))
        for line in f.get("also") or []:
            print(_wrap(f"also: {line}"))
        print(_wrap(f["finding"]))
        print(_wrap(f"fix: {f['fix']}"))
    for u in doc["unread"]:
        print(f"\n{WARN:<4}  unread: {u['section']}")
        print(_wrap(u["detail"]))
    if doc["unrecognised"]:
        print("\nunrecognised warnings, listed as found:")
        for u in doc["unrecognised"]:
            where = ", ".join(u["objects"][:3]) + (
                " ..." if len(u["objects"]) > 3 else "")
            label = f"{u['source']} {u['reason']}".strip()
            print(f"  x{u['count']:<4} {label}  {where}")
            print(_wrap(u["example"], indent=8))
    print(f"\n{doc['summary']}")
