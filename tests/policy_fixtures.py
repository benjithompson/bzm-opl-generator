"""Admission policy objects as the API server returns them, for doctor tests.

Shaped on the Kyverno policy library, the Gatekeeper library and the
Kubernetes ValidatingAdmissionPolicy examples, trimmed to what a check reads
plus the status and annotations a real object carries."""

import copy

from bzm_opl_gen import evidence

POD = {"any": [{"resources": {"kinds": ["Pod"]}}]}
SYSTEM = {"any": [{"resources": {"namespaces": ["kube-system", "kyverno"]}}]}


def _kyverno(name, rules, action="Enforce", kind="ClusterPolicy",
             namespace=None, description=None):
    meta = {"name": name, "annotations": {
        "policies.kyverno.io/title": name.replace("-", " ").title(),
        "policies.kyverno.io/category": "Best Practices"}}
    if description:
        meta["annotations"]["policies.kyverno.io/description"] = description
    if namespace:
        meta["namespace"] = namespace
    return {"apiVersion": "kyverno.io/v1", "kind": kind, "metadata": meta,
            "spec": {"validationFailureAction": action, "background": True,
                     "rules": rules},
            "status": {"ready": True}}


def disallow_latest_tag(action="Enforce"):
    return _kyverno("disallow-latest-tag", [
        {"name": "require-image-tag", "match": POD,
         "validate": {"message": "An image tag is required.",
                      "pattern": {"spec": {"containers": [{"image": "*:*"}]}}}},
        {"name": "validate-image-tag", "match": POD,
         "validate": {"message": "Using a mutable image tag e.g. 'latest' is "
                                 "not allowed.",
                      "pattern": {"spec": {"containers": [
                          {"image": "!*:latest"}]}}}},
    ], action)


def restrict_image_registries(registries="eu.foo.io/* | bar.io/*",
                              action="Enforce"):
    image = {"image": registries}
    return _kyverno("restrict-image-registries", [
        {"name": "validate-registries", "match": POD,
         "validate": {"message": "Unknown image registry.",
                      "pattern": {"spec": {"=(ephemeralContainers)": [image],
                                           "=(initContainers)": [image],
                                           "containers": [image]}}}},
    ], action)


def require_requests_limits(action="Enforce"):
    return _kyverno("require-requests-limits", [
        {"name": "validate-resources", "match": POD,
         "validate": {"message": "CPU and memory resource requests and "
                                 "memory limits are required for containers.",
                      "pattern": {"spec": {"containers": [{"resources": {
                          "requests": {"memory": "?*", "cpu": "?*"},
                          "limits": {"memory": "?*"}}}]}}}},
    ], action)


def require_run_as_nonroot(action="Enforce"):
    optional = {"=(securityContext)": {"=(runAsNonRoot)": True}}
    required = {"securityContext": {"runAsNonRoot": True}}
    return _kyverno("require-run-as-nonroot", [
        {"name": "run-as-non-root", "match": POD,
         "validate": {"message": "Running as root is not allowed.",
                      "anyPattern": [
                          {"spec": {"securityContext": {"runAsNonRoot": True},
                                    "=(ephemeralContainers)": [optional],
                                    "=(initContainers)": [optional],
                                    "containers": [optional]}},
                          {"spec": {"=(ephemeralContainers)": [required],
                                    "=(initContainers)": [required],
                                    "containers": [required]}}]}},
    ], action)


def disallow_privilege_escalation(action="Enforce"):
    return _kyverno("disallow-privilege-escalation", [
        {"name": "privilege-escalation", "match": POD,
         "validate": {"message": "Privilege escalation is disallowed.",
                      "pattern": {"spec": {"containers": [{"securityContext": {
                          "allowPrivilegeEscalation": "false"}}]}}}},
    ], action)


def require_ro_rootfs(action="Enforce"):
    return _kyverno("require-ro-rootfs", [
        {"name": "validate-readOnlyRootFilesystem", "match": POD,
         "validate": {"message": "Root filesystem must be read-only.",
                      "pattern": {"spec": {"containers": [{"securityContext": {
                          "readOnlyRootFilesystem": True}}]}}}},
    ], action)


def require_labels(action="Enforce"):
    return _kyverno("require-labels", [
        {"name": "check-for-labels", "match": POD,
         "validate": {"message": "The label app.kubernetes.io/name is "
                                 "required.",
                      "pattern": {"metadata": {"labels": {
                          "app.kubernetes.io/name": "?*"}}}}},
    ], action)


def disallow_host_path(action="Enforce"):
    return _kyverno("disallow-host-path", [
        {"name": "host-path", "match": POD,
         "validate": {"message": "HostPath volumes are forbidden.",
                      "pattern": {"spec": {"=(volumes)": [
                          {"X(hostPath)": "null"}]}}}},
    ], action)


def pod_security(level, action="Enforce"):
    return _kyverno(f"podsecurity-subrule-{level}", [
        {"name": level, "match": POD,
         "validate": {"podSecurity": {"level": level, "version": "latest"}}},
    ], action)


def unrecognised(action="Enforce"):
    return _kyverno("require-pod-probes", [
        {"name": "validate-probes", "match": POD,
         "validate": {"message": "Probes are required.",
                      "foreach": [{"list": "request.object.spec.containers",
                                   "deny": {"conditions": {"all": [
                                       {"key": "{{ element.livenessProbe }}",
                                        "operator": "Equals",
                                        "value": None}]}}}]}},
    ], action, description="Liveness probes need to be configured.")


def excluding(policy, namespaces):
    """A copy whose every rule excludes these namespaces."""
    policy = copy.deepcopy(policy)
    for rule in policy["spec"]["rules"]:
        rule["exclude"] = {"any": [{"resources": {"namespaces": namespaces}}]}
    return policy


def selecting(policy, selector):
    """A copy whose every rule matches pods in namespaces with these labels."""
    policy = copy.deepcopy(policy)
    for rule in policy["spec"]["rules"]:
        rule["match"] = {"any": [{"resources": {
            "kinds": ["Pod"], "namespaceSelector": selector}}]}
    return policy


# -- Gatekeeper ---------------------------------------------------------------

def template(kind, description):
    return {"apiVersion": "templates.gatekeeper.sh/v1",
            "kind": "ConstraintTemplate",
            "metadata": {"name": kind.lower(),
                         "annotations": {"description": description}},
            "spec": {"crd": {"spec": {"names": {"kind": kind}}},
                     "targets": [{"target": "admission.k8s.gatekeeper.sh",
                                  "rego": "package x\nviolation[{}] { false }"}]},
            "status": {"created": True}}


def constraint(kind, name, parameters=None, action=None, excluded=None,
               kinds=("Pod",)):
    spec = {"match": {"kinds": [{"apiGroups": [""], "kinds": list(kinds)}]}}
    if excluded:
        spec["match"]["excludedNamespaces"] = excluded
    if parameters is not None:
        spec["parameters"] = parameters
    if action:
        spec["enforcementAction"] = action
    return {"apiVersion": "constraints.gatekeeper.sh/v1beta1", "kind": kind,
            "metadata": {"name": name}, "spec": spec,
            "status": {"totalViolations": 0}}


def allowed_repos(repos=("registry.corp.example/",), **kw):
    return constraint("K8sAllowedRepos", "repo-is-corp",
                      {"repos": list(repos)}, **kw)


def required_resources(**kw):
    return constraint("K8sRequiredResources", "container-must-have-limits",
                      {"limits": ["cpu", "memory"],
                       "requests": ["cpu", "memory"]}, **kw)


def container_limits(cpu="1", memory="4Gi", **kw):
    return constraint("K8sContainerLimits", "container-must-have-limits-max",
                      {"cpu": cpu, "memory": memory}, **kw)


def privileged_container(**kw):
    return constraint("K8sPSPPrivilegedContainer", "psp-privileged-container",
                      **kw)


TEMPLATES = [template("K8sAllowedRepos", "Requires container images to begin "
                                         "with a string from the specified "
                                         "list."),
             template("K8sRequiredResources", "Requires containers to have "
                                              "defined resources set."),
             template("K8sPSPPrivilegedContainer", "Controls the ability of "
                                                   "any container to enable "
                                                   "privileged mode."),
             template("K8sContainerLimits", "Requires containers to have "
                                            "memory and CPU limits set.")]


# -- ValidatingAdmissionPolicy --------------------------------------------------

def vap(name, expression, resources=("pods",), selector=None):
    spec = {"failurePolicy": "Fail",
            "matchConstraints": {"resourceRules": [
                {"apiGroups": [""], "apiVersions": ["v1"],
                 "operations": ["CREATE", "UPDATE"],
                 "resources": list(resources)}]},
            "validations": [{"expression": expression}]}
    if selector:
        spec["matchConstraints"]["namespaceSelector"] = selector
    return {"apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicy", "metadata": {"name": name},
            "spec": spec}


def binding(policy, actions=("Deny",), selector=None):
    spec = {"policyName": policy, "validationActions": list(actions)}
    if selector:
        spec["matchResources"] = {"namespaceSelector": selector}
    return {"apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingAdmissionPolicyBinding",
            "metadata": {"name": f"{policy}-binding"}, "spec": spec}


NO_LATEST_CEL = ("object.spec.containers.all(c, !c.image.endsWith(':latest'))")
CORP_ONLY_CEL = ("object.spec.containers.all(c, "
                 "c.image.startsWith('registry.corp.example/'))")


# -- webhooks -------------------------------------------------------------------

def webhook(name, resources=("pods",), selector=None):
    hook = {"name": f"{name}.example.com", "admissionReviewVersions": ["v1"],
            "sideEffects": "None", "failurePolicy": "Fail",
            "rules": [{"apiGroups": [""], "apiVersions": ["v1"],
                       "operations": ["CREATE"],
                       "resources": list(resources)}]}
    if selector:
        hook["namespaceSelector"] = selector
    return {"apiVersion": "admissionregistration.k8s.io/v1",
            "kind": "ValidatingWebhookConfiguration",
            "metadata": {"name": name}, "webhooks": [hook]}


# -- cluster sections -----------------------------------------------------------

def sections(kyverno=None, policies=None, templates=None, constraints=None,
             admission=None, bindings=None, webhooks=None):
    """Policy sections for a cluster: NOT_SERVED where an engine is absent
    (Kyverno and Gatekeeper by default), [] for the built-in kinds."""
    ns = evidence.NOT_SERVED
    return {"kyverno_clusterpolicies": ns if kyverno is None else kyverno,
            "kyverno_policies": (ns if kyverno is None and policies is None
                                 else policies or []),
            "gatekeeper_templates": ns if templates is None else templates,
            "gatekeeper_constraints": (ns if constraints is None
                                       else constraints),
            "validating_admission_policies": admission or [],
            "validating_admission_policy_bindings": bindings or [],
            "validating_webhooks": webhooks or []}
