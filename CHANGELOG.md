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
- **`doctor` checks that an existing service account is really there.** Only
  when the bundle does not create one. Nothing fails at apply time if it is
  missing: the Deployment is accepted, no pod is ever created, and the reason
  is an event on the ReplicaSet.

### Changed

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
