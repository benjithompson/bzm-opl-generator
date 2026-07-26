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

## The two test layers

**Offline** (`pytest tests -q`) — stdlib + fixtures, no cluster, ~1s. Every
check the live rig performs has an offline counterpart that fakes the cluster
or API response, so failure modes are covered without burning 15 minutes.
**Add one whenever you add a live check.** This is the rule that keeps the
suite worth running.

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

## Working against the BlazeMeter account

Live runs touch a real account, so:

- **Creating or starting anything is a real write.** Agree on the artifact
  first rather than discovering a colleague's location repurposed.
- **Create scratch locations**; don't reuse someone else's harbor.
- If a run repoints an existing test, it must restore the original
  `executions` — the code does this in a `finally` and prints the original
  first. Verify afterwards; CLAUDE.md has the one-liner.
- Leave the account clean: check for stray namespaces, containers, and
  minikube profiles when a run is interrupted.

## Pull requests

Everything lands on `main` through a PR. Enable the guard once per clone:

```
git config core.hooksPath .githooks
```

`.githooks/pre-push` then refuses a push whose target is `main` and tells you
what to do instead. It is client-side, so it catches the reflex `git push` from
a branch you forgot you were on — not a determined `--no-verify`. Treat it as a
seatbelt, not a lock.

- Comments explain **why**, especially where a non-obvious environment fact
  drove the code. Match the surrounding density; don't narrate the obvious.
- CI runs the offline suite on Python 3.9 (the declared floor) and 3.13, and
  generates from the sample facts. Both must be green.
- If you change what a live check proves, update its offline counterpart and
  the relevant section of README.md in the same PR.

## Cutting a release

Users install from a GitHub Release, not from git — the repo is private, so a
`pip install git+https://…` needs credentials that `gh release download`
handles for them.

On a PR like anything else: bump `version` in `pyproject.toml`, and move your
entries from `## [Unreleased]` into a new `## [x.y.z] — YYYY-MM-DD` section of
`CHANGELOG.md`. Then tag:

```
git tag v0.1.1 && git push origin v0.1.1
```

`.github/workflows/release.yml` runs the offline suite, builds the wheel,
checks it carries the templates/profiles/UI bundle, and publishes it. It
refuses to release if:

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
| `README.md` | what the tool does and every flag — user-facing |
| `CONTRIBUTING.md` | this file: setup, test layers, PR flow |
| `CLAUDE.md` | live-rig internals, account facts, the traps behind each flag |
| `docs/` | write-ups of specific findings (e.g. crane's nginx Ingress port) |
