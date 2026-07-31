# Changelog

All notable changes to bzm-opl-gen are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

Group each release's notes under **Added**, **Changed**, **Fixed**, **Removed**
or **Security**, and drop any section that would be empty. Write entries for
the person upgrading: what changed for them, not which files moved. Lead with
anything that breaks.

## [Unreleased]

### Added

- **`bzm-opl-gen plan --users N`: how much infrastructure a load target needs,
  before any of it exists.** Every other command here starts from something that
  already exists — a location, an agent, a cluster, an evidence file. This one
  starts from a number somebody has in a planning meeting, and takes **no API
  key, no facts file and no cluster**, because the customer who needs it most has
  none of them: the cluster is a ticket they have not raised yet, and this is
  what they raise it with.

  ```
  bzm-opl-gen plan --users 5000 -o ./plan
  ```

  5,000 virtual users → 10 engines of 2 CPU / 8Gi → 10 nodes of 3 vCPU / 10Gi
  capacity, a peak of 30 vCPU / 100Gi that idles at zero between runs, one small
  always-on node for the agent, the egress hosts a firewall rule needs, and the
  four BlazeMeter-side settings (`slots`, `threadsPerEngine`, `overrideCPU`,
  `overrideMemory`) without which the cluster is provisioned and then not used.

  **One vocabulary, BlazeMeter's own:** a location holds agents, an agent runs
  engines, and each engine drives virtual users. `slots` and `threadsPerEngine`
  appear only as the names of the two location *fields* they are — concurrent
  engines, and virtual users per engine — rather than as terms anything is
  explained in. The document says nothing about *what* is being tested: the
  request is for capacity to run load tests from this cluster, and naming an
  application invites the reply that it should be sized per application.

  `-o DIR` writes **`capacity-request.md`** — the same numbers written for a
  platform team that has never heard of BlazeMeter, showing the arithmetic so
  the request can be *checked* rather than only read. `--markdown` prints it,
  `--json` gives the whole plan as data.

  **The virtual-users-per-engine figure is an assumption, and everything says
  so.** How many virtual users one engine carries is a property of the script,
  not of the engine — a chatty API test with no think time exhausts one far
  sooner than a browsing journey does — so unset, `--vus-per-engine` assumes what
  an engine of the chosen size is *rated* for (500 for 2 CPU / 8Gi, scaled
  linearly on whichever of CPU and memory is tighter for any other size) and the
  plan carries `vus_per_engine_assumed`. It follows the engine size rather than
  sitting at a flat 500: on the Small preset a flat 500 assumed load the engine
  cannot carry — and then warned about the figure the planner itself had chosen —
  and on Large it asked for twice the nodes needed. The document leads with it, the web panel shows
  it as a callout, and the MCP tool's description tells a model to pass the
  qualifier on. The honest sequence is plan → provision small → measure →
  re-plan, and the document says that too.

  The same calculator is in all three surfaces: **`plan`** in the CLI, a
  **Plan capacity** view in the web UI (a view rather than a step, since
  everything step 1 asks for is what somebody sizing a cluster has not got yet),
  and **`opl_plan capacity`** on the MCP server, which returns the numbers and
  the document together. In the UI, *Use this plan* fills in the location's
  concurrent engines and virtual users per engine, and the bundle's engine size;
  it writes nothing to BlazeMeter. Full reference in
  [docs/capacity-planning.md](docs/capacity-planning.md).

  **None of the BlazeMeter side waits for the cluster**, and the document says
  so: a location and its agent are records in BlazeMeter, so both can be created
  with the planned settings while the infrastructure request is still being read.
  An agent that has never sent a heartbeat is the expected state until its
  manifests are applied, not a half-finished setup — so the wait for nodes is
  setup time rather than dead time.

  `doctor` and the planner now share the virtual-users-per-engine ratio
  (`plan.supported_vus`) rather than each carrying it, so a plan the preflight
  would then warn about cannot be produced.

- **Change a location's settings from the web UI, after it exists.** The
  correction that follows a setup: a location and its agent are built for 500
  virtual users an engine, a real run says the figure is 1,000, and until now
  the only answer was "go and edit it in BlazeMeter" — the one place this tool
  otherwise never sends you.

  Step 1 now edits the selected location's **concurrent engines** (`slots`),
  **virtual users per engine** (`threadsPerEngine`) and the engine's CPU and
  memory **requests** (`overrideCPU` / `overrideMemory`). None of those four is
  in a manifest, so a change needs no regenerate, no re-apply and no restart —
  it applies to the next test that starts, which the panel says.

  **The answer is a re-read of the location, not an echo of the request.**
  BlazeMeter's own create endpoint accepts `threadsPerEngine` and does not store
  it — that is why a freshly created location 403s every test start — so a form
  that reported what it sent would show a number the account never took. Fields
  that came back unchanged are reported as not stored, in amber, beside the ones
  that saved.

  **`slots` is engines per agent, and the calculator divides by them.**
  BlazeMeter's UI calls the field "Engines per agent" — "the number of
  engines/tests that can run on one agent" — so a location's concurrency is
  `agents × slots`. The planner had it as the location's total, which on a
  two-agent location asks for twice the engines and twice the cluster. It now
  takes the agent count (the UI defaults to the number the location has), sets
  `slots` to the run divided by it, and reports **nodes per agent**, because one
  agent is one cluster and the infrastructure request is for one of them. The
  field is labelled "Engines per agent" throughout, and `doctor` — which
  measures one cluster, so was right all along — says "engine(s) per agent" too.

  **The settings open out of the location, and size themselves.** Selecting a
  location expands it the way an agent row does, and the settings are inside it
  — they belong to the one that is selected and to nothing else. **Calculate**,
  beside the heading, sizes *that* location from a virtual user target, starting
  from what it already says: 5,000 virtual users at the 50 an engine a location
  currently advertises is 100 engines and 100 nodes, which is the argument for
  changing the figure rather than the pool.

  It is guidance, not a form. It answers in engines, **nodes** and peak vCPU —
  the cost that lands off this page, on a cluster nobody sees from here — flags
  the users-per-engine figure as an assumption when nothing supplied one, and
  carries the same warnings the planner does. *Apply* fills concurrent engines
  and the two engine requests; applying is not saving, and **Save** is still the
  only control that writes.

  Step 1's three sections (Connect, Private location, Agent) are now bordered
  panels with tinted headers, and they fold: a chevron on the left of the
  header, pointing right when closed and down when open, and the whole bar is
  the control. They open on whichever section the step has reached until one is
  pinned, and a folded one says on its header what it holds. Three sections
  divided by a hairline on one white background read as a single long form; a
  panel's extent is the thing a reader needs before anything inside it.

  Only changed fields are sent, so a page left open does not write back three
  values somebody else has since edited; blank means "leave alone", so there is
  no way to *clear* a setting here; and `funcIds` is deliberately not in the set
  (it is `add_func_id`'s, which is additive by construction — a passthrough
  would let a caller replace the whole list by accident). This is the third and
  last write the page makes to a customer's account, and like the other two it
  is a control of its own that says what it costs first.

### Fixed

- **The account and workspace dropdowns say when they are loading.** Both are a
  round trip to BlazeMeter over whatever network the user is on, and both were
  silent while it happened — an empty dropdown and a slow one look identical, so
  the answer to "my account is not in the list" was to wait and try again. They
  now show `loading…` and a small spinner inside the field, and cannot be
  cleared or opened until the options arrive.

- **A collapsed step-1 section kept its controls clickable.** The body stays
  mounted while folded so what was typed into it survives, but a mounted body
  inside a zero-height row is still in the hit-testing and accessibility trees:
  its buttons took clicks aimed at whatever was drawn over them, and a keyboard
  tab walked into a section nobody could see. Folded sections are now
  `visibility: hidden` as well as zero-height, which keeps the state and takes
  them out of both.

- **An API call the running server has never heard of now says so.** The UI
  bundle is read from disk on every request, so a server left running for a day
  serves a page whose calls it cannot answer — FastAPI has no route, the SPA's
  static mount answers the POST with `405 Method Not Allowed`, and a working
  feature looks broken. A 404 or 405 carrying no `detail` is exactly that case,
  and the page now reports it as "this page is newer than the server it is
  talking to" with the command to restart, rather than as the feature's own
  error.

- **The web UI's engine-sizing hint still claimed engine requests could not be
  set.** "Crane stamps them at 250m/256Mi and the scheduler packs nodes on
  those" was the belief a live GKE run disproved in the previous release — the
  bundle sets the engine's *limits*, the location's `overrideCPU`/
  `overrideMemory` set its *requests*, and 250m/256Mi is only the default for a
  location that sets neither. The correction reached the generator, the node
  pool recipe and `doctor`; this hint was missed, so the one place a user
  configures engine size still told them the fix was unavailable.

### Changed

- **BREAKING: `generate --api-key` no longer fetches an AUTH_TOKEN.** It fetched
  one before, and that fetch *mints*: BlazeMeter issues a fresh token and
  invalidates the previous one. So regenerating a bundle — even just to look at
  it — revoked the credential of the agent already running from the last one, and
  it failed silently. Crane answers a dead token with `404`, logs `Sleeping for
  300`, and never starts its health service, so the pod sits `0/1 Running` and
  reads as a slow boot rather than a revoked credential. That cost a live
  debugging session before the cause was found.

  Two harms, and neither is secrecy — crane logs the token in plaintext, and
  anyone who can read a pod log in that namespace can read the Secret anyway.
  The first is **permanence and reach**: a token in a model transcript or a
  shared `profile.json` reaches people who never had access, for as long as the
  file exists. The second is **rotation**, above.

  `generate` now resolves the token in four steps, prints which one it took, and
  reaches BlazeMeter only in the second:

  1. `--auth-token <token>` wins outright.
  2. `--rotate-token` (new, and needs `--api-key`) issues a new one. It warns
     before it acts, naming the ship and the consequence. There is no
     confirmation prompt — the flag is the confirmation.
  3. Otherwise the token already written into `-o` is read back and reused,
     provided that bundle's `profile.json` names the same `ship_id`. This is what
     makes regenerating a bundle byte-identical.

     If `-o` holds a bundle for a *different* ship, or one whose `profile.json`
     cannot say whose token it is, **the command refuses and writes nothing.**
     Generating there would overwrite that bundle, and its AUTH_TOKEN cannot be
     fetched again afterwards — the only endpoint that returns one issues a new
     one — so it would survive nowhere but inside an agent still running on it.
     Pass `--auth-token` (or `--rotate-token`) and the directory is not consulted
     at all, which is how you replace such a bundle deliberately.
  4. Otherwise the `<YOUR_AUTH_TOKEN>` placeholder stays, and the command names
     the two places a real token comes from — what `create-ship` printed, or
     `kubectl -n <ns> get secret blazemeter-secret -o
     jsonpath='{.data.AUTH_TOKEN}' | base64 -d` for an agent already deployed.
     That command is printed, never run: nothing here reads your cluster.

  **What to change:** anywhere you ran `generate --api-key`, pass `--auth-token`
  instead, or drop the flag and let the bundle in `-o` supply its own token. On
  its own `--api-key` now warns that it has no effect. `out/profile.json` still
  does not carry the token and will not start doing so.

  `create-ship` is unchanged except in what it says: the token it prints is the
  durable artifact, nothing here records it, and the `next:` line it prints now
  passes `--auth-token` rather than `--api-key` (which would produce a
  placeholder bundle).

  **BREAKING on the MCP surface: `opl_bundle generate`'s `fetch_token` argument
  is now `rotate_token`, and defaults to `false`.** It defaulted to minting, so
  every generate revoked the running agent's credential — and a session there has
  no terminal to be warned in and no prompt to be stopped at. The rename is the
  safeguard: a model reads the argument name, so the argument name has to be the
  warning. `fetch_token` is *refused* rather than ignored, because a caller
  working from a cached description means to mint and a silently-placeholder
  bundle is a worse answer than one round trip.

  Every generate now reports `token_source: {branch, ship_id, message}` — one of
  `given`, `rotated`, `reused`, `placeholder` — and a rotation is repeated in
  `warnings`, naming the ship whose credential was replaced. A live rotation on
  this surface used to answer `warnings: []` and name nothing at all. The token
  itself still never appears in a response, on any branch. `generate` also
  passes `out_dir` through now, so an MCP session reaches the `reused` branch:
  regenerating into a directory that already holds this ship's bundle issues
  nothing and comes out byte-identical. It could not reach that branch before.
  `opl_location reveal_token` is unchanged — the sanctioned way to read a token,
  and a whole action so it cannot happen as a side effect.

  **The web UI follows the same rule**, and the button that used to break a
  running agent was the download: it fetched an AUTH_TOKEN on the way out, so
  taking a copy of the bundle to read it rotated the credential of the install
  already running. Downloading and **Save to folder** now mint nothing, and both
  say which of the four ways their bundle got its token.

  Where the token comes from instead: **creating an agent issues it once, there,
  and puts it in a field on the page** — a ship created a moment ago has no
  previous credential to invalidate, which is why that is the one action that
  still fetches. The field is masked with a *Show* toggle, and nothing writes it
  down, so that page is the copy to keep.

  **Pointing at an agent that already exists leaves the field empty**, because
  no API reads an existing token back. Paste what you kept, or tick *Issue a NEW
  AUTH_TOKEN with this bundle* — which says, before you download, that it kills
  the credential the running agent holds. A download with neither is a
  placeholder bundle, and the page says so over the button rather than leaving
  you to find out at `kubectl apply`.

  **Saving twice into the same folder no longer rotates.** The bundle already
  there supplies its own token — same folder, same ship, same bytes — so
  re-rendering with one option changed leaves the agent deployed from the last
  save working.

- **`livetest` issues one credential per run instead of one per render.** Its
  regenerate step called the token endpoint every time it was invoked, and a run
  invokes it three or four times — the negative control renders twice, then
  `--run-test` and `--local-proxy` each do — so a single run minted four
  credentials, each invalidating the last. Any agent deployed from an earlier
  render was holding a revoked token and sat `0/1 Running`, which is
  indistinguishable from a slow boot; this is plausibly a real source of the
  rig's intermittent failures. One token now, minted at the start, printed with
  the ship it was for, and threaded through every render.

  There is no `--rotate-token` on this command and there should not be: bringing
  an agent online is its entire purpose, so the rotation is implied by running
  it. New `--auth-token` skips the mint for a caller already holding one — the
  token `create-ship` printed, say.

### Added

- **`--sv-ingress none`, for a location that offers service virtualization when
  you only want performance.** A location carrying `mockServices` was refused
  without an ingress, full stop — and plenty of accounts have locations carrying
  both funcIds because somebody enabled them together, then run nothing but
  tests on them. In the web UI the Service virtualization switch was marked
  *required* and snapped straight back on, so there was no bundle to be had at
  all.

  The refusal stays, because unset means nobody answered and the failure it
  catches is invisible on a cluster. `none` is the answer: the bundle is the
  performance one — no ingress, no SV RBAC, no TLS secret — and `--format helm`
  works again, since there is nothing left for the chart to be missing. What it
  costs is stated rather than hidden: deploy a virtual service to such a
  location and it stalls at `WAITING_FOR_DOMAIN`, which is what the refusal was
  protecting you from. The images do not change — which set the agent runs is a
  fact about the location, not about this option.

  In the UI the switch now turns off, the row reads *declined* and says what was
  given up, and `profile.json` records `sv_ingress: none`, so re-importing a
  bundle does not land back on the refusal.

- **`bzm-opl-gen mcp` — an MCP server**, so an AI session can do the whole OPL
  deployment without a checkout of this repo: find the location, read its real
  image references, preflight a cluster from an evidence file, and write the
  manifests. `pipx install 'bzm-opl-gen[mcp]'`, then point your client at it —
  copy-paste config in [docs/mcp.md](docs/mcp.md).

  Five tools, each dispatching on an `action`, matching the shape the sibling
  BlazeMeter MCP servers already use: `opl_location`, `opl_facts`, `opl_bundle`,
  `opl_preflight`, `opl_agent`. The reference pages ship with the wheel and are
  served as resources, so a session can read the options table rather than guess
  at an option name.

  `opl_location list` answers one line per location and the first 50 of them,
  narrowable by `name_contains` and `limit`, with `show` for the agents of the
  one you pick. An account with 171 locations and 221 ships listed in full came
  to 84,779 characters — past a client's result ceiling, truncated to a file and
  never read, which blocked every step behind it. Anything the cap or the filter
  leaves out comes back as a count: a list that quietly stopped would read as
  the whole account.

  Three things it will not do. **The AUTH_TOKEN never appears in a response** —
  `generate` writes the Secret and answers with file names and byte counts, and
  reading a bundle file back redacts the token rather than handing it over,
  because a response is transcribed, summarised and quoted back, and this
  credential rotates every time it is issued. `reveal_token` is the one way to
  get the value, and it is a whole action so it cannot happen by accident;
  `generate` issues one only when asked, by name (see `rotate_token`, above).
  **A secret is never a tool argument** — passing `auth_token` in the options is
  refused rather than written; a path may be named, and the key itself comes
  from the server's environment. Files are named the same way: `opl_preflight`
  takes `evidence` as the path of the cluster-evidence JSON the customer sent as
  readily as the parsed object, so a session need not read several KB of node
  lists aloud to preflight one. **Nothing applies to a cluster** — `kubectl
  apply` stays in your shell, where you can see what is being applied. The one
  exception is `opl_agent livetest`, which deploys because that is all it does,
  and which is off by default.

  `opl_location delete` needs `BZM_OPL_ALLOW_DESTRUCTIVE=1` and `opl_agent
  livetest` needs `BZM_OPL_ENABLE_LIVETEST=1` — separate variables, because
  enabling one should not quietly enable the other. Both are read when the
  action runs, so setting one does not mean restarting your client. Image
  mirroring is annotated destructive but not gated: it adds images to a
  registry you named, where the worst case is repositories nobody wanted.

### Changed

- **`ui --dev` now detects a `BZM_API_KEY_FILE` set after startup.** The four
  paths an `api-key.json` is looked for were frozen at import, and `--dev` sets
  that variable for its reloader subprocess — which worked only because the
  subprocess re-imports. Read per call now. Nothing else about key detection
  changed, and the secret is still never read back out; only the key id is.

  This is the one behaviour change in an otherwise internal split: what the
  tool *does* moved to `bzm_opl_gen/core.py`, which imports no web framework,
  and `server.py` is the HTTP layer over it. Nothing in the API moved, and the
  same status codes come back from the same routes. It matters here because the
  ship a token is fetched for was decided in three places — the UI's download
  button, `generate --api-key` and `livetest` — and fetching a token rotates it,
  so the three disagreeing would have meant rotating a credential belonging to
  an agent nobody mentioned. One rule now, in one place.

- **Python 3.10 is now the floor**, up from 3.9 — which has been end-of-life
  since October 2025. The generator itself uses nothing newer; the bump is the
  `mcp` SDK's requirement, and it is being made now so the MCP layer lands on a
  supported floor rather than shifting it later.

- **`docs/options.md` is generated.** Every option now has a row, grouped by
  what it configures, where ten of the thirty-one keys previously had no
  documentation at all — including `namespace`, `run_as_user`, `tolerations`,
  `node_selector` and all three ephemeral-storage settings. The descriptions
  live in `bzm_opl_gen/options.py` and the doc is rebuilt from them with
  `python -m bzm_opl_gen.options`; editing a table cell by hand now fails the
  test suite, which also fails if an option is added to the generator and not
  to the registry. The prose sections around the table are still hand-written.

  The three CA options and the two engine-limit options used to share a table
  row each, which is why `ca_configmap_key` had nowhere to be documented; the
  "pick exactly one" that grouping carried is now stated above the section.

### Fixed

- **`images --pull` runs at all.** It raised `NameError: name 'dry' is not
  defined` on every invocation, with or without `--mirror`, with or without
  `--dry-run` — a guard at the end of the command tested a name that never
  existed, and it was evaluated unconditionally once `--pull` was given. Behind
  it sat a second bug: a `subprocess.run` on the loop variable *after* the loop,
  which would have re-run the last command on its own. Both are gone;
  `core.mirror_images` was always the thing doing the pull/tag/push, and the loop
  in the command only reports what it did. Nothing covered this path, which is
  how a crash on the happy path survived. Plain `images` (listing only) was never
  affected, and neither was the MCP `opl_bundle images` action.

- **An account that refuses to issue an agent credential now says so, and says
  what still works.** Some accounts serve the token endpoint only from
  BlazeMeter's own gateway and answer everything else `403 Forbidden: Should
  access from Private-Data gateway`. That raw body used to be the whole message:
  it names no ship, does not distinguish "the credential could not be issued"
  from "your request was wrong", and offers no way on — so `generate --api-key`
  and `create-ship` dead-ended on an account whose only real problem is that
  tokens have to come from the UI. The refusal now names the location and the
  ship, says which half failed, and points at `--auth-token`, which also stops
  the token being rotated. The upstream reason is still quoted.

- **A refusal on the command line is a sentence, not a traceback.** Anything
  `bzm-opl-gen` refuses deliberately is written for the person who ran the
  command, and `generate` had no guard around it — so on a refusing account the
  message above arrived under seventy lines of Python stack, which is a worse
  answer than the raw `403` it replaced. `main()` now renders any deliberate
  refusal and exits non-zero; `create-ship` still catches its own first, so the
  agent it just created is reported whatever the token endpoint answers.

### Added

- **`GET /api/option-docs`** — one line per option, plus its type, whether it
  accepts null, its choices and whether it is a credential. Kept separate from
  `/api/option-defaults`, whose every key is submitted back as an option.

### Changed

- **Crane's Kubernetes auto-updater is now OFF by default**
  (`AUTO_KUBERNETES_UPDATE: 'false'`), in both output formats and in the chart
  standalone. It was on for every bundle without a private registry, copied
  from BlazeMeter's own manual Kubernetes manifest, which ships `'true'`.

  On, it breaks the upgrade path of the thing that installed it. Crane takes
  field ownership of its own Deployment within seconds of install (manager
  `OpenAPI-Generator`), rewriting the image and `.spec.strategy` from `Recreate`
  to `RollingUpdate` — so the next `helm upgrade` fails on a field-ownership
  conflict with the ConfigMap already applied, and `--force-conflicts` cannot
  resolve it: forcing `type: Recreate` back leaves crane's
  `strategy.rollingUpdate` beside it and the API server rejects the pair.
  Changing anything meant uninstall + install. The documented fix was a value
  you had to set *before* installing, which nobody knew to do until the upgrade
  that failed. That is the whole reason for the change: a default that breaks
  its own upgrade path is not a default.

  **What it costs, and it is real:** the agent no longer updates itself.
  Keeping it current is now your job — re-generate and re-apply, or bump
  `image.tag` and `helm upgrade` — and an agent that falls far enough behind
  loses BlazeMeter support. Generated bundles say so: the ConfigMap, both
  READMEs and the chart's `values.yaml` all state it where the value is set.

  **To keep the old behaviour**, generate with `--auto-update` (option
  `auto_update: true`, "Agent auto-update → On" in the UI, `autoUpdate: true`
  in the chart), knowing upgrades then mean uninstall + install. Existing
  clusters are untouched until you re-apply; a `profile.json` from before this
  change has no `auto_update` key and so re-generates with the new default —
  re-apply the ConfigMap and restart crane to actually turn the updater off on
  a running agent.

### Added

- **Auto-update is now an option, in both output formats and the UI.**
  `AUTO_KUBERNETES_UPDATE` was decided entirely by the registry, with no way to
  say otherwise short of editing the ConfigMap after generating.
  `--auto-update` / `--no-auto-update` (option `auto_update`, "Agent
  auto-update" under Security & RBAC in the UI, `autoUpdate` in the chart) now
  set it either way, which is what made the default above a choice rather than
  a removal. The generated README gives whichever upgrade instruction matches
  the bundle, instead of one instruction that was wrong for half of them.

  This is BlazeMeter's Kubernetes auto-updater. Their `AUTO_UPDATE` is a
  different variable — documented as the Docker-side switch, inert on a
  Kubernetes agent — and nothing this generator emits sets it.

### Changed

- **Engines drop privileges on every platform, not just OpenShift.** The two
  ConfigMap keys that make crane stamp a security context on the pods it spawns
  — `INHERIT_RUNNING_USER_AND_GROUP` and `KUBERNETES_SECURITY_CONTEXT_CAP_JSON`
  — were emitted only for `platform=openshift`. Since `platform` defaults to
  `openshift`, the restricted engine was already what most bundles got; naming
  `k8s` quietly opted out of it and left crane's own default, which is a
  *privileged* engine pod. Restricted PodSecurity, OpenShift's restricted-v2 SCC
  and GKE Autopilot's Warden all refuse that — and refuse it after the agent is
  online and the location reads ready, so the run hangs at `BOOT_STARTING`
  rather than failing usefully. Nothing in those keys was ever
  OpenShift-specific. Verified against the images that have to tolerate it —
  the taurus engine, the doduo grid proxy and a charmander browser pod all
  observed from inside a running container with every capability set zero, and
  none of them needing one; see **Added** below. `--no-restrict-engines`
  restores the old behaviour for an image that genuinely needs a capability, at
  the cost of the posture on every container crane creates. `doctor` follows the
  option rather than the platform: `pod-security.kubernetes.io/enforce=restricted`
  is now a PASS, and a FAIL only when the restriction is turned off.

### Fixed

- **Browser images from a live GUI location now name repos that exist.** A
  Kubernetes agent reports them as keys with a path of their own —
  `blazemeter/charmander/chrome_136.0.7103.113` — and only the last segment was
  kept, so the repo came out as `.../blazemeter/chrome_136.0.7103.113`, which
  404s. With a private registry that is the failure the registry was configured
  to prevent: the mirror script pulls nothing, or, if the mirroring was done
  separately, `IMAGE_OVERRIDES` sends crane after an image nobody pushed and the
  location dies mid-test on an `ImagePullBackOff`. Losing `charmander` from the
  repo also cost the images their category — they came back `performance`, so a
  performance-only location selected four browsers it has no use for. Both are
  fixed by stripping only the redundant `blazemeter/` prefix. Flat keys and the
  irregular ones (`taurus-cloud`→`v4`, `blazemeter`→`v3`) are unchanged.
- **The crane pod now asks for the ephemeral storage it actually uses, and asks
  for it as one number.** The request was `100Mi` against a `1Gi` limit; crane
  reaches ~161MiB (107MiB of it `/tmp`) within seconds of starting, so the
  request never described the pod on any platform — elsewhere only the limit
  kept it alive. On GKE Autopilot, which rewrites the ephemeral-storage limit
  down to the request, the pod came back `100Mi/100Mi` and was evicted about
  twelve seconds into every start, indefinitely. Both fields are now `1Gi`, and
  `--crane-ephemeral-storage SIZE` moves them together — one value, because a
  gap between them is headroom on some platforms and a silent ceiling on
  others. CPU and memory are unaffected and unchanged.

### Added

- **[docs/hardened-engines.md](docs/hardened-engines.md) — which images have
  actually run under the hardened default.** The posture is a property of the
  pod spec, so re-running it on another cluster proves little; what varies is
  the image. Each image crane makes a pod from is recorded there with what was
  read *inside* a running container, including a browser pod driving a real
  Selenium session and an OpenShift run where the SCC, not `run_as_user`,
  assigned the UID. Nothing needed a capability — which matters before reaching
  for `--no-restrict-engines`, since it drops the posture for every container
  crane creates, not for the one image that wanted something.
  [docs/repro/hardened-posture-probe.yaml](docs/repro/hardened-posture-probe.yaml)
  re-runs the image half against any tag, with no account and no crane.
- **Choose the ServiceAccount the agent runs as, and whether to create it.**
  `--service-account <name>` (default `crane`, so existing bundles are
  unchanged) names the account the Deployment runs as and the one the
  RoleBinding — and the ClusterRoleBinding, with `--cluster-rbac` — grants to.
  `--no-create-service-account` leaves the ServiceAccount object out of the
  bundle for an account your platform team already owns; everything still
  references the name you gave. Both are in the web UI beside the namespace,
  and in the Helm chart as the `serviceAccount.create` / `serviceAccount.name`
  values it already had.
- **`scripts/bzm-cluster-evidence.sh`** — a read-only script to hand a customer
  whose cluster you have no access to. They run it, and one JSON file comes back
  carrying what a deployment has to be shaped around: nodes, ingress classes,
  the namespace, its LimitRanges/quotas/ServiceAccounts, which ingress API
  groups the cluster serves, the OpenShift ingress domain and cluster proxy, and
  `auth can-i` answers for everything the bundle applies. It is the cluster-side
  twin of `facts --manual`. Secrets are listed by name and type only — never
  `-o json`, so no secret value is ever in the output — and ConfigMaps by name,
  which also keeps a 300KB CA bundle out of the file. Anything unreadable is
  recorded as `null` with the error rather than as an empty list, because
  "denied" and "there are none" are different answers and `doctor` treats them
  differently.
- **`doctor --cluster-evidence <file>` preflights a cluster you have no access
  to**, from the JSON that script produced there — no cluster reachable, no
  kubeconfig configured. It runs the same checks over the same data and prints
  the same verdict list: the file carries the `kubectl get` documents `doctor`
  would have read, and they are normalised into exactly what the live path
  gathers, so nothing downstream knows which way the data arrived. The namespace
  defaults to the one the evidence was collected for, and preflighting a
  different one is reported rather than quietly used. Two things are reported as
  unverified rather than guessed: egress, which needs a pod inside the namespace
  to curl from, and any section the script was refused — those stay WARN ("we
  did not look") instead of becoming the FAIL an empty list means ("we looked,
  there are none"), so a file collected with little access exits 0 with warnings
  rather than a false alarm. A file whose `schema` is missing or unrecognised is
  refused by name.
- **`bzm-opl-gen suggest --cluster-evidence <file>`** — what a cluster's
  evidence implies about the generate options, with no cluster and no API key.
  `doctor` asks whether a deployment would survive a cluster; this answers the
  question that comes first, and writes the reasoning down instead of leaving it
  in whoever read the file. Each suggestion names the evidence behind it and how
  strongly it holds: **decisive** (the namespace already holds the ServiceAccount
  the bundle would create) or **suggestive** (the served API groups rule some
  `sv_ingress` values out without picking among the rest — narrowing to one
  survivor is still not choosing it). Covers `platform`, `service_account_create`
  / `service_account_name`, `sv_ingress`, `sv_subdomain`, `pull_secret`,
  `ca_existing_configmap`, `proxy`, `ca_openshift_inject` and `cluster_rbac`.
  Nothing is applied; `--json` emits the same as data.
- **Cluster preflight in the web UI.** Pick the file the collector wrote and see
  `doctor`'s verdicts against the configuration on screen, re-run as you edit it.
  `POST /api/preflight` needs no API key and no kubecontext — the same "no access
  to anything" path manual facts entry serves. The panel header says what was
  imported — collected when, the namespace the file *describes* as against the
  one being preflighted, and every section the collector was refused — and the
  same facts lead the verdict list, so a thin file cannot read as a clean bill
  of health. A file that is not evidence is refused by name and leaves the
  verdicts already on screen standing.
- **Apply what the evidence implies, one suggestion at a time.** Decisive
  suggestions offer their value as a single click; suggestive ones offer a button
  per candidate and never a default. **A value you already set is never
  overwritten silently** — the row turns amber, shows both values and the
  evidence behind the suggestion, and the button says *Replace*. Applying is
  reversible for the session, and an applied value is an ordinary option from
  there on: the bundle and `profile.json` are identical to what typing it gives.
- **`doctor` checks that an existing service account is really there.** Only
  when the bundle does not create one. Nothing fails at apply time if it is
  missing: the Deployment is accepted, no pod is ever created, and the reason
  is an event on the ReplicaSet.

### Fixed

- **The Helm chart no longer refuses `serviceType: NODEPORT` without
  `clusterRbac: true`.** The refusal rested on crane resolving its advertised
  address from the cluster-scoped Node object and falling back silently to
  `127.0.0.1` when denied. A live performance location on crane 3.7.55
  disproved it: deployed with NODEPORT and namespaced RBAC only — no ClusterRole
  in the cluster — the agent came online, crane created its NodePort Service
  through the namespaced Role, and a real engine ran a test to `ENDED`. Crane
  takes the address from its own network interfaces, and nothing in its log was
  forbidden. Corrected in `clusterrole.yaml` (both formats), the chart's
  `values.yaml` and its README; cluster-scoped node reads remain genuinely
  optional, for capacity awareness. The parity suite now covers NODEPORT
  *without* cluster RBAC — the combination the two formats disagreed on was
  tested in neither direction, which is how the disagreement survived. (#49)
- **`doctor` no longer fails a manually-entered location for `slots` and
  `threadsPerEngine`.** With no account to read them from, both are now reported
  unknown, naming Settings → Private Locations. A location gathered from the
  account with either genuinely unset still FAILs with the 403-at-start wording:
  the two are told apart by the `images_source` marker the facts already carry,
  so generated manifests are unaffected and nothing else downstream learns how
  the facts arrived. The no-account, no-cluster path — manual facts plus an
  imported evidence file — previously reported two failures for values nobody
  could have supplied. (#55)
- **`scripts/bzm-cluster-evidence.sh` no longer claims the cluster-scoped
  permission rows decide whether `serviceType: NODEPORT` is available.** Crane
  resolves its advertised address from its own network interfaces, not from the
  Node object, and NODEPORT has run green against a cluster where the agent had
  namespaced RBAC only — see #49. `suggest` will not draw that inference either.
- **Nothing is suggested from evidence the collector could not read.** A `null`
  section is skipped, but that alone is not enough: `auth can-i` and
  `api-resources` both report failure as *no*, so a file collected with no
  kubeconfig reads at face value as a plain Kubernetes cluster where nothing may
  be created — and would have produced `platform`, `cluster_rbac` and
  `service_account_create` about a cluster nobody described.
  `versions.serverVersion` is present only when a server actually answered, and
  without it `suggest` returns nothing and says why. `doctor` still reads such a
  file usefully: a warning about what could not be seen is worth having, a
  configuration guessed from it is not.

### Changed

- **Service virtualization: `--service-type NODEPORT` is now allowed with
  `--sv-ingress nginx` or `openshift`, and still refused with `contour` or
  `istio`.** It used to be refused for every backend, on the reasoning that
  NODEPORT forces a cluster-scoped Node read a namespaced Role cannot grant.
  That reasoning was wrong — crane's Node read is denied under NODEPORT on all
  four backends and two of them publish fine anyway. What actually decides it is
  the port crane writes into the object it publishes: `nginx` and `openshift`
  write a constant that stays valid, while `contour` and `istio` take the
  Service's **nodePort**, which nothing reaches the ingress on. Those two fail
  silently — object written, mock `1/1`, endpoint advertised, and contour
  answers 503 while istio's gateway listens on the nodePort alone — so the
  refusal stays for them, now with the measured reason. All four were deployed
  live to settle it. If you use `nginx` or `openshift`, NODEPORT is available in
  the CLI and the web UI, and an imported profile keeps whichever service type it
  arrived with instead of being rewritten to `CLUSTERIP`. Nothing about existing
  `CLUSTERIP` bundles changes. Details, including a crane-free reproduction of
  the contour case, in `docs/service-virtualization.md`.

- **`doctor` no longer fails a cluster for something it was not allowed to
  look at.** A `get` that is denied or errors — nodes, LimitRanges,
  ResourceQuotas, ServiceAccounts — now reports WARN "could not be read" for
  that check, where a denied `list nodes` previously produced the same
  "no eligible node — engines have nowhere to run" FAIL, and a non-zero exit, as
  a cluster that genuinely had none. Reading nothing and finding nothing are
  different answers; only the second is a failure.

- **Helm chart: `serviceAccount.name` is now required when
  `serviceAccount.create` is `false`, and the chart refuses to render without
  it.** It previously fell back to the namespace's `default` ServiceAccount —
  the usual chart scaffold, and wrong here: that installs cleanly and grants
  crane's Role to every other pod in the namespace that runs as `default`. If
  you install this chart with `serviceAccount.create: false` and no name, set
  the name to whichever account you meant. Bundles from `bzm-opl-gen generate`
  always carry an explicit name and are unaffected.

## [0.2.0] — 2026-07-27

Two things you could not do in 0.1.0: install the deployment as a **Helm chart**
rather than flat YAML, and **generate a bundle for an account you have no access
to**. Plus a real fix — `IMAGE_OVERRIDES` could come out empty for a location
with no running agent, which only shows up once the customer's cluster is
actually sealed.

### Removed

- **`sv-bridge` support.** The funcId is retired upstream, so it no longer
  selects an image, no longer appears in the create-location form, and no longer
  makes the service-virtualization ingress options mandatory. Locations that
  still carry the funcId now generate as ordinary performance locations — if you
  mirror images for one, the `sv-bridge` image is no longer in the set.
- **Web UI: the `sv-expose` panel**, and the `POST /api/sv-expose` endpoint
  behind it. It asked for an ingress class most people cannot judge, on a screen
  that appeared whether or not the cluster had the problem it solves. The
  `bzm-opl-gen sv-expose` **command is unchanged**; the endpoint check below is
  what now tells you when you need it.

### Added

- **Helm chart output** — `generate --format helm` emits the same deployment as
  a chart (`out/helm/`, byte-identical for every customer) plus a values overlay
  (`out/bzm-opl-values.yaml`, the only file generated from the account), instead
  of flat manifests. Both formats render the same objects; a parity check renders
  17 option combinations both ways and requires them to agree, so the choice is
  about how you install and upgrade, not what lands in the cluster. Set
  `autoUpdate: false` in the overlay if you intend to run `helm upgrade` — left
  on, crane takes ownership of its own Deployment and the next upgrade fails
  half-applied on a field-ownership conflict. Both behaviours were confirmed
  against a live cluster and a real agent. Service virtualization is refused in
  this format rather than emitted broken, and `livetest` does not take a chart
  directory. See [docs/helm.md](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/helm.md).
- **Generate for an account you cannot reach** — the three values BlazeMeter
  shows on an agent (harbor id, ship id, AUTH_TOKEN) are enough to render every
  manifest, so a customer's deployment can be produced with access to neither
  their BlazeMeter account nor their cluster. `bzm-opl-gen facts --manual
  --harbor-id H --ship-id S`, or **Enter values manually** in the web UI. Nothing
  is validated and nothing is sent to BlazeMeter. What you give up is listed in
  the README — chiefly that the crane tag floats on `latest`, and that GUI
  browser images cannot be resolved without a live agent.
- **Web UI: the deployed virtual services, beside the heartbeat** — while
  watching an SV deployment, each one is listed with the endpoint host it
  publishes, refreshed on the existing poll. The agent reports idle whether or
  not its virtual services ever became reachable, so a deploy stalled at
  `WAITING_FOR_DOMAIN` used to look identical to a healthy one. Needs a
  kubecontext like `sv-expose` does; without one the panel still watches the
  heartbeat and says why the list is absent.
- **Web UI: configure one feature at a time.** The configure step shows the
  selected feature's options plus the ones that apply to any deployment. It is a
  view, not a scope — the manifests still come from the location's own funcIds,
  so nothing set under another feature is lost or omitted. Options set out of
  view are listed beside the preview; required ones missing from view block the
  download with a link to the feature that needs them. The feature list is
  served, so functional testing, secrets or API monitoring become selectable
  without a UI release.
- **Web UI: picking and creating are separate.** Starting to create a location
  or an agent hides the list of existing ones until you finish or cancel, so it
  is never ambiguous which of the two you are doing — they have very different
  consequences when an agent identity is already running somewhere.
- **Web UI: check whether a published endpoint answers.** Beside the virtual
  services in the watch panel, a check reports the HTTP status or which kind of
  failure it was. A 503 is the diagnosis rather than a broken check: it is the
  cluster refusing crane's port reference, and it names `sv-expose` as the fix.
- **Web UI: the SV prerequisites the bundle does not create** — wildcard TLS
  secret, Istio Gateway, the controller — now say who provides each one and what
  the chosen backend actually does with it, alongside the endpoint host to check
  after applying. Previously README-only, while the failure it prevents is
  silent: manifests apply, agent goes idle, mock runs 1/1, every deploy hangs at
  `WAITING_FOR_DOMAIN`.
- **Web UI: every funcId a location can be created with** is served from the
  generator rather than copied into the frontend, so `proxyRecorder` can be
  selected at last — the hardcoded list omitted it.
- **`ui --host`** — bind the web UI to something other than loopback, for when
  the machine running it is not the machine you are sitting at. The default is
  unchanged (`127.0.0.1`) and a widened bind warns at startup: the server holds
  your API key in process memory, and downloading a bundle rotates the
  AUTH_TOKEN out from under any agent already running for that ship. An SSH
  tunnel to the default bind does the same job without exposing the listener.

### Fixed

- **`IMAGE_OVERRIDES` came out empty for a location with no live agent.** The
  built-in image catalogue held only the two performance images, so a
  `mockServices` or `proxyRecorder` location generated overrides that covered
  nothing — and crane resolves a missing key against the *public* registry
  silently, so the bundle looks correct right up until the customer's cluster is
  actually sealed. The catalogue now covers mock, recorder and doduo. GUI browser
  images remain uncoverable without a running agent (60+ version-pinned repos,
  and only the agent says which one a location uses); that is flagged, and
  escalated to a warning when a private registry is set.
- **A Kubernetes agent's image inventory was being discarded.** k8s agents report
  bare keys (`taurus-cloud:latest`) where Docker agents report registry-qualified
  tags, and only the Docker shape was handled — so every k8s agent, which is the
  kind this tool generates for, silently produced no inventory and fell back to
  the catalogue. Reading it properly also pins exact tags where the catalogue
  could only say `latest`.

### Changed

- **The README is now short**, covering what the tool is, how to install it and
  how to get a bundle out. The reference material it used to carry — every
  option, the web UI, Helm, service virtualization, preflight, the live rig — is
  in [`docs/`](https://github.com/benjithompson/bzm-opl-generator/blob/main/docs/), linked from a table in the README.

## [0.1.0] — 2026-07-26

First packaged release.

### Added

- **`generate`** — renders private-location manifests for Kubernetes and
  OpenShift from a location's real account facts, so the features it actually
  has decide which images ship and what `IMAGE_OVERRIDES` a private registry
  needs. Scenario presets cover the standard, private-registry and proxy/CA
  postures.
- **`facts`** — reads a location's enabled features, agents and live image
  inventory straight from the account, instead of you transcribing them.
- **`doctor`** — preflights a target cluster before anyone waits on a stuck
  run: capacity, quota, LimitRange, admission (PSA/SCC), ingress class and
  egress. Exits non-zero on anything that would stop a test from starting.
- **`toolcheck`** — preflights your own machine against the live rig's
  requirements, so a missing tool fails in seconds rather than 15 minutes in.
- **`livetest`** — deploys the generated manifests and waits for the agent to
  report online. Optional rigs reproduce the awkward customer environments
  locally: air-gapped registry, proxy with a custom CA, default-deny egress,
  and a real engine run.
- **`images`** — lists, pulls and mirrors a location's images to a private
  registry.
- **Web UI** (`bzm-opl-gen ui`) — connect, pick or create a location,
  configure, preview the manifests live and download them as a zip. Ships
  prebuilt in the wheel; no Node toolchain needed.
- **Service virtualization** — ingress configuration for istio, contour, nginx
  and OpenShift routes, plus `sv-expose` for reaching a virtual service where
  crane's own nginx Ingress doesn't resolve.
