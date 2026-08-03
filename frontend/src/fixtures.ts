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

/** generate.DOCKER_IGNORED, as the page receives it from /api/docker-ignored.
 *
 *  A copy, and the only one. It cannot be derived -- the authority is Python --
 *  so `tests/test_server.py::test_docker_ignored_is_served_from_the_generator`
 *  holds the route equal to the generator's table, and what is checked here is
 *  the half no Python test can see: what a page does with such a table. */
export const DOCKER_IGNORED: Record<string, string> = {
  platform: "there is no OpenShift/Kubernetes distinction on a docker host",
  namespace: "containers are not namespaced",
  service_account_name: "there is no ServiceAccount to run as",
  service_account_create: "there is no ServiceAccount to create",
  cluster_rbac: "there is no RBAC",
  service_type: "KUBERNETES_SERVICE_USE_TYPE is a Kubernetes variable",
  pull_secret: "the host's own docker login is what authenticates a pull",
  run_as_user: "the container runs as its image says; see the docs on "
    + "INHERIT_RUNNING_USER_AND_GROUP",
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
