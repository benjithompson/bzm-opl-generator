# Live test

`bzm-opl-gen livetest` deploys a bundle for real and passes when the BlazeMeter
API reports the agent with a **fresh heartbeat** in an idle or running state.
That exercises the full chain: RBAC, SCC admission, image pull, egress to
`*.blazemeter.com`, and credentials.

There are **two rigs**, and the bundle decides which: a manifests bundle is
applied to a cluster, a `--format docker` bundle is started with Docker Compose
on this host. Nothing on the command line selects it.

## The compose path (`--format docker` bundles)

Up, online, down:

```
bzm-opl-gen livetest --api-key api-key.json --facts facts.json \
    --manifests out --ship-id <ship-id> --timeout 300
```

No `--namespace` and no `--cluster` — a docker bundle is one container on this
host. A namespace passed anyway is reported as reaching nothing; `--cluster`,
`--local-registry`, `--local-proxy`, `--contain-egress` and `--run-test` are
**refused**, because each is cluster-only and a pass without it would claim
something the run never tested.

It needs a docker daemon with the Compose v2 plugin and nothing else. It
re-renders nothing, so it mints no credential and deploys the bundle exactly as
it sits on disk; `docker compose down --remove-orphans` runs in a `finally`, and
a surviving container is removed by name.

Before starting anything it refuses a directory with **no compose file**, one
whose **`container_name` is not the agent under test**, one whose
`HARBOR_ID`/`SHIP_ID` name another location, and one still carrying a field
left blank (compose's `${…:?}` guard). A blank field written as a **file** —
`ca_bundle`, or the virtual-service TLS pair — is refused too; the rig reads the
file the container would mount, so a variable (`CA_BUNDLE`, `SV_TLS_CERT`,
`SV_TLS_KEY`) pointing at a finished file on the host passes. A file it cannot
read is a note, not a refusal.

### What the compose path does **not** prove

It never starts an engine, so:

- **`-u 0` and `DOCKER_PORT_RANGE` are not exercised.** Crane only uses the
  docker socket and the port range once it starts an engine. Deploying a real
  virtual service exercises the socket, but under the uid the bundle already
  asks for. `DOCKER_PORT_RANGE` does not apply to virtual services at all —
  BlazeMeter publishes them on its own `10000-32000` — see
  [Service virtualization](service-virtualization.md#what-a-live-run-showed).
- There is no private-registry mirror, proxy interception, CA trust check,
  egress containment or negative control; each of those is cluster-only.
- Nothing is read back off the running container: the bundle is checked before
  it starts and the account after.

What it proves is the chain BlazeMeter can see: the image pulled, the container
started, the agent reached `*.blazemeter.com`, and the credential was accepted.

## The cluster path

`--cluster kind` and `--cluster minikube` use a local cluster named
`bzm-opl-test` (crane comes online; engines may not fit laptop resources — use
`--cluster current` against a real cluster for full engine validation). `--keep`
skips teardown.

**A run deletes a cluster only if it created one.** An existing `bzm-opl-test`
is reused and survives teardown, and the run says which happened as it starts
and finishes. A stopped minikube profile is started, and still not deleted. The
one exception is announced: `--contain-egress` recreates a running minikube
profile that has no NetworkPolicy enforcer, because `--cni` applies only at
creation — after which the profile is the run's and teardown deletes it.

When the cluster survives, teardown cleans up inside it:

| what the run made | what teardown does with it |
|---|---|
| the namespace, where the run created it | deletes it, and everything in it |
| a namespace that was already there | deletes the applied `*.yaml`, plus the egress NetworkPolicy by name |
| `127.0.0.1 <registry>` in the node's `/etc/hosts` (`--local-registry`) | removes those lines |

### Reproducing hard customer environments locally

| flag | container | what it proves |
|---|---|---|
| `--local-registry [PORT]` (5001) | `registry:2`, published on the host, pulled via `host.minikube.internal` | air-gapped pulls: `DOCKER_REGISTRY`, `IMAGE_OVERRIDES`, no public-registry fallback |
| `--local-proxy` | `mitmproxy`, joined to the cluster's own docker network | proxy egress **and** custom CA trust |
| `--contain-egress` | calico + a default-deny egress NetworkPolicy | that the proxy is the **only** way out |
| `--run-test TEST_ID` | a real BlazeMeter run on the location | what crane passes to the **engines** it spawns: image, CA, proxy env |

**Pair `--local-registry` with `--run-test`.** No engine exists unless a test
starts one, so a crane-only run pulls no engine image and cannot catch a wrong
engine reference. Without `--run-test` it covers crane's own image and, on
minikube only, the blackholed public registries. The rig warns when the pair is
not run together (except for a location that runs no engine).

`--local-proxy` is deliberately hostile: mitmproxy terminates TLS with its own
CA, so `*.blazemeter.com` is unreachable unless the generated CA configuration
actually reaches the crane process. The rig

1. starts the cluster, then mitmdump (authenticated by default —
   `--proxy-auth user:pass`, or `none` for an open proxy) **on the cluster's
   docker network**, addressed by container IP,
2. reads the mitm CA out of the container and appends it to the public roots,
3. CONNECTs through the proxy from inside the node and requires the attempt to
   appear in the proxy's own log,
4. **regenerates** the bundle from `profile.json` with `proxy` and the CA mode
   under test merged in, so what is deployed is generator output,
5. deploys, waits for the agent to come online, and requires `blazemeter.com`
   lines in the proxy log — online *without* them means the agent bypassed the
   proxy, which fails the test.

The proxy is never published on a host port: a port already owned by something
else (an ssh tunnel, a stray process) would answer instead, and the agent would
get a `403` from a proxy that is not yours while the rig's log stays empty.

### Which CA mode is under test (`--ca-mode`)

| `--ca-mode` | who owns the ConfigMap | what the bundle carries |
|---|---|---|
| `inline` | the generator | `bzm_cacerts.yaml`, holding the PEM |
| `existing` | the **rig**, created before the deploy | a reference by name and key only |
| `file` | the **rig**, under the generator's own name | a certificate file name, and no ConfigMap |

**The default is the mode the bundle was generated for**, read from
`profile.json`, and `inline` where the bundle has none the rig can build (no CA,
or OpenShift trust injection). Naming the flag replaces the bundle's mode, and
the run says which mode it deploys before building anything. `--ca-mode` needs
`--local-proxy`.

- `existing` is the mode most customers use. The rig creates
  `bzm-opl-livetest-trust` holding the MITM CA, under the key `corp-root.pem` —
  deliberately not the default `ca-bundle.crt`, so the run fails unless the
  configured key really reaches `REQUESTS_CA_BUNDLE`.
- `file` is what the web UI generates. The rig builds `blazemeter-cacerts` the
  way the bundle's README says to — `kubectl create configmap
  --from-file=<key>=<file>` — after the negative control.

The rig refuses a ConfigMap of that name it did not create, and deletes the one
it did create when the namespace survives. The negative control clears every CA
mode, not only the inline PEM.

**Without `--local-proxy`, a `file` or `existing` bundle is refused** before the
cluster is built: both name a ConfigMap the bundle does not create, in a
namespace the rig creates, so the pod would sit at `ContainerCreating` for the
whole timeout. An OpenShift-injection bundle is not refused (its ConfigMap is
emitted, empty). A `profile.json` setting two CA modes is always refused.

## The credential a run uses

**A run issues one AUTH_TOKEN, and issuing it revokes the previous one.** The
rig exists to bring an agent online, so running it is the consent. One token per
run, reused by every re-render. `--auth-token <token>` skips the mint — use it
when the agent already deployed must not be disturbed.

A run that re-renders nothing (no `--local-proxy`, no `--run-test`) deploys the
bundle exactly as it sits in `--manifests` and mints nothing. If that bundle
still carries a marker such as `<AUTH_TOKEN>`, the command refuses up front,
naming the field.

**The bundle's identity is checked.** `--manifests` defaults to `out/`, which
holds whatever the last `generate` wrote. Before the cluster exists, the rig
refuses a `HARBOR_ID`/`SHIP_ID` (in the ConfigMap or `profile.json`) that is not
the agent the run was told to test, naming both values, and refuses any
`*.yaml` the generator does not emit. It also refuses a chart directory, a
profile with `service_account_create: false`, and a `file`/`existing` CA bundle
without `--local-proxy` (above). An identity it cannot read is a note.

## What a pass proves

"Agent online" alone is weak — plenty of wrong configurations reach it. The run
also:

- **blackholes the public registries** on the node (`127.0.0.1 gcr.io`, plus a
  purge of cached copies) with `--local-registry`, so an image
  `IMAGE_OVERRIDES` forgot to rewrite fails here rather than silently falling
  back to the public registry;
- **runs a negative control first** — the same deploy with the CA stripped,
  required to fail with `CERTIFICATE_VERIFY_FAILED`. Skip with
  `--skip-negative-control` (saves ~2 min);
- **reads the deployed objects back**: `AUTH_TOKEN` not in the ConfigMap, proxy
  credentials not readable there, `AUTO_KUBERNETES_UPDATE` as requested,
  `IMAGE_OVERRIDES` covering every image the location's funcIds need, every
  running image from the private registry, and the CA bundle present and
  parseable *inside the crane pod*;
- **reads the proxy log** for any `407` (credentials rejected) and any
  Kubernetes API traffic that `NO_PROXY` should have kept out.

Any failure turns the run red with the specific claim printed.

## Egress containment (`--contain-egress`)

The proxy log proves the agent *used* the proxy, not that it had to.
`--contain-egress` starts minikube with calico and applies a default-deny egress
NetworkPolicy allowing only DNS, the Kubernetes API and the proxy, then probes
from inside the crane pod: `a.blazemeter.com` must be unreachable directly and
reachable through the proxy.

```
egress contained: DNS + apiserver (10.96.0.1:443, 192.168.67.2:8443) + proxy 192.168.67.3:8080, everything else denied
  egress probes from the crane pod: direct rc=28, via proxy rc=0
```

- **minikube's default CNI accepts NetworkPolicies and enforces none**, so
  without calico the policy would be a silent no-op.
- **The API rule names both the Service ClusterIP and its endpoint**, because
  policy is evaluated after kube-proxy's DNAT.

## Engine validation (`--run-test TEST_ID`)

`--run-test` runs an existing BlazeMeter test on the location so an engine
actually spawns, then checks what crane handed it:

```
test 10000001 repointed at harbor-0a1b2c3d4e5f60718293a4b5 (original locations saved for restore)
started test 10000001 -> master 20000002
  engine pod r-v4-0a1b2c3d4e5f607182931-0-0-c-abcde (Running, 10.244.0.9)
  master 20000002: BOOT_STARTING … TAURUS_ENGINE_READY … DATA_RECEIVED … ENDED
  proxy saw engine upload traffic: data.blazemeter.com=64, storage.blazemeter.com=22
restored the original locations on test 10000001
```

Checked: the engine image comes from the private registry, the CA bundle
propagated via `KUBERNETES_CA_BUNDLE_MOUNT`, `HTTPS_PROXY` reached the engine,
the engine's own traffic appears in the proxy log, and the run reached `ENDED`.
Engine traffic is identified by the hosts only engines contact
(`footprint.ENGINE_UPLOAD_HOSTS`), not by pod IP — pod traffic is SNAT'd to the
node address first.

The test's `executions[].locations` are repointed at `harbor-<id>` and restored
in a `finally`; the original is printed so it can be put back by hand if the
process is killed. Engines are sized down with `--engine-cpu` / `--engine-mem`
(default 1 / 4Gi). The bundle sets engine requests equal to the limits
(`KUBERNETES_RESOURCES_DEFAULT_*`); a location's `overrideCPU`/`overrideMemory`
replace them. The run prints any gap between requests and limits as
`ENGINE SIZING:`.

**Use a test that makes real requests.** A dummy-sampler script reports
plausible samples while issuing none, so engine egress goes untested. The API
client's `create_smoke_test()` builds a 1-VU/1-min Taurus test against a real
URL. For a taurus-script test the location lives in the uploaded YAML —
`PATCH /tests/{id}` silently drops `executions` for one.

**Engines do not proxy their sampler traffic.** JMeter ignores `HTTP(S)_PROXY`,
so engine→system-under-test traffic goes direct even while results upload
through the proxy. The manifests cannot change this: a customer whose system
under test is only reachable through a proxy has to put it in the *test* (taurus
`modules.jmeter.properties` with `http.proxyHost`/`http.proxyPort`, or JMeter's
`-H`/`-P`). Worth saying in a customer conversation, because an agent online and
results uploading look like proof that "the proxy works".

## Notes

- The CA bundle is the mitm CA plus the public roots, as a corporate bundle is.
- Image pulls come from the kubelet, which ignores the pod's proxy environment,
  so the local registry is reached directly.
- mitmproxy is pinned to `11.1.3`; 12+ dies with SIGILL on arm64 VMs.
- Any manifest over 200KB is applied `--server-side` (with `--force-conflicts`),
  because a real CA bundle overruns the 256KB cap on kubectl's
  last-applied-configuration annotation.
- Probes run `curl` inside the crane pod; its `python3` is an agent shim, not an
  interpreter.
