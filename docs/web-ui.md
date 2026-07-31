# Web UI

```
bzm-opl-gen ui          # opens http://127.0.0.1:8765
```

Installed from the release wheel with the `[ui]` extra (see the
[README](../README.md#install)); from a checkout, `pip install -e ".[ui]"`.

Three steps, one on screen at a time, with the stepper and Back/Next in one bar
at the top: **Agent details** — either connect (key stays local) and pick or
create a location & agent, or enter the harbor id, ship id and token by hand →
**Configure** → **Download & verify**, which is also where you watch the agent
flip online. The manifests are a **Preview** tab beside Configure rather than a
column, so the form has the page's full width; the count on the tab says how
many files the current options produce. Profile JSON import/export round-trips
with `generate --profile`.

Next is greyed until the step is finished, and says what is missing rather than
leaving you to find it: a step that is complete only because everything has a
default, and that you have not opened, reads *ready — nothing required* rather
than showing a tick.

**Save to folder** writes the same bundle (profile.json included) to a
directory on the machine running the server, instead of a browser download.
That directory is the shape `bzm-opl-gen livetest` re-renders from and an MCP
session's `opl_bundle` reads, so it is the handoff between the UI and both:
configure here, then `kubectl apply` / livetest / ask an AI session to carry
on from the same folder. Saving into a folder that already holds this ship's
bundle reuses the token already there, so a re-render with one option changed is
the same bytes and leaves the deployed agent alone.

**Cluster check (crane-hook)** under *Deployment settings* adds
[crane-hook](https://github.com/Blazemeter/crane-hook) to the bundle: a one-shot
Pod, plus its own read-only Role and RoleBinding, that checks node capacity,
egress, RBAC and — for service virtualization — the ingress and its TLS secret,
then exits 0 or 1. It is not part of the agent; `kubectl logs cranehook` is the
report, and deleting it changes nothing. In a Helm bundle it is the chart's
`helm test` hook instead. This is a different thing from **Preflight the target
cluster** below the download button, which needs no cluster access at all — see
[Preflight](preflight.md).

### The AUTH_TOKEN, and where it comes from

**Nothing on this page issues a credential except creating an agent.** Asking
BlazeMeter for a token *mints* one and the previous one dies with the request, so
the download button used to break the install it was being downloaded for: crane
answers a dead token with `404`, logs `Sleeping for 300`, never starts its health
service, and the pod sits `0/1 Running` looking like a slow boot. Downloading and
saving now mint nothing, and each says which of four ways its bundle got a token
(as entered · newly issued · reused from that folder · placeholder).

- **Creating an agent captures its token**, in a masked field with a *Show*
  toggle. That is the one moment issuing one is free — a new ship has no previous
  credential to invalidate — and it is the only copy: nothing here writes it down.
  Keep it as you would what `create-ship` prints.
- **Pointing at an agent that already exists leaves the field empty**, because no
  API reads an existing token back. Paste what you kept — or press **Regenerate
  token** in that agent's own row, which arms to *I'm sure* (beside a *Cancel*)
  and names, before it issues anything, the agent whose credential it kills and
  what that looks like when it happens. The new token lands in the field above
  the button, and the download then carries it rather than issuing a second one.
- **A download with neither is a placeholder bundle**, which is a fine thing to
  read and an unusable thing to apply, so the page says so over the button and
  names both places a real token comes from — including the `kubectl … get secret`
  for an agent already deployed. That command is printed, never run.

The field is masked because this is the one place the change makes a token *more*
visible: it now sits on a page rather than streaming into a zip. Masking is not
secrecy — crane logs the token, and anyone who can read a pod log in that
namespace can read the Secret — it is about a screen share and a screenshot.

**A refresh does not disconnect you.** The API key lives in the server process,
not in the browser, so reloading the page never actually dropped the
connection — the page simply forgot. It now asks on load, and puts back the
account, workspace, location, agent, step and options it was pointed at. Each
selection is re-applied only once the account has confirmed it still exists, so
a location deleted since the last load comes back as nothing rather than as an
id the page believes. **The AUTH_TOKEN is never stored** — see `session.strip`
— because the page's promise is that it is held for this browser session and
that nothing writes it down, and browser storage is a file in the browser's
profile.

**The Connect button becomes the Disconnect button.** The key form stays where
it is — the paste fields, the `api-key.json` path, Browse, *Remember this key* —
and shows, greyed, the key that is in use; connecting and disconnecting change
what the controls say, never where they are. One Connect for both ways in: a
pasted id and secret if there is one, the file otherwise — the pasted pair is
the deliberate act, so it wins. The path is prefilled from a key detected on
this machine, which is why the paste fields fold away above it. Disconnect makes the server forget
the key and clears everything read with it; a key you asked to save stays on
disk, so reconnecting is one click. Without it, a key pasted by mistake — or the
wrong account — cost a server restart.

**Run it without a terminal** (macOS): `bzm-opl-gen ui --install-service`
writes a LaunchAgent that serves the UI from login onward with whatever
`--port`/`--host`/`--api-key` you gave it, restarts it if it dies, and logs to
`~/Library/Logs/bzm-opl-gen-ui.log`. `--uninstall-service` removes it. The
agent runs the python that installed it, so rebuilding or moving the venv
means reinstalling the service. Not docker, deliberately: the point of saving
bundles is that `kubectl` on this machine can apply them, and a container
puts a filesystem boundary exactly there.

Frontend dev:
`cd frontend && npm install && npm run dev` (proxies /api to :8765); `npm run
build` refreshes the shipped bundle in `bzm_opl_gen/ui_dist/`, and `npm test`
runs the logic suites CI runs as its own job — the option groups and the
preflight panel, both plain data in and data out, neither rendering anything.

**Namespace and service account are always on screen**, under *Deployment
settings*: every deployment has both, and both are always sent.
The service account's **Create it** checkbox is the only thing that decides
whether the bundle carries the ServiceAccount object — the name is what the
Deployment runs as and what the RoleBinding grants to either way, so a customer
who must run under an account their platform team owns unchecks it and types
that name. The name itself is required, and an empty one blocks the download.

**Everything is on screen at once, in two sections.** *Deployment features* is
one card per feature — each marked `Enabled` or `Not enabled` from the
location's own funcIds — holding the options only that feature has. *Deployment
settings* is everything every deployment gets: namespace, service account,
registry, proxy, CA trust, scheduling, security, the cluster check, and
Advanced. A rail down the left names what is set in each, so what the bundle
contains is answerable without scrolling the form.

There used to be a feature *selector* switching between two views of the same
groups. Nothing is hidden now, so nothing has to be handed back: no "also in
this bundle", no "not in view". A group that is unfinished blocks the download
and the download step offers the way back to it.

A feature the location does **not** run has its options greyed, and touching one
asks *Enable it and configure it here?* — answering yes adds that feature's
funcId to the location in BlazeMeter, because an agent is only ever asked to
serve what its location says it runs. The feature list is served, so it grows
without a UI release, and a location carrying funcIds no feature claims says so
rather than hiding them.

**A location holds agents**, and step 1 is built around that: a path line naming
both (`LOCATION x › AGENT y`), the two lists styled alike because they are the
same kind of choice, and *New location* / *New agent identity* above their lists
rather than under them. A location with no agents says so on its row and again
in the panel — an empty location is not broken, it just has nothing deployed to
it, and the first agent has to be created. Choosing an agent expands its row to
hold that agent's credential and the regenerate control; only one row is open at
a time. Reusing an identity that is already running somewhere conflicts with
that install, and the row says so.

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

The panel header states what was imported before any of it: the file name, when
it was collected, the namespace the file *describes*, the namespace being
preflighted, and every section its collector could not read. Those last two are
different things — a file collected for another namespace still describes the
same nodes, but its LimitRanges, quotas, ServiceAccounts and PSA labels are
somebody else's, and the header says so. The same facts lead the verdict list as
its first row, because every verdict under it is only as good as they are. A
thin file is a page of warnings with a reason attached, never a clean bill of
health. A file that is not evidence, or carries a schema this version does not
know, is refused by name and leaves the verdicts already on screen standing.

### Applying what the cluster implies

Under the verdicts, the same file answers the question that comes first: not
whether the deployment survives this cluster but how it should have been
configured. Each row names one option, what the evidence says about it, the
evidence paths behind it, and what the configuration holds right now — the whole
point being that you stop transcribing a namespace's ServiceAccount names and a
router's wildcard domain by hand.

- **Decisive** suggestions offer the value as one click. **Suggestive** ones
  offer a button per candidate and never a default, at one candidate as much as
  at three: narrowing the shortlist is not choosing from it.
- **A value you set is never overwritten silently.** Where the evidence
  disagrees with it the row turns amber and shows both values, and the button
  says *Replace* rather than *Apply*.
- **Applying is reversible for the session.** Each applied row grows an *Undo*
  that puts the previous value back without you re-entering it.
- An option no row names is not in this file. It is left exactly as you set it,
  and nothing here has checked it.

An applied value is an ordinary option from there on: the preview, the bundle
and `profile.json` are identical to what you get typing it in the form, and
nothing downstream can tell the difference.

Reading the namespace is the one thing the UI does that needs a cluster, and it
needs one only for that: it uses whatever `kubectl`/`oc` context the machine
running `bzm-opl-gen ui` has. There is often none, so an unreadable cluster is a
normal answer rather than an error — it says which of *no CLI*, *no context*,
*denied* or *no virtual services in that namespace* applied, and the heartbeat
keeps working either way. Nothing else in the UI needs a cluster at all.

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
