<!-- What changed, and why. The commit subject is the headline; this is the
     part somebody reads when they hit the change a year from now. -->

## What this changes

## Why

<!-- Closes #123 -->

## Tests

Which layers you ran (CONTRIBUTING.md explains what each one covers):

- [ ] `pytest tests -q` — ends `N passed`, nothing skipped
- [ ] `python tests/helm_parity.py` — if you touched `templates/`
- [ ] `cd frontend && npx vitest run && npx tsc --noEmit` — if you touched `frontend/`
- [ ] `bzm-opl-gen livetest` — if you touched what gets deployed (12–20 min)
- [ ] not needed, because:

## Checklist

- [ ] A new `generate` option has a row in `bzm_opl_gen/options.py`, and
      `docs/options.md` was regenerated with `python -m bzm_opl_gen.options`
- [ ] A new live check has an offline counterpart that fakes the cluster
- [ ] No account name, id, token or internal hostname in the diff — including in
      test fixtures and comments
- [ ] `CHANGELOG.md` under `## [Unreleased]`, written for the person upgrading
