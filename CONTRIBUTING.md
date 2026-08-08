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
facts drive the output — `func_ids` decides which images are dead weight and
drop out of `IMAGE_OVERRIDES`.

The web UI ships prebuilt in `bzm_opl_gen/ui_dist/`, so nothing above needs npm.
Changing it does: `cd frontend && npm install`, and `npm run build` is what
refreshes what the wheel carries ([docs/web-ui.md](docs/web-ui.md)). **Commit
the rebuild with the source change.** A build records a fingerprint of the
sources it was built from, and `tests/test_ui_build.py` recomputes it — so a
`frontend/src` edit committed without a rebuilt `ui_dist` fails the offline
suite and names the command, rather than shipping a page older than the code
behind it.

## Layout

```
bzm_opl_gen/
  api.py         BlazeMeter API client (stdlib)
  facts.py       account fact gathering + image classification
  generate.py    manifest rendering (templates/ + per-option assembly)
  doctor.py      cluster preflight (pure verdicts over fetched cluster JSON)
  suggest.py     what a cluster's evidence implies about the generate options
  workstation.py workstation preflight for the live rig (`toolcheck`)
  quantity.py    k8s CPU/memory quantities as numbers (sizing arithmetic)
  plan.py        a load target -> engines, nodes, and a request document (reaches nothing)
  livetest.py    deploy, poll-until-online, teardown
  options.py     what each generate option is for (docs/options.md renders it)
  evidence.py    the cluster-evidence document's section names, stated once
  service.py     the macOS LaunchAgent `ui --install-service` writes
  ui_build.py    the fingerprint a frontend build records, and how to recompute it
  core.py        orchestration, transport-free -- no fastapi, no request objects
  server.py      HTTP over core.py: routes, request models, the web UI's bind
  mcp_server.py  MCP over core.py: six action-dispatch tools, docs as resources
  cli.py         subcommands: plan | facts | generate | doctor | suggest | toolcheck
                              | images | livetest | ui | mcp | sv-expose | locations
                              | create-location | create-agent | delete-location
  templates/     per-CRD best-practice templates, plus templates/helm/ (the chart)
  profiles/      option profiles (standard | private-registry | proxy-ca)
  ui_dist/       prebuilt web UI, shipped in the wheel; a build records the
                 sources it was built from in source-fingerprint.json
frontend/        web UI source (React); `npm run build` refreshes ui_dist/
tests/           offline unit tests (fixture facts), plus helm_parity.py
docs/            user-facing reference split out of README.md
examples/        sample facts + api-key placeholder (the no-account path)
```

## The test layers

**Offline** (`pytest tests -q`) — stdlib + fixtures, no cluster, ~3s. Every
check the live rig performs has an offline counterpart that fakes the cluster
or API response, so failure modes are covered without burning 15 minutes.
**Add one whenever you add a live check.** This is the rule that keeps the
suite worth running.

**Helm parity** (`python tests/helm_parity.py`) — renders 28 option combinations
as both `--format manifests` and `--format helm` and requires the same objects
out of each. Deliberately not pytest, and its own CI job: it shells out
to `helm`, and a test that skips when a binary is missing is the `fastapi`
problem above. Every judgement in `templates/*.yaml` is restated in Go templates
and nothing else notices one drifting, so **touch either and run this**.

**Frontend** (`cd frontend && npm test && npm run typecheck`) — the third CI
job. Logic lives in plain modules with their own suites and the components wire
them; `noUnusedLocals` is on, so a binding left behind by a refactor fails the
typecheck rather than accumulating.

**Live rig** (`bzm-opl-gen livetest`) — deploys generated manifests to a local
cluster and waits for the agent to report online in a real BlazeMeter account.
Runs take **12–20 minutes**. Before starting one:

```
bzm-opl-gen toolcheck --cluster minikube --local-registry 5001 --local-proxy
```

`toolcheck` preflights *your machine* (kubectl/oc, docker, kind/minikube, disk,
arch, the pinned rig images) against the flags you intend to pass — the things
that otherwise surface as a traceback several minutes in. `doctor` is the
different question: can a *cluster* run the location's concurrency.

What each rig flag proves, and the environment trap behind it, is in
[CLAUDE.md](CLAUDE.md). Read that before your first live run; several of the
behaviours look like bugs and are not.

## Working against a BlazeMeter account

**You need no account to contribute.** `examples/facts.example.json` drives the
generator, the offline suite, the helm parity check and the frontend tests, and
CI runs all four without one. Only `bzm-opl-gen livetest` needs an account, and
only because deploying to a real cluster and waiting for the agent to come
online is the whole of what it does.

If you do run the rig, run it against **your own** account, and treat it as
production, because it is somebody's:

- **Creating or starting anything is a real write.** Decide which artifact a run
  is allowed to touch before it starts, rather than discovering afterwards that
  it repurposed one somebody depended on.
- **Create a scratch private location for it**, rather than pointing it at one
  that already has a job.
- If a run repoints an existing test, it must restore the original
  `executions` — the code does this in a `finally` and prints the original
  first. Verify afterwards; CLAUDE.md has the one-liner.
- Leave the account clean: check for stray namespaces, containers, and
  minikube profiles when a run is interrupted.

Never put an account name, an account id, a harbor or ship id, or an AUTH_TOKEN
in a commit, a test fixture, a comment or an issue. `examples/facts.example.json`
holds the obviously-fake vocabulary to reach for instead.

## Pull requests

Everything lands on `main` through a PR. **Fork it, branch, open the PR** —
that is the whole flow, and nothing here needs write access to this repo.

Working from a clone you can push to instead? Enable the guard once:

```
git config core.hooksPath .githooks
```

`.githooks/pre-push` then refuses a push whose target is `main` and tells you
what to do instead. It is client-side, so it catches the reflex `git push` from
a branch you forgot you were on — not a determined `--no-verify`. Treat it as a
seatbelt, not a lock. Contributors working from a fork can skip it: pushing to
your own fork's `main` costs nobody anything.

- Comments explain **why**, especially where a non-obvious environment fact
  drove the code. Match the surrounding density; don't narrate the obvious.
- CI is three jobs and all of them must be green: the offline suite on Python
  3.10 (the declared floor) and 3.13, which also generates the manifests and the
  chart from the sample facts; helm parity, which lints the chart first; and the
  frontend's tests and typecheck. **No run at all is not a pass** — push and
  pull-request events are webhooks and can be dropped, and re-pushing an
  unchanged ref emits nothing. Start one by hand instead:
  `gh workflow run tests.yml --ref <branch>`.
- If you change what a live check proves, update its offline counterpart and
  the relevant page under `docs/` in the same PR.
- Add your entry to `## [Unreleased]` in `CHANGELOG.md` — that section is what
  the next release's notes are cut from, so anything missing from it is missing
  from the release.

## Cutting a release

**The tag is the release**, and it fans out to PyPI (the front door), the wheel
attached to the GitHub Release, and the git URL at that tag. The notes carry the
command people paste, from `.github/release-footer.md`, whose `VERSION`
placeholder the workflow replaces with the tag.

On a PR like anything else: bump `version` in `pyproject.toml`, move your entries
from `## [Unreleased]` into a new `## [x.y.z] — YYYY-MM-DD` section with a
compare link, and update the version pinned in `README.md` and `docs/mcp.md` —
`tests/test_install_docs.py` fails if those disagree. Then tag:

```
git tag v0.3.2 && git push origin v0.3.2
```

**If no run appears, the webhook was dropped**, which is a thing that happens
when Actions is degraded. Pushing again does nothing — the remote ref already
matches, so there is no change to emit an event for. Start it through the API
instead, which needs no webhook:

```
gh workflow run release.yml --ref v0.3.2
```

Dispatch it on the **tag**, never a branch: the version comes from the ref's
name, and the workflow refuses anything that is not a tag.

`.github/workflows/release.yml` runs the offline suite, builds the wheel,
checks it carries the templates, the profiles, the UI bundle and every page
under `docs/` — the MCP server serves those as resources, so a missing one is a
reference an installed copy does not have — and publishes it. It refuses to
release if:

- the tag disagrees with `pyproject.toml`'s version — the tag is what users
  pin to, so the two disagreeing is worse than no release;
- `CHANGELOG.md` has no section for that version.

Release notes are the CHANGELOG section plus `.github/release-footer.md`,
which carries the install instructions so every release reads the same. Notes
are never generated from commit subjects: they're for the person upgrading,
and nobody writes those by accident.

## Where things are documented

| | |
|---|---|
| `README.md` | what the tool is, how to install it, how to get a bundle out — deliberately short |
| `docs/` | the user-facing reference: options, web UI, Helm, SV, preflight, capacity planning, the live rig, plus write-ups of specific findings |
| `CONTRIBUTING.md` | this file: setup, layout, test layers, PR flow, releases |
| `CLAUDE.md` | live-rig internals, account facts, the traps behind each flag |

README.md is kept to what a new user needs to install and produce a bundle.
Reference detail goes in `docs/`, linked from the README's documentation table —
add the link when you add a page, or nothing will find it.
