"""Live deployment test: apply generated manifests to a real cluster and verify
the agent comes online in the customer's BlazeMeter account.

Success criterion = BlazeMeter API reports the ship with a fresh heartbeat
(state idle/running). That proves the whole chain: image pull, RBAC, SCC,
egress to *.blazemeter.com, and credentials.

Two optional local rigs reproduce the hard customer environments end to end:
  --local-registry  registry:2 container + mirrored images (private registry)
  --local-proxy     mitmproxy container: an authenticated HTTP proxy that also
                    terminates TLS with its own CA (proxy + custom CA trust).
                    The interception is the point -- an agent that ignores the
                    proxy, or fails to trust the mounted bundle, never comes
                    online, so the pass is self-validating.

Cluster targets:
  current   -- whatever kubectl/oc context is active (CRC, remote OpenShift...)
  kind      -- create/reuse a disposable kind cluster (smoke test; engines
               won't fit laptop resources, but crane-online still validates
               the deployment itself)
  minikube  -- create/reuse a disposable minikube profile (docker driver).
               On arm64 the BlazeMeter images are amd64-only: your docker
               runtime's x86 emulation has to be on (works, but engines are
               slow)
"""

import collections
import functools
import glob
import json
import os
import platform
import re
import subprocess
import time

from . import generate

KIND_CLUSTER = "bzm-opl-test"
MINIKUBE_PROFILE = "bzm-opl-test"
REGISTRY_NAME = "bzm-opl-registry"
REGISTRY_IMAGE = "registry:2"
# What the minikube node calls the host's registry. generate() manifests must
# use this as --private-registry when livetesting with --local-registry.
REGISTRY_CLUSTER_HOST = "host.minikube.internal"

PROXY_NAME = "bzm-opl-proxy"
# mitmproxy >= 12 dies with SIGILL on arm64 VMs, whichever docker runtime hosts
# them; 11.1.3 is the newest tag that runs there. Pin it.
PROXY_IMAGE = "mitmproxy/mitmproxy:11.1.3"
PROXY_CA_PATH = "/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem"
PROXY_PORT = 8080               # mitmdump's default; container-internal only
# Sent to *.blazemeter.com through the proxy, so it must stay out of NO_PROXY.
PROXY_NO_PROXY = "kubernetes.default,127.0.0.1,localhost,.svc,.cluster.local"
# Over this, apply server-side (see _apply) -- CA bundles are the usual cause.
LARGE_MANIFEST_BYTES = 200_000


def _run(cmd, check=True, capture=False):
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True,
                          capture_output=capture)


@functools.cache
def cli_tool():
    """oc if available (OpenShift-friendly), else kubectl. Cached: which one is
    on PATH cannot change mid-run, and run() asks several times."""
    for c in ("oc", "kubectl"):
        try:
            subprocess.run([c, "version", "--client"], capture_output=True, check=True)
            return c
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    raise RuntimeError("neither oc nor kubectl found on PATH")


def ensure_kind():
    out = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True)
    if KIND_CLUSTER not in out.stdout.split():
        _run(["kind", "create", "cluster", "--name", KIND_CLUSTER, "--wait", "120s"])
    _run(["kubectl", "config", "use-context", f"kind-{KIND_CLUSTER}"])


def ensure_minikube(insecure_registry=None, cni=None):
    if platform.machine() in ("arm64", "aarch64"):
        print("note: BlazeMeter images are amd64-only -- your docker runtime's "
              "x86 emulation must be enabled for pods to run on this host")
    st = subprocess.run(["minikube", "status", "-p", MINIKUBE_PROFILE,
                         "--format", "{{.Host}}"], capture_output=True, text=True)
    running = st.stdout.strip() == "Running"
    if running and cni and not policy_enforced():
        # minikube's default CNI accepts NetworkPolicies and enforces nothing,
        # so containment would silently be a no-op. --cni only applies at
        # creation; the profile is disposable, so recreate it.
        print(f"recreating the '{MINIKUBE_PROFILE}' profile: egress containment "
              f"needs --cni={cni}, and the running profile has no policy enforcer")
        _run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        running = False
    if not running:
        cmd = ["minikube", "start", "-p", MINIKUBE_PROFILE, "--driver=docker",
               "--cpus=4", "--memory=6g", "--wait=all"]
        if insecure_registry:
            cmd.append(f"--insecure-registry={insecure_registry}")
        if cni:
            cmd.append(f"--cni={cni}")
        _run(cmd)
    elif insecure_registry:
        print(f"note: reusing running minikube profile -- it must already trust "
              f"insecure registry {insecure_registry} (flag only applies at creation)")
    _run(["kubectl", "config", "use-context", MINIKUBE_PROFILE])
    if cni:
        _run(["kubectl", "-n", "kube-system", "rollout", "status",
              "daemonset/calico-node", "--timeout=300s"], check=False)


def policy_enforced():
    """Is there a CNI in the cluster that actually enforces NetworkPolicy?"""
    out = subprocess.run(["kubectl", "-n", "kube-system", "get", "pods",
                          "-l", "k8s-app=calico-node", "-o", "name"],
                         capture_output=True, text=True)
    return bool(out.stdout.strip())


def ensure_registry(port):
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                          REGISTRY_NAME], capture_output=True, text=True)
    if out.stdout.strip() != "true":
        _run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
        _run(["docker", "run", "-d", "--name", REGISTRY_NAME,
              "-p", f"{port}:5000", REGISTRY_IMAGE])


def mirror_images(facts, port, arch="linux/amd64"):
    """Pull the location's images (amd64 -- what the engines are built for),
    push into the local registry under the names generate() writes into
    IMAGE_OVERRIDES / the crane Deployment."""
    from .facts import select_images
    refs = [facts["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in select_images(facts)
    ]
    for ref in refs:
        name = ref.rsplit("/", 1)[-1]
        target = f"localhost:{port}/{name}"
        _run(["docker", "pull", "--platform", arch, ref])
        _run(["docker", "tag", ref, target])
        _run(["docker", "push", target])
    return refs


def ensure_proxy(cluster, user=None, password=None, timeout=60):
    """Start mitmdump on the cluster's own docker network: an HTTP proxy that
    also terminates TLS with its own CA.

    Returns (address, PEM trust bundle). The agent must trust the bundle to
    reach *.blazemeter.com at all, so 'agent online' is proof the CA actually
    reached the crane process -- and the proxy log is proof the traffic went
    through the proxy rather than around it.

    Deliberately NOT published to a host port: the node would then reach it via
    host.minikube.internal, i.e. whatever already owns that port on the
    machine, and the failure mode is silent -- you get the other process's
    errors back and the proxy log stays empty.
    """
    _run(["docker", "rm", "-f", PROXY_NAME], check=False, capture=True)
    cmd = ["docker", "run", "-d", "--name", PROXY_NAME,
           # mitmdump's flow log is block-buffered without a tty; we parse it.
           "-e", "PYTHONUNBUFFERED=1", PROXY_IMAGE, "mitmdump"]
    if user:
        cmd += ["--proxyauth", f"{user}:{password}" if password else user]
    _run(cmd)
    ca = _proxy_exec(["cat", PROXY_CA_PATH], wait=timeout)
    # Replacing the trust store with the mitm CA alone would break every
    # non-intercepted TLS call; real corporate bundles append to public roots.
    roots = _proxy_exec(["python", "-c",
                         "import certifi;print(open(certifi.where()).read())"])
    return _attach_to_cluster_net(cluster), ca.rstrip() + "\n" + roots.lstrip()


def _attach_to_cluster_net(cluster):
    """Join the proxy to the network the cluster nodes live on and return the
    address pods should use."""
    net = {"minikube": MINIKUBE_PROFILE, "kind": "kind"}.get(cluster)
    if not net:
        raise RuntimeError(f"--local-proxy supports minikube/kind, not '{cluster}'")
    _run(["docker", "network", "connect", net, PROXY_NAME], check=False)
    out = subprocess.run(
        ["docker", "inspect", "-f",
         '{{(index .NetworkSettings.Networks "' + net + '").IPAddress}}',
         PROXY_NAME], capture_output=True, text=True)
    ip = out.stdout.strip()
    if not ip:
        raise RuntimeError(f"proxy did not get an address on docker network "
                           f"'{net}': {out.stderr.strip()}")
    return ip


def _proxy_exec(argv, wait=0):
    """docker exec into the proxy, retrying until it's up (CA is generated a
    moment after start)."""
    deadline = time.time() + wait
    while True:
        out = subprocess.run(["docker", "exec", PROXY_NAME] + argv,
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
        if time.time() >= deadline:
            raise RuntimeError(f"proxy container {PROXY_NAME}: "
                               f"{' '.join(argv)} failed: {out.stderr.strip()}")
        time.sleep(2)


def proxy_flows(host_substr="blazemeter.com"):
    """Lines mitmdump logged for a host -- evidence the agent's egress really
    went through the proxy."""
    out = subprocess.run(["docker", "logs", PROXY_NAME],
                         capture_output=True, text=True)
    return [l for l in (out.stdout + out.stderr).splitlines() if host_substr in l]


def proxy_overlay(host, port, ca_pem, user=None, password=None):
    """generate() options that point the agent at the local mitm proxy and
    make it trust the mitm CA (inline mode -- the generator owns the ConfigMap).
    Clears the other CA modes so _ca_cfg() doesn't see two."""
    url = f"http://{host}:{port}"
    proxy = {"http": url, "https": url, "no_proxy": PROXY_NO_PROXY}
    if user:
        proxy["username"] = user
        if password:
            proxy["password"] = password
    return {"proxy": proxy, "ca_bundle": ca_pem,
            "ca_existing_configmap": None, "ca_configmap_key": None,
            "ca_openshift_inject": False}


def verify_proxy_reachable(host, cluster, user=None, password=None):
    """CONNECT through the proxy from inside the cluster and require the attempt
    to show up in OUR proxy's log. A reply alone proves nothing -- some other
    listener answering on that address looks identical to the agent."""
    if cluster != "minikube":
        return True
    creds = f"{user}:{password}@" if user else ""
    before = len(proxy_flows("client connect"))
    _run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
          f"curl -s -o /dev/null --max-time 10 -x http://{creds}{host}:{PROXY_PORT} "
          f"-p https://example.com"], check=False, capture=True)
    if len(proxy_flows("client connect")) > before:
        return True
    print(f"FAILED: {host}:{PROXY_PORT} answered nothing that reached the proxy "
          f"container -- the address in the manifests is not our proxy")
    return False


def ensure_cluster(cluster, insecure_registry=None, cni=None):
    """Idempotent -- safe to call before deploy() when something (the proxy
    overlay) needs the node up early."""
    if cluster == "kind":
        ensure_kind()
    elif cluster == "minikube":
        ensure_minikube(insecure_registry, cni=cni)


def blackhole_public_registries(facts, cluster, private_registry):
    """Route the public image hosts to 127.0.0.1 on the node.

    Without this, an image IMAGE_OVERRIDES forgot to rewrite is quietly pulled
    from the public registry and the run still passes -- exactly the mistake
    that only shows up in the customer's air-gapped cluster. Blackholed, a
    missing override is an ImagePullBackOff here.
    """
    if cluster != "minikube":
        print("note: registry blackhole needs minikube; skipping")
        return []
    from .facts import select_images
    refs = [facts["crane_image"]] + [f"{i['repo']}:{i['tag']}"
                                     for i in select_images(facts)]
    private_host = (private_registry or "").split("/")[0]
    hosts = sorted({r.split("/")[0] for r in refs
                    if "." in r.split("/")[0]} - {private_host})
    for h in hosts:
        _run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
              f"grep -q ' {h}$' /etc/hosts || echo '127.0.0.1 {h}' | sudo tee -a /etc/hosts"],
             check=False, capture=True)
        # A cached copy would satisfy a wrong override without any pull at all.
        _run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
              f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' "
              f"| grep '^{h}/' | xargs -r docker rmi -f"],
             check=False, capture=True)
    print(f"blackholed public registries on the node: {', '.join(hosts)}")
    return hosts


EGRESS_POLICY_NAME = "bzm-opl-egress-containment"


def egress_policy(namespace, proxy_ip, api_targets):
    """Default-deny egress for the whole namespace, with three holes: DNS, the
    Kubernetes API (crane creates engine pods through it), and the proxy.

    api_targets is a list of (ip, port). Both the Service ClusterIP and the real
    endpoint belong there: policy is evaluated after kube-proxy's DNAT, so a
    rule naming only the ClusterIP would not match the packet that leaves.

    This is rig-only -- applied by livetest, never emitted into the customer's
    manifests. Its job is to turn 'the agent used the proxy' into 'the agent had
    no other way out'."""
    api_rules = "".join(
        f"    - to:\n"
        f"        - ipBlock:\n"
        f"            cidr: {ip}/32\n"
        f"      ports:\n"
        f"        - {{protocol: TCP, port: {port}}}\n"
        for ip, port in api_targets)
    return f"""apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {EGRESS_POLICY_NAME}
  namespace: {namespace}
spec:
  podSelector: {{}}
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - {{protocol: UDP, port: 53}}
        - {{protocol: TCP, port: 53}}
{api_rules}    - to:
        - ipBlock:
            cidr: {proxy_ip}/32
      ports:
        - {{protocol: TCP, port: {PROXY_PORT}}}
"""


def apply_egress_policy(cli, namespace, proxy_ip, manifest_dir):
    """Namespace and policy before the workload: a pod that starts under no
    policy could reach the internet before enforcement lands."""
    _run([cli, "create", "ns", namespace], check=False, capture=True)
    targets = _apiserver_targets(cli)
    path = os.path.join(manifest_dir, ".egress-policy.yaml")   # dot: deploy() globs *.yaml
    with open(path, "w") as f:
        f.write(egress_policy(namespace, proxy_ip, targets))
    _run([cli, "-n", namespace, "apply", "-f", path])
    allowed = ", ".join(f"{ip}:{port}" for ip, port in targets)
    print(f"egress contained: DNS + apiserver ({allowed}) + proxy "
          f"{proxy_ip}:{PROXY_PORT}, everything else denied")


def _apiserver_targets(cli):
    """Service ClusterIP and the endpoint behind it -- see egress_policy()."""
    svc = json.loads(subprocess.run(
        [cli, "get", "svc", "kubernetes", "-n", "default", "-o", "json"],
        capture_output=True, text=True).stdout or "{}")
    targets = [(svc["spec"]["clusterIP"], p["port"]) for p in svc["spec"]["ports"]] \
        if svc else []
    eps = json.loads(subprocess.run(
        [cli, "get", "endpoints", "kubernetes", "-n", "default", "-o", "json"],
        capture_output=True, text=True).stdout or "{}")
    for sub in eps.get("subsets", []):
        for addr in sub.get("addresses", []):
            for port in sub.get("ports", []):
                targets.append((addr["ip"], port["port"]))
    if not targets:
        raise RuntimeError("could not resolve the Kubernetes API address")
    return targets


# curl exit codes: 6 = DNS, 7 = refused, 28 = timeout. Anything else means the
# TCP connection got somewhere, however badly it then went.
CURL_DNS, CURL_BLOCKED = 6, (7, 28)


def crane_curl(cli, namespace, args):
    """Run curl inside the crane pod and return its exit code. curl rather than
    python: /usr/local/bin/python3 in the crane image is a crane-agent shim, not
    an interpreter."""
    out = _crane_exec(cli, namespace, f"curl {args}; echo rc=$?")
    for line in reversed(out.splitlines()):
        if line.startswith("rc="):
            return int(line[3:])
    return -1


def assert_egress_contained(cli, namespace, host="a.blazemeter.com"):
    """Two probes from inside the crane pod: BlazeMeter must be unreachable
    directly, and reachable through the proxy.

    The second probe is what makes the first mean anything -- 'direct fails'
    on its own is equally consistent with a policy so tight that nothing works,
    which would pass a containment check while proving nothing.
    """
    direct = crane_curl(cli, namespace,
                         f"-s -o /dev/null --max-time 6 --noproxy '*' https://{host}/")
    proxied = crane_curl(cli, namespace,
                          f'-s -o /dev/null --max-time 20 --cacert "$REQUESTS_CA_BUNDLE" '
                          f"https://{host}/api/v4/web/version")
    print(f"  egress probes from the crane pod: direct rc={direct}, "
          f"via proxy rc={proxied}")
    fails = []
    if direct == CURL_DNS:
        fails.append(f"the crane pod cannot resolve {host} -- the egress policy "
                     f"blocks DNS, so the containment probe proves nothing")
    elif direct not in CURL_BLOCKED:
        fails.append(f"the crane pod reached {host} directly (curl rc={direct}) "
                     f"-- egress is not contained, so using the proxy was optional")
    if proxied != 0:
        fails.append(f"the crane pod could not reach {host} through the proxy "
                     f"either (curl rc={proxied}) -- the policy denies more than "
                     f"it should, so 'direct is blocked' proves nothing")
    return fails


def engine_pods(cli, namespace):
    """Pods crane created for a run -- everything in the namespace that is not
    crane itself."""
    out = subprocess.run([cli, "-n", namespace, "get", "pods", "-o", "json"],
                         capture_output=True, text=True)
    items = json.loads(out.stdout or "{}").get("items", [])
    return [p for p in items
            if not p["metadata"]["name"].startswith("crane-")]


def wait_for_engine_pod(cli, namespace, timeout=420, poll=10):
    """Crane only creates the engine once BlazeMeter hands it the run, so this
    is a wait, not a check. Waits for an IP too -- that is how the engine's own
    traffic is later recognised in the proxy log."""
    deadline = time.time() + timeout
    seen = None
    while time.time() < deadline:
        pods = engine_pods(cli, namespace)
        if pods:
            seen = pods[0]
            if seen["status"].get("podIP"):
                print(f"  engine pod {seen['metadata']['name']} "
                      f"({seen['status'].get('phase')}, {seen['status']['podIP']})")
                return seen
        time.sleep(poll)
    return seen        # spec is still checkable without an IP


# Hosts only an engine talks to: results and artifact upload. Crane itself uses
# a.blazemeter.com. Pod IPs cannot be used to tell them apart -- pod traffic is
# SNAT'd to the node address before it reaches the proxy, so every flow in the
# proxy log has the same source.
ENGINE_UPLOAD_HOSTS = ("data.blazemeter.com", "storage.blazemeter.com")


def engine_upload_marks():
    return {h: len(proxy_flows(h)) for h in ENGINE_UPLOAD_HOSTS}


def engine_proxy_evidence(before):
    """Did the engine's own upload traffic go through the proxy? The env var can
    be present and still ignored by whatever the engine runs; the proxy's log
    is what settles it."""
    new = {h: len(proxy_flows(h)) - before.get(h, 0) for h in ENGINE_UPLOAD_HOSTS}
    total = sum(new.values())
    print("  proxy saw engine upload traffic: " +
          ", ".join(f"{h}={n}" for h, n in new.items()))
    if total:
        return []
    return ["the engine's results never went through the proxy (no new "
            f"{' / '.join(ENGINE_UPLOAD_HOSTS)} flows) -- engines egress around it"]


def sut_hosts_via_proxy():
    """Hosts other than BlazeMeter's that the proxy was asked to reach -- i.e.
    the engine's traffic to the system under test.

    Reported, not asserted: a customer's SUT is often internal and legitimately
    in NO_PROXY. What it answers is whether the engine honours the proxy for its
    own sampler traffic, which propagating HTTPS_PROXY into the env does not."""
    out = subprocess.run(["docker", "logs", PROXY_NAME], capture_output=True, text=True)
    hosts = set()
    for line in (out.stdout + out.stderr).splitlines():
        m = re.search(r"server connect ([^:\s]+):\d+", line)
        if m and not m.group(1).endswith("blazemeter.com"):
            hosts.add(m.group(1))
    return sorted(hosts)


def assert_engine_did_work(client, master_id):
    """Did the engine actually generate load? A dummy-sampler script reaches
    ENDED without a single request leaving the pod, so 'ENDED' alone says
    nothing about whether the engine can drive traffic in this environment."""
    try:
        s = client.master_summary(master_id) or {}
    except Exception as e:
        return [f"could not read the run summary for master {master_id}: {e}"]
    summary = (s.get("summary") or [{}])[0] if isinstance(s.get("summary"), list) else s
    hits = summary.get("hits") or summary.get("samples") or 0
    avg = summary.get("avg") or summary.get("avgResponseTime")
    errors = summary.get("failed") or summary.get("errorsCount") or 0
    print(f"  run summary: {hits} samples, avg {avg}ms, {errors} failed")
    if not hits:
        return [f"the run produced no samples -- the engine never issued a "
                f"request, so nothing about its egress was exercised"]
    if errors and errors >= hits:
        return [f"every one of the {hits} samples failed -- the engine could not "
                f"reach the target from inside the cluster"]
    return []


def run_engine_test(client, cli, namespace, test_id, harbor_id, opts,
                    engine_timeout=420, run_timeout=900):
    """Start a real BlazeMeter test on the location so crane actually spawns an
    engine, then check what that engine was given. The test's own locations are
    repointed at the private location and restored afterwards."""
    before = client.point_test_at_location(test_id, harbor_id)
    if before:
        print(f"test {test_id} repointed at harbor-{harbor_id} "
              f"(original locations saved for restore)")
    else:
        print(f"test {test_id} carries its locations in its script -- left as is; "
              f"it must already target harbor-{harbor_id}")
    before_upload = engine_upload_marks()
    master_id = None
    try:
        master_id = client.start_test(test_id)
        print(f"started test {test_id} -> master {master_id}")
        pod = wait_for_engine_pod(cli, namespace, engine_timeout)
        if not pod:
            return [f"crane never created an engine pod for master {master_id} "
                    f"-- RBAC, resources, or IMAGE_OVERRIDES for the engine"]
        fails = assert_engine_config(pod, opts)
        gap = engine_request_gap(pod)
        if gap:
            print("  ENGINE SIZING: " + gap)
        status = wait_master_done(client, master_id, run_timeout)
        if status != "ENDED":
            fails.append(f"the run finished as {status}, not ENDED -- the engine "
                         f"did not complete and report back to BlazeMeter")
        if opts.get("proxy"):
            fails += engine_proxy_evidence(before_upload)
        fails += assert_engine_did_work(client, master_id)
        if opts.get("proxy"):
            print(f"  non-BlazeMeter hosts the engine reached via the proxy: "
                  f"{sut_hosts_via_proxy() or '(none -- sampler traffic did not use it)'}")
        return fails
    finally:
        if master_id:
            try:
                client.stop_master(master_id)
            except Exception:
                pass                      # already finished; nothing to stop
        if before:
            client.update_test(test_id, before)
            print(f"restored the original locations on test {test_id}")


def assert_engine_config(pod, opts):
    """What crane passed down to the engine it spawned. Everything here is
    invisible to a crane-only live test: the image override for the engine (a
    different IMAGE_OVERRIDES key from crane's own), the CA bundle propagated
    via KUBERNETES_CA_BUNDLE_MOUNT, and the proxy env."""
    from .generate import CA_MOUNT_PATH
    fails = []
    containers = pod["spec"].get("containers", [])
    env = {e["name"]: e.get("value") for c in containers for e in c.get("env", [])}
    images = [c.get("image", "") for c in containers]

    reg = opts.get("private_registry")
    if reg and not all(i.startswith(reg.split("/")[0]) for i in images):
        fails.append(f"engine image is not from the private registry: {images} "
                     f"-- IMAGE_OVERRIDES does not cover the engine")
    if opts.get("ca_bundle") or opts.get("ca_existing_configmap"):
        # Crane mounts the ConfigMap as a directory at /var/cm; the engine gets
        # the bundle file itself (/var/cm/ca-bundle.crt, subPath). Accept both.
        mounts = [m for c in containers for m in c.get("volumeMounts", [])
                  if (m.get("mountPath") or "").startswith(CA_MOUNT_PATH)]
        if not mounts:
            fails.append(f"engine pod has no CA bundle mounted at {CA_MOUNT_PATH} "
                         f"-- KUBERNETES_CA_BUNDLE_MOUNT did not propagate")
        if not env.get("REQUESTS_CA_BUNDLE"):
            fails.append("engine pod has no REQUESTS_CA_BUNDLE -- it cannot trust "
                         "the corporate CA even if the bundle is mounted")
    if opts.get("proxy"):
        if not (env.get("HTTPS_PROXY") or env.get("https_proxy")):
            fails.append("engine pod has no HTTPS_PROXY -- engines would egress "
                         "directly, bypassing the customer's proxy")
    return fails


LIMIT_RANGER_ANNOTATION = "kubernetes.io/limit-ranger"


def engine_request_gap(pod):
    """The gap between what an engine is limited to and what it asks the
    scheduler for, or None when there is none.

    Reported, not asserted: nothing in the manifests can close it. Crane sets
    the engine's requests *explicitly* (250m/256Mi observed on a real run), and
    a LimitRange's defaultRequest only fills fields a pod leaves unset -- the
    engine pod comes back with no kubernetes.io/limit-ranger annotation at all,
    while crane's own test-job pods, which declare nothing, do get one. The
    scheduler packs nodes on requests, so this is what decides how many engines
    land on a node, whatever the limits say.
    """
    for c in pod["spec"].get("containers", []):
        res = c.get("resources") or {}
        req, lim = res.get("requests") or {}, res.get("limits") or {}
        if not lim:
            continue
        short = [k for k in ("cpu", "memory")
                 if k in lim and k in req and req[k] != lim[k]]
        if short:
            touched = LIMIT_RANGER_ANNOTATION in (pod["metadata"].get("annotations") or {})
            note = "" if touched else (
                f" (no {LIMIT_RANGER_ANNOTATION} annotation on the pod: a "
                f"namespace LimitRange did not and cannot change them)")
            return (f"engine {c.get('name')} requests {dict(req)} against limits "
                    f"{dict(lim)} -- the scheduler packs on requests, so engines "
                    f"pack {'; '.join(short)} tighter than they run. Crane sets "
                    f"these explicitly{note}")
    return None


def wait_master_done(client, master_id, timeout=900, poll=20):
    """Poll the run to completion. 'ENDED' with sessions that produced data is
    the only outcome that proves the engine talked to BlazeMeter."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st = client.master_status(master_id)
        status = st.get("status") if isinstance(st, dict) else st
        if status != last:
            print(f"  master {master_id}: {status}")
            last = status
        if status in ("ENDED", "ABORTED", "FAILED"):
            return status
        time.sleep(poll)
    return last


def kget(cli, namespace, kind, name=None):
    """`get -o json` -> parsed object, {} when it is not there. Omit `name` for
    the whole kind (a list, under "items"); a namespace that does not exist yet
    is the normal preflight case, not an error."""
    cmd = [cli, "get", kind, "-o", "json"]
    if name:
        cmd.insert(3, name)
    if namespace:
        cmd[1:1] = ["-n", namespace]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else {}



def assert_live_config(cli, namespace, facts, opts):
    """Read the deployed objects back and check what the generator claims about
    them. A manifest that renders, and even one whose agent comes online, is not
    the same as one that is configured correctly."""
    from .facts import select_images
    fails = []
    cm = kget(cli, namespace, "configmap", "blazemeter-configmap").get("data", {})
    if not cm:
        return ["blazemeter-configmap not found in the cluster"]

    if opts.get("use_secret", True):
        if "AUTH_TOKEN" in cm:
            fails.append("AUTH_TOKEN is in the ConfigMap despite use_secret")
        leaked = [k for k in ("HTTP_PROXY", "HTTPS_PROXY")
                  if "@" in cm.get(k, "")]
        if leaked:
            fails.append(f"proxy credentials readable in the ConfigMap: {leaked}")

    reg = opts.get("private_registry")
    if reg:
        if cm.get("AUTO_KUBERNETES_UPDATE") != "false":
            fails.append("AUTO_KUBERNETES_UPDATE is not false -- auto-update would "
                         "pull from BlazeMeter's public registry")
        want = {i["key"] for i in select_images(facts)}
        have = set(json.loads(cm.get("IMAGE_OVERRIDES") or "{}"))
        if want - have:
            fails.append(f"IMAGE_OVERRIDES missing keys: {sorted(want - have)}")
        for img in _pod_images(cli, namespace):
            if not img.startswith(reg.split("/")[0]):
                fails.append(f"running image is not from the private registry: {img}")

    ca_path = cm.get("REQUESTS_CA_BUNDLE")
    if ca_path:
        n = _crane_exec(cli, namespace,
                        f'grep -c "BEGIN CERTIFICATE" {ca_path} 2>/dev/null || echo 0')
        if n.strip() in ("", "0"):
            fails.append(f"{ca_path} is missing or holds no certificates in the "
                         f"crane pod -- the CA ConfigMap never reached the process")
        else:
            print(f"  CA bundle in pod: {n.strip()} certificates at {ca_path}")
    return fails


def _pod_images(cli, namespace):
    out = subprocess.run([cli, "-n", namespace, "get", "pods", "-l", "role=role-crane",
                          "-o", "jsonpath={.items[*].spec.containers[*].image}"],
                         capture_output=True, text=True)
    return out.stdout.split()


def _crane_exec(cli, namespace, sh):
    out = subprocess.run([cli, "-n", namespace, "exec", "deploy/crane", "--",
                          "sh", "-c", sh], capture_output=True, text=True)
    return out.stdout


def _internal_flows():
    return proxy_flows(":6443") + proxy_flows("kubernetes.default")


def proxy_log_marks():
    """Counts of what proxy_log_failures() watches for, so a later call can
    ignore whatever the negative control's deliberately broken run logged."""
    return {"407": len(proxy_flows("407")), "internal": len(_internal_flows())}


def proxy_log_failures(before=None):
    """What the proxy log says about config that 'agent online' cannot: rejected
    credentials, and in-cluster traffic that NO_PROXY should have excluded."""
    before = before or {"407": 0, "internal": 0}
    fails = []
    if len(proxy_flows("407")) > before["407"]:
        fails.append("the proxy answered 407 -- the credentials the generator "
                     "embedded in HTTP(S)_PROXY were rejected")
    internal = len(_internal_flows()) - before["internal"]
    if internal > 0:
        fails.append(f"Kubernetes API traffic went through the proxy "
                     f"({internal} lines) -- NO_PROXY is wrong")
    return fails


def negative_control(regenerate, overlay, manifest_dir, namespace, cluster,
                     timeout=180):
    """Deploy the same thing minus the CA and require it to fail. A rig that
    cannot fail proves nothing about the run that passes."""
    from .generate import CA_CONFIGMAP
    print("negative control: deploying without the CA bundle, expecting TLS failure")
    regenerate({**overlay, "ca_bundle": None})
    stale = os.path.join(manifest_dir, "bzm_cacerts.yaml")
    if os.path.exists(stale):
        os.remove(stale)          # else deploy() re-applies the previous render
    cli = cli_tool()
    _run([cli, "-n", namespace, "delete", "cm", CA_CONFIGMAP, "--ignore-not-found"],
         check=False, capture=True)
    deploy(manifest_dir, namespace, cluster)
    deadline = time.time() + timeout
    while time.time() < deadline:
        logs = subprocess.run([cli, "-n", namespace, "logs", "deploy/crane",
                               "--tail=200"], capture_output=True, text=True).stdout
        if "CERTIFICATE_VERIFY_FAILED" in logs:
            print("negative control OK: without the CA the agent cannot verify "
                  "BlazeMeter's certificate")
            return True
        time.sleep(10)
    print("FAILED: negative control -- the agent did not fail without the CA, so "
          "a pass would not prove the CA trust config works")
    return False


def _apply(cli, namespace, path):
    """Client-side apply stashes the whole object in the
    kubectl.kubernetes.io/last-applied-configuration annotation, which the API
    server caps at 256KB -- a full CA trust bundle blows past that. Server-side
    apply keeps no such copy."""
    cmd = [cli, "-n", namespace, "apply", "-f", path]
    if os.path.getsize(path) > LARGE_MANIFEST_BYTES:
        cmd += ["--server-side", "--force-conflicts"]
    _run(cmd)


def deploy(manifest_dir, namespace, cluster="current", insecure_registry=None):
    ensure_cluster(cluster, insecure_registry)
    cli = cli_tool()
    _run([cli, "get", "ns", namespace], check=False)
    _run([cli, "create", "ns", namespace], check=False)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _apply(cli, namespace, f)
    _run([cli, "-n", namespace, "rollout", "status", "deploy/crane", "--timeout=300s"])
    return cli


def wait_online(client, harbor_id, ship_id, timeout=600, poll=15):
    """Poll BlazeMeter until the ship heartbeats fresher than test start."""
    start = time.time()
    while time.time() - start < timeout:
        harbor = client.private_location(harbor_id)
        ship = next((s for s in harbor.get("ships", []) if s["id"] == ship_id), None)
        if ship:
            hb, state = ship.get("lastHeartBeat") or 0, ship.get("state")
            fresh = hb >= start - 60
            print(f"  ship={ship_id} state={state} heartbeat_age={time.time()-hb:.0f}s")
            if fresh and state in ("idle", "running"):
                return True
        time.sleep(poll)
    return False


def teardown(manifest_dir, namespace, cluster="current"):
    if cluster == "kind":
        _run(["kind", "delete", "cluster", "--name", KIND_CLUSTER], check=False)
        return
    if cluster == "minikube":
        _run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        return
    cli = cli_tool()
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _run([cli, "-n", namespace, "delete", "-f", f, "--ignore-not-found"], check=False)


def run(client, manifest_dir, namespace, harbor_id, ship_id,
        cluster="current", timeout=600, keep=False,
        facts=None, local_registry=None,
        local_proxy=None, proxy_user=None, proxy_pass=None, regenerate=None,
        negative_control_check=True, opts=None, contain_egress=False,
        run_test=None, engine_cpu="1", engine_mem="4Gi"):
    """regenerate(overlay) -- callback that re-renders the manifests in
    manifest_dir with extra generate() options merged in. Required with
    --local-proxy, whose CA only exists once the proxy container is up.

    opts -- the generate() options the manifests were built from; enables the
    read-back assertions in assert_live_config()."""
    ok = False
    try:
        insecure = None
        if local_registry:
            ensure_registry(local_registry)
            refs = mirror_images(facts, local_registry)
            print(f"mirrored {len(refs)} images into localhost:{local_registry}")
            insecure = f"{REGISTRY_CLUSTER_HOST}:{local_registry}"
        # The node has to exist before anything can be done to it: joining the
        # proxy to its network, blackholing registries, deploying.
        ensure_cluster(cluster, insecure, cni="calico" if contain_egress else None)
        if local_registry:
            blackhole_public_registries(facts, cluster,
                                        (opts or {}).get("private_registry"))
        # Engines are sized down so one fits a laptop cluster; the default
        # request (2 CPU / 8Gi) would sit Pending forever.
        engine_overlay = {"engine_cpu_limit": engine_cpu,
                          "engine_mem_limit": engine_mem} if run_test else {}
        if engine_overlay and not local_proxy:
            if not regenerate:
                raise RuntimeError("--run-test needs a regenerate callback")
            regenerate(engine_overlay)
            opts = dict(opts or {}, **engine_overlay)
        if local_proxy:
            if not regenerate:
                raise RuntimeError("--local-proxy needs a regenerate callback")
            host, ca_pem = ensure_proxy(cluster, proxy_user, proxy_pass)
            print(f"proxy up at {host}:{PROXY_PORT} "
                  f"({'authenticated' if proxy_user else 'open'}), "
                  f"MITM CA bundle {len(ca_pem)} bytes")
            if not verify_proxy_reachable(host, cluster, proxy_user, proxy_pass):
                return False
            overlay = {**proxy_overlay(host, PROXY_PORT, ca_pem,
                                       proxy_user, proxy_pass), **engine_overlay}
            if contain_egress:
                apply_egress_policy(cli_tool(), namespace, host,
                                    manifest_dir)
            if negative_control_check and not negative_control(
                    regenerate, overlay, manifest_dir, namespace, cluster):
                return False
            regenerate(overlay)
            opts = dict(opts or {}, **overlay)
            # Everything the negative control put in the log belongs to a run
            # that was supposed to fail; only count what happens from here.
            mark, marks = len(proxy_flows()), proxy_log_marks()
        deploy(manifest_dir, namespace, cluster, insecure_registry=insecure)
        print(f"waiting up to {timeout}s for agent to report online in BlazeMeter...")
        ok = wait_online(client, harbor_id, ship_id, timeout)
        why = "agent online in BlazeMeter" if ok else "agent never reported online"
        if local_proxy:
            flows = proxy_flows()[mark:]
            print(f"proxy saw {len(flows)} blazemeter.com log lines")
            for l in flows[-5:]:
                print("  " + l)
            if ok and not flows:
                # Online but nothing in the proxy log = the agent went around
                # it; the proxy config is not actually in force.
                ok, why = False, ("agent online but no blazemeter.com traffic "
                                  "through the proxy -- proxy settings bypassed")
            elif ok:
                why = ("agent online via the MITM proxy -- proxy env and CA "
                       "trust both in force")
        if ok:
            fails = assert_live_config(cli_tool(), namespace,
                                       facts, opts or {})
            if local_proxy:
                fails += proxy_log_failures(marks)
            if contain_egress:
                fails += assert_egress_contained(cli_tool(), namespace)
            if run_test:
                fails += run_engine_test(client, cli_tool(), namespace,
                                         run_test, harbor_id, opts or {})
            for f in fails:
                print("  CONFIG FAILURE: " + f)
            if fails:
                ok, why = False, (f"agent online but {len(fails)} configuration "
                                  f"check(s) failed")
        print(f"LIVE TEST {'PASSED' if ok else 'FAILED'}: {why}")
    finally:
        if not keep:
            teardown(manifest_dir, namespace, cluster)
            if local_registry:
                _run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
            if local_proxy:
                _run(["docker", "rm", "-f", PROXY_NAME], check=False, capture=True)
    return ok


def sv_mocks(cli, namespace):
    """Deployed virtual services in `namespace`, as
    [{"name", "port", "harbor", "ship"}].

    Read off the pods rather than the BlazeMeter API. There *is* an API for this
    -- GET https://mock.blazemeter.com/api/v1/workspaces/<ws>/service-mocks
    returns containerName and endpoints -- but it lives on a different host to
    the rest of the tool's calls, needs the workspace id (which facts.json does
    not carry), and answers what BlazeMeter believes rather than what is running.
    The pod is the deployed truth, and carries the harbor/ship ids that
    profile.json omits. Deduped, because a mid-rollout namespace can hold two
    pods for the same mock.
    """
    return _sv_mocks(kget(cli, namespace, "pods").get("items", []))


def _sv_mocks(pods):
    found = {}
    for pod in pods:
        labels = (pod.get("metadata") or {}).get("labels") or {}
        name = labels.get(generate.SV_POD_NAME_LABEL)
        if not name:
            continue                      # crane itself, engines, test jobs
        harbor = labels.get(generate.SV_POD_HARBOR_LABEL)
        ports = [p.get("containerPort")
                 for c in (pod.get("spec") or {}).get("containers") or []
                 for p in c.get("ports") or [] if p.get("containerPort")]
        if ports:
            found[name] = {"name": name, "port": ports[0], "harbor": harbor,
                           "ship": labels.get(generate.SV_POD_SHIP_LABEL)}
    return [found[n] for n in sorted(found)]


# Why a namespace could not be read, as far as the two CLIs let it be told
# apart. kget() flattens every failure to {}, which is right for its callers --
# they are mid-livetest, where a working cluster is a precondition. The web UI
# is the opposite case: it is API-only by design and plenty of the people
# running it have no kubecontext at all, so it has to say which of these it was
# instead of showing a dead panel.
SV_READ_OK = "ok"
SV_READ_NO_CLI = "no_cli"
SV_READ_NO_CONTEXT = "no_context"
SV_READ_DENIED = "denied"
SV_READ_NO_MOCKS = "no_mocks"

SvClusterRead = collections.namedtuple("SvClusterRead", "status mocks detail")


def sv_read(namespace, timeout=15):
    """sv_mocks() for a caller that may have no cluster: the same read, plus
    which way it failed.

    `timeout` exists because an unreachable API server makes kubectl retry
    rather than fail, and a browser request cannot wait it out; the CLI has no
    such deadline because a person watching a terminal can Ctrl-C.
    """
    try:
        cli = cli_tool()
    except RuntimeError as e:
        return SvClusterRead(SV_READ_NO_CLI, [], str(e))
    cmd = [cli, "get", "pods", "-n", namespace, "-o", "json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return SvClusterRead(SV_READ_NO_CONTEXT, [],
                             f"{cli} did not answer within {timeout}s")
    if out.returncode != 0:
        # stderr carries the diagnosis; fall back to stdout for the rare tool
        # build that prints there.
        err = (out.stderr or out.stdout or "").strip()
        return SvClusterRead(_sv_read_reason(err), [], err)
    pods = json.loads(out.stdout or "{}").get("items", [])
    mocks = _sv_mocks(pods)
    return SvClusterRead(SV_READ_OK if mocks else SV_READ_NO_MOCKS, mocks, "")


def _sv_read_reason(stderr):
    """Classify a failed `get pods` by what the tool printed. There is no
    machine-readable form of this -- kubectl and oc exit 1 for all of it -- so
    the message is the only signal."""
    e = stderr.lower()
    if "forbidden" in e or "unauthorized" in e or "must be logged in" in e:
        # Authenticated but not permitted, or no longer authenticated: either
        # way the fix is credentials, not a different namespace.
        return SV_READ_DENIED
    if "not found" in e or "notfound" in e:
        # `namespaces "x" not found`. A namespace that is not there answers the
        # same question as an empty one -- nothing is deployed to expose yet.
        return SV_READ_NO_MOCKS
    # Everything else -- no kubeconfig, no current context, connection refused,
    # DNS failure, a cluster address that has moved. One reason rather than
    # five, because the way forward is identical and the raw message travels
    # alongside as the detail.
    return SV_READ_NO_CONTEXT
