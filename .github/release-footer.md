
## Install

```
gh release download --repo benjithompson/bzm-opl-generator --pattern '*.whl'
pipx install './bzm_opl_gen-*.whl[ui]'
bzm-opl-gen ui
```

Upgrading from an earlier version? Download the new wheel and add `--force`:
`pipx install --force './bzm_opl_gen-*.whl[ui]'`.

Drop `[ui]` for the CLI alone — it has no dependencies. Needs Python 3.9+ and
read access to this repo; "repository not found" means access, not a typo.

See the [README](https://github.com/benjithompson/bzm-opl-generator#readme) to
get started.
