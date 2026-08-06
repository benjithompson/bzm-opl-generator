"""BlazeMeter's agent-environment reference, as data.

`extra_env` is the escape hatch, and it was a name box and a value box: to use
it somebody had to already know that KUBERNETES_USE_PRE_PULLING exists, spell
it, and know that its value is the word `true`. That is a documentation lookup
performed at the keyboard, and every part of it is somewhere it can go wrong --
a typo produces a variable the agent never reads, and nothing anywhere says so.

So the reference is here, transcribed from

    https://help.blazemeter.com/docs/guide/private-locations-blazemeter-agent-environment-variables.html

and the form offers it as a list with a control per type. Three things follow
from that page being the source rather than a summary of it:

**Every documented variable is declared, including the ones this generator
writes for itself.** `core.agent_env()` subtracts `generate.RESERVED_ENV` at
the point it is served, so a name is offered exactly while no option owns it --
and an option removed later hands its variable back to this list without anyone
remembering to. Declaring only the leftovers would be the same table written
twice, out of sync at the first change.

**The platform is part of the record, and so is the functionality.** They are
two different questions and a performance location was being offered the answer
to both. The page has two tables and they are not the same table:
HOSTNAME_OVERRIDE and TLS_CERT are Docker's, the KUBERNETES_* half is not, and a
form that offered all of them would be offering a setting the agent under the
bundle has no reader for. Beside that, a variable can be documented for this
platform and still reach nothing the *location* runs -- the Grid proxy's port on
a location with no grid -- so each row also names the functionalities that read
it, empty meaning every location. `core.agent_env()` applies both filters at the
point it is served, for the same reason it subtracts RESERVED_ENV there:
declaring only what a given location is offered would be this table written once
per location.

**The type is what the control is chosen from.** A boolean gets a three-way
control rather than a text box (see `env.ts` for why three), an integer a number
box, and a JSON object the key/value table this page uses everywhere else --
nobody types JSON (#127). The values still reach `extra_env` as strings,
because an environment variable is text and `generate.extra_env` refuses
anything else.

`default` is the agent's own default, stated on the row: a variable left unset
is not a variable with no value, and a form that did not say so would make
"leave it alone" look like a gap.
"""

# The two tables on that page. A variable in both is in both tuples.
KUBERNETES = "kubernetes"
DOCKER = "docker"
BOTH = (KUBERNETES, DOCKER)

# What the form builds a control from. `pem` is a string as far as the agent is
# concerned -- it is here because a certificate pasted into a one-line input is
# a one-line input holding 40 lines, and that is a different control rather
# than a different value. It is a claim about what the variable *holds*, never
# about what its name suggests: TLS_CERT reads as a certificate and holds a
# path to one (#181), and typing it `pem` invited the value the agent cannot
# use.
TYPES = ("string", "bool", "int", "json_object", "pem")

DOC_URL = ("https://help.blazemeter.com/docs/guide/"
           "private-locations-blazemeter-agent-environment-variables.html")


def _v(name, type_, platforms, summary, default=None, example=None,
       functionalities=()):
    """One row of the reference.

    `functionalities` is read exactly the way `OptionGroup.functionalities` is
    on the page: the funcIds whose agent has a reader for this variable, and
    **empty means every location**. Empty is therefore both "agent-wide" and
    "nobody has decided", which is the safe direction to be wrong in -- a
    variable offered where it reaches nothing costs a row, one filtered out
    where it was needed costs the setting. The reserved names below are
    untagged for that reason and no other: nothing offers them, so there is no
    decision to record, and if an option is removed later its variable comes
    back offered to everyone rather than to nobody.
    """
    return {"name": name, "type": type_, "platforms": list(platforms),
            "summary": summary, "default": default, "example": example,
            "functionalities": list(functionalities)}


# Transcribed in the page's own order, Docker's table first and then the
# Kubernetes one, minus the names that appear in both. Summaries are the page's
# sentence tightened to fit a row; where the page states a default it is here
# verbatim, and where it does not the field is None rather than a guess.
AGENT_ENV = (
    # -- identity and the credential. Reserved by this generator, declared here
    # so the reference is whole rather than pre-filtered.
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
    # Doduo is BlazeMeter's Selenium *grid* proxy, which is why this and the two
    # _GRID certificates below are GUI functional's rather than agent-wide.
    # Not a new claim: `facts.IMAGE_CATEGORY` already classifies the `doduo`
    # image as `gui`, and a live functionalGui location's /versions carries
    # blazemeter/doduo where a performance-only location's does not -- so the
    # tag agrees with a table this repo already had rather than asserting
    # something beside it.
    _v("DODUO_PORT", "int", BOTH,
       "Port the BlazeMeter Grid proxy (Doduo) listens on", default="8000",
       functionalities=["functionalGui"]),
    # -- virtual services: how they are published
    # BlazeMeter's own reference defines this one, TLS_CERT and TLS_KEY against
    # "transactional virtual services", which is service virtualization -- the
    # sentence is in the summaries below, and the tag is that sentence read as
    # data.
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
    # -- TLS material for the endpoints the agent serves itself. The first pair
    # is the domain HOSTNAME_OVERRIDE names, so it goes where that does; the
    # _GRID pair is Doduo's, so it goes where DODUO_PORT does.
    #
    # These two hold a **path**, not a certificate (#181). BlazeMeter's own
    # example sets them beside the mounts that put the files there --
    # `--env TLS_CERT=/etc/ssl/certs/public.pem -v /path/to/public.pem:/etc/
    # ssl/certs/public.pem` -- which is the same shape REQUESTS_CA_BUNDLE and
    # AWS_CA_BUNDLE above already carry, so they are typed the same way: a
    # string whose example is the path. They were `pem`, which put a
    # certificate textarea on the page, and a certificate pasted into a
    # variable the agent opens as a filename is an agent that starts, reports
    # online and serves no TLS. The summary carries what the name cannot --
    # the path is *inside the container*, so the file has to be mounted there.
    # The _GRID pair below is left `pem` deliberately: it is declared for both
    # platforms, only the Docker side is documented as a path, and what
    # Kubernetes expects is unconfirmed (#186).
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
