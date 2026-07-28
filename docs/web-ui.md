# Web UI

```
bzm-opl-gen ui          # opens http://127.0.0.1:8765
```

Installed from the release wheel with the `[ui]` extra (see the
[README](../README.md#install)); from a checkout, `pip install -e ".[ui]"`.

Single page: Agent details — either connect (key stays local) and pick or create
a location & agent, or enter the harbor id, ship id and token by hand → choose
what the location runs → configure → live manifest preview → download zip
(AUTH_TOKEN fetched on download when connected, as entered when not) → watch the
agent flip online. Profile JSON import/export round-trips with
`generate --profile`. Frontend dev:
`cd frontend && npm install && npm run dev` (proxies /api to :8765); `npm run
build` refreshes the shipped bundle in `bzm_opl_gen/ui_dist/`, and `npm test`
runs the logic suites CI runs as its own job — the option groups and the
preflight panel, both plain data in and data out, neither rendering anything.

**Namespace and service account are always on screen**, above the groups and
outside the feature view: every deployment has both, and both are always sent.
The service account's **Create it** checkbox is the only thing that decides
whether the bundle carries the ServiceAccount object — the name is what the
Deployment runs as and what the RoleBinding grants to either way, so a customer
who must run under an account their platform team owns unchecks it and types
that name. The name itself is required, and an empty one blocks the download.

**Configure one feature at a time.** The step shows that feature's options plus
the ones that apply to any deployment — registry, proxy, CA trust, scheduling,
security. It is a **view, not a scope**: the manifests come from the location's
own funcIds either way, so anything set under one feature stays set and stays in
the bundle. Options set under a feature you are not looking at are listed beside
the preview; ones that are *required* and not on screen block the download with
a link to the feature that needs them. The feature list is served, so it grows
without a UI release — and a location carrying funcIds no feature claims says so
rather than hiding them.

Picking or creating is one or the other: starting to create a location or an
agent hides the list of existing ones until you finish or cancel. Reusing an
agent identity that is already running somewhere conflicts with that install,
so the two paths are kept apart deliberately.

For a location with SV enabled, the page names every prerequisite the bundle
does *not* create (the wildcard TLS secret, an Istio Gateway when one is named,
the controller itself) and what the chosen backend does with each, plus the
endpoint host to check once it is applied — the same facts as
[Service virtualization](service-virtualization.md), against the namespace and
domain actually configured.

While watching the agent, an SV deployment lists the virtual services deployed
in the namespace, the endpoint host each publishes, and a check for whether that
host actually answers. That is the part the heartbeat cannot tell you: the agent
reports idle whether or not any of them became reachable, so a deploy stalled at
`WAITING_FOR_DOMAIN` reads as healthy until you look at the hosts. A **503**
there is the diagnosis, not a failed check — it is this cluster refusing crane's
port reference, and [`sv-expose`](service-virtualization.md#reaching-a-virtual-service-from-outside-sv-expose)
is the fix. A probe that gets no status line says which kind it was: the host did
not resolve, nothing accepted the connection, the TLS handshake failed, or
nothing replied in time.

**Preflight a cluster from a file.** Under Download & verify, pick the JSON
[`scripts/bzm-cluster-evidence.sh`](preflight.md#a-cluster-you-cannot-reach)
wrote on a machine with cluster access, and the page shows the verdicts
`bzm-opl-gen doctor` reaches — PASS, WARN or FAIL each on its own row — against
the configuration currently on screen. Editing an option re-runs them, so the
list always describes what is configured rather than what was when the file was
picked. It needs no API key and no kubecontext, which is the point: the same
person who cannot reach the account usually cannot reach the cluster either.

The list leads with where the answers came from — when the file was collected,
which namespace for, and every section its collector could not read — because
each verdict under it is only as good as that. A thin file is a page of warnings
with a reason attached, never a clean bill of health. A file that is not
evidence, or carries a schema this version does not know, is refused by name and
leaves the verdicts already on screen standing.

Reading the namespace is the one thing the UI does that needs a cluster, and it
needs one only for that: it uses whatever `kubectl`/`oc` context the machine
running `bzm-opl-gen ui` has. There is often none, so an unreadable cluster is a
normal answer rather than an error — it says which of *no CLI*, *no context*,
*denied* or *no virtual services in that namespace* applied, and the heartbeat
keeps working either way. Nothing else in the UI needs a cluster at all.

**It binds this machine only, on purpose.** The server holds your API key in
process memory, so reaching the page is equivalent to holding the key — and the
download button fetches an AUTH_TOKEN, which *rotates* it and leaves any agent
already running for that ship on a token the API no longer accepts. To use it
from another device, prefer a tunnel to the default bind, which keeps the
listener local and lets your existing SSH auth decide who gets in:

```
ssh -L 8765:127.0.0.1:8765 you@that-machine     # then open http://127.0.0.1:8765
```

`--host` widens the bind when you really do want the server itself listening
elsewhere (`--host 0.0.0.0`, or a specific interface address). It warns at
startup, and it is the wrong tool on any network you do not control.
