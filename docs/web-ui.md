# Web UI

```
bzm-opl-gen ui          # opens http://127.0.0.1:8765
```

Installed with the `[ui]` extra (see the [README](../README.md#install)); from a
checkout, `pip install -e ".[ui]"`. The page ships prebuilt, so there is no npm
step.

Three views, in a drawer down the left that collapses to a rail: **Generate**,
the three steps below; **Account capacity**, the account's rated virtual users
rolled up by workspace; and **Images**, the container images to mirror into
your own registry. The API key, the account and the workspace live together
at the foot of that drawer, because they last the session while a location and
an agent are chosen per bundle.

**Generate** is three steps, one on screen at a time, with the stepper and
Back/Next in one bar at the top: **Capacity & agent** → **Configure** →
**Download & verify**. Next says what is missing when it is greyed. A step is
ticked only once you have opened it.

The generated manifests are in a second drawer on the right, which pushes the
form over rather than covering it; closed, it shows the file count. Under the
step, the summary line — account › workspace › location › agent — stays in view
on every step, with "none yet" in amber where one is missing.

## Step 1 — Capacity & agent

**The sizing is the first card.** Tick the functionalities the run is for and
give each a target **in its own unit** — virtual users, browser instances,
requests per second — then the engine size and how many engines a node holds.
It answers in engines, nodes and peak vCPU, with the request document to raise
the infrastructure ticket with ([capacity-planning.md](capacity-planning.md)).
*Edit* opens it.

Where several are ticked, the pool is sized for the largest and the card says
which. **Service Virtualization has no per-pod figure**, because none has been
measured: its target is carried into the request document, and a sizing with
nothing else in it explains why rather than showing a node count.

**Saved sizings** sit at the top: pick one to fill the fields, or name the
current fields and save them. One starting point ships per sizing model. They
last as long as the browser session, and picking one fills the fields without
applying anything.

The sizing needs no key, account or cluster, so it works before you connect.
It has no *agents* field: on Kubernetes an agent is a cluster, so you raise
`slots` and let the node pool scale. The engine size it plans against is the
bundle's own option, so the sizing and the manifests agree.

Under it, where the location and agent come from: **Connect to BlazeMeter**, or
**Enter values manually** for an account you cannot reach. Connected, the
**Private location** and **Agent** panels fold, each showing its state on the
header, and each ends in a **Confirm** so the identity the bundle is for is
always seen before moving on.

A location with no agents says so: it is not broken, it just needs its first
agent created. Choosing an agent opens its row, holding its credential and the
regenerate control. Reusing an agent identity that is already running elsewhere
conflicts with that install, and the row says so.

The server remembers the account tree — accounts, workspaces, locations and an
agent's facts — for **60 seconds**, so a reload is fast. Anything this page
writes drops that cache immediately. An agent's heartbeat is never cached.
**Refresh** on the location list and on the Account capacity view re-reads the
account, for changes made elsewhere (an agent a colleague created, a location
deleted in BlazeMeter).

In manual entry both ids are optional: a blank one becomes a marker
(`<HARBOR_ID>`, `<SHIP_ID>`), so a bundle can be produced for review before the
private location exists.

Manual entry reads no account, so the server pins each image to the newest
release in BlazeMeter's public registry. That read usually takes about a
second and can take up to 8 seconds. While it runs, the form says so and
**Download** waits, because the facts on screen are for the previous values.
When it is done, the form shows what those facts cannot tell. For example, a
location can ask for an older release than the newest.

### Changing a location after it exists

Selecting a location expands it, showing what the sizing would change as before
→ after against what the account holds: **engines per agent** (`slots`),
**virtual users per engine** (`threadsPerEngine`) and the location's engine
request overrides (`overrideCPU` / `overrideMemory`, which replace the requests
the bundle sets). The sizing above fills these
fields and they stay editable; **Save** is the only control here that writes to
the account. `slots` is engines per *agent*, so the row divides the sizing by the
location's agent count.

None of those four values is in a manifest, so changing one needs no regenerate
or re-apply; it applies to the next test that starts. Save sends only the fields
that changed, and reports a **re-read of the location** afterwards, so a value
the account did not store is shown as not stored. The change applies to every
agent in the location and every test on it, which the panel says before you
press Save. Clearing a setting is not offered: blank means "leave this one
alone".

### The AUTH_TOKEN, and where it comes from

**Only two controls issue a credential, and both say so**: creating an agent, and
*Regenerate token* in that agent's row. Issuing a token revokes the previous one
— crane left with a dead token answers `404`, logs `Sleeping for 300`, and the
pod sits `0/1 Running` like a slow boot. Downloading a bundle never issues one.

- **Creating an agent captures its token**, in a masked field with a *Show*
  toggle. A new agent has no previous credential to revoke. Keep the downloaded
  bundle as you would what `create-agent` prints.
- **A token this page issued comes back after a refresh.** The server keeps it
  in memory for that agent until you disconnect or it restarts; nothing is
  written to disk or browser storage. A token you **paste** replaces the
  remembered one for that agent, and does not survive a reload.
- **An agent this page did not create starts with an empty field**, because no
  API reads a token back. Paste what you kept, or press **Regenerate token**,
  which asks *I'm sure* and names the agent whose credential it revokes first.
- **A download with no token carries the `<AUTH_TOKEN>` marker**, fine to read
  and unusable to apply until filled in.

The field is masked for screen shares and screenshots; it is not secrecy —
anyone who can read the Secret in that namespace can read the token.

**A refresh does not disconnect you.** The API key lives in the server process,
and on load the page restores the account, workspace, location, agent, step and
options it was pointed at, each only once the account confirms it still exists.
**The AUTH_TOKEN is never written to browser storage.**

**The key is a menu at the foot of the drawer**: it shows the key in use, holds
the account and workspace pickers, and offers *Connect…* (or *Use a different
key…*) and *Disconnect*. Connect takes a pasted id and secret, or a key file —
prefilled when one is detected on this machine. Disconnect makes the server
forget the key and everything read with it; a key you asked to save stays on
disk.

## Step 2 — Configure

**The format is the first control, and the form follows it.** A docker bundle is
one agent as one container on a host: no namespace, no ServiceAccount, no node
selectors, no engine limits. Options a format ignores are hidden for it; the
value is kept, and the bundle's README names it if it was set.

**Two kinds of option.** *Deployment functionalities* has one card per
functionality this tool configures — Performance, GUI Functional and Service
Virtualization — holding the options only that functionality has. Other funcIds
the location carries are named underneath. *Placement* is the namespace and the
service account (absent for docker). *Agent settings* is what every deployment
gets: registry, proxy, CA trust, scheduling, security, the cluster check, and
Advanced. A rail down the left summarises what is set in each.

A functionality the location does not run is not shown, and its options are
cleared.

The service account's **Create it** checkbox decides whether the bundle carries
the ServiceAccount object; the name is what the Deployment runs as either way.
Uncheck it to run under an account your platform team owns.

**Blank required fields warn, never block.** A namespace or service account
left empty becomes a marker (`<NAMESPACE>`, `<SERVICE_ACCOUNT_NAME>`); the field
turns amber and its hint names the marker. See
[Fields left blank](options.md#fields-left-blank).

**Advanced asks two questions.** *Security posture* is who assigns the pod's UID
— the SCC-friendly default leaves it to the cluster, and suits vanilla
Kubernetes too. *Cluster* is whether this is actually OpenShift, and decides
whether every command in the bundle's README is written with `oc` or `kubectl`.
Outside OpenShift it also removes **OpenShift cluster trust injection** from
*Custom CA trust*, since nothing else fills that ConfigMap.

**The PEM mode takes a file, read in the browser.** *Custom CA trust* → *Paste
PEM* has a **Choose file** control; the certificate is read locally into the same
field. It shows the file name and certificate count, and refuses a file that
holds no PEM certificate — a `.crt` is often DER, and the message names
`openssl x509` to convert it.

**Reference an existing ConfigMap** is the recommended mode. The bundle's README
prints the command that creates it:

```
kubectl -n <namespace> create configmap <name> --from-file=<key>=/path/to/your-ca.pem
```

The explicit `<key>=` matters: BlazeMeter document the bare `--from-file=<path>`,
which keys the entry on the file's name, and a key that does not match the
bundle's gives an **empty** mount — the agent starts and fails every TLS
handshake with nothing naming the cause.

**In manual entry, the functionality cards are the declaration.** With no
account to read funcIds from, each card's **Enabled** checkbox says what the
typed identity runs, which decides the images the bundle carries and the
suggested namespace. Tick as many as the location runs; several ticked suggest
one namespace (the first in order), and a namespace you type wins.

**Service virtualization is declared on its own.** In manual entry and the
new-location form, ticking it clears Performance and GUI Functional and the
other way round: the agent applies one CPU and memory limit pair to every pod it
creates, and an SV agent carries no test engine. A connected location that
already runs both is warned about, never blocked.

A location carrying `mockServices` shows **Service virtualization** enabled with
its group marked *required*, because a bundle without an ingress stalls at
`WAITING_FOR_DOMAIN`. Switching it off reads *declined* and records
`sv_ingress: none` — see
[Not using it on a location that offers it](service-virtualization.md#not-using-it-on-a-location-that-offers-it).
With SV on, the page lists every prerequisite the bundle does *not* create (the
wildcard TLS secret, an Istio Gateway when one is named, the controller) and the
endpoint host to check once applied.

**Environment variables**, a fold beside *Advanced*, lists every documented
BlazeMeter agent variable that no control on the page already writes, with the
agent's default beside it: `PREFERRED_INTERFACE`, `KUBERNETES_USE_PRE_PULLING`,
`DODUO_PORT`, `VERIFY_SSL`, `KUBERNETES_LABELS` and others. Each row has the
control its type needs: text, a whole number, a certificate box, a key/value
table for JSON maps, and for booleans three positions — *Default*, *On*, *Off* —
since leaving it alone writes nothing.

The list shows variables for this format's agent and this location's
functionalities only. **Another variable by name** takes anything else; a name
the bundle already writes is refused there, naming the option that owns it.
**Set by this bundle, elsewhere on this step** lists every variable the bundle
writes itself, with the option and section that control it — for instance
`AUTO_KUBERNETES_UPDATE` comes from *Agent self-update* under **Security &
RBAC**.

These are the `extra_env` option, so they travel in `profile.json`. They reach
the agent, not the engines it spawns.

Profile JSON **Export** / **Import**, at the top of this step, round-trips with
`generate --profile`.

## Step 3 — Download & verify

**Download bundle (.zip)**, with a summary of what the bundle holds. Fields
still carrying a marker are listed in one folded section, one row per field with
where its value comes from; when there are none it reads *Nothing left to fill
in*. An error the generator reports appears in red with its own message.

**Whether the cluster will take it** is `bzm-opl-gen doctor`'s question — against
a live cluster, or against the JSON
[`scripts/bzm-cluster-evidence.sh`](preflight.md#a-cluster-you-cannot-reach)
wrote on a machine with access.

**Watch agent status** polls every ten seconds and turns green once the applied
deployment sends a heartbeat. It needs an API key; a manual session points at
Settings → Private Locations in BlazeMeter instead.

While watching, an SV deployment lists the virtual services in the namespace,
the endpoint host each publishes, and whether that host answers — which the
heartbeat cannot tell you, since the agent reports idle either way. A **503** is
the cluster refusing crane's port reference, and
[`sv-expose`](service-virtualization.md#reaching-a-virtual-service-from-outside-sv-expose)
is the fix. A probe with no status says which failure it was: the host did not
resolve, the connection was refused, the TLS handshake failed, or it timed out.

Reading the namespace uses whatever `kubectl`/`oc` context the machine running
`bzm-opl-gen ui` has. Without one, the page says which of *no CLI*, *no
context*, *denied* or *no virtual services in that namespace* applied, and the
heartbeat keeps working. Nothing else in the UI needs a cluster.

## Account capacity

The second view answers one number — how many virtual users this account can
run at once — and breaks it down by workspace, location, agents and engines so
it can be checked.

"Rated" means `agents × slots × virtual users per engine`: BlazeMeter enforces
the engine count, while virtual users per engine is what those engines are
*sized* for. A location with no sizing has no rating, and those are **counted
separately** rather than shown as zero. A location shared between workspaces is
striped and counted once in the account total, so the total is not the sum of
the workspace figures.

## Images

The third view lists the container images a corporate registry must hold for
the agent to run: each image reference, what it is for, which functionality
pulls it, when it is pulled, and what the public registry says about it. It
needs no key.

**Where the list comes from** is the first line of the view:

| You are | The view shows |
|---|---|
| Connected, with a location chosen under Generate | "Images for location *name* (read from your account)": the versions crane asks for on that location. |
| Connected, with no location chosen | BlazeMeter's image catalogue. Choose a location to see its versions. |
| Not connected, or in manual entry | BlazeMeter's image catalogue, with each image pinned to the newest release in BlazeMeter's public registry. Where the registry did not answer, a tag can be `latest`. |

Versions pinned to the newest release are not read from a location. A location
can ask for an older release than the newest, and then a mirror built from this
list does not have the image the agent asks for. The view says so above the
table. For the exact list, connect an account and choose a location.

The view reads the location again when you choose another one under Generate.

**What a row says:**

- **Image**: the full reference, with **Copy** beside it, the functionalities
  that pull it, and where its tag came from (the location's version list, a
  running agent's inventory, the newest release in BlazeMeter's registry, or
  the catalogue). CSV and Markdown carry the same value. A functionality shows
  BlazeMeter's name for it. Without a connected account, the tool has names
  only for the functionalities it configures, so the view shows any other one
  as its funcId (for example `functionalApi`), in code type.
- **Purpose** and **pulled**: what the image does and when crane pulls it.
  *(inferred)* marks a purpose that nobody has seen on a running agent.
- **Size** and **digest**: the compressed size and the digest from the public
  registry. Click the short digest to copy the full one.
- **Tag**: *pinned*; a warning for a tag such as `latest`, which names a
  different image after each release; or *newer tag available*, where
  BlazeMeter's registry holds a newer tag in the same series. For a tag such as
  `latest`, the view also shows the version tag that has the same digest now,
  for example `latest = 2.4.538-reduced`. When no version tag has that digest,
  the view says so. When the registry could not be read, the view says the
  version was not read.
- **Required**: only for a location. The catalogue cannot say what a location
  needs, so it has no such column.

A value the view could not read says **not read**, and the reason shows when
you point at it. It is never blank and never `0`. A notice above the table says
when the location's version list could not be read, when the location has no
agent yet, and when the public registry did not answer for some or all images.

For a location, **Show** switches between **Required only** and **All** (every
image the location's functionalities can pull). The catalogue has no such
choice, because it always lists every image it knows.

**Copy** puts every reference on the clipboard, one per line. **CSV** and
**Markdown** download the rows on screen; the page makes them itself and sends
no further request. Both have a column for the version a tag resolves to. The
CSV has a `func_ids` column beside the names, and a `registry_state` column,
so a blank digest, size or resolved version is read against it.

Tags follow BlazeMeter's releases, so a mirror goes out of date after an
upgrade. Check it again after each one:

```
bzm-opl-gen images --verify <registry>
```

When the bundle's **Registry** option is set, the view fills in that registry.
The view does not show the name each image has inside your mirror, because
the generator composes those names.

## Running it

**It binds this machine only.** The server holds your API key in memory, so
reaching the page is equivalent to holding the key: it can create locations and
agents and issue tokens that revoke running ones. To use it from another
device, prefer an SSH tunnel:

```
ssh -L 8765:127.0.0.1:8765 you@that-machine     # then open http://127.0.0.1:8765
```

`--host` widens the bind (`--host 0.0.0.0`, or an interface address) when you
really want the server listening elsewhere. It warns at startup, and it is the
wrong tool on any network you do not control.

**Run it without a terminal** (macOS): `bzm-opl-gen ui --install-service` writes
a LaunchAgent that serves the UI from login with whatever `--port`/`--host`/
`--api-key` you gave it, restarts it if it dies, and logs to
`~/Library/Logs/bzm-opl-gen-ui.log`. `--uninstall-service` removes it. The
service runs the Python that installed it, so rebuilding or moving the venv
means reinstalling the service. It is not a container, because `kubectl` on this
machine has to be able to apply the bundle.

Working on the page itself from a checkout is covered in
[CONTRIBUTING.md](../CONTRIBUTING.md).
