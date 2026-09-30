# After installing: `smoke`

`smoke` checks an agent that you have already deployed. It reads the agent in
your cluster, the agent in BlazeMeter, and the configuration crane runs with.
Each check is reported as `PASS`, `WARN`, `UNREAD`, `SKIP` or `FAIL`, with its
evidence and, where something is wrong, the fix.

`smoke` does not create, deploy, change or delete anything. It does not create a
cluster, and it does not change a test. With `--run-test` it also starts one
test that you name. That is a real run in your BlazeMeter account.

```
bzm-opl-gen smoke --api-key api-key.json -n <namespace>
bzm-opl-gen smoke --api-key api-key.json -n <namespace> --harbor-id <harbor-id> --ship-id <ship-id>
bzm-opl-gen smoke --api-key api-key.json -n <namespace> --run-test <test-id>
bzm-opl-gen smoke --api-key api-key.json -n <namespace> --json
```

| flag | default | what it sets |
|---|---|---|
| `--api-key` | the environment, then the saved key | the BlazeMeter API key file |
| `-n`, `--namespace` | required | the namespace the agent was deployed to |
| `--harbor-id` | read from the deployed ConfigMap | the location id |
| `--ship-id` | read from the deployed ConfigMap | the agent id |
| `--run-test` | off | a test id. Starts that test and checks the engine crane creates for it |
| `--engine-timeout` | `420` | with `--run-test`: seconds to wait for the engine pod |
| `--timeout` | `900` | with `--run-test`: seconds to wait for the run to end before it is stopped |
| `--json` | off | the report as data, for a script or an MCP client |

`smoke` uses `oc` if it is installed, else `kubectl`, with the current context.
It needs `get` and `list` on `deployments`, `pods` and `configmaps` in the
namespace, and `get` on the agent's Secret. From the Secret it reads the key
names only, never the values. To count the certificates in the CA bundle inside
the crane pod it needs `create` on `pods/exec`. A read that the cluster refuses
is reported as `UNREAD`, never as a failure.

## The stages

### 1. The agent in the cluster

| check | passes when |
|---|---|
| `crane-deployment` | the crane Deployment has all its replicas ready |
| `crane-pod` | the crane pod is running and ready. A pod that restarted and recovered is a `WARN`, with the reason of the last exit |
| `configmap` | the ConfigMap crane reads is in the namespace |
| `credential` | the Secret crane reads holds `AUTH_TOKEN`. A token in the ConfigMap is a `WARN`: anyone who may read ConfigMaps can read it |
| `agent-ids` | the location and agent ids are known. They are read from the ConfigMap when you do not give them. Ids you give that do not match the deployed agent are a `FAIL`, and the report then checks the deployed agent |

### 2. The agent in BlazeMeter

| check | passes when |
|---|---|
| `agent` | the agent reports now: its state is `idle` or `running`, and its last heartbeat is less than 120 seconds old |
| `location` | the location has slots and threads per engine set. Without them, BlazeMeter refuses every test start with 403 `Not enough available resources` |

### 3. The configuration crane runs with

| check | passes when |
|---|---|
| `engine-sizing` | the ConfigMap sets `KUBERNETES_RESOURCES_LIMITS_CPU` and `_MEMORY`, and `KUBERNETES_RESOURCES_DEFAULT_CPU` and `_MEM` equal to them. Then every engine requests what it is limited to (QoS `Guaranteed`) |
| `engine-overrides` | the location's `overrideCPU` and `overrideMemory` are not set, or equal the limits. When set, they replace the engine requests |
| `ca-trust` | no CA bundle is configured, or the bundle is mounted in crane, holds certificates, and reaches the engines through `KUBERNETES_CA_BUNDLE_MOUNT` |
| `proxy` | no proxy is configured, or `NO_PROXY` names `kubernetes.default` and the proxy credentials are not in the ConfigMap. Credentials are never printed |
| `registry` | the images come from BlazeMeter's registry, or from your registry with `IMAGE_OVERRIDES` naming every image the location uses |

### 4. One real engine run (`--run-test`)

This stage starts the test you name. Before it starts the test, `smoke` prints
what it will do: the test, the location, and how long it waits. The run uses
the test's own load settings.

The test must already run on this location. `smoke` never repoints a test. A
test whose locations do not include this location is a `FAIL`, and the test is
not started. To point it here, open the test in BlazeMeter and choose the
private location under Load Distribution. For a Taurus script, name it under
`execution` as `locations: harbor-<harbor-id>: 1`. A Taurus script test keeps its
locations in the script, which the API does not show, so its target is `UNREAD`
and the run goes ahead.

No test starts when an earlier check failed or the agent is not reporting. The
stage then shows `SKIP`.

| check | passes when |
|---|---|
| `test-target` | the test runs on this location |
| `engine-pod` | crane creates an engine pod within `--engine-timeout` |
| `engine-size` | the engine's limits are the ones in the ConfigMap |
| `engine-qos` | the engine's QoS class is `Guaranteed`: its requests equal its limits |
| `engine-config` | the engine image comes from your registry (with a private registry), and the CA bundle and `HTTPS_PROXY` reach the engine where they are configured |
| `engine-heap` | the JVM heap is under the memory limit and more than half of it. Outside that range it is a `WARN` |
| `run-status` | the run ends as `ENDED` within `--timeout` |
| `run-samples` | the run produced samples, and not every sample failed |
| `engine-exit` | Taurus exited 0. An engine that stops partway can still end as `ENDED` |

A run that has not ended within `--timeout`, or whose engine never appeared, is
stopped. A run proves the engine's egress only if the test's samplers make real
requests.

### 5. Triage on a failure

When any check fails, `smoke` runs [`triage`](triage.md) on the namespace and
adds its findings to the report. They name the cause that events, pod statuses
and crane's log show.

## Statuses and exit status

| status | meaning |
|---|---|
| `PASS` | checked and correct |
| `WARN` | works, and can cause a problem later |
| `UNREAD` | the read was refused or not answered. This is not a failure, and a report with `UNREAD` checks can miss one |
| `SKIP` | not checked, because what it depends on failed or is absent |
| `FAIL` | the agent cannot run tests as it is |

| exit | when |
|---|---|
| `0` | no `FAIL`. `WARN`, `UNREAD` and `SKIP` still exit 0, as in `doctor` and `triage` |
| `1` | at least one `FAIL`, or a refused command line (for example a bad timeout) |

A report in which nothing could be read says so in its last line.

## From an MCP client

`opl_agent smoke` with `{namespace, harbor_id?, ship_id?}` returns the same
document as `--json`, plus `next`. It runs stages 1 to 3 and reads only.
`run_test` starts a test, and is refused unless the server runs with
`BZM_OPL_ENABLE_LIVETEST=1`. See [mcp.md](mcp.md).
