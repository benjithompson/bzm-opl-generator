"""The flat-manifest bundle: YAML to `kubectl apply`, rendered from
templates/*.yaml.
"""

import json
import os
from string import Template

from .bundle_env import extra_env, proxy_env, proxy_has_creds
from .bundle_names import (APPLY_ORDER, CA_CONFIGMAP, CA_CONFIGMAP_FILE,
                           CLUSTERROLE_FILE, CLUSTERROLEBINDING_FILE,
                           CONFIGMAP_FILE, CONFIGMAP_NAME,
                           DEPLOYMENT_FILE, HOOK_FILE, HOOK_ROLE_NAME,
                           MIRROR_SCRIPT_FILE, NODEPOOLS_FILE, ROLE_FILE,
                           ROLEBINDING_FILE, SECRET_FILE, SECRET_NAME,
                           SERVICEACCOUNT_FILE, TEMPLATE_DIR)
from .bundle_options import (auto_update, cli, crane_scheduling,
                             engine_request, engine_scheduling, separate_pools)
from .ca_trust import CA_MOUNT_PATH, ca_cfg
from .footprint import (CRANE_CPU_LIMIT, CRANE_CPU_REQUEST,
                        CRANE_EPHEMERAL_STORAGE, CRANE_MEM_LIMIT,
                        CRANE_MEM_REQUEST, ENGINE_DEFAULT_CPU,
                        ENGINE_DEFAULT_MEM, PUBLIC_REGISTRY)
from .image_registry import (HOOK_IMAGE_REPO, HOOK_IMAGE_TAG,
                             crane_image, image_overrides, mirror_script)
from .markers import is_placeholder
from .nodepools import nodepools_md
from .quoting import yq
from .readme_parts import (bundle_table, ca_slot_block,
                           create_namespace_cmd, deploy_steps,
                           ignored_block, location_bullet,
                           placeholder_block, requests_bullet, sa_bullet,
                           sizing_bullet, sv_bullet, verify_block)
from .service_virt import SV_INGRESS_BACKENDS, sv_cfg


def _tpl(name):
    """A template from templates/, for string.Template substitution."""
    with open(os.path.join(TEMPLATE_DIR, name)) as f:
        return Template(f.read())


def _configmap(facts, o):
    """bzm_configmap.yaml: the agent's environment."""
    # Every data value goes through yq: a bare `*.corp.example` in NO_PROXY
    # would be a YAML alias.
    lines = [
        "kind: ConfigMap",
        "apiVersion: v1",
        "metadata:",
        f"  name: {CONFIGMAP_NAME}",
        f"  namespace: {o['namespace']}",
        "data:",
        f"  HARBOR_ID: {yq(facts['harbor_id'])}",
        f"  SHIP_ID: {yq(o['ship_id'])}",
    ]
    if not o["use_secret"]:
        lines += [
            "  # Readable by anyone who can read ConfigMaps; use_secret=true moves it to a Secret.",
            f"  AUTH_TOKEN: {yq(o['auth_token'])}",
        ]
    lines += ["  CONTAINER_MANAGER_TYPE: KUBERNETES"]
    # Crane's default engine pod is privileged, which restricted PodSecurity,
    # OpenShift's SCC and GKE Autopilot all refuse; the run then hangs at
    # BOOT_STARTING with crane healthy.
    if o["restrict_engines"]:
        lines += [
            "  # Engines run as crane's UID:GID with no capabilities. This meets",
            "  # OpenShift's restricted-v2 SCC, GKE Autopilot and baseline PodSecurity.",
            "  INHERIT_RUNNING_USER_AND_GROUP: 'true'",
            "  KUBERNETES_SECURITY_CONTEXT_CAP_JSON: '{\"drop\": [\"ALL\"]}'",
        ]
    lines += [
        f"  KUBERNETES_SERVICE_USE_TYPE: {yq(o['service_type'])}",
        "  RUN_HEALTH_WEB_SERVICE: 'true'",
    ]
    sv = sv_cfg(facts, o)
    if sv:
        lines += [
            "  # Service virtualization. Endpoints are",
            "  # <virtual-service>-<port>-<namespace>.<subdomain>.",
            f"  KUBERNETES_WEB_EXPOSE_TYPE: {yq(sv['type'].upper())}",
            f"  KUBERNETES_WEB_EXPOSE_SUB_DOMAIN: {yq(sv['subdomain'])}",
            "  # Required even for HTTP virtual services.",
            f"  KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME: {yq(sv['tls_secret'])}",
        ]
        if sv["type"] == "istio":
            lines.append(
                f"  KUBERNETES_ISTIO_GATEWAY_NAME: {yq(sv['istio_gateway'])}"
                if sv["istio_gateway"] else
                "  # KUBERNETES_ISTIO_GATEWAY_NAME unset: crane creates a"
                " Gateway per virtual service."
            )
    if o["private_registry"]:
        overrides = image_overrides(facts, o)
        lines += [
            f"  # Private registry. Images from: {facts.get('images_source', 'unknown')}.",
            f"  DOCKER_REGISTRY: {yq(o['private_registry'])}",
            f"  IMAGE_OVERRIDES: {yq(json.dumps(overrides))}",
        ]
        if o["registry_auth"]:
            lines += [
                "  # BlazeMeter documents these for docker agents. Engine pods pull with",
                "  # the default ServiceAccount's pull secrets (see the README):",
                "  # DOCKER_REGISTRY_USERNAME: <user>",
                "  # DOCKER_REGISTRY_PASSWORD: <password>",
                "  # DOCKER_REGISTRY_EMAIL: <email>",
            ]
    else:
        lines.append(f"  DOCKER_REGISTRY: {yq(PUBLIC_REGISTRY)}")
    if auto_update(o):
        lines += [
            "  # Auto-update on: crane replaces its own image when BlazeMeter",
            "  # releases a newer agent."
            + ("\n  # Mirror the newer tag into your registry first."
               if o["private_registry"] else ""),
            "  AUTO_KUBERNETES_UPDATE: 'true'",
        ]
    else:
        lines += [
            "  # Auto-update off: the agent keeps its image until you re-generate",
            "  # and re-apply. An agent far behind loses support.",
            "  AUTO_KUBERNETES_UPDATE: 'false'",
        ]
    if o["proxy"]:
        if proxy_has_creds(o) and o["use_secret"]:
            lines.append(f"  # HTTP(S)_PROXY carry credentials and are in {SECRET_NAME}.")
        else:
            if proxy_has_creds(o):
                lines.append("  # WARNING: plaintext proxy credentials. Regenerate with"
                             " use_secret=true to move them to a Secret.")
            lines += [f"  {k}: {yq(v)}" for k, v in proxy_env(o).items()
                      if k != "NO_PROXY"]
        lines.append(f"  NO_PROXY: {yq(proxy_env(o)['NO_PROXY'])}")
    eng_sel, eng_tol = engine_scheduling(o)
    split = separate_pools(o)
    if eng_tol:
        lines += [
            "  # Tolerations for the engine pods." if split else
            "  # Engines get the crane pod's tolerations.",
            f"  KUBERNETES_TOLERATIONS_JSON: {yq(json.dumps(eng_tol))}",
        ]
    if eng_sel:
        if split:
            lines.append("  # Engines run on their own node pool; see nodepools.md.")
        lines.append(f"  KUBERNETES_NODE_SELECTOR_JSON: {yq(json.dumps(eng_sel))}")
    # Always emitted, defaults included, so engines run at the engine_size()
    # doctor and the planner certify rather than with no limits.
    lines.append(f"  KUBERNETES_RESOURCES_LIMITS_CPU: "
                 f"{yq(o['engine_cpu_limit'] or ENGINE_DEFAULT_CPU)}")
    lines.append(f"  KUBERNETES_RESOURCES_LIMITS_MEMORY: "
                 f"{yq(o['engine_mem_limit'] or ENGINE_DEFAULT_MEM)}")
    req_cpu, req_mem = engine_request(o)
    lines += [
        "  # Engine requests, equal to the limits (memory in MiB). A location's",
        "  # overrideCPU/overrideMemory, if set in BlazeMeter, replace them.",
        f"  KUBERNETES_RESOURCES_DEFAULT_CPU: {yq(req_cpu)}",
        f"  KUBERNETES_RESOURCES_DEFAULT_MEM: {yq(req_mem)}",
    ]
    if o["engine_ephemeral_request_mb"]:
        lines.append(f"  KUBERNETES_REQUESTS_EPHEMERAL_STORAGE: {yq(o['engine_ephemeral_request_mb'])}")
    if o["engine_ephemeral_limit_mb"]:
        lines.append(f"  KUBERNETES_LIMITS_EPHEMERAL_STORAGE: {yq(o['engine_ephemeral_limit_mb'])}")
    ca = ca_cfg(o)
    if ca:
        ca_comment = {
            "inline": f"  # Corporate CA bundle, from {CA_CONFIGMAP_FILE}.",
            "file": f"  # Corporate CA bundle -- the {ca['cm']} ConfigMap, which you",
            "existing": f"  # CA bundle from the existing ConfigMap '{ca['cm']}', which",
            "inject": "  # OpenShift cluster trust bundle, injected by OpenShift.",
        }[ca["mode"]]
        lines.append(ca_comment)
        if ca["mode"] == "existing":
            lines.append("  # these manifests only read.")
        if ca["mode"] == "file":
            lines.append(f"  # create from {ca['key']} -- see the README.")
        path = f"{CA_MOUNT_PATH}/{ca['key']}"
        lines += [
            "  # Mounted into crane; engines get it via KUBERNETES_CA_BUNDLE_MOUNT.",
            f"  REQUESTS_CA_BUNDLE: {yq(path)}",
            f"  AWS_CA_BUNDLE: {yq(path)}",
            "  KUBERNETES_CA_BUNDLE_MOUNT: " + yq(
                f"REQUESTS_CA_BUNDLE={ca['cm']}={ca['key']}:"
                f"AWS_CA_BUNDLE={ca['cm']}={ca['key']}"),
        ]
    # Last: extra_env refuses every name written above, so nothing here shadows
    # them.
    env = extra_env(o)
    if env:
        lines.append("  # Additional agent variables. They reach crane, not the"
                     " engines.")
        lines += [f"  {k}: {json.dumps(v)}" for k, v in env.items()]
    return "\n".join(lines) + "\n"


def _indent_yaml(obj, indent):
    """Render obj as indented YAML-compatible JSON block lines."""
    pad = " " * indent
    return "\n".join(pad + line for line in json.dumps(obj, indent=2).splitlines())


def _scheduling_block(o):
    """tolerations / nodeSelector for the crane pod. Engines are placed by the
    KUBERNETES_*_JSON variables in the ConfigMap instead.
    """
    sel, tol = crane_scheduling(o)
    out = ""
    if tol:
        out += "      tolerations:\n" + _indent_yaml(tol, 8) + "\n"
    if sel:
        out += "      nodeSelector:\n" + "\n".join(
            f"        {json.dumps(str(k))}: {yq(v)}" for k, v in sel.items()) + "\n"
    return out


def _ca_configmap(facts, o):
    """bzm_cacerts.yaml, for the inline and inject modes; the file and existing
    modes emit none.
    """
    ca = ca_cfg(o)
    if ca["mode"] == "inject":
        return f"""kind: ConfigMap
apiVersion: v1
metadata:
  name: {CA_CONFIGMAP}
  namespace: {o['namespace']}
  labels:
    # OpenShift fills this ConfigMap with the cluster-wide trust bundle.
    config.openshift.io/inject-trusted-cabundle: "true"
"""
    pem = "\n".join("    " + line for line in o["ca_bundle"].strip().splitlines())
    return f"""kind: ConfigMap
apiVersion: v1
metadata:
  name: {CA_CONFIGMAP}
  namespace: {o['namespace']}
data:
  {ca['key']}: |
{pem}
"""


def _proxy_secret_block(o):
    """The proxy URLs for the Secret, where they carry credentials."""
    if not (proxy_has_creds(o) and o["use_secret"]):
        return ""
    lines = ["  # Proxy URLs carrying credentials."]
    lines += [f"  {k}: {yq(v)}" for k, v in proxy_env(o).items() if k != "NO_PROXY"]
    return "\n".join(lines) + "\n"


def _security_context(o):
    """Crane's container securityContext: OpenShift's SCC assigns the UID,
    elsewhere run_as_user pins it.
    """
    if o["platform"] == "openshift":
        return (
            "          securityContext:\n"
            "            # The restricted-v2 SCC assigns the UID.\n"
            "            runAsNonRoot: true\n"
            "            allowPrivilegeEscalation: false\n"
            "            capabilities:\n"
            "              drop:\n"
            "                - ALL"
        )
    return (
        "          securityContext:\n"
        "            runAsNonRoot: true\n"
        f"            runAsUser: {o['run_as_user']}\n"
        f"            runAsGroup: {o['run_as_user']}\n"
        "            allowPrivilegeEscalation: false\n"
        "            capabilities:\n"
        "              drop:\n"
        "                - ALL"
    )


def _hook_sub(o, sv):
    """The crane-hook template's substitutions, kept apart from those of the
    always-emitted templates. The SV variables are set only when there is an
    ingress to check.
    """
    registry = o["private_registry"] or PUBLIC_REGISTRY
    sv_env = ""
    if sv:
        sv_env = (
            f"        - name: KUBERNETES_WEB_EXPOSE_TYPE\n"
            f"          value: {sv['type'].upper()}\n"
            f"        - name: KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME\n"
            f"          value: {sv['tls_secret']}\n")
    return {
        "HOOK_ROLE": HOOK_ROLE_NAME,
        "REGISTRY": registry,
        "HOOK_IMAGE": f"{registry.rstrip('/')}/{HOOK_IMAGE_REPO}:{HOOK_IMAGE_TAG}",
        # Pinned only off OpenShift, whose SCC refuses a pinned UID.
        "HOOK_UID_BLOCK": (
            "" if o["platform"] == "openshift" else
            f"        runAsUser: {o['run_as_user']}\n"
            f"        runAsGroup: {o['run_as_user']}\n"),
        "HOOK_SV_ENV": sv_env,
    }


def _sv_rbac_block(sv):
    """Role rules for publishing virtual services: only the configured
    backend's API group, the only one crane touches. Namespaced, since
    neither backend needs node reads.
    """
    if not sv:
        return ""
    backend = SV_INGRESS_BACKENDS[sv["type"]]
    return "\n".join([
        f"  # Service virtualization: crane publishes one {backend.creates}"
        " per virtual service.",
        f"  - apiGroups: [{backend.group}]",
        f"    resources: [{', '.join(backend.resources)}]",
        "    verbs: [get, list, watch, create, update, patch, delete, deletecollection]",
    ]) + "\n"


def _readme(facts, o, files):
    """README.md for the manifests bundle."""
    ns, kc = o["namespace"], cli(o)
    apply_lines = "\n".join(
        [create_namespace_cmd(o)]
        + [f"{kc} -n {ns} apply -f {f}" for f in APPLY_ORDER if f in files])
    # Client-side apply stores a copy in an annotation capped at 256KB.
    big_ca = ""
    if o["ca_bundle"] and len(o["ca_bundle"]) > 200_000:
        big_ca = (f"\n- The CA bundle is {len(o['ca_bundle']) // 1024}KB, so apply "
                  f"`{CA_CONFIGMAP_FILE}` with `--server-side` -- client-side apply "
                  f"stores a copy in an annotation capped at 256KB.")
    where_token = (SECRET_FILE if o["use_secret"]
                   else f"{CONFIGMAP_FILE} (plain text)")
    token_row = [("AUTH_TOKEN", f"in {where_token}")]
    # Quote the marker the file holds, which may be an older version's.
    if is_placeholder(o["auth_token"]):
        token_row = [("AUTH_TOKEN", f"`{str(o['auth_token']).strip()}` in "
                                    f"{where_token} -- **not supplied**")]
    # Auto-update is off by default, so the common bundle is the one that says
    # the agent will not update itself.
    pinned = ""
    if not auto_update(o):
        pinned = (f"\n- Auto-update is **off**, so the agent stays on "
                  f"`{crane_image(facts, o).rsplit(':', 1)[1]}` until you "
                  f"re-generate\n  and re-apply. An agent far enough behind "
                  f"loses BlazeMeter support.")
    return f"""{bundle_table(facts, o, token_row)}{placeholder_block(facts, o)}{ca_slot_block(o)}
## Deploy

{deploy_steps(o, "Apply")}```
{apply_lines}
```

The first line creates the namespace if it is missing. No file here owns it, so
`{kc} delete -f .` leaves it in place.

{verify_block(o)}
## Worth knowing

{sizing_bullet(facts, o)}{location_bullet(facts, o)}{sa_bullet(o)}{sv_bullet(facts, o)}
{requests_bullet(facts, o)}{pinned}{big_ca}
- `bzm-opl-gen doctor` checks a cluster against all of the above before you apply.
{ignored_block(o)}"""


def render(facts, o, ca, sv, sa):
    """The manifests bundle as {filename: content}, profile.json aside."""
    sub = {
        "SV_RBAC_BLOCK": _sv_rbac_block(sv),
        "NAMESPACE": o["namespace"],
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
        "SERVICE_ACCOUNT": sa,
        "PROXY_SECRET_BLOCK": _proxy_secret_block(o),
        "CRANE_IMAGE": crane_image(facts, o),
        "PULL_SECRETS_BLOCK": (
            f"      imagePullSecrets:\n        - name: {o['pull_secret']}\n"
            if o["pull_secret"] else ""
        ),
        "SECURITY_CONTEXT_BLOCK": _security_context(o),
        "CRANE_CPU_REQUEST": CRANE_CPU_REQUEST,
        "CRANE_MEM_REQUEST": CRANE_MEM_REQUEST,
        "CRANE_CPU_LIMIT": CRANE_CPU_LIMIT,
        "CRANE_MEM_LIMIT": CRANE_MEM_LIMIT,
        "CRANE_EPHEMERAL_STORAGE": (o["crane_ephemeral_storage"]
                                    or CRANE_EPHEMERAL_STORAGE),
        "SECRET_REF_BLOCK": (
            f"            - secretRef:\n                name: {SECRET_NAME}\n"
            if o["use_secret"] else ""
        ),
        "SCHEDULING_BLOCK": _scheduling_block(o),
        "VOLUME_MOUNTS_BLOCK": (
            "          volumeMounts:\n"
            f"            - name: cacerts\n"
            f"              mountPath: {CA_MOUNT_PATH}\n"
            f"              readOnly: true\n"
            if ca else ""
        ),
        "VOLUMES_BLOCK": (
            "      volumes:\n"
            f"        - name: cacerts\n"
            f"          configMap:\n"
            f"            name: {ca['cm']}\n"
            if ca else ""
        ),
    }

    out = {
        CONFIGMAP_FILE: _configmap(facts, o),
        ROLE_FILE: _tpl("role.yaml").substitute(sub),
        ROLEBINDING_FILE: _tpl("rolebinding.yaml").substitute(sub),
        DEPLOYMENT_FILE: _tpl("deployment.yaml").substitute(sub),
    }
    if o["service_account_create"]:
        # Off means the account is somebody else's; applying it would take
        # ownership of it.
        out[SERVICEACCOUNT_FILE] = _tpl("serviceaccount.yaml").substitute(sub)
    if o["use_secret"]:
        out[SECRET_FILE] = _tpl("secret.yaml").substitute(sub)
    if o["cluster_rbac"]:
        out[CLUSTERROLE_FILE] = _tpl("clusterrole.yaml").substitute(sub)
        out[CLUSTERROLEBINDING_FILE] = _tpl(
            "clusterrolebinding.yaml").substitute(sub)
    # `existing` names somebody else's ConfigMap, and `file` one created from
    # the certificate file (the README prints the command); neither is emitted.
    if ca and ca["mode"] in ("inline", "inject"):
        out[CA_CONFIGMAP_FILE] = _ca_configmap(facts, o)
    if o["crane_hook"]:
        out[HOOK_FILE] = _tpl("cranehook.yaml").substitute(
            sub, **_hook_sub(o, sv))
    if o["private_registry"]:
        out[MIRROR_SCRIPT_FILE] = mirror_script(facts, o)
    if separate_pools(o):
        out[NODEPOOLS_FILE] = nodepools_md(facts, o)
    out["README.md"] = _readme(facts, o, out)
    return out
