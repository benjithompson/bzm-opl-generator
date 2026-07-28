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

- **Service virtualization no longer forces `CLUSTERIP`.** `--sv-ingress`
  together with `--service-type NODEPORT` used to be refused, on the reasoning
  that NODEPORT sends crane to the cluster-scoped Node object a namespaced Role
  cannot grant. It was run: on minikube (k8s 1.32, ingress-nginx v1.11.3) with
  crane 3.7.55 and a namespaced Role only, the virtual service deployed,
  BlazeMeter published `http://<vs>-8080-<ns>.<subdomain>`, and all three
  transactions answered there. Crane's Node read *is* denied once a virtual
  service is deployed — it logs the 403 and falls back to `127.0.0.1` — but that
  address belongs to its Service pool, which the ingress path never consults.
  The warning is expected there and not a symptom. The web UI offers NODEPORT
  with SV on,
  and an imported profile keeps whichever service type it arrived with instead
  of being rewritten to `CLUSTERIP`. Nothing about existing `CLUSTERIP` bundles
  changes. Details in `docs/service-virtualization.md`.

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
