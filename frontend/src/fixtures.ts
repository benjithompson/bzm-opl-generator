// Payloads shared by more than one test file, declared once each. Copies of
// served tables are held equal to the Python originals by tests/test_server.py.
import {
  AgentEnvVar, SizingModel, SlotMinimum,
} from "./api";

/** core.SLOT_MINIMUMS as /api/slot-minimums serves it. The only copy, held
 *  equal by test_server.py; `message` is BlazeMeter's sentence verbatim. */
export const SLOT_MINIMUMS: Record<string, SlotMinimum> = {
  functionalGui: {
    label: "GUI Functional",
    minimum: 2,
    message: "The option Parallel engine runs must be greater than 1 for a "
      + "Private Location with the GUI Functional Functionality enabled.",
  },
};

/** plan.SIZING_MODELS as /api/sizing-models serves it. The only copy, held
 *  equal by test_server.py; `measured: false` must stay as served. */
export const SIZING_MODELS: SizingModel[] = [
  { functionality: "performance", label: "Performance",
    unit: "virtual users", figure_unit: "virtual users per engine",
    pods: "engines", measured: true, example_target: 5000 },
  { functionality: "functionalGui", label: "GUI Functional",
    unit: "browser instances", figure_unit: "browser instances per engine",
    pods: "engines", measured: true, example_target: 20 },
  { functionality: "mockServices", label: "Service Virtualization",
    unit: "requests per second", figure_unit: "requests per second per core",
    pods: "mock pods", measured: false, example_target: 2000 },
];

/** bundle_options.IGNORED_BY_FORMAT as /api/ignored-options serves it. The only
 *  copy, held equal by test_server.py. Every format is stated, `{}` included:
 *  a missing format would mean "not read yet" to the page. */
export const IGNORED_BY_FORMAT: Record<string, Record<string, string>> = {
  manifests: {
    sv_hostname: "HOSTNAME_OVERRIDE is a docker variable; a Kubernetes agent "
      + "returns a DNS-based URL and needs no hostname override",
    sv_tls_cert: "a Kubernetes agent serves its endpoints through the ingress, "
      + "which reads the certificate from sv_tls_secret",
    sv_tls_key: "a Kubernetes agent serves its endpoints through the ingress, "
      + "which reads the key from sv_tls_secret",
  },
  helm: {
    sv_hostname: "HOSTNAME_OVERRIDE is a docker variable; a Kubernetes agent "
      + "returns a DNS-based URL and needs no hostname override",
    sv_tls_cert: "a Kubernetes agent serves its endpoints through the ingress, "
      + "which reads the certificate from sv_tls_secret",
    sv_tls_key: "a Kubernetes agent serves its endpoints through the ingress, "
      + "which reads the key from sv_tls_secret",
  },
  docker: {
    platform: "there is no OpenShift/Kubernetes distinction on a docker host",
    openshift_cluster: "there is no cluster, so no oc and no Route",
    namespace: "containers are not namespaced",
    service_account_name: "there is no ServiceAccount to run as",
    service_account_create: "there is no ServiceAccount to create",
    cluster_rbac: "there is no RBAC",
    service_type: "KUBERNETES_SERVICE_USE_TYPE is a Kubernetes variable",
    pull_secret: "the host's own docker login is what authenticates a pull",
    run_as_user: "the container runs as root (-u 0) because that is what opens "
      + "the docker socket it starts engines through",
    restrict_engines: "engine security context is a pod field",
    tolerations: "scheduling is a Kubernetes concern",
    node_selector: "scheduling is a Kubernetes concern",
    engine_tolerations: "scheduling is a Kubernetes concern",
    engine_node_selector: "scheduling is a Kubernetes concern",
    engine_cpu_limit: "KUBERNETES_RESOURCES_LIMITS_CPU is a Kubernetes variable",
    engine_mem_limit: "KUBERNETES_RESOURCES_LIMITS_MEMORY is a Kubernetes variable",
    engine_ephemeral_request_mb: "ephemeral storage is a pod field",
    engine_ephemeral_limit_mb: "ephemeral storage is a pod field",
    crane_ephemeral_storage: "ephemeral storage is a pod field",
    ca_existing_configmap: "there is no ConfigMap; the bundle mounts a file",
    ca_configmap_key: "there is no ConfigMap; the bundle mounts a file",
    ca_openshift_inject: "nothing injects a trust bundle into a container",
    engines_per_node: "there is one host, and it is this one",
    sv_ingress: "KUBERNETES_WEB_EXPOSE_TYPE is a Kubernetes variable; a docker "
      + "agent publishes under sv_hostname instead",
    sv_subdomain: "KUBERNETES_WEB_EXPOSE_SUB_DOMAIN is a Kubernetes variable; "
      + "the docker agent's host is sv_hostname",
    sv_tls_secret: "there is no Secret to name; this bundle mounts sv_tls_cert "
      + "and sv_tls_key as files",
    sv_istio_gateway: "istio is a Kubernetes service mesh",
    registry_auth: "the stubs are ConfigMap lines; a docker host authenticates "
      + "with its own docker login",
  },
};


/** bundle_env.RESERVED_ENV with each name's owning option, as
 *  /api/reserved-env serves it. The only copy, held equal by test_server.py;
 *  `null` is a name no single option owns. */
export const RESERVED_ENV: Record<string, string | null> = {
  AUTH_TOKEN: "auth_token",
  AUTO_KUBERNETES_UPDATE: "auto_update",
  AUTO_UPDATE: "auto_update",
  AWS_CA_BUNDLE: "ca_existing_configmap | ca_bundle | ca_bundle_slot | ca_openshift_inject",
  CONTAINER_MANAGER_TYPE: null,
  DOCKER_PORT_RANGE: null,
  DOCKER_REGISTRY: "private_registry",
  DOCKER_REGISTRY_EMAIL: "registry_auth",
  DOCKER_REGISTRY_PASSWORD: "registry_auth",
  DOCKER_REGISTRY_USERNAME: "registry_auth",
  HARBOR_ID: null,
  HOSTNAME_OVERRIDE: "sv_hostname",
  HTTPS_PROXY: "proxy",
  HTTP_PROXY: "proxy",
  IMAGE_OVERRIDES: "private_registry",
  INHERIT_RUNNING_USER_AND_GROUP: "restrict_engines",
  KUBERNETES_CA_BUNDLE_MOUNT: "ca_existing_configmap | ca_bundle | ca_bundle_slot | ca_openshift_inject",
  KUBERNETES_ISTIO_GATEWAY_NAME: "sv_istio_gateway",
  KUBERNETES_LIMITS_EPHEMERAL_STORAGE: "engine_ephemeral_limit_mb",
  KUBERNETES_NODE_SELECTOR_JSON: "engine_node_selector",
  KUBERNETES_REQUESTS_EPHEMERAL_STORAGE: "engine_ephemeral_request_mb",
  KUBERNETES_RESOURCES_DEFAULT_CPU: "engine_cpu_limit",
  KUBERNETES_RESOURCES_DEFAULT_MEM: "engine_mem_limit",
  KUBERNETES_RESOURCES_LIMITS_CPU: "engine_cpu_limit",
  KUBERNETES_RESOURCES_LIMITS_MEMORY: "engine_mem_limit",
  KUBERNETES_SECURITY_CONTEXT_CAP_JSON: "restrict_engines",
  KUBERNETES_SERVICE_USE_TYPE: "service_type",
  KUBERNETES_TOLERATIONS_JSON: "engine_tolerations",
  KUBERNETES_WEB_EXPOSE_SUB_DOMAIN: "sv_subdomain",
  KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME: "sv_tls_secret",
  KUBERNETES_WEB_EXPOSE_TYPE: "sv_ingress",
  NO_PROXY: "proxy",
  REQUESTS_CA_BUNDLE: "ca_existing_configmap | ca_bundle | ca_bundle_slot | ca_openshift_inject",
  RUN_HEALTH_WEB_SERVICE: null,
  SHIP_ID: null,
  TLS_CERT: "sv_tls_cert",
  TLS_KEY: "sv_tls_key",
};

/** A sample of /api/agent-env, one variable per control type, with real names
 *  and tags. Not a copy: nothing on the page has to agree with the catalogue.
 *  Every docker-only variable is now reserved, so there is no docker-only row. */
export const AGENT_ENV: AgentEnvVar[] = [
  { name: "PREFERRED_INTERFACE", type: "string",
    platforms: ["kubernetes", "docker"], functionalities: [],
    summary: "Network interface to read the machine's IP address from",
    default: "the first interface that is not docker0 or lo", example: "eth0" },
  { name: "VERIFY_SSL", type: "bool", platforms: ["kubernetes", "docker"],
    functionalities: [],
    summary: "Verify certificates on outbound HTTPS", default: "true",
    example: null },
  { name: "DODUO_PORT", type: "int", platforms: ["kubernetes", "docker"],
    functionalities: ["functionalGui"],
    summary: "Port the BlazeMeter Grid proxy listens on", default: "8000",
    example: null },
  { name: "KUBERNETES_LABELS", type: "json_object", platforms: ["kubernetes"],
    functionalities: [],
    summary: "Labels added to every object the agent creates",
    default: null, example: '{"team": "perf"}' },
  { name: "KUBERNETES_USE_APIPA", type: "bool", platforms: ["kubernetes"],
    functionalities: ["mockServices"],
    summary: "Publish endpoints on the node's IP address rather than 127.0.0.1",
    default: "true", example: null },
];


/** The marker rule as worked examples, one per key shape. placeholder.test.ts
 *  holds the page's `marker` to them and test_server.py holds markers.marker
 *  to the same entries. */
export const MARKER_EXAMPLES: Record<string, string> = {
  namespace: "<NAMESPACE>",
  auth_token: "<AUTH_TOKEN>",
  service_account_name: "<SERVICE_ACCOUNT_NAME>",
  ca_existing_configmap: "<CA_EXISTING_CONFIGMAP>",
  "proxy.http": "<PROXY_HTTP>",
  "proxy.https": "<PROXY_HTTPS>",
  "extra_env.FOO": "<EXTRA_ENV_FOO>",
};
