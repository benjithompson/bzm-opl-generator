# Live test

Success = the BlazeMeter API reports the ship with a **fresh heartbeat** and
idle/running state. That exercises the full chain: RBAC, SCC admission, image
pull, egress to `*.blazemeter.com`, and credentials. `--keep` skips teardown;
`--cluster kind` creates/deletes a disposable `bzm-opl-test` cluster (crane
comes online; engines won't fit laptop resources — use `--cluster current`
against a real cluster for full engine validation).

## Reproducing the hard customer environments locally

Two optional rigs turn a laptop into the awkward network a customer has, and
are torn down with the cluster.

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
4. **regenerates** `out/` from `out/profile.json` with `proxy` + inline
   `ca_bundle` merged in (so the manifests under test are generator output, not
   a hand-patched Deployment),
5. deploys, waits for the agent to come online, and then requires
   `blazemeter.com` lines in the proxy log — online *without* them means the
   agent bypassed the proxy, which fails the test.

## What a pass actually proves

"Agent online" is a weak claim on its own — plenty of wrong configurations still
reach it. The run therefore also:

- **blackholes the public registries** on the node (`127.0.0.1 gcr.io`, plus a
  purge of cached copies) whenever `--local-registry` is on, so an image
  `IMAGE_OVERRIDES` forgot to rewrite is an ImagePullBackOff here rather than a
  silent fallback that only breaks in the customer's air-gapped cluster;
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
proxy log under its pod IP, and the run reached `ENDED` rather than dying.

The test's `executions[].locations` are repointed at `harbor-<id>` and restored
in a `finally`; the original is printed so it can be put back by hand if the
process is killed. Engines are sized down with `--engine-cpu` / `--engine-mem`
(default 1 / 4Gi) — the documented 2 CPU / 8Gi will not schedule on a laptop.
Note crane sets its own resource *requests* (250m / 256Mi) and only the limits
come from the generated envs.

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
arm64 VMs. The CA ConfigMap is applied `--server-side`: a real bundle
overruns the 256KB cap on kubectl's last-applied-configuration annotation.

Not covered by either rig: proof that egress *cannot* leave except through the
proxy (needs `--cni=calico` + a default-deny egress NetworkPolicy), and CA
propagation into engine pods, which only a real test run on the location
exercises.
