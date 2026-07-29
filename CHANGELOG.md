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

  Three things it will not do. **The AUTH_TOKEN never appears in a response** —
  `generate` writes the Secret and answers with file names and byte counts,
  because a response is transcribed, summarised and quoted back, and this
  credential rotates every time it is fetched. `reveal_token` is the one
  exception and is a whole action so it cannot happen by accident. **A secret is
  never a tool argument** — a path may be; the key comes from the server's
  environment. **Nothing writes to a cluster** — `kubectl apply` stays in your
  shell, where you can see what is being applied.

  `opl_location delete` and image mirroring need `BZM_OPL_ALLOW_DESTRUCTIVE=1`;
  `opl_agent livetest` needs `BZM_OPL_ENABLE_LIVETEST=1`. Both are read when the
  action runs, so setting one does not mean restarting your client.

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
