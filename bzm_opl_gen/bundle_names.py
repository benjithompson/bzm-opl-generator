"""Names of the files a bundle emits and of the objects in it, stated once for
the renderers, the READMEs, livetest and the MCP server.
"""

import os


# The templates and the helm chart ship inside the package.
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# The chart is copied verbatim into CHART_DIR, and the account's values go
# beside it as an overlay passed with -f, so `helm show values` stays the
# documentation and re-generating never touches the chart.
HELM_DIR = os.path.join(TEMPLATE_DIR, "helm")
CHART_DIR = "helm"
HELM_CHART_FILE = f"{CHART_DIR}/Chart.yaml"
HELM_VALUES_FILE = "bzm-opl-values.yaml"

# The flat manifests.
SERVICEACCOUNT_FILE = "bzm_serviceaccount.yaml"
CONFIGMAP_FILE = "bzm_configmap.yaml"
SECRET_FILE = "bzm_secret.yaml"
ROLE_FILE = "bzm_role.yaml"
ROLEBINDING_FILE = "bzm_rolebinding.yaml"
CLUSTERROLE_FILE = "bzm_clusterrole.yaml"
CLUSTERROLEBINDING_FILE = "bzm_clusterrolebinding.yaml"
DEPLOYMENT_FILE = "bzm_deployment.yaml"

CA_CONFIGMAP_FILE = "bzm_cacerts.yaml"

# The order the manifests README applies them in.
APPLY_ORDER = [
    SERVICEACCOUNT_FILE, CONFIGMAP_FILE, SECRET_FILE, CA_CONFIGMAP_FILE,
    ROLE_FILE, ROLEBINDING_FILE, CLUSTERROLE_FILE, CLUSTERROLEBINDING_FILE,
    DEPLOYMENT_FILE,
]

# The mirror script, the node pool recipe, profile.json, and the sv-expose
# command's output.
MIRROR_SCRIPT_FILE = "bzm-opl-image-mirror.sh"
NODEPOOLS_FILE = "nodepools.md"
PROFILE_FILE = "profile.json"
SV_EXPOSE_FILE = "bzm_sv_expose.yaml"

# Every bundle's list of the images its agent pulls.
IMAGES_FILE = "IMAGES.md"

# Every bundle's document for a change-approval board or security team.
REVIEW_FILE = "SECURITY-REVIEW.md"

# Listed last when a bundle is shown (see generate.preview_order).
PREVIEW_TAIL = [MIRROR_SCRIPT_FILE, REVIEW_FILE, IMAGES_FILE, "README.md"]

# The docker bundle.
DOCKER_RUN_FILE = "bzm-opl-agent.sh"

# `docker compose up -d` finds this name with no -f.
DOCKER_COMPOSE_FILE = "compose.yaml"

# Never `.env`: compose reads that name for interpolation into compose.yaml,
# not into the container.
DOCKER_ENV_FILE = "bzm-opl-agent.env"
DOCKER_CA_FILE = "ca-bundle.crt"
DOCKER_SV_CERT_FILE = "sv-tls.crt"
DOCKER_SV_KEY_FILE = "sv-tls.key"

# Objects in the bundle.
CONFIGMAP_NAME = "blazemeter-configmap"

# The Secret holding the AUTH_TOKEN, as templates/secret.yaml and the chart's
# `bzm-opl.secretName` name it.
SECRET_NAME = "blazemeter-secret"
CA_CONFIGMAP = "blazemeter-cacerts"

# The compose service, which `docker compose logs -f` takes.
DOCKER_COMPOSE_SERVICE = "crane"


def docker_container_name(ship_id):
    """The container name BlazeMeter's own command uses.

    Both docker routes use it, so running the script and compose together fails
    on the name instead of starting two cranes on one agent identity, which
    BlazeMeter reports as duplicated results.
    """
    return f"bzm-crane-{ship_id}"
