# Live rig

How to run `livetest`, read its output, verify a virtual service by hand, and
release an agent. The account guardrails (confirm writes, standing fixtures)
are in `CLAUDE.md`; the customer-facing account of what a pass proves is
`docs/live-test.md`.

## Before a run

```
bzm-opl-gen toolcheck --cluster minikube --local-registry 5001 --local-proxy
```

- Fixtures: `testbed.local.md` if present; otherwise a scratch location and
  agent (`create-location`, `create-agent`), deleted after the run.
- `--run-test` proves something only with a test whose samplers hit the
  network and whose locations live in `executions` — a JMX test, not a
  taurus-script one.

## Run

```
PYTHONUNBUFFERED=1 .venv/bin/python -m bzm_opl_gen livetest --api-key api-key.json \
    --namespace bzm-livetest --cluster minikube \
    --local-registry 5001 --local-proxy --contain-egress \
    --run-test <test-id> --timeout 420 > <log> 2>&1
```

Run it with `run_in_background: true` and poll with
`until grep -qE "LIVE TEST|Traceback"`. `PYTHONUNBUFFERED=1` makes the log
stream; `kubectl get pods` is the live view. Allow 12–20 min with every flag.

**Cluster.** `--cluster minikube` runs a 4 CPU / 6g node, which fits only the
rig's default 1 CPU / 4Gi engine. `--cluster kind` uses the kind cluster
`bzm-opl-test`, creating it if absent; one that already existed is never
deleted. Kind nodes see the whole VM, so a 2 CPU / 8Gi engine
(`--engine-cpu 2 --engine-mem 8Gi`) fits. The registry
blackhole and `--contain-egress` (calico) are minikube-only.

## After a run

```
python -c "from bzm_opl_gen import core; print(core.client_from_key('api-key.json').test(<id>).get('executions'))"
kubectl get ns | grep bzm-livetest ; docker ps -a | grep bzm-opl ; minikube status -p bzm-opl-test
```

## What each flag proves

- `--local-registry` mirrors the location's images into a `registry:2` and (on
  minikube only) blackholes public registries, so a missing `IMAGE_OVERRIDES`
  key fails. Pair it with `--run-test`: a crane-only run pulls no engine image
  (the rig warns). The mirror reads the generator's destinations
  (`image_registry.cluster_composed_targets`), never a rule of its own.
- `--local-proxy` runs mitmproxy on the cluster's docker network, never on a
  host port (something else may own it). A CONNECT probe must show in our own
  log. The negative control deploys CA-stripped and requires
  `CERTIFICATE_VERIFY_FAILED` (`--skip-negative-control` only while iterating).
- `--contain-egress` needs calico — minikube's default CNI accepts
  NetworkPolicies and enforces nothing. The API rule names the ClusterIP *and*
  the endpoint (policy is evaluated after kube-proxy DNAT).
- `--run-test` spawns a real engine. Engines mount the CA as a file
  (`/var/cm/ca-bundle.crt`); crane mounts the directory. Engine traffic is SNAT'd
  to the node address, so it is identified by `footprint.ENGINE_UPLOAD_HOSTS`.
- JMeter ignores `HTTP(S)_PROXY` for sampler traffic, so engine→SUT fails under
  `--contain-egress` while results still upload. Expected; the proxy belongs in
  the test, not the generator.
- The rig deploys the directory as it sits and re-renders only to inject the
  proxy CA or engine sizing (it mints a token only then). `bundle_check`
  refuses a bundle whose `HARBOR_ID`/`SHIP_ID` is not the agent under test, any
  unknown `*.yaml`, a chart directory, `service_account_create: false`, a
  placeholder token it won't re-render, and a `file`/`existing` CA mode without
  `--local-proxy`. A re-render keeps the bundle's own CA mode
  (`bundle_check.rig_ca_mode`).
- Two rigs, chosen by the bundle (`bundle_check.bundle_platform`), never a
  flag: `livetest.run` applies manifests; `run_compose` does
  `docker compose up -d`, waits for the heartbeat, takes it down. Compose runs
  never start an engine, so `-u 0` is still unproven there.
- The rig deletes a cluster only if it created it (`ensure_cluster` says which).
  Existence is read from `minikube profile list`; a stopped profile is started
  but still not the run's. A surviving cluster keeps the namespace and the
  node's `/etc/hosts` edits, so teardown removes those explicitly. Exception:
  `--contain-egress` recreating a minikube profile with no policy enforcer
  (announced) makes the profile the run's own.

## Reading an engine run

- `ENGINE SIZING:` prints only when an engine's requests differ from its
  limits; its absence is the pass. Read `kubectl get pod -o json` for the
  numbers: a correct engine is QoS `Guaranteed`.
- `ENGINE HEAP:` reads the JMeter `-Xmx` from the engine's running processes:
  Taurus starts JMeter only after `INIT_SCRIPT`, and the pod is gone soon
  after the run ends. To read a pod by hand, match processes on argv0 (`java`),
  never on the whole command line, or the probe finds itself.
- An engine `Pending` for the whole run, a master stuck at `BOOT_STARTING` and
  0 samples mean the engine image never arrived — usually disk (the v4 image is
  ~3.5GB), not the agent.
- After such a run the agent stays `running` in BlazeMeter and holds its slot,
  so the next start answers 403 `Not enough available resources`. Terminate the
  master (`POST /masters/{id}/terminate`); the slot can stay held after it
  ends, so give a scratch location a second slot.

## Inducing failures for triage

Break the kept deployment one way at a time and restore it between: a wrong
AUTH_TOKEN in the Secret, a crane tag that does not exist (`kubectl set image`),
an unreachable `HTTPS_PROXY` in the ConfigMap. `kubectl apply` of the bundle
does not remove keys a patch added; remove them with a JSON patch.

## Local environment

- A full disk makes minikube fail with `RSRC_DOCKER_STORAGE`. `toolcheck` knows
  which number binds (VM `df` for colima/Lima, host free space for Docker
  Desktop).
- colima's default `fs.inotify.max_user_instances` (128) is used up by a running
  kind cluster, and the minikube node dies with `Too many open files`
  (`GUEST_PROVISION_CONTAINER_EXITED`). Raise it until colima restarts:
  `colima ssh -- sudo sysctl -w fs.inotify.max_user_instances=8192`.
- `docker image prune --filter until=…` filters on *build* date and deletes
  BlazeMeter images the day you pull them.
- arm64: BlazeMeter images are amd64-only; size engines down
  (`--engine-cpu 1 --engine-mem 4Gi`). Pin `mitmproxy:11.1.3` (12+ SIGILLs).

## Virtual services by hand

`livetest` covers performance only; verify SV by deploying a real virtual
service and curling the advertised endpoint.
- One ingress controller can hold the node's :80/:443; scale the incumbent to 0.
  Istio's gateway must carry label `istio: ingressgateway` (crane hardcodes it).
- `CONFIGURING` after an interrupted deploy refuses both deploy and stop; it
  drops to `FAILED` on its own after a few minutes.
- A virtual service can only be created after its location's agent has been
  online (`idle`), and the SV side lags the agent by a minute or two — retry.
- `KUBERNETES_SERVICE_USE_TYPE` changes don't touch Services already in crane's
  pool; `kubectl get svc` shows the old type.
- After replacing crane, the first deploy fails on `CONTAINER_READY` while the
  new crane cleans up; budget one.

## Releasing an agent BlazeMeter won't delete

`Cannot remove ship with active containers`: let a *running* crane report zero.
On Kubernetes delete the stopped virtual service's mock Deployment with crane
still up, wait for `idle`, then remove crane, the ship and the location in that
order. On docker, start any crane on that harbor/ship id with its token; it
clears in ~30s. Deleting crane first wedges it.
