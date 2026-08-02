# Working on bzm-opl-gen

Generates BlazeMeter OPL (private-location) k8s/OpenShift manifests from a
customer's real account facts, and live-tests them. README.md is the user-facing
doc; this file is what a session needs before touching the code or the tests.

Every rule below cost something to learn. Where the reason is short it is here;
where it is long it is a comment at the site, and the site is named.

## Three test layers

**Offline — `.venv/bin/python -m pytest tests -q`.** Stdlib + fixtures, no
cluster, ~2s. Every live-rig check has an offline counterpart that fakes the
cluster/API response; add one whenever you add a live check.

The run must end **`N passed` with nothing skipped.** `tests/test_server.py`
skips its whole module without `fastapi`, so a venv missing the extra reports a
clean pass having tested none of the HTTP layer. Install
`.venv/bin/pip install -e ".[dev]"`; CI asserts the optional deps import rather
than trusting a green run.

**Helm parity — `python tests/helm_parity.py`.** Renders 28 option combinations
as both `--format manifests` and `--format helm` and requires the same objects
out of each. Deliberately *not* pytest: it shells out to `helm`, and a test that
skips when a binary is missing is the fastapi problem again. Its own CI job.
Every judgement in `templates/*.yaml` is restated in Go templates and nothing
else notices one drifting. Offline counterpart: `tests/test_helm.py` (the values
overlay, the refusals). Add to both when you touch either.

**Live rig — `bzm-opl-gen livetest`.** Deploys generated manifests to a local
cluster and waits for the agent to report online in a real account:

```
.venv/bin/python -m bzm_opl_gen livetest --api-key api-key.json \
    --namespace bzm-livetest --cluster minikube \
    --local-registry 5001 --local-proxy --contain-egress \
    --run-test <TEST_ID> --timeout 420
```

Runs take **12–20 minutes**. Start with `run_in_background: true` and poll with
`until grep -qE "LIVE TEST|Traceback"` — never assume a short wait is enough.
Python buffers stdout when redirected, so the log stays empty until exit;
`kubectl get pods` is the live view.

**Frontend — `cd frontend && npx vitest run && npx tsc --noEmit`.** Logic lives
in plain modules with tests (`capacity.ts`, `manualIds.ts`, `preflight.ts`,
`suggestions.ts`, `session.ts`, `text.ts`, `token.ts`, `optionGroups.ts`);
components wire them. `noUnusedLocals` is on, so a binding left behind by a
refactor fails the typecheck rather than accumulating.

## Account facts

Live runs need a real account. Nothing identifying one is recorded here —
gather it at the start of a session and keep it there:

```
bzm-opl-gen locations --api-key api-key.json --account-name "<ACCOUNT NAME>"
```

| what | where it comes from |
|---|---|
| account / workspace / project | `locations`, or the account owner |
| scratch private location + ship | create your own (`create-location`, `create-ship`) |
| smoke test for `--run-test` | an existing Taurus test that makes **real HTTP requests** |
| API key | `api-key.json` in the repo root (gitignored) |

`--run-test` only means something with a test whose samplers hit the network: a
dummy-sampler test reports hundreds of plausible samples while issuing no
requests, so engine validation passes without proving anything.

Create scratch locations rather than reusing colleagues' harbors. If the rig
repoints an existing customer test it must restore the original `executions` (it
does, in a `finally`, printing the original first). Verify after any live run:

```
python -c "from bzm_opl_gen import core; print(core.client_from_key('api-key.json').test(<id>).get('executions'))"
kubectl get ns | grep bzm-livetest ; docker ps -a | grep bzm-opl ; minikube status -p bzm-opl-test
```

## What the rig proves, and the trap behind each part

- **`--local-registry`** mirrors the location's images into a `registry:2` and
  blackholes public registries on the node, so a missing `IMAGE_OVERRIDES` key
  fails here instead of silently falling back to the public registry.
- **`--local-proxy`** runs mitmproxy **on the cluster's docker network**, never
  published to a host port. Publishing makes the rig silently wrong whenever
  something else owns that port (8080 is popular): the node reaches *that*
  service, gets its 403, and our log stays empty. A CONNECT probe requires the
  attempt to appear in our own log first.
- **Negative control** deploys CA-stripped and requires
  `CERTIFICATE_VERIFY_FAILED`. Keep it unless iterating
  (`--skip-negative-control`).
- **`--contain-egress`** needs calico — minikube's default CNI accepts
  NetworkPolicies and enforces nothing, so the profile is recreated if no
  enforcer is present. The API rule must name the ClusterIP *and* the endpoint:
  policy is evaluated after kube-proxy DNAT.
- **`--run-test`** spawns a real engine. Engines mount the CA as a *file*
  (`/var/cm/ca-bundle.crt`, subPath) where crane mounts the directory. Engine
  traffic cannot be told apart by pod IP — pods are SNAT'd to the node address
  first — so it is identified by `api.ENGINE_UPLOAD_HOSTS`, which only engines
  use.

Known and expected: **JMeter ignores `HTTP(S)_PROXY`** for sampler traffic (a
library convention, not a JVM one), so engine→SUT goes direct and fails under
`--contain-egress` while results still upload. Not a manifest bug — the proxy
goes in the test. Don't "fix" it in the generator.

### Testing a virtual service by hand

`livetest` covers performance locations only, so an SV backend is verified by
deploying a real virtual service and curling the endpoint BlazeMeter advertises.
Three things cost an afternoon each:

- **Only one ingress controller can hold the node's `:80`/`:443`.** ingress-nginx,
  Contour's envoy and istio's gateway all want hostPorts 80 and 443; on one-node
  minikube the second sits `Pending` with `didn't have free ports`. Scale the
  incumbent to 0. Istio's gateway must be installed under a release name
  producing the label `istio: ingressgateway` — crane hardcodes that selector,
  and a Gateway matching no pod fails exactly like a bad port.
- **Never delete a Service or Route crane created.** Crane keeps a *pool* and
  binds one to a mock by setting its selector at deploy time; deleting one it
  holds desynchronises the agent, and the next deploy gives a mock pod with no
  Service and a virtual service wedged in `CONFIGURING`. Stop the virtual
  service and let crane rebuild. The pool also survives a
  `KUBERNETES_SERVICE_USE_TYPE` change — old Services keep their old type, so
  `kubectl get svc` does not report what is configured.
- **`CONFIGURING` clears itself, eventually.** A deploy interrupted mid-flight
  leaves BlazeMeter refusing both `deploy` ("already running") and `stop` ("not
  running"). It drops to `FAILED` on its own after a few minutes; no API call
  forces it.

## Local environment

Run `bzm-opl-gen toolcheck --cluster minikube --local-registry 5001
--local-proxy` first — it reports the active docker daemon, free disk and
missing tools. The rest is what it cannot fix for you.

- **`.venv` is an editable install pointing at this checkout, so in a git
  worktree it silently tests the wrong code.** A `pytest` run from a worktree
  reusing that venv imports `bzm_opl_gen` from *here*: the suite passes, and
  none of the code under test is the code you changed (a deliberately-failing
  test passed in the full run and failed in isolation, which is how it was
  caught). Build a venv inside the worktree before trusting any figure from
  one, and re-run in the main checkout after merging — that run is
  authoritative.
- **Docker provider varies and it matters for disk.** A full disk makes
  minikube fail with `RSRC_DOCKER_STORAGE`, which never mentions disk. Which
  number binds depends on the provider: a preallocated VM disk (colima, Lima)
  is bounded by the VM's `df`; a sparse image (Docker Desktop) by host free
  space. `toolcheck` picks the right one; `docker context ls` says what you are
  on.
- **Pruning by age deletes images you still need**: `--filter until=168h`
  filters on image *build* date, and BlazeMeter images are old enough to be
  deleted the day you pull them.
- **arm64:** BlazeMeter images are amd64-only and run under emulation. Engines
  are slow; size them down (`--engine-cpu 1 --engine-mem 4Gi`) or they stay
  Pending. **Pin `mitmproxy:11.1.3`** — 12+ dies with SIGILL on arm64 VMs.
- `minikube -p bzm-opl-test` is disposable: the rig deletes and recreates that
  profile freely and touches only its own named containers.

## Architecture

- **Orchestration in `core.py`, transport in `server.py`.** `core` imports no
  fastapi, no pydantic, nothing about requests — `tests/test_core.py` asserts it
  by parsing the imports, because a web framework reachable from there puts the
  HTTP stack behind every other caller *and* behind that suite, which then
  skips. Failures are `core.CoreError` subclasses carrying `.status`;
  `server._answer` is the only thing turning one into an `HTTPException`. The
  server keeps what is genuinely its own: routes, request models, the zip's
  headers, where a pasted key lives for a browser session, the TTL cache, and
  how the process is bound. **Do not re-export core's vocabulary from `server`**
  — `FEATURES` was aliased there, an alias does not follow a monkeypatch, and a
  test patched one list while asserting against the other.

- **`plan.py` reaches nothing, and that is the requirement.** It sizes a load
  target (users → engines → nodes → machine size) for somebody with no cluster
  and often no account, because the answer is what they raise the request *with*.
  Any dependency added here puts the first step behind a later one.
  `tests/test_plan.py::test_plan_reaches_nothing` asserts it over the imports,
  because prose is what the fifth recurrence of a rule is made of. It shares
  rather than restates: the engine footprint and node overhead come from
  `generate`, and `plan.supported_vus()` is the 500-per-2CPU/8Gi ratio
  `doctor.check_threads_per_engine` judges against — the planner recommending
  what the preflight then WARNs about is the failure mode, and the pair are
  asserted to agree. **The users-per-engine figure is an assumption and every
  surface says so**: nothing here can measure it (it is a property of the
  script), and `vus_per_engine_assumed` carries the difference between a figure
  supplied and one defaulted. Warnings are plain prose — no backticks, no `--`
  — because they render as Markdown in the document and as text in the panel.

- **`slots` is engines per *agent*, not per location.** BlazeMeter's own UI
  calls it "Engines per agent", so a location's concurrency is `agents x slots`;
  real accounts lean on it (17 agents at slots=1; 2 agents at slots=10).
  `doctor` was always right — it measures one agent's share against one cluster.
  The planner was not, and told a two-agent location to set `slots` to the whole
  run. `plan.capacity_plan` takes `agents` and divides; nodes are reported per
  agent because an agent is a cluster.

- **The MCP server's audience has no checkout.** `mcp_server.py` is written for
  a session in a customer's directory with a cluster and an account and none of
  this repo, so its tool descriptions, `instructions` block and served docs are
  *all* the documentation there is — a thing that is only in a comment here does
  not exist to it. Three rules it keeps that `core` does not: **the AUTH_TOKEN
  is never in a response** (`reveal_token` is a whole action, so it cannot
  happen as a side effect); **a secret is never an argument** (a path may be);
  **nothing writes to a cluster** (the session runs `kubectl apply` in its own
  shell, where the person watching sees it). `docs/*.md` reach the wheel through
  the `bzm_opl_gen.docs` **package-dir mapping** — they stay at the repo root
  where they are edited and where their links resolve, and `docs_dir()` looks in
  both places. The release workflow asserts every page made it in.

- **A new option needs a row in `bzm_opl_gen/options.py`; the doc table is
  generated from it.** `DEFAULT_OPTIONS` remains the only source of the default
  *value*; the registry carries what the option is *for*, in two lengths —
  `summary` (≤20 words, capped because all of them land in every MCP session's
  context) and `doc` (the `docs/options.md` cell). Regenerate with
  `python -m bzm_opl_gen.options`; editing between the markers fails
  `tests/test_options.py`, as does adding a key to one side only.
  **`frontend/src/optionGroups.ts` is out of scope for it** — it holds
  `detect`/`enable`/`disable` *functions*, which a Python registry cannot carry.

- **The UI: three views, three steps, two option buckets.** `layout/NavDrawer`
  picks the view (Generate / Plan capacity / Account capacity) and holds the key
  at its foot; `layout/AccountMenu` is that key plus the account and workspace,
  because all three last the session while an agent is chosen per bundle, and
  three separate things read the account. `layout/StepFlow` shows one step at a
  time (controlled from App, because the download step sends you back to
  Configure); `layout/PreviewDrawer` pushes the manifests in from the right
  rather than covering the form. The steps are `steps/AgentPanel`,
  `steps/ConfigurePanel`, `steps/DownloadPanel` — **App keeps every piece of
  state and every effect and hands them down as typed props**, so `core`-style
  ownership holds here too. A group belongs to no feature (`SHARED_GROUPS`) or
  to one (`groupsOf`), and both are on screen at once — there is no
  `visibleGroups`/`setButHidden`/`hiddenBlockers` any more, and nothing that
  hands back what a view was hiding. Don't reintroduce one: a feature is a view
  over a location's options, never a scope on what gets generated.

  **Three writes to the account come from this page and nowhere else** —
  `POST /api/ships/token` (regenerate), `POST /api/locations/func-id` (enable a
  feature) and `POST /api/locations/settings` — and each says what it costs
  before it is pressed. The third re-reads the location afterwards and reports
  *that*, not the request: BlazeMeter's own POST accepts `threadsPerEngine` and
  does not store it, so a form echoing back what was typed would show a value
  the account never took. `core.LOCATION_SETTINGS` is a closed set for the same
  reason `add_func_id` exists — a general passthrough would let `funcIds` be
  replaced wholesale by a caller that meant to add one. Every route that writes
  carries `server._writes`, which drops the cache after it; a test asserts that
  over the app's own routes, because the one that had to remember had forgotten.

## "Could not read" and "there is nothing there" must never share a representation

The same bug six times, three of them in one session. `null` vs `[]` in the
evidence collector; the identical collapse latent in `gather_cluster()`, where a
denied `list nodes` produced the same FAIL as an empty cluster; `auth can-i` and
`api-resources` reporting failure as *no*, so a file collected with no
kubeconfig read as a locked-down cluster; `raw.namespace: null` becoming `{}`,
which had `check_admission` announce a namespace "does not exist yet" when it
had merely been refused. The fourth landed *inside* the change written to fix
the first two — which is the point: **the distinction survives only where it is
structural, never where it is remembered.**

Two named helpers carry it, and a new reader should go through one:

- `suggest._read(doc, path, kind=...)` — `path` is one of the dotted paths built
  from `evidence` at the top of the module, and the same string the suggestion
  cites. Absent, null and wrong-typed all give `None`; `kind=bool` coerces only a
  value that is *present*, so a refused probe never arrives as `false`. A path
  the *document* does not define raises instead: no file will ever carry it.
- `doctor._unread_section(cluster, key, name, detail)` — the unread branch for
  six checks.

**Neither is the only route, and do not read the pair as an invariant.**
`check_service_account` and `check_egress` branch on falsiness;
`suggest._normalised` reaches a section directly. Each is
argued at its site; the point is that a new reader should have to argue too.
`doctor.evaluate`/`run` take `evidence=` — the whole `Evidence`, not its parts
unpacked per call site. **A denied read is a WARN and exits 0; an empty result
can be a FAIL.** If a new field cannot express both, it is not ready to be read.
(`versions.serverVersion` is how the boolean sections tell the two apart.)

The fifth was on the account side: `facts.manual()` leaves `slots` and
`threadsPerEngine` `None` because there is no account to ask, and `gather()`
returns the same `None` for a real location that has them unset — the
403-at-every-start FAIL. The value genuinely cannot carry it, so
`doctor.check_location` reads `facts.from_manual_entry()` instead. The sixth was
the account rollup: `core.account_capacity` keeps `rated_vus: None` and counts
`unrated`, and the page rendered neither, so a location nobody had sized drew as
one with no capacity. Same rule, one layer up.

**The evidence document's section names are stated once**, in
`bzm_opl_gen/evidence.py`, and the collector, `doctor`, `suggest` and
`core.preflight` resolve against it. Same rule one level up: rename a section in
the collector alone and every reader treats it as one nobody could read, so the
report says "could not read nodes" about a section sitting in the file — a false
unread, indistinguishable from an honest one, and nothing fails. A shell script
cannot import a Python table, so `tests/test_cluster_evidence.py` parses the
script's emitting half (everything below its `# -- the document` marker, one key
per line at an indent that is its depth) and compares the keys it writes with
`evidence.DOCUMENT`. Rename a section in either place and that test names it;
the same table holds the three fixture files and the dotted paths
`docs/preflight.md` quotes. Add a section in both, or in neither.

Beside it, `tests/evidence_fixtures.py` is the one builder for the document —
there were two, with different defaults for the same schema, and `test_server`
imported both. It also names the three collected files: the all-null degraded
one and two **half-read** ones (`cluster-scoped-denied`, `namespace-denied`).
The half-read pair is the case the rule is about — a reader that had lost the
distinction entirely still looks right on an all-null file, because there is
nothing there to be right about.

Two structural applications of it added since: `server.Blank` is `_typed` as a
pydantic type, so an optional field cannot be declared without the blank-vs-
absent rule; and `plan.capacity_plan` returns `override_cpu: None` where an
engine is not a whole number of cores, which is what the field cannot express —
it used to emit a formatted `"500m"` that the UI caught with a regex.

## Generator details that bite

- **`livetest` deploys the directory, and only sometimes re-renders it.**
  `generate` writes `out/profile.json` (resolved options minus `auth_token`), and
  the rig re-renders from it only where it has something to inject: the proxy's
  CA (`--local-proxy`) or the engine sizing (`--run-test`). A lean run has no
  regenerate callback at all and applies `<dir>/*.yaml` exactly as it sits —
  which this file used to describe as "manifests under test stay generator
  output". They do not, and #107 is what that cost: `--manifests` defaults to
  `out/`, `out/` is whatever the last `generate` left there, and a run given
  `--ship-id` and `--auth-token` deployed a nine-day-old bundle for a *different*
  agent, plus a `bzm_limitrange.yaml` from a version that no longer emits one.
  Re-rendering everywhere would not have caught it either — `_regenerator` merges
  onto the profile and prefers *its* `ship_id` over the command line, so the
  stale identity survives a re-render — and on the lean path it would have to
  either mint a token (revoking the one the bundle is running on, which is why
  the mint sits inside the re-render branch) or silently rewrite a directory the
  operator built deliberately. So the identity is **checked**, not re-rendered:
  `livetest.bundle_check` refuses a `HARBOR_ID`/`SHIP_ID` (in the ConfigMap or in
  `profile.json`) that is not the one the run was told to test, naming both
  values, and refuses any `*.yaml` outside `emitted_yaml_files()`. It runs in
  `cmd_livetest` before the token mint and again at the top of `livetest.run`
  before the try block — outside it, because that `finally` tears down a cluster.
  Unreadable is a note, not a refusal.
- **The chart is copied, never re-rendered.** `--format helm` walks
  `templates/helm/` and emits it verbatim, so anything added there ships in every
  bundle — including files `package-data` would drop. Its globs do not recurse
  and `*` does not match a leading dot, which is why `.helmignore` and each
  directory are named explicitly in `pyproject.toml`; the release workflow
  asserts the wheel carries them, because a missing chart file fails at generate
  time on an installed copy and never in a checkout.
- `--format helm` refuses a service-virtualization location, `livetest` refuses
  a chart directory, a profile with `service_account_create: false`, a
  placeholder `AUTH_TOKEN` it will not re-render over, and a bundle whose
  identity is not the agent under test. Guards over silent failures: a chart
  without the ingress stalls at `WAITING_FOR_DOMAIN`, the rig's `*.yaml` glob
  comes back empty, and a namespace the rig was told already exists never gets
  created, so every object applies, no pod is created, and the run waits out its
  timeout. Each of these is 12–20 minutes and a deleted cluster otherwise.
- CA bundles exceed the 256KB cap on kubectl's last-applied-configuration
  annotation — manifests over 200KB apply `--server-side`.
- A taurus-script test keeps its locations in the uploaded YAML;
  `PATCH /tests/{id}` silently drops `executions` for one.
- **This generator emits no LimitRange, and shouldn't.** It did, opt-in, and was
  removed after a live install showed both halves of why. It cannot change
  engine requests: crane sets them explicitly from the location's
  `overrideCPU`/`overrideMemory` (250m/256Mi when it says nothing — **not** a
  fixed value; a live run showed a location at 1/4096 producing requests
  {1, 4Gi} against limits {2, 8Gi}), and a LimitRange's `defaultRequest` only
  fills fields a pod leaves unset. What it *did* reach was crane's `test-job-*`
  pods, which declare nothing and so got a full engine's worth of CPU and memory
  for jobs needing neither, reserving capacity a real engine then couldn't get.
  Issue #2 was filed assuming it helped — don't re-add it. `livetest
  --run-test` prints the live gap as `ENGINE SIZING:`.
- `doctor` still *reads* a LimitRange the customer already has: an existing
  `max` below the engine size, or below crane's own 1 CPU / 2Gi, rejects the
  respective pod at admission.
- `doctor` measures capacity against node **allocatable**, deliberately: what is
  actually free needs every pod's requests summed per node, a much bigger read
  for a preflight. Say "upper bound" in any detail string you add.
- **List calls ask for one big page.** `/workspaces` asked for 100, SE Demo has
  166, and the missing 66 held 40% of the account's rated VUs — attributed on
  screen to no workspace at all. A truncated list only looks short.

## Facts and images

- **`facts.manual()` is the same shape `gather()` returns, on purpose.** Manual
  mode builds facts from a typed harbor id, ship id and token so a bundle can be
  produced for an account nobody here can reach. Nothing that *generates* learns
  which way the facts arrived — keep it that way, and add to `FALLBACK_IMAGES`
  rather than special-casing the manual path. The one consumer that legitimately
  asks is `doctor`, and it asks the marker the facts carry
  (`from_manual_entry()`).
- **A Kubernetes agent reports images as bare keys** (`taurus-cloud:latest`,
  `torero:4.6.182`) with no registry and `Size: 0` — crane's configured image
  set rather than what is on the node. Docker agents report registry-qualified
  tags. `gather()` handled only the Docker shape, so every k8s agent — the kind
  this tool generates for — silently produced no inventory and fell through to
  the catalogue; that is how `torero` and `richrach` stayed missing from a
  performance bundle. `repo_for_key()` resolves the bare form, and reading it
  properly also pins exact tags where the catalogue could only say `latest`.
  **A key may carry a path, and all of it is repo**: browser images arrive as
  `blazemeter/charmander/chrome_136.0.7103.113`, where only `blazemeter/` is
  redundant. Keeping the last segment resolved them to a repo that 404s *and*
  dropped `charmander`, which is the substring `image_category()` reads, so a
  performance-only location selected four browsers (#70).
- **`FALLBACK_IMAGES` was read off live inventories, not derived from the keys.**
  Keys do not reliably match their repo (`taurus-cloud`→`v4`,
  `apm-image`→`apm`, `blazemeter`→`v3`), so a "tidy-up" that regularises them
  produces repos that do not exist. `test_manual_facts.py` asserts the catalogue
  covers every category `CATEGORY_BY_FUNC` can ask for, so a new funcId fails
  there rather than on a sealed cluster.
- GUI browser images are the one gap and cannot be closed: the account carries
  60+ version-pinned `charmander/*` repos and only a live agent says which one a
  location uses. `facts.gui_images_incomplete()` flags it; don't invent a
  default.

## Conventions

- Comments explain *why*, especially where a non-obvious environment fact drove
  the code. Match the existing density; don't narrate the obvious.
- **Button labels are concise — one word wherever one will do.** `Calculate`,
  `Apply`, `Save`, `Download`, `Copy`, `Use`. What a control costs or reaches
  goes in the sentence beside it, which is where this page puts every other
  warning — a label carrying the explanation makes the button wide, the row
  ragged, and the sentence redundant.
- Never push to `main`. Commit on a branch, push that, open a PR. `.githooks/`
  holds a pre-push guard, but it applies only where someone ran
  `git config core.hooksPath .githooks` — assume it is *not* active.
- Creating or starting anything in the BlazeMeter account is a real write —
  confirm with the user first unless they already named the artifact to use.
