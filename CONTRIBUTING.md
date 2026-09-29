# Contributing

## Setup

```
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests -q
```

The run must end **`N passed`** with nothing skipped. `[dev]` is `[test]` +
`[ui]`; install less than that and `tests/test_server.py` import-skips itself,
so the suite reports a clean pass having tested none of the HTTP layer.

You need no BlazeMeter account to work on the generator:

```
.venv/bin/bzm-opl-gen generate --facts examples/facts.example.json \
    --namespace demo -o out/
```

Editing `examples/facts.example.json` is the fastest way to see how account
facts drive the output — `func_ids` decides which images drop out of
`IMAGE_OVERRIDES`.

## Layout

```
bzm_opl_gen/
  api.py              BlazeMeter API client (stdlib)
  facts.py            account fact gathering + image classification
  core.py             orchestration, transport-free -- no fastapi, no request objects
  cli.py              the bzm-opl-gen subcommands (`bzm-opl-gen --help`)
  server.py           HTTP over core.py: routes, request models, the web UI's bind
  mcp_server.py       MCP over core.py: six action-dispatch tools, docs as resources
  options.py          what each generate option is for (docs/options.md renders it)
  agent_env.py        BlazeMeter's documented agent environment variables
  plan.py             a load target -> engines, nodes, and a request document (reaches nothing)

  generate.py         generate(): facts + options -> bundle files; profile.json; token read-back
  bundle_options.py   option defaults, what each format ignores, engine sizing readers
  bundle_names.py     file and object names a bundle emits
  footprint.py        BlazeMeter's published footprint and fixed hosts
  markers.py          the <KEY> marker a blank required field resolves to
  required_fields.py  which fields a bundle requires, and where each value comes from
  service_virt.py     service virtualization: ingress backends, docker hostname/TLS
  ca_trust.py         CA trust modes and the ConfigMap/file each names
  bundle_env.py       the agent environment a bundle writes, and the reserved names
  image_registry.py   image destinations and the mirror script
  nodepools.py        the nodepools.md recipe for split engine pools
  quoting.py          how a value is written into YAML, compose or shell
  readme_parts.py     README fragments shared by the three formats
  render_manifests.py / render_helm.py / render_docker.py   one renderer per format

  doctor.py           cluster preflight (pure verdicts over fetched cluster JSON)
  suggest.py          what a cluster's evidence implies about the generate options
  evidence.py         the cluster-evidence document's section names, stated once
  verdict.py          PASS/WARN/FAIL vocabulary and report shared by doctor and toolcheck
  workstation.py      workstation preflight for the live rig (`toolcheck`)
  kube.py             subprocess and kubectl primitives
  livetest.py         the live rig: deploy, poll-until-online, teardown
  bundle_check.py     is a directory the bundle a live run was told to test?
  sv_read.py          virtual services deployed in a namespace, read off the pods
  cert.py             certificate names (the only `cryptography` importer)
  quantity.py         k8s CPU/memory quantities as numbers
  service.py          the macOS LaunchAgent `ui --install-service` writes
  ui_build.py         the fingerprint a frontend build records, and how to recompute it
  templates/          per-object templates, plus templates/helm/ (the chart)
  profiles/           option profiles (standard | private-registry | proxy-ca)
  ui_dist/            prebuilt web UI, shipped in the wheel
frontend/             web UI source (React); `npm run build` refreshes ui_dist/
scripts/              bzm-cluster-evidence.sh, the read-only collector a customer runs
tests/                offline tests (fixture facts), plus helm_parity.py
docs/                 user-facing reference, shipped in the wheel and served over MCP
examples/             sample facts + api-key placeholder (the no-account path)
```

## The test layers

**Offline** (`pytest tests -q`) — stdlib + fixtures, no cluster, ~10s. Every
check the live rig performs has an offline counterpart that fakes the cluster
or API response. **Add one whenever you add a live check.**

**Helm parity** (`python tests/helm_parity.py`) — renders every option
combination in its `CASES` table as both `--format manifests` and
`--format helm` and requires the same objects out of each. Not pytest, and its
own CI job: it shells out to `helm`, and a test that skips when a binary is
missing would report a pass having tested nothing. Every judgement in
`templates/*.yaml` is restated in Go templates, so **touch either and run
this**. `tests/test_helm.py` is its offline counterpart.

**Docker compose** — a docker bundle's `compose.yaml` and `bzm-opl-agent.sh` are
checked twice, for different questions:

| check | where | what it answers |
|---|---|---|
| **parity** | `tests/test_generate.py::test_compose_and_docker_run_describe_the_same_container` | do the two files describe the *same container* — image, environment, mounts, user, network mode, restart policy, workdir, command? |
| **validity** | the `docker` job in `.github/workflows/tests.yml` | does `docker compose config -q` accept the file? |

Parity runs over `helm_parity.py`'s option matrix plus the docker-only
branches. Two differences are representation and are undone before comparing:
compose doubles every `$`, and the split credential is in neither inline set.
The one licensed difference is a blank field — the marker in the script,
`${...:?}` in compose — asserted in both directions. Validity covers the default
shape, the branches it does not render (token inline, CA mount, private
registry), and a negative case: a bundle with a blank field must be *refused*.

The docker format has no upstream compose file to compare against, and its
`docker run` shape must match **the command BlazeMeter's API returns**
(`POST .../docker-command`), not the documentation pages — the pages omit `-u 0`
and `DOCKER_PORT_RANGE`.

**Frontend** (`cd frontend && npm test && npm run typecheck`) — logic lives in
plain modules with their own suites and the components wire them;
`noUnusedLocals` is on, so a binding left behind by a refactor fails the
typecheck.

**Live rig** (`bzm-opl-gen livetest`) — deploys a bundle to a local cluster (or
starts a docker bundle with compose) and waits for the agent to report online in
a real BlazeMeter account. Runs take **12–20 minutes**. Before starting one:

```
bzm-opl-gen toolcheck --cluster minikube --local-registry 5001 --local-proxy
```

`toolcheck` preflights *your machine* against the flags you intend to pass.
What each rig flag proves is in [docs/live-test.md](docs/live-test.md), and the
environment traps behind them in [LIVE_RIG.md](LIVE_RIG.md).

## The web UI build

The web UI ships prebuilt in `bzm_opl_gen/ui_dist/`, so nothing above needs npm.
Changing it does:

```
cd frontend && npm install
npm run dev        # proxies /api to :8765
npm run build      # refreshes bzm_opl_gen/ui_dist/
```

**Commit the rebuild with the source change.** A build records a fingerprint of
its sources in `ui_dist/source-fingerprint.json`, and `tests/test_ui_build.py`
recomputes it, so a `frontend/src` edit committed without a rebuilt `ui_dist`
fails the offline suite and names the command.

A server running from a checkout compares the same fingerprint at startup and
serves the answer as `/api/build`, which the page shows as a banner. `stale`
has four values:

| `/api/build` | meaning | what you see |
|---|---|---|
| `true` | the page was not built from these sources | an amber banner, and `!!` lines at startup |
| `false` | compared, and it matches | nothing |
| `"unrecorded"` | the page records no fingerprint, so this was not checked | a plain note naming the rebuild |
| `null` | no `frontend/` — an installed wheel | nothing |

A stale page is worth the banner: a route the page needs answers `404`, which
the page reads as *not read yet*, and it then offers options a format hides.
`ui --install-service` runs with `--dev`, so backend changes take effect without
a restart but the built page does not.

## Working against a BlazeMeter account

**You need no account to contribute.** `examples/facts.example.json` drives the
generator, the offline suite, the helm parity check and the frontend tests, and
CI runs all of them without one. Only `bzm-opl-gen livetest` needs an account.

If you do run the rig, run it against **your own** account, and treat it as
production:

- **Creating or starting anything is a real write.** Decide which artifact a run
  may touch before it starts.
- **Create a scratch private location for it**, rather than pointing it at one
  that already has a job.
- If a run repoints an existing test, it restores the original `executions` in a
  `finally` and prints the original first. Verify afterwards; LIVE_RIG.md has the
  post-run checks.
- Leave the account clean: check for stray namespaces, containers and minikube
  profiles when a run is interrupted.

Never put an account name, an account id, a harbor or agent id, or an AUTH_TOKEN
in a commit, a test fixture, a comment or an issue. `examples/facts.example.json`
holds obviously fake values to use instead.

## Pull requests

Everything lands on `main` through a PR. **Fork it, branch, open the PR.**

Working from a clone you can push to? Enable the guard once:

```
git config core.hooksPath .githooks
```

`.githooks/pre-push` then refuses a push whose target is `main`. It is
client-side — a seatbelt, not a lock.

- Comments state current behaviour and, where it is not obvious, the reason —
  in one to three lines. No history: the commit log and CHANGELOG hold that.
- CI is four jobs and all must be green: the offline suite on Python 3.10 (the
  declared floor) and 3.13; helm parity, which lints the chart first; the docker
  compose validity check; and the frontend's tests and typecheck. **No run at
  all is not a pass** — webhooks can be dropped, and re-pushing an unchanged ref
  emits nothing. Start one by hand: `gh workflow run tests.yml --ref <branch>`.
- If you change what a live check proves, update its offline counterpart and
  the relevant page under `docs/` in the same PR.
- A new option needs a row in `bzm_opl_gen/options.py`; regenerate
  `docs/options.md` with `python -m bzm_opl_gen.options`.
- Add your entry to `## [Unreleased]` in `CHANGELOG.md` — the next release's
  notes are cut from it.

## Cutting a release

**The tag is the release**, and it fans out to PyPI, the wheel attached to the
GitHub Release, and the git URL at that tag. The notes carry the install command
from `.github/release-footer.md`, whose `VERSION` placeholder the workflow
replaces with the tag.

On a PR like anything else: bump `version` in `pyproject.toml`, move your entries
from `## [Unreleased]` into a new `## [x.y.z] — YYYY-MM-DD` section with a
compare link, and update the version pinned in `README.md` and `docs/mcp.md` —
`tests/test_install_docs.py` fails if those disagree. Then tag:

```
git tag v0.3.2 && git push origin v0.3.2
```

**If no run appears, the webhook was dropped.** Pushing again does nothing, as
the remote ref already matches. Dispatch it on the **tag** instead:

```
gh workflow run release.yml --ref v0.3.2
```

`.github/workflows/release.yml` runs the offline suite, builds the wheel,
checks it carries the templates, the profiles, the UI bundle and every page
under `docs/` (the MCP server serves them as resources), and publishes it. It
refuses to release if the tag disagrees with `pyproject.toml`'s version, or if
`CHANGELOG.md` has no section for that version.

Release notes are the CHANGELOG section plus `.github/release-footer.md`, never
generated from commit subjects.

## Where things are documented

| | |
|---|---|
| `README.md` | what the tool is, how to install it, how to get a bundle out — deliberately short |
| `docs/` | the user-facing reference: options, web UI, Helm, docker, SV, preflight, capacity planning, the live rig. Shipped in the wheel and served to MCP sessions, so write it for a customer |
| `CONTEXT.md` | the glossary |
| `CONTRIBUTING.md` | this file: setup, layout, test layers, PR flow, releases |
| `CLAUDE.md` | what a coding session needs: tests, account guardrails, architecture, invariants |
| `LIVE_RIG.md` | live-rig internals: the run command, the traps behind each flag, post-run checks |

Add a new `docs/` page to the README's documentation table, or nothing will find
it.
