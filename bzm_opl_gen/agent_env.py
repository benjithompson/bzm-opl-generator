"""BlazeMeter's agent-environment reference, as data.

Transcribed from

    https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html

so the `extra_env` form can offer each variable with a control per type rather
than asking for a name and a value blind.

Every documented variable is declared, including the ones the generator writes
itself: `core.agent_env()` subtracts `bundle_env.RESERVED_ENV` when serving, so an
option removed later hands its variable back. Each row names its platforms
(the page has a Docker table and a Kubernetes table) and the functionalities
that read it (empty meaning every location); `core.agent_env()` filters by both.
`type` picks the control; values still reach `extra_env` as strings. `default`
is the agent's own default.
"""

# The two tables on that page. A variable in both is in both tuples.
KUBERNETES = "kubernetes"
DOCKER = "docker"
BOTH = (KUBERNETES, DOCKER)

# What the form builds a control from. `pem` is a string to the agent but
# needs a multi-line control; it describes what the variable holds, not what
# its name suggests (TLS_CERT holds a path).
TYPES = ("string", "bool", "int", "json_object", "pem")


def _v(name, type_, platforms, summary, default=None, example=None,
       functionalities=()):
    """One row of the reference.

    `functionalities` are the funcIds whose agent reads this variable; empty
    means every location, the safe direction to be wrong in.
    """
    return {"name": name, "type": type_, "platforms": list(platforms),
            "summary": summary, "default": default, "example": example,
            "functionalities": list(functionalities)}


# In the page's own order (Docker's table, then Kubernetes' remaining names).
# A default is the page's verbatim, or None where the page states none.
AGENT_ENV = (
    # -- identity and the credential (reserved by this generator)
    _v("AUTH_TOKEN", "string", BOTH, "The agent auth token"),
    _v("HARBOR_ID", "string", BOTH,
       "ID of the private location the agent is associated with"),
    _v("SHIP_ID", "string", BOTH, "ID of the agent"),
    # -- what kind of agent, and where its images come from
    _v("CONTAINER_MANAGER_TYPE", "string", BOTH,
       "Container manager deployment type: DOCKER or KUBERNETES",
       default="DOCKER"),
    _v("DOCKER_REGISTRY", "string", BOTH,
       "Address of a private registry to pull agent images from",
       example="localhost:5000"),
    _v("DOCKER_REGISTRY_USERNAME", "string", (DOCKER,),
       "User name for the private registry"),
    _v("DOCKER_REGISTRY_PASSWORD", "string", (DOCKER,),
       "Password for the private registry"),
    _v("DOCKER_REGISTRY_EMAIL", "string", (DOCKER,),
       "Email for the private registry"),
    _v("IMAGE_OVERRIDES", "json_object", (KUBERNETES,),
       "Replace the images BlazeMeter names with your own, per image",
       example='{"blazemeter/crane:latest": '
               '"registry.example.com/blazemeter/crane:3.6.47"}'),
    # -- self-update
    _v("AUTO_UPDATE", "bool", (DOCKER,),
       "Whether the agent updates itself", default="true"),
    _v("AUTO_KUBERNETES_UPDATE", "bool", (KUBERNETES,),
       "Activate the Kubernetes auto updater", default="false"),
    _v("KUBERNETES_USE_PRE_PULLING", "bool", (KUBERNETES,),
       "Pre-pull images across the cluster when BlazeMeter components update",
       default="false"),
    # -- the engine security posture
    _v("INHERIT_RUNNING_USER_AND_GROUP", "bool", BOTH,
       "Containers the agent launches run as the same UID:GID as crane",
       default="false"),
    # -- proxying
    _v("HTTP_PROXY", "string", BOTH,
       "URL of the HTTP proxy for requests to a.blazemeter.com"),
    _v("HTTPS_PROXY", "string", BOTH,
       "URL of the HTTPS proxy for requests to a.blazemeter.com"),
    _v("NO_PROXY", "string", BOTH,
       "Hosts to contact directly rather than through the proxy"),
    # -- CA trust
    _v("REQUESTS_CA_BUNDLE", "string", BOTH,
       "Where the agent reads its CA bundle from",
       default="/etc/ssl/certs/ca-certificates.crt"),
    _v("AWS_CA_BUNDLE", "string", BOTH,
       "Where the agent's AWS client reads its CA bundle from",
       default="/etc/ssl/certs/ca-certificates.crt"),
    _v("VERIFY_SSL", "bool", BOTH,
       "Verify certificates on outbound HTTPS. Off needs no CA bundle and "
       "trusts anything on the path",
       default="true"),
    # -- networking
    _v("PREFERRED_INTERFACE", "string", BOTH,
       "Network interface to read the machine's IP address from",
       default="the first interface that is not docker0 or lo",
       example="eth0"),
    # Doduo is the Selenium grid proxy, present only on GUI functional agents.
    _v("DODUO_PORT", "int", BOTH,
       "Port the BlazeMeter Grid proxy (Doduo) listens on", default="8000",
       functionalities=["functionalGui"]),
    # -- virtual services: how they are published
    _v("HOSTNAME_OVERRIDE", "string", (DOCKER,),
       "Hostname for transactional virtual services created on this agent",
       functionalities=["mockServices"]),
    _v("KUBERNETES_WEB_EXPOSE_TYPE", "string", (KUBERNETES,),
       "How virtual services are published: INGRESS, CONTOUR or ISTIO"),
    _v("KUBERNETES_WEB_EXPOSE_SUB_DOMAIN", "string", (KUBERNETES,),
       "Subdomain the virtual-service endpoints are published under",
       example="mocks.example.com"),
    _v("KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME", "string", (KUBERNETES,),
       "TLS secret holding the key and certificate for that subdomain"),
    _v("KUBERNETES_ISTIO_GATEWAY_NAME", "string", (KUBERNETES,),
       "Name of an existing Istio Gateway to publish through; one is created "
       "if unset"),
    _v("KUBERNETES_WEB_EXPOSE_SHORT_URL", "bool", (KUBERNETES,),
       "Shorter ingress URLs, omitting namespace and container port. Limits a "
       "container to one exposed port",
       default="false", functionalities=["mockServices"]),
    _v("KUBERNETES_SERVICE_USE_TYPE", "string", (KUBERNETES,),
       "Service type for virtual services: NODEPORT or CLUSTERIP",
       default="NODEPORT"),
    _v("KUBERNETES_SERVICES_BLOCKING_GET", "bool", (KUBERNETES,),
       "Wait for each Service to be readable before continuing. For agents "
       "creating many transactional virtual services at once",
       default="false", functionalities=["mockServices"]),
    _v("KUBERNETES_USE_APIPA", "bool", (KUBERNETES,),
       "Publish endpoints on the node's IP address rather than 127.0.0.1",
       default="true", functionalities=["mockServices"]),
    # -- TLS material for the endpoints the agent serves itself. TLS_CERT and
    # TLS_KEY hold a path inside the container, where the file must be mounted.
    # The _GRID pair stays `pem`: only its Docker side is documented as a path.
    _v("TLS_CERT", "string", (DOCKER,),
       "Path in the container to the public certificate for the domain in "
       "HOSTNAME_OVERRIDE; mount the file there",
       example="/etc/ssl/certs/public.pem",
       functionalities=["mockServices"]),
    _v("TLS_KEY", "string", (DOCKER,),
       "Path in the container to the private key for the domain in "
       "HOSTNAME_OVERRIDE; mount the file there",
       example="/etc/ssl/certs/privatekey.pem",
       functionalities=["mockServices"]),
    _v("TLS_CERT_GRID", "pem", BOTH,
       "Public certificate for the domain the Grid proxy serves over HTTPS",
       functionalities=["functionalGui"]),
    _v("TLS_KEY_GRID", "pem", BOTH,
       "Private key for the domain the Grid proxy serves over HTTPS",
       functionalities=["functionalGui"]),
    # -- what the agent puts on the objects it creates
    _v("KUBERNETES_LABELS", "json_object", (KUBERNETES,),
       "Labels added to every object the agent creates",
       example='{"team": "perf", "cost-centre": "1234"}'),
    _v("KUBERNETES_CUSTOM_ANNOTATIONS_JSON", "json_object", (KUBERNETES,),
       "Annotations added to every pod the agent creates",
       example='{"karpenter.sh/do-not-disrupt": "true"}'),
    _v("KUBERNETES_NODE_SELECTOR_JSON", "json_object", (KUBERNETES,),
       "nodeSelector for the engine pods crane creates",
       example='{"pool": "bzm-engines"}'),
    _v("KUBERNETES_TOLERATIONS_JSON", "string", (KUBERNETES,),
       "Tolerations for the engine pods crane creates, as a JSON array"),
    # -- engine resources
    _v("KUBERNETES_RESOURCES_LIMITS_CPU", "string", (KUBERNETES,),
       "CPU limit for the pods the agent creates"),
    _v("KUBERNETES_RESOURCES_LIMITS_MEMORY", "string", (KUBERNETES,),
       "Memory limit for the pods the agent creates"),
    _v("KUBERNETES_REQUESTS_EPHEMERAL_STORAGE", "int", (KUBERNETES,),
       "Ephemeral storage request of the Taurus pod, in megabytes",
       default="100"),
    _v("KUBERNETES_LIMITS_EPHEMERAL_STORAGE", "int", (KUBERNETES,),
       "Ephemeral storage limit of the Taurus pod, in megabytes",
       example="8192"),
)

AGENT_ENV_BY_NAME = {v["name"]: v for v in AGENT_ENV}
