"""Render deployable manifests from templates + facts + customer options."""

import json
import os
from string import Template

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
    "proxy": None,                   # {"http": ..., "https": ..., "no_proxy": ...}
    "gui": False,                    # include GUI-functional images in overrides
    "run_as_user": 1337,             # k8s platform only (openshift: SCC assigns)
}


def _tpl(name):
    with open(os.path.join(TEMPLATE_DIR, name)) as f:
        return Template(f.read())


def _image_overrides(facts, registry, gui):
    """Build crane IMAGE_OVERRIDES JSON from account facts."""
    entries = {}
    for img in facts["images"]:
        if not img.get("key"):
            continue
        if not gui and not img.get("performance", True):
            continue
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
        overrides = _image_overrides(facts, o["private_registry"], o["gui"])
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
        if p.get("http"):
            lines.append(f"  HTTP_PROXY: {p['http']}")
        if p.get("https"):
            lines.append(f"  HTTPS_PROXY: {p['https']}")
        lines.append(f"  NO_PROXY: {p.get('no_proxy', 'kubernetes.default,127.0.0.1,localhost')}")
    return "\n".join(lines) + "\n"


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

    sub = {
        "NAMESPACE": o["namespace"],
        "HARBOR_ID": facts["harbor_id"],
        "SHIP_ID": o["ship_id"],
        "AUTH_TOKEN": o["auth_token"],
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
    out["README.md"] = _readme(facts, o, out)
    return out


APPLY_ORDER = [
    "bzm_serviceaccount.yaml", "bzm_configmap.yaml", "bzm_secret.yaml",
    "bzm_role.yaml", "bzm_rolebinding.yaml",
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
            f"{i['repo']}:{i['tag']}" for i in facts["images"]
            if i.get("key") and (o["gui"] or i.get("performance", True))
        ]
        mirror = (
            "\n## Mirror these images into "
            f"`{o['private_registry']}` first\n\n```\n" + "\n".join(imgs) + "\n```\n"
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
