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

**Helm parity (`python tests/helm_parity.py`)** — renders 19 option
combinations as both `--format manifests` and `--format helm` and requires the
same objects out of each. Deliberately *not* a pytest module: it shells out to
`helm`, and a test that skips when a binary is missing is the fastapi problem
again. It has its own CI job. Every judgement in `templates/*.yaml` had to be
restated in Go templates, and nothing else notices one being restated slightly
differently. Its offline counterpart is `tests/test_helm.py`, which covers
everything decided in Python (the values overlay, the refusals) and needs no
helm. Add to both when you touch either side.

**Live rig (`bzm-opl-gen livetest`)** — deploys generated manifests to a local
cluster and waits for the agent to report online in a real BlazeMeter account.
Canonical full invocation:

```
.venv/bin/python -m bzm_opl_gen livetest --api-key api-key.json \
    --namespace bzm-livetest --cluster minikube \
    --local-registry 5001 --local-proxy --contain-egress \
    --run-test <TEST_ID> --timeout 420
```

Runs take **12–20 minutes**. Start them with `run_in_background: true` and poll
the log with an `until grep -qE "LIVE TEST|Traceback"` loop — never assume a
short wait is enough. Python buffers stdout when redirected, so the log is
mostly empty until the process exits; `kubectl get pods` is the live view.

## Account facts

Live runs need a real account. Nothing identifying one is recorded here —
gather it at the start of a session instead, and keep it in the session rather
than committing it:

```
bzm-opl-gen locations --api-key api-key.json --account-name "<ACCOUNT NAME>"
```

| what | where it comes from |
|---|---|
| account / workspace / project | `locations`, or the account owner |
| scratch private location + ship | create your own (`create-location`, `create-ship`) |
| smoke test for `--run-test` | an existing Taurus test that makes **real HTTP requests** |
| API key | `api-key.json` in the repo root (gitignored) |

`--run-test` is only meaningful with a test whose samplers hit the network. A
dummy-sampler test reports hundreds of plausible samples while issuing no
requests at all, so the engine validation passes without proving anything —
confirm what the test actually does before trusting a green run.

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
  published to a host port. Publishing it makes the rig silently wrong whenever
  something else already owns that port — 8080 is a popular one — because the
  node then reaches *that* service, gets its 403, and our proxy's log stays
  empty. A CONNECT probe requires the attempt to appear in our own log before
  proceeding.
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

## Local environment

Run `bzm-opl-gen toolcheck --cluster minikube --local-registry 5001
--local-proxy` first — it reports the active docker daemon, free disk, and
missing tools for whatever setup you actually have. The rest of this section is
the classes of problem it can't fix for you.

- **Docker provider varies and it matters for disk.** Whatever runs the daemon,
  a full disk makes minikube fail with `RSRC_DOCKER_STORAGE`, which never
  mentions disk. Which number binds depends on the provider: a preallocated VM
  disk (colima, Lima, Minikube's own VM) is bounded by the VM's `df`; a sparse
  disk image on the host (Docker Desktop) is bounded by host free space.
  `toolcheck` picks the right one; `docker context ls` tells you what you're on.
- **Pruning by age deletes images you still need.** `docker image prune -a
  --filter until=168h` filters on image *build* date, not last use — BlazeMeter
  images are old enough to be deleted the day you pull them.
- **arm64:** BlazeMeter images are amd64-only and run under whatever x86
  emulation your docker runtime provides. Engines are slow; size them down
  (`--engine-cpu 1 --engine-mem 4Gi`) or they stay Pending.
- **Pin `mitmproxy:11.1.3`**; 12+ dies with SIGILL on arm64 VMs.
- `minikube -p bzm-opl-test` is disposable — the rig deletes and recreates that
  profile freely, and touches only its own named containers. Whatever else you
  have running in docker is left alone.

## Generator details that bite

- **"Could not read" and "there is nothing there" must never share a
  representation.** This has been the same bug four times, and three of them
  were found within one session: `null` vs `[]` in the evidence collector; the
  same collapse already latent in `gather_cluster()`, where a denied `list
  nodes` produced the identical "engines have nowhere to run" FAIL and non-zero
  exit as an empty cluster; `auth can-i` and `api-resources` both reporting
  failure as *no*, so a file collected with no kubeconfig read as a locked-down
  cluster and would have yielded a configuration about a cluster nobody
  described; and `raw.namespace: null` becoming `{}`, which had `check_admission`
  announce a namespace "does not exist yet — re-run after creating it" when it
  had merely been refused. The fourth landed *inside* the change written to fix
  the first two, which is the point: the distinction survives only where it is
  structural, never where it is remembered. A denied read is a WARN and exits 0;
  an empty result can be a FAIL. If a new field cannot express both, it is not
  ready to be read. (`versions.serverVersion` is how the boolean sections tell
  the two apart, since a bare `false` cannot.)
- `generate` writes `out/profile.json` (resolved options minus `auth_token`);
  `livetest` re-renders from it, so manifests under test stay generator output
  rather than hand-patched YAML.
- **The chart is copied, never re-rendered.** `--format helm` walks
  `templates/helm/` and emits it verbatim, so anything added there ships in every
  generated bundle — including files `package-data` would drop. Its globs do not
  recurse and `*` does not match a leading dot, which is why `.helmignore` and
  each directory are named explicitly in `pyproject.toml`; the release workflow
  asserts the wheel carries them, because a missing chart file fails at generate
  time on an installed copy and never in a checkout.
- `--format helm` refuses a service-virtualization location, and `livetest`
  refuses a chart directory. Both are one-line guards over silent failures —
  a chart without the ingress stalls at `WAITING_FOR_DOMAIN`, and the rig's
  `*.yaml` glob would come back empty. `livetest` also refuses a profile with
  `service_account_create: false`: the rig creates its own namespace, so an
  account it was told already exists never does, every object applies, no pod
  is created, and the run waits out its whole timeout.
- CA bundles exceed the 256KB cap on kubectl's last-applied-configuration
  annotation — manifests over 200KB apply `--server-side`.
- A taurus-script test keeps its locations in the uploaded YAML;
  `PATCH /tests/{id}` silently drops `executions` for one.
- **This generator emits no LimitRange, and shouldn't.** It used to, opt-in, and
  it was removed after a live install showed both halves of why. It cannot
  change engine requests: crane sets the engine pod's requests explicitly to
  250m/256Mi, and a LimitRange's `defaultRequest` only fills fields a pod leaves
  unset — the `r-v4-*` engine pod comes back with no
  `kubernetes.io/limit-ranger` annotation at all. And what it *did* reach was
  crane's `test-job-*` pods, which declare nothing and so were handed a full
  engine's worth of CPU and memory for jobs that need neither, reserving
  capacity a real engine then couldn't get. Issue #2 was filed on the assumption
  it helped — don't re-add it. `livetest --run-test` prints the live gap as
  `ENGINE SIZING:`.
- `doctor` still *reads* a LimitRange the customer already has, which is a
  different thing and stays: an existing `max` below the engine size, or below
  crane's own 1 CPU / 2Gi, rejects the respective pod at admission.
- `doctor` measures capacity against node **allocatable**, deliberately: what is
  actually free needs every pod's requests summed per node, which is a much
  bigger read for a preflight. Say "upper bound" in any detail string you add.

- **`facts.manual()` is the same shape `gather()` returns, on purpose.** The UI's
  manual mode and `facts --manual` build facts from a typed harbor id, ship id
  and token so a bundle can be produced for an account nobody here can reach.
  Nothing downstream learns which way the facts arrived — keep it that way, and
  add to `FALLBACK_IMAGES` rather than special-casing the manual path.
- **A Kubernetes agent reports its images as bare keys** -- `taurus-cloud:latest`,
  `torero:4.6.182` -- with no registry and `Size: 0`, i.e. crane's configured
  image set rather than what is on the node. Docker agents report
  registry-qualified tags instead. `gather()` handled only the Docker shape, so
  every k8s agent -- the kind this tool generates for -- silently produced no
  inventory and fell through to the catalogue; that is how `torero` and
  `richrach` stayed missing from a performance bundle. `repo_for_key()` resolves
  the bare form. Reading it properly also pins exact tags (`crane:3.7.55`,
  `torero:4.6.182`) where the catalogue could only say `latest`.
- **`FALLBACK_IMAGES` was read off live inventories, not derived from the keys.**
  Keys do not reliably match their repo — `taurus-cloud`→`v4` and
  `apm-image`→`apm` in the table, `blazemeter`→`v3` and `secrets-image`→`secrets`
  elsewhere in the account — so a "tidy-up" that regularises them produces repos
  that do not exist. `test_manual_facts.py` asserts the
  catalogue covers every category `CATEGORY_BY_FUNC` can ask for — a new funcId
  needing a new category fails there rather than on a sealed cluster.
- GUI browser images are the one gap and cannot be closed: the account carries
  60+ version-pinned `charmander/*` repos and only a live agent says which one a
  location uses. `facts.gui_images_incomplete()` flags it; don't invent a default.

## Conventions

- Comments explain *why*, especially where a non-obvious environment fact drove
  the code. Match the existing density; don't narrate the obvious.
- Never push to `main`. Commit on a branch, push that, open a PR. `.githooks/`
  holds a pre-push guard, but it only applies where someone ran
  `git config core.hooksPath .githooks` — assume it is *not* active and don't
  rely on it to catch you.
- Creating or starting anything in the BlazeMeter account is a real write —
  confirm with the user first unless they already named the artifact to use.
