"""After installing: check a deployed agent from the cluster, from BlazeMeter,
and optionally with one real engine run.

Runs against the customer's own agent. It creates, deploys, repoints and
deletes nothing; the one write is starting the test named with --run-test.
The reads here are the impure layer, the *_checks functions are pure over
what they read, and core.smoke orders the stages. A section read as None could
not be read; {} was read and is not there.
"""

import collections
import json
import shlex
import subprocess
import textwrap
import urllib.parse

from . import api, kube, livetest, markers, triage as triage_mod
from .bundle_names import CONFIGMAP_NAME, SECRET_NAME
from .bundle_options import DEFAULT_OPTIONS, engine_size
from .ca_trust import CA_MOUNT_PATH
from .footprint import PUBLIC_REGISTRY
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory
from .verdict import FAIL, PASS, WARN

# UNREAD: the read was refused or not answered; never a failure. SKIP: not
# judged, because what it depends on failed or is absent.
UNREAD = "UNREAD"
SKIP = "SKIP"
STATUSES = (PASS, WARN, UNREAD, SKIP, FAIL)

Check = collections.namedtuple("Check", "name status detail fix")


def check(name, status, detail, fix=None):
    return Check(name, status, detail, fix)


STAGE_CLUSTER = "cluster"
STAGE_BLAZEMETER = "blazemeter"
STAGE_CONFIG = "configuration"
STAGE_ENGINE = "engine run"
STAGE_TITLES = {
    STAGE_CLUSTER: "the agent in the cluster",
    STAGE_BLAZEMETER: "the agent in BlazeMeter",
    STAGE_CONFIG: "the configuration crane runs with",
    STAGE_ENGINE: "one real engine run",
}

# Crane's container in both formats; the Deployment's own name can differ
# under Helm's fullnameOverride.
CRANE_CONTAINER_PREFIX = "bzm-crane-"
TERMINAL = ("ENDED", "ABORTED", "FAILED")

DEFAULT_ENGINE_TIMEOUT = 420
DEFAULT_RUN_TIMEOUT = 900
POLL_S = 10


# -- reads ------------------------------------------------------------------------

def _read(cmd):
    """(stdout, None) for a read that worked, (None, reason) otherwise, and
    ("", "NotFound") where the API server answered NotFound."""
    try:
        out = kube.quiet(cmd, timeout=triage_mod.READ_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, f"{cmd[0]} did not answer within {triage_mod.READ_TIMEOUT_S}s"
    except OSError as e:
        return None, str(e)
    if out.returncode == 0:
        return out.stdout or "", None
    why = (out.stderr or out.stdout or "").strip()
    if "(NotFound)" in why:
        return "", "NotFound"
    return None, (why or f"{cmd[0]} exited {out.returncode}")[:triage_mod.EVIDENCE_CHARS]


def _get(cli, namespace, kind, name=None):
    """(object, reason): the parsed object, {} for NotFound, None if unread."""
    cmd = [cli, "-n", namespace, "get", kind] + ([name] if name else []) + [
        "-o", "json", f"--request-timeout={triage_mod.REQUEST_TIMEOUT}"]
    text, why = _read(cmd)
    if why == "NotFound":
        return {}, None
    if text is None:
        return None, why
    try:
        return json.loads(text or "{}"), None
    except ValueError:
        return None, f"unparseable output: {text.strip()[:120]}"


# Key names only: the values are the credential, and are never read.
_KEY_NAMES = '{{range $k, $v := .data}}{{$k}}{{"\\n"}}{{end}}'


def secret_keys(cli, namespace, name):
    """(keys, reason): the Secret's key names, {} for NotFound, None if unread."""
    text, why = _read([cli, "-n", namespace, "get", "secret", name,
                       "-o", f"go-template={_KEY_NAMES}",
                       f"--request-timeout={triage_mod.REQUEST_TIMEOUT}"])
    if why == "NotFound":
        return {}, None
    if text is None:
        return None, why
    return sorted(k for k in text.split() if k), None


def ca_certificates_in_pod(cli, namespace, pod, path):
    """(count, reason) of certificates at `path` inside crane's pod. count is
    0 for a file with none, -1 for no readable file, None if exec was refused."""
    p = shlex.quote(path)
    text, why = _read([cli, "-n", namespace, "exec", pod, "--", "sh", "-c",
                       f'if [ -r {p} ]; then echo "count=$(grep -c "BEGIN '
                       f'CERTIFICATE" {p})"; else echo count=missing; fi'])
    for line in (text or "").splitlines():
        if line.startswith("count="):
            value = line[6:].strip()
            return (-1 if value == "missing" else int(value or 0)), None
    return None, why or "no answer from the pod"


Cluster = collections.namedtuple(
    "Cluster", "deployments pods deployment configmap_name configmap secret_name "
               "secret_keys ca_name ca_configmap ca_in_pod unread")


def is_crane_deployment(d):
    labels = (d.get("metadata") or {}).get("labels") or {}
    containers = (((d.get("spec") or {}).get("template") or {}).get("spec")
                  or {}).get("containers") or []
    return (labels.get(triage_mod.CRANE_LABEL[0]) == triage_mod.CRANE_LABEL[1]
            or any((c.get("name") or "").startswith(CRANE_CONTAINER_PREFIX)
                   for c in containers))


def _pod_spec(deployment):
    return ((deployment.get("spec") or {}).get("template") or {}).get("spec") or {}


def _crane_container(deployment):
    containers = _pod_spec(deployment).get("containers") or []
    named = [c for c in containers
             if (c.get("name") or "").startswith(CRANE_CONTAINER_PREFIX)]
    return (named or containers or [{}])[0]


def env_sources(deployment):
    """(ConfigMap name, Secret name or None) crane's envFrom names."""
    cm, secret = None, None
    for src in _crane_container(deployment).get("envFrom") or []:
        if "configMapRef" in src and cm is None:
            cm = src["configMapRef"].get("name")
        if "secretRef" in src and secret is None:
            secret = src["secretRef"].get("name")
    return cm, secret


def ca_volume(deployment):
    """The ConfigMap mounted at the CA path in crane, or None."""
    mounts = {m.get("name") for m in _crane_container(deployment).get("volumeMounts") or []
              if (m.get("mountPath") or "").startswith(CA_MOUNT_PATH)}
    for v in _pod_spec(deployment).get("volumes") or []:
        if v.get("name") in mounts and v.get("configMap"):
            return v["configMap"].get("name")
    return None


def pick_deployment(deployments, ship_id=None):
    """The crane Deployment for `ship_id`, else the first one."""
    cranes = [d for d in deployments or [] if is_crane_deployment(d)]
    if ship_id:
        for d in cranes:
            labels = (d.get("metadata") or {}).get("labels") or {}
            if (labels.get("ship_id") == ship_id or _crane_container(d).get("name")
                    == f"{CRANE_CONTAINER_PREFIX}{ship_id}"):
                return d
    return cranes[0] if cranes else None


def crane_pods(pods, deployment):
    """The pods the Deployment selects, else every pod triage calls crane."""
    selector = ((deployment or {}).get("spec") or {}).get("selector") or {}
    want = selector.get("matchLabels") or {}
    if want:
        return [p for p in pods or []
                if all(((p.get("metadata") or {}).get("labels") or {}).get(k) == v
                       for k, v in want.items())]
    return [p for p in pods or [] if triage_mod.pod_role(p) == "crane"]


def read_cluster(cli, namespace, ship_id=None):
    """Everything the cluster and configuration stages judge, read once."""
    unread = {}
    deployments, why = _get(cli, namespace, "deployments")
    if deployments is None:
        unread["deployments"] = why
    else:
        deployments = deployments.get("items", [])
    pods, why = _get(cli, namespace, "pods")
    if pods is None:
        unread["pods"] = why
    else:
        pods = pods.get("items", [])

    deployment = pick_deployment(deployments, ship_id)
    cm_name, secret_name = env_sources(deployment) if deployment else (None, None)
    # Unread Deployment: the ConfigMap and Secret are still read by the
    # bundle's names.
    cm_name = cm_name or CONFIGMAP_NAME
    if deployments is None:
        secret_name = SECRET_NAME
    configmap, why = _get(cli, namespace, "configmap", cm_name)
    if configmap is None:
        unread["configmap"] = why
    keys = None
    if secret_name:
        keys, why = secret_keys(cli, namespace, secret_name)
        if keys is None:
            unread["secret"] = why
    ca_name = ca_volume(deployment) if deployment else None
    ca_cm = None
    if ca_name:
        ca_cm, why = _get(cli, namespace, "configmap", ca_name)
        if ca_cm is None:
            unread["ca configmap"] = why
    data = (configmap or {}).get("data") or {}
    in_pod = None
    running = [p for p in crane_pods(pods, deployment)
               if (p.get("status") or {}).get("phase") == "Running"]
    if data.get("REQUESTS_CA_BUNDLE") and running:
        count, why = ca_certificates_in_pod(
            cli, namespace, running[0]["metadata"]["name"], data["REQUESTS_CA_BUNDLE"])
        in_pod = count
        if count is None:
            unread["ca in pod"] = why
    return Cluster(deployments, pods, deployment, cm_name, configmap, secret_name,
                   keys, ca_name, ca_cm, in_pod, unread)


# -- stage 1: the agent in the cluster ----------------------------------------------

def _unread(name, cluster, section, what):
    return check(name, UNREAD, f"{what} could not be read: {cluster.unread.get(section)}",
                 "Run this with a kubectl or oc context that may read "
                 f"{section} in the namespace. A refused read is not a failure.")


def deployment_check(cluster, namespace):
    if cluster.deployments is None:
        return _unread("crane-deployment", cluster, "deployments", "Deployments")
    cranes = [d for d in cluster.deployments if is_crane_deployment(d)]
    if not cranes:
        return check("crane-deployment", FAIL,
                     f"no crane Deployment in namespace {namespace}",
                     "Apply the bundle to this namespace, or pass the namespace "
                     "it was applied to.")
    d = cluster.deployment
    name = d["metadata"]["name"]
    want = (d.get("spec") or {}).get("replicas", 1)
    ready = (d.get("status") or {}).get("readyReplicas") or 0
    extra = (f"; {len(cranes)} crane Deployments share this namespace, and this "
             f"report reads {name}" if len(cranes) > 1 else "")
    if want == 0:
        return check("crane-deployment", FAIL, f"{name} is scaled to 0 replicas{extra}",
                     f"Scale it back: kubectl -n {namespace} scale deploy/{name} "
                     f"--replicas=1.")
    if ready < 1:
        conds = [f"{c.get('type')}={c.get('status')}: {c.get('message')}"
                 for c in (d.get("status") or {}).get("conditions") or []
                 if c.get("status") != "True"]
        return check("crane-deployment", FAIL,
                     f"{name}: {ready} of {want} replicas ready"
                     + (f" ({'; '.join(conds)})" if conds else "") + extra,
                     "The crane-pod check and the triage below say why the pod "
                     "is not ready.")
    status = WARN if len(cranes) > 1 else PASS
    return check("crane-deployment", status, f"{name}: {ready} of {want} replicas ready{extra}",
                 "Pass --ship-id to choose the agent to check." if extra else None)


def pod_check(cluster):
    if cluster.pods is None:
        return _unread("crane-pod", cluster, "pods", "Pods")
    pods = crane_pods(cluster.pods, cluster.deployment)
    if not pods:
        return check("crane-pod", FAIL, "no crane pod in the namespace",
                     "The Deployment could not create its pod. The triage below "
                     "names why (quota, admission, service account).")
    worst = None
    for p in pods:
        name = p["metadata"]["name"]
        status = p.get("status") or {}
        for cs in status.get("containerStatuses") or []:
            waiting = (cs.get("state") or {}).get("waiting") or {}
            restarts = cs.get("restartCount") or 0
            last = (cs.get("lastState") or {}).get("terminated") or {}
            if waiting.get("reason") and waiting["reason"] not in triage_mod.BENIGN_WAITING:
                return check("crane-pod", FAIL,
                             f"{name}: {waiting['reason']}"
                             + (f": {waiting.get('message')}" if waiting.get("message") else "")
                             + (f", {restarts} restarts" if restarts else ""),
                             "The triage below matches this reason to its fix.")
            if not cs.get("ready"):
                return check("crane-pod", FAIL,
                             f"{name} is {status.get('phase')} and not ready",
                             "Crane answers its readiness probe once it has "
                             "started. The triage below reads its log.")
            if restarts and worst is None:
                why = (f"; last exit: {last.get('reason')}, code {last.get('exitCode')}, "
                       f"at {last.get('finishedAt')}" if last else "")
                worst = check("crane-pod", WARN,
                              f"{name} is running and ready, and has restarted "
                              f"{restarts} times{why}",
                              "A restart that does not recur is harmless. If the "
                              "count grows, run bzm-opl-gen triage, which reads "
                              "the log of the run before the restart.")
    if worst:
        return worst
    images = sorted({c.get("image") for p in pods
                     for c in (p.get("spec") or {}).get("containers") or []})
    return check("crane-pod", PASS,
                 f"{', '.join(p['metadata']['name'] for p in pods)} running and "
                 f"ready, no restarts, image {', '.join(images)}")


def configmap_check(cluster):
    if cluster.configmap is None:
        return _unread("configmap", cluster, "configmap",
                       f"The ConfigMap {cluster.configmap_name}")
    if not cluster.configmap:
        return check("configmap", FAIL,
                     f"the ConfigMap {cluster.configmap_name} is not in the namespace",
                     "Apply the bundle's ConfigMap. Crane reads its whole "
                     "configuration from it and does not start without it.")
    data = cluster.configmap.get("data") or {}
    return check("configmap", PASS,
                 f"{cluster.configmap_name} holds {len(data)} variables")


def credential_check(cluster):
    data = (cluster.configmap or {}).get("data") or {}
    # With the Deployment unread, the Secret was read by the bundle's name and
    # may not be the one crane references.
    guessed = cluster.deployments is None
    if cluster.secret_name and not (guessed and not cluster.secret_keys):
        if cluster.secret_keys is None:
            return _unread("credential", cluster, "secret",
                           f"The Secret {cluster.secret_name} (key names only)")
        if cluster.secret_keys == {}:
            return check("credential", FAIL,
                         f"the Secret {cluster.secret_name} that crane reads is "
                         f"not in the namespace",
                         "Apply the bundle's Secret. Crane cannot start without "
                         "it (CreateContainerConfigError).")
        if "AUTH_TOKEN" not in cluster.secret_keys:
            return check("credential", FAIL,
                         f"the Secret {cluster.secret_name} holds no AUTH_TOKEN "
                         f"(keys: {', '.join(cluster.secret_keys) or 'none'})",
                         "Apply the bundle's Secret again, or regenerate the bundle.")
        if "AUTH_TOKEN" in data:
            return check("credential", WARN,
                         "AUTH_TOKEN is in the ConfigMap as well as the Secret, "
                         "so anyone who may read ConfigMaps can read it",
                         "Remove AUTH_TOKEN from the ConfigMap.")
        return check("credential", PASS,
                     f"the Secret {cluster.secret_name} holds AUTH_TOKEN "
                     f"(its value is not read)")
    if cluster.configmap is None:
        return _unread("credential", cluster, "configmap", "The ConfigMap")
    token = data.get("AUTH_TOKEN")
    if guessed and not token:
        return _unread("credential", cluster, "deployments",
                       "The Deployment, which names the Secret crane reads,")
    if not token or markers.is_placeholder(token):
        return check("credential", FAIL,
                     "no AUTH_TOKEN reaches crane: no Secret is referenced and "
                     "the ConfigMap holds " + ("a marker" if token else "none"),
                     "Regenerate the bundle with the agent's token and apply it.")
    return check("credential", WARN,
                 "AUTH_TOKEN is in the ConfigMap, where anyone who may read "
                 "ConfigMaps can read it",
                 "Regenerate with use_secret on, which moves it to a Secret.")


def resolve_ids(cluster, harbor_id=None, ship_id=None):
    """(harbor, ship, check). The deployed ids win: they name the agent that
    runs in the namespace, and a given id that differs is a FAIL."""
    data = (cluster.configmap or {}).get("data") or {}
    labels = ((cluster.deployment or {}).get("metadata") or {}).get("labels") or {}
    got = {"location": data.get("HARBOR_ID") or labels.get("harbor_id"),
           "agent": data.get("SHIP_ID") or labels.get("ship_id")}
    given = {"location": harbor_id, "agent": ship_id}
    where = "the ConfigMap" if data.get("HARBOR_ID") else "the Deployment's labels"
    marked = [v for v in got.values() if v and markers.is_placeholder(v)]
    if marked:
        return (harbor_id, ship_id, check(
            "agent-ids", FAIL, f"the deployed bundle carries {', '.join(marked)} "
            f"where the ids belong", "Fill in the location and agent ids, "
            "regenerate and apply the bundle."))
    wrong = [k for k in got if got[k] and given[k] and str(got[k]) != str(given[k])]
    if wrong:
        return (got["location"], got["agent"], check(
            "agent-ids", FAIL,
            f"the namespace runs agent {got['agent']} of location {got['location']}, "
            f"not the {' and '.join(wrong)} id given "
            f"({', '.join(str(given[k]) for k in wrong)}). This report checks the "
            f"agent in the namespace",
            "Pass the ids of the agent deployed here, or the namespace the "
            "other agent was deployed to."))
    harbor, ship = got["location"] or harbor_id, got["agent"] or ship_id
    if not (harbor and ship):
        return (harbor, ship, check(
            "agent-ids", UNREAD, "the location and agent ids could not be read "
            "from the cluster", "Pass --harbor-id and --ship-id."))
    source = where if got["location"] and got["agent"] else "the command line"
    return harbor, ship, check("agent-ids", PASS,
                               f"location {harbor}, agent {ship}, read from {source}")


def cluster_checks(cluster, namespace, id_check):
    return [deployment_check(cluster, namespace), pod_check(cluster),
            configmap_check(cluster), credential_check(cluster), id_check]


# -- stage 2: the agent in BlazeMeter ---------------------------------------------

def agent_check(status, error=None, missing=False, proxy=None):
    """`status` is core.agent_status's answer; `error` a refused read, and
    `missing` the answer that the agent is not in the location. `proxy` is
    configured_proxy()'s answer, named as the first suspect when crane is
    silent."""
    suspect = (f"The proxy crane is configured with ({proxy}) is the first "
               f"suspect: crane can hang at its first call to BlazeMeter "
               f"while its pod still shows Ready. " if proxy else "")
    if missing:
        return check("agent", FAIL, f"BlazeMeter does not know this agent: {error}",
                     "Check the ids against the agent in BlazeMeter. A deleted "
                     "agent or location stops the deployed one from reporting.")
    if error is not None:
        return check("agent", UNREAD, f"BlazeMeter could not be read: {error}",
                     "Check the API key and try again.")
    if status is None:
        return check("agent", SKIP, "no agent ids to ask about")
    version = status.get("installed_version")
    about = (f"state {status.get('state')}, last heartbeat "
             f"{status.get('heartbeat_age_s')}s ago"
             + (f", crane {version}" if version else ""))
    if status.get("online"):
        return check("agent", PASS, f"reporting: {about}")
    if status.get("heartbeat_age_s") is None:
        return check("agent", FAIL,
                     f"the agent has never reported (state {status.get('state')})",
                     "Crane has not reached BlazeMeter. " + suspect
                     + "The triage below reads crane's log for the cause: "
                     "egress, proxy, CA trust or the token.")
    return check("agent", FAIL, f"not reporting: {about}",
                 "Crane stopped reporting. " + (suspect or
                 "The usual causes are the network or proxy between crane and "
                 "BlazeMeter, where crane can hang at its first call while its "
                 "pod still shows Ready. ") + "A revoked token stops it too: a "
                 "new token issued for this agent revokes the old one. The "
                 "triage below reads crane's log for which.")


def location_check(facts, error=None):
    if error is not None:
        return check("location", UNREAD, f"the location could not be read: {error}")
    if facts is None:
        return check("location", SKIP, "no location id to ask about")
    funcs = ", ".join(facts.get("func_ids") or []) or "none"
    detail = (f"{facts.get('harbor_name') or facts.get('harbor_id')}: functionalities "
              f"{funcs}; {facts.get('slots')} slots; "
              f"{facts.get('threads_per_engine')} threads per engine; "
              f"agents in the location: {len(facts.get('ships') or [])}")
    missing = [f for f, k in (("slots", "slots"), ("threadsPerEngine", "threads_per_engine"))
               if not facts.get(k)]
    if missing:
        return check("location", FAIL, f"{detail}; {' and '.join(missing)} not set",
                     "Set them in BlazeMeter (Settings, Private Locations). Until "
                     "then every test start is refused with 403 Not enough "
                     "available resources.")
    if not facts.get("func_ids"):
        return check("location", WARN, detail,
                     "Enable a functionality on the location in BlazeMeter.")
    return check("location", PASS, detail)


# -- stage 3: the configuration crane runs with -------------------------------------

def _cm_or_skip(name, cluster):
    if cluster.configmap is None:
        return _unread(name, cluster, "configmap", "The ConfigMap")
    if not cluster.configmap:
        return check(name, SKIP, "there is no ConfigMap to read")
    return None


def engine_size_of(data):
    """(cpu millicores, memory bytes) the ConfigMap limits engines to, and the
    requests it sets, as {limits, requests}; a value None where absent. Raises
    ValueError for a value that is not a quantity."""
    def cpu(k):
        return parse_cpu(data[k]) if data.get(k) else None

    def mem(k, mib=False):
        if not data.get(k):
            return None
        return int(data[k]) * 1024 ** 2 if mib else parse_memory(data[k])

    return {"limits": (cpu("KUBERNETES_RESOURCES_LIMITS_CPU"),
                       mem("KUBERNETES_RESOURCES_LIMITS_MEMORY")),
            "requests": (cpu("KUBERNETES_RESOURCES_DEFAULT_CPU"),
                         mem("KUBERNETES_RESOURCES_DEFAULT_MEM", mib=True))}


def _size(cpu, mem):
    return f"{format_cpu(cpu)} CPU / {format_memory(mem)}"


SIZING_FIX = ("Regenerate the bundle with this version and apply it: it writes "
              "KUBERNETES_RESOURCES_DEFAULT_CPU and _MEM equal to the engine "
              "limits, so every engine is Guaranteed and the scheduler places it "
              "by what it uses.")


def engine_sizing_check(cluster):
    skip = _cm_or_skip("engine-sizing", cluster)
    if skip:
        return skip
    data = cluster.configmap.get("data") or {}
    try:
        size = engine_size_of(data)
    except ValueError as e:
        return check("engine-sizing", FAIL, f"an engine size is not a quantity: {e}",
                     SIZING_FIX)
    (lcpu, lmem), (rcpu, rmem) = size["limits"], size["requests"]
    absent = [k for k, v in (("KUBERNETES_RESOURCES_LIMITS_CPU", lcpu),
                             ("KUBERNETES_RESOURCES_LIMITS_MEMORY", lmem),
                             ("KUBERNETES_RESOURCES_DEFAULT_CPU", rcpu),
                             ("KUBERNETES_RESOURCES_DEFAULT_MEM", rmem)) if v is None]
    if absent:
        costs = []
        if rcpu is None or rmem is None:
            costs.append("without requests crane asks for 250m / 256Mi per engine")
        if lcpu is None or lmem is None:
            costs.append("without limits an engine may use the whole node")
        return check("engine-sizing", FAIL,
                     f"the ConfigMap does not set {', '.join(absent)}: "
                     f"{', and '.join(costs)}", SIZING_FIX)
    if (lcpu, lmem) != (rcpu, rmem):
        return check("engine-sizing", FAIL,
                     f"engines request {_size(rcpu, rmem)} but are limited to "
                     f"{_size(lcpu, lmem)}, so the scheduler packs them tighter "
                     f"than they run", SIZING_FIX)
    return check("engine-sizing", PASS,
                 f"engines request and are limited to {_size(lcpu, lmem)}")


def engine_override_check(cluster, facts, error=None):
    """The location's overrideCPU/overrideMemory replace the ConfigMap's
    requests, so where set they must equal the limits."""
    skip = _cm_or_skip("engine-overrides", cluster)
    if skip:
        return skip
    if error is not None:
        return check("engine-overrides", UNREAD,
                     f"the location's engine overrides could not be read: {error}")
    if facts is None:
        return check("engine-overrides", SKIP, "no location id to ask about")
    cpu, mem = facts.get("override_cpu"), facts.get("override_memory")
    if not cpu and not mem:
        return check("engine-overrides", PASS,
                     "the location sets no engine overrides, so the ConfigMap's "
                     "requests apply")
    try:
        lcpu, lmem = engine_size_of(cluster.configmap.get("data") or {})["limits"]
    except ValueError:
        return check("engine-overrides", SKIP, "the ConfigMap's limits are not quantities")
    off = []
    if cpu and lcpu is not None and int(round(float(cpu) * 1000)) != lcpu:
        off.append(f"overrideCPU {cpu} against a CPU limit of {format_cpu(lcpu)}")
    if mem and lmem is not None and int(float(mem)) * 1024 ** 2 != lmem:
        off.append(f"overrideMemory {mem} (MB) against a memory limit of "
                   f"{format_memory(lmem)}")
    if off:
        return check("engine-overrides", FAIL,
                     "the location's engine overrides replace the requests and do "
                     "not equal the limits: " + "; ".join(off),
                     "Clear overrideCPU and overrideMemory on the location in "
                     "BlazeMeter, or set them to the engine limits.")
    return check("engine-overrides", PASS,
                 f"the location's overrides (CPU {cpu}, memory {mem} MB) equal the limits")


def ca_check(cluster):
    skip = _cm_or_skip("ca-trust", cluster)
    if skip:
        return skip
    data = cluster.configmap.get("data") or {}
    path = data.get("REQUESTS_CA_BUNDLE")
    if not path:
        return check("ca-trust", PASS,
                     "no CA bundle is configured. Crane uses its own trust store, "
                     "which is enough unless a TLS-inspecting proxy is on the path")
    key = path.rsplit("/", 1)[-1]
    fix = ("Regenerate with one CA trust option (ca_bundle, ca_bundle_slot, "
           "ca_existing_configmap or ca_openshift_inject) and apply the bundle.")
    if cluster.deployment is not None and not cluster.ca_name:
        return check("ca-trust", FAIL,
                     f"REQUESTS_CA_BUNDLE names {path}, but crane mounts no "
                     f"ConfigMap at {CA_MOUNT_PATH}", fix)
    engines = data.get("KUBERNETES_CA_BUNDLE_MOUNT") or ""
    if f"={key}" not in engines:
        return check("ca-trust", FAIL,
                     f"KUBERNETES_CA_BUNDLE_MOUNT does not hand {key} to the "
                     f"engines, so they cannot trust the CA crane trusts", fix)
    if cluster.ca_name:
        if cluster.ca_configmap is None:
            return _unread("ca-trust", cluster, "ca configmap",
                           f"The CA ConfigMap {cluster.ca_name}")
        if not cluster.ca_configmap:
            return check("ca-trust", FAIL,
                         f"the CA ConfigMap {cluster.ca_name} is not in the "
                         f"namespace, so crane cannot start", fix)
        pem = ((cluster.ca_configmap.get("data") or {}).get(key) or "")
        if "BEGIN CERTIFICATE" not in pem:
            return check("ca-trust", FAIL,
                         f"the CA ConfigMap {cluster.ca_name} holds no certificate "
                         f"under the key {key}", fix)
    source = f" from the ConfigMap {cluster.ca_name}" if cluster.ca_name else ""
    if cluster.ca_in_pod is None:
        why = cluster.unread.get("ca in pod") or "crane is not running"
        return check("ca-trust", PASS,
                     f"{path} is mounted{source}; the file inside the pod was not "
                     f"read ({why})")
    if cluster.ca_in_pod < 1:
        return check("ca-trust", FAIL,
                     f"{path} is missing or holds no certificates inside the "
                     f"crane pod", fix)
    return check("ca-trust", PASS,
                 f"{path} holds {cluster.ca_in_pod} certificates inside the crane "
                 f"pod, mounted{source}; engines get it through "
                 f"KUBERNETES_CA_BUNDLE_MOUNT")


def _proxy_host(url):
    """The proxy's address without its credentials."""
    try:
        parts = urllib.parse.urlsplit(url)
        return f"{parts.hostname}:{parts.port}" if parts.port else (parts.hostname or "?")
    except ValueError:
        return "an unparseable URL"


def configured_proxy(cluster):
    """The proxy crane runs with, without credentials: its address from the
    ConfigMap, "set in the Secret <name>", or None for none or unread."""
    data = (cluster.configmap or {}).get("data") or {}
    hosts = sorted({_proxy_host(data[k]) for k in ("HTTPS_PROXY", "HTTP_PROXY")
                    if data.get(k)})
    if hosts:
        return ", ".join(hosts)
    if any(k in (cluster.secret_keys or []) for k in ("HTTPS_PROXY", "HTTP_PROXY")):
        return f"set in the Secret {cluster.secret_name}"
    return None


def proxy_check(cluster):
    skip = _cm_or_skip("proxy", cluster)
    if skip:
        return skip
    data = cluster.configmap.get("data") or {}
    in_secret = [k for k in ("HTTP_PROXY", "HTTPS_PROXY")
                 if k in (cluster.secret_keys or [])]
    in_cm = [k for k in ("HTTP_PROXY", "HTTPS_PROXY") if data.get(k)]
    if not in_secret and not in_cm:
        return check("proxy", PASS, "no proxy is configured: crane and engines "
                     "reach BlazeMeter directly")
    leaked = livetest.proxy_credentials_in(data)
    if leaked:
        return check("proxy", WARN,
                     f"{', '.join(leaked)} carry credentials in the ConfigMap, "
                     f"where anyone who may read ConfigMaps can read them",
                     "Regenerate with use_secret on, which moves them to a Secret.")
    no_proxy = data.get("NO_PROXY") or ""
    hosts = ", ".join(sorted({_proxy_host(data[k]) for k in in_cm})) or \
        f"set in the Secret {cluster.secret_name}"
    if "kubernetes.default" not in no_proxy:
        return check("proxy", WARN,
                     f"proxy {hosts}; NO_PROXY ({no_proxy or 'unset'}) does not "
                     f"name kubernetes.default, so crane's calls to the "
                     f"Kubernetes API may go through the proxy",
                     "Add kubernetes.default and the API service address to the "
                     "no_proxy key of the proxy option, then regenerate.")
    return check("proxy", PASS, f"proxy {hosts}; NO_PROXY {no_proxy}")


def registry_check(cluster, facts, error=None):
    skip = _cm_or_skip("registry", cluster)
    if skip:
        return skip
    data = cluster.configmap.get("data") or {}
    reg = data.get("DOCKER_REGISTRY")
    auto = (data.get("AUTO_KUBERNETES_UPDATE") or "").lower() == "true"
    if not reg or reg == PUBLIC_REGISTRY:
        return check("registry", PASS,
                     f"images come from BlazeMeter's public registry "
                     f"({PUBLIC_REGISTRY}); auto-update is "
                     f"{'on' if auto else 'off'}")
    host = reg.split("/")[0]
    problems = []
    try:
        missing = (livetest.missing_image_overrides(data, facts)
                   if facts is not None else None)
    except ValueError:
        return check("registry", FAIL, "IMAGE_OVERRIDES is not valid JSON",
                     "Regenerate the bundle with private_registry set and apply it.")
    if missing:
        problems.append(f"IMAGE_OVERRIDES does not name {', '.join(missing)}, "
                        f"so crane pulls those images from the public registry")
    crane_image = _crane_container(cluster.deployment or {}).get("image") or ""
    if crane_image and not crane_image.startswith(host):
        problems.append(f"crane's own image {crane_image} is not from {host}")
    if auto:
        problems.append("auto-update is on, so crane replaces its image with a "
                        "newer tag that the mirror may not hold yet")
    if missing:
        return check("registry", FAIL, f"private registry {reg}; " + "; ".join(problems),
                     "Mirror the images (bzm-opl-gen images --mirror), then "
                     "regenerate with private_registry and apply the bundle.")
    if facts is None:
        why = f": {error}" if error else ""
        problems.append(f"the location's image list could not be read{why}, so "
                        f"IMAGE_OVERRIDES coverage is not checked")
    if problems:
        return check("registry", WARN, f"private registry {reg}; " + "; ".join(problems),
                     "Check the images with bzm-opl-gen images --verify " + reg + ".")
    return check("registry", PASS,
                 f"private registry {reg}; IMAGE_OVERRIDES names every image the "
                 f"location uses; auto-update is off")


def config_checks(cluster, facts, facts_error=None):
    return [engine_sizing_check(cluster),
            engine_override_check(cluster, facts, facts_error),
            ca_check(cluster), proxy_check(cluster),
            registry_check(cluster, facts, facts_error)]


# -- stage 4: one real engine run -------------------------------------------------

def test_target(test, harbor_id):
    """(check, other locations): does the test already run on this location?
    It is never repointed here."""
    key = f"harbor-{harbor_id}"
    lists = [test.get(k) for k in ("executions", "overrideExecutions")
             if test.get(k)]
    fix = (f"In BlazeMeter, open the test and choose this private location "
           f"under Load Distribution. For a Taurus script, name it under "
           f"execution: locations: {key}: 1.")
    if not lists:
        return check("test-target", UNREAD,
                     "the test carries its locations in its script, which the "
                     "API does not show. If the script does not name "
                     f"{key}, no engine starts in this namespace"), []
    names = [set((e.get("locations") or {}).keys()) for execs in lists for e in execs]
    others = sorted(set().union(*names) - {key})
    if not all(key in n for n in names):
        return check("test-target", FAIL,
                     f"the test does not run on this location ({key}); it runs on "
                     f"{', '.join(others) or 'no location'}. This check never "
                     f"repoints a test", fix), others
    also = f", and also on {', '.join(others)}" if others else ""
    return check("test-target", PASS, f"the test runs on {key}{also}"), others


def deployed_options(data, cluster):
    """The options the deployed ConfigMap implies, as far as livetest's engine
    checks read them."""
    reg = data.get("DOCKER_REGISTRY")
    proxied = any(data.get(k) or k in (cluster.secret_keys or [])
                  for k in ("HTTP_PROXY", "HTTPS_PROXY"))
    return {**DEFAULT_OPTIONS,
            "engine_cpu_limit": data.get("KUBERNETES_RESOURCES_LIMITS_CPU"),
            "engine_mem_limit": data.get("KUBERNETES_RESOURCES_LIMITS_MEMORY"),
            "private_registry": reg if reg and reg != PUBLIC_REGISTRY else None,
            "ca_existing_configmap": (cluster.ca_name or "deployed")
            if data.get("REQUESTS_CA_BUNDLE") else None,
            "proxy": {"https": "deployed"} if proxied else None}


def engine_pod_checks(pod, data, cluster):
    """What the engine was given: its limits against the ConfigMap, its QoS
    class, its image and trust, and its heap against its limit."""
    opts = deployed_options(data, cluster)
    name = pod["metadata"]["name"]
    out = []
    if data.get("KUBERNETES_RESOURCES_LIMITS_CPU") and data.get("KUBERNETES_RESOURCES_LIMITS_MEMORY"):
        try:
            fails = livetest.assert_engine_size(pod, opts)
            want = _size(*engine_size(opts))
        except ValueError as e:
            fails, want = [str(e)], None
        out.append(check("engine-size", FAIL, "; ".join(fails),
                         "Crane reads the limits from the ConfigMap when it "
                         "starts. Restart crane after changing it.")
                   if fails else
                   check("engine-size", PASS,
                         f"{name} is limited to {want}, as the ConfigMap says"))
    else:
        out.append(check("engine-size", SKIP, "the ConfigMap names no engine limits"))
    qos = (pod.get("status") or {}).get("qosClass")
    if not qos:
        out.append(check("engine-qos", UNREAD, f"{name} reports no QoS class yet"))
    elif qos == "Guaranteed":
        out.append(check("engine-qos", PASS,
                         f"{name} is Guaranteed: it requests what it is limited to"))
    else:
        gap = livetest.engine_request_gap(pod)
        out.append(check("engine-qos", FAIL,
                         f"{name} is {qos}, not Guaranteed"
                         + (f": {gap}" if gap else ""), SIZING_FIX))
    images = [c.get("image") for c in pod["spec"].get("containers", [])]
    fails = livetest.assert_engine_config(pod, opts)
    out.append(check("engine-config", FAIL, "; ".join(fails),
                     "Regenerate the bundle with the option each line names and "
                     "apply it; crane passes these to every engine.")
               if fails else
               check("engine-config", PASS,
                     f"image {', '.join(images)}"
                     + ("; CA bundle mounted" if opts["ca_existing_configmap"] else "")
                     + ("; HTTPS_PROXY set" if opts["proxy"] else "")))
    heap = livetest.engine_heap_bytes(pod)
    if heap is not None:
        # Otherwise engine_run reads the heap off the running JVM.
        out.append(heap_check(heap, pod, livetest.engine_heap_note(pod)))
    return out


def heap_check(heap, pod, note):
    """The engine heap against the pod's memory limit; UNREAD where no heap
    was read."""
    limit = next((parse_memory(m) for m in
                  ((c.get("resources") or {}).get("limits", {}).get("memory")
                   for c in pod["spec"].get("containers", [])) if m), None)
    if heap is None:
        return check("engine-heap", UNREAD, note)
    if limit is not None and (heap >= limit or heap * 2 <= limit):
        return check("engine-heap", WARN, note,
                     "Set the engine heap on the location in BlazeMeter to "
                     "about three quarters of the memory limit.")
    return check("engine-heap", PASS, note)


def _engine_names(pods):
    return {p["metadata"]["name"] for p in pods or []
            if triage_mod.pod_role(p) == "engine"}


def wait_for_new_engine(cli, namespace, before, timeout, poll=POLL_S):
    """The first engine pod not in `before`, or None when none appeared."""
    def new():
        pods, _ = _get(cli, namespace, "pods")
        for p in (pods or {}).get("items", []):
            if (triage_mod.pod_role(p) == "engine"
                    and p["metadata"]["name"] not in before):
                return p
        return None
    return kube.poll_until(new, timeout, poll)


def run_plan(test, test_id, harbor_id, namespace, others, engine_timeout,
             run_timeout):
    """What --run-test is about to do, said before it does it."""
    name = test.get("name") or "unnamed"
    also = (f" The test also runs on {', '.join(others)}, which this check "
            f"does not watch." if others else "")
    return (f"Starting test {test_id} ({name}) now. This is a real run in your "
            f"BlazeMeter account, with the test's own load settings, on location "
            f"{harbor_id}.{also} This check waits up to {engine_timeout}s "
            f"for crane to start an engine in namespace {namespace}, and up to "
            f"{run_timeout}s for the run to end. A run that has not ended by "
            f"then is stopped.")


def _refused(e):
    text = str(e)
    if "Not enough available resources" in text or getattr(e, "status", None) == 403:
        return (f"BlazeMeter refused to start the test: {text}",
                "Every slot on the location is busy, or the location lacks "
                "slots or threads per engine. Wait for the running test to end, "
                "or add slots in BlazeMeter.")
    return (f"BlazeMeter refused to start the test: {text}",
            "Start the test once in BlazeMeter to read the full refusal.")


def engine_run(client, cli, namespace, test_id, harbor_id, cluster, blocked=None,
               notify=print, engine_timeout=DEFAULT_ENGINE_TIMEOUT,
               run_timeout=DEFAULT_RUN_TIMEOUT, poll=POLL_S):
    """Start the customer's own test on their location and read what crane
    gave the engine. `blocked` is why an earlier stage rules the run out."""
    try:
        test = client.test(test_id) or {}
    except api.BzmApiError as e:
        return [check("test-target", FAIL, f"test {test_id} could not be read: {e}",
                      "Check the test id and that this API key's user can see it.")]
    target, others = test_target(test, harbor_id)
    out = [target]
    if target.status == FAIL:
        return out
    if blocked:
        return out + [check("engine-run", SKIP, f"not started: {blocked}",
                            "Fix the failures above, then run this again.")]
    data = (cluster.configmap or {}).get("data") or {}
    notify(run_plan(test, test_id, harbor_id, namespace, others,
                    engine_timeout, run_timeout))
    try:
        master_id = client.start_test(test_id)
    except api.BzmApiError as e:
        detail, fix = _refused(e)
        return out + [check("engine-run", FAIL, detail, fix)]
    notify(f"started test {test_id}: master {master_id}")
    status = None
    try:
        pod = wait_for_new_engine(cli, namespace, _engine_names(cluster.pods),
                                  engine_timeout, poll)
        if not pod:
            return out + [check("engine-pod", FAIL,
                                f"crane started no engine pod within {engine_timeout}s "
                                f"of master {master_id}",
                                "The triage below reads crane's log, where a "
                                "refused engine (quota, Pod Security, webhook) "
                                "shows.")]
        if target.status == UNREAD:
            # One run at a time per agent, so this run's engine proves the target.
            out[0] = check("test-target", PASS,
                           f"crane started an engine in {namespace} for this "
                           f"run, so the test's script names this location")
        out.append(check("engine-pod", PASS,
                         f"{pod['metadata']['name']} ({(pod.get('status') or {}).get('phase')})"))
        out += engine_pod_checks(pod, data, cluster)
        # Taurus puts the JMeter heap on its own command line once the run is
        # under way, so a heap the pod spec lacks is read off the running JVM.
        watch = (None if livetest.engine_heap_bytes(pod) is not None
                 else livetest.EngineHeapWatch(cli, namespace, pod))
        try:
            status = livetest.wait_master_done(
                client, master_id, run_timeout,
                while_running=watch.poll if watch else None)
        except api.BzmApiError as e:
            out.append(check("run-status", UNREAD, f"the run's status could not be read: {e}"))
            return out
        finally:
            if watch:
                out.append(heap_check(watch.jmeter_xmx(), pod, watch.note()))
        if status != "ENDED":
            out.append(check("run-status", FAIL,
                             f"the run is {status}, not ENDED, after {run_timeout}s"
                             if status not in TERMINAL else
                             f"the run finished as {status}, not ENDED",
                             "Open the report in BlazeMeter for the run's own "
                             "errors; the triage below reads the namespace."))
            return out
        out.append(check("run-status", PASS, f"master {master_id} ENDED"))
        out += _result_checks(client, master_id)
        return out
    finally:
        if status not in TERMINAL:
            try:
                client.stop_master(master_id)
                notify(f"stopped master {master_id}: it had not ended")
            except api.BzmApiError:
                notify(f"master {master_id} could not be stopped; stop it in "
                       f"BlazeMeter")


def _result_checks(client, master_id):
    out = []
    try:
        hits, avg, errors = livetest.run_summary(client, master_id)
    except (api.BzmApiError, AttributeError, TypeError, IndexError) as e:
        out.append(check("run-samples", UNREAD, f"the run summary could not be read: {e}"))
    else:
        fails = livetest.summary_failures(hits, errors)
        about = f"{hits} samples, average {avg} ms, {errors} failed"
        out.append(check("run-samples", FAIL, f"{about}; {fails[0]}",
                         "A test whose samplers make real requests is what "
                         "proves the engine's egress. Check the target is "
                         "reachable from the cluster.")
                   if fails else check("run-samples", PASS, about))
    try:
        codes = livetest.taurus_exit_codes(client, master_id)
    except api.BzmApiError as e:
        out.append(check("engine-exit", UNREAD, f"the run's events could not be read: {e}"))
        return out
    if not codes:
        out.append(check("engine-exit", UNREAD,
                         "the run's events carry no Taurus exit status, so whether "
                         "the engine finished or stopped partway is not known"))
    elif any(c != "0" for c in codes):
        out.append(check("engine-exit", FAIL,
                         livetest.exit_code_failures(codes, master_id)[0],
                         "An engine that stops partway usually ran out of memory. "
                         "Raise engine_mem_limit, or lower the engine heap on the "
                         "location."))
    else:
        out.append(check("engine-exit", PASS, "Taurus exited 0"))
    return out


# -- the report -------------------------------------------------------------------

def counts(stages):
    c = collections.Counter(ch["status"] for s in stages for ch in s["checks"])
    return {s: c[s] for s in STATUSES}


def summary_line(stages):
    c = counts(stages)
    parts = [f"{c[PASS]} passed", f"{c[WARN]} warnings", f"{c[UNREAD]} unread"]
    if c[SKIP]:
        parts.append(f"{c[SKIP]} skipped")
    parts.append(f"{c[FAIL]} failures" if c[FAIL] else "no failures")
    line = ", ".join(parts)
    if c[FAIL]:
        line += ". The agent is not ready to run tests: each FAIL names its fix"
    elif not c[PASS] and not c[WARN]:
        line += ". Nothing could be read, so this report cannot show a failure"
    return line


def stage(name, checks):
    return {"stage": name, "title": STAGE_TITLES[name],
            "checks": [c._asdict() for c in checks]}


def _wrap(text, indent=6):
    return textwrap.fill(text, width=100, initial_indent=" " * indent,
                         subsequent_indent=" " * indent)


def report(doc):
    """Print core.smoke's document for a person."""
    print(f"smoke: namespace {doc['namespace']}, location {doc['harbor_id']}, "
          f"agent {doc['ship_id']}")
    for n, s in enumerate(doc["stages"], 1):
        print(f"\n{n}. {s['title']}")
        width = max((len(c["name"]) for c in s["checks"]), default=0)
        for c in s["checks"]:
            print(f"{c['status']:<6}  {c['name']:<{width}}  {c['detail']}")
            if c["fix"] and c["status"] != PASS:
                print(_wrap(f"fix: {c['fix']}", indent=8 + width + 2))
    if doc["triage"] is not None:
        print("\ntriage, because a check failed:")
        if doc["triage"].get("error"):
            print(_wrap(doc["triage"]["error"]))
        else:
            triage_mod.report(doc["triage"])
    print(f"\n{doc['summary']}")
