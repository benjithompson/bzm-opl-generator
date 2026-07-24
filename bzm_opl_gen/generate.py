"""Render deployable manifests from templates + facts + customer options."""

import json
import os
from string import Template
from urllib.parse import quote

from .facts import select_images

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

DEFAULT_OPTIONS = {
    "platform": "openshift",        # openshift | k8s
    "namespace": "blazemeter",
    "use_secret": True,              # False -> AUTH_TOKEN in ConfigMap (simplified)
    "auth_token": "<YOUR_AUTH_TOKEN>",
    "private_registry": None,        # e.g. registry.example.com/blazemeter
    "pull_secret": None,             # name of docker-registry secret for crane image
    "registry_auth": False,          # emit commented DOCKER_REGISTRY_USERNAME/PASSWORD
    "cluster_rbac": False,           # include optional ClusterRole/Binding files
    "service_type": "CLUSTERIP",    # CLUSTERIP | NODEPORT
    # {"http", "https", "no_proxy", "username", "password"} -- credentials are
    # embedded in the proxy URL (user:pass@host, per BlazeMeter docs) and the
    # URL moves into the Secret when use_secret is on.
    "proxy": None,
    "run_as_user": 1337,             # k8s platform only (openshift: SCC assigns)
    # Real-cluster scheduling / trust / sizing (all optional):
    "tolerations": None,             # k8s toleration list -> crane pod + engines
    "node_selector": None,           # {"label": "value"} -> crane pod + engines
    # CA trust -- pick ONE mode:
    "ca_bundle": None,               # inline PEM -> generator creates the ConfigMap
    "ca_existing_configmap": None,   # name of a ConfigMap the platform team owns/rotates
    "ca_configmap_key": None,        # bundle file key within it (default ca-bundle.crt)
    "ca_openshift_inject": False,    # labeled empty CM; OpenShift injects cluster trust
    "engine_cpu_limit": None,        # e.g. "2" -> KUBERNETES_RESOURCES_LIMITS_CPU
    "engine_mem_limit": None,        # e.g. "8Gi" -> KUBERNETES_RESOURCES_LIMITS_MEMORY
    "engine_ephemeral_request_mb": None,  # int MB -> KUBERNETES_REQUESTS_EPHEMERAL_STORAGE
    "engine_ephemeral_limit_mb": None,    # int MB -> KUBERNETES_LIMITS_EPHEMERAL_STORAGE
}

CA_MOUNT_PATH = "/var/cm"
CA_FILENAME = "ca-bundle.crt"
CA_CONFIGMAP = "blazemeter-cacerts"


def _ca_cfg(o):
    """Resolve the CA-trust mode to {cm, key, mode} or None.

    Ownership model:
      existing -- the platform/security team owns and rotates a trust-bundle
                  ConfigMap in the namespace (e.g. written by trust-manager);
                  we only reference it.
      inline   -- the app team owns the PEM; the generator creates the ConfigMap.
      inject   -- OpenShift-only: we emit an empty ConfigMap labeled
                  config.openshift.io/inject-trusted-cabundle=true and the
                  cluster network operator injects ca-bundle.crt into it.
    """
    modes = [("inline", bool(o["ca_bundle"])),
             ("existing", bool(o["ca_existing_configmap"])),
             ("inject", bool(o["ca_openshift_inject"]))]
    active = [m for m, on in modes if on]
    if len(active) > 1:
        raise ValueError("choose one CA mode: ca_bundle (inline PEM) | "
                         "ca_existing_configmap | ca_openshift_inject")
    if not active:
        return None
    mode = active[0]
    if mode == "existing":
        return {"cm": o["ca_existing_configmap"],
                "key": o["ca_configmap_key"] or CA_FILENAME, "mode": mode}
    # inline + inject both use our own ConfigMap; inject's key is fixed to
    # ca-bundle.crt (the key OpenShift writes into labeled ConfigMaps).
    return {"cm": CA_CONFIGMAP, "key": CA_FILENAME, "mode": mode}


def _proxy_url(url, p):
    """Embed credentials in the proxy URL (http://user:pass@host:port) --
    BlazeMeter has no separate proxy-auth env vars; the URL carries them."""
    user = p.get("username")
    if not url or not user:
        return url
    userinfo = quote(user, safe="")
    if p.get("password"):
        userinfo += ":" + quote(p["password"], safe="")
    scheme, sep, rest = url.partition("://")
    return f"{scheme}{sep}{userinfo}@{rest}" if sep else f"{userinfo}@{url}"


def _proxy_has_creds(o):
    return bool(o["proxy"] and o["proxy"].get("username"))


def _tpl(name):
    with open(os.path.join(TEMPLATE_DIR, name)) as f:
        return Template(f.read())


def _image_overrides(facts, registry):
    """Build crane IMAGE_OVERRIDES JSON from account facts. Which images are
    included follows the location's enabled funcIds (facts.select_images)."""
    entries = {}
    for img in select_images(facts):
        name = img["repo"].rsplit("/", 1)[-1]
        entries[img["key"]] = f"{registry.rstrip('/')}/{name}:{img['tag']}"
    return entries


def _configmap(facts, o):
    lines = [
        "kind: ConfigMap",
        "apiVersion: v1",
        "metadata:",
        "  name: blazemeter-configmap",
        f"  namespace: {o['namespace']}",
        "data:",
        f"  HARBOR_ID: \"{facts['harbor_id']}\"",
        f"  SHIP_ID: \"{o['ship_id']}\"",
    ]
    if not o["use_secret"]:
        lines += [
            "  # Simplified: AUTH_TOKEN in ConfigMap. Hardened option: move to a Secret",
            "  # (regenerate with use_secret=true).",
            f"  AUTH_TOKEN: \"{o['auth_token']}\"",
        ]
    lines += ["  CONTAINER_MANAGER_TYPE: KUBERNETES"]
    if o["platform"] == "openshift":
        lines += [
            "  # OpenShift non-privileged path: engines inherit crane's SCC-assigned",
            "  # UID:GID and drop all capabilities, so spawned engine pods also pass",
            "  # the restricted-v2 SCC.",
            "  INHERIT_RUNNING_USER_AND_GROUP: 'true'",
            "  KUBERNETES_SECURITY_CONTEXT_CAP_JSON: '{\"drop\": [\"ALL\"]}'",
        ]
    lines += [
        f"  KUBERNETES_SERVICE_USE_TYPE: {o['service_type']}",
        "  RUN_HEALTH_WEB_SERVICE: 'true'",
    ]
    if o["private_registry"]:
        overrides = _image_overrides(facts, o["private_registry"])
        lines += [
            "  # Private registry: images resolved from the account's live agent",
            f"  # inventory ({facts.get('images_source', 'unknown')}).",
            f"  DOCKER_REGISTRY: {o['private_registry']}",
            f"  IMAGE_OVERRIDES: '{json.dumps(overrides)}'",
            "  # Auto-update pulls from BlazeMeter's public registry -- disabled for",
            "  # private-registry clusters. Upgrade = re-mirror + bump tags.",
            "  AUTO_KUBERNETES_UPDATE: 'false'",
        ]
        if o["registry_auth"]:
            lines += [
                "  # Crane-side auth for engine image pulls (or use cluster pull secrets):",
                "  # DOCKER_REGISTRY_USERNAME: <user>",
                "  # DOCKER_REGISTRY_PASSWORD: <password>",
                "  # DOCKER_REGISTRY_EMAIL: <email>",
            ]
    else:
        lines += [
            "  DOCKER_REGISTRY: gcr.io/verdant-bulwark-278",
            "  AUTO_KUBERNETES_UPDATE: 'true'",
        ]
    if o["proxy"]:
        p = o["proxy"]
        if _proxy_has_creds(o) and o["use_secret"]:
            lines.append("  # HTTP(S)_PROXY embed credentials -> kept in blazemeter-secret.")
        else:
            if _proxy_has_creds(o):
                lines.append("  # WARNING: proxy credentials below are plaintext -- anyone who can")
                lines.append("  # read ConfigMaps sees them. Regenerate with use_secret=true.")
            if p.get("http"):
                lines.append(f"  HTTP_PROXY: \"{_proxy_url(p['http'], p)}\"")
            if p.get("https"):
                lines.append(f"  HTTPS_PROXY: \"{_proxy_url(p['https'], p)}\"")
        lines.append(f"  NO_PROXY: {p.get('no_proxy', 'kubernetes.default,127.0.0.1,localhost')}")
    if o["tolerations"]:
        lines += [
            "  # Engines inherit the crane pod's tolerations via this env.",
            f"  KUBERNETES_TOLERATIONS_JSON: '{json.dumps(o['tolerations'])}'",
        ]
    if o["node_selector"]:
        lines.append(f"  KUBERNETES_NODE_SELECTOR_JSON: '{json.dumps(o['node_selector'])}'")
    if o["engine_cpu_limit"]:
        lines.append(f"  KUBERNETES_RESOURCES_LIMITS_CPU: \"{o['engine_cpu_limit']}\"")
    if o["engine_mem_limit"]:
        lines.append(f"  KUBERNETES_RESOURCES_LIMITS_MEMORY: \"{o['engine_mem_limit']}\"")
    if o["engine_ephemeral_request_mb"]:
        lines.append(f"  KUBERNETES_REQUESTS_EPHEMERAL_STORAGE: \"{o['engine_ephemeral_request_mb']}\"")
    if o["engine_ephemeral_limit_mb"]:
        lines.append(f"  KUBERNETES_LIMITS_EPHEMERAL_STORAGE: \"{o['engine_ephemeral_limit_mb']}\"")
    ca = _ca_cfg(o)
    if ca:
        ca_comment = {
            "inline": "  # Corporate CA bundle (generator-created ConfigMap).",
            "existing": f"  # CA bundle from existing ConfigMap '{ca['cm']}' -- the platform",
            "inject": "  # OpenShift cluster trust bundle (operator-injected ConfigMap).",
        }[ca["mode"]]
        lines.append(ca_comment)
        if ca["mode"] == "existing":
            lines.append("  # team owns and rotates it; these manifests only reference it.")
        path = f"{CA_MOUNT_PATH}/{ca['key']}"
        lines += [
            "  # Mounted into crane; engines get the same ConfigMap mounted via",
            "  # KUBERNETES_CA_BUNDLE_MOUNT (ENV=configmapName=fileKey).",
            f"  REQUESTS_CA_BUNDLE: {path}",
            f"  AWS_CA_BUNDLE: {path}",
            f"  KUBERNETES_CA_BUNDLE_MOUNT: \"REQUESTS_CA_BUNDLE={ca['cm']}={ca['key']}:AWS_CA_BUNDLE={ca['cm']}={ca['key']}\"",
        ]
    return "\n".join(lines) + "\n"


def _indent_yaml(obj, indent):
    """Render obj as indented YAML-compatible JSON block lines."""
    pad = " " * indent
    return "\n".join(pad + line for line in json.dumps(obj, indent=2).splitlines())


def _scheduling_block(o):
    """tolerations / nodeSelector for the crane pod itself (engines get the
    matching env vars in the ConfigMap)."""
    out = ""
    if o["tolerations"]:
        out += "      tolerations:\n" + _indent_yaml(o["tolerations"], 8) + "\n"
    if o["node_selector"]:
        out += "      nodeSelector:\n" + "\n".join(
            f"        {k}: \"{v}\"" for k, v in o["node_selector"].items()) + "\n"
    return out


def _ca_configmap(facts, o):
    ca = _ca_cfg(o)
    if ca["mode"] == "inject":
        return f"""kind: ConfigMap
apiVersion: v1
metadata:
  name: {CA_CONFIGMAP}
  namespace: {o['namespace']}
  labels:
    # OpenShift's network operator injects the cluster-wide trust bundle
    # (proxy/custom CAs configured at cluster level) as ca-bundle.crt.
    # Nobody hand-manages PEMs; rotation is the cluster's job.
    config.openshift.io/inject-trusted-cabundle: "true"
"""
    pem = "\n".join("    " + line for line in o["ca_bundle"].strip().splitlines())
    return f"""kind: ConfigMap
apiVersion: v1
metadata:
  name: {CA_CONFIGMAP}
  namespace: {o['namespace']}
data:
  {CA_FILENAME}: |
{pem}
"""


def _proxy_secret_block(o):
    if not (_proxy_has_creds(o) and o["use_secret"]):
        return ""
    p = o["proxy"]
    lines = ["  # Proxy URLs embed credentials (user:pass@host) -> kept out of the ConfigMap."]
    if p.get("http"):
        lines.append(f"  HTTP_PROXY: \"{_proxy_url(p['http'], p)}\"")
    if p.get("https"):
        lines.append(f"  HTTPS_PROXY: \"{_proxy_url(p['https'], p)}\"")
    return "\n".join(lines) + "\n"


def _mirror_script(facts, o):
    refs = [facts["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in select_images(facts)
    ]
    reg = o["private_registry"].rstrip("/")
    lines = [
        "#!/usr/bin/env bash",
        "# Mirror the BlazeMeter images this private location needs into your",
        "# private registry. Engines are amd64-only; --platform matters on ARM hosts.",
        f"# Location: {facts.get('harbor_name')} ({facts['harbor_id']}), "
        f"source: {facts.get('images_source')}",
        "set -euo pipefail",
        "",
    ]
    for ref in refs:
        name = ref.rsplit("/", 1)[-1]
        lines += [
            f"docker pull --platform linux/amd64 {ref}",
            f"docker tag {ref} {reg}/{name}",
            f"docker push {reg}/{name}",
            "",
        ]
    return "\n".join(lines)


def _security_context(o):
    if o["platform"] == "openshift":
        return (
            "          securityContext:\n"
            "            # restricted-v2 SCC assigns an in-range UID; leave runAsUser\n"
            "            # unset for portability.\n"
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


def _crane_image(facts, o):
    if not o["private_registry"]:
        return facts["crane_image"]
    tag = facts["crane_image"].rsplit(":", 1)[1]
    return f"{o['private_registry'].rstrip('/')}/crane:{tag}"


def generate(facts, options):
    """Return {filename: content}. options overrides DEFAULT_OPTIONS."""
    o = {**DEFAULT_OPTIONS, **options}
    if "ship_id" not in o:
        ships = facts.get("ships") or []
        if len(ships) == 1:
            o["ship_id"] = ships[0]["id"]
        else:
            raise ValueError(
                f"ship_id required: location has {len(ships)} ships "
                f"({[s['id'] for s in ships]})"
            )

    ca = _ca_cfg(o)
    sub = {
        "NAMESPACE": o["namespace"],
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
        "PROXY_SECRET_BLOCK": _proxy_secret_block(o),
        "CRANE_IMAGE": _crane_image(facts, o),
        "PULL_SECRETS_BLOCK": (
            f"      imagePullSecrets:\n        - name: {o['pull_secret']}\n"
            if o["pull_secret"] else ""
        ),
        "SECURITY_CONTEXT_BLOCK": _security_context(o),
        "SECRET_REF_BLOCK": (
            "            - secretRef:\n                name: blazemeter-secret\n"
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
        "bzm_serviceaccount.yaml": _tpl("serviceaccount.yaml").substitute(sub),
        "bzm_configmap.yaml": _configmap(facts, o),
        "bzm_role.yaml": _tpl("role.yaml").substitute(sub),
        "bzm_rolebinding.yaml": _tpl("rolebinding.yaml").substitute(sub),
        "bzm_deployment.yaml": _tpl("deployment.yaml").substitute(sub),
    }
    if o["use_secret"]:
        out["bzm_secret.yaml"] = _tpl("secret.yaml").substitute(sub)
    if o["cluster_rbac"]:
        out["bzm_clusterrole.yaml"] = _tpl("clusterrole.yaml").substitute(sub)
        out["bzm_clusterrolebinding.yaml"] = _tpl("clusterrolebinding.yaml").substitute(sub)
    if ca and ca["mode"] in ("inline", "inject"):
        out["bzm_cacerts.yaml"] = _ca_configmap(facts, o)
    if o["private_registry"]:
        out["bzm-opl-image-mirror.sh"] = _mirror_script(facts, o)
    out["README.md"] = _readme(facts, o, out)
    return out


APPLY_ORDER = [
    "bzm_serviceaccount.yaml", "bzm_configmap.yaml", "bzm_secret.yaml",
    "bzm_cacerts.yaml", "bzm_role.yaml", "bzm_rolebinding.yaml",
    "bzm_clusterrole.yaml", "bzm_clusterrolebinding.yaml",
    "bzm_deployment.yaml",
]


def _readme(facts, o, files):
    cli = "oc" if o["platform"] == "openshift" else "kubectl"
    apply_lines = "\n".join(
        f"{cli} -n {o['namespace']} apply -f {f}" for f in APPLY_ORDER if f in files
    )
    mirror = ""
    if o["private_registry"]:
        imgs = [facts["crane_image"]] + [
            f"{i['repo']}:{i['tag']}" for i in select_images(facts)
        ]
        mirror = (
            "\n## Mirror these images into "
            f"`{o['private_registry']}` first\n\nRun the included "
            "`bzm-opl-image-mirror.sh` (docker pull/tag/push, forced linux/amd64), "
            "or mirror manually:\n\n```\n" + "\n".join(imgs) + "\n```\n"
        )
    return f"""# BlazeMeter OPL -- generated manifests

- Location: **{facts.get('harbor_name')}** (`{facts['harbor_id']}`), features: {facts.get('func_ids')}
- Agent (ship): `{o['ship_id']}`
- Platform: {o['platform']}, namespace: `{o['namespace']}`
- AUTH_TOKEN: {"in Secret (bzm_secret.yaml)" if o['use_secret'] else "in ConfigMap (simplified)"}
{mirror}
## Apply

```
{apply_lines}
```

## Verify

```
{cli} -n {o['namespace']} rollout status deploy/crane
{cli} -n {o['namespace']} logs -l role=role-crane -f
```

Then confirm the agent shows **online** in BlazeMeter (Settings -> Private Locations),
or run the generator's live test: `bzm-opl-gen livetest ...`

Engines need **2 CPU + 8Gi RAM + 60GB disk (40GB /tmp)** per concurrent engine.
Egress required to *.blazemeter.com and the image registry.
"""


def write(files, outdir):
    os.makedirs(outdir, exist_ok=True)
    for name, content in files.items():
        with open(os.path.join(outdir, name), "w") as f:
            f.write(content)
    return sorted(files)
