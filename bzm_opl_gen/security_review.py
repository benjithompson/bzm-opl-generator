"""SECURITY-REVIEW.md: one document for a change-approval board or a security
team, saying what this bundle's agent runs, what it reaches and what it may do.

Each section reads the source the bundle is rendered from. The RBAC rules,
security contexts and Secret keys are parsed out of the rendered objects; the
images come from the catalogue, the hosts and sizes from footprint.
"""

import re
import textwrap
import urllib.parse

from . import plan
from .admission_policy import ENGINE_NO_RUN_AS_NON_ROOT
from .bundle_env import proxy_env, proxy_has_creds
from .bundle_names import (APPLY_ORDER, CHART_DIR, CONFIGMAP_FILE, CONFIGMAP_NAME,
                           DEPLOYMENT_FILE, DOCKER_COMPOSE_FILE,
                           DOCKER_ENV_FILE, DOCKER_RUN_FILE, HELM_VALUES_FILE,
                           HOOK_FILE, HOOK_ROLE_NAME, IMAGES_FILE,
                           MIRROR_SCRIPT_FILE,
                           PROFILE_FILE, SECRET_FILE, docker_container_name)
from .bundle_options import (cli, engine_request_quantities, engine_size,
                             engines_per_node, ignored_options, is_openshift,
                             service_account)
from .ca_trust import CA_MODES, CA_MOUNT_PATH, MODE_OPTION, ca_cfg
from .footprint import (API_BASE, CRANE_CPU_LIMIT, CRANE_CPU_REQUEST,
                        CRANE_EPHEMERAL_STORAGE, CRANE_MEM_LIMIT,
                        CRANE_MEM_REQUEST, ENGINE_DISK_GB, ENGINE_TMP_GB,
                        ENGINE_UPLOAD_HOSTS, NODE_OVERHEAD_CPU,
                        NODE_OVERHEAD_MEM, engine_requests)
from .image_catalog import bundle_rows, cell, functionality_cell
from .image_registry import crane_image
from .markers import MARKER_RE, helm_token_at_install
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory
from .readme_parts import sizing_vocab
from .render_docker import (DOCKER_CA_PATH, DOCKER_MOUNTS, DOCKER_NETWORK,
                            DOCKER_NO_PROXY, DOCKER_PORT_RANGE, DOCKER_RESTART,
                            DOCKER_USER, docker_file_mounts, docker_split_env)
from .service_virt import SV_INGRESS_BACKENDS, sv_cfg, sv_docker_cfg


# -- reading the rendered objects ---------------------------------------------

_KIND_RE = re.compile(r"^kind:\s*(\S+)", re.M)
_NAME_RE = re.compile(r"\bname:\s*([^\s,}]+)")
_NAMESPACE_RE = re.compile(r"\bnamespace:\s*([^\s,}]+)")
_RULE_FIELD_RE = re.compile(r"(apiGroups|resources|verbs):\s*\[([^\]]*)\]",
                            re.S)
_RULE_FIELDS = ("apiGroups", "resources", "verbs")

CLUSTER_KINDS = ("ClusterRole", "ClusterRoleBinding")


def _documents(text):
    """The YAML documents in `text`, comment lines removed."""
    for doc in re.split(r"^---\s*$", text, flags=re.M):
        body = "\n".join(ln for ln in doc.splitlines()
                         if not ln.lstrip().startswith("#"))
        if _KIND_RE.search(body):
            yield body


def _flow_list(text):
    """`[a, "b", ""]` as ["a", "b", ""]; `""` is the core API group."""
    return [item.strip().strip("\"'") for item in text.split(",")]


def _metadata(body):
    """The text of an object's `metadata`, flow or block style."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("metadata:"):
            rest = line[len("metadata:"):].strip()
            if rest:
                return rest
            block = []
            for nxt in lines[i + 1:]:
                if nxt and not nxt.startswith(" "):
                    break
                block.append(nxt)
            # Only metadata's own keys, not those under labels.
            return "\n".join(ln for ln in block if re.match(r"^  \S", ln))
    return ""


def objects(text):
    """[{kind, name, namespace, body}] for each object in a rendered file.
    `namespace` is None for an object that has none."""
    out = []
    for body in _documents(text):
        meta = _metadata(body)
        name = _NAME_RE.search(meta)
        ns = _NAMESPACE_RE.search(meta)
        out.append({"kind": _KIND_RE.search(body).group(1),
                    "name": name.group(1) if name else None,
                    "namespace": ns.group(1) if ns else None,
                    "body": body})
    return out


def rules(body):
    """A Role or ClusterRole's rules as [{apiGroups, resources, verbs}].

    Raises ValueError on a rule it cannot read whole: a missing verb list read
    as "no verbs" would under-report what the bundle grants.
    """
    tail = body.split("\nrules:", 1)
    if len(tail) < 2:
        return []
    found, current = [], None
    for field, value in _RULE_FIELD_RE.findall(tail[1]):
        if field == "apiGroups":
            current = {}
            found.append(current)
        if current is None or field in current:
            raise ValueError(f"could not read the RBAC rules: {field} "
                             f"outside a rule")
        current[field] = _flow_list(value)
    if len(found) != tail[1].count("- apiGroups") or any(
            set(r) != set(_RULE_FIELDS) for r in found):
        raise ValueError("could not read the RBAC rules: a rule lacks "
                         "apiGroups, resources or verbs")
    return found


def rbac(files):
    """Every Role and ClusterRole the rendered files carry, with its rules,
    in apply order and the crane-hook check last."""
    out = []
    for name in [*APPLY_ORDER, HOOK_FILE]:
        for obj in objects(files.get(name, "")):
            if obj["kind"] in ("Role", "ClusterRole"):
                out.append({**obj, "rules": rules(obj["body"]),
                            "hook": obj["name"] == HOOK_ROLE_NAME})
    return out


def security_contexts(body):
    """Each `securityContext:` block in a rendered object, dedented, in the
    order they appear (the pod's before its container's)."""
    lines = body.splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.strip() != "securityContext:":
            continue
        indent = len(line) - len(line.lstrip())
        block = [line[indent:]]
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            if nxt.strip():
                block.append(nxt[indent:])
        out.append("\n".join(block))
    return out


# The fields the restricted Pod Security Standard requires of a pod: the text
# each has in a rendered security context, and its name for a reader.
RESTRICTED_FIELDS = {
    "runAsNonRoot: true": "runAsNonRoot: true",
    "allowPrivilegeEscalation: false": "allowPrivilegeEscalation: false",
    "- ALL": "capabilities dropped (drop: ALL)",
    "type: RuntimeDefault": "seccompProfile: RuntimeDefault",
}


def restricted_gaps(contexts):
    """The RESTRICTED_FIELDS names the security contexts lack; [] meets the
    standard."""
    joined = "\n".join(contexts)
    return [name for text, name in RESTRICTED_FIELDS.items()
            if text not in joined]


def data_keys(body):
    """The keys of a rendered ConfigMap's `data` or a Secret's `stringData`,
    never their values."""
    tail = re.split(r"^(?:data|stringData):\s*$", body, maxsplit=1, flags=re.M)
    if len(tail) < 2:
        return []
    return re.findall(r"^  ([A-Za-z_][A-Za-z0-9_]*):", tail[1], re.M)


def data_line(body, key):
    """One `KEY: value` line of a rendered ConfigMap, stripped, or None. Only
    for keys that hold no credential."""
    m = re.search(rf"^\s+({re.escape(key)}:.*)$", body, re.M)
    return m.group(1).strip() if m else None



# -- text helpers ----------------------------------------------------------------

def show(value):
    """A value for the document, with any marker written as a lower-case
    sample (`<namespace>`): the README names the fields left blank."""
    return MARKER_RE.sub(lambda m: m.group(0).lower().replace("_", "-"),
                         str(value))


def _code(value):
    return f"`{show(value)}`"


def _para(text):
    """A paragraph wrapped for reading as plain text."""
    return textwrap.fill(text, width=79, break_on_hyphens=False,
                         break_long_words=False)


def _bullets(items):
    """Markdown bullets, each wrapped with a hanging indent."""
    return "\n".join(
        textwrap.fill(item, width=79, initial_indent="- ",
                      subsequent_indent="  ", break_on_hyphens=False,
                      break_long_words=False)
        for item in items)


def _table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _count(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _redact(url):
    """A proxy URL with any user:password replaced, for printing."""
    u = urllib.parse.urlsplit(url)
    if "@" not in u.netloc:
        return url
    return urllib.parse.urlunsplit(
        u._replace(netloc="<credentials>@" + u.netloc.rsplit("@", 1)[1]))


def _host_port(ref):
    """(host, port) of a URL or of an image reference's registry."""
    if "://" not in ref:
        ref = "https://" + ref.split("/", 1)[0]
    u = urllib.parse.urlsplit(ref)
    return u.hostname, u.port or 443


FORMAT_NAMES = {"manifests": "Kubernetes manifests", "helm": "Helm chart",
                "docker": "Docker container"}


# -- the sections ----------------------------------------------------------------

def _header(facts, o):
    ids = facts.get("func_ids")
    funcs = ("not read" if ids is None else
             ", ".join(f"`{f}`" for f in ids) or "none")
    rows = [("Location", _code(facts["harbor_id"])),
            ("Agent", _code(o["ship_id"])),
            ("Format", FORMAT_NAMES[o["output_format"]])]
    if o["output_format"] != "docker":
        rows += [("Namespace", _code(o["namespace"])),
                 ("Platform", "OpenShift" if is_openshift(o) else "Kubernetes")]
    rows.append(("Functionalities", funcs))
    name = facts.get("harbor_name") or show(facts["harbor_id"])
    intro = _para(
        "This document is for the people who approve a deployment of the "
        "BlazeMeter agent. It says what the agent runs, which hosts it "
        "connects to, what it is allowed to do and what it holds. It was "
        "written from the same options as the files beside it, and it "
        "changes when the bundle is generated again.")
    return f"# Security review: {name}\n\n{intro}\n\n{_table(('', ''), rows)}\n"


def _functionality_bullets(facts, docker=False):
    """One sub-bullet per funcId: the pods it runs, or that nothing here
    configures it. A docker host runs containers, not pods."""
    ids = facts.get("func_ids")
    if ids is None:
        lines = ["The location's functionalities were not read."]
    elif not ids:
        lines = ["The location has none."]
    else:
        lines = []
        for f in ids:
            model = plan.SIZING_MODELS.get(f)
            lines.append(f"`{f}`: {model['runs']}." if model and docker else
                         f"`{f}`: {model['runs']}, in {model['pods']}."
                         if model else f"`{f}`: this bundle does not configure it.")
    return "\n".join(f"  - {line}" for line in lines)


def _slots_phrase(facts):
    slots = facts.get("slots")
    return (f"at most {slots} at once (the location's `slots`)" if slots
            else "at most the location's `slots` at once (not read here)")


def _what_runs_cluster(facts, o, files):
    dep = objects(files[DEPLOYMENT_FILE])[0]
    replicas = re.search(r"^\s+replicas:\s*(\d+)", dep["body"], re.M)
    made = ("created by this bundle" if o["service_account_create"]
            else "which must already exist, because this bundle does not "
                 "create it")
    named = (" (named after the Helm release)"
             if o["output_format"] == "helm" else "")
    m = sizing_vocab(facts, o)
    items = [
        f"**Crane, the agent**: Deployment {_code(dep['name'])}{named} with "
        f"{_count(int(replicas.group(1)) if replicas else 1, 'replica', 'replicas')} "
        f"in namespace {_code(o['namespace'])}. It runs all the time, as the "
        f"ServiceAccount {_code(service_account(o))} ({made}).",
        f"**The pods crane starts**: for each test, {m['pods'] if m else 'engines'} "
        f"in the same namespace, {_slots_phrase(facts)}. They exist only while "
        f"the test runs. Crane starts them for these functionalities:",
    ]
    hook = ""
    if o["crane_hook"]:
        how = ("`helm test` runs it" if o["output_format"] == "helm"
               else "it runs once when it is applied")
        hook = "\n" + _bullets([
            f"**The crane-hook check**: a one-shot pod that reads the cluster "
            f"and exits; {how}. It has its own read-only Role."])
    if o["output_format"] == "manifests":
        rows = [(obj["kind"], _code(obj["name"]),
                 "cluster" if obj["kind"] in CLUSTER_KINDS
                 else _code(obj["namespace"]), f"`{name}`")
                for name in [*APPLY_ORDER, HOOK_FILE]
                for obj in objects(files.get(name, ""))]
        listing = ("The objects this bundle applies:\n\n"
                   + _table(("Kind", "Name", "Namespace", "File"), rows))
    else:
        listing = _para(
            f"The chart applies the same objects as the manifests format. "
            f"`helm template crane ./{CHART_DIR} -f {HELM_VALUES_FILE}` prints "
            f"them with their names.")
    return (f"\n## What runs\n\n{_bullets(items)}\n"
            f"{_functionality_bullets(facts)}{hook}\n\n{listing}\n")


def _what_runs_docker(facts, o):
    mounts = [f"`{m}`" for m in DOCKER_MOUNTS] + [
        f"`{m.file}` at `{m.path}` (read-only)" for m in docker_file_mounts(o)]
    items = [
        f"**Crane, the agent**: one container, "
        f"{_code(docker_container_name(o['ship_id']))}, from "
        f"`{crane_image(facts, o)}`. It runs as user `{DOCKER_USER}` (root) on "
        f"the host's network (`--net={DOCKER_NETWORK}`), and restarts "
        f"`{DOCKER_RESTART}`.",
        f"**Mounts**: {', '.join(mounts)}.",
        f"**The containers crane starts**: for each test, engines as sibling "
        f"containers on the same host, {_slots_phrase(facts)}. Crane starts "
        f"them through the docker socket. They exist only while the test "
        f"runs. Crane starts them for these functionalities:",
    ]
    return (f"\n## What runs\n\n{_bullets(items)}\n"
            f"{_functionality_bullets(facts, docker=True)}\n")


def _images(facts, o):
    rows = bundle_rows(facts, o)
    table = _table(
        ("Image", "What it does", "Functionality", "When it is pulled"),
        [(f"`{r['ref']}`" + (" (floating tag)" if r["tag_mutable"] else ""),
          r["purpose"], functionality_cell(r), r["pulled_when"])
         for r in rows])
    reg = o["private_registry"]
    text = (f"This bundle pulls from your registry, `{reg}`, and "
            f"`{MIRROR_SCRIPT_FILE}` copies the images there." if reg else
            "This bundle pulls from BlazeMeter's public registry. To pull from "
            "your own registry, generate the bundle again with "
            "`--private-registry <registry>`.")
    if any(r["tag_mutable"] for r in rows):
        text += (" A floating tag names what BlazeMeter published last, so "
                 "the image it names can change without a new bundle.")
    text += (f" `{IMAGES_FILE}` says when each image is pulled, the name each "
             f"must have in a registry, and the command that checks a mirror "
             f"(`bzm-opl-gen images --verify`).")
    return f"\n## Images\n\n{table}\n\n{_para(text)}\n"


def _egress_rows(facts, o, docker):
    api_host, api_port = _host_port(API_BASE)
    reg_host, reg_port = _host_port(crane_image(facts, o))
    puller = ("the docker daemon on the host" if docker
              else "each node's container runtime")
    rows = [(f"`{api_host}`", str(api_port), "crane",
             "HTTPS: registers the agent, receives tests, reports status")]
    rows += [(f"`{h}`", "443", "engines",
              "HTTPS: uploads test results and artifacts")
             for h in ENGINE_UPLOAD_HOSTS]
    rows.append((f"`{reg_host}`", str(reg_port), puller,
                 "pulls the images above"))
    return rows


def _proxy_block(o, docker):
    env = proxy_env(o, no_proxy=DOCKER_NO_PROXY) if docker else proxy_env(o)
    if not env:
        return "No proxy is set. The agent connects to these hosts directly.\n"
    lines = [f"{k}={show(_redact(v))}" for k, v in env.items()]
    creds = ""
    if proxy_has_creds(o):
        creds = "\n" + _para(
            "The proxy URLs carry credentials. "
            + ("They are stored with the AUTH_TOKEN (see Secrets)."
               if o["use_secret"] else
               "This bundle writes them in plain text, because `use_secret` "
               "is off.")) + "\n"
    return ("The agent uses this proxy:\n\n```\n" + "\n".join(lines)
            + f"\n```\n{creds}")


def _inbound_cluster(facts, o, files):
    probe = re.search(r"httpGet:.*\bport:\s*(\d+)", files[DEPLOYMENT_FILE])
    items = []
    if probe:
        items.append(f"Crane listens on port {probe.group(1)} in its pod, for "
                     f"the kubelet's health probes.")
    items.append(
        f"The Services crane creates for its pods are of type "
        f"`{o['service_type']}` (`KUBERNETES_SERVICE_USE_TYPE`). "
        + ("They are reachable only inside the cluster."
           if o["service_type"] == "CLUSTERIP" else
           "Each one also opens a port on every node."))
    sv = sv_cfg(facts, o)
    if sv:
        backend = SV_INGRESS_BACKENDS[sv["type"]]
        tls = (f"It reads the TLS certificate from the Secret "
               f"{_code(sv['tls_secret'])}, which this bundle does not create."
               if backend.tls_secret_read else
               "It reads no TLS Secret.")
        items.append(
            f"Virtual services are published. Crane creates one "
            f"{backend.creates} (`{backend.group}`) for each virtual service, "
            f"at `<virtual-service>-<port>-<namespace>.{show(sv['subdomain'])}`. "
            f"{tls}")
    else:
        items.append("No virtual service is published: this bundle sets no "
                     "ingress.")
    return _bullets(items)


def _inbound_docker(o):
    items = [f"The container shares the host's network. Engines use host "
             f"ports `{DOCKER_PORT_RANGE}` (`DOCKER_PORT_RANGE`), which must be "
             f"free on the host."]
    sv = sv_docker_cfg(o)
    if sv:
        how = ("over HTTPS, with the certificate this bundle mounts"
               if sv["cert"] else "over HTTP, because no certificate is set")
        items.append(f"Virtual services are published under "
                     f"{_code(sv['hostname'])}, {how}.")
    else:
        items.append("No virtual service is published: this bundle sets no "
                     "hostname for them.")
    return _bullets(items)


def _network(facts, o, files):
    docker = o["output_format"] == "docker"
    inbound = _inbound_docker(o) if docker else _inbound_cluster(facts, o, files)
    sut = _para("Engines also connect to the systems your tests target. Those "
                "hosts come from your test scripts, not from this bundle.")
    return f"""
## Network

Outbound connections:

{_table(("Host", "Port", "From", "What for"), _egress_rows(facts, o, docker))}

{sut}

{_proxy_block(o, docker)}
Inbound connections and published endpoints:

{inbound}
"""


def _tls(o):
    ca = ca_cfg(o)
    args = "--ca-bundle <ca-file>"
    if o["proxy"]:
        args += " --proxy <proxy-url>"
    if o["private_registry"]:
        args += f" --registry {o['private_registry']}"
    check = (_para("Before you deploy, check the CA against the certificate "
                   "chain your network presents. Run this on a machine on the "
                   "agent's network:")
             + f"\n\n```\nbzm-opl-gen ca-check {args}\n```\n")
    if not ca:
        body = _para(
            "This bundle adds no CA. Crane and the engines trust the public "
            "roots in their images. Behind a proxy that inspects TLS, the "
            "agent cannot reach BlazeMeter until you add your CA: generate the "
            "bundle again with one of the CA options.")
    elif o["output_format"] == "docker":
        body = _para(
            f"The CA comes from {CA_MODES[MODE_OPTION[ca['mode']]]}. It is "
            f"mounted at `{DOCKER_CA_PATH}`, where `REQUESTS_CA_BUNDLE` and "
            f"`AWS_CA_BUNDLE` point, and it replaces the container's CA store. "
            f"It must therefore hold your CA and the public roots.")
    else:
        body = _para(
            f"The CA comes from {CA_MODES[MODE_OPTION[ca['mode']]]}: the "
            f"ConfigMap {_code(ca['cm'])}, key {_code(ca['key'])}. Crane mounts "
            f"it at `{CA_MOUNT_PATH}` and gives it to each engine "
            f"(`KUBERNETES_CA_BUNDLE_MOUNT`). `REQUESTS_CA_BUNDLE` and "
            f"`AWS_CA_BUNDLE` point to it, so it replaces the agent's trust "
            f"store. It must therefore hold your CA and the public roots.")
    return f"\n## TLS trust\n\n{body}\n\n{check}"


# The verbs that change nothing.
READ_VERBS = {"get", "list", "watch"}


def _rbac_section(o, files):
    ns, sa = o["namespace"], service_account(o)
    helm = o["output_format"] == "helm"
    roles = rbac(files)
    blocks = []
    for role in roles:
        name = "" if helm else f" {_code(role['name'])}"
        if role["kind"] == "ClusterRole":
            head = f"### ClusterRole{name} (cluster-wide)"
            use = (f"Optional (`cluster_rbac`). Bound to the ServiceAccount "
                   f"{_code(sa)} in {_code(ns)}.")
        elif role["hook"]:
            head = f"### Role{name} for the crane-hook check (namespace {_code(ns)})"
            use = ("Bound to the same ServiceAccount. The check pod uses it, "
                   "and crane does not need it.")
        else:
            head = f"### Role{name} (namespace {_code(ns)})"
            use = (f"Crane's own permissions, bound to the ServiceAccount "
                   f"{_code(sa)}. A Role reaches only its own namespace.")
        rows = [(", ".join(f"`{g}`" if g else "core" for g in r["apiGroups"]),
                 ", ".join(f"`{x}`" for x in r["resources"]),
                 ", ".join(r["verbs"]))
                for r in role["rules"]]
        blocks.append(f"{head}\n\n{_para(use)}\n\n"
                      + _table(("API group", "Resources", "Verbs"), rows))
    cluster = [r for r in roles if r["kind"] == "ClusterRole"]
    if not cluster:
        scope = "Nothing in this bundle is cluster-scoped."
    elif all(set(rule["verbs"]) <= READ_VERBS
             for role in cluster for rule in role["rules"]):
        scope = "The ClusterRole grants reads only."
    else:
        scope = "The ClusterRole grants more than reads."
    lead = _para(
        "These rules are read from the rendered objects of this bundle. Crane "
        "creates, watches and deletes the pods, Services, Deployments, Jobs "
        "and Secrets of each test run and virtual service itself, which is "
        f"why its Role grants write verbs. {scope}")
    return f"\n## Kubernetes permissions\n\n{lead}\n\n" + "\n\n".join(blocks) + "\n"


def _pod_security(o, files):
    dep = objects(files[DEPLOYMENT_FILE])[0]
    contexts = security_contexts(dep["body"])
    uid = ("This bundle sets no UID for crane. On OpenShift the SCC assigns "
           "one; elsewhere the image's own user applies."
           if o["platform"] == "openshift" else
           f"Crane runs as UID and GID `{o['run_as_user']}`.")
    if not restricted_gaps(contexts):
        uid += " The crane pod meets the `restricted` Pod Security Standard."
    labels = (["# the pod", "# the crane container"] if len(contexts) == 2
              else [""] * len(contexts))
    shown = "\n".join(f"{label}\n{ctx}".strip() for label, ctx in
                      zip(labels, contexts))
    cm = files[CONFIGMAP_FILE]
    keys = [line for line in (data_line(cm, "INHERIT_RUNNING_USER_AND_GROUP"),
                              data_line(cm, "KUBERNETES_SECURITY_CONTEXT_CAP_JSON"))
            if line]
    if keys:
        engines = (_para("Engines and the other pods crane starts run as "
                         "crane's UID and GID, with every capability dropped. "
                         "They meet the `baseline` Pod Security Standard, not "
                         "`restricted`: " + ENGINE_NO_RUN_AS_NON_ROOT + ". A "
                         "namespace that enforces `restricted` refuses every "
                         "engine after the agent is online. This bundle sets:")
                   + "\n\n```\n" + "\n".join(keys) + "\n```\n")
    else:
        engines = _para(
            "**Engines run privileged.** `restrict_engines` is off, so crane "
            "starts its pods with its own default, a privileged container. "
            "Restricted Pod Security admission, OpenShift's restricted-v2 SCC "
            "and GKE Autopilot refuse such a pod.") + "\n"
    hook = ""
    if HOOK_FILE in files:
        pod = [x for x in objects(files[HOOK_FILE]) if x["kind"] == "Pod"][0]
        gaps = restricted_gaps(security_contexts(pod["body"]))
        hook = "\n" + _para(
            "The crane-hook check pod meets the `restricted` Pod Security "
            "Standard." if not gaps else
            "The crane-hook check pod does not meet the `restricted` Pod "
            "Security Standard. It lacks " + ", ".join(gaps) + ".") + "\n"
    return (f"\n## Pod security\n\n{_para(uid)} Its security contexts, as "
            f"rendered:\n\n```yaml\n{shown}\n```\n\n{engines}{hook}")


def _host_access(o):
    why = ignored_options(o)
    root = why["run_as_user"][0].upper() + why["run_as_user"][1:]
    text = _para(
        "There is no Kubernetes RBAC on a docker host. The agent's power on "
        "the host comes from the docker socket, `/var/run/docker.sock`: access "
        f"to it is effectively root on the host. {root}. The engine security "
        f"settings of the cluster formats do not apply here: "
        f"{why['restrict_engines']}.")
    return f"\n## Host access\n\n{text}\n"


def _total(values, parse, fmt):
    return fmt(sum(parse(v) for v in values))


def _resources(facts, o):
    docker = o["output_format"] == "docker"
    cpu, mem = engine_size(o)
    m = sizing_vocab(facts, o)
    pod, pods = (m["pod"], m["pods"]) if m else ("engine", "engines")
    slots = int(facts["slots"]) if facts.get("slots") else None
    per_node = (slots or 1) if docker else engines_per_node(o)
    p = plan.capacity_plan(users=slots or 1, vus_per_engine=1,
                           engine_cpu=format_cpu(cpu),
                           engine_mem=format_memory(mem),
                           engines_per_node=per_node)
    node = p["node"]
    overhead = (f"{format_cpu(NODE_OVERHEAD_CPU)} CPU and "
                f"{format_memory(NODE_OVERHEAD_MEM)}")
    disk = f"{ENGINE_DISK_GB}GB of disk ({ENGINE_TMP_GB}GB of it in /tmp)"
    if docker:
        text = _para(
            f"This bundle sets no CPU or memory limits on the container or on "
            f"the engines. BlazeMeter documents each engine at "
            f"{format_cpu(cpu)} CPU, {format_memory(mem)} of memory and {disk}. "
            f"For {_count(per_node, 'engine', 'engines')} at once, the host "
            f"needs a capacity of about {node['cpu']} CPU, {node['memory']} of "
            f"memory and {node['disk_gb']}GB of disk, plus crane. That "
            f"includes about {overhead} for the host itself.")
        return f"\n## Resources\n\n{text}\n"
    req_cpu, req_mem = engine_requests(facts, engine_request_quantities(o))
    req_mem = format_memory(parse_memory(req_mem))
    ephem = "not set by this bundle"
    if o["engine_ephemeral_request_mb"] or o["engine_ephemeral_limit_mb"]:
        ephem = (f"request {o['engine_ephemeral_request_mb'] or 'not set'} MB, "
                 f"limit {o['engine_ephemeral_limit_mb'] or 'not set'} MB")
    crane_ephem = o["crane_ephemeral_storage"] or CRANE_EPHEMERAL_STORAGE
    table = _table(
        ("Pod", "CPU request", "CPU limit", "Memory request", "Memory limit",
         "Ephemeral storage"),
        [("Crane (one, always running)", CRANE_CPU_REQUEST, CRANE_CPU_LIMIT,
          CRANE_MEM_REQUEST, CRANE_MEM_LIMIT, f"{crane_ephem} request and limit"),
         (f"Each {pod}", req_cpu, format_cpu(cpu), req_mem, format_memory(mem),
          ephem)])
    notes = [f"Crane applies one limits pair to every pod it creates. "
             f"BlazeMeter documents {disk} for each engine."]
    if facts.get("override_cpu") or facts.get("override_memory"):
        notes.append("The engine requests come from this location's "
                     "`overrideCPU` / `overrideMemory` in BlazeMeter, which "
                     "replace the requests this bundle sets.")
    if slots:
        notes.append(
            f"At full concurrency ({_count(slots, pod, pods)} and crane) the "
            f"pods request "
            f"{_total([CRANE_CPU_REQUEST] + [req_cpu] * slots, parse_cpu, format_cpu)} "
            f"CPU and "
            f"{_total([CRANE_MEM_REQUEST] + [req_mem] * slots, parse_memory, format_memory)}, "
            f"with limits of "
            f"{_total([CRANE_CPU_LIMIT] + [format_cpu(cpu)] * slots, parse_cpu, format_cpu)} "
            f"CPU and "
            f"{_total([CRANE_MEM_LIMIT] + [format_memory(mem)] * slots, parse_memory, format_memory)}.")
    nodes = (f" This agent needs {_count(p['nodes_per_agent'], 'such node', 'such nodes')}."
             if slots else "")
    notes.append(
        f"A node for {_count(per_node, pod, pods)} needs a capacity of about "
        f"{node['cpu']} CPU and {node['memory']} of memory, which includes "
        f"about {overhead} that the node keeps for itself.{nodes}")
    return (f"\n## Resources\n\n{table}\n\n"
            + "\n\n".join(_para(n) for n in notes) + "\n")


def _secrets(facts, o, files):
    # Imported here: generate imports this module to write the document.
    from .generate import SECRET_OPTIONS
    items = []
    if o["output_format"] == "docker":
        _, secret = docker_split_env(facts, o)
        if secret:
            items.append(
                f"`{DOCKER_ENV_FILE}` holds "
                f"{', '.join(f'`{k}`' for k in secret)}. The script and "
                f"`{DOCKER_COMPOSE_FILE}` pass it with `--env-file`, which "
                f"keeps it out of the command line.")
        else:
            items.append(
                f"**The AUTH_TOKEN is in plain text in `{DOCKER_RUN_FILE}` "
                f"and `{DOCKER_COMPOSE_FILE}`**, because `use_secret` is off.")
        items += [f"`{m.file}` holds the private key of the virtual-service "
                  f"certificate." for m in docker_file_mounts(o)
                  if m.option == "sv_tls_key"]
    else:
        if o["output_format"] == "helm" and helm_token_at_install(o):
            items.append("The AUTH_TOKEN is in no file of this bundle. It is "
                         "passed at `helm install` with "
                         "`--set-string authToken=...`.")
        for secret in objects(files.get(SECRET_FILE, "")):
            keys = ", ".join(f"`{k}`" for k in data_keys(secret["body"]))
            items.append(f"The Secret {_code(secret['name'])} holds {keys}.")
        if "AUTH_TOKEN" in data_keys(files[CONFIGMAP_FILE]):
            items.append(
                f"**The AUTH_TOKEN is in plain text in the ConfigMap "
                f"`{CONFIGMAP_NAME}`**, because `use_secret` is off. Anyone "
                f"who can read ConfigMaps in {_code(o['namespace'])} can read "
                f"it.")
        if o["pull_secret"]:
            items.append(f"The image pull secret {_code(o['pull_secret'])} is "
                         f"named, and this bundle does not create it.")
        sv = sv_cfg(facts, o)
        if sv and SV_INGRESS_BACKENDS[sv["type"]].tls_secret_read:
            items.append(f"The TLS Secret {_code(sv['tls_secret'])} for "
                         f"virtual services is named, and this bundle does "
                         f"not create it.")
    omitted = ", ".join(f"`{k}`" for k in sorted(SECRET_OPTIONS))
    items.append(f"`{PROFILE_FILE}` records the options without the "
                 f"credentials ({omitted}).")
    lead = _para("The AUTH_TOKEN is the agent's credential for BlazeMeter. "
                 "This document never shows its value.")
    return f"\n## Secrets\n\n{lead}\n\n{_bullets(items)}\n"


def _deploy_note(o):
    if o["output_format"] == "docker":
        return ""
    return "\n" + _para(
        f"A person with the rights to create these objects in "
        f"{_code(o['namespace'])} applies the bundle with `{cli(o)}`"
        + (" or `helm`" if o["output_format"] == "helm" else "")
        + ". Nothing in it applies itself.") + "\n"


def review_md(facts, o, files):
    """SECURITY-REVIEW.md for a bundle.

    `files` are the rendered Kubernetes objects: the bundle's own manifests,
    or for a chart the manifests of the same options, which the chart is held
    equal to. None for docker.
    """
    parts = [_header(facts, o)]
    if o["output_format"] == "docker":
        parts += [_what_runs_docker(facts, o), _images(facts, o),
                  _network(facts, o, None), _tls(o), _host_access(o),
                  _resources(facts, o), _secrets(facts, o, None)]
    else:
        parts += [_what_runs_cluster(facts, o, files), _images(facts, o),
                  _network(facts, o, files), _tls(o), _rbac_section(o, files),
                  _pod_security(o, files), _resources(facts, o),
                  _secrets(facts, o, files), _deploy_note(o)]
    return "".join(parts).rstrip() + "\n"
