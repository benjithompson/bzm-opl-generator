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
import { PreflightOut, Suggestion } from "./api";

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

/** One implication of an imported file, as suggest.py serves it: the cluster
 *  says this is plain Kubernetes and the configuration says OpenShift.
 *
 *  `platform` rather than an SV option deliberately -- it belongs to no feature,
 *  so no location's funcIds can clear it on the way past (notRunPatch), which is
 *  what a page-level test of applying and undoing would otherwise be fighting
 *  rather than testing. One click to apply, and a previous value worth putting
 *  back, which is what an undo is about. */
export const PLATFORM_SUGGESTION: Suggestion = {
  option: "platform", strength: "DECISIVE", value: "k8s",
  candidates: ["k8s"], ruled_out: [],
  evidence: ["api_groups.openshift_security"],
  detail: "security.openshift.io is not served, so this is plain Kubernetes",
  state: "CONFLICT", current: "openshift",
  current_shown: "openshift", value_shown: "k8s", candidates_shown: ["k8s"],
  ruled_out_shown: [], blocked: null,
};

/** A served preflight answer: one file judged against one configuration.
 *
 *  Here rather than in each test that wants one, for the reason at the top of
 *  this file: three files now need this shape -- the panel that renders it, the
 *  snapshot that stores it and the page that restores it -- and three builders
 *  with three sets of defaults for one schema is the divergence
 *  tests/evidence_fixtures.py was written to end. Every judgement in it is
 *  doctor's and suggest.py's, and is asserted against the command in
 *  tests/test_doctor.py and tests/test_suggest.py; what a test overrides here is
 *  what it needs the *page* to be told. */
export function preflightOut(over: Partial<PreflightOut> = {}): PreflightOut {
  return {
    namespace: "blazemeter",
    summary: "3 passed, 1 warning, no failures",
    evidence: { collected_at: "2026-07-28T02:51:50Z", namespace: "some-ns",
                elsewhere: false, unreadable: [] },
    checks: [{ name: "location slots", status: "PASS",
               detail: "2 concurrent engine(s)" }],
    suggestions: [], why_nothing: null,
    ...over,
  };
}
