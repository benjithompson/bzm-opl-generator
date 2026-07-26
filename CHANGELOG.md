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

- **Web UI: pick what the bundle deploys** — a performance agent or a
  service-virtualization agent — before choosing a location. The SV kind seeds
  the create-location funcIds, defaults the namespace to `blazemeter-sv`, and
  turns the SV options on; locations are labelled with the kind their funcIds
  imply, and one carrying both is flagged with the namespace/slot/lifecycle
  coupling it brings. Advisory only — any location can still be generated for
  either kind, and the generator gained no new rejection.
- **Web UI: the SV prerequisites the bundle does not create** — wildcard TLS
  secret, Istio Gateway, the controller — now say who provides each one and what
  the chosen backend actually does with it, alongside the endpoint host to check
  after applying. Previously README-only, while the failure it prevents is
  silent: manifests apply, agent goes idle, mock runs 1/1, every deploy hangs at
  `WAITING_FOR_DOMAIN`.
- **Web UI: `sv-expose` from the browser**, rendering the Service+Ingress pair
  into the same preview, with the ingress class settable for the first time
  outside the CLI. Reading the cluster is optional and the only thing in the UI
  that needs one: no CLI, no context, denied and no-virtual-services are
  reported as distinct outcomes, each with the equivalent command prefilled to
  run elsewhere.
- **Web UI: every funcId a location can be created with** is served from the
  generator rather than copied into the frontend, so `sv-bridge` (and
  `proxyRecorder`) can be selected at last — the hardcoded list omitted both.
- **`ui --host`** — bind the web UI to something other than loopback, for when
  the machine running it is not the machine you are sitting at. The default is
  unchanged (`127.0.0.1`) and a widened bind warns at startup: the server holds
  your API key in process memory, and downloading a bundle rotates the
  AUTH_TOKEN out from under any agent already running for that ship. An SSH
  tunnel to the default bind does the same job without exposing the listener.

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
