# bzm-opl-gen

Generates BlazeMeter private-location agent deployments for Kubernetes and
OpenShift from a customer's real account, sizes the cluster they need, and
verifies both against a live rig.

The vocabulary has two halves that must not be blurred: what BlazeMeter's
account holds, and what this tool produces. Where BlazeMeter has a word, that
word wins — a customer reading their own API docs beside this tool should not
have to translate.

## The account

**Private location**:
A customer-owned pool of agents that BlazeMeter dispatches tests to.
_Avoid_: harbor, OPL (except `harbor_id`, which is the account's own field name)

**Agent**:
One deployment inside a private location — a cluster, or a container.
_Avoid_: ship (except `ship_id`), server, node

**Functionality**:
One capability a private location is enabled for: Performance, GUI Functional,
Service Virtualization, and the ones this tool does not cover.
_Avoid_: feature, function, capability, module

**funcId**:
The account's identifier for a functionality — `performance`, `functionalGui`,
`mockServices`.

**Covered functionality**:
A functionality this tool configures. The rest are named on screen and
configured nowhere.

**Slot**:
How many engines one agent may run at once. A location's concurrency is agents
× slots.

**Location settings**:
The four values that live in BlazeMeter rather than in a bundle — slots,
threads per engine, and the two engine overrides. No regenerate or redeploy
applies them.

## The bundle

**Bundle**:
What a generate produces: the manifests, chart or run script for one agent,
plus the README and profile beside them.

**Option**:
One setting a bundle is generated from.

**Profile**:
A JSON file of options — either one shipped as a starting point, or the one a
bundle emits so it can be regenerated exactly. Never a sizing, and never
minikube's `-p`.
_Avoid_: preset, template, config file

**Format**:
Which shape a bundle takes: manifests, a Helm chart, or a docker run script.
A format may hide an option, and must then never refuse it.

**Placeholder**:
The marker a required field left blank resolves to, so the failure is loud and
early rather than a plausible-looking default.

**Reserved variable**:
An agent environment variable the generator writes itself, so it is refused
rather than merged when somebody sets it by hand.

**Crane**:
BlazeMeter's agent process — the thing a bundle deploys. Not ours.

**Engine**:
A pod crane starts to run one test.

## Sizing

**Sizing**:
A named, saved statement of what capacity is needed for one functionality —
virtual users for Performance, browser instances for GUI Functional, requests
per second for Service Virtualization. It is the question; the plan is the
answer.
_Avoid_: capacity profile, workload, preset, template

**Plan**:
What the planner computes from a sizing: engines, nodes, a machine size, and
the request document a platform team is handed.

**Preset**:
A named engine size — Small, Standard, Large or Custom. Only ever an engine
size.

**Assumed figure**:
A sizing input nothing here can measure, because it is a property of the
customer's script rather than of their cluster. Every surface says which
figures are assumed.

## The cluster

**Evidence**:
The document a customer's cluster is described by, collected by a script they
run themselves.

**Unread**:
A section of that document nobody was allowed to read. Never the same as a
section that was read and found empty — a denied read warns, an empty result
can fail.

**Preflight**:
Judging collected evidence against what a bundle will need, before anything is
applied.

**Rig**:
The live test that deploys a bundle to a real cluster and waits for the agent
to come online in a real account.
