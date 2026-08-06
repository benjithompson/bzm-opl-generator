// Fixtures shared by more than one test file, declared once each.
//
// The rule is tests/evidence_fixtures.py's, one layer up: there were two
// builders for the evidence document with different defaults for the same
// schema, and one test file imported both. The same thing had started here --
// formats.test.ts and App.test.tsx each carried their own slice of
// DOCKER_IGNORED, and they had already diverged by five keys, so the page test
// was asserting against a table the unit test would have called incomplete.
//
// Not in fakeApi.ts: that file deliberately holds no payloads (an invented
// answer lets a test pass while proving nothing). This is a payload, and it is
// only ever handed to a route a test chose to stub.
import { AgentEnvVar, SizingModel, SlotMinimum } from "./api";

/** core.SLOT_MINIMUMS as the page receives it from /api/slot-minimums — the
 *  slots a functionality needs before BlazeMeter will create the location.
 *
 *  A copy, and the only one, held equal to core's by
 *  `tests/test_server.py::test_the_pages_copy_of_the_slot_minimums_is_cores`.
 *  `message` is transcribed rather than paraphrased on purpose: it is
 *  BlazeMeter's own sentence, which is what a customer meeting this rule in
 *  BlazeMeter's UI reads. */
export const SLOT_MINIMUMS: Record<string, SlotMinimum> = {
  functionalGui: {
    label: "GUI Functional",
    minimum: 2,
    message: "The option Parallel engine runs must be greater than 1 for a "
      + "Private Location with the GUI Functional Functionality enabled.",
  },
};

/** plan.SIZING_MODELS as the page receives it from /api/sizing-models.
 *
 *  A copy, and the only one, held equal to the planner's table by
 *  `tests/test_server.py::test_the_pages_copy_of_the_sizing_models_is_the_planner_s`.
 *  It cannot be derived: the authority is Python and the page's tests run
 *  without a server.
 *
 *  `measured: false` is the one the card branches on, and the reason this is a
 *  copy rather than a sample: a model with no measured figure offers no
 *  per-pod box, and a fixture that quietly gave service virtualization one
 *  would let a test pass over the exact case the card exists to get right. */
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

/** generate.DOCKER_IGNORED, as the page receives it from /api/docker-ignored.
 *
 *  A copy, and the only one. It cannot be derived -- the authority is Python --
 *  so `tests/test_server.py::test_docker_ignored_is_served_from_the_generator`
 *  holds the route equal to the generator's table, and what is checked here is
 *  the half no Python test can see: what a page does with such a table. */
export const DOCKER_IGNORED: Record<string, string> = {
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
  crane_hook: "crane-hook is a Pod, and there is no cluster to run it in",
  registry_auth: "the stubs are ConfigMap lines; a docker host authenticates "
    + "with its own docker login",
};


/** generate.RESERVED_ENV with the option that owns each name, as the page
 *  receives it from /api/reserved-env -- the environment variables a bundle
 *  writes for itself, which `extra_env` refuses.
 *
 *  A copy, and the only one, held equal to the generator's by
 *  `tests/test_server.py::test_the_pages_copy_of_the_reserved_env_names_is_the_generators`.
 *  `null` is a name no single option owns. */
export const RESERVED_ENV: Record<string, string | null> = {
  AUTH_TOKEN: "auth_token",
  AUTO_KUBERNETES_UPDATE: "auto_update",
  AUTO_UPDATE: "auto_update",
  AWS_CA_BUNDLE: "ca_bundle | ca_existing_configmap",
  CONTAINER_MANAGER_TYPE: null,
  DOCKER_PORT_RANGE: null,
  DOCKER_REGISTRY: "private_registry",
  DOCKER_REGISTRY_EMAIL: "registry_auth",
  DOCKER_REGISTRY_PASSWORD: "registry_auth",
  DOCKER_REGISTRY_USERNAME: "registry_auth",
  HARBOR_ID: null,
  HTTPS_PROXY: "proxy",
  HTTP_PROXY: "proxy",
  IMAGE_OVERRIDES: "private_registry",
  INHERIT_RUNNING_USER_AND_GROUP: "restrict_engines",
  KUBERNETES_CA_BUNDLE_MOUNT: "ca_bundle | ca_existing_configmap",
  KUBERNETES_ISTIO_GATEWAY_NAME: "sv_istio_gateway",
  KUBERNETES_LIMITS_EPHEMERAL_STORAGE: "engine_ephemeral_limit_mb",
  KUBERNETES_NODE_SELECTOR_JSON: "engine_node_selector",
  KUBERNETES_REQUESTS_EPHEMERAL_STORAGE: "engine_ephemeral_request_mb",
  KUBERNETES_RESOURCES_LIMITS_CPU: "engine_cpu_limit",
  KUBERNETES_RESOURCES_LIMITS_MEMORY: "engine_mem_limit",
  KUBERNETES_SECURITY_CONTEXT_CAP_JSON: "restrict_engines",
  KUBERNETES_SERVICE_USE_TYPE: "service_type",
  KUBERNETES_TOLERATIONS_JSON: "engine_tolerations",
  KUBERNETES_WEB_EXPOSE_SUB_DOMAIN: "sv_subdomain",
  KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME: "sv_tls_secret",
  KUBERNETES_WEB_EXPOSE_TYPE: "sv_ingress",
  NO_PROXY: "proxy",
  REQUESTS_CA_BUNDLE: "ca_bundle | ca_existing_configmap",
  RUN_HEALTH_WEB_SERVICE: null,
  SHIP_ID: null,
};

/** A few of the variables /api/agent-env offers, one per control the area can
 *  render.
 *
 *  A **sample**, and deliberately not a copy: unlike RESERVED_ENV and
 *  DOCKER_IGNORED above, nothing on the page has to agree with this list. The
 *  area renders what it is served and offers a name box for whatever is not in
 *  it, so a table here held equal to the catalogue would be forty records kept
 *  in step to prove something no rendering depends on. What the tests need is
 *  one variable of each type, which is what this is.
 *
 *  The names are real ones, so a record that stopped being offered -- an option
 *  added to the generator claims it into RESERVED_ENV -- shows up as a test
 *  about a variable the server would no longer serve. Their `functionalities`
 *  are the real tags too, for the same reason and no stronger one: a test that
 *  serves this list scoped, as the server would, wants a row that really does
 *  drop out of a performance location's answer. */
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
  { name: "HOSTNAME_OVERRIDE", type: "string", platforms: ["docker"],
    functionalities: ["mockServices"],
    summary: "Hostname for transactional virtual services on this agent",
    default: null, example: null },
];
