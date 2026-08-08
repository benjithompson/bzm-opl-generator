# Live test

Success = the BlazeMeter API reports the ship with a **fresh heartbeat** and
idle/running state. That exercises the full chain: RBAC, SCC admission, image
pull, egress to `*.blazemeter.com`, and credentials. `--keep` skips teardown;
`--cluster kind` and `--cluster minikube` use a cluster named `bzm-opl-test`
(crane comes online; engines won't fit laptop resources — use
`--cluster current` against a real cluster for full engine validation).

**A run deletes a cluster only if it created one.** Both flags reuse a
`bzm-opl-test` that is already there, and a reused cluster survives teardown.
The run says which happened as it starts and again as it finishes. A **stopped**
minikube profile is started and still not owned: starting somebody's profile is
not creating it. This matters wherever a standing cluster carries that name:
deleting it unconditionally is what #226 was. The one exception is
deliberate and announced: `--contain-egress` recreates a running minikube
profile that has no policy enforcer, because `--cni` applies at creation only
and containment would otherwise be a silent no-op — after which the profile is
this run's, and teardown deletes it.

**A cluster that survives makes everything inside it survive**, which the
cluster deletion used to hide, so each of those is answered for on its own:

| what the run made | what teardown does with it |
|---|---|
| the namespace, where the run created it | deletes it, and every object and pod in it |
| a namespace that was already there | deletes the applied `*.yaml`, plus the egress NetworkPolicy by name — it lives in a dotfile so no glob reaches it |
| `127.0.0.1 <registry>` in the node's `/etc/hosts` (`--local-registry`) | removes those lines where the cluster is kept |

The middle one is the expensive one to get wrong. A default-deny egress policy
left in the namespace, whose only hole is a proxy container the same `finally`
has just removed, does not fail the next run — it makes it wait out its whole
timeout and report that the agent never came online.

There are **two rigs**, and which one a run gets is read off the bundle rather
than asked for — a manifests bundle is applied to a cluster, a `--format docker`
bundle is started with Docker Compose on the host you are sitting at. Nothing on
the command line selects it: the bundle already records what it is, and a flag
saying so would be a second place to get it wrong, where both wrong answers look
identical (nothing is created and the run waits out its whole timeout). They
share the success criterion and nothing below it.

## The compose path (`--format docker` bundles)

Up, online, down:

```
bzm-opl-gen livetest --api-key api-key.json --facts facts.json \
    --manifests out --ship-id <SHIP_ID> --timeout 300
```

No `--namespace` and no `--cluster` — a docker bundle is one container on this
host and has neither. Pass a namespace anyway and the run says it reaches
nothing rather than refusing; pass `--cluster`, `--local-registry`,
`--local-proxy`, `--contain-egress` or `--run-test` and it **refuses**, because
each of them is cluster-shaped and a run that quietly dropped one would report a
pass that proved none of it.

The run needs a docker daemon with the Compose v2 plugin and nothing else — no
minikube, no kind, none of the 12–20 minute cluster build. It re-renders
nothing, so it mints no credential and deploys the bundle exactly as it sits on
disk; `docker compose down --remove-orphans` runs in a `finally`, and a
container that survives it is removed by name so it cannot hold the name the
next run needs.

Before the container exists it refuses a directory with **no compose file**, one
whose **`container_name` is not the agent under test** (the container name
carries the ship id — #107 on this platform), one whose `HARBOR_ID`/`SHIP_ID`
name another location, and one still carrying compose's `${…:?}`
required-variable guard, which is what the generator writes where a required
value was left blank. `compose up` would refuse that last one too, but only
after creating a container against a real account.

A required value this format writes as a **file** — `ca_bundle`, and the
virtual-service TLS pair — is refused on the same rule and needs its own read:
the marker is in the file's own bytes, so no variable carries it, and
`profile.json` carries neither the file nor (for `sv_tls_key`, which is a secret)
the option. The rig reads the file the container would actually mount, which is
the one `CA_BUNDLE`/`SV_TLS_CERT`/`SV_TLS_KEY` resolves to — so a bundle finished
the way its own README recommends, by pointing a variable at a file the host
already keeps, passes. A file it cannot read is a note, not a refusal.

### What the compose path does **not** prove

It is the first live proof `--format docker` has ever had, and it is a narrow
one. It never starts an engine, so:

- **`-u 0` and `DOCKER_PORT_RANGE` are not exercised.** These are the two flags
  in the docker format that came from BlazeMeter's *pages* rather than from the
  command their API returns, and they are the two that broke the bundle the last
  time this was guessed — the container ran as the image's non-root user and
  died on the docker socket it exists to use. Crane only reaches for the socket
  and the port range once it starts something. **Issue #214** covers `-u 0`,
  which #184 was expected to settle and did not: deploying a real virtual
  service exercised the socket, but under the uid this bundle had already asked
  for, so what happens without the flag is still untested.
  `DOCKER_PORT_RANGE` is settled and the answer was that it does not apply —
  BlazeMeter publishes virtual services on its own `10000-32000` whatever that
  variable says, so nothing about a mock could ever have exercised it. See
  [Service virtualization](service-virtualization.md#what-a-live-run-showed).
- There is no private-registry mirror, no proxy interception, no CA trust check,
  no egress containment and no negative control. Every one of those is
  cluster-shaped in the Kubernetes rig (a registry blackholed on a node, a
  NetworkPolicy an unenforced CNI silently ignores) and none of them has a
  compose analogue here.
- Nothing is read back off the running container. The Kubernetes rig's
  `assert_live_config` checks the deployed objects against the options; the
  compose path checks the *bundle* before it starts and the *account* after.

What it does prove is the chain BlazeMeter can see: the image pulled, the
container started, the agent reached `*.blazemeter.com`, and the credential in
the bundle was accepted.

## Reproducing the hard customer environments locally

Everything from here down is the **cluster** rig. Two optional local
environments turn a laptop into the awkward network a customer has, and are torn
down with the cluster.

| flag | container | what it proves |
|---|---|---|
| `--local-registry [PORT]` (5001) | `registry:2`, published on the host, pulled via `host.minikube.internal` | air-gapped pulls: `DOCKER_REGISTRY`, `IMAGE_OVERRIDES`, no public-registry fallback |
| `--local-proxy` | `mitmproxy`, joined to the cluster's own docker network | proxy egress **and** custom CA trust |
| `--contain-egress` | calico + a default-deny egress NetworkPolicy | that the proxy is the **only** way out, not just the way that was taken |
| `--run-test TEST_ID` | a real BlazeMeter run on the location | what crane passes to the **engines** it spawns: image override, CA propagation, proxy env |

`--local-proxy` is deliberately hostile: mitmproxy terminates TLS with its own
CA, so `*.blazemeter.com` is unreachable unless the generated `REQUESTS_CA_BUNDLE`
/ CA ConfigMap actually lands in the crane process. The rig

1. starts the cluster, then mitmdump (authenticated by default —
   `--proxy-auth user:pass`, or `none` for an open proxy) **on the cluster's
   docker network**, addressed by container IP,
2. reads the mitm CA out of the container and appends it to the public roots,
3. CONNECTs through the proxy from inside the node and requires the attempt to
   appear in the proxy's *own* log before going further,
4. **regenerates** `out/` from `out/profile.json` with `proxy` + the CA mode
   under test merged in (so the manifests under test are generator output, not
   a hand-patched Deployment),
5. deploys, waits for the agent to come online, and then requires
   `blazemeter.com` lines in the proxy log — online *without* them means the
   agent bypassed the proxy, which fails the test.

### Which CA mode is under test (`--ca-mode`)

There are two ways a customer configures CA trust on a cluster, and until #227
only one of them had ever been deployed under interception.

| `--ca-mode` | who owns the ConfigMap | what the bundle carries |
|---|---|---|
| `inline` (default) | the generator | `bzm_cacerts.yaml`, holding the PEM |
| `existing` | the **rig**, created before the deploy | a reference by name and key only |

`existing` is the mode BlazeMeter recommend and the one nearly every customer
takes, because a platform team owns and rotates the bundle. The rig creates
`bzm-opl-livetest-trust` holding the MITM CA and generates a bundle that only
references it.

**The key is deliberately not `ca-bundle.crt`.** It is `corp-root.pem`, because
`ca-bundle.crt` is what the generator falls back to when `ca_configmap_key` is
unset — so a run using it would pass whether or not the configured key reached
anything. With a key nothing defaults to, `REQUESTS_CA_BUNDLE` inside the crane
pod is `/var/cm/corp-root.pem` or the run fails.

Two rules it keeps, both the cluster's and the namespace's one level further
down: it **refuses a ConfigMap of that name it did not create** rather than
replacing a trust bundle that is somebody's, and it deletes the one it did
create when the namespace survives teardown. The negative control clears **all
three** CA modes rather than the inline PEM alone — clearing only that leaves an
existing-mode run referencing a ConfigMap that is gone, and a pod that cannot
start never logs `CERTIFICATE_VERIFY_FAILED`, so the control would fail having
tested nothing.

`--ca-mode` needs `--local-proxy`; without one no CA is configured at all, so
the flag is refused rather than ignored.

## The credential a run uses

**A run issues one AUTH_TOKEN, and issuing it revokes the one before it.** That
is the whole of the rotation cost, and it is unavoidable here: the rig exists to
bring an agent online, so it needs a credential that works. There is no
`--rotate-token` on this command for the same reason — running it at all is the
consent.

One per *run*, though, not one per render. The re-render steps (`--local-proxy`
adds the proxy's CA, `--run-test` the engine sizing) used to fetch a fresh token
each time they fired, and a run fires them three or four times — so the agent
deployed from an earlier render was left holding a revoked credential, sitting
`0/1 Running` in a way the rig cannot tell from a slow boot. If you are chasing
an intermittent rig failure from before this changed, that is a candidate.

`--auth-token <token>` skips the mint for a caller already holding one — what
`create-agent` printed, say. Use it when a run must not disturb the agent that is
already deployed there.

A run that re-renders nothing (no `--local-proxy`, no `--run-test`) deploys the
bundle exactly as it sits in `--manifests`, and so mints nothing at all. If that
bundle still carries the `<AUTH_TOKEN>` marker the command refuses up
front, rather than deploying an agent that cannot authenticate and reporting,
twelve to twenty minutes later, only that it never came online. The same refusal
covers every other field left blank when the bundle was generated — each carries
`<KEY>` for its own option key, and the refusal names the field beside the
marker so the message and the file are one search.

**The bundle's identity is checked, not re-rendered.** `--manifests` defaults to
`out/`, and `out/` is whatever the last `generate` left there — so a run given
`--ship-id`/`--auth-token` could deploy an old bundle built for a *different*
agent. Re-rendering would not have caught it either: the re-render merges onto
`profile.json` and prefers *its* `ship_id` over the command line, so the stale
identity survives. Instead the rig refuses, before the cluster exists, a
`HARBOR_ID`/`SHIP_ID` (in the ConfigMap or in `profile.json`) that is not the one
the run was told to test, naming both values — and refuses any `*.yaml` the
generator does not emit, which is how a leftover from an older version is
caught. An identity it cannot *read* is a note, not a refusal.

The compose path makes the same refusal off the files a docker bundle has: the
`container_name` both docker routes share, and the `HARBOR_ID`/`SHIP_ID` in the
compose file's environment block.

## What a pass actually proves

"Agent online" is a weak claim on its own — plenty of wrong configurations still
reach it. The run therefore also:

- **blackholes the public registries** on the node (`127.0.0.1 gcr.io`, plus a
  purge of cached copies) whenever `--local-registry` is on, so an image
  `IMAGE_OVERRIDES` forgot to rewrite is an ImagePullBackOff here rather than a
  silent fallback that only breaks in the customer's air-gapped cluster.
  **Pass `--run-test` with it.** A run that stops at "agent online" pulls
  crane's image and no other, and crane's reference is one the bundle names
  itself — so the whole registry proof stopped at the half that works, and the
  engine was pulled from a path nothing had pushed to for months (#234). The
  engine image check below is what covers the other half, and it needs an
  engine;
- **runs a negative control first** — the same deploy with the CA stripped,
  required to fail with `CERTIFICATE_VERIFY_FAILED` before the real run is
  trusted. A rig that cannot fail proves nothing. Skip with
  `--skip-negative-control` (saves ~2 min);
- **reads the deployed objects back** and checks the generator's promises:
  `AUTH_TOKEN` not in the ConfigMap, proxy credentials not readable there,
  `AUTO_KUBERNETES_UPDATE` matching what the options asked for, `IMAGE_OVERRIDES`
  covering every image the location's funcIds need, every running image coming
  from the private registry, and the CA bundle actually present and parseable
  *inside the crane pod* (not merely mounted);
- **reads the proxy log** for what online-ness cannot show: any `407` (the
  embedded credentials were rejected) and any Kubernetes API traffic that
  `NO_PROXY` should have kept out. Lines the negative control produced are
  excluded from both checks.

Any of these failing turns a green run red, with the specific claim printed.

## Egress containment (`--contain-egress`)

Without it, the proxy log proves the agent *used* the proxy — not that it had
to. `--contain-egress` starts minikube with calico and applies a default-deny
egress NetworkPolicy to the namespace, opening only DNS, the Kubernetes API,
and the proxy. Then it probes from inside the crane pod: `a.blazemeter.com`
must be unreachable directly and reachable through the proxy.

```
egress contained: DNS + apiserver (10.96.0.1:443, 192.168.67.2:8443) + proxy 192.168.67.3:8080, everything else denied
  egress probes from the crane pod: direct rc=28, via proxy rc=0
```

Both halves matter: "direct fails" alone is equally consistent with a policy so
tight nothing works, which would pass a containment check while proving
nothing. Two details this needs to be real rather than decorative:

- **minikube's default CNI accepts NetworkPolicies and enforces none**, so the
  policy would be a silent no-op. `--cni` only applies at cluster creation, so
  if the running profile has no policy enforcer the rig recreates it (it is
  disposable by design).
- **The API rule names both the Service ClusterIP and the endpoint behind it**,
  because policy is evaluated after kube-proxy's DNAT — a rule listing only the
  ClusterIP does not match the packet that actually leaves, and crane loses the
  API access it needs to create engine pods.

Probes run `curl` inside the crane pod, not python: `/usr/local/bin/python3`
there is a crane-agent shim, not an interpreter.

## Engine validation (`--run-test TEST_ID`)

Crane coming online says nothing about the pods crane *creates*. `--run-test`
runs an existing BlazeMeter test on the location so an engine actually spawns,
then checks what crane handed it:

```
test 10000001 repointed at harbor-0a1b2c3d4e5f60718293a4b5 (original locations saved for restore)
started test 10000001 -> master 20000002
  engine pod r-v4-0a1b2c3d4e5f607182931-0-0-c-abcde (Running, 10.244.0.9)
  master 20000002: BOOT_STARTING … TAURUS_ENGINE_READY … DATA_RECEIVED … ENDED
  proxy saw engine upload traffic: data.blazemeter.com=64, storage.blazemeter.com=22
restored the original locations on test 10000001
```

Checked: the engine image comes from the private registry (a *different*
`IMAGE_OVERRIDES` key than crane's own, so crane being right proves nothing
about it), the CA bundle propagated via `KUBERNETES_CA_BUNDLE_MOUNT`,
`HTTPS_PROXY` reached the engine env, the engine's own traffic appears in the
proxy log, and the run reached `ENDED` rather than dying. Engine flows are
identified by the hosts only an engine talks to (`api.ENGINE_UPLOAD_HOSTS`),
**not** by pod IP: pod traffic is SNAT'd to the node address before it reaches
the proxy, so every flow in the log has the same source.

The test's `executions[].locations` are repointed at `harbor-<id>` and restored
in a `finally`; the original is printed so it can be put back by hand if the
process is killed. Engines are sized down with `--engine-cpu` / `--engine-mem`
(default 1 / 4Gi) — the documented 2 CPU / 8Gi will not schedule on a laptop.
Note crane sets the engine's resource *requests* itself, from the location's
`overrideCPU`/`overrideMemory` — 250m / 256Mi only when the location says
nothing. A location at 1 / 4096 produces requests {1, 4Gi} against limits
{2, 8Gi}. Only the limits come from the generated envs; `--run-test` prints the
live gap as `ENGINE SIZING:`.

Engines mount the bundle as a file (`/var/cm/ca-bundle.crt`, subPath) where
crane mounts the directory — the check accepts both.

**Use a script that makes real requests.** A dummy-sampler script still reports
hundreds of samples with plausible response times, so the run summary cannot
tell load generation from none — and none of the engine's egress gets exercised.
`api.create_smoke_test()` builds a 1-VU/1-min Taurus test against a real URL for
exactly this. Note its location goes in the uploaded YAML: for a taurus-script
test, `PATCH /tests/{id}` silently drops `executions`, because the script *is*
the load configuration (`point_test_at_location` returns `None` for such tests
rather than pretending the repoint worked).

**Engines do not proxy their sampler traffic.** With real requests the rig shows
engine→BlazeMeter going through the proxy (`data.blazemeter.com`,
`storage.blazemeter.com`) while engine→SUT does not appear there at all: JMeter
ignores `HTTP(S)_PROXY`, which is an env-var convention of HTTP libraries, not
of the JVM. The manifests cannot fix this — a customer whose SUT is only
reachable through the corporate proxy has to put the proxy in the *test*
(taurus `modules.jmeter.properties` with `http.proxyHost`/`http.proxyPort`, or
JMeter's `-H`/`-P`). Worth saying out loud in a customer conversation, because
crane coming online and results uploading look like proof that "the proxy
works".

Why the CONNECT probe, and why the cluster network rather than a published port: a host
port belongs to whatever already claimed it (an ssh tunnel, a stray Java
process), and the node then reaches *that* instead. The symptom is a plausible
lie — the agent gets `403 Forbidden` from a proxy that was never yours, while
your proxy's log stays empty.

Notes: the CA bundle is mitm CA + public roots, because replacing the trust
store outright is not what a corporate bundle does. Image pulls come from the
kubelet, which ignores the pod's proxy env — that's why the registry rig is
reachable directly. mitmproxy is pinned to `11.1.3`; 12+ dies with SIGILL on
arm64 VMs. Any manifest over 200KB is applied `--server-side`
(`--force-conflicts` with it) — the CA ConfigMap is the one that gets there,
because a real bundle overruns the 256KB cap on kubectl's
last-applied-configuration annotation.

Not covered by any rig: engine→SUT egress through the proxy. JMeter ignores
`HTTP(S)_PROXY` for sampler traffic, so that hop goes direct and never appears
in the proxy log — see *Engines do not proxy their sampler traffic* above. It is
a property of the test, not of the manifests, so the proxy goes in the test
rather than in the generator.

(Two things that *are* covered, in case you are looking for them here: proof
that egress cannot leave except through the proxy is `--contain-egress`, and CA
propagation into engine pods is checked by `--run-test`, which reads
`KUBERNETES_CA_BUNDLE_MOUNT` out of a real engine.)
