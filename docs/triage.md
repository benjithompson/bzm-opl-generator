# After deploying: `triage`

`triage` reads the namespace an agent was deployed to and names each known
failure in it, with the option or action that fixes it. Run it when BlazeMeter
shows the agent offline, or a test stays at `BOOT_STARTING`.

Most failures after an apply do not show in BlazeMeter. They show in Kubernetes
events, in pod and container statuses, and in crane's log. `triage` reads those
three sources and matches each line against a table of known failures. It
changes nothing in the cluster.

```
bzm-opl-gen triage -n <namespace>
bzm-opl-gen triage -n <namespace> --since 2h --crane-log-lines 2000
bzm-opl-gen triage -n <namespace> --json
```

| flag | default | what it sets |
|---|---|---|
| `-n`, `--namespace` | required | the namespace the agent was deployed to |
| `--since` | `1h` | how far back to read events and crane's log (`30m`, `1h`, `1h30m`, `2d`) |
| `--crane-log-lines` | `500` | the maximum number of lines read from each crane log |
| `--json` | off | the report as data, for a script or an MCP client |

`triage` uses `oc` if it is installed, else `kubectl`, with the current context.
It needs `get` and `list` on `events` and `pods`, and `get` on `pods/log`, in
the namespace. It also reads the Namespace object to tell a missing namespace
from an empty one; many namespaced roles cannot, and that is not an error.
When an event says a pod waited for a ServiceAccount, it reads that
ServiceAccount (`get` on `serviceaccounts`).

## What it reads

1. **Events in the namespace**, within `--since`. Events tell why a pod was not
   created, not scheduled or not started. Most clusters keep events for about
   one hour, so run `triage` soon after the failure.
2. **Pods**: crane, the engines crane starts (`r-...`), crane's housekeeping
   `test-job-...` pods, and anything else in the namespace. For each pod it
   reads the scheduling condition, the eviction reason, and each container's
   waiting reason and last termination (for example `OOMKilled`).
3. **Crane's log**, within `--since`. If crane restarted, it also reads the log
   of the previous run, whatever its age, because the crash is in that log.
   Crane creates engine pods itself, so a quota, Pod Security or webhook
   refusal of an engine shows in crane's log, not in an event. A crane
   container that has not started has no log yet; the report says so under
   `read`, and that is not an unread section.

Crane 3.8 writes its AUTH_TOKEN to its log when it starts. `triage` replaces
every AUTH_TOKEN value, and every user name and password in a URL, with
`<redacted>` as it reads the log, so no report and no `--json` document
carries them.

Engine pods are deleted when a run ends. To see an engine failure, run `triage`
while the test is still starting.

## Reading the report

Each finding shows:

- the status and rule id, for example `FAIL  image-not-found`;
- `about:` the image, taint, quota, policy or resource concerned, where the
  message names one;
- `seen on:` the objects that showed it, and how many times;
- `evidence:` the first line that matched, with a long middle elided;
- `also:` container states on the same pod that the finding explains, for
  example the exit code of a crash-looping container;
- the finding, and `fix:` what to change.

Failures come first, then warnings, then notes. A `NOTE` needs no action now.
Events outlive their pods by about one hour, so a finding seen only on pods that
no longer exist is history: it shows as `NOTE` with `(pod gone; history)`, comes
after the current findings, and does not count as a failure. A finding about a
ReplicaSet or Deployment is never history. When the pods could not be read,
nothing is marked as history.

Two lines are not listed, because a finding already explains them or they are
harmless: a bare `Traceback (most recent call last):` header in a log where a
rule matched the error, and a readiness probe that raced its container's exit
on a pod that has finished or is gone.

After the findings the report lists:

- **unread**: a read the cluster refused or did not answer, with its reason. An
  unread section is not an empty section. A report with unread sections can
  miss a failure.
- **unrecognised warnings**: Warning events, container states and crane log
  errors that no rule knows. They are listed as found, grouped by message, with
  a count. They are never dropped.

### Exit status

| exit | when |
|---|---|
| `0` | no known failure. Unread sections, unrecognised warnings and notes, history included, still exit 0, as in `doctor`. |
| `1` | at least one known failure (`FAIL`) |

A report with nothing read says so in its last line.

## The known failures

| rule | what it means | what fixes it |
|---|---|---|
| `namespace-missing` | the namespace does not exist on this cluster | the right namespace and context; apply the bundle |
| `crane-missing` | no crane pod in the namespace | apply the bundle; the other findings say why the Deployment made no pod |
| `image-pull-registry-tls` | the node does not trust the registry's certificate | node runtime trust (containerd registry hosts, OpenShift image configuration). The CA options do not reach image pulls. |
| `image-pull-auth` | the registry refused the pull | `pull_secret` for the crane image; the same Secret on the `default` ServiceAccount for engines |
| `image-not-found` | the registry has no such image or tag | `bzm-opl-gen images --mirror`; tags follow BlazeMeter releases, so a mirror goes stale |
| `image-pull-unreachable` | the node cannot reach the registry | node egress, or a mirror and `private_registry` |
| `image-pull` | a pull failed for another reason | the evidence line; `private_registry`, `pull_secret`, `registry_auth` |
| `missing-reference` | a pod names a ConfigMap or Secret that is not there | create it, or correct `ca_existing_configmap`, `ca_cert_file`, `pull_secret`, `sv_tls_secret` |
| `tls-trust` | crane does not trust a certificate on its way to BlazeMeter | one CA option: `ca_bundle`, `ca_bundle_slot` with `ca_cert_file`, `ca_existing_configmap`, `ca_openshift_inject` |
| `proxy-kube-api` | crane sent Kubernetes API calls to the proxy | `proxy.no_proxy` must cover the API server |
| `proxy-auth` | the proxy answered 407 | `proxy.username` and `proxy.password` |
| `proxy-unreachable` | crane cannot connect through the proxy | `proxy.http` and `proxy.https` |
| `egress-blocked` | crane cannot reach BlazeMeter | egress to `a.blazemeter.com`, `data.blazemeter.com`, `storage.blazemeter.com`; `proxy`; `doctor` probes it |
| `crane-hung-proxy` | crane's log ends at its first call to BlazeMeter after a minute of running, and names a proxy. The pod still shows Ready. | test from a curl pod in the namespace through the proxy; `proxy.http`, `proxy.https`, `proxy.no_proxy` |
| `crane-hung` | the same with no proxy: a firewall, NetworkPolicy or DNS drops the connection | test from a curl pod in the namespace; egress to `a.blazemeter.com`, or `proxy` |
| `auth-token` | BlazeMeter refused the AUTH_TOKEN, or the agent is gone | apply the current token; issue a new one only if nothing else runs on the agent |
| `disk-pressure` | a node is short of disk | about 60GB per concurrent engine; `engines_per_node`, `engine_ephemeral_request_mb` |
| `schedule-taint` | no node tolerated the pod | `tolerations`, `engine_tolerations`, with a matching node selector |
| `schedule-resources` | no node has room | engines request 2 CPU / 8Gi by default: `engine_cpu_limit`, `engine_mem_limit`, larger nodes, fewer slots |
| `schedule-selector` | no node matches the selector | `node_selector`, `engine_node_selector` |
| `quota` | a ResourceQuota refused a pod | quota of at least slots × engine size plus crane; `engine_cpu_limit`, `engine_mem_limit` |
| `pod-security` | Pod Security admission refused a pod | `restrict_engines` (on by default), a non-root `run_as_user` |
| `openshift-scc` | no SCC admitted the pod | `platform: openshift`, `restrict_engines`; an SCC for `service_account_name` |
| `admission-webhook` | Kyverno, Gatekeeper or similar refused a pod | the policy the evidence names: `restrict_engines`, `run_as_user`, `private_registry`, engine size, or an exception |
| `service-account-missing` | the ReplicaSet cannot create crane's pod: its ServiceAccount does not exist | apply the bundle's ServiceAccount; with `service_account_create` off, your platform team creates the one `service_account_name` names |
| `service-account-unread` | a pod waited for its ServiceAccount, and whether it exists now could not be read (warning) | `kubectl get serviceaccount` in the namespace |
| `service-account-late` | a pod waited for its ServiceAccount, which exists now: the files were applied Deployment first (note) | nothing |
| `engine-prestop` | an engine's preStop hook failed as the pod ended; seen on engines whose runs returned all their results (note) | nothing on its own |
| `rbac` | the API refused crane's service account | the bundle's Role and RoleBinding, `service_account_name`, `cluster_rbac` for nodes |
| `oom-engine` | an engine ran out of memory | `engine_mem_limit`, or a lower `threadsPerEngine` on the location |
| `oom` | another container ran out of memory | for crane, its 2Gi limit in the Deployment; tell BlazeMeter support |
| `evicted` | a pod was evicted | `engine_ephemeral_request_mb`, `engine_ephemeral_limit_mb`, `crane_ephemeral_storage`, node disk; `engine_mem_limit` for memory |
| `crash-loop` | a container keeps restarting (warning) | the crane log findings, or `kubectl logs --previous` |

[options.md](options.md) describes each option. After a change, regenerate the
bundle, apply it, and run `triage` again.

## Limits

- The crane log rules match the error text of the HTTP and TLS libraries crane
  uses and of the Kubernetes API. A crane release that words an error
  differently shows it under unrecognised warnings.
- A docker bundle has no namespace. Read its log with
  `docker logs bzm-crane-<ship-id>`.
- An autoscaling cluster can show `schedule-resources` while it adds a node.
  Run `triage` again after a few minutes.

## From an MCP client

`opl_agent triage` with `{namespace, since?, crane_log_lines?}` returns the same
document as `--json`, plus `next`. It reads with the kubectl or oc context of
the machine that runs the MCP server, and writes nothing.
