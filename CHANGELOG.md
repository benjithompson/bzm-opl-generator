# Changelog

All notable changes to bzm-opl-gen are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

Group each release's notes under **Added**, **Changed**, **Fixed**, **Removed**
or **Security**, and drop any section that would be empty. Write entries for
the person upgrading: what changed for them, not which files moved. Lead with
anything that breaks.

## [Unreleased]

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
