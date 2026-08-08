"""Live deployment test: start a generated bundle for real and verify the agent
comes online in the customer's BlazeMeter account.

Two rigs, picked off the bundle rather than off a flag (bundle_platform):
`run()` applies manifests to a cluster, and `run_compose()` starts a docker
bundle with `docker compose up -d` on this host's daemon. They share
wait_online, because the success criterion is a fact about the account rather
than about either platform, and nothing else -- everything below the account is
cluster-shaped.

Success criterion = BlazeMeter API reports the ship with a fresh heartbeat
(state idle/running). That proves the whole chain: image pull, RBAC, SCC,
egress to *.blazemeter.com, and credentials. On the compose path it proves the
image pull, the egress and the credential; there is no RBAC, no SCC and no
engine (see docs/live-test.md for what that leaves unproven).

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
import tempfile
import time

from . import generate
# Which hosts an engine uploads to is a fact about BlazeMeter, not about this
# rig -- doctor probes them and the planner names them as required egress, and
# neither should have to import the deploy rig to find out.
from .api import ENGINE_UPLOAD_HOSTS

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

# The rig's own trust-bundle ConfigMap, for --ca-mode existing. The key is
# deliberately NOT `ca-bundle.crt`: that is what _ca_cfg falls back to when
# ca_configmap_key is unset, so a run using it would pass whether or not the
# configured key reached anything -- a proof that holds for the wrong reason,
# which is the failure the negative control exists to stop elsewhere. A name of
# the rig's own, so nothing a customer or a platform team owns is ever the
# object under test (see ensure_ca_configmap, which refuses one it did not make).
CA_RIG_CONFIGMAP = "bzm-opl-livetest-trust"
CA_RIG_KEY = "corp-root.pem"
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


# What this run made, and may therefore destroy (#226). One record rather than
# a widening list of booleans on teardown(), because they are one question asked
# about three things: the cluster, the namespace inside it, and the node's
# /etc/hosts. Every field's default is the safe answer -- a run that fell over
# before it learned anything owns nothing.
class Owned(collections.namedtuple(
        "Owned", "cluster namespace blackholed ca_configmap")):
    __slots__ = ()

    def __new__(cls, cluster=False, namespace=False, blackholed=(),
                ca_configmap=False):
        return super().__new__(cls, cluster, namespace, tuple(blackholed),
                               ca_configmap)


def minikube_profile_exists():
    """Is the profile on disk at all?

    `minikube status` cannot answer this: it reports a libmachine host state,
    and a profile that exists reads Running, Stopped, Paused, Saved, Starting,
    Stopping, Error or Timeout depending on what the docker runtime did last.
    Reading "it exists" off a list of the states somebody remembered fails in
    the destructive direction -- an unlisted state means absent, means ours,
    means deleted.

    An unreadable answer is "it was already there". The two wrong answers are
    not equal: a wrong no deletes somebody's cluster, and a wrong yes leaves one
    behind, having said so."""
    out = subprocess.run(["minikube", "profile", "list", "-o", "json"],
                         capture_output=True, text=True)
    try:
        doc = json.loads(out.stdout)
        groups = [g for g in doc.values() if isinstance(g, list)]
    except (ValueError, TypeError, AttributeError):
        print(f"note: could not read the minikube profile list, so "
              f"'{MINIKUBE_PROFILE}' is treated as one this run did not create "
              f"-- teardown will leave it up")
        return True
    return any(p.get("Name") == MINIKUBE_PROFILE
               for g in groups for p in g if isinstance(p, dict))


def ensure_kind(announce=True):
    """True if this run created the cluster, False if it reused one already
    there. The answer is what teardown is allowed to delete -- see #226.

    `announce` is off for the caller that throws the answer away. deploy()
    calls this a second time, after run() has already created the cluster, and
    the reuse message it printed then -- "this run will not delete it" -- was
    the opposite of what teardown went on to do. A function that drops the
    answer must not narrate it either."""
    out = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True)
    created = KIND_CLUSTER not in out.stdout.split()
    if created:
        _run(["kind", "create", "cluster", "--name", KIND_CLUSTER, "--wait", "120s"])
    elif announce:
        print(f"reusing the kind cluster '{KIND_CLUSTER}' -- this run will not "
              f"delete it")
    _run(["kubectl", "config", "use-context", f"kind-{KIND_CLUSTER}"])
    return created


def ensure_minikube(insecure_registry=None, cni=None, announce=True):
    """True if this run created the profile, False if one was already there.

    The recreate below is the one place the rig deletes a cluster it did not
    build, and it stays: a profile with no policy enforcer makes containment a
    silent no-op, the flag only applies at creation, and this profile is
    disposable by design. It also makes the answer honest -- after a recreate
    the profile running is this run's."""
    if platform.machine() in ("arm64", "aarch64"):
        print("note: BlazeMeter images are amd64-only -- your docker runtime's "
              "x86 emulation must be enabled for pods to run on this host")
    # Existing and running are two questions, and only the first decides whose
    # profile it is: starting somebody's stopped profile is not creating it, and
    # a run that deleted one afterwards would be #226 with an extra step.
    existed = minikube_profile_exists()
    st = subprocess.run(["minikube", "status", "-p", MINIKUBE_PROFILE,
                         "--format", "{{.Host}}"], capture_output=True, text=True)
    running = existed and st.stdout.strip() == "Running"
    recreated = False
    # Before policy_enforced(), which reads whatever cluster kubectl is pointed
    # at rather than this profile. Asked in the wrong order it answered about
    # the standing kind testbed or CRC, and now decides a delete as well as a
    # recreate -- so the context has to be this profile's first.
    if existed:
        _run(["kubectl", "config", "use-context", MINIKUBE_PROFILE], check=False)
    if running and cni and not policy_enforced():
        # minikube's default CNI accepts NetworkPolicies and enforces nothing,
        # so containment would silently be a no-op. --cni only applies at
        # creation; the profile is disposable, so recreate it.
        print(f"recreating the '{MINIKUBE_PROFILE}' profile: egress containment "
              f"needs --cni={cni}, and the running profile has no policy enforcer")
        _run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        running, recreated = False, True
    if not running:
        cmd = ["minikube", "start", "-p", MINIKUBE_PROFILE, "--driver=docker",
               "--cpus=4", "--memory=6g", "--wait=all"]
        if insecure_registry:
            cmd.append(f"--insecure-registry={insecure_registry}")
        if cni:
            cmd.append(f"--cni={cni}")
        _run(cmd)
    if existed and not recreated:
        if announce:
            print(f"reusing the minikube profile '{MINIKUBE_PROFILE}' -- "
                  f"this run will not delete it")
        if insecure_registry:
            # True of a stopped profile too: --insecure-registry applies when
            # the profile is created, and starting one is not creating it.
            print(f"note: it must already trust insecure registry "
                  f"{insecure_registry} (flag only applies at creation)")
    _run(["kubectl", "config", "use-context", MINIKUBE_PROFILE])
    if cni:
        _run(["kubectl", "-n", "kube-system", "rollout", "status",
              "daemonset/calico-node", "--timeout=300s"], check=False)
    return recreated or not existed


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
    from .facts import image_refs
    refs = image_refs(facts)
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


def proxy_overlay(host, port, ca_pem, user=None, password=None,
                  ca_mode="inline"):
    """generate() options that point the agent at the local mitm proxy and make
    it trust the mitm CA.

    Two CA modes, because both are real customer configurations and only one had
    ever been deployed under interception (#227):

      inline    -- the generator owns the ConfigMap and writes the PEM into it.
      existing  -- the *rig* owns a ConfigMap (ensure_ca_configmap) and the
                   bundle only references it, which is the mode BlazeMeter
                   recommend and nearly every customer takes.

    Whichever is asked for, the other two are cleared: the overlay is merged
    onto a profile.json that may already carry any of the three, and _ca_cfg
    refuses a bundle where two are set. So this is not "set the mode" but
    "replace whatever mode was there", which is why every key is written on
    both branches rather than only the one being turned on."""
    url = f"http://{host}:{port}"
    proxy = {"http": url, "https": url, "no_proxy": PROXY_NO_PROXY}
    if user:
        proxy["username"] = user
        if password:
            proxy["password"] = password
    existing = ca_mode == "existing"
    return {"proxy": proxy,
            "ca_bundle": None if existing else ca_pem,
            "ca_existing_configmap": CA_RIG_CONFIGMAP if existing else None,
            "ca_configmap_key": CA_RIG_KEY if existing else None,
            "ca_openshift_inject": False}


def ensure_ca_configmap(cli, namespace, ca_pem,
                        name=None, key=None):
    """Create the rig's trust-bundle ConfigMap. True if this run created it.

    Written with `--from-file=<key>=<path>`, which is the explicit form the
    generated README tells a customer to use and for the same reason: the bare
    `--from-file=<path>` BlazeMeter document keys the entry on the file's own
    basename, so a temp file would land under a key nothing mounts and the pod
    would get an *empty* bundle rather than a missing one.

    **It refuses one it did not create.** The cluster's rule and the namespace's,
    one level further down: this replaces the entire content of a trust bundle,
    so a name that is already taken is somebody else's and the rig does not know
    whose. In a namespace this run created the case cannot arise."""
    name, key = name or CA_RIG_CONFIGMAP, key or CA_RIG_KEY
    out = subprocess.run([cli, "-n", namespace, "get", "cm", name],
                         capture_output=True, text=True)
    if out.returncode == 0:
        raise RuntimeError(
            f"a ConfigMap named {name} already exists in namespace "
            f"{namespace}; this run did not create it and will not replace its "
            f"contents. Remove it, or run against a namespace of your own.")
    # A temp file rather than a heredoc: --from-file is the only form that keys
    # the entry explicitly, and it reads a path.
    fd, path = tempfile.mkstemp(prefix="bzm-opl-ca-", suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(ca_pem)
        _run([cli, "-n", namespace, "create", "configmap", name,
              f"--from-file={key}={path}"], check=True)
    finally:
        os.unlink(path)
    print(f"created ConfigMap {name} in {namespace} holding the MITM CA "
          f"under key {key}")
    return True


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


def ensure_cluster(cluster, insecure_registry=None, cni=None, announce=True):
    """Idempotent -- safe to call before deploy() when something (the proxy
    overlay) needs the node up early.

    Returns whether this run created the cluster, which is the only thing that
    licenses teardown to delete it. `current` is never ours: the run was
    pointed at whatever kubectl already had."""
    if cluster == "kind":
        return ensure_kind(announce=announce)
    if cluster == "minikube":
        return ensure_minikube(insecure_registry, cni=cni, announce=announce)
    return False


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
    from .facts import image_refs
    refs = image_refs(facts)
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


# ENGINE_UPLOAD_HOSTS (api.py) is how engine traffic is told apart here: pod IPs
# cannot do it, because pod traffic is SNAT'd to the node address before it
# reaches the proxy, so every flow in the proxy log has the same source.


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


_TAURUS_EXIT = re.compile(r"Taurus completed \(Exit: (\d+)\)")


def assert_engine_exited_cleanly(client, master_id):
    """Did the engine finish, or did it die partway through and get reported as
    a completed run?

    This is the only honest signal there is, and nothing else in the API
    carries it. Measured across a memory bisection where the engine was starved
    by degrees -- every one of these reached ENDED, and every one reported
    **zero failures**:

        limit    samples   avg     exit
        1536MB     1,139   338ms   1      died as the ramp completed
        2048MB     2,435   297ms   1
        2560MB    31,130   322ms   1      died halfway
        3072MB    61,348   322ms   0      the whole run

    The failing runs look *better* than the passing one: fewer errors, lower
    latency, because an engine that dies early only ever samples the gentle
    part of the ramp. Sample count is the only figure that gives it away, and
    only against a baseline you would have to already know. So `ENDED` cannot
    be the success criterion, and neither can the summary.
    """
    try:
        events = (client.master_status(master_id) or {}).get("events") or []
    except Exception as e:
        return [f"could not read the run's events for master {master_id}: {e}"]
    codes = [m.group(1) for m in
             (_TAURUS_EXIT.search(e.get("message") or "") for e in events) if m]
    if not codes:
        # Absent is not zero: an old master whose events have aged out, or a
        # shape this does not recognise, is unverified rather than passed.
        return [f"no Taurus exit status in the events for master {master_id}, so "
                f"whether the engine finished or died partway is unverified"]
    if any(c != "0" for c in codes):
        return [f"the engine exited {codes[0]}, not 0 -- it died partway through "
                f"and the run was still reported as ENDED. The samples it did "
                f"produce come from the part of the ramp it survived, so they "
                f"read as *better* than a healthy run rather than worse"]
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
        fails += assert_engine_size(pod, opts)
        # kget reports a node it could not read as {}, which the assertion needs
        # as None: "not read" and "read, and does not match" are different
        # findings and only the second is a failure.
        node_name = pod["spec"].get("nodeName")
        node = kget(cli, None, "node", node_name) if node_name else None
        fails += assert_engine_pool(pod, node or None, opts)
        gap = engine_request_gap(pod)
        if gap:
            print("  ENGINE SIZING: " + gap)
        print("  ENGINE HEAP: " + engine_heap_note(pod))
        status = wait_master_done(client, master_id, run_timeout)
        if status != "ENDED":
            fails.append(f"the run finished as {status}, not ENDED -- the engine "
                         f"did not complete and report back to BlazeMeter")
        if opts.get("proxy"):
            fails += engine_proxy_evidence(before_upload)
        fails += assert_engine_did_work(client, master_id)
        # After the summary, deliberately: a starved engine's summary looks
        # healthier than a good one's, so the exit code has to be able to
        # contradict it.
        fails += assert_engine_exited_cleanly(client, master_id)
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


def assert_engine_size(pod, opts):
    """The engine runs at the size the bundle configured.

    The limits are the half of engine sizing that *is* ours: crane reads them
    from KUBERNETES_RESOURCES_LIMITS_CPU / _MEMORY, so a bundle that set them
    and an engine that came back with something else means the ConfigMap never
    reached the engine -- which looks identical to a working run until someone
    reads the numbers it produced. (The requests are crane's own and are
    reported by engine_request_gap, not asserted.)
    """
    from .generate import engine_size
    from .quantity import format_cpu, format_memory, parse_cpu, parse_memory
    want_cpu, want_mem = engine_size(opts)
    fails = []
    for c in pod["spec"].get("containers", []):
        lim = (c.get("resources") or {}).get("limits") or {}
        if not lim:
            continue
        got_cpu = parse_cpu(lim["cpu"]) if "cpu" in lim else None
        got_mem = parse_memory(lim["memory"]) if "memory" in lim else None
        if got_cpu is not None and got_cpu != want_cpu:
            fails.append(f"engine {c.get('name')} has a CPU limit of "
                         f"{format_cpu(got_cpu)}, not the configured "
                         f"{format_cpu(want_cpu)}")
        if got_mem is not None and got_mem != want_mem:
            fails.append(f"engine {c.get('name')} has a memory limit of "
                         f"{format_memory(got_mem)}, not the configured "
                         f"{format_memory(want_mem)}")
    return fails


_XMX = re.compile(r"-Xmx(\d+)([kKmMgG]?)")
_XMX_UNIT = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


def engine_heap_bytes(pod):
    """The JVM heap the engine container was actually started with, or None.

    Searched across env values, command and args because the heap is a
    *location* setting pushed by BlazeMeter rather than anything these
    manifests write, so where it lands in the pod is not ours to fix. None is
    "not found here", never "the default" -- an engine whose heap we could not
    read must not be reported as one whose heap matches.
    """
    for c in pod["spec"].get("containers", []):
        haystack = [e.get("value") or "" for e in c.get("env", [])]
        haystack += list(c.get("command") or []) + list(c.get("args") or [])
        for value in haystack:
            m = _XMX.search(value)
            if m:
                return int(m.group(1)) * _XMX_UNIT[m.group(2).lower()]
    return None


def engine_heap_note(pod):
    """The heap against the container limit, as a line to print. Reported and
    not asserted: the pairing is the location's to fix, not the bundle's, and
    a run that produced valid numbers is not a failed live test.

    Worth printing on every run because neither way of getting it wrong looks
    like a memory problem: a heap over the limit is an OOMKill reported as a
    test that stopped, and a heap far under it is capacity the node reserved
    and nothing used.
    """
    from .quantity import format_memory, parse_memory
    heap = engine_heap_bytes(pod)
    limits = [(c.get("resources") or {}).get("limits", {}).get("memory")
              for c in pod["spec"].get("containers", [])]
    limit = next((parse_memory(m) for m in limits if m), None)
    if heap is None:
        return ("no -Xmx found in the engine container's env, command or args "
                "-- heap unread, so its fit against the limit is unverified")
    if limit is None:
        return f"engine JVM heap is {format_memory(heap)}; the pod sets no memory limit"
    pct = round(100 * heap / limit)
    verdict = ""
    if heap >= limit:
        verdict = " -- at or above the limit: OOMKill once the heap fills"
    elif heap * 2 <= limit:
        # `* 2 <=`, matching doctor.check_engine_heap rather than `pct < 50`:
        # the default pairing (engineXmx 4096MB against the documented 8Gi) is
        # exactly half, so a strict comparison stays silent on the single most
        # common way an engine pool is oversized.
        verdict = " -- at or under half the limit, so the rest is reserved and unused"
    return (f"engine JVM heap is {format_memory(heap)} against a "
            f"{format_memory(limit)} limit ({pct}%){verdict}")


def assert_engine_pool(pod, node, opts):
    """The engine landed on the pool it was aimed at.

    Only meaningful with split pools, and it is the one part of the two-pool
    shape that nothing else can confirm: the manifests carry the selector, the
    node carries the labels, and whether crane actually joined them up is
    visible only on a spawned engine. `node` is None when it could not be read,
    which is not the same as a node that does not match -- an unread node says
    so and asserts nothing.
    """
    from .generate import separate_pools, engine_scheduling
    if not separate_pools(opts):
        return []
    selector, _ = engine_scheduling(opts)
    if not selector:
        return []
    name = pod["spec"].get("nodeName")
    if node is None:
        print(f"  ENGINE POOL: node {name or '(unknown)'} could not be read -- "
              f"placement unverified")
        return []
    labels = (node.get("metadata") or {}).get("labels") or {}
    missing = {k: v for k, v in selector.items() if labels.get(k) != v}
    if missing:
        return [f"engine landed on node {name}, which does not carry the engine "
                f"pool's labels {missing} -- crane did not apply "
                f"KUBERNETES_NODE_SELECTOR_JSON, or the pool is mislabelled"]
    print(f"  ENGINE POOL: engine is on {name}, which matches {selector}")
    return []


LIMIT_RANGER_ANNOTATION = "kubernetes.io/limit-ranger"


def engine_request_gap(pod):
    """The gap between what an engine is limited to and what it asks the
    scheduler for, or None when there is none.

    Reported, not asserted: nothing in *the manifests* can close it. The
    requests come from the location's overrideCPU/overrideMemory -- verified
    live, where overrideCPU=1 / overrideMemory=4096 against a bundle asking for
    2 CPU / 8Gi produced requests {1, 4Gi} and limits {2, 8Gi}. Unset they
    default to 250m/256Mi, which is the case on nearly every location and the
    reason this gap is usually large.

    A LimitRange still cannot close it either: crane sets the requests
    explicitly whichever way they were chosen, and a defaultRequest only fills
    fields a pod leaves unset -- the engine pod comes back with no
    kubernetes.io/limit-ranger annotation at all, while crane's own test-job
    pods, which declare nothing, do get one. The scheduler packs nodes on
    requests, so this is what decides how many engines land on a node.
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
                    f"pack {'; '.join(short)} tighter than they run. Raise the "
                    f"location's overrideCPU/overrideMemory to match the limits "
                    f"to close it{note}")
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

    # Against the resolved option rather than a flat "false with a registry":
    # auto_update is settable now, and a run testing a mirrored bundle that
    # deliberately keeps updating itself must be judged on what it asked for.
    # The default resolves to false under a private registry, so the case this
    # check was written for -- auto-update pulling from the public registry the
    # rig has blackholed -- still fires.
    want_auto = "true" if generate.auto_update(opts) else "false"
    if cm.get("AUTO_KUBERNETES_UPDATE") != want_auto:
        fails.append(f"AUTO_KUBERNETES_UPDATE is {cm.get('AUTO_KUBERNETES_UPDATE')!r}, "
                     f"expected {want_auto!r} for these options")

    reg = opts.get("private_registry")
    if reg:
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
    cannot fail proves nothing about the run that passes.

    **All three modes are cleared, not `ca_bundle` alone.** Clearing the inline
    PEM is the whole answer only for an inline run; for an existing-ConfigMap
    run it leaves the reference standing, and a Deployment referencing a
    ConfigMap that is not there does not start at all -- so nothing ever logs
    CERTIFICATE_VERIFY_FAILED and the control fails having tested nothing. What
    is wanted is an agent that runs and cannot verify, which is the bundle with
    no CA configured at all."""
    from .generate import CA_CONFIGMAP
    print("negative control: deploying without the CA bundle, expecting TLS failure")
    regenerate({**overlay, "ca_bundle": None, "ca_existing_configmap": None,
                "ca_configmap_key": None, "ca_openshift_inject": False})
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


# -- the directory is the bundle under test -----------------------------------
#
# deploy() applies <dir>/*.yaml as it finds it, and --manifests defaults to out/,
# which holds whatever the last `generate` left there. #107: a run given
# --ship-id and --auth-token deployed a nine-day-old bundle for a *different*
# agent -- its ConfigMap named another HARBOR_ID/SHIP_ID and its Secret held
# another agent's token, so both flags were ignored in substance. Crane came up
# with an identity it could not register, `rollout status` timed out saying only
# that, and the rig then deleted the cluster. Nothing in the run said which of
# the many possible causes it was.
#
# The cluster build and the 300s rollout are the expensive part of that failure.
# Everything needed to refuse it was in two files on disk the whole time.

CONFIGMAP_FILE = "bzm_configmap.yaml"

# HARBOR_ID / SHIP_ID as templates/configmap writes them. A regex rather than a
# YAML parse, and the reason is no longer "this package has no runtime
# dependencies": it has one now (cryptography, for the certificate check --
# see pyproject.toml), and one that earned its place is not a licence to add
# another. PyYAML would be a second dependency to read two fields out of a file
# this generator wrote itself, which is what generate.existing_auth_token reads
# its own field back the same way for.
_IDENTITY_RE = re.compile(r'^\s*(HARBOR_ID|SHIP_ID):\s*"?([^"\s]+)"?\s*$', re.M)

class BundleCheck(collections.namedtuple("BundleCheck", "refusals notes")):
    """What a directory said about itself: what stops the run, and what could
    not be looked at."""

    def report(self):
        """Print the notes, and hand back the refusals as one message -- or None
        where there are none.

        Only the *reporting* is shared. What a refusal costs the caller is not
        the same on both sides of it: the CLI exits with the message, and
        livetest raises BundleMismatch, which the MCP server catches and which
        must not arrive looking like "the agent did not come online". Four sites
        printed these two lines each; collapsing the disposition into them too
        would flatten a difference that is load-bearing at one of the four.
        """
        for note in self.notes:
            print("note: " + note)
        return "\n".join(self.refusals) or None


class BundleMismatch(RuntimeError):
    """The manifests on disk are not the ones this run was told to test."""


def manifest_identity(manifest_dir):
    """{"HARBOR_ID": ..., "SHIP_ID": ...} as the bundle on disk names them, or
    None where the ConfigMap could not be read at all.

    Three answers, not two, and the third is this module's central rule: a file
    nobody could read (absent, or there and unreadable) is None, a file that is
    there and names neither field is `{}`, and a key is absent from a `{}`-or-
    bigger answer where the file does not carry it. "The directory does not say"
    and "the directory says something else" must not share a representation --
    only the second is a refusal -- and neither may share one with "nothing here
    read the directory".
    """
    text = _file_text(os.path.join(manifest_dir, CONFIGMAP_FILE))
    if text is None:
        return None
    return {m.group(1): m.group(2) for m in _IDENTITY_RE.finditer(text)}


def emitted_yaml_files():
    """Every *.yaml a *manifests* bundle from this generator can hold, taken
    from the generator's own constants rather than restated here -- a list that
    has to be told separately about a new manifest file is a list that goes
    stale, and the failure mode of a stale one is refusing a good bundle.

    The chart's values file is deliberately absent: this rig deploys manifests,
    so a bzm-opl-values.yaml at the top level is either a chart (refused by name
    in cmd_livetest, which reads the profile) or what a chart render left behind
    in a directory since re-rendered as manifests. Applying it is a kubectl
    error at best.
    """
    return frozenset(generate.APPLY_ORDER) | {generate.HOOK_FILE,
                                              generate.SV_EXPOSE_FILE}


def bundle_yaml(manifest_dir):
    """The files deploy() would apply, by basename. Deliberately the same glob:
    a check over a different set than the one applied has a hole in it either
    way round. (glob does not match a leading dot, which is why the rig's own
    .egress-policy.yaml is neither applied by that loop nor judged by this.)"""
    return sorted(os.path.basename(p) for p in
                  glob.glob(os.path.join(manifest_dir, "*.yaml")))


# -- which of the two rigs a directory needs ----------------------------------
#
# There are two, and they share only wait_online: one applies manifests to a
# cluster, the other starts one container on this host with docker compose.
# Nothing on the command line says which, and a flag saying it would be a second
# place to get it wrong -- a --compose left off a docker bundle, or left on a
# manifests one, is exactly the silent run this rig's guards exist to prevent
# (the *.yaml glob comes back empty, every object "applies", and the run waits
# out its whole timeout having created nothing).
#
# So it is read off the bundle, which already knows what it is, and read from
# two places rather than one because they answer at different times:
# profile.json's output_format is the generator's own record, and the compose
# file's presence is what a directory with no profile still says. A directory
# that the two disagree about does not arise from this generator -- a docker
# bundle always carries both -- and where they would, the refusals below name
# what was found rather than picking a winner.

PLATFORM_MANIFESTS = "manifests"
PLATFORM_COMPOSE = "compose"


def bundle_platform(manifest_dir, profile=None):
    """PLATFORM_COMPOSE or PLATFORM_MANIFESTS: which rig this directory needs.

    The profile is the answer where there is one. Where there is not -- a
    hand-assembled directory, or one a `generate` never wrote a profile into --
    the compose file's presence is the answer, because a directory holding one
    is a docker bundle whatever nobody recorded about it.
    """
    if (profile or {}).get("output_format") == "docker":
        return PLATFORM_COMPOSE
    if profile:
        # It said something else -- manifests or helm, and helm is refused by
        # name before this. Not overridden by a stray compose file: a directory
        # holding both is judged by the manifests branch, whose unknown-*.yaml
        # refusal names compose.yaml as the leftover it is.
        return PLATFORM_MANIFESTS
    return (PLATFORM_COMPOSE if os.path.exists(compose_path(manifest_dir))
            else PLATFORM_MANIFESTS)


def compose_path(manifest_dir):
    return os.path.join(manifest_dir, generate.DOCKER_COMPOSE_FILE)


# container_name, as _docker_compose_yaml writes it (_compose_value quotes every
# scalar). A regex for the reason _IDENTITY_RE is one: PyYAML would be a second
# runtime dependency, to read three fields out of a file this generator wrote.
_COMPOSE_NAME_RE = re.compile(r'^\s*container_name:\s*"?([^"\s]+)"?\s*$', re.M)
# A value nobody supplied, as _compose_required writes it. The name is captured
# rather than the whole expression -- the message beside it is a sentence with
# spaces in, and what a refusal here has to name is the variable.
_COMPOSE_UNSET_RE = re.compile(
    r'\$\{' + re.escape(generate.COMPOSE_UNSET_PREFIX) + r'([A-Za-z0-9_]+):\?')


def _file_text(path):
    """The file's text, or None where it could not be read. Absent and empty
    must not share a representation here either: an empty compose file names no
    container, which is a refusal, while a missing one is a different refusal
    and an unreadable one is neither. Every caller of this hands the None
    straight on rather than folding it into an empty answer -- see
    manifest_identity and compose_identity, which is where that was got wrong.

    UnicodeDecodeError beside OSError because "could not be read" is the whole
    of what this function answers and bytes that are not text are one way of it:
    a DER certificate saved over sv-tls.key is a file this rig has to have an
    answer about, and a traceback out of a guard is not one.
    """
    try:
        with open(path) as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def compose_identity(manifest_dir):
    """{"HARBOR_ID", "SHIP_ID", "container_name"} as compose.yaml names them, or
    None where the file could not be read.

    Same three answers as manifest_identity, for the same reason: a key is
    absent where the file does not carry it, `{}` is a file that carries none of
    the three, and None is a file nothing could look at. "The file does not say"
    is a note, "the file says something else" is a refusal, and "nobody read the
    file" is a third note that names no field, because there is no field it
    could honestly name.
    """
    text = _file_text(compose_path(manifest_dir))
    if text is None:
        return None
    # The environment block writes HARBOR_ID/SHIP_ID in the ConfigMap's own
    # shape -- `NAME: "value"` -- so _IDENTITY_RE reads this file too, and the
    # two platforms cannot drift about what an identity looks like.
    found = {m.group(1): m.group(2) for m in _IDENTITY_RE.finditer(text)}
    name = _COMPOSE_NAME_RE.search(text)
    if name:
        found["container_name"] = name.group(1)
    return found


def compose_unset(manifest_dir):
    """The variables this bundle still carries compose's required-variable guard
    for, sorted. Both files, because which one holds the credential is
    use_secret's decision and interpolation reaches an env_file value too.

    Read off the files rather than off profile.json, which carries no
    auth_token: the credential is the value most often left blank, and it is the
    one a profile can never report.
    """
    names = set()
    for name in (generate.DOCKER_COMPOSE_FILE, generate.DOCKER_ENV_FILE):
        text = _file_text(os.path.join(manifest_dir, name)) or ""
        names.update(m.group(1) for m in _COMPOSE_UNSET_RE.finditer(text))
    return sorted(names)


BlankMounts = collections.namedtuple("BlankMounts", "blank unread")


def compose_blank_mounts(manifest_dir):
    """The bundle's mounted files that still carry the marker, and the ones that
    could not be read -- two lists, because they are two answers. Each entry is
    the generator's own mount beside the path that was actually looked at, which
    is not always the bundle's copy (see below).

    The guard above cannot see these, and neither can `placeholder_options`. A
    blank *variable* becomes `${BZM_OPL_UNSET_NAME:?...}` in a file this reads;
    a blank *file* -- `sv_tls_key`, `sv_tls_cert`, `ca_bundle` -- puts the marker
    in the file's own bytes, where `TLS_KEY` beside it holds a container path
    that was never blank. profile.json cannot answer for the one most likely to
    be blank either: `sv_tls_key` is in SECRET_OPTIONS and is deliberately not
    in there. So the bundle's own files are what is read, off
    `generate.DOCKER_FILE_MOUNTS` rather than off a list of names restated here.

    **Resolved through the variable, exactly as `bzm-opl-agent.sh` does it.**
    Every one of these mounts is overridable to a path the host already keeps,
    that escape hatch is what the bundle's own refusal recommends first, and
    `compose up` inherits this process's environment -- so a run with SV_TLS_KEY
    set is one where compose mounts a real key and the marker in the bundle's
    copy reaches nothing. Refusing it would be a guard that survives its own fix.

    A file the directory does not have and no variable points at is not a mount
    this bundle carries, and is skipped rather than reported as missing: which
    of the three a bundle writes is its options' answer, and nothing here holds
    them.
    """
    blank, unread = [], []
    for m in generate.DOCKER_FILE_MOUNTS:
        override = os.environ.get(m.var)
        path = override or os.path.join(manifest_dir, m.file)
        if not override and not os.path.exists(path):
            continue
        text = _file_text(path)
        if text is None:
            unread.append((m, path))
        elif generate.PLACEHOLDER in text:
            blank.append((m, path))
    return BlankMounts(blank, unread)


def bundle_check(manifest_dir, harbor_id, ship_id, profile=None):
    """Is this directory the bundle this run was told to test?

    Returns refusals (deploy nothing) and notes (what could not be checked).
    Costs two file reads, and is worth making before the cluster exists -- or,
    on the compose path, before a container is started against a real account.

    Both platforms are judged here, because the question is the same one and the
    incident behind it (#107) is about a directory rather than about kubectl.
    What differs is where a bundle records its identity: a manifests bundle in
    its ConfigMap, a compose bundle in the compose file's environment block and
    in the container name the two docker routes share.
    """
    if bundle_platform(manifest_dir, profile) == PLATFORM_COMPOSE:
        return _compose_bundle_check(manifest_dir, harbor_id, ship_id, profile)
    refusals, notes = [], []
    path = os.path.join(manifest_dir, CONFIGMAP_FILE)
    claimed = manifest_identity(manifest_dir)
    if claimed is None:
        # Nothing read the file -- it is not there, or it is there and could not
        # be opened. Said as that rather than as "carries no HARBOR_ID/SHIP_ID",
        # which is the other note and is a claim about a file somebody read.
        notes.append(
            f"{path} could not be read, so this bundle's identity was not "
            f"checked against harbor {harbor_id} / ship {ship_id}")
        claimed = {}
    elif not claimed:
        notes.append(
            f"{path} carries no HARBOR_ID/SHIP_ID, so this bundle's identity "
            f"was not checked against harbor {harbor_id} / ship {ship_id}")
    for field, want in (("HARBOR_ID", harbor_id), ("SHIP_ID", ship_id)):
        got = claimed.get(field)
        if claimed and got is None:
            notes.append(f"{path} names no {field}, so it was not checked "
                         f"against {want}")
        elif got and want and got != want:
            refusals.append(
                f"{path} names {field} {got}, but this run was told to test "
                f"{want}. The directory holds the bundle for a different agent: "
                f"crane would come up with an identity BlazeMeter will not "
                f"register, the rollout would time out saying only that, and "
                f"the cluster would be deleted with nothing left to read. "
                f"Re-generate into {manifest_dir}/, or point --manifests at the "
                f"bundle built for {want}")
    refusals += _profile_refusals(manifest_dir, ship_id, profile)
    unknown = [n for n in bundle_yaml(manifest_dir)
               if n not in emitted_yaml_files()]
    if unknown:
        # Refused, not warned. The file is applied, so it is part of what the run
        # deploys: bzm_limitrange.yaml -- the one that happened -- was dropped
        # from this generator precisely because what a LimitRange actually
        # reached was crane's own test-job pods, which declare nothing and were
        # handed a full engine's worth of CPU and memory, reserving capacity a
        # real engine then could not get. So the leftover changes the run it is
        # part of. It is also evidence the directory is an older version's
        # output, which is the failure this whole check exists for, and the
        # identity above can still match by luck. A printed warning would be
        # true and useless -- it scrolls past in the first seconds of a 12-20
        # minute run, which is exactly how the nine-day-old bundle went
        # unnoticed. The fix costs one rm, or a --manifests pointed somewhere
        # empty.
        refusals.append(
            f"{manifest_dir}/ holds {', '.join(unknown)}, which this generator "
            f"does not emit -- and livetest applies every *.yaml in the "
            f"directory, so an older version's leftovers are deployed as part "
            f"of the run and the run stops being a test of generator output. "
            f"Delete them, or generate into an empty directory")
    return BundleCheck(refusals, notes)


def _profile_refusals(manifest_dir, ship_id, profile):
    """What profile.json alone says is wrong, on either platform. Shared because
    the file is the same file and neither question is about kubectl."""
    refusals = []
    path = os.path.join(manifest_dir, generate.PROFILE_FILE)
    # profile.json is what the re-rendering paths merge their overlay onto, and
    # _regenerator prefers the ship_id it finds there over the one on the command
    # line -- so a stale profile deploys the wrong agent even on a path that does
    # re-render, which is why re-rendering could never have been this guard.
    prof_ship = (profile or {}).get("ship_id")
    if prof_ship and ship_id and prof_ship != ship_id:
        refusals.append(
            f"{path} was generated for ship {prof_ship}, not the {ship_id} "
            f"this run was told to test -- the directory is another agent's "
            f"bundle, and a re-render would merge onto it rather than "
            f"correct it")
    # A field somebody left blank. The API server would refuse the object
    # anyway -- <PLACEHOLDER> is not a legal name -- but it refuses it *after*
    # this rig has built a cluster, and the run then spends its whole 12-20
    # minutes reporting that the agent never came online. The same shape as the
    # three guards around it, and cheaper than all of them: it is one read of a
    # file already open.
    blank = generate.placeholder_options(profile or {})
    if blank:
        refusals.append(
            f"{path} was generated with {', '.join(blank)} left blank, so the "
            f"bundle carries {generate.PLACEHOLDER} instead of "
            f"{'those values' if len(blank) > 1 else 'that value'}. Nothing "
            f"here can guess {'them' if len(blank) > 1 else 'it'}: re-generate "
            f"the bundle with {'them' if len(blank) > 1 else 'it'} set")
    return refusals


def _compose_bundle_check(manifest_dir, harbor_id, ship_id, profile):
    """The same question about a docker bundle, asked of the files a docker
    bundle actually has.

    Two of these are new rather than translated, because a compose bundle has no
    *.yaml to validate and no ConfigMap to read an identity out of. Both failures
    they cover are silent from the daemon's end: compose given a directory with
    no compose file reports only that, several layers into a run, and a bundle
    for another agent starts a container that reports to an identity BlazeMeter
    will not register -- which from here looks exactly like an agent that is
    slow to come online, and the run waits out its whole timeout to say so.
    """
    refusals, notes = [], []
    path = compose_path(manifest_dir)
    if not os.path.exists(path):
        # Reachable one way: profile.json says output_format=docker and the
        # compose file is not there -- an older bundle (compose arrived in
        # #177), or a directory somebody tidied. There is nothing to start.
        return BundleCheck([
            f"{manifest_dir}/ is a docker bundle with no "
            f"{generate.DOCKER_COMPOSE_FILE} in it, and this run starts a "
            f"docker bundle with `docker compose up`. Re-generate it: "
            f"{generate.DOCKER_RUN_FILE} on its own is the other route, and "
            f"the two are either/or rather than interchangeable here"], [])
    claimed = compose_identity(manifest_dir)
    want_name = generate.docker_container_name(ship_id) if ship_id else None
    # The file is there -- the refusal above is the only answer to it not being
    # -- so None here is a file nothing could open or decode. That is one note
    # and no per-field ones: a file nobody read names no field, and saying it
    # three times says the wrong thing three times.
    read = claimed is not None
    if not read:
        notes.append(
            f"{path} could not be read, so nothing in it was checked -- not the "
            f"container name against {want_name}, and not the identity against "
            f"harbor {harbor_id} / ship {ship_id}")
        claimed = {}
    got_name = claimed.get("container_name")
    if got_name is None and read:
        notes.append(f"{path} names no container_name, so it was not checked "
                     f"against {want_name}")
    elif want_name and got_name and got_name != want_name:
        refusals.append(
            f"{path} starts a container called {got_name}, but this run was "
            f"told to test ship {ship_id}, whose container is {want_name}. The "
            f"directory holds the bundle for a different agent: crane would "
            f"come up with an identity BlazeMeter will not register, and this "
            f"run would wait out its whole timeout reporting only that the "
            f"agent never came online. Re-generate into {manifest_dir}/, or "
            f"point --manifests at the bundle built for {ship_id}")
    for field, want in (("HARBOR_ID", harbor_id), ("SHIP_ID", ship_id)):
        got = claimed.get(field)
        if got is None:
            if read:
                notes.append(f"{path} names no {field}, so it was not checked "
                             f"against {want}")
        elif got and want and got != want:
            refusals.append(
                f"{path} names {field} {got}, but this run was told to test "
                f"{want}. Re-generate into {manifest_dir}/, or point "
                f"--manifests at the bundle built for {want}")
    # The credential is the value most often left blank and the one profile.json
    # can never report (SECRET_OPTIONS keeps it out), so this reads the files.
    # `compose up` would refuse it too -- that is what the expression is for --
    # but it would do so as a non-zero exit in the middle of a run rather than
    # as a sentence before one, and a container started for a half-finished
    # bundle is a write against a real account.
    unset = compose_unset(manifest_dir)
    if unset:
        refusals.append(
            f"{manifest_dir}/ still carries compose's required-variable guard "
            f"for {', '.join(unset)}, which is what this generator writes where "
            f"a required value was left blank. `docker compose up` refuses it "
            f"and so does this: fill "
            f"{'them' if len(unset) > 1 else 'it'} in, or re-generate the "
            f"bundle with {'them' if len(unset) > 1 else 'it'} set")
    # ...and the other half of the same question, which neither the guard above
    # nor _profile_refusals can reach: a required value written as a *file*
    # rather than as a variable. The marker sits in the file's own bytes, so
    # nothing in compose.yaml carries it and profile.json carries neither the
    # file nor -- for sv_tls_key, the likeliest of the three -- the option.
    # Refused for the reason the credential is: `compose up` would refuse it
    # too, as a non-zero exit partway through a run, and a virtual service
    # published with a placeholder for a private key is a container started
    # against a real account to prove a handshake that cannot happen.
    mounts = compose_blank_mounts(manifest_dir)
    for m, at in mounts.unread:
        notes.append(f"{at} could not be read, so it was not checked for "
                     f"{generate.PLACEHOLDER} -- the {m.what} this bundle "
                     f"mounts is whatever that file holds")
    if mounts.blank:
        files = ", ".join(at for _m, at in mounts.blank)
        opts = ", ".join(m.option for m, _at in mounts.blank)
        names = ", ".join(m.var for m, _at in mounts.blank)
        many = len(mounts.blank) > 1
        refusals.append(
            f"{files} carr{'y' if many else 'ies'} {generate.PLACEHOLDER}, "
            f"which is what this generator writes into a mounted file whose "
            f"option was left blank ({opts}). The container would come up and "
            f"fail on it later -- a rejected handshake rather than an agent "
            f"that never appears -- so the run would report the wrong thing "
            f"about the wrong bundle. Set "
            f"{names} to {'files' if many else 'a file'} this host already has, "
            f"or re-generate the bundle with {opts} filled in")
    refusals += _profile_refusals(manifest_dir, ship_id, profile)
    return BundleCheck(refusals, notes)


def _apply(cli, namespace, path):
    """Client-side apply stashes the whole object in the
    kubectl.kubernetes.io/last-applied-configuration annotation, which the API
    server caps at 256KB -- a full CA trust bundle blows past that. Server-side
    apply keeps no such copy."""
    cmd = [cli, "-n", namespace, "apply", "-f", path]
    if os.path.getsize(path) > LARGE_MANIFEST_BYTES:
        cmd += ["--server-side", "--force-conflicts"]
    _run(cmd)


def ensure_namespace(cli, namespace):
    """True if this run created the namespace.

    The cluster's rule, one level down, and needed for the same reason: once a
    reused cluster survives teardown, whatever was applied into it survives too
    unless something removes it. Deleting the namespace removes the lot --
    including the two things the `*.yaml` sweep cannot reach, the egress
    NetworkPolicy (written to a dotfile) and the pods crane created."""
    out = subprocess.run([cli, "get", "ns", namespace],
                         capture_output=True, text=True)
    if out.returncode == 0:
        print(f"reusing the existing namespace '{namespace}' -- this run will "
              f"not delete it")
        return False
    _run([cli, "create", "ns", namespace], check=False)
    return True


def unblackhole(hosts):
    """Undo blackhole_public_registries on a node that outlives the run.

    It appends `127.0.0.1 <registry>` to the node's /etc/hosts, and used to need
    no undo because the profile was deleted afterwards. On a profile this run
    did not create, leaving it there breaks every later public image pull on
    that node -- an ImagePullBackOff with nothing to connect it to a run that
    reported itself clean. The cached images it also removed are not restored:
    a re-pull is a cost, not a fault."""
    for h in hosts:
        _run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
              f"sudo sed -i '/^127.0.0.1 {h}$/d' /etc/hosts"],
             check=False, capture=True)
    if hosts:
        print(f"restored the node's /etc/hosts: {', '.join(hosts)}")


def deploy(manifest_dir, namespace, cluster="current", insecure_registry=None):
    # The return is dropped deliberately: run() calls ensure_cluster before this
    # and keeps the answer. A caller reaching deploy() on its own path and then
    # calling teardown() gets owned.cluster False and leaks a cluster it built,
    # which is the safe direction of the two.
    #
    # ...and announce=False for the same reason. This call happens after run()
    # has created the cluster, so it finds one and would report "reusing ...
    # this run will not delete it" about a profile teardown is about to delete.
    ensure_cluster(cluster, insecure_registry, announce=False)
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


def teardown(manifest_dir, namespace, cluster="current", owned=None):
    """Delete what this run created, and nothing else.

    `owned` comes from ensure_cluster and ensure_namespace. It used to be
    absent, and a `--cluster kind` run deleted `bzm-opl-test` whether or not it
    had built it -- which on a machine keeping a standing testbed under that
    name destroyed two agents and a serving virtual service (#226). ensure_kind
    reuses a cluster that is already there and is right to; the deletion is what
    had to learn whose it was.

    The default is the safe answer rather than the common one: a run that fell
    over before ensure_cluster returned knows nothing about whose cluster it is,
    and `finally` calls this anyway.

    **A cluster that survives makes everything inside it survive**, which the
    cluster deletion used to hide. So the reuse path is not the `*.yaml` sweep
    alone: it drops the namespace where this run created it, and where it did
    not, removes the egress policy by name -- that one is written to a dotfile
    precisely so deploy()'s glob skips it, so nothing else would reach it. A
    default-deny policy left behind whose only hole is a proxy container the
    `finally` has just removed does not fail the next run: it makes it wait out
    its whole timeout and report that the agent never came online."""
    owned = owned or Owned()
    if owned.cluster and cluster == "kind":
        _run(["kind", "delete", "cluster", "--name", KIND_CLUSTER], check=False)
        return
    if owned.cluster and cluster == "minikube":
        _run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        return
    if cluster in ("kind", "minikube"):
        print(f"leaving the {cluster} cluster up: this run did not create it.")
    cli = cli_tool()
    if cluster == "minikube":
        unblackhole(owned.blackholed)
    if owned.namespace:
        print(f"deleting the namespace '{namespace}', which this run created")
        _run([cli, "delete", "ns", namespace, "--ignore-not-found"], check=False)
        return
    # Reached only where the namespace survives, so the ConfigMap would too --
    # and ensure_ca_configmap refuses one it did not create, which would make
    # the next run into this namespace fail over an object this rig left there.
    if owned.ca_configmap:
        _run([cli, "-n", namespace, "delete", "cm", CA_RIG_CONFIGMAP,
              "--ignore-not-found"], check=False)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _run([cli, "-n", namespace, "delete", "-f", f, "--ignore-not-found"], check=False)
    _run([cli, "-n", namespace, "delete", "networkpolicy", EGRESS_POLICY_NAME,
          "--ignore-not-found"], check=False)


# -- the compose rig ----------------------------------------------------------
#
# `--format docker` had never been live-tested at all: the rig applies YAML to a
# cluster, so a docker bundle was refused outright rather than run. Compose is
# the cheapest live proof this repo can have -- a docker daemon and nothing
# else, where the Kubernetes rig costs a cluster build and 12 to 20 minutes.
#
# Up, online, down, and deliberately nothing more. There is no compose analogue
# of --local-registry, --local-proxy, --contain-egress or the negative control
# here: each of those is cluster-shaped (a registry blackholed on a node, a
# NetworkPolicy an unenforced CNI silently ignores), and reimplementing them for
# one container is another afternoon each. What this does not reach is stated in
# docs/live-test.md rather than left to be discovered -- most of all `-u 0` and
# DOCKER_PORT_RANGE, which only matter once crane starts something, and which
# #184 covers by starting a virtual service rather than an engine.

COMPOSE_TOOL = ["docker", "compose"]
# What a failed run prints of the container's own account of itself. `up -d`
# returns as soon as the container is created, so a crash-looping crane and a
# slow one look identical from here -- and the Kubernetes path gets this for
# free from `rollout status`, which fails loudly. Without it the run reports
# "never came online" over a log nobody read.
COMPOSE_LOG_LINES = 40


def compose_tool():
    """`docker compose`, checked to be there and to be v2.

    Raised rather than discovered halfway: `docker-compose` (the v1 python
    script) is a different command with a different file precedence, and a host
    with neither is a run that cannot start anything.
    """
    try:
        out = subprocess.run(COMPOSE_TOOL + ["version"], capture_output=True,
                             text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise RuntimeError(
            "`docker compose version` does not work here, so this bundle "
            "cannot be started: a docker daemon with the Compose v2 plugin is "
            "the whole of what the compose path needs") from e
    return out.stdout.strip()


def _compose(manifest_dir, *args, check=True):
    # -f rather than a cwd change: compose resolves `env_file:` and every
    # relative bind (the CA bundle, the virtual-service certificate) against the
    # directory of the first -f, which is where those files are.
    return _run(COMPOSE_TOOL + ["-f", compose_path(manifest_dir), *args],
                check=check)


def compose_up(manifest_dir):
    print(f"docker compose: {compose_tool()}")
    _compose(manifest_dir, "up", "-d")


def compose_logs(manifest_dir, lines=COMPOSE_LOG_LINES):
    _compose(manifest_dir, "logs", "--tail", str(lines), check=False)


def compose_down(manifest_dir, container_name=None):
    """`down`, and then a check that it worked.

    --remove-orphans because a bundle re-generated between runs can leave a
    service under the old project's name, and this rig's promise is that it
    leaves the daemon as it found it. The rm afterwards is not belt and braces:
    `down` is a no-op for a container whose compose file has since been
    rewritten out from under it, and the leftover then holds the very name the
    next run needs.
    """
    _compose(manifest_dir, "down", "--remove-orphans", check=False)
    if not container_name:
        return
    left = subprocess.run(["docker", "ps", "-aq", "--filter",
                           f"name=^{container_name}$"],
                          capture_output=True, text=True)
    if left.stdout.strip():
        print(f"note: {container_name} survived `compose down` -- removing it "
              f"by name so it does not hold the name for the next run")
        _run(["docker", "rm", "-f", container_name], check=False, capture=True)


def run_compose(client, manifest_dir, harbor_id, ship_id, timeout=600,
                keep=False, opts=None):
    """Start the docker bundle in `manifest_dir` on this host's daemon, wait for
    the agent to report online in BlazeMeter, and stop it again.

    Success is the same claim wait_online makes for the cluster rig -- a fresh
    heartbeat in a real account -- because it is the same question about the
    same agent. What it proves is narrower and stated in docs/live-test.md.
    """
    # Before the container exists, and outside the try below, whose finally
    # would `compose down` a project this run never started. Same reasoning and
    # the same exception as the Kubernetes path: "this is somebody else's
    # bundle" must not arrive looking like "the agent did not come online".
    bad = bundle_check(manifest_dir, harbor_id, ship_id, opts).report()
    if bad:
        raise BundleMismatch(bad)
    name = generate.docker_container_name(ship_id)
    ok = False
    try:
        compose_up(manifest_dir)
        print(f"waiting up to {timeout}s for agent to report online in "
              f"BlazeMeter...")
        ok = wait_online(client, harbor_id, ship_id, timeout)
        if not ok:
            compose_logs(manifest_dir)
        why = ("agent online in BlazeMeter" if ok else
               "agent never reported online")
        print(f"LIVE TEST {'PASSED' if ok else 'FAILED'}: {why}")
    finally:
        if not keep:
            compose_down(manifest_dir, name)
    return ok


def run(client, manifest_dir, namespace, harbor_id, ship_id,
        cluster="current", timeout=600, keep=False,
        facts=None, local_registry=None,
        local_proxy=None, proxy_user=None, proxy_pass=None, regenerate=None,
        negative_control_check=True, opts=None, contain_egress=False,
        run_test=None, engine_cpu="1", engine_mem="4Gi", ca_mode="inline"):
    """regenerate(overlay) -- callback that re-renders the manifests in
    manifest_dir with extra generate() options merged in. Required with
    --local-proxy, whose CA only exists once the proxy container is up.

    ca_mode -- which CA-trust configuration is under test, "inline" (the
    generator owns the ConfigMap) or "existing" (the rig creates one and the
    bundle references it). Only meaningful with --local-proxy, which is what
    makes the CA load-bearing.

    opts -- the generate() options the manifests were built from; enables the
    read-back assertions in assert_live_config()."""
    # This function is the cluster rig; run_compose is the other one. Refused
    # rather than dispatched, and here rather than only in the CLI, because the
    # MCP server calls run() directly: every argument below is cluster-shaped,
    # so a caller who handed a docker bundle to this one asked for something
    # that does not exist rather than for the same run on another platform.
    # Without it the *.yaml glob comes back empty, nothing is created, and the
    # run waits out its whole timeout to report that the agent never appeared.
    if bundle_platform(manifest_dir, opts) == PLATFORM_COMPOSE:
        raise BundleMismatch(
            f"{manifest_dir}/ is a docker bundle -- one container on a host, "
            f"not a cluster deployment -- and this is the rig that applies "
            f"manifests with kubectl. Start it with `bzm-opl-gen livetest "
            f"--manifests {manifest_dir}` (no --namespace, no --cluster), "
            f"which brings it up with docker compose, waits for the agent, and "
            f"takes it down again")
    # Before anything is created, and outside the try below -- whose finally
    # would tear down a cluster this run never touched. Raised rather than
    # returned False: "the manifests are somebody else's" must not arrive
    # looking like "the agent did not come online", which is the whole defect.
    # Here as well as in the CLI because the MCP server deploys through run().
    bad = bundle_check(manifest_dir, harbor_id, ship_id, opts).report()
    if bad:
        raise BundleMismatch(bad)
    ok = False
    # Filled in as the run learns what it made, and read by teardown in the
    # finally. It starts empty so a run that fails before the cluster is up
    # leaves whatever is there alone (#226).
    owned = Owned()
    try:
        insecure = None
        if local_registry:
            ensure_registry(local_registry)
            refs = mirror_images(facts, local_registry)
            print(f"mirrored {len(refs)} images into localhost:{local_registry}")
            insecure = f"{REGISTRY_CLUSTER_HOST}:{local_registry}"
        # The node has to exist before anything can be done to it: joining the
        # proxy to its network, blackholing registries, deploying.
        owned = owned._replace(cluster=ensure_cluster(
            cluster, insecure, cni="calico" if contain_egress else None))
        # The namespace is created here rather than left to deploy() and
        # apply_egress_policy(), which both reach for it: one asker, one answer
        # about whose it is.
        owned = owned._replace(namespace=ensure_namespace(cli_tool(), namespace))
        if local_registry:
            owned = owned._replace(blackholed=blackhole_public_registries(
                facts, cluster, (opts or {}).get("private_registry")))
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
            # Before the negative control, which deploys with no CA configured
            # at all and so never references this. One creation, one owner.
            if ca_mode == "existing":
                owned = owned._replace(ca_configmap=ensure_ca_configmap(
                    cli_tool(), namespace, ca_pem))
            overlay = {**proxy_overlay(host, PROXY_PORT, ca_pem,
                                       proxy_user, proxy_pass, ca_mode),
                       **engine_overlay}
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
            teardown(manifest_dir, namespace, cluster, owned)
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
    try:
        pods = json.loads(out.stdout or "{}").get("items", [])
    except ValueError:
        # A zero exit whose output will not parse -- a wrapper or plugin that
        # printed before the JSON. Rare, but this answers a browser, and
        # /api/sv-mocks promises no bare errors, so it is an unreadable
        # cluster like any other rather than a traceback.
        return SvClusterRead(SV_READ_NO_CONTEXT, [],
                             (out.stdout or "").strip())
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
