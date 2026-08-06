# Working on bzm-opl-gen

Generates BlazeMeter OPL (private-location) k8s/OpenShift manifests from a
customer's real account facts, and live-tests them. README.md is the user-facing
doc; this file is what a session needs before touching the code or the tests.

Every rule below cost something to learn. Where the reason is short it is here;
where it is long it is a comment at the site, and the site is named.

`CONTEXT.md` is the glossary, and it settles which word wins where two were
doing one job: **functionality** (never feature) for what a private location is
enabled to do, **agent** (never ship, outside `ship_id`), **profile** for a JSON
file of options and **sizing** for a statement of the capacity a run needs.
Read it before naming anything new.

## Four test layers

**Offline — `.venv/bin/python -m pytest tests -q`.** Stdlib + fixtures, no
cluster, ~3s. Every live-rig check has an offline counterpart that fakes the
cluster/API response; add one whenever you add a live check.

The run must end **`N passed` with nothing skipped.** `tests/test_server.py`
skips its whole module without `fastapi`, so a venv missing the extra reports a
clean pass having tested none of the HTTP layer. Install
`.venv/bin/pip install -e ".[dev]"`; CI asserts the optional deps import rather
than trusting a green run.

**Helm parity — `python tests/helm_parity.py`.** Renders 29 option combinations
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
in plain modules, each with its own `.test.ts` — `api`, `attempt`, `capacity`,
`engineSize`, `env`, `foldSet`, `formats`, `heartbeat`, `manualIds`, `openRow`,
`optionGroups`, `sched`, `session`, `sv`, `text`, `token` — and components wire
them. Two suites do render (`App`, `CapacityView`), for the flows only an effect
reaches. `noUnusedLocals` is on, so a binding left behind by a refactor fails
the typecheck rather than accumulating.

## Account facts

Live runs need a real account. Nothing identifying one is recorded here —
gather it at the start of a session and keep it there:

```
bzm-opl-gen locations --api-key api-key.json --account-name "<ACCOUNT NAME>"
```

| what | where it comes from |
|---|---|
| account / workspace / project | `locations`, or the account owner |
| scratch private location + agent | create your own (`create-location`, `create-agent`) |
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
  — `FUNCTIONALITIES` was aliased there, an alias does not follow a
  monkeypatch, and a test patched one list while asserting against the other.

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

- **Three sizing models, one pod size, and one of the three has no figure**
  (#154). `plan.SIZING_MODELS` holds what each covered functionality is *asked
  for* in — virtual users, browser instances, requests per second — and what one
  pod of the chosen size carries. One pod size because crane applies a single
  limits pair to every pod it creates, so where several are sized the largest
  decides and `driven_by` names it; largest and **not** the sum, which the plan
  warns about. **`baseline: None` is the load-bearing entry.** Requests per
  second per core has not been measured, so a service-virtualization sizing
  carries a target, `pods: None` and `per_pod_source: "unmeasured"` — a *third*
  value beside supplied and assumed, and the same rule as everything else here:
  a figure nobody has and a figure this tool chose must not share a
  representation. It drives nothing, and there is no flag or field to supply
  one — a mock-pod count would size a pool that every number after it calls
  engines. Sized alone it is a `ValueError` carrying `_unmeasured_note`, which is
  the one wording of that sentence and is shown **once per surface** (the
  document drops it from Worth knowing, having already made it an assumption).
  Don't fill the None in, and don't reuse the performance ratio for it. The card
  renders the models from `/api/sizing-models` rather than from three field
  groups in TypeScript — same rule as `IGNORED_BY_FORMAT`, same single
  `fixtures.ts` copy held equal by `test_server.py`.

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
  shell, where the person watching sees it) — the one exception is
  `opl_agent livetest`, which deploys because deploying is all it is, and is off
  unless `BZM_OPL_ENABLE_LIVETEST` says otherwise. `docs/*.md` reach the wheel through
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

- **The funcId vocabulary is the account's, and the hardcoded three are the
  *keyless* answer rather than a fallback.** `core.func_ids()` reads
  `GET /accounts/{id}/functionalities` (#148), which carries BlazeMeter's own
  display names and settles what a hand-written table got wrong in both
  directions: it never listed the five real locations carry (tdm,
  dataPublisher, delphix, secretsPrivateVault, enableSecretsToggle) and it
  offered `functionalApi`, which the account retired — so dropping that from
  what a location can be *created* with needs no rule naming it, while reading
  it off a location that has one is untouched. `account_id` is optional because
  the page fetches this on mount with no key and manual entry never has an
  account, and the answer with none is `core.covered_func_ids()`: the three this
  tool configures, under the names the account would give them, so nothing on
  screen changes wording when the real list lands. **An account that refuses the
  read raises** — answering "this account offers exactly the three we cover"
  to a 401 is could-not-read wearing there-is-nothing-else, one layer up from
  the
  evidence rule below. Every row carries `covered`, because a page listing
  `delphix` beside `performance` with nothing to tell them apart is offering to
  configure something no bundle can; an uncovered funcId is *named*, never
  dropped, since silence there reads as coverage.

- **A functionality *is* a funcId — one entry, `id` equal to it** (#149). It
  was two entries and `performance` claimed four (`performance`, `functionalApi`,
  `functionalGui`, `proxyRecorder`), so its label had to name all of them:
  "Performance & functional testing", printed over a location whose only funcId
  is `performance`. Everything joins by equality now — a location's funcIds to
  the cards, `OptionGroup.functionalities` to the served ids, manual entry's
  declaration to the funcId its facts are gathered for — and a
  per-functionality list of funcIds would be exactly the translation table the
  1:1 mapping exists
  instead of. `covered_func_ids()` is that same list read as a vocabulary rather
  than a second table beside it, so `covered` on a funcId row and having a card
  are one fact. The two funcIds that lost their card are not lost:
  `functionalApi` (retired) and `proxyRecorder` (no options here) are
  *unclaimed*, named on the configure step like `tdm` — and a location carrying
  only those claims nothing, which is read one level up as nobody having
  answered.

- **The engine limits belong to no functionality, and are never cleared for
  one.** BlazeMeter defines `KUBERNETES_RESOURCES_LIMITS_CPU`/`_MEMORY` as the
  limits for *resources created by agent* — one pair, reaching engines,
  browser pods and mock-service pods alike, with no `KUBERNETES_MOCK_RESOURCES_*`
  beside
  it. `notRunPatch` used to clear them for a location that ran no performance,
  which left an SV-only or GUI-only agent's pods on crane's 250m/256Mi defaults:
  the LimitRange note's silent failure, reached from the page. What survives is
  `ENGINE_FUNCTIONALITIES`, and it decides **placement of the statement only**.
  Read off single-functionality locations' `/versions`: performance carries
  apm/crane/v4, functionalGui adds doduo and a pinned browser to the same three,
  and **an SV-only agent carries no taurus engine at all** (crane,
  group-gateway, service-mock). Its limits are still emitted and still reach its
  mock pods; what they *mean* there is a sizing model that does not exist yet
  (#154), so that card states nothing rather than an engine size that is not
  there. Don't reintroduce a clearing rule for them.

- **A required field left blank is a `<PLACEHOLDER>`, not an empty string and
  not a refusal.** Every one of these had a plausible-looking failure when
  empty: an unnamed service account becomes the namespace's `default` (binding
  crane's Role to every pod there), an empty AUTH_TOKEN is a pod that reads as a
  slow boot, a blank `sv_subdomain` stalls at `WAITING_FOR_DOMAIN`. The marker
  makes all of them one loud, early failure. **The angle brackets are the
  guard**: no Kubernetes name may contain them, so `kubectl apply` rejects the
  object and names the field — which is why a blank field may resolve to a value
  at all. The chart's `bzm-opl.validate` refuses one for the values the API
  server never sees as names, and `livetest.bundle_check` refuses one before it
  builds a cluster. Two halves decide what is required, and they are not the
  same question: `generate.REQUIRED_TEXT` fills what the *options* show is
  needed, and `optionGroups`' per-group `requires` fills what only a **switch on
  the page** shows — a registry, a proxy and a CA are configured by having a
  value, so blank and "not using one" are the same options dict on the server.
  `placeholder_options()` reads the marker rather than either table, so both
  halves report identically. Two exemptions, both "answered elsewhere" rather
  than "unanswered": docker has no namespace or ServiceAccount, and a chart
  leaves `authToken` empty because `--set-string` at install time is what its
  README asks for. The page **warns, never blocks** — `configureBlockedBy` kept
  only what a marker cannot stand in for (a question nobody answered, two
  answers that contradict, an env name no process could read), and the marker
  never enters `options`, only `withPlaceholders(options)` on the way out, or it
  lands in the session snapshot looking like something somebody typed.

- **`extra_env` is the escape hatch, and the reserved set is what keeps it
  honest.** BlazeMeter's agent-environment reference is much wider than the
  options here, and the only way to the rest was hand-editing the generated
  ConfigMap — which the next `generate` reverts without saying so. All three
  formats carry it (ConfigMap entries, `extraEnv` in the overlay, `--env`
  flags), and it reaches the **agent**: crane reads it, and the engines crane
  spawns do not, because crane builds their environment from the `KUBERNETES_*`
  variables rather than passing its own down. Every name the generator writes is
  **refused** rather than merged — two values for one key is a ConfigMap with a
  duplicate entry, and whichever wins is not the one the form showed — and the
  refusal names the owning option, because "set it there" is the whole answer.
  `RESERVED_ENV` is the union across formats, so a `KUBERNETES_*` variable is
  refused in a docker bundle too: it reaches nothing there either, and accepting
  it would read as a setting that had been made. The set is *emitted from real
  bundles* by `tests/test_generate.py::test_reserved_env_is_what_the_bundles_actually_write`
  rather than restated, so a variable added to a template fails there instead of
  becoming quietly overridable. It is served (`/api/reserved-env`) with the
  owner per name, and the page never keeps its own copy — same rule as
  `IGNORED_BY_FORMAT`, with the same `fixtures.ts` single copy held equal by
  `test_server.py`.

- **...and the *offered* set is the same table read the other way.**
  `agent_env.AGENT_ENV` is BlazeMeter's documented reference transcribed whole —
  the identity, the proxy trio, the engine limits included — and
  `core.agent_env()` subtracts `RESERVED_ENV` at the point it is **served**
  (`/api/agent-env`). So the env area offers exactly what is left over, and an
  option removed later hands its variable back with no edit on either side;
  declaring only the leftovers would be that table kept twice. The area is a
  **list**, not a name box: it was a switch over two empty inputs, which asked
  somebody to supply the vocabulary as well as the value, so it is no longer a
  group at all — no switch (a list has nothing to be off), a fold beside
  Advanced (`ConfigurePanel.FoldRow`), and its one blocking rule reads from
  `configureBlockedBy` instead of a group's `incomplete`. `type` picks the
  control and a **boolean gets three positions**, because "the agent's default"
  is a third answer a switch cannot give and the defaults run both ways
  (`VERIFY_SSL` on, `KUBERNETES_USE_PRE_PULLING` off). **`platforms` and
  `functionalities` are two different questions** and a performance location was
  being offered the answer to both (#150): the first says which agent reads the
  variable, the second says whether this location runs the thing that reads it —
  the Grid proxy's port on a location with no grid. The second is filtered
  server-side too, `core.agent_env(func_ids)`, so the CLI and the MCP server get
  one answer; three states, and absent is not empty (the route takes them
  comma-separated for exactly that reason). Nothing is ever hidden by any of it:
  a variable with no row above it — the other platform's, another
  functionality's, one the vocabulary has since lost, a JSON value no table can
  round-trip — keeps the name/value editor underneath (`env.otherRows`), because
  a form showing nothing for a variable the bundle carries is the failure this
  area's rules are about. `fixtures.ts` holds a **sample** rather than a copy
  here, and says so: nothing on the page has to agree with the catalogue.

  **A remainder says nothing about what was taken out of it**, and that was the
  other half of the report: `AUTO_KUBERNETES_UPDATE` read as missing when it is
  `auto_update`'s, a tri-state inside a group titled Security & RBAC. The
  refusal already named the option — but only to somebody who had typed the name
  into the editor, which is the one thing a person who thinks it is missing will
  not do. So the area states the whole reserved table beside the offered one
  (`EnvVars.SetByTheBundle` over `optionGroups.reservedList`), with the owning
  option and the *section* holding it, read off each group's own `keys`. A
  rendered list rather than a search box: the browser's find is the search, and
  it only works on what is on the page.

- **The UI: two views, three steps, two option buckets.** `layout/NavDrawer`
  picks the view (Generate / Account capacity) and holds the key at its foot;
  `layout/AccountMenu` is that key plus the account and workspace, because all
  three last the session while an agent is chosen per bundle. `layout/StepFlow`
  shows one step at a time, controlled from App because the download step sends
  you back to Configure; `layout/PreviewDrawer` pushes the manifests in from the
  right rather than covering the form. **The shell is `h-screen` and the pane
  beside the drawer is what scrolls** — it was `min-h-screen`, so the *document*
  grew to whatever the view rendered and that `overflow-y-auto` never had a
  bounded parent: on a real account (166 workspaces) Account capacity is
  11,000px tall, the drawer stretched to match, and the account menu at its foot
  sat that far below the fold — unreachable on the one view whose whole subject
  is the account. Generate never showed it because `StepFlow` pins itself to
  `100vh - 6.75rem` and scrolls its own step. No `overflow-hidden` beside the
  height: the account menu is absolutely positioned inside the drawer and has to
  be able to leave it. The steps are `steps/AgentPanel`,
  `steps/ConfigurePanel`, `steps/DownloadPanel` — **App owns every piece of
  domain state and every effect that reaches the server**, handed down as typed
  props, so `core`-style ownership holds here too; a panel keeps only what is
  local to its own view (folds, busy flags, copied). A group belongs to no
  functionality (`SHARED_GROUPS`) or to one (`groupsOf`), and both are on screen
  at once — there is no `visibleGroups`/`setButHidden`/`hiddenBlockers` any more,
  and nothing that hands back what a view was hiding. Don't reintroduce one: a
  functionality is a view over a location's options, never a scope on what gets
  generated.

  **The format is step 2's first control, and the form follows it.** A
  functionality hides nothing; a *format* genuinely does, and the two must not
  be confused. It used to be chosen on the download step, one step too late —
  Configure asked
  for a namespace, a ServiceAccount, node selectors and engine limits, none of
  which a docker bundle carries. What is on screen now derives from
  `formats.optionApplies` over the generator's own `IGNORED_BY_FORMAT`,
  **served** as `/api/ignored-options` and keyed by format (#176). Never
  restate that table in TypeScript, or a key
  added to the generator goes on being offered for a format that drops it; the
  one copy that has to exist is `fixtures.ts`, for tests that run without a
  server, held equal by a Python test. `optionGroups.groupsFor` drops a group
  whose every declared key is ignored; one with some keeps its row and hides the
  rest inside its body, so a group body takes the `Applies` predicate rather
  than a format string, and a section that is not a group (placement, Advanced)
  uses `keysApply` over the keys it owns. **There are two empties here and they
  are different facts**: a format with no entry is one nothing has been read for
  — the fetch has not landed, or failed — while an entry that is `{}` has been
  read and drops nothing. `formats.ignoredFor` is the one reader and returns
  `null` for the first, so the distinction is structural rather than
  remembered; indexing the record would hand back `undefined` typed as an
  object and lose which it was. Both show every field, because a field too many
  beats hiding a required one on a guess. Hiding is never a refusal — the value is kept, sent and named in the
  bundle's README, which is `generate.ignored_options()` on that side. Where a
  hidden field needs explaining, render the served reason (`whyIgnored`) rather
  than writing a second copy of the generator's sentence.

  **The page asks nothing about the target cluster, and that is a decision**
  (#144). It asked three ways at once: Test deploy handed over a crane-hook
  manifest, an evidence file was imported and judged, and a switch shipped
  crane-hook inside the bundle. All three are gone, with `preflight.ts`,
  `suggestions.ts`, `SuggestionList`, `session.SavedPreflight` and
  `/api/preflight`. Two reasons, and the second is the load-bearing one:
  crane-hook is BlazeMeter's image rather than ours — a live run on kind proved
  our own rendering of it could never pass, because `ROLE_NAME` named the hook's
  read-only Role where `pkg/rbac.go` checks the *agent's*, and `role.yaml` lacks
  the `createcollection` verb upstream's carries — and the imported preflight was
  a page of machinery for a customer nobody had. `bzm-opl-gen doctor` and
  `scripts/bzm-cluster-evidence.sh` are untouched and are where the question is
  answered; whoever can collect the file has a shell already. `core.preflight`
  stays for the MCP server, tested in `test_core`/`test_cli`/`test_mcp`. The
  `crane_hook` **generate option** also stays — a profile that sets it still
  renders — it is only off the page. Don't put a cluster check back on this page
  without an image we publish: that was the grilling, and the answer was no.

  **The planner is step 1's first card, not a view of its own.**
  `steps/Sizing` states the sizing and one Edit expands it downward;
  picking a location opens what that sizing would change about it, before →
  after against what the account holds. A **sizing**, never a profile: a
  profile here is a JSON file of generator options, and CONTEXT.md keeps the
  two apart. The fold is legitimate only because step
  1 needs no account -- sizing a cluster for somebody who has none is why the
  planner exists, and `App.test.tsx` drives the page with no key, which is what
  keeps it true. The sizing *fills* the location draft and the fields stay
  editable; a hand edit outranks later sizing changes until Reset. It has no
  `agents` field on purpose (see `usePlan.ts`): on Kubernetes an agent is a
  cluster, so you scale `slots` and let the node pool autoscale -- 78% of the
  locations in one real account have exactly one agent, and the largest is one
  agent at 50 slots. Where the count is a *fact* -- a
  location that exists -- it is read off that location.

  **Four routes write to the account, and each says what it costs before it is
  pressed.** Two *create* — `POST /api/locations`, `POST /api/ships` — and two
  change what the account already holds: `POST /api/ships/token` (regenerate)
  and `POST /api/locations/settings`. The last re-reads the location afterwards
  and reports *that*, not the request: BlazeMeter's own POST accepts
  `threadsPerEngine` and does not store it, so a form echoing back what was
  typed would show a value the account never took. Verified live against a real
  location: the panel reported `1 → 4, 500 → 400, not set → 2, not set → 8192`
  and the account held exactly that. `core.LOCATION_SETTINGS` is a closed set
  because BlazeMeter's PATCH replaces `funcIds` wholesale, so a general
  passthrough would drop every functionality a caller did not name. Every route
  that writes carries `server._writes`, which drops the cache after it; a test
  asserts that over the app's own routes, because the one that had to remember
  had forgotten.

  There was a fifth — `POST /api/locations/func-id`, behind "Enable on this
  location…" on the configure step — and #113 removed it, along with
  `core.add_func_id` and `api.update_private_location`'s `func_ids`. The others
  change an agent's credential and a location's concurrency, or make one;
  turning a funcId on changes what the location *is*, which is BlazeMeter's own
  UI's.

  **A functionality is judged twice, and the two are different questions.** The
  location decides whether it is *run*; the format decides whether this bundle
  can *serve* it, and a card can be silent for either reason without them being
  the same reason. `sv.functionalityBlocked` is the second, keyed by
  functionality id, and it says so in its own sentence — "not possible in this
  bundle" and "not enabled on this location" are separate answers and the card
  gives only the true one. Don't generalise it into a served "which
  functionalities does a format refuse" table: helm and docker refuse *one* and
  nothing else refuses any, and `IGNORED_BY_FORMAT` keeps helm's entry empty
  precisely because helm does not *ignore* service virtualization — it refuses
  it. Ignoring and refusing are different answers, and a format that refuses
  says so in `generate()` rather than in that table. **A format's refusal clears no
  options**; the *format*
  gives way (`correction` inside `sv.ts`, surfaced as `Sv.patch`), because a
  configuration somebody wrote outranks a segment, and only `notRunPatch` — the
  location's answer — wipes anything. That correction is the one write on this
  page overriding a choice made on it rather than completing one, so it is never
  silent: App records what it replaced (`formatNotice`) and the panel says so
  until a format is picked.

  **`blockedFormats` follows what is configured, never what is demanded** (#115).
  `generate()` refuses a helm or docker bundle on `_sv_cfg` returning a config,
  and never reads the funcIds first. Read off the demand instead, the gap was a
  location whose funcIds carry no served functionality (real accounts have them:
  tdm, dataPublisher, delphix) — `enabledFunctionalities` answers null,
  `runsFunctionality` reads null as yes, every switch is offered, `notRunPatch`
  clears nothing, and a full
  SV configuration generated as docker and was refused by the server with the
  segment still enabled. `svState` therefore takes `runs` as a fourth input:
  options on their way out must not take a format with them, or a docker choice
  valid all along is lost on the way past. The formats the page blocks are held
  to the ones `generate()` actually raises on, by `test_server.py`, because the
  two refusals are far apart and easy to grow a third of.

  **A functionality the location does not run is not on the configure step at
  all.** It was *stated* for a while (#113) — a card naming the funcId to add —
  which is a true sentence about the location and nothing that step's reader can
  act on, and on a performance location (most of them) it was half the section.
  So `ConfigurePanel` filters `p.functionalities` by
  `optionGroups.runsFunctionality` and the card, its groups and its rail entry
  go together. **Manual entry is exempt, and structurally**: there the card *is*
  the declaration (see below), so filtering by the answer would take away the
  control that gives it — which is why `FunctionalityCard`'s not-run branch has
  one sentence and not two. Hiding is still
  only half — `notRunPatch` clears the options too, through each group's own
  `disable`, because `generate()` refuses an `sv_ingress` with no subdomain
  whatever the location runs and a hidden row would just move the blocker to the
  server. The three states stay three (`enabledFunctionalities`): runs, does not
  run, and nobody has said, which is null and shows everything.

  **In manual entry the functionality is not a view at all — it is the
  declaration**
  (#118). It names the funcIds the typed identity is gathered for, which name
  the images the bundle carries, and it was the one bundle-deciding input a
  refresh did not restore: an SV identity came back a performance one,
  `notRunPatch` wiped its `sv_*` options on the way, and the namespace
  suggestion rewrote a name generated into every manifest. So it is in the
  snapshot (`session.declaredFunctionalities`, empty in connect mode
  structurally rather than by convention), and **checked** against the served
  vocabulary rather than trusted: a functionality no longer offered names no
  funcId, so it is dropped and, if nothing survives, the page lands where a
  fresh manual session lands rather than
  gathering facts for nothing with no box ticked to say so. Manual mode never
  reads a functionality back off
  `facts.func_ids` for the same reason — those funcIds *are* the declaration, so
  reading them can only restate it, or lose it while `/api/functionalities` is
  still outstanding. Since #149 the declaration *is* the funcId, so that one
  list is the only thing it waits on: it used to be turned into a funcId through
  `/api/func-ids`' `changes_images` as well, and an identity gathered for
  nothing while the *second* vocabulary was outstanding is a wait that reads as
  an answer.

  **It is a list, and dropping one member must not drop the others** (#151). One
  id was tenable while `performance` claimed four funcIds; after #149, 71 of the
  168 locations in one real account run `performance` and `functionalGui`
  together, so a single value described a location nobody would create. The
  control is a checkbox per covered functionality (`toggleDeclared`, which keeps
  the list in served order — these ids reach `manualFacts`, and a list that
  reshuffles on a tick is a new request for a declaration that did not change).
  Several ticked suggest **one** namespace, and the rule is `startFunctionality`
  — the first in served order, the same tie-break a connected location carrying
  both funcIds already uses, so a manual identity lands where the equivalent
  connected one does. Emptying the list is allowed and *warned* rather than
  refused: a checkbox that will not untick is the off-screen blocker in one
  control.

  **Service virtualization is exclusive, and only where a location is being
  decided.** `sv.exclusiveWith` — manual entry and the create-location form —
  clears the engine functionalities when SV is ticked and the other way round,
  because crane applies **one** `KUBERNETES_RESOURCES_LIMITS_CPU`/`_MEMORY`
  pair to every pod it creates and an SV agent carries no taurus engine at all
  (read off single-functionality locations' `/versions`: crane, group-gateway,
  service-mock, no `v4`). Connect mode gets `sv.SV_MIXED`, a sentence and never
  a refusal — `POST /api/locations/func-id` went in #113 because what a location
  *is* belongs in BlazeMeter's own UI, so refusing to generate for a mixed one
  would refuse the only bundle it can have. Derived from `ENGINE_FUNCTIONALITIES`
  rather than a second list; a funcId neither side names (tdm, dataPublisher)
  excludes nothing, because nothing here knows what those cost.

  **`sv.required` conjoins `runs`, and that is what stops an effect loop.** It
  used to be `location && !declined` on the reading that a demand implies the
  bundle carries the functionality — true connected, where both come from
  `facts.func_ids`, and false in manual entry, where `runs` is the *declaration*
  and the funcIds are the facts fetched for the previous one, a debounce behind.
  In that gap `notRunPatch` cleared `sv_ingress` and `sv.correction` re-seeded
  it from the stale demand, forever: unticking Service virtualization hung the
  page. Two writers, one question, two sources.

  **An AUTH_TOKEN this app minted survives a refresh; one that was typed does
  not** (#123). It is seen at exactly two moments, both this page's own writes —
  creating an agent, and Regenerate — and no API reads a token back, so the
  browser held the only copy and a reload lost it for good. The backup is
  `server._minted_tokens`, keyed by ship id: **transport rather than core**,
  because it exists only where a *browser* forgets, while the CLI and the MCP
  server mint and write the bundle in one process. It is named at `_state` with
  the client and the cache, which together are the single-user seam.
  `session.strip()` is untouched, which is also why a *pasted* token **evicts**
  the remembered one (`DELETE /api/ships/minted-token`) rather than out-ranking
  it: the page cannot keep what was typed, so a copy left in the store comes
  back on the next load and silently replaces it. Keying by ship makes mixing
  two up impossible by construction, rather than by every caller remembering to
  let go. **The lookup's answers are three and stay three**: a token; `null` for
  "this process holds none", which is honestly what a restart says too and so is
  never worded as "nothing was ever minted"; and a request that failed, which is
  not a body at all. `token.Recall` carries that, and only `none` may say a
  credential cannot be read back — `unread` saying it would be "could not read"
  wearing "there is nothing there", about exactly the agents this app created.

- **`profile.json` is the bundle, and only the bundle.** It carries every
  resolved *option* — 35 of the 36, all but the token — plus `ship_id`, which is
  not an option and is the one identity it records, so `generate --profile`
  replays a bundle exactly and `livetest` judges one. Three things are
  deliberately not in it, and each absence is load-bearing:
  **the AUTH_TOKEN** (`SECRET_OPTIONS`), because a profile is the file people
  commit, diff and paste into tickets, and the bundle beside it is where a
  regenerate reads the token back from;
  **`harbor_id`**, which comes from facts rather than options — which is why
  `livetest.bundle_check` reads HARBOR_ID out of the ConfigMap rather than from
  here;
  **the four location settings**, because they are BlazeMeter-side and no
  regenerate or redeploy applies them. A file that recorded them would invite
  exactly that belief.
  So a bundle does not carry the location configuration it was sized for. The
  request document (`capacity-request.md`) states those four, and the settings
  panel writes them; if that split ever stops being enough, record them under
  their own key rather than beside the options, so nothing reads them as
  something a regenerate applies.

## "Could not read" and "there is nothing there" must never share a representation

The same bug six times, three of them in one session: `null` vs `[]` in the
evidence collector; a denied `list nodes` giving `gather_cluster()` the same
FAIL as an empty cluster; `auth can-i` and `api-resources` reporting failure as
*no*, so a file collected with no kubeconfig read as a locked-down cluster; and
`raw.namespace: null` becoming `{}`, which had `check_admission` announce a
namespace "does not exist yet" when it had merely been refused. The fourth
landed *inside* the change written to fix the first two — which is the point:
**the distinction survives only where it is structural, never where it is
remembered.**

Two named helpers carry it, and a new reader should go through one:

- `suggest._read(doc, path, kind)` — `path` is one of the dotted paths built
  from `evidence` at the top of the module, and the same string the suggestion
  cites. Absent, null and wrong-typed all give `None`; `kind=bool` coerces only a
  value that is *present*, so a refused probe never arrives as `false`. A path
  the *document* does not define raises instead: no file will ever carry it.
- `doctor.reads(key, name, unread)` — the decorator a check wears to declare
  the section it reads. Ten checks carry one and nine of those give it an unread
  branch, reached through `_unread_section`, which the declaration is now the
  only caller of. A cluster mapping *missing* the key raises `MissingSection`
  rather than reading as unread: a section nobody declared is a bug here, not a
  read somebody was refused.

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
`evidence.DOCUMENT`. Rename a section in either place and that test names it.
Two more readers resolve against the same vocabulary from their own tests:
`tests/evidence_fixtures.py` holds the three collected fixture files, and
`tests/test_suggest.py` holds the dotted paths `docs/preflight.md` quotes. Add a
section in both the collector and `DOCUMENT`, or in neither.

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

A third, on the account side: `facts.image_list` (#152). Four answers — read,
unread, no-agent, not-asked — because all four leave the *same* fallback images
behind, so `images` could never have said which happened. `_read_image_list`
carries it structurally, returning `None` for the entries wherever the state is
not `read`; a caller that iterates without looking gets a TypeError rather than
an empty list it reads as "this location runs nothing".

A fourth, arriving from BlazeMeter rather than from a file: **404 is the one
upstream status that is a different type.** `core._upstream` had every
`BzmApiError` become an `UpstreamError`/502, so a location somebody deleted and
a BlazeMeter nobody could reach were the same failure carrying whatever sentence
the API wrote — while the two remedies are opposites, re-read the account or
wait. `api.BzmApiError` had carried the code all along; it was thrown away one
line later. Only 404: a 401 is an expired key and a 403 is an account that
restricts the endpoint, and neither says the thing asked for is gone. The
browser half is `frontend/src/stale.ts`, which branches on `ApiError.status` and
on nothing else — never on the message, and never on a status-less failure like
fetch rejecting because the server is not running.

**The page has a Refresh, and it is a button because the staleness is rare.**
What the page holds ages while it is open (an agent a colleague created, a
location deleted in BlazeMeter's own UI, somebody else raising a location's
engines-per-agent); a poll was considered and rejected, because a control that
exists is itself the hint that the data can age, and a background timer is not.
There are **two**, because there are two reads: `AgentPanel`'s private-location
header — connect mode only, structurally, since manual entry renders none of
that branch and has no account to re-read — and the Account capacity header,
which re-reads the whole-account rollup. **`POST /api/refresh` is what makes
either mean anything**: without dropping `_cache` first, a re-read is served the
same answer for up to `CACHE_TTL_S` and the click looks exactly like one that
worked. It is not `_writes` — nothing there reaches the account — and it answers
nothing, because what to re-read is the caller's; each button re-reads its own
one thing, and a route that re-read both would be deciding which of the
account's slow calls a click on the other view is worth.
`App.refreshLocations` is a **separate path from the workspace effect**, which
is the initial load: that one blanks the list, resolves the harbor id a restored
session is holding and calls `release()`, none of which may happen again on a
page that is already configured. So a refresh writes `locations` and nothing
else — not the selection, not the options, not facts (their cache is dropped
too, so picking the location again re-reads them). `App.refreshCapacity` is the
same shape and carries the **same guard as the effect it sits beside**, in the
form a callback can have it: `live` is a closure over one run of an effect, and
a button outlives all of them, so the account is compared through a ref — 1.3s
on a 171-location account is plenty of time to change account in the drawer, and
the slower answer must not land under the newer account's name. A selected
location missing
from the answer is `vanished` in the panel: a notice and a forced fold back to
the list, not a silent repoint, and not the sentence a *failed action* gets —
`stale.goneNotice` says "press Refresh", `stale.vanishedNotice` cannot, having
been reached by one. Neither says **reload**: a pasted AUTH_TOKEN does not
survive one (#123), so sending somebody to the browser's reload button to fix a
stale list can cost a credential nothing can read back.

## Generator details that bite

- **`livetest` deploys the directory, and only sometimes re-renders it.**
  `generate` writes `out/profile.json` (resolved options minus `auth_token`), and
  the rig re-renders from it only where it has something to inject: the proxy's
  CA (`--local-proxy`) or the engine sizing (`--run-test`). A lean run has no
  regenerate callback at all and applies `<dir>/*.yaml` exactly as it sits — so
  "the manifests under test are generator output" is false, and #107 is what
  that cost: `--manifests` defaults to `out/`, which is whatever the last
  `generate` left there, and a run given `--ship-id` and `--auth-token` deployed
  a nine-day-old bundle for a *different* agent, plus a `bzm_limitrange.yaml`
  from a version that no longer emits one. Re-rendering everywhere would not
  have caught it — `_regenerator` merges onto the profile and prefers *its*
  `ship_id` over the command line, so a stale identity survives a re-render —
  and on the lean path it would have to either mint a token (revoking the one
  the bundle is running on, which is why the mint sits inside the re-render
  branch) or silently rewrite a directory the operator built deliberately. So
  the identity is **checked**, not re-rendered:
  `livetest.bundle_check` refuses a `HARBOR_ID`/`SHIP_ID` (in the ConfigMap or in
  `profile.json`) that is not the one the run was told to test, naming both
  values, and refuses any `*.yaml` outside `emitted_yaml_files()`. It runs in
  `cmd_livetest` before the token mint and again at the top of `livetest.run`
  before the try block — outside it, because that `finally` tears down a cluster.
  Unreadable is a note, not a refusal.
- **`--format docker` is a different platform, not a third rendering.** One
  agent as one container; there is no namespace, no ServiceAccount, no pod, and
  around two dozen options reach nothing. They are *named* rather than refused
  -- docker's entry in `IGNORED_BY_FORMAT`, listed in the bundle's README (the
  "Set here, but not carried" table, `_set_but_not_carried`) and only where set away
  from their default, because the failure is silent otherwise (a bundle handed
  over and believed to have applied a node selector). The page hides the same
  keys, off the same served table (see the UI bullet above); the two halves
  found each other's gaps -- hiding them on screen turned up `crane_hook` and
  `registry_auth` still offered and reaching nothing here.

  **A format may not refuse what it says it ignores**, and that half is
  `ignored_options()` rather than a rule anybody remembers. Three validators had
  broken it: `service_account_name` (empty was refused), the two engine limits,
  and `_ca_cfg`'s "choose one CA mode" -- reachable by picking an existing
  ConfigMap, switching to docker and pasting a PEM. Each refusal names a field
  the page for that format does not show, so it is a blocker with nothing on
  screen to clear: the off-screen blocker, again. `engine_size()` also *reads*
  through it, so a docker README states the engine size it carries rather than
  the one its own footer says was dropped. A new validator over an option in the
  table asks `ignored_options(o)` first, and
  `test_a_format_never_refuses_what_it_says_it_ignores` walks the whole table
  rather than the three keys that happened to break.
  `helm_parity.py` does not cover
  it and should not: there is nothing to render the same objects as. What holds
  it instead is `sh -n` over every branch of the generated script and the shape
  BlazeMeter's own Docker Command tab returns -- see `docs/docker.md`.
  **Check it against the command their API returns, never against the pages
  describing it.** Their generated command carries `-u 0` and
  `DOCKER_PORT_RANGE`; neither page mentions either, this was built from the
  pages, and the bundle did not start -- the container ran as the image's
  non-root user and died on the docker socket it exists to use.

  **`compose.yaml` is that same container, and the two are either/or** (#177).
  Compose buys no capability for one container; it is a shape some customers
  require, so it ships *inside* the docker bundle rather than as a fourth
  format. Both files carry `container_name: bzm-crane-<shipId>`, which is what
  makes running both fail at `compose up` with the name in the message --
  otherwise two cranes hold one agent identity and BlazeMeter reports
  **duplicated results rather than an error**. BlazeMeter publishes no compose
  file, so the rule above has no counterpart here and parity with the script is
  what holds it honest: every fixed value (`DOCKER_USER`, `DOCKER_RESTART`,
  `DOCKER_NETWORK`, the mounts, the workdir, the entrypoint) is a constant both
  renderers read, and the environment is `docker_split_env()` for both. **The
  constants make the comparison cheap; they are not what performs it** (#178) --
  a value written into one file alone has no constant to be caught by. So the
  two files are checked twice, and the questions are different:
  `test_compose_and_docker_run_describe_the_same_container` parses both and
  holds them against each other over `helm_parity.py`'s own matrix (pytest, and
  it must not skip -- both sides are built in Python, so unlike helm parity
  there is no binary to be missing), and the workflow's `docker` job runs
  `docker compose config -q` over generated bundles, which parity cannot answer:
  two python dicts can agree perfectly about a document compose refuses to
  parse. The one difference the parity check licenses is a value nobody supplied
  -- the marker to the script, `${...:?}` to compose (#183) -- and it is asserted
  in both directions rather than skipped. Two
  traps, both silent: **never emit a file named `.env`** -- compose auto-loads
  that one for interpolation *into the compose file*, not into the container, so
  a token there never reaches crane while looking as though it had (the
  credential stays `bzm-opl-agent.env`, read as `env_file:`) -- and **every
  inline value is written with `$` doubled** (`_compose_value`), because compose
  substitutes `$VAR` in its own values while the `--env` beside it passes the
  same string through untouched. The one deliberate interpolation is
  `${CA_BUNDLE:-./ca-bundle.crt}`, the counterpart of the script's overridable
  `CA_BUNDLE`.

- **The chart is copied, never re-rendered.** `--format helm` walks
  `templates/helm/` and emits it verbatim, so anything added there ships in every
  bundle — including files `package-data` would drop. Its globs do not recurse
  and `*` does not match a leading dot, which is why `.helmignore` and each
  directory are named explicitly in `pyproject.toml`; the release workflow
  asserts the wheel carries them, because a missing chart file fails at generate
  time on an installed copy and never in a checkout.
- `--format helm` and `--format docker` refuse a bundle *configured* for service
  virtualization — never a location for carrying the funcId, which is #115's
  whole point, and a location generated `--sv-ingress none` has the chart.
  `livetest` refuses a chart directory, a docker bundle, a profile
  with `service_account_create: false`, a placeholder `AUTH_TOKEN` it will not
  re-render over, and a bundle whose identity is not the agent under test. Guards over silent failures: a chart
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
  `max` below the engine size, or below crane's own 1 CPU / 2Gi **limit**,
  rejects the respective pod at admission.
- **Crane's request and its limit are 250m/512Mi and 1 CPU/2Gi, and which one
  you want depends on the question.** A LimitRange judges the limit; the
  *scheduler* places on the request, so "how many agents fit on this node" is
  answered by 250m, not by 1 CPU. Quoting the limit as a scheduling floor sized
  a kind cluster for two agents that comfortably held three.
- `doctor` measures capacity against node **allocatable**, deliberately: what is
  actually free needs every pod's requests summed per node, a much bigger read
  for a preflight. Say "upper bound" in any detail string you add.
- **List calls ask for one big page.** `/workspaces` asked for 100, one account has
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

- **The location's own image list is the first source, and it needs no agent**
  (#152). `GET /private-locations/{h}/ships/{s}/versions` — the same call crane
  makes at startup — answers for an agent in state `empty` that has never been
  online, which is every first install. `dockerTag` is crane's key and `version`
  its tag, so an entry is `dockerTag:version`, exactly the form a live
  Kubernetes agent reports the same image in; the map's *own* keys are
  BlazeMeter's resource ids (`taurusEngineDockerImage`) and crane resolves an
  override by none of them.

  **Three sources, and none of them replaces another.** `gather()` takes the
  image list, then a running agent's inventory, then the catalogue, and the
  first to name a key keeps it — they answer different questions, so precedence
  is per key rather than a fallback chain. The middle one is not redundant: a
  live *Kubernetes* agent reports `torero` and `richrach` beside the location's
  images and **no `/versions` response names either** — not a performance
  location's, not a twelve-resource one's — while every Docker agent read in the
  same account reports neither. They belong to the Kubernetes container manager
  rather than to the location, which is why the catalogue keeps them (a key
  crane cannot find in a sealed cluster is an ImagePullBackOff mid-test) and why
  the image list is right not to carry them. They stay in the `performance`
  category: the one live Kubernetes agent that reports them runs engines, and no
  SV-only Kubernetes agent has been read.

- **The GUI browser gap is closed, and `gui_images_incomplete` now reads the
  images rather than their provenance.** The account carries 60+ version-pinned
  `charmander/*` repos, and the location's image list names the one this
  location pins, off its browser funcIds and with no agent running. So the
  sentence this file used to carry — only a live agent knows — is wrong, and
  what is left is narrower: **manual entry still cannot know**, because there is
  no account to ask. Provenance was a proxy in both directions (a live inventory
  carrying no browser passed; an image list would pass by existing), so the
  predicate asks the only question that matters — does this bundle name a
  browser image at all. Still don't invent a default.

- **`images_source` names every source that contributed; `image_list` says how
  the read went.** Two fields because they are two questions, and the second is
  the one the rule below is about: `read` (with a count, and 0 is a location
  whose resources are none), `unread` (refused — never a count), `no-agent`
  (per-agent route, no agent, so nothing was asked) and `not-asked` (manual
  entry, or a facts file written before any of this). `_read_image_list`
  returns `None` rather than `[]` wherever the state is not `read`, so a reader
  that ignores the state raises instead of seeing an empty answer somebody was
  refused.

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
