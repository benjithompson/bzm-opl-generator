# Security policy

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

Report it through GitHub's private advisory form —
[Security → Report a vulnerability](https://github.com/benjithompson/bzm-opl-generator/security/advisories/new)
— which is visible only to the maintainers until an advisory is published.

Please include what you were running (version, format, platform), what you
expected, and what happened. A proof of concept helps; a `profile.json` with the
`auth_token` removed helps more. Expect a first response within a week.

If the problem is in **BlazeMeter itself** — the agent, crane, the API — rather
than in this generator, report it to BlazeMeter/Perforce support. This project
only generates manifests; it cannot fix the platform they deploy.

## What this tool handles that is worth your attention

A private-location **AUTH_TOKEN is a credential**: anyone holding it can
register as that agent. Three consequences shape how the code treats it, and a
report about any of them is in scope.

- **It is never written to `profile.json`.** A profile is the file people commit,
  diff and paste into tickets; the token lives in the generated Secret beside it
  and nowhere else. See `SECRET_OPTIONS` in `bzm_opl_gen/generate.py`.
- **The MCP server never returns it.** Revealing it is a whole action
  (`reveal_token`) rather than something that can happen as a side effect of
  another call, and a secret is never a tool argument — a path may be.
- **`--rotate-token` invalidates the previous token.** That is BlazeMeter's
  behaviour, not ours, and it is why `generate` never mints one implicitly.

Also in scope: anything that makes a generated bundle less safe than the options
asked for — a Secret rendered world-readable, a ServiceAccount bound wider than
the chart declares, a placeholder resolving to a real value rather than failing
loudly, or an `extra_env` entry overriding a variable the generator owns.

## What is out of scope

- **The example credentials.** `examples/api-key.example.json` and the
  placeholder ids in the web UI are deliberately fake.
- **Container images.** The manifests reference images BlazeMeter publishes;
  vulnerabilities in those are BlazeMeter's to fix.
- **`bzm-opl-gen ui` binding to `127.0.0.1`.** It is a local, single-user tool
  with no authentication by design. Exposing that port to a network is a
  deployment choice, not a defect here — but a report that it binds more widely
  than documented very much is.
- **The live rig deploying to a cluster.** `livetest` deploys because deploying
  is all it is, and it is off in the MCP server unless
  `BZM_OPL_ENABLE_LIVETEST` says otherwise.

## Supported versions

The latest release on PyPI is the supported one. Fixes go into a new release
rather than being backported.
