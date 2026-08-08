# Web UI

```
bzm-opl-gen ui          # opens http://127.0.0.1:8765
```

Installed with the `[ui]` extra (see the [README](../README.md#install)); from a
checkout, `pip install -e ".[ui]"`. The page is committed prebuilt, so neither
route has an npm step.

Two views, in a drawer down the left that collapses to a rail: **Generate**, the
three steps below, and **Account capacity**, the account's rated virtual users
rolled up by workspace, which is out of reach without a key and says which
control fixes that.

The key, the account and the workspace live together at the foot of that drawer.
All three last the session while a location and an agent are chosen per bundle,
and three separate things read the account — connecting used to be the first
section of step 1, which put "which API key am I using" inside "which agent am I
generating for".

**Generate** is three steps, one on screen at a time, with the stepper and
Back/Next in one bar at the top: **Capacity & agent** → **Configure** →
**Download & verify**. The step scrolls inside itself and the page does not
scroll at all, so what is unfinished and what is next cannot be pushed off the
bottom by a long step. Next is greyed until the step is finished and says what is
missing rather than leaving you to find it; a step is ticked only once you have
opened it, because everything on step 2 has a default and a tick on a step nobody
has looked at claims something that did not happen.

The manifests are in a second drawer, on the right, which pushes the form over
rather than covering it — a sticky column cost the form half its width for the
whole session, tabs made it one thing at a time, and a slide-over that closed on
any click outside meant re-opening it for every field you checked. Closed it is a
rail carrying the file count, which is what says a bundle exists while it is out
of sight.

Under the step, and outside its scroller so it does not move, is the summary
line: account › workspace › location › agent, with "none yet" in amber where one
is missing. It stays as the steps change, because that is a question you have in
step 2 and step 3 as much as in step 1.

## Step 1 — Capacity & agent

**The sizing is the first card**, above the locations the run might go
to. Tick the functionalities this run is for and give each one a target **in its
own unit** — virtual users, browser instances, requests per second — then the
engine size and how many engines a node holds. It answers in engines, nodes and
peak vCPU, with the request document to raise the infrastructure ticket with, to
download, copy or read here ([capacity-planning.md](capacity-planning.md)).
*Edit* opens it downward; the summary line stays put.

Where several are ticked the pool is sized for the largest of them and the card
says which. **Service Virtualization has no per-pod figure box**, because no
requests-per-second-per-core figure has ever been measured here: its target is
carried into the request and stated, and a sizing with nothing else in it comes
back as the reason rather than as a node count.

**Saved sizings** sit at the top: pick one to fill the fields, or name what is
in them now and save it. One ships as a starting point for each sizing model
the server serves, carrying that model's own example target.
They last as long as the browser session does, like everything else this page
remembers, and picking one applies nothing — it fills the fields, and the fields
*are* the sizing.

It reaches nothing — no key, no account, no cluster — which is why it renders on
a page nobody has connected, and why it is *first* rather than a view of its own:
the planner asks the first question and the generator the last, and side by side
in the drawer the first read as an alternative to the second, with its answer to
be carried across by hand. It has no *agents* field on purpose: on Kubernetes an
agent is a cluster, so you raise `slots` and let the node pool scale. The engine
size it plans against is the bundle's own option rather than a copy of one, so
the sizing and the manifests cannot drift apart.

Under it, where the harbor id, ship id and token come from: **Connect to
BlazeMeter**, or **Enter values manually** for an account nobody here can reach.
Connected, the two sections — **Private location** and **Agent (ship)** — are
bordered panels that fold, opening on whichever the step has reached until you
pin a different one. A folded one carries its state on the header, so nothing has
to be opened to find out whether it needed opening. Each ends in a **Confirm**,
because a lone agent is auto-picked and without one the step could complete
itself — leaving the one screen that names what the bundle is for never seen.

A location with no agents says so on its row and again in the panel: an empty
location is not broken, it just has nothing deployed to it, and the first agent
has to be created. Choosing an agent expands its row to hold that agent's
credential and the regenerate control; only one row is open at a time. Reusing an
identity that is already running somewhere conflicts with that install, and the
row says so.

The account tree — accounts, workspaces, locations and an agent's facts — is
**remembered for 60 seconds** by the server, so reloading the page costs one
local round trip rather than four to BlazeMeter (2.5s on a small account; the
location list alone is 1.3s on one holding 171). Anything this server writes —
creating a location or an agent, changing a location's settings, regenerating a
token, or connecting a different key — drops the cache immediately, so your own
changes are never the stale ones. An agent's heartbeat is never cached: the
status poll is what says an agent came online.

### Changing a location after it exists

Selecting a location expands it, and what the sizing would change about
it is inside, as a before → after against what the account holds: **engines per
agent** (`slots`), **virtual users per engine** (`threadsPerEngine`) and the
engine's CPU and memory **requests** (`overrideCPU` / `overrideMemory`). They
open out of the location rather than sitting under the list, because they belong
to the one that is selected and to nothing else. The case is the correction
rather than the setup — a location built for 500 virtual users an engine that a
real run says should be 1,000.

There is no calculator in here and no *Apply*: the sizing above fills these
fields and they stay editable. A calculator of its own was a third place the same
four numbers were worked out, and filling a field applies nothing, so a button to
do it sat between the sizing and the only control here that costs anything.
**Save** is that control, and nothing in this panel reaches the account without
it. `slots` is engines per *agent*, so a location's concurrency is agents ×
slots, and the row divides the sizing by the number of agents this location has.

None of those four values is in a manifest, so changing one needs no
regenerate, no re-apply and no restart; it applies to the next test that
starts. Save sends only the fields that changed, and the answer is a **re-read
of the location**, not an echo of the request: a field the account did not
store comes back reported as not stored. That is not hypothetical — BlazeMeter's
own create endpoint accepts `threadsPerEngine` and drops it, which is why a
freshly created location 403s every test start until it is PATCHed.

It changes the location for every agent in it and every test that starts on it,
which the panel says before the button is pressed. Clearing a setting is not
offered: blank means "leave this one alone", and the two are different intents.

### The AUTH_TOKEN, and where it comes from

**The two controls that issue a credential are the two that say so**: creating an
agent, and *Regenerate token* in that agent's row. Asking BlazeMeter for a token
*mints* one and the previous one dies with the request, so the download button
used to break the install it was being downloaded for: crane answers a dead token
with `404`, logs `Sleeping for 300`, never starts its health service, and the pod
sits `0/1 Running` looking like a slow boot. Downloading now mints nothing, and
says which of two ways its bundle got its token — as entered, or the placeholder.

- **Creating an agent captures its token**, in a masked field with a *Show*
  toggle. That is the one moment issuing one is free — a new ship has no previous
  credential to invalidate — and the bundle you download is the copy to keep, as
  you would what `create-agent` prints.
- **A token this app minted comes back after a refresh**, silently and with
  nothing typed: both moments it is ever shown one are its own writes, so the
  server keeps what it handed over, in memory, for that agent, until you
  disconnect or it restarts. Nothing is written to disk or to browser storage. A
  token you **paste** wins for that agent and drops the remembered one, so it is
  not quietly replaced on the next load — and it is gone with the page, because a
  pasted value is not one this app can offer back.
- **Pointing at an agent this app did not create leaves the field empty**,
  because no API reads an existing token back — and it says which of the two that
  is: an agent this app minted nothing for, or one its store could not be asked
  about. Paste what you kept, or press **Regenerate token** in that agent's own
  row, which arms to *I'm sure* (beside a *Cancel*) and names, before it issues
  anything, the agent whose credential it kills. The new token lands in the field
  above the button, and the download carries it rather than issuing a second one.
- **A download with neither is a placeholder bundle**, which is a fine thing to
  read and an unusable thing to apply. The download step says exactly that over
  the button, in one line: where a token comes from is step 1's question, and a
  page of recovery instructions under a download button answers one nobody has
  asked yet.

The field is masked because this is the one place the change makes a token *more*
visible: it now sits on a page rather than streaming into a zip. Masking is not
secrecy — crane logs the token, and anyone who can read a pod log in that
namespace can read the Secret — it is about a screen share and a screenshot.

**A refresh does not disconnect you.** The API key lives in the server process,
not in the browser, so reloading the page never actually dropped the
connection — the page simply forgot. It now asks on load, and puts back the
account, workspace, location, agent, step and options it was pointed at, plus
what a manual session declared its identity to run. Each selection is re-applied only once the account has confirmed it
still exists, so a location deleted since the last load comes back as nothing
rather than as an id the page believes. **The AUTH_TOKEN is never written to
browser storage** — see `session.strip` — because browser storage is a file in
the browser's profile. What comes back instead is what the *server* minted, from
its own memory, for the agent it minted it for; a restart forgets it, and so does
disconnecting.

**The key is a menu at the foot of the drawer**: it states the key in use, holds
the account and workspace pickers, and offers *Connect…* — *Use a different key…*
once there is one — and *Disconnect*. The form itself is a modal, because
connecting is a question being asked rather than a panel to work in. One Connect
for both ways in: a pasted id and secret if there is one, the file otherwise, the
pasted pair being the deliberate act. The path is prefilled from a key detected
on this machine, which is why the paste fields fold away above it. Disconnect
makes the server forget the key and clears everything read with it; a key you
asked to save stays on disk, so reconnecting is one click. Without it, a key
pasted by mistake — or the wrong account — cost a server restart.

## Step 2 — Configure

**The format is the first control, and the form follows it.** A docker bundle is
one agent as one container on a host: no namespace, no ServiceAccount, no node
selectors, no engine limits — around two dozen options reach nothing in it. The
choice used to be made on the download step, one step too late, with the
generated README the only thing that said so. What is on screen is derived from
the generator's own table, served as `/api/ignored-options` and never restated
here: a key added to the generator would otherwise go on being offered for a
format that drops it. Hiding is not refusing — the value is kept, sent, and named
in the bundle's README — and where a hidden field needs explaining, the page
renders the generator's reason rather than a second copy of it.

A format the configuration rules out is disabled with the reason on it: helm is
the one that cannot serve service virtualization, because our chart carries none
of the ingress the upstream one does. Docker can, in its own vocabulary — a
hostname and a TLS pair rather than an ingress — so the page offers that group
for it and hides the Kubernetes four, and the reverse. And a
format you picked is never replaced in silence — where a configuration forces
Kubernetes manifests, the panel says which format it replaced, and how to get
back to it, until you pick one again. A configuration somebody wrote outranks a
segment.

**Two kinds of option — a functionality's own, and every deployment's — and
nothing is hidden between them.** *Deployment functionalities* is one card per
funcId this tool configures — Performance, GUI Functional and Service
Virtualization, under BlazeMeter's own names — marked `Enabled` or `Not
enabled` from the location's own funcIds, holding the options only that
functionality has. Anything else the location runs is named underneath, because a page that
said nothing about it would read as covering it. *Placement* is the
namespace and the service account — its own section because it is the part a
docker bundle does not have at all, and a section that comes and goes has to be
one. *Agent settings* is everything every deployment gets: registry, proxy, CA
trust, scheduling, security, the cluster check, and Advanced. A rail down the
left names what is set in each, off the same groups the cards show, so what the
bundle contains is answerable without scrolling the form.

The service account's **Create it** checkbox is the only thing that decides
whether the bundle carries the ServiceAccount object — the name is what the
Deployment runs as and what the RoleBinding grants to either way, so a customer
who must run under an account their platform team owns unchecks it and types
that name. The name itself is required, and an empty one blocks the download.

**Advanced asks two questions, not one.** *Security posture* is who assigns the
pod's UID — the SCC-friendly default leaves it to the cluster, and it is
recommended on vanilla Kubernetes as much as on OpenShift. *Cluster*, beside it,
is which of the two this actually is, and it is asked only under that posture
because the other one is named `k8s` and answers for itself. The cluster decides
what a human is told to type: every command in the bundle's README, its verify
block and its node-pool recipe is written in `oc` or in `kubectl` off this one
control. It also takes **OpenShift cluster trust injection** off *Custom CA
trust*, and clears it if it was chosen — that mode is OpenShift's own operator
filling a labeled ConfigMap, and anywhere else it emits an empty one nothing
ever fills, leaving a bundle that reads as configured while the agent trusts
nothing extra.

**The PEM mode takes a file, and the file is read in the browser.** A customer
configuring CA trust has a certificate file, so *Custom CA trust* → *Paste PEM*
carries a **Choose file** control beside the box. It fills the same option:
`ca_bundle` is content and never a path, so the file is read where it sits and
the server never learns it existed. It states the file name and how many
certificates arrived, and it refuses rather than filling the box with whatever
the bytes decode to — a `.crt` is often DER, and DER read as text mounts
cleanly and is trusted by nothing. Three answers, three sentences: the file
holds nothing, the file could not be read, and the file was read and carries no
certificate — the last one names `openssl x509` because that is the way out.

The other mode does not need one. **Reference an existing ConfigMap** is the
recommended path and the one nearly every customer takes, and the bundle's
README now prints the command that creates it, in `oc` or `kubectl` off the
same cluster control:

```
kubectl -n <namespace> create configmap <name> --from-file=<key>=/path/to/your-ca.pem
```

The `<key>=` is the point. BlazeMeter document the bare `--from-file=<path>`,
which keys the entry on the file's own basename: a customer who follows that
literally with `corp-root.pem`, and leaves the bundle key at its default
`ca-bundle.crt`, gets a mount that is **empty rather than missing**. The agent
starts, reports online, and fails every TLS handshake with nothing naming the
cause. The explicit form makes the key the manifests mount the key the command
creates, whatever the file is called, so the two cannot be set into
disagreement.

**A functionality the location does not run is not on the step at all.** It was
stated for a while — a card naming the funcId to add in BlazeMeter (Settings →
Private Locations) — which is a true sentence about the location and nothing the
reader of this step can act on, and on a performance location it was half the
section. So the card, its options and its rail entry go together. The options are
*cleared*, not just hidden, because `generate` refuses an `sv_ingress` with no
subdomain whatever the location runs, so a hidden row would only move the blocker
to the server. Manual entry is the exception, and structurally: there the card
*is* the declaration, so filtering it out would remove the control that answers
the question.

Turning a funcId on was offered here once and is not any more: it changes what
the location *is*, which is BlazeMeter's own UI's to do, unlike this page's two
writes to an agent's credential and a location's concurrency. A card can be
silent for the other reason too — this *format* cannot serve that functionality —
and the two answers are kept apart, because they have different remedies.

**In manual entry the functionality is not a view of the options, it is the
declaration.** With no account to read funcIds off, each card's **Enabled**
checkbox is what says what the typed identity runs. The cards *are* the funcIds,
so what is ticked decides the images the bundle carries and the namespace
suggested for it. It is in the session snapshot for
that reason: a refresh used to bring an SV identity back as a performance one,
clearing its options on the way and rewriting the namespace generated into every
manifest.

**Tick as many as the identity runs** — a real account has 71 of its 168
locations running Performance and GUI Functional together, so a control that
could only say one described a location nobody would create. Ticking two suggests
one namespace, not two: the first in the served order, which is the same
tie-break a connected location carrying both funcIds already uses. A hand-typed
namespace still wins over any of it. If a member of a restored declaration is no
longer in the served vocabulary it is dropped and the rest stand; only when none
of it survives does the page land where a fresh manual session lands.

**Service virtualization is declared on its own.** Ticking it clears Performance
and GUI Functional, and ticking either of those clears it, in manual entry and in
the new-location form alike — the agent applies one CPU and memory limit pair to
every pod it creates, so engine sizing and mock throughput cannot be set apart,
and an SV agent carries no test engine at all. Both surfaces say so before
anything is ticked. **Connected, a location that already runs both is warned
about and never blocked**: what a location *is* is BlazeMeter's own UI's to
change, so refusing to generate would be refusing the only bundle that location
can have.

A location carrying `mockServices` shows **Service virtualization** enabled with
its group marked *required*, because a bundle without an ingress stalls at
`WAITING_FOR_DOMAIN` — but the switch does turn off, and that is how you
generate a location that offers both for performance alone. Switched off it
reads *declined*, the row says what was given up, and the download unblocks;
the profile records it as `sv_ingress: none`, which is a decision rather than a
gap, so re-importing it does not put you back where you started. See
[Not using it on a location that offers it](service-virtualization.md#not-using-it-on-a-location-that-offers-it).

For a location with SV enabled, the page names every prerequisite the bundle
does *not* create (the wildcard TLS secret, an Istio Gateway when one is named,
the controller itself) and what the chosen backend does with each, plus the
endpoint host to check once it is applied — the same facts as
[Service virtualization](service-virtualization.md), against the namespace and
domain actually configured.

**Environment variables**, a fold under *Agent settings* beside *Advanced*, is
the escape hatch — and it is a **list**, not a blank box. BlazeMeter's
agent-environment reference is much wider than the settings on this page, and
the only way to reach the rest used to be editing the generated ConfigMap by
hand, which the next generate silently reverts. Open the fold and every
documented variable that no control above it already writes is there, with the
agent's own default beside it: `PREFERRED_INTERFACE`, `KUBERNETES_USE_PRE_PULLING`,
`DODUO_PORT`, `VERIFY_SSL`, `KUBERNETES_LABELS` and the others. Nothing is typed
from memory.

Each row carries the control its type deserves. A string is a text box; an
integer refuses what is not a whole number; a certificate gets a box a
certificate fits in; `KUBERNETES_LABELS` and `KUBERNETES_CUSTOM_ANNOTATIONS_JSON`
are key/value tables, so nobody hand-encodes JSON. A boolean has **three**
positions — *Default*, *On*, *Off* — because leaving it alone is a real answer
and writes nothing: `VERIFY_SSL` defaults on and `KUBERNETES_USE_PRE_PULLING`
defaults off, so *Off* is a departure for one and the default for the other.

The list is what is *left over*: the proxy, the registry, the CA bundle, engine
sizing and the SV ingress have their own groups on this page, so their variables
are not offered here — the page reads the same reserved table the generator
refuses them by, and a variable stops being offered the moment an option starts
writing it. Under the list, **Another variable by name** keeps the old name/value
rows for anything the list does not carry: a variable documented for the other
platform, one belonging to a functionality this location does not run, or one
newer than this tool. A name the bundle already writes is **refused** there,
naming the option that owns it — two values for one key is a ConfigMap with a
duplicate entry, and whichever wins is not the one the form showed.

Beside it, **Set by this bundle, elsewhere on this step** is the whole reserved
table: every variable the bundle writes itself, the option that writes it and the
section of this step holding that option. It is there for the variable you go
looking for and do not find — `AUTO_KUBERNETES_UPDATE` is not missing, it is
written from **Security & RBAC**'s *Agent self-update*, and nothing else on the
page led from the name to the control.

Two things decide which variables are on screen, and they are different
questions. **The format** picks which half of BlazeMeter's reference applies: a
Kubernetes bundle is offered crane's variables, a docker bundle the container
agent's. **The location** picks which functionality's variables apply: a
performance location has no Selenium grid and publishes no virtual services, so
`DODUO_PORT`, the two `_GRID` certificates and the three variables about
publishing mocks are not offered to it. Nothing is lost either way — a variable
already set that this bundle does not offer keeps its value and appears in the
rows underneath, because hiding a variable the bundle carries is exactly what
this area's rules exist to prevent.

It is an option (`extra_env`), so it travels in `profile.json` and a regenerate
replays it. All three formats carry it: ConfigMap entries for manifests,
`extraEnv` in the values overlay for Helm, `--env` flags in the docker script. It
reaches the **agent** — crane's pod — and not the engines crane spawns, whose
environment crane builds from the `KUBERNETES_*` variables rather than passing
its own down.

Profile JSON **Export** / **Import**, at the top of this step, round-trips with
`generate --profile`.

## Step 3 — Download & verify

**Download bundle (.zip)**, and beside it what this bundle holds. A group left
unfinished blocks the download, is named here, and gets a button back to it — a
disabled button whose cause is a step away is the failure that is here to be
removed. A bundle carrying the placeholder AUTH_TOKEN says so over the button.

**Whether the cluster will take it is a terminal's question, not this page's.**
`bzm-opl-gen doctor` answers it — against a live cluster, or against the JSON
[`scripts/bzm-cluster-evidence.sh`](preflight.md#a-cluster-you-cannot-reach)
wrote on a machine with access. This step used to import that file and render
the verdicts, and offered crane-hook two ways beside it; both are gone. The
verdicts are the same either way, and the person who can collect the file has a
shell in front of them already.

**Watch agent status** polls every ten seconds and goes green once the applied
deployment heartbeats. It needs an API key, so a manual session says so — and
points at Settings → Private Locations in BlazeMeter — rather than showing a dead
switch.

While watching, an SV deployment lists the virtual services deployed in the
namespace, the endpoint host each publishes, and a check for whether that host
actually answers. That is the part the heartbeat cannot tell you: the agent
reports idle whether or not any of them became reachable, so a deploy stalled at
`WAITING_FOR_DOMAIN` reads as healthy until you look at the hosts. A **503**
there is the diagnosis, not a failed check — it is this cluster refusing crane's
port reference, and [`sv-expose`](service-virtualization.md#reaching-a-virtual-service-from-outside-sv-expose)
is the fix. A probe that gets no status line says which kind it was: the host did
not resolve, nothing accepted the connection, the TLS handshake failed, or
nothing replied in time.

Reading the namespace is the one thing the UI does that needs a cluster, and it
needs one only for that: it uses whatever `kubectl`/`oc` context the machine
running `bzm-opl-gen ui` has. There is often none, so an unreadable cluster is a
normal answer rather than an error — it says which of *no CLI*, *no context*,
*denied* or *no virtual services in that namespace* applied, and the heartbeat
keeps working either way. Nothing else in the UI needs a cluster at all.

## Account capacity

The second view answers one number: how many virtual users this account can run
at once. Everything else on it exists to make that number checkable — which
workspace holds it, which location, and out of how many agents and engines —
because a total nobody can take apart is a total nobody believes.

"Rated" is the load-bearing word, and it was measured rather than assumed:
`agents × slots` is the engine count and BlazeMeter enforces it, while
multiplying by virtual users per engine gives what those engines are *sized* for,
which a run may exceed and be packed onto them instead. A location nobody has
sized has no rating at all, and those are **counted separately** rather than
drawn as capacity of zero — "not sized" and "sized at nothing" are different
facts, and a page that renders neither shows the first as the second. A location
shared between workspaces is striped and counted once in the account total, which
is why that total is not the sum of the workspace figures.

## Running it

**It binds this machine only, on purpose.** The server holds your API key in
process memory, so reaching the page is equivalent to holding the key: whoever
reaches it can create locations and agents in your account, and can ask for a new
AUTH_TOKEN, which revokes the one a running agent holds. To use it
from another device, prefer a tunnel to the default bind, which keeps the
listener local and lets your existing SSH auth decide who gets in:

```
ssh -L 8765:127.0.0.1:8765 you@that-machine     # then open http://127.0.0.1:8765
```

`--host` widens the bind when you really do want the server itself listening
elsewhere (`--host 0.0.0.0`, or a specific interface address). It warns at
startup, and it is the wrong tool on any network you do not control.

**Run it without a terminal** (macOS): `bzm-opl-gen ui --install-service`
writes a LaunchAgent that serves the UI from login onward with whatever
`--port`/`--host`/`--api-key` you gave it, restarts it if it dies, and logs to
`~/Library/Logs/bzm-opl-gen-ui.log`. `--uninstall-service` removes it. The
agent runs the python that installed it, so rebuilding or moving the venv
means reinstalling the service. Not docker, deliberately: the point of a bundle
is that `kubectl` on this machine can apply it, and a container puts a
filesystem boundary exactly there.

**A local install tracks your checkout; releases are the distribution.** The
service runs with `--dev`, so backend changes take effect without a restart.
The *built page* is the half reload cannot reach — nothing rebuilds
`bzm_opl_gen/ui_dist/` for you, so after pulling frontend changes:

```
cd frontend && npm run build
```

You will not have to remember. When the built page is older than
`frontend/src`, the page carries a banner saying so and the server prints the
same warning at startup; `/api/build` is the underlying answer. That matters
because staleness is otherwise invisible in the worst way: a route the page
needs answers `404`, the page reads that as *not read yet* rather than as an
error, and it then shows options a format hides and builds bundles missing
files. A service left running for a day did exactly that, and four generator
defects were suspected before the server was.

Frontend dev: `cd frontend && npm install && npm run dev` (proxies /api to
:8765); `npm run build` refreshes the shipped bundle in `bzm_opl_gen/ui_dist/`.
CI runs `npm test` and `npm run typecheck` as its own job — the logic modules
(option groups, formats, the session snapshot) with no DOM between them, and
the panels that decide something with one.
