# The hardened default, and the images that have run under it

`restrict_engines` (on by default, `--no-restrict-engines` to turn it off) puts
two keys in the ConfigMap:

```
INHERIT_RUNNING_USER_AND_GROUP: 'true'
KUBERNETES_SECURITY_CONTEXT_CAP_JSON: '{"drop": ["ALL"]}'
```

Crane's own default is a *privileged* engine pod, which restricted PodSecurity,
OpenShift's restricted-v2 SCC and GKE Autopilot's Warden all refuse — after the
agent is online, so the run hangs at `BOOT_STARTING`. With the keys set, every
pod crane creates for a run comes out like this (read off a live one):

```yaml
spec:
  securityContext:
    seccompProfile: {type: RuntimeDefault}      # the platform's default profile
  containers:
  - securityContext:
      privileged: false
      allowPrivilegeEscalation: false
      capabilities: {drop: [ALL]}
      runAsUser: 1337                           # crane's own UID, inherited
      runAsGroup: 1337
      readOnlyRootFilesystem: false
```

## Why this is verified per image, not per cluster

The posture is a property of the pod spec, not of the cluster. A cluster that
enforces admission accepts a subset of what a permissive one accepts, so a
shape the strictest cluster admits is admitted everywhere by construction; and
once admitted, the container's runtime identity comes from the spec rather than
from the cluster's policy. Re-running the same shape on a more permissive
cluster proves very little.

What varies is the **image**: each one has to work with no capabilities and a
UID it did not choose. The one thing the cluster does decide is where that UID
comes from — an SCC assigns it on OpenShift, `run_as_user` pins it on plain
k8s — so that split is checked separately below.

## What has run under it

Each "observed" column was read from inside a *running* container (`id` and
`/proc/self/status`), not from the pod spec, except the one row that says
otherwise. `CapPrm` and `CapEff` were zero in every row — as were `CapBnd` and
`CapAmb` wherever they were read — with `NoNewPrivs: 1`, so only the identity is
repeated per row. The GKE rows are `platform: k8s`, which pins the UID; the
OpenShift rows are `platform: openshift`, which leaves it to the SCC.

| image | how it was exercised | observed inside | outcome |
|---|---|---|---|
| `crane:3.7.55` | the Deployment this generator emits (GKE Autopilot) | `uid=1337 gid=1337` | agent online |
| `crane:3.7.55` | the same, on OpenShift (CRC 4.22.1, `restricted-v2`) | `uid=1000680000 gid=0` | agent online |
| `v4:2.4.444-reduced` (taurus engine) | a real JMeter run on GKE Autopilot | `uid=1337 gid=1337` | 20 samples, 0 failed |
| `v4:2.4.444-reduced` | a real JMeter run on OpenShift, UID from the SCC | `uid=1000680000 gid=0` | 23 samples, 0 failed |
| chromedriver *inside* the engine | a Selenium test on GKE Autopilot | not read separately — it is a process in the engine container above | 13 samples, 0 failed |
| `service-mock:6.0.29.6` | a virtual service serving its transactions | `uid=1337 gid=1337` | all transactions, correct 404 for an unmatched request |
| `doduo:0.0.144` (grid proxy) | a GUI-functional run: crane created the `doduo-r-gp-*` pod | `uid=1337 gid=1337` | redis + gunicorn up, polling BZA for commands, uploading logs |
| `charmander/chrome_136.0.7103.113:2.10.45` (browser) | the same run: crane created the `grid-r-sg-*` pod, and the engine drove it as a remote WebDriver | `uid=1337 gid=1337` | real session: navigated demoblaze.com and clicked through to "Add to cart" |
| `torero:4.6.182` | direct probe — no run creates one (below) | `uid=1337(blazemeter) gid=1337` | starts, exits on missing `TEST_ID` |
| `richrach:1.0.81` | direct probe — no run creates one (below) | `uid=1337(blazemeter) gid=1337` | starts, exits on missing `COMMAND` |

Three rows are the earlier verification, from when the default changed and
issue #69 recorded them: the GKE engine run (20 samples), chromedriver inside
the engine, and `service-mock`. Every other row was measured while closing it.

Nothing needed a capability, so nothing motivates turning the restriction off.
That matters because the escape hatch is all-or-nothing: `--no-restrict-engines`
removes the posture from *every* container crane creates, not from the one image
that wanted something. An image that genuinely cannot run restricted belongs in
this table with what it needs, not in a quiet global downgrade.

Two things about the browser row are worth stating plainly, because "the pod
came up" is not the claim being made. The engine's log says
`Forcing browser to Remote, because of remote WebDriver address` — the session
ran in the browser pod, not in the engine's own chromedriver — and the run ended
on `UnexpectedAlertPresentException: Alert Text: Product added`, i.e. Chrome had
navigated the site, clicked a product and added it to the cart before the
recorded script tripped over an alert it does not dismiss. The script is stale;
the browser did real work under the posture, which is what was being checked.

## The browser image: no capability, because it gives up Chrome's sandbox itself

Headless Chrome is the classic case for wanting a capability, so the browser
image was checked beyond "the pod came up". Inside the charmander container,
under the posture above:

```
$ google-chrome --headless=new --no-sandbox --disable-setuid-sandbox \
      --disable-gpu --user-data-dir=/tmp/cd --dump-dom https://blazedemo.com
<!DOCTYPE html> ... <title> BlazeDemo</title> ...          # renders

$ google-chrome --headless=new --disable-gpu --user-data-dir=/tmp/cd2 \
      --dump-dom https://blazedemo.com
The setuid sandbox is not running as root. Common causes: ...
Failed to move to new namespace: PID namespaces supported, Network namespace
supported, but failed: errno = Operation not permitted
Aborted
```

Both halves are expected and neither is a defect. `chrome-sandbox` is setuid
root, and `allowPrivilegeEscalation: false` sets `NoNewPrivs`, which makes the
setuid bit inert; the namespace sandbox then fails too, with `Operation not
permitted` on the unshare — which of the dropped capabilities and the default
seccomp profile refuses it was not isolated, and does not need to be. The image
never asks for either: `src/tools/browser_extension.py` in the image appends
`--no-sandbox --disable-setuid-sandbox` to every Chrome and Edge launch (only
Chrome was exercised here), treating the container as the boundary. So the
restriction takes away something the image had already given up.

What the posture does cost the browser is cosmetic and worth recognising in a
log: Chrome cannot renice its renderers, so each one logs
`Failed to adjust OOM score of renderer ... Permission denied`. The page still
renders.

The `--user-data-dir` is not incidental: the image sets no `HOME`, so it is `/`,
which is not writable by the inherited UID. Chrome supplies its own writable
directory in the real path; a bare `google-chrome` in a shell does not, and
crashes for that reason rather than for a sandbox one.

## torero and richrach: started, but not exercised

Neither is created by any run observed here — not a performance run, not a
GUI-functional run — although crane's image set names both, which is why they
are mirrored for a sealed cluster. They were therefore exercised directly: each
image starts under the posture, runs its own entrypoint, and reaches its
argument validation before exiting for want of the environment crane would have
given it (`TEST_ID`, `COMMAND`). That is a configuration failure, not a refused
capability, but it is weaker evidence than the rows above and is recorded as
such: what a fully-configured torero does under no capabilities is untested,
because nothing here can make one run.

## Where the UID comes from: an SCC, or a pinned value

`platform: openshift` leaves `runAsUser` unset (restricted-v2 assigns a UID from
the namespace's range, and pinning 1337 there would be rejected); `platform: k8s`
pins `run_as_user`. Either way crane passes *its own* UID down, so this is the
one platform difference the engines see.

The OpenShift half was re-checked after the default changed: on CRC, crane comes
up as `uid=1000680000 gid=0(root)` under `restricted-v2`, and the engine pod
crane spawned for a real run came up as the same `uid=1000680000 gid=0`, all
capabilities dropped, returning 23 samples with 0 failures. Inheritance works
when the UID is assigned, not just when it is pinned.

Pinning is the more defensive of the two, and these images show why. With
`runAsUser` unset and `runAsNonRoot: true`, the kubelet has to read a numeric
`USER` off the image:

| image | `USER` in the image | with `runAsUser` unset |
|---|---|---|
| `crane`, `v4`, `doduo`, `charmander/*` | numeric (`1337`, `1337:0`) | starts, `uid=1337 gid=0` |
| `service-mock` | numeric (`1337`) | starts, `uid=1337 gid=1337` |
| `torero`, `richrach` | **named** (`blazemeter`) | refuses to start: `container has runAsNonRoot and image has non-numeric user (blazemeter), cannot verify user is non-root` |

So leaving the UID to the image is only safe where something else fills it in.
It is safe on OpenShift because the SCC does; it would not be safe on a plain
cluster, and it is a second variable besides — unset also gives crane and the
engine `gid=0(root)`, where pinning gives `1337`.

## One thing the keys do not reach

`test-job-*` pods — the short-lived jobs crane runs for its own housekeeping —
are created without the cap-drop JSON: on GKE Autopilot one came back with
`capabilities: {drop: [NET_RAW]}`, which is the platform's own mutation and not
crane's. They run the crane image, so they still land on its numeric `USER`, and
they are refused by nothing observed here. It is worth knowing that the two keys
describe the pods crane creates *for a run*, not literally every pod it creates.

## Reproducing

[`docs/repro/hardened-posture-probe.yaml`](repro/hardened-posture-probe.yaml)
runs the four images no run here creates — torero, richrach, doduo and a
browser — under the posture, with no BlazeMeter account and no crane, and swaps
in any other image you give it a tag for. Its header carries the commands, the
observed output, a control and the `runAsUser`-unset variant. The rows it does
not cover (crane, the engine, `service-mock`) come from a real run instead, and
that half needs a live location:
deploy a bundle, then start a test on it and watch the pods crane creates
(`bzm-opl-gen livetest --run-test` does this for a performance run; a
GUI-functional test is what creates the `doduo-r-gp-*` and `grid-r-sg-*` pods).

Tags matter: the ones above are what a live GUI-functional location reported.
Only a live agent says which browser image a location uses, so re-read them with
`bzm-opl-gen facts` before repeating this for a customer.
