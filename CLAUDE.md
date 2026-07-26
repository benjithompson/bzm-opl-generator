# Working on bzm-opl-gen

Generates BlazeMeter OPL (private-location) k8s/OpenShift manifests from a
customer's real account facts, and live-tests them. README.md is the user-facing
doc; this file is what a session needs to know before touching the tests.

## Two test layers

**Offline (`.venv/bin/python -m pytest tests -q`)** — stdlib + fixtures, no
cluster, ~1s. Every check in the live rig has an offline counterpart that fakes
the cluster/API response, so failure modes are covered without burning 15
minutes. Add one whenever you add a live check.

The run must end **`N passed`** with nothing skipped. `tests/test_server.py`
skips its whole module when `fastapi` is missing, so a venv built without the
optional extra reports a clean pass while testing none of the HTTP layer — it
went unnoticed for a while. Install `.venv/bin/pip install -e ".[dev]"`, which
is `[test]` + `[ui]`; `fastapi` is now in `[test]` too, and CI asserts the
optional deps import rather than trusting a green run.

**Live rig (`bzm-opl-gen livetest`)** — deploys generated manifests to a local
cluster and waits for the agent to report online in a real BlazeMeter account.
Canonical full invocation:

```
.venv/bin/python -m bzm_opl_gen livetest --api-key api-key.json \
    --namespace bzm-livetest --cluster minikube \
    --local-registry 5001 --local-proxy --contain-egress \
    --run-test 15791473 --timeout 420
```

Runs take **12–20 minutes**. Start them with `run_in_background: true` and poll
the log with an `until grep -qE "LIVE TEST|Traceback"` loop — never assume a
short wait is enough. Python buffers stdout when redirected, so the log is
mostly empty until the process exits; `kubectl get pods` is the live view.

## Account facts (SE Demo, private repo so IDs are fine here)

| what | id |
|---|---|
| account | `291446` (BlazeMeter SE Demo) — *not* the default 1798215 |
| workspace / project | `2194183` / `2503033` |
| scratch private location | `6a63a79dcc45dccca90bf440` (`scratch-opl-perf-livetest`), ship `6a63a7a4b3187156310483f5` |
| smoke test, real HTTP | `15791473` — Taurus, 1 VU, 60s, hits blazedemo.com |
| API key | `api-key.json` in the repo root (gitignored) |

Do **not** use test `15783207` for engine validation: it is dummy samplers, so
it reports ~499 plausible samples while issuing no network requests at all.

Rules for the account: create scratch locations rather than reusing colleagues'
harbors, and if the rig repoints an existing customer test, it must restore the
original `executions` (the code does this in a `finally` and prints the original
first). Verify after any live run:

```
python -c "from bzm_opl_gen import api; print(api.BzmClient('api-key.json').test(<id>).get('executions'))"
kubectl get ns | grep bzm-livetest ; docker ps -a | grep bzm-opl ; minikube status -p bzm-opl-test
```

## What the rig proves, and the traps behind each part

- **`--local-registry`** mirrors the location's images into a `registry:2` and
  blackholes public registries on the node (`127.0.0.1 gcr.io` + cached-image
  purge), so a missing `IMAGE_OVERRIDES` key fails here instead of silently
  falling back to the public registry.
- **`--local-proxy`** runs mitmproxy **on the cluster's docker network**, never
  published to a host port — port 8080 on this Mac is taken, and the node then
  reaches that instead and gets its 403 while our proxy's log stays empty. A
  CONNECT probe requires the attempt to appear in our own log before proceeding.
- **Negative control** deploys CA-stripped first and requires
  `CERTIFICATE_VERIFY_FAILED`. Keep it on unless iterating (`--skip-negative-control`).
- **`--contain-egress`** needs calico; minikube's default CNI accepts
  NetworkPolicies and enforces nothing, so the profile is recreated if no policy
  enforcer is present. The API rule must name the ClusterIP *and* the endpoint —
  policy is evaluated after kube-proxy DNAT.
- **`--run-test`** spawns a real engine. Engines mount the CA as a *file*
  (`/var/cm/ca-bundle.crt`, subPath) where crane mounts the directory. Engine
  traffic cannot be identified by pod IP — pods are SNAT'd to the node address
  before the proxy sees them — so it is identified by `data.blazemeter.com` /
  `storage.blazemeter.com`, which only engines use.

Known and expected: **JMeter ignores `HTTP(S)_PROXY`** for sampler traffic (a
library convention, not a JVM one), so engine→SUT goes direct and fails under
`--contain-egress` while results still upload. Not a manifest bug; the proxy has
to go in the test. Don't "fix" it in the generator.

## Environment (this machine)

- Docker is **Docker Desktop** here now (context `desktop-linux`; colima is no
  longer installed) — this line said colima for a long while, so check
  `docker context ls` rather than trusting it. `bzm-opl-gen toolcheck` reports
  the active daemon and the free space that matters: colima preallocates a
  fixed VM disk, so its own `colima ssh -- df -h /` binds, while Docker
  Desktop's disk is a sparse file that grows into the host filesystem. Either
  one filling up makes minikube fail with `RSRC_DOCKER_STORAGE`, which does not
  mention disk. `docker image prune -a --filter until=168h` filters on image
  *build* date, not last use — it will delete BlazeMeter images pulled the
  same day.
- arm64: BlazeMeter images are amd64-only and run under Rosetta. Engines are
  slow; size them down (`--engine-cpu 1 --engine-mem 4Gi`) or they stay Pending.
- Pin `mitmproxy:11.1.3`; 12+ dies with SIGILL on Apple-silicon VMs.
- `minikube -p bzm-opl-test` is disposable — the rig deletes and recreates it
  freely. Anything else running in docker (e.g. a saleor stack) is the user's.

## Generator details that bite

- `generate` writes `out/profile.json` (resolved options minus `auth_token`);
  `livetest` re-renders from it, so manifests under test stay generator output
  rather than hand-patched YAML.
- CA bundles exceed the 256KB cap on kubectl's last-applied-configuration
  annotation — manifests over 200KB apply `--server-side`.
- A taurus-script test keeps its locations in the uploaded YAML;
  `PATCH /tests/{id}` silently drops `executions` for one.
- The emitted LimitRange's `max` is raised to cover **crane's own** limits
  (1 CPU / 2Gi) when engines are configured smaller. A `max` pinned to the
  engine size gets the crane pod rejected in its own namespace — verified
  against a real API server, not theory. Don't "tighten" it back.
- **`bzm_limitrange.yaml` does not change engine requests, and cannot.** Crane
  sets the engine pod's requests explicitly to 250m/256Mi; a LimitRange's
  `defaultRequest` only fills fields a pod leaves unset. Proven on a live run:
  the `r-v4-*` engine pod comes back with no `kubernetes.io/limit-ranger`
  annotation, while crane's `test-job-*` pods (which declare nothing) do carry
  one. Issue #2 was filed on the opposite assumption — don't re-add the claim.
  `livetest --run-test` prints the live gap as `ENGINE SIZING:`.
- `doctor` measures capacity against node **allocatable**, deliberately: what is
  actually free needs every pod's requests summed per node, which is a much
  bigger read for a preflight. Say "upper bound" in any detail string you add.

## Conventions

- Comments explain *why*, especially where a non-obvious environment fact drove
  the code. Match the existing density; don't narrate the obvious.
- A git hook blocks pushing to `main`. Commit on a branch, push that, and ask
  the user to fast-forward main themselves.
- Creating or starting anything in the BlazeMeter account is a real write —
  confirm with the user first unless they already named the artifact to use.
