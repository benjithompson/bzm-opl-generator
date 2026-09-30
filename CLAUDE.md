# Working on bzm-opl-gen

Generates BlazeMeter OPL (private-location) bundles — Kubernetes/OpenShift
manifests, a Helm chart, or a docker script + compose file — from a customer's
real account facts, and live-tests them. `README.md` is the user-facing doc;
this file is what a session needs before touching the code. `CONTEXT.md` is the
glossary: **functionality** (never "feature"), **agent** (never "ship", outside
`ship_id`), **profile** (a JSON file of options), **sizing** (the capacity a run
needs). Read it before naming anything new.

## Tests — four layers, all must stay green

| Layer | Command | Notes |
|---|---|---|
| Offline | `.venv/bin/python -m pytest tests -q` | ~15s. Must end **`N passed`, nothing skipped** — install `pip install -e ".[dev]"`; `test_server`/`test_mcp` skip without fastapi/mcp and CI asserts the extras import. |
| Helm parity | `python tests/helm_parity.py` | Renders option sets as manifests and as the chart and requires the same objects. Not pytest on purpose (needs the `helm` binary). Offline counterpart: `tests/test_helm.py`. Touch the chart or the manifests → add to both. |
| Frontend | `cd frontend && npx vitest run && npx tsc --noEmit && npm run lint` | Node 22, as CI: under Node 26 `App.test.tsx` fails (its global `localStorage` hides jsdom's). Then `npm run build` — it rewrites `bzm_opl_gen/ui_dist` and its source fingerprint, which `tests/test_ui_build.py` checks. Commit the rebuilt `ui_dist` with any `frontend/` change. |
| Live rig | `livetest` — see `LIVE_RIG.md` | 12–20 min, needs a cluster and an account. |

Every live-rig check has an offline counterpart that fakes the cluster/API; add
one with any new live check. `tests/conftest.py` fails any offline test that
runs a real kubectl/oc/docker/minikube/kind/helm.

**Worktrees:** `.venv` is an editable install of the checkout it was built in.
In a git worktree, build a venv inside the worktree or you test the wrong code.

**`LIVE_RIG.md`** — read it before running `livetest`, verifying a virtual
service by hand, releasing an agent, or freeing local disk for a cluster. It
holds the run command, what each flag proves, how to read an engine run, the
post-run checks, and the local-environment gotchas.

## The account — real writes, shared fixtures

- Creating, changing or starting anything in the BlazeMeter account is a real
  write: **confirm with the user first** unless they named the artifact.
- The standing testbed (locations, agent, a Taurus test making real HTTP
  requests, a virtual service) is described in **`testbed.local.md`**
  (gitignored). `cat` it at the start of a session that touches the account.
  If it is absent you are not on that machine: gather your own account
  (`bzm-opl-gen locations --api-key api-key.json --account-name "<name>"`) and
  create scratch fixtures. Read every id from the account in this session; an
  id from an earlier session may name a deleted object.
- `api-key.json` in the repo root (gitignored) is the key.

Standing fixtures are reused, so leave them as found:

- **Leave every Service, Route and Deployment crane created in place**
  (including a mock Deployment at 0 replicas): crane holds a pool, and deleting
  one desynchronises the agent. Exception: a teardown you have decided to
  finish.
- **One run at a time per agent.** Two cranes on one agent identity make
  BlazeMeter report duplicated results, not an error.
- **Reuse the agent's AUTH_TOKEN** (`--auth-token`); minting a new one revokes
  the one any deployed agent runs on.
- **After any live run**, run the post-run checks in `LIVE_RIG.md`. A deleted
  location does not stop its agent: a stray crane keeps running and reporting,
  and nothing on the BlazeMeter side shows it.

## Architecture

```
cli.py  server.py  mcp_server.py      three front doors, thin
          \   |   /
           core.py                    orchestration; raises core.CoreError(.status)
   api.py  facts.py  plan.py  doctor.py  suggest.py  workstation.py  livetest.py
   generate.py → render_manifests / render_helm / render_docker
     bundle_options  bundle_names  bundle_env  ca_trust  service_virt
     image_registry  nodepools  readme_parts  markers  required_fields  quoting
   footprint.py (leaf: sizes, hosts)   quantity.py (leaf: k8s quantities)
   kube.py (kubectl primitives)   agent_env.py (BlazeMeter's env-var reference)
   bundle_check.py  sv_read.py  verdict.py  evidence.py  cert.py  options.py
   image_catalog.py (what each image is; release rules)   registry_client.py
   ca_check.py (TLS chain against the network)   triage.py (post-deploy rules)
   smoke.py (post-install check of a deployed agent; reuses livetest readers)
frontend/ (React)  →  bzm_opl_gen/ui_dist (committed build)
```

- **`core` imports no web framework** (asserted by `test_core`). Failures are
  `CoreError` subclasses with `.status`; the server's exception handler is the
  only place one becomes HTTP. Shared front-door logic lives in core:
  `build_bundle` (checks the output path *before* resolving/minting a token),
  `create_agent`, `whoami`, `default_account_id`, `facts_warnings`,
  `reporting_counts`.
- **Never re-export a name from another module** — an alias does not follow a
  monkeypatch. Import from the home module and call `kube.run(...)`-style
  (module attribute), never `from .kube import run` (a test enforces this for
  `kube`).
- **`plan.py` reaches nothing.** It sizes a target for someone with no cluster
  and often no account; `test_plan` checks its *transitive* import closure (no
  api/facts/generate/urllib). It shares engine footprint with `footprint` and
  its per-engine ratio with `doctor.check_threads_per_engine` (asserted equal).
  Users-per-engine is an assumption and every surface says so.
- **One runtime dependency: `cryptography`**, imported only by `cert.py`, which
  `service_virt` imports lazily so `plan` stays light.
- **The generator:** `generate()` is the entry point; `bundle_options` holds
  `DEFAULT_OPTIONS`, `IGNORED_BY_FORMAT` and option readers; one renderer per
  format. The chart under `templates/helm/` is copied verbatim into every helm
  bundle and restates generator rules in Go (`bzm-opl.svBackends`,
  `bzm-opl.validate`, `MARKER_PATTERN`) — held equal by `test_helm` and parity.
  `pyproject.toml` names chart dirs and `.helmignore` explicitly (package-data
  globs don't recurse or match dotfiles); the release workflow asserts the
  wheel carries them.
- **MCP server** (`mcp_server.py`) serves a session with no checkout: its tool
  descriptions, `INSTRUCTIONS` and `docs/*.md` are all the documentation there
  is. It never returns an AUTH_TOKEN (`reveal_token` is its own action), never
  takes a secret as an argument, and never writes to a cluster except gated
  `opl_agent livetest` (`BZM_OPL_ENABLE_LIVETEST`, which also gates
  `opl_agent smoke` with `run_test`); `opl_agent triage` and `smoke` only
  read one. Anticipated failures raise
  the SDK's `ToolError`; anything else reaches the client as a bare
  "Error executing tool".
- **Options:** a new option needs a row in `options.py` (`summary` ≤20 words,
  served to every MCP session; `doc` for the table). Regenerate
  `docs/options.md` with `python -m bzm_opl_gen.options`; never hand-edit
  between its markers. `DEFAULT_OPTIONS` is the only source of default values.
- **Frontend:** `App.tsx` owns domain state; hooks called from App
  (`useServedTables`, `useResource`, `useCapacity`, `useAgentWatch`,
  `usePreview`, `useBundleOptions`) keep it so,
  and panels keep only view-local state. Every route goes through the `Api`
  seam (`fakeApi` in tests). Tables the generator owns (`IGNORED_BY_FORMAT`,
  `RESERVED_ENV`, sizing models, slot minimums, markers) are **served**, never
  restated in TS; the single test copy is `fixtures.ts`, held equal by
  `test_server.py`. The page asks nothing about the target cluster — that is
  `doctor`/`scripts/bzm-cluster-evidence.sh`.

## Invariants

1. **"Could not read" and "there is nothing there" never share a
   representation.** The bug recurred six times; it survives only where it is
   structural. Readers go through `suggest._read`, `doctor.reads(...)`,
   `kube.kget_named` ({} = NotFound, None = unread; `kget` flattens both and is
   only for lists), `facts.image_list` states, `cert.dns_names` (None = did not
   parse, [] = no names), `ui_build.staleness`, `ca_trust.CA_UNRESOLVED`,
   `core.NotFound` (404 only), `registry_client` states (read / unread /
   not-asked; present / missing / unread), `doctor.Unprobed`, `triage.gather`
   sections (None = unread), frontend `stale.ts` (status, never message).
   A denied read is a WARN and exits 0; an empty result can be a FAIL. The
   evidence document's section names live once in `evidence.py`, and
   `test_cluster_evidence` holds the shell collector to them.
2. **Blank required fields become markers**, `<KEY>` in upper case, built only
   by `markers.marker` and recognised only by `is_placeholder`/`marker_in`
   (readers match *any* marker). Angle brackets make `kubectl apply` refuse the
   object by name. `harbor_id`/`ship_id` may be blank too (bundle before the
   location exists) via `or_marker`. Sample values in docs are **lower case**
   (`<harbor-id>`, `--auth-token <token>`); only real markers are upper case.
   The README's not-finished table must list exactly the markers the files
   carry (`test_the_readme_names_exactly_the_markers_the_bundle_carries`).
   The page warns and never blocks on a blank.
3. **A format never refuses what it says it ignores.** `IGNORED_BY_FORMAT` is
   served; the page hides those fields and the README lists values set but not
   carried. A validator over an ignored option asks `ignored_options(o)` first
   (`test_a_format_never_refuses_what_it_says_it_ignores`). No format refuses a
   functionality; ignoring and refusing are different answers.
4. **Functionality = funcId.** The vocabulary is the account's
   (`core.func_ids()`), with the covered three as the keyless answer; an
   account that refuses the read raises. Uncovered funcIds are named, never
   dropped. Connected, the location's funcIds decide what runs; in manual entry
   the ticked list *is* the declaration (persisted, checked against the served
   vocabulary). SV is exclusive of engine functionalities only where a location
   is being decided.
5. **Sizing.** `slots` is engines per *agent*; a location's concurrency is
   agents × slots. `plan.SIZING_MODELS` holds virtual users, browser instances
   and requests/s; SV's baseline is `None` ("unmeasured") and must never be
   filled in or borrow the performance ratio. The engine limits
   (`KUBERNETES_RESOURCES_LIMITS_*`) reach every pod crane creates and are
   never cleared for a functionality. `threadsPerEngine` is never relabelled.
6. **`extra_env`** reaches crane only. Every variable the bundle writes is
   refused there, naming the owning option (`bundle_env.RESERVED_ENV`, derived
   from real bundles by a test); `/api/agent-env` offers BlazeMeter's reference
   minus that set.
7. **`profile.json`** records every resolved option plus `ship_id`, and
   deliberately not the AUTH_TOKEN or `sv_tls_key` (`SECRET_OPTIONS`),
   `harbor_id`, or the four BlazeMeter-side location settings.
8. **Generated bundle text is customer-facing.** Comments, READMEs and scripts
   in a bundle say what the operator needs to act — no issue numbers, internal
   history or internal module names.

## Generator facts that bite

- Crane composes `${DOCKER_REGISTRY}/<repo path>:<tag>` for engines and ignores
  `IMAGE_OVERRIDES` there, so the override value *is* the composed name
  (`cluster_composed_targets`, shared with every mirror). Only the engine
  reference was observed live.
- Docker is its own platform: one container, no namespace/SA/pod; BlazeMeter's
  real command carries `-u 0` and `DOCKER_PORT_RANGE` — check against the
  command their API returns, not their docs. `compose.yaml` ships beside the
  script; both set `container_name: bzm-crane-<shipId>` so running both fails.
  Never emit a `.env`; `$` is doubled in compose values. The two SV PEMs are
  content; the key must be PKCS#8 and the hostname covered by the certificate.
- SV is published with disjoint variables per platform
  (`KUBERNETES_WEB_EXPOSE_*` vs `HOSTNAME_OVERRIDE` + `TLS_CERT`/`TLS_KEY`),
  so each set is the other format's ignored options.
- CA bundles can exceed kubectl's last-applied annotation cap; manifests over
  200KB apply `--server-side`.
- `PATCH /tests/{id}` silently drops `executions` for a taurus-script test.
- **Engines default to 2 CPU / 8Gi, requests equal to limits.** The default
  lives in `footprint.ENGINE_DEFAULT_CPU/_MEM` and the chart's `_helpers.tpl`,
  held equal by `test_helm`. Requests are the bundle's
  `KUBERNETES_RESOURCES_DEFAULT_CPU` and `_MEM` (memory integer MiB;
  `bundle_options.engine_request`) — missing from BlazeMeter's
  environment-variable page, but what BlazeMeter's own `helm-crane` chart writes
  for `resourcesExecutors.requests`. Measured on live engines: 1/4Gi and 2/8Gi
  bundles gave requests equal to limits (QoS `Guaranteed`). A location's
  `overrideCPU/overrideMemory`, when set, become the limits
  (`resolve_engine_limits`) and replace the requests (measured: 1/4096 gave
  requests {1, 4Gi}); unset everywhere, crane uses 250m/256Mi.
- No LimitRange is emitted: crane sets requests explicitly, so a LimitRange
  only hits crane's `test-job-*` pods. `doctor` still reads an existing one.
- Crane requests 250m/512Mi and limits 1 CPU/2Gi: the scheduler places on the
  request. `doctor` measures against node allocatable — say "upper bound".
- List calls ask for one big page (1000); a truncated list only looks short.
- Kubernetes agents report bare image keys (`taurus-cloud:latest`,
  `blazemeter/charmander/chrome_…` — the whole path is the repo). Keys don't
  match repos (`taurus-cloud`→`v4`); `FALLBACK_IMAGES` was read off live
  inventories. Image sources, per key: the location's `/versions` list (works
  with no agent), then a live agent's inventory (adds `torero`, `richrach`),
  then the catalogue. `facts.manual()` returns the same shape as `gather()`.
- **`latest` on BlazeMeter's registry is stale** (measured: v4 `latest` is
  1.24.169, crane `latest` 3.7.44), and no other moving tag is reliable. With
  an account the location's `/versions` list is exact. Without one,
  `core.release_pins` pins each repo to its newest release by
  `image_catalog.RELEASE_SERIES` (the SV `X.Y.Z.N` rule is inferred); a
  location can still ask for an older release, and every surface says so.
  Generating a bundle makes no network call: the pins live in the facts.

## Conventions

- **Comments** state current behaviour and, where non-obvious, the reason or
  environment fact, in one to three lines. No issue numbers, "used to", or
  narrated history — that belongs in git and the issues.
- Button labels are one word where possible (`Apply`, `Save`, `Download`); the
  cost goes in the sentence beside it.
- Every change reaches `main` through a PR: branch, push, open one.
- Warnings shown in both Markdown and plain text (plan warnings,
  `PLACEHOLDER_SOURCE`) are plain prose: no backticks, `--`, emphasis or `->`.
