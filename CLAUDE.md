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
| Offline | `.venv/bin/python -m pytest tests -q` | ~3s. Must end **`N passed`, nothing skipped** — install `pip install -e ".[dev]"`; `test_server`/`test_mcp` skip without fastapi/mcp and CI asserts the extras import. |
| Helm parity | `python tests/helm_parity.py` | Renders option sets as manifests and as the chart and requires the same objects. Not pytest on purpose (needs the `helm` binary). Offline counterpart: `tests/test_helm.py`. Touch the chart or the manifests → add to both. |
| Frontend | `cd frontend && npx vitest run && npx tsc --noEmit && npm run lint` | Then `npm run build` — it rewrites `bzm_opl_gen/ui_dist` and its source fingerprint, which `tests/test_ui_build.py` checks. Commit the rebuilt `ui_dist` with any `frontend/` change. |
| Live rig | see below | 12–20 min, needs a cluster and an account. |

Every live-rig check has an offline counterpart that fakes the cluster/API; add
one with any new live check. `tests/conftest.py` fails any offline test that
runs a real kubectl/oc/docker/minikube/kind/helm.

**Worktrees:** `.venv` is an editable install of the checkout it was built in.
In a git worktree, build a venv inside the worktree or you test the wrong code.

```
.venv/bin/python -m bzm_opl_gen livetest --api-key api-key.json \
    --namespace bzm-livetest --cluster minikube \
    --local-registry 5001 --local-proxy --contain-egress \
    --run-test <test-id> --timeout 420
```

Run it with `run_in_background: true` and poll with
`until grep -qE "LIVE TEST|Traceback"`. stdout is buffered when redirected, so
the log is empty until exit; `kubectl get pods` is the live view. Run
`bzm-opl-gen toolcheck --cluster minikube --local-registry 5001 --local-proxy`
first.

## The account — real writes, shared fixtures

- Creating, changing or starting anything in the BlazeMeter account is a real
  write: **confirm with the user first** unless they named the artifact.
- The standing testbed (locations, agent, a Taurus test making real HTTP
  requests, a virtual service) is described in **`testbed.local.md`**
  (gitignored). `cat` it at the start of a session that touches the account.
  If it is absent you are not on that machine: gather your own account
  (`bzm-opl-gen locations --api-key api-key.json --account-name "<name>"`) and
  create scratch fixtures. Never reconstruct ids from an earlier session.
- `api-key.json` in the repo root (gitignored) is the key.
- `--run-test` only proves something with a test whose samplers hit the network.

Standing fixtures are reused, so leave them as found:

- **Never delete a Service, Route or Deployment crane created** (including a
  mock Deployment at 0 replicas): crane holds a pool and deleting one
  desynchronises the agent. Exception: a teardown you have decided to finish.
- **One run at a time per agent.** Two cranes on one agent identity make
  BlazeMeter report duplicated results, not an error.
- **Don't regenerate an AUTH_TOKEN casually** — minting revokes the one any
  deployed agent runs on.
- If the rig repoints a test it restores `executions` in a `finally`. After any
  live run verify:
  ```
  python -c "from bzm_opl_gen import core; print(core.client_from_key('api-key.json').test(<id>).get('executions'))"
  kubectl get ns | grep bzm-livetest ; docker ps -a | grep bzm-opl ; minikube status -p bzm-opl-test
  ```
  A deleted location does **not** stop its agent; a stray crane keeps running
  and reporting and nothing on the BlazeMeter side shows it.
- **Releasing a ship BlazeMeter won't delete** (`Cannot remove ship with active
  containers`): let a *running* crane report zero. On Kubernetes delete the
  stopped virtual service's mock Deployment with crane still up, wait for
  `idle`, then remove crane, the ship and the location in that order. On docker,
  start any crane on that harbor/ship id with its token; it clears in ~30s.
  Deleting crane first wedges it.

## Live rig — what each flag proves

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

### Virtual services by hand

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

## Local environment

- A full disk makes minikube fail with `RSRC_DOCKER_STORAGE`. `toolcheck` knows
  which number binds (VM `df` for colima/Lima, host free space for Docker
  Desktop).
- `docker image prune --filter until=…` filters on *build* date and deletes
  BlazeMeter images the day you pull them.
- arm64: BlazeMeter images are amd64-only; size engines down
  (`--engine-cpu 1 --engine-mem 4Gi`). Pin `mitmproxy:11.1.3` (12+ SIGILLs).

## Architecture

```
cli.py  server.py  mcp_server.py      three front doors, thin
          \   |   /
           core.py                    orchestration; raises core.CoreError(.status)
   api.py  facts.py  plan.py  doctor.py  suggest.py  workstation.py  livetest.py
   generate.py → render_manifests / render_helm / render_docker
     bundle_options  bundle_names  bundle_env  ca_trust  service_virt
     image_registry  nodepools  readme_parts  markers  required_fields  quoting
   footprint.py (leaf: sizes, hosts)   kube.py (kubectl primitives)
   bundle_check.py  sv_read.py  verdict.py  evidence.py  cert.py  options.py
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
  `opl_agent livetest` (`BZM_OPL_ENABLE_LIVETEST`). Anticipated failures raise
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
   `core.NotFound` (404 only), frontend `stale.ts` (status, never message).
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
- No LimitRange is emitted: crane sets engine requests from the location's
  `overrideCPU/overrideMemory` (250m/256Mi when unset), and a LimitRange only
  hits crane's `test-job-*` pods. `doctor` still reads an existing one.
- Crane requests 250m/512Mi and limits 1 CPU/2Gi: the scheduler places on the
  request. `doctor` measures against node allocatable — say "upper bound".
- List calls ask for one big page (1000); a truncated list only looks short.
- Kubernetes agents report bare image keys (`taurus-cloud:latest`,
  `blazemeter/charmander/chrome_…` — the whole path is the repo). Keys don't
  match repos (`taurus-cloud`→`v4`); `FALLBACK_IMAGES` was read off live
  inventories. Image sources, per key: the location's `/versions` list (works
  with no agent), then a live agent's inventory (adds `torero`, `richrach`),
  then the catalogue. `facts.manual()` returns the same shape as `gather()`.

## Conventions

- **Comments** state current behaviour and, where non-obvious, the reason or
  environment fact, in one to three lines. No issue numbers, "used to", or
  narrated history — that belongs in git and the issues.
- Button labels are one word where possible (`Apply`, `Save`, `Download`); the
  cost goes in the sentence beside it.
- Never push to `main`; branch, push, open a PR.
- Warnings shown in both Markdown and plain text (plan warnings,
  `PLACEHOLDER_SOURCE`) are plain prose: no backticks, `--`, emphasis or `->`.
