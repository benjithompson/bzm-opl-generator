"""README fragments shared by the three bundle formats.

A README is a handover to whoever applies the bundle: what it is, what is
unfinished, how to tell it worked and what it costs to run.
test_readme_is_short_and_actionable caps its length.
"""

import textwrap

from . import plan
from .bundle_names import (CA_CONFIGMAP, CHART_DIR, DOCKER_CA_FILE,
                           DOCKER_RUN_FILE, MIRROR_SCRIPT_FILE,
                           NODEPOOLS_FILE, PROFILE_FILE,
                           docker_container_name)
from .bundle_options import (DEFAULT_OPTIONS, cli, engine_request, engine_size,
                             ignored_options, is_openshift,
                             separate_pools, service_account)
from .ca_trust import ca_cfg
from .facts import runs_engine
from .footprint import ENGINE_DISK_GB, ENGINE_TMP_GB, PUBLIC_REGISTRY
from .markers import MARKER_PATTERN, helm_token_at_install, marker
from .quantity import format_cpu, format_memory
from .required_fields import (PLACEHOLDER_REFUSED_BY_API,
                              PLACEHOLDER_SOURCE, placeholder_fields)
from .service_virt import SV_INGRESS_BACKENDS, sv_cfg


# How the summary table names the public registry.
PUBLIC_REGISTRY_LABEL = f"{PUBLIC_REGISTRY} (BlazeMeter public)"


def bundle_table(facts, o, extra=()):
    """The README's heading and summary table."""
    rows = [
        ("Location", f"`{facts['harbor_id']}`"),
        ("Agent", f"`{o['ship_id']}`"),
    ]
    # Namespace and platform are Kubernetes answers. The platform row names the
    # cluster and its CLI rather than the UID posture.
    if o["output_format"] != "docker":
        rows += [("Namespace", f"`{o['namespace']}`"),
                 ("Platform", f"{'OpenShift' if is_openshift(o) else 'Kubernetes'}"
                              f" -- deploy with `{cli(o)}`")]
    else:
        rows.append(("Container", f"`{docker_container_name(o['ship_id'])}`"))
    rows.append(("Images from",
                 f"`{o['private_registry'] or PUBLIC_REGISTRY_LABEL}`"))
    rows += list(extra)
    head = f"# BlazeMeter agent -- {facts.get('harbor_name') or facts['harbor_id']}\n\n"
    return head + "| | |\n|---|---|\n" + "".join(
        f"| {k} | {v} |\n" for k, v in rows)


def placeholder_block(facts, o, where=()):
    """The README section naming every field left blank, or "".

    `where` maps a field to the file it is filled in in, where the format
    knows.
    """
    found = placeholder_fields(facts, o)
    if not found:
        return ""
    where = dict(where)
    rows = "\n".join(
        f"| `{k}` | `{marker(k)}` "
        f"| {PLACEHOLDER_SOURCE.get(k, 'no value was given')}"
        + (f" (in `{where[k]}`)" if k in where else "") + " |"
        for k in found)
    n = len(found)
    subject = (f"{n} fields were left blank, and each carries a marker naming "
               f"it" if n > 1
               else f"1 field was left blank, and it carries "
                    f"`{marker(found[0])}`")
    # What stops an unfinished bundle differs per format: the API server
    # refuses a marker only in a name or label value, the chart's validation
    # refuses the rest, and on docker the script and compose refuse it.
    if o["output_format"] == "docker":
        stops = (f"Both `{DOCKER_RUN_FILE}` and `docker compose up` refuse "
                 "to start until it is filled in.")
    elif all(k in PLACEHOLDER_REFUSED_BY_API for k in found):
        markers = " and ".join(f"`{marker(k)}`" for k in found)
        stops = (f"Applying it fails -- {markers} "
                 + ("are not legal Kubernetes names or label values" if n > 1
                    else "is not a legal Kubernetes name or label value")
                 + ", so the API server rejects the object and names the "
                   "field.")
    elif o["output_format"] == "helm":
        stops = "`helm install` refuses it and names the field."
    else:
        stops = (f"Some markers apply without error and fail only once the "
                 f"agent runs, so check with "
                 f"`grep -rl '{MARKER_PATTERN}' *.yaml` before applying.")
    # A chart's absent token is in no row and carries no marker, so it is said
    # here or the count reads as complete.
    also = ""
    if o["output_format"] == "helm" and helm_token_at_install(o):
        also = (" The AUTH_TOKEN is not among them and not in this bundle at "
                "all: pass it with "
                "`--set-string authToken=...` in the Deploy command below.")
    quote = textwrap.fill(
        f"**This bundle is not finished.** {subject} instead of a value. "
        f"{stops} Fill {'them' if n > 1 else 'it'} in, or re-generate "
        f"with {'them' if n > 1 else 'it'} set.{also}",
        width=76, break_on_hyphens=False)
    quoted = "\n".join("> " + ln for ln in quote.splitlines())
    return f"""
{quoted}

| field | marker | where the value comes from |
|---|---|---|
{rows}
"""


def ca_slot_block(o):
    """The README section for a bundle whose certificate is a file, or "".

    Separate from the placeholder block: the file mode is a decision, not a
    blank. The manifests command uses `--from-file=<key>=<path>` so the
    ConfigMap key is what KUBERNETES_CA_BUNDLE_MOUNT names, whatever the file
    is called.
    """
    ca = ca_cfg(o)
    if not ca or ca["mode"] != "file":
        return ""
    name = ca["key"]
    if o["output_format"] == "docker":
        lead = (f"**This bundle names a certificate it does not carry.** Put it "
                f"beside `./{DOCKER_RUN_FILE}` as `{DOCKER_CA_FILE}`, or point "
                f"`CA_BUNDLE` at one the host already has. The script refuses "
                f"to start without it.")
        cmd = ""
    elif o["output_format"] == "helm":
        lead = (f"**This bundle names a certificate it does not carry.** Put "
                f"the PEM in `{CHART_DIR}/` as `{name}`, beside `Chart.yaml`, "
                f"and install as normal. The chart builds the `{CA_CONFIGMAP}` "
                f"ConfigMap from it and refuses to install without it.")
        cmd = ""
    else:
        lead = (f"**This bundle names a certificate it does not carry.** The "
                f"manifests mount the `{CA_CONFIGMAP}` ConfigMap but do not "
                f"create it. Create it from your certificate first, then apply:")
        cmd = (f"\n>\n> ```\n"
               f"> kubectl create configmap {CA_CONFIGMAP} \\\n"
               f">     --from-file={name}=./{name} -n {o['namespace']}\n"
               f"> kubectl apply -f .\n> ```\n>\n"
               + "\n".join("> " + line for line in textwrap.fill(
                   "Until that ConfigMap exists the crane pod stays in "
                   "`ContainerCreating`, naming it.",
                   width=76, break_on_hyphens=False).splitlines()))
    body = textwrap.fill(lead, width=76, break_on_hyphens=False)
    return "\n> " + "\n> ".join(body.splitlines()) + cmd + "\n\n"


def verify_block(o):
    """The README's check-it-worked section."""
    kc = cli(o)
    return f"""## Check it worked

```
{kc} -n {o['namespace']} rollout status deploy/crane
{kc} -n {o['namespace']} logs -l role=role-crane -f
```

The agent should show **online** in BlazeMeter under Settings -> Private
Locations within a minute or so.
"""


def sizing_vocab(facts, o):
    """The sizing model this location is described in, plus whether its agent
    runs an engine and what one pod of the configured size carries; None
    where its funcIds name no model.

    Unread funcIds and uncovered ones both give None: a handover can say
    nothing true about the unit in either case.
    """
    models = plan.sizing_models_for(facts.get("func_ids")) or []
    if not models:
        return None
    fid = models[0]
    return {**plan.SIZING_MODELS[fid],
            "id": fid,
            "is_performance": fid == plan.PERFORMANCE,
            "engine": runs_engine(fid),
            "per_pod": plan.per_pod_capacity(fid, *engine_size(o))}


def sizing_bullet(facts, o):
    """What one pod costs, in the word for the pod this location creates.

    The engine branch states BlazeMeter's documented requirement, true on any
    platform. The other states only the limits a cluster bundle applies: nobody
    has measured a mock pod, and docker carries no limits pair.
    """
    reg = o["private_registry"]
    egress = "egress to `*.blazemeter.com`" + (f" and `{reg}`." if reg else ".")
    m = sizing_vocab(facts, o)
    if m is None or m["engine"]:
        return (f"- Each concurrent engine needs **{format_cpu(engine_size(o)[0])} CPU + "
                f"{format_memory(engine_size(o)[1])} RAM + {ENGINE_DISK_GB}GB disk** "
                f"({ENGINE_TMP_GB}GB of it /tmp),\n  and {egress}")
    if "engine_cpu_limit" in ignored_options(o):
        return (f"- This format sets no CPU or memory limits on this location's "
                f"{m['runs']}.\n  The agent needs {egress}")
    return (f"- Each concurrent {m['pod']} is capped at **{format_cpu(engine_size(o)[0])} CPU + "
            f"{format_memory(engine_size(o)[1])} RAM** -- crane applies\n  one limits "
            f"pair to every pod it creates -- and the agent needs {egress}")


def requests_bullet(facts, o):
    """The engine requests this bundle sets, and whether the location's own
    overrides replace them. Measured on engines only, and said so for a
    location without one."""
    m = sizing_vocab(facts, o)
    cpu, mib = engine_request(o)
    have_cpu, have_mem = facts.get("override_cpu"), facts.get("override_memory")
    if m is not None and not m["engine"]:
        return (f"- Pods crane creates request {cpu} CPU / {mib}Mi (equal to the limits);\n"
                f"  measured on engines, not yet on a {m['pod']}.")
    line = (f"- Engines request what they are limited to: {cpu} CPU / {mib}Mi\n"
            f"  (`KUBERNETES_RESOURCES_DEFAULT_CPU` / `_MEM`), so nodes are not overpacked.")
    if have_cpu or have_mem:
        line += (f"\n  This location's `overrideCPU` / `overrideMemory` ({have_cpu or 'unset'} /\n"
                 f"  {have_mem or 'unset'}) replace them; clear them in BlazeMeter to use these.")
    return line


def location_bullet(facts, o):
    """The location's slots and threadsPerEngine, or a prompt to check them.

    Unset, the agent looks healthy and every test start fails with 403. This
    never asks how the facts arrived: unknown figures get "check them" either
    way. threadsPerEngine is never relabelled; another model's own figure is
    printed beside it.
    """
    slots, tpe = facts.get("slots"), facts.get("threads_per_engine")
    if not slots or not tpe:
        return ("\n- **Check this location's `slots` and `threadsPerEngine`** "
                "(Settings -> Private Locations): unset, the agent comes online "
                "and looks healthy, and every test start fails with 403 *Not "
                "enough available resources*.")
    total = "its total is that times the agents in it"
    m = sizing_vocab(facts, o)
    # funcIds unread or uncovered: state the fields and claim no unit.
    if m is None:
        return (f"\n- This location holds `slots` **{slots}** and "
                f"`threadsPerEngine` **{tpe:,}**; {total}. Nothing here knows "
                f"which model it is sized in, so neither figure is given a unit.")
    if not m["engine"]:
        carries = (f"they are sized in **{m['unit']}**, about **{m['per_pod']}** "
                   f"to each" if m["per_pod"] else
                   f"how many {m['unit']} its {m['runs']} serve has not been measured")
        return (f"\n- This location runs **{m['runs']}**, which carry no taurus "
                f"engine. `slots` is **{slots}** and `threadsPerEngine` "
                f"**{tpe:,}**, as the account stores them; {carries}.")
    if m["is_performance"]:
        return (f"\n- This location runs **{slots} engine(s) per agent at {tpe:,} "
                f"virtual users each** (`slots` / `threadsPerEngine`); {total}.")
    carries = (f", about **{m['per_pod']}** to an engine this size"
               if m["per_pod"] else "")
    return (f"\n- This location runs **{slots} engine(s) per agent** (`slots`); "
            f"{total}. It runs {m['runs']}, sized in **{m['unit']}**{carries} "
            f"rather than in the **{tpe:,}** its `threadsPerEngine` holds.")


def sa_bullet(o):
    """Named only when the bundle does not create the ServiceAccount: a missing
    one leaves the pod uncreated, with the event on its ReplicaSet.
    """
    if o["service_account_create"]:
        return ""
    return (f"\n- ServiceAccount **`{service_account(o)}`** must already exist in "
            f"`{o['namespace']}` -- this bundle\n  does not create it. If it is "
            f"missing, apply succeeds but the agent pod is\n  never created.")


def sv_bullet(facts, o):
    """Where the wildcard TLS secret goes, for a backend that reads it.

    Measured on ingress-nginx: crane resolves the secret in the agent's
    namespace, and without it the endpoint still answers 200 over the
    controller's own certificate, so nothing reports it missing.
    """
    cfg = sv_cfg(facts, o)
    if not cfg or not SV_INGRESS_BACKENDS[cfg["type"]].tls_secret_read:
        return ""
    return (
        f"\n- **The wildcard TLS secret `{cfg['tls_secret']}` has to be in "
        f"`{o['namespace']}`, and this\n  bundle does not create it.** Crane "
        f"creates its {SV_INGRESS_BACKENDS[cfg['type']].creates} in the agent's "
        f"namespace and\n  looks the secret up there. It has to cover "
        f"`*.{cfg['subdomain']}`.\n"
        f"  ```\n"
        f"  {cli(o)} -n {o['namespace']} create secret tls {cfg['tls_secret']} "
        f"--cert=<file> --key=<file>\n"
        f"  ```\n"
        f"  Without it, endpoints are served with the ingress controller's "
        f"default\n  certificate, which verifying clients reject.")


def create_namespace_cmd(o):
    """The namespace command a cluster bundle prints, which succeeds whether or
    not the namespace exists.

    No bundle file owns the namespace, so `delete -f .` never takes it. A
    guarded create sends nothing for an existing namespace, where piping a
    dry-run create into apply was measured deleting another owner's labels, a
    PodSecurity level among them.
    """
    kc, ns = cli(o), o["namespace"]
    return f"{kc} get namespace {ns} >/dev/null 2>&1 || {kc} create namespace {ns}"


def deploy_steps(o, verb):
    """The numbered steps before the apply or install, with the verb last.

    Node pools first (a missing pool fails late and silently), then the mirror,
    then an existing trust-bundle ConfigMap (usually already there).
    """
    steps = []
    if separate_pools(o):
        steps.append(
            f"**{{n}}. Create the node pools** -- see [{NODEPOOLS_FILE}]"
            f"({NODEPOOLS_FILE}). Engines are pinned to nodes\nthat must exist "
            f"first, or every test stays Pending.\n\n")
    if o["private_registry"]:
        steps.append(
            "**{n}. Mirror the images** (needs push access to the registry; "
            "the pull side needs none):\n\n"
            f"```\n./{MIRROR_SCRIPT_FILE}\n```\n\n")
    if o["pull_secret"]:
        # Measured: engine pods carry no imagePullSecrets of crane's and run as
        # `default`. A plain patch replaces that list, so append, or create it.
        kc, ns, name = cli(o), o["namespace"], o["pull_secret"]
        server = (o["private_registry"] or "<registry>").split("/")[0]
        steps.append(
            f"**{{n}}. Create the pull Secret and give it to the engines** -- "
            f"this bundle names\n`{name}` for crane's own image and does not "
            f"create it. The engine pods crane\ncreates run as the namespace's "
            f"`default` ServiceAccount and get no pull secret\nfrom crane, so "
            f"add it there too (once):\n\n"
            f"```\n{create_namespace_cmd(o)}\n"
            f"{kc} -n {ns} create secret docker-registry {name} "
            f"--docker-server={server} --docker-username=<user> "
            f"--docker-password=<password>\n"
            f"{kc} -n {ns} patch serviceaccount default --type json -p "
            f"'[{{{{\"op\":\"add\",\"path\":\"/imagePullSecrets/-\","
            f"\"value\":{{{{\"name\":\"{name}\"}}}}}}}}]' 2>/dev/null || "
            f"{kc} -n {ns} patch serviceaccount default -p "
            f"'{{{{\"imagePullSecrets\":[{{{{\"name\":\"{name}\"}}}}]}}}}'\n```\n\n")
    ca = ca_cfg(o)
    if ca and ca["mode"] == "existing":
        # `--from-file=<key>=<path>` rather than BlazeMeter's documented bare
        # path: a key the manifests do not mount gives crane an empty bundle
        # rather than an error. The namespace command is repeated because this
        # step comes before the one that creates it.
        make_ns = f"{create_namespace_cmd(o)}\n"
        steps.append(
            f"**{{n}}. Create the trust-bundle ConfigMap**, if your platform "
            f"team has not\nalready -- this bundle references `{ca['cm']}`\nin "
            f"`{o['namespace']}` and does not create it:\n\n"
            f"```\n{make_ns}{cli(o)} -n {o['namespace']} create configmap "
            f"{ca['cm']} --from-file={ca['key']}=/path/to/your-ca.pem\n```\n\n"
            f"Keep the `{ca['key']}=` in front of the path: it sets the key "
            f"these manifests mount.\n\n")
    if not steps:
        return ""
    body = "".join(s.format(n=i) for i, s in enumerate(steps, 1))
    return f"{body}**{len(steps) + 1}. {verb}**\n\n"


def set_but_not_carried(o):
    """The options this bundle set that its format cannot carry, as (name, why)
    pairs. Only those changed from their default, so the one that matters is
    not buried.
    """
    return [(k, why) for k, why in sorted(ignored_options(o).items())
            if o.get(k) != DEFAULT_OPTIONS[k]]


def ignored_block(o):
    """The README's "Set here, but not carried" table, or ""."""
    ignored = set_but_not_carried(o)
    if not ignored:
        return ""
    rows = "\n".join(f"| `{k}` | {why} |" for k, why in ignored)
    return f"""
## Set here, but not carried

These were set, but this format has nowhere to put them, so this bundle does
not apply them. They are recorded in `{PROFILE_FILE}`.

| option | why |
|---|---|
{rows}
"""
