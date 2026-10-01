# Crane internals

What BlazeMeter's agent image (crane) does, read from its own code. Use this
file when a question about crane's behaviour decides what the generator emits:
an environment variable, a Role rule, a pod field, a probe, a timeout.
BlazeMeter's public documentation is incomplete, and in places it is wrong.

## Evidence and scope

| Item | Value |
|---|---|
| Image | `gcr.io/verdant-bulwark-278/blazemeter/crane:3.8.0` |
| Build inside the image | `/etc/issue.ver` says 3.8.1; the agent reports `crane 3.8.1` in its heartbeat |
| Config digest | `sha256:89c786b3b69e185f3b42f2ba6e7f4ffbc4fa4c91799efdf5452dee13de2b97db` |
| Program | A PyInstaller one-file binary, `/usr/local/bin/crane-agent`, Python 3.12. Packages: `agent/`, `crane_updater/`, `bzm_common_library` (not extracted) |
| Read on | 2026-09-30 |

Method: fetch the image layers with the registry v2 API (anonymous pull, no
`docker pull`); take `crane-agent` from the top layers; extract the PyInstaller
archive and its PYZ; disassemble each module with `dis` under Python 3.12. A
reference `module:line` below is a source line number that the bytecode
carries.

Each fact carries a mark:

- **M** (measured): read directly in the bytecode or the image config.
- **I** (inferred): reasoned from the code, from library behaviour or from
  arithmetic. It was not observed.
- **L** (live): observed on a live cluster in this project (see `LIVE_RIG.md`).

A new crane release can change any of this. Before you rely on a fact for a
release other than 3.8.x, read the code of that release again.

Module short names: `CH` agent.command_handler, `CFG` agent.config, `BC`
agent.blazemeter_client, `KM` …kubernetes.kubernetes_manager, `KC`
…kubernetes.kubernetes_client, `SP` …kubernetes.kubernetes_service_pool,
`IDF` …kubernetes.image_data_fetcher, `CLK` …cleaner.k8s, `CL` …cleaner,
`UH` …helpers.user_helper, `DM` …docker_manager, `KU`
crane_updater.updaters.k8s_updater, `DU` crane_updater.updaters.docker_updater,
`base`, `nginx`, `istio`, `contour` the matching
…kubernetes.kubernetes_*_web_expose_service modules.

## Where crane contradicts this project

These are open. Each one needs a change in the generator, its docs or
CLAUDE.md. Remove a row when the fix is merged.

| # | Crane does | This project says or does | Mark | Evidence |
|---|---|---|---|---|
| 1 | Puts `AUTH_TOKEN=<value>` in plain text into the env of every engine Deployment and Job. Only five API keys go to a Secret. | `use_secret` is presented as keeping the token out of plain view; `SECURITY-REVIEW.md` does not name the engine pod spec. | M | CH:310; KM:138, 1270-1282 |
| 2 | Sets no `seccompProfile` on any pod. | `admission_policy` models engines as meeting the seccomp rule; `docs/hardened-engines.md` shows `RuntimeDefault` (GKE and OpenShift inject it). On a plain cluster `restricted` refuses engines for seccomp too. | M (absence); I (PSA result) | no `seccomp` string in any module; KM:647, 659 |
| 3 | Reads `KUBERNETES_INGRESS_CLASS` (default `nginx`). | `doctor.CRANE_INGRESS_CLASS`, `suggest`, `docs/preflight.md` and `docs/crane-nginx-ingress-port.md` say the class is hardcoded. | M | CFG:464; nginx expose:51 |
| 4 | Treats `KUBERNETES_WEB_EXPOSE_TYPE=INGRESS` as `NGINX` (exact, case-sensitive). Also accepts `OPENSHIFT`. | `service_virt.py` and the docs say `INGRESS` creates nothing. | M | KM:125-129, 282-284 |
| 5 | Requires the TLS secret name only for nginx and contour. Istio reads the secret for TCP and TCP_TLS (`SIMPLE`, `credentialName`). | `sv_cfg` requires the secret for every backend; istio is recorded as never reading it. | M | nginx:19-20; contour:19-20; istio:42-44, 72-80 |
| 6 | Forwards `VERIFY_SSL`, `VIRTUAL_SERVICE_LOGS` and `VIRTUAL_SERVICE_ADDITIONAL_JARS` from its own env to every container it starts. | Invariant 6: `extra_env` reaches crane only. | M | CH:326-334 |
| 7 | Writes the ephemeral-storage request and limit only when `KUBERNETES_LIMITS_EPHEMERAL_STORAGE` is set. | `engine_ephemeral_request_mb` is documented as working alone. | M | KM:758-760 |
| 8 | Uses `HOSTNAME_OVERRIDE` on Kubernetes too (mock env, Service access info). | `agent_env.py` marks it docker-only; "disjoint variables per platform". | M | CH:335, 346-352; SP:316 |
| 9 | On docker, logs in to the registry itself with `DOCKER_REGISTRY_USERNAME/_PASSWORD/_EMAIL` when it pulls (auto-update only). A failed login exits with code 0. | `registry_auth` is documented as useless on docker because the host's `docker login` authenticates. | M | DM:110-127; DU:223-230 |
| 10 | RBAC use differs from our Role. | See [RBAC](#rbac). | M | — |
| 11 | About 80 variables are read that `agent_env.py` lacks; several rows there are wrong. | `agent_env.py` is BlazeMeter's documented reference. | M | [Configuration](#configuration) |

## Process model

```
crane-agent (PID 1, image USER 1337, HEALTHCHECK_PORT=5000)
 └ agent.agent.main
    ├ Windows → CraneAgent directly (no updater)
    └ Linux   → UpdaterAgent  (the updater, parent)
         picks KubernetesUpdater | DockerUpdater | BasicUpdater (no-op)
         update_crane_only()      self-update before the agent starts
         multiprocessing.Process → CraneAgent  (the agent, child)
         loop while the child lives: hourly update check
         sys.exit(child exit code)
```

- The updater never restarts the agent. Kubernetes or docker restarts the
  container. M (updaters.updater:99-114)
- `KubernetesUpdater` runs only with `CONTAINER_MANAGER_TYPE=KUBERNETES` and
  `AUTO_KUBERNETES_UPDATE`. `DockerUpdater` runs only with `DOCKER` and
  `AUTO_UPDATE`. M (updater_agent:44-58)

| Exit code | Cause | Mark |
|---|---|---|
| 2 | `HARBOR_ID` empty | M |
| 3 | `SHIP_ID` empty | M |
| 4 | neither `API_KEY` nor `AUTH_TOKEN` | M |
| 1 | uncaught exception in the agent, for example the startup check gives up | I |
| 0 | any exception in the updater outside the self-update, for example an RBAC refusal at `KubernetesUpdater` start | M (flow) |
| 0 | a failed registry login on docker | M |
| 0 | docker self-update succeeded (the old container ends) | M |

A container restart policy of `on-failure` does not restart an exit code 0. I

## Startup

In order (M unless marked):

1. The updater reads its config and logs `API_KEY` and `AUTH_TOKEN` at INFO.
   crane_updater.config:281, 284
2. Required ids are checked (exit 2, 3, 4).
3. The updater runs its self-update check (Kubernetes: needs `/versions`).
4. The agent reads its config and logs `API_KEY` and `AUTH_TOKEN` again.
   CFG:396, 399
5. The health server starts on `0.0.0.0:HEALTHCHECK_PORT` (default 5000),
   before any call to BlazeMeter. crane_agent:15-16
6. **Startup connectivity check**: `GET {A_ENVIRONMENT}/api/v4/private-locations/<h>/ships/<s>/status`,
   then `raise_for_status()`. Skipped with `BZM_SKIP_SANITY`. CH:62-65, 151-163
7. The container manager starts. On Kubernetes it creates a `test-job-<uuid>`
   Job to learn whether Jobs work, then **waits until one pooled Service is
   free**. If Service creation is refused, crane never finishes starting.
   KM:194-201, 251-252
8. The heartbeat and main loops start; the image map is fetched from
   `/versions?all=true` and refreshed every hour.
9. `verify()` reads the agent (`GET …/ships/<s>`) and retries forever.

Nothing in crane creates a location or an agent. M

### Timeouts on BlazeMeter calls

| Layer | Value | Evidence |
|---|---|---|
| Request timeout (connect and read) | 60 s | BC:128 |
| urllib3 retry | total, connect, read 5; backoff 0.3; retries on 429, 500, 502, 503, 504; POST included | BC:180-191 |
| Startup check | `retry(tries=10, delay=1, jitter=(1,3))` | CH:153 |
| Agent verification | `retry(delay=10, jitter=10, max_delay=600)`, no limit | CH:611 |
| Heartbeat | `retry(tries=10, delay=2)` | CH:221 |
| Run or create a container | `retry(tries=5, delay=2, jitter=1)` | CH:250 |
| HTTP 400–404 (normal calls) | log, sleep 300 s, continue | BC:196-199 |

Worst case for the startup check (I):

| Network state | Result |
|---|---|
| Packets dropped (blackholed proxy or host) | about 63 minutes, then exit 1 and a restart |
| Connection refused, or DNS failure | crash loop after about 2–3 minutes |
| Wrong token (HTTP 401) | exit 1 after about 1–2 minutes |
| Deleted location or agent (HTTP 404) | crane never stops; it sleeps 300 s per call and keeps going |

## Main loop and heartbeat

| Item | Value | Mark | Evidence |
|---|---|---|---|
| Command polling | `POST …/ships/<s>/batch-commands` in a loop with no sleep; the server long-polls | M (loop), I (long poll) | CH:795-816 |
| Worker threads | `PARALLEL_HANDLERS_COUNT`, default 20 | M | CFG:537-539 |
| Serial mode | `USE_PARALLEL_HANDLER=false` → `POST …/commands` once per pass | M | CH:584-589 |
| Heartbeat | `POST …/ships/<s>/status` every 30 s and right after each command | M | CH:182-189 |
| Heartbeat payload | `containers`, `address`, `ts`, `lastCommandOutput`, `version`, `hostInfo` (disk space, platform, container manager, image inventory), `updaterStatus`, `uptime`, `canProvisionBatch`, `baseUrl`, `hasProxy`, `verifySSL`, `isAutoUpdate` | M | updater.status:64-100 |
| `isAutoUpdate` | always false on Kubernetes | M | updater.status:100 |
| Heartbeat response | `logConfiguration` switches log shipping; `containers[].shouldBeRunning: false` removes zombies | M | CH:235-236, 632-656 |
| Agent states | crane has no `idle`, `running` or `empty` strings; BlazeMeter derives them | M (absence), I | — |

BlazeMeter endpoints crane calls, all under `{A_ENVIRONMENT}/api/v4/private-locations/<h>/ships/<s>`:
`GET /status` (startup), `GET` (verification), `GET /versions?all=true`
(hourly), `GET /versions` (docker updater), `POST /status`,
`POST /batch-commands`, `POST /commands`, `POST /containers/delta`. M

## Health and readiness

- Endpoint: `GET /healtz` (crane's spelling) on `HEALTHCHECK_PORT`. It returns
  200 when every registered check is within its time budget, else 500. With no
  checks registered it returns 200. M (health.webservice:32-36)
- Checks and budgets (M):

| Check | Budget | Refreshed by | Registered |
|---|---|---|---|
| Image Data Fetcher Loop | 3610 s | each `/versions` fetch | at import, before the startup check |
| Main Loop | 60 s | each main-loop pass | after the startup check |
| Heartbeat Loop | 120 s | a successful status POST only | after the startup check |
| Web Expose Service Loop | 180 s | the SV expose loop (Kubernetes) | at manager start |
| Service Pool Loop | 180 s | the Service pool loop (Kubernetes) | at manager start |

- **Consequence.** During the startup check only the 3610 s check exists, so
  the pod stays Ready for up to about an hour when BlazeMeter is unreachable.
  L: an unreachable proxy gave `1/1 Ready` with a stale heartbeat. After
  startup, the pod goes NotReady within about 120 s of failed heartbeats. I
- Edge case (I): with `AUTO_KUBERNETES_UPDATE=true` and BlazeMeter blackholed,
  the updater fetches `/versions` for about 6 minutes before the agent starts.
  No health server runs then, which is longer than the generator's liveness
  budget (300 s delay + 3 × 15 s), so the pod enters a restart loop.

## Configuration

### Precedence and parsing (M, CFG:313-349)

- `/etc/blazemeter/config.json` overrides the environment, which overrides
  crane's defaults. `-e NAME=VALUE` arguments are added to the environment.
- Boolean: true for `true`, `on`, `enabled`, `enable`, `1` (any case).
  Anything else, and unset, is false unless the default is true.
- JSON variables are parsed with `json.loads`; int and float with `int()` and
  `float()`.
- With `HTTP_PROXY` or `HTTPS_PROXY` set and `NO_PROXY` unset, `NO_PROXY`
  becomes `127.0.0.1,localhost`. CFG:424-427

### Variables

Defaults are crane's own. "Gen" marks a variable the generator writes today.
Evidence is in the findings under the matching section of this file.

**Identity, API and logging**

| Variable | Default | Effect |
|---|---|---|
| `A_ENVIRONMENT` | `https://a.blazemeter.com` | API base URL |
| `HARBOR_ID`, `SHIP_ID` | — | identity (Gen) |
| `AUTH_TOKEN` | — | header `X-Auth-Token`; logged at INFO; copied to every engine (Gen) |
| `API_KEY` | — | only satisfies the required check; logged at INFO; never sent |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | — | proxies for BlazeMeter calls; copied to engines in both cases (Gen) |
| `BZM_CRANE_SKIP_REQUESTS_PROXIES` | — | any value: no explicit proxies (the environment still applies, I) |
| `VERIFY_SSL` | `true` | TLS verification for BlazeMeter calls only; forwarded to engines |
| `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, any `*_CA_BUNDLE` | — | forwarded to engines; on Kubernetes also the mount paths (Gen) |
| `BZM_SKIP_SANITY` | false | skip the startup check |
| `RUN_HEALTH_WEB_SERVICE` | true | start the health server (Gen, redundant) |
| `HEALTHCHECK_PORT` | 5000 | health server port |
| `VERBOSE`, `VERBOSITY_LEVEL` | false, — | log level; `VERBOSE` also logs request headers, token included |
| `SEND_STATUS_HEARTBEAT_INTERVAL_IN_SEC` | 30 | heartbeat period |
| `IMAGES_VERSIONS_FETCH_INTERVAL_IN_SEC` | 3600 | `/versions` refresh |
| `USE_PARALLEL_HANDLER`, `PARALLEL_HANDLERS_COUNT`, `PARALLEL_HANDLER_UPDATE_INTERVAL` | true, 20, 5 | command handling |
| `PROVISION_BATCH` | true | reported `canProvisionBatch` |
| `USE_DELTA_REPORTING`, `DELTA_FLUSH_INTERVAL_SEC`, `DELTA_RESOLVE_POOL_SIZE`, `DELTA_UPSERT_CHUNK_SIZE` | false, 2, 8, 100 | delta reporting to `/containers/delta` |
| `ERROR_LOG_FILE_NAME` | — | `/tmp/<name>` for verification tracebacks |
| `PREFERRED_INTERFACE` | — | the address crane reports; otherwise any interface except `docker0` and `lo`, in set order (not "first") |
| `CRANE_VERSION` | from the image | reported version |

**Updates and cleanup**

| Variable | Default | Effect |
|---|---|---|
| `CONTAINER_MANAGER_TYPE` | `DOCKER` | `DOCKER`, `KUBERNETES` or `NATIVE` (Gen) |
| `AUTO_UPDATE` | true | docker self-update; also gates `forceUpdate` on every platform (Gen) |
| `AUTO_KUBERNETES_UPDATE` | false | Kubernetes self-update (Gen) |
| `UPDATE_INTERVAL` | 60 minutes | update check period; 86400 s after a failed update |
| `AUTO_UPDATE_CONTAINERS_WHILE_RUNNING` | true | the updater also patches running containers whose image is updatable while running |
| `IMAGE_OVERRIDES` | `{}` | merged over the image map (Gen) |
| `DOCKER_REGISTRY` | — | image prefix (Gen) |
| `SKIP_DOCKER_REGISTRY` | false | the docker updater refuses registry pulls |
| `AUTO_CLEAN`, `ZOMBIE_CLEAN` | true, true | cleaner and zombie removal; `AUTO_CLEAN=force` also accepted |
| `CLEANER_GRACE_MINUTES` | 3 | added to a container's expiry |
| `OVERRIDE_BZM_EXPIRES_DURATION` | — | replaces the command's expiry duration |

**Kubernetes engines**

| Variable | Default | Effect |
|---|---|---|
| `KUBERNETES_API_URL` | `https://kubernetes.default` | API server; the client never verifies its certificate |
| `KUBERNETES_SERVICE_ACCOUNT_TOKEN`, `KUBERNETES_NAMESPACE` | from the ServiceAccount files | API token and namespace |
| `KUBERNETES_RESOURCES_ALLOCATION_MODE` | `REQUESTS` | see [Resources](#resources) |
| `KUBERNETES_RESOURCES_DEFAULT_CPU`, `_MEM` | `0.25`, 256 (MiB, integer) | requests (Gen) |
| `KUBERNETES_RESOURCES_LIMITS_CPU`, `_MEMORY` | — | limits, written last (Gen) |
| `KUBERNETES_REQUESTS_EPHEMERAL_STORAGE`, `KUBERNETES_LIMITS_EPHEMERAL_STORAGE` | 100, — | both written only when the limit is set |
| `INHERIT_RUNNING_USER_AND_GROUP` | false | engines run as crane's UID and GID (Gen) |
| `KUBERNETES_SECURITY_CONTEXT_CAP_JSON` | `{}` | container `capabilities` (Gen) |
| `KUBERNETES_NODE_SELECTOR_JSON`, `KUBERNETES_TOLERATIONS_JSON` | `{}` | engine and DaemonSet placement (Gen) |
| `KUBERNETES_CUSTOM_ANNOTATIONS_JSON` | `{}` | pod annotations; `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` is always written over it |
| `KUBERNETES_LABELS` | `{}` | labels on engine Deployments, Jobs and pods only (not Services) |
| `KUBERNETES_CA_BUNDLE_MOUNT` | — | `ENV=configmap[=subPath]` entries joined by `:`; mount path = crane's value of `ENV`; `ENV` limited to `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, `TLS_CERT_GRID`, `TLS_KEY_GRID` (Gen) |
| `KUBERNETES_PERMANENT_MOUNT` | — | `claim=path[=ro]` PVC mounts |
| `KUBERNETES_READINESS_THRESHOLD`, `KUBERNETES_READINESS_INITIAL_DELAY`, `KUBERNETES_LIVENESS_INITIAL_DELAY` | 60, 0, 60 | engine probes |
| `ENV_POD_TERMINATION_PERIOD_SECONDS` | — | `terminationGracePeriodSeconds`; the variable really has the `ENV_` prefix |
| `KUBERNETES_CLEANER_RESTART_THRESHOLD` | 0 | restarts above this delete the engine Deployment; `-1` disables |
| `KUBERNETES_USE_PRE_PULLING` | false | pre-puller DaemonSet; pull policy `IfNotPresent` |
| `KUBERNETES_PRE_PULLING_NODE_TAGGING` | false | a docker-CLI DaemonSet with hostPath `/var/run` that labels nodes |
| `KUBERNETES_PRE_PULLER_REPULL_INTERVAL_SEC` | 900 | pre-puller `sleep` |
| `KUBERNETES_PREPULLER_LIMITS_CPU`, `_MEMORY` | — | pre-puller limits |
| `KUBERNETES_USE_CACHE`, `KUBERNETES_CACHE_TTL_POD_DATA_SEC`, `KUBERNETES_CACHE_TTL_POD_DATA_JITTER_SECS` | false, 600, 300 | ConfigMap cache `bzm-cache--<harbor>-<ship>` |

**Kubernetes Services and virtual services**

| Variable | Default | Effect |
|---|---|---|
| `KUBERNETES_SERVICE_USE_TYPE` | `nodeport` | `NODEPORT`, `CLUSTERIP` or `LOADBALANCER`, any case (Gen) |
| `KUBERNETES_SERVICE_USE_LOAD_BALANCER` | false | LoadBalancer; then web exposure is refused |
| `KUBERNETES_SERVICE_DEFAULT_CONTAINER_PORT`, `_WORLD_PORT` | 8080, 80 | pooled Service `targetPort`, and `port` under ClusterIP or LoadBalancer |
| `KUBERNETES_SERVICES_BUFFER` | 1 | free Services kept in the pool |
| `KUBERNETES_SERVICES_BLOCKING_GET` | false | false: an empty pool means the container is **not created** |
| `KUBERNETES_PARAMETERIZED_SVC` | false | one pool per Service type |
| `KUBERNETES_USE_APIPA` | true | a refused node read gives access host `127.0.0.1` |
| `KUBERNETES_WEB_EXPOSE_TYPE` | — | `INGRESS` = `NGINX`, `CONTOUR`, `ISTIO`, `OPENSHIFT`; exact case; other values warn and expose nothing (Gen) |
| `KUBERNETES_WEB_EXPOSE_SUB_DOMAIN` | — | required by every backend; lower-cased, dots stripped (Gen) |
| `KUBERNETES_WEB_EXPOSE_TLS_SECRET_NAME` | — | required by nginx and contour; istio TCP (Gen) |
| `KUBERNETES_WEB_EXPOSE_SHORT_URL` | false | host `<prefix>.<subdomain>` |
| `KUBERNETES_ISTIO_GATEWAY_NAME` | — | reuse this Gateway (Gen) |
| `KUBERNETES_INGRESS_CLASS` | `nginx` | nginx `ingressClassName` |
| `RECREATE_MISSING_INGRESSES` | false | recreate exposure for `service-mock` Deployments at start and unpause |
| `HOSTNAME_OVERRIDE` | — | mock container env on every platform; access info (Gen, docker) |
| `LOAD_BALANCER_HTTP_PORT_OVERRIDE`, `_HTTPS_PORT_OVERRIDE` | — | access-info labels |
| `VIRTUAL_SERVICE_LOGS`, `VIRTUAL_SERVICE_ADDITIONAL_JARS` | — | forwarded to containers; on docker, bind-mounted paths |

**Docker and native**

| Variable | Default | Effect |
|---|---|---|
| `DOCKER_PORT_RANGE` | `4000-5000` | host ports when the command gives none (Gen) |
| `DOCKER_REGISTRY_USERNAME`, `_PASSWORD`, `_EMAIL` | — | crane's own registry login, docker only |
| `DOCKER_HOST` | — | used only with a TLS client config; otherwise `unix://var/run/docker.sock` |
| `DOCKER_CERT_PATH`, `DOCKER_TLS_CERT`, `DOCKER_TLS_KEY`, `DOCKER_TLS_CA_CERT` | `/tmp`, — | TLS docker client; a decode error logs the value |
| `DOCKER_API_VERSION` | `auto` | docker-py API version |
| `DOCKER_LOG_DRIVER`, `DOCKER_LOG_DRIVER_CONFIG_JSON`, `DOCKER_SECURITY_OPTS_JSON` | — | child container log driver and security options |
| `DOCKER_SHM_SIZE_MB` | 256 | child `/dev/shm` |
| `DOCKER_VOID_DNS` | true | child `dns=[]` |
| `FORCE_HOST_PORT_ALLOWED` | true | honour a command's `FORCE_HOST_PORT` |
| `SECRET_MANAGER_MOUNT_SOURCE` | — | bind `<src>:/app/secrets:ro` for images named `*secrets*` |
| `TLS_CERT`, `TLS_KEY`, `TLS_CERT_GRID`, `TLS_KEY_GRID` | — | **paths**; with `useTls` forwarded and bind-mounted from crane's own mount; Kubernetes skips `TLS_CERT`/`TLS_KEY` (Gen) |
| `NATIVE_STATE_FILE`, `NATIVE_STATE_FILE_FLAGS`, `SQLITE_LOCK`, `KEEP_CRANE_UPDATE_BAT` | — | native backend |

Read but never used (M): `AGENT_FILES_URL`, `DISTRIBUTION`,
`DOCKER_REGISTRY_CREDENTIALS_FILE`, `KUBERNETES_CACHE_SIZE`. Defined but
never read: `S3_CRED_INTERVAL`. In `agent_env.py` but never read by crane:
`DODUO_PORT`.

## Commands

BlazeMeter sends generic commands. Crane has no code for one functionality,
and it names no engine: `r-v4-…`, `grid-r-sg-…` and `doduo-r-gp-…` come from
the command. M (dispatch CH:124-136); I (names)

| Command | Effect on Kubernetes |
|---|---|
| `runContainer` | Build the environment (below) and create the container; skip if the name exists |
| `runContainers` | The same for each command in the list, one at a time |
| `removeContainer` | Delete the Job or Deployment (`gracePeriodSeconds: 0`, Background) and its `bzm-sec-keys-<id>` Secret |
| `removeContainers` | Delete every Deployment and Job that matches a label selector |
| `execContainerCommand` | `sh -c '<env> <cmd>'` in container `ctr-<id>` of each running pod; output is not captured |
| `pauseContainer`, `unpauseContainer` | Scale the Deployment to 0, or back to `replicas` |
| `restartContainer` | Delete the Deployment's pods |
| `exposePortOnContainer`, `unexposePortOnContainer` | Virtual-service exposure (Kubernetes only; docker refuses it) |
| `forceUpdate` | Pass to the updater, only with `AUTO_UPDATE` |
| `removeResources` | No-op on Kubernetes |

Fields read from `runContainer`: `name`, `image`, `command`, `environment`,
`networkMode`, `harborConfig.overrideCPU/overrideMemory`, `labels`,
`privileged`, `exposedPort`, `portRange`, `portsMapping`, `userId`, `groupId`,
`workDir`, `replicas`, `useTls`, `restartPolicy`. M (CH:366-384)

## Engine pods on Kubernetes

### Kind and names (M)

- A **Job** when the command says `restartPolicy: Never` and the startup
  `test-job-<uuid>` worked. Otherwise a **Deployment** with `restartPolicy:
  Always`. KM:1335-1337
- Deployment: replicas from the command, `RollingUpdate` (maxSurge 1,
  maxUnavailable 0), selector = all template labels. KM:538-562
- Job: `backoffLimit: 0`; `ttlSecondsAfterFinished: 60` on Kubernetes 1.23 and
  later. KM:564-584
- The object name is the command's `name`; the container is `ctr-<name>`.
- `test-job-<uuid>` runs crane's own image with `echo 'Test Job'`, **no
  resources and no securityContext**. A LimitRange or an admission policy
  applies to it. KM:586-618

### Labels and annotations (M)

- Labels: the command's labels plus `BZM_HARBOR_ID`, `BZM_SHIP_ID`,
  `BZM_CONTAINER_NAME`, `BZM_BASE_URL`, `BZM_EXPIRES` (when the command sets a
  duration), and `KUBERNETES_LABELS`. CH:391-411; KM:1339-1340
- Annotations: `KUBERNETES_CUSTOM_ANNOTATIONS_JSON`, then
  `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` always. KM:530-533

### Image (M)

- The image is looked up in crane's image map: `<dockerTag>:latest` →
  `{DOCKER_REGISTRY}/<imageRelativePath>:<version>` from `/versions?all=true`,
  then `IMAGE_OVERRIDES` merged over it. An image not in the map is used as
  written. If no `/versions` resource has `imageRelativePath`, the map is
  `IMAGE_OVERRIDES` alone. IDF:50-53, 81-85, 111-116, 134-137
- `imagePullPolicy: Always`; `IfNotPresent` only with pre-pulling. KM:632
- **No `imagePullSecrets`, no `serviceAccountName`.** Engines run as the
  namespace's `default` ServiceAccount and get its pull secrets and token. M
  (absence), L (engine pods measured with `default` and no pull secrets)

### Resources

`cpu` = `overrideCPU` or `KUBERNETES_RESOURCES_DEFAULT_CPU`; `mem` =
`overrideMemory` (MiB) or `KUBERNETES_RESOURCES_DEFAULT_MEM`. M (KM:746-768)

| Mode | requests.cpu | requests.memory | limits.cpu | limits.memory |
|---|---|---|---|---|
| `REQUESTS` (default) | cpu | mem | not set | `overrideMemory` or **8192Mi** |
| `LIMITS` | DEFAULT_CPU | DEFAULT_MEM | cpu | `overrideMemory` or 8192Mi |
| then, both modes | — | — | `KUBERNETES_RESOURCES_LIMITS_CPU` if set | `KUBERNETES_RESOURCES_LIMITS_MEMORY` if set |

- Unset everywhere: requests 250m / 256Mi, a memory limit of 8192Mi and **no
  CPU limit**. The generator sets requests and limits explicitly, so its
  engines are QoS `Guaranteed`. L (2 CPU / 8Gi)
- `/dev/shm`: an emptyDir `medium: Memory` sized by the command env
  `BZM_CRANE_SHM_SIZE` (MB); 64 MB if that exceeds the memory limit. KM:724-729
- With no `engineXmx` on the location, Taurus sets the JMeter heap inside the
  engine: `-Xms3328m -Xmx6656m` at 8Gi (81%), beside a second JVM
  (`jetpack.jar`) with no `-Xmx`. L

### securityContext (M, KM:659-664, 737-741; UH:16-24)

| Field | Value |
|---|---|
| `runAsUser`, `runAsGroup` | crane's UID and GID with `INHERIT_RUNNING_USER_AND_GROUP`; else the command's `userId`/`groupId`; else **0** |
| `privileged` | false unless the run user is 0 and the command asks for it |
| `allowPrivilegeEscalation` | false when the run user is not 0; otherwise not set |
| `readOnlyRootFilesystem` | the command env `BZM_READ_ONLY_ROOT_FILESYSTEM` |
| `capabilities` | `KUBERNETES_SECURITY_CONTEXT_CAP_JSON` |
| Never set | `runAsNonRoot`, `seccompProfile`, `fsGroup`, a pod-level securityContext, `hostNetwork`, `priorityClassName` |

So engines meet PodSecurity `baseline` and never `restricted` on a cluster
that does not inject seccomp. L (`restricted` refused the engines on kind)

### Environment crane adds (M, CH:286-353; KM:1261-1287)

| Variable | When |
|---|---|
| `AUTH_TOKEN` | always, as a plain `value` |
| `http_proxy`/`HTTP_PROXY`, `https_proxy`/`HTTPS_PROXY`, `NO_PROXY`/`no_proxy` | when crane has a proxy |
| `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, every `*_CA_BUNDLE` | when set in crane's env |
| `TLS_CERT_GRID`, `TLS_KEY_GRID` | with `useTls` (Kubernetes skips `TLS_CERT`, `TLS_KEY`) |
| `VERIFY_SSL`, `VIRTUAL_SERVICE_LOGS`, `VIRTUAL_SERVICE_ADDITIONAL_JARS` | when set in crane's env |
| `STARTUP_SCRIPT_URL` | rewritten to crane's BlazeMeter host |
| `HOSTNAME_OVERRIDE` | for images named `service-mock` or `virtual-service` |
| `BZM_API_KEY_ID`, `BZM_API_SECRET_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | moved to Secret `bzm-sec-keys-<name>` and referenced with `secretKeyRef` |

### Mounts, scheduling, probes (M)

- CA bundle: one read-only ConfigMap volume per `KUBERNETES_CA_BUNDLE_MOUNT`
  entry, mounted at crane's value of the named variable. The generator's value
  gives one mount at `/var/cm/<key>`. KM:731-733, 1393-1498
- PVCs: `KUBERNETES_PERMANENT_MOUNT`. KM:1500-1540
- `nodeSelector` and `tolerations` from the JSON variables. **No affinity**
  (a node-affinity helper exists but is never called). KM:504-528
- Liveness: `/healthcheck.sh` if the image has it, else success; initial delay
  60 s, timeout 15 s, 3 failures. Readiness: the same script; period 1 s,
  timeout 5 s, 60 failures. KM:672-695
- preStop: `/pre-stop-hook.sh` if the image has it, else success. KM:698-700.
  The `FailedPreStopHook` event on every engine therefore comes from the
  engine image. L (event), I (cause)
- `containerPort` for each exposed port; no `hostPort` on Kubernetes.

## Service pool (Kubernetes)

- Crane keeps `KUBERNETES_SERVICES_BUFFER` (1) free Services named
  `crane-<5 hex>-<harbor>`, labelled `BZM_HARBOR_ID`, with no selector. Port
  8080 → 8080 under NodePort; 80 → 8080 under ClusterIP or LoadBalancer. M
  (SP:133-157)
- A container that exposes a port takes one Service and patches it: its labels,
  `USED_AT=<epoch>`, a selector on the container's labels, and its ports. M
  (SP:373-412)
- Every 5 s crane deletes an in-use Service older than 120 s whose selector
  matches no Deployment or Job. M (SP:165-204)
- Access info: node ExternalIP, else InternalIP (NodePort); `127.0.0.1` when
  the node read is refused and `KUBERNETES_USE_APIPA` is true. M (SP:290-305)
- **Why deleting a pooled Service breaks the agent** (I): the free queue and
  the in-use list live only in memory. A deleted free Service stays queued, so
  the next exposing container gets a 404 on patch and is never created. A
  deleted in-use Service stays in the list for ever; crane logs a traceback
  every 5 s and the container loses its access info.

## Virtual-service exposure (Kubernetes)

Host: `<prefix>-<port>-<namespace>.<subdomain>`, or `<prefix>.<subdomain>`
with `KUBERNETES_WEB_EXPOSE_SHORT_URL`; `prefix` = the command's
`subDomainPrefix` or the container name, cut to 63 characters. M (base:39-43)

| | nginx (`INGRESS`, `NGINX`) | istio | contour | OpenShift |
|---|---|---|---|---|
| Objects | `Ingress` `ing-<container>-<port>` | `Gateway` `gw-<svc>` (unless `KUBERNETES_ISTIO_GATEWAY_NAME`), `VirtualService` `vs-<svc>`, `DestinationRule` `dr-<svc>` (TCP_TLS only) | `HTTPProxy` `hprox-<container>-<port>` | `Route` `route-<svc>` |
| Class or selector | `ingressClassName`: `KUBERNETES_INGRESS_CLASS` or `nginx` | selector `istio: ingressgateway`, hardcoded | none | none |
| TLS secret | required; `tls[0].secretName` | TCP/TCP_TLS: port 443 `SIMPLE` with `credentialName`; HTTP/HTTPS/HTTP2: `PASSTHROUGH`, no secret | required; `virtualhost.tls.secretName`; passthrough with `tlsPassthrough` | none; `passthrough`, or `edge` with `insecureEdgeTerminationPolicy: Allow` for HTTP |
| Backend port | container port 8080 | access port (the nodePort under NodePort) | access port | container port 8080 |
| Unexpose | not implemented; the 60 s cleaner removes the Ingress | deletes its objects and returns the Service | reference count, then deletes | deletes Route and Service |

All M (per-backend modules). Under ClusterIP the Service port is 80 and the
nginx backend is 8080, which gives a 503; `KUBERNETES_SERVICE_DEFAULT_WORLD_PORT=8080`
may remove it. I, untested

## Cleaner, cache, pre-pulling, inventory (Kubernetes)

- Every 30 s the cleaner removes pods and Jobs whose `BZM_EXPIRES` has passed,
  pods in phase `Succeeded` or `Failed`, finished Jobs, and orphan
  `bzm-sec-keys-*` Secrets. M (CL:48; CLK:19-72, 147-187)
- A pod restart count above `KUBERNETES_CLEANER_RESTART_THRESHOLD` (default 0)
  deletes its Deployment: the first engine restart removes the engine. M
- Removal is always forceful on Kubernetes (`gracePeriodSeconds: 0`). M
- "Active containers" in the heartbeat = every Deployment and Job labelled with
  this harbor and ship and not being deleted, a 0-replica SV Deployment
  included. BlazeMeter's "Cannot remove ship with active containers" probably
  counts this list. M (list), I (BlazeMeter side)
- Pre-puller DaemonSet `<ctx>-crane-pre-puller-<harbor>`: one container per
  image in the map, `sleep 900`, `imagePullPolicy: Always`, no securityContext,
  no pull secrets. Node tagging adds a docker-CLI DaemonSet with hostPath
  `/var/run`, which works only on docker-runtime nodes and which `baseline`
  refuses. M (structure), I (runtime and PSA)
- **The inventory a Kubernetes agent reports is its image map** (BlazeMeter's
  `/versions` list plus `IMAGE_OVERRIDES`), not the images on the node. An
  image in it was not necessarily pulled. M (KM:1389-1391; IDF:36-58)

## RBAC

Every Kubernetes API call in crane (M):

| Group | Resource | Verbs crane uses |
|---|---|---|
| "" | pods | list, watch, delete, deletecollection |
| "" | pods/exec | create (get for the older upgrade path) |
| "" | services | create, get, list, patch, delete |
| "" | endpoints | list |
| "" | secrets | create, patch, delete, list |
| "" | configmaps | list, get, create, update, delete — only with `KUBERNETES_USE_CACHE` |
| "" | nodes (cluster) | get; list and patch only with node tagging |
| apps | deployments | create, get, list, watch, delete, patch |
| apps | deployments/scale | patch |
| apps | replicasets | get |
| apps | daemonsets | create, get, list, delete — only with pre-pulling |
| batch | jobs | create, get, list, watch, delete |
| networking.k8s.io | ingresses | create, list, delete |
| networking.istio.io | gateways, virtualservices, destinationrules | create, list, patch, delete |
| projectcontour.io | httpproxies | create, list, patch, delete |
| route.openshift.io | routes | create, list, patch, delete |

Against the generator's Role and ClusterRole:

- **Missing:** `configmaps` (only with the cache); `nodes: patch` (only with
  node tagging); istio `destinationrules` (TCP_TLS virtual services).
- **Granted but unused:** the `extensions` group; `pods/log`; `pods/*`
  (only `pods/exec` is used); pods get, create, update, patch; services watch,
  update, deletecollection; endpoints except list; replicasets except get;
  daemonsets watch, update, patch, deletecollection; deployments update,
  deletecollection; jobs update, patch, deletecollection; secrets update;
  nodes watch.
- Crane reads nodes only to choose a Service's access address and for node
  tagging, never for capacity. The ClusterRole comment says otherwise.
- Crane uses Secrets only for the five API keys in engine env, not for
  registry credentials. The Role comment says otherwise.

## Self-update

**Kubernetes** (only with `AUTO_KUBERNETES_UPDATE`), M (KU:35-175):

1. Crane finds its own Deployment from its pod IP (pods list, replicasets get).
2. The target image is the image map's `blazemeter/crane:latest` entry, that is
   `{DOCKER_REGISTRY}/<imageRelativePath>:<version>` from `/versions?all=true`,
   or `IMAGE_OVERRIDES["blazemeter/crane:latest"]`. Crane never pulls the
   registry's `:latest` tag.
3. It refuses a Deployment with more than one container or `imagePullPolicy:
   Never`.
4. It creates `<name>-test-run` (label `TEST_RUN=True`), waits up to 300 s for
   it to be available, and deletes it. A test-run crane polls no commands and
   sends no heartbeat.
5. It patches its own Deployment: the image, a template label `ROLLBACK`, and
   **strategy `RollingUpdate` maxSurge 1 / maxUnavailable 0**. This replaces
   the generator's `Recreate`.
6. If it is still alive after 300 s, it rolls back and waits 86400 s before
   the next try.

During a periodic update the old and new crane overlap until the new pod is
Ready (I). That may break "one crane per agent" for that period.

**Docker** (only with `AUTO_UPDATE`), M (DU:178-724): crane pulls each
`/versions` resource (logging in with `DOCKER_REGISTRY_*` or BlazeMeter's
per-resource credentials), renames itself `blazemeter-crane-old-…`, starts
`blazemeter-crane-temp-<ship>` with the same env, user, mounts and network
(default `host`) and restart policy `on-failure`, and renames it to
`bzm-crane-<ship>` when it is healthy. The new container is outside compose's
control. I

## Docker backend

- Crane uses `unix://var/run/docker.sock` unless a TLS client is configured.
  The image runs as UID 1337, so the stock `root:docker 0660` socket needs
  `-u 0` (or the docker group). M (socket, user), I (permissions)
- Engine and mock containers (M, DM:325-413):

| Setting | Value |
|---|---|
| user | crane's UID:GID with `INHERIT_RUNNING_USER_AND_GROUP`; else the command's, else `0:0` |
| ports | `exposedPort` → a host port from `portRange` or `DOCKER_PORT_RANGE` (4000–5000) |
| mounts | only crane's own bind mounts whose destination equals a CA, TLS or virtual-service path; **never the docker socket** |
| memory, CPU | `mem_limit` = `overrideMemory` MB; `cpu_shares` = `overrideCPU` × 1024 |
| restart | `no` for `restartPolicy: Never`, else `unless-stopped` |
| env | as on Kubernetes, `AUTH_TOKEN` included |

- Crane never pulls when it creates a container. Only the auto-updater pulls.
  M
- Virtual services on docker publish a host port; access host = the first
  interface except `lo` and `docker0`. `TLS_CERT` and `TLS_KEY` are paths that
  must be mounted into crane at the same path. Crane does not parse the
  certificate, so the PKCS#8 and hostname rules come from the mock image, if
  they exist. M
- Inventory: local images whose repository is on the location's `/versions`
  list, plus any `blazemeter/charmander` image. M
- Cleanup: expired and exited containers with the harbor label. M

## Native backend

`CONTAINER_MANAGER_TYPE=NATIVE` runs images as host processes from downloaded
zips and keeps state in SQLite (`crane.db`). It is Windows-first and is the
only type that runs as a Windows service. No exec, no dynamic exposure. M

## Secrets and logs

| Where the token appears | Mark |
|---|---|
| crane's log at startup, twice (updater and agent), with `API_KEY` | M, L |
| crane's log at DEBUG with `VERBOSE` (request headers) | M |
| every engine pod spec, as a plain env value | M |
| crane's own Secret or ConfigMap (the generator's `use_secret`) | L |
| a `DOCKER_TLS_*` value that fails to decode is logged | M |

Crane's agent log also goes to a file under `/tmp` that rotates every 30 s
(prefix `crane.d/<harbor>/<ship>`). BlazeMeter can switch on its upload to
object storage through `logConfiguration` in the heartbeat response. That log
holds the token line. M (mechanism), I (upload)

Crane needs a writable `/tmp` (log file, error log, PyInstaller extraction).
I

## torero and richrach

Both images are in the location's image list, but crane has no code for
either one. It runs one only when BlazeMeter sends a `runContainer` that names
it; no run in this project did. What each does, read from its own code, is in
[docs/images.md](docs/images.md#torero-and-richrach). Summary:

- **torero** checks a test's script files with Taurus and posts the result to
  the test's validations; it can also import a Swagger file. It sends its API
  key as HTTP Basic authentication to each address in
  `DEPENDENCIES_VALIDATORS_DETAILS`.
- **richrach** zips a report's logs or crane's own log files into S3-compatible
  storage and reports a 24-hour link. It does not verify the storage's TLS
  certificate.

## Open questions

- Which BlazeMeter action sends torero or richrach to a private location.
- Whether a `ValidationError` from the SV expose service at start ends crane.
  The call has no local handler. I: it is fatal.
- Where `bzm_common_library` sends the log upload, and its credentials. The
  library was not extracted.
- What BlazeMeter does with `ACCESS_HOSTNAME_OVERRIDE` and the load-balancer
  port overrides on Kubernetes.
- Whether the overlap of two cranes during a Kubernetes self-update produces
  duplicated results.
