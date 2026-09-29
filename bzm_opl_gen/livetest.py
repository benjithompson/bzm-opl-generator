"""Live deployment test: start a generated bundle for real and verify the agent
comes online in the customer's BlazeMeter account.

Two rigs, picked off the bundle (bundle_check.bundle_platform): `run()` applies
manifests to a cluster, `run_compose()` starts a docker bundle with
`docker compose up -d`. Both succeed on the same fact, wait_online: BlazeMeter
reports the ship idle/running with a fresh heartbeat, which proves image pull,
RBAC, SCC, egress and credentials together. The compose path proves pull,
egress and credential only (see docs/live-test.md).

Optional local rigs for the hard customer environments:
  --local-registry  registry:2 + mirrored images, public registries blackholed
  --local-proxy     mitmproxy: authenticated proxy that terminates TLS with its
                    own CA, so an agent that bypasses the proxy or distrusts the
                    mounted bundle never comes online
  --contain-egress  default-deny NetworkPolicy with holes for DNS, API, proxy
  --run-test        start a real test so crane spawns an engine, then check it

Cluster targets: `current` (whatever context is active), or a disposable
`kind` / `minikube` (docker driver) cluster named bzm-opl-test, reused if there.
BlazeMeter images are amd64-only; on arm64 they run under emulation.
"""

import collections
import glob
import json
import os
import platform
import re
import tempfile
import time

from . import bundle_check, generate, kube
from .api import ENGINE_UPLOAD_HOSTS
from .facts import image_refs, select_images
from .generate import (CA_CONFIGMAP, CA_MOUNT_PATH, cluster_composed_targets,
                       engine_scheduling, engine_size, separate_pools)
from .quantity import format_cpu, format_memory, parse_cpu, parse_memory

KIND_CLUSTER = "bzm-opl-test"
MINIKUBE_PROFILE = "bzm-opl-test"
REGISTRY_NAME = "bzm-opl-registry"
REGISTRY_IMAGE = "registry:2"
# What the minikube node calls the host's registry; generate() must use it as
# --private-registry when livetesting with --local-registry.
REGISTRY_CLUSTER_HOST = "host.minikube.internal"

PROXY_NAME = "bzm-opl-proxy"
# mitmproxy >= 12 dies with SIGILL on arm64 VMs; 11.1.3 is the newest that runs.
PROXY_IMAGE = "mitmproxy/mitmproxy:11.1.3"
PROXY_CA_PATH = "/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem"
PROXY_PORT = 8080               # mitmdump's default; container-internal only
# *.blazemeter.com must stay out of NO_PROXY: that traffic is what is tested.
PROXY_NO_PROXY = "kubernetes.default,127.0.0.1,localhost,.svc,.cluster.local"


class Owned(collections.namedtuple(
        "Owned", "cluster namespace blackholed ca_configmap")):
    """What this run created, and teardown may therefore destroy.

    Every default is the safe answer: a run that failed early owns nothing.
    `ca_configmap` is the name this run created, or None."""
    __slots__ = ()

    def __new__(cls, cluster=False, namespace=False, blackholed=(),
                ca_configmap=None):
        return super().__new__(cls, cluster, namespace, tuple(blackholed),
                               ca_configmap)


# -- the cluster ---------------------------------------------------------------

def minikube_profile_exists():
    """Is the profile on disk at all?

    Read off `minikube profile list`, not `minikube status`: a profile reports
    one of eight host states, and a list of remembered ones fails towards
    deleting somebody's cluster. Unreadable answers True (not ours)."""
    out = kube.quiet(["minikube", "profile", "list", "-o", "json"])
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
    """True if this run created the cluster, False if it reused one.

    `announce=False` for a caller that drops the answer: it must not print
    "will not delete it" about a cluster teardown is about to delete."""
    out = kube.quiet(["kind", "get", "clusters"])
    created = KIND_CLUSTER not in out.stdout.split()
    if created:
        kube.run(["kind", "create", "cluster", "--name", KIND_CLUSTER, "--wait", "120s"])
    elif announce:
        print(f"reusing the kind cluster '{KIND_CLUSTER}' -- this run will not "
              f"delete it")
    kube.run(["kubectl", "config", "use-context", f"kind-{KIND_CLUSTER}"])
    return created


def ensure_minikube(insecure_registry=None, cni=None, announce=True):
    """True if this run created the profile, False if one was already there.

    Existing decides ownership, not running: starting a stopped profile does not
    make it this run's. The one exception is recreating a running profile that
    has no NetworkPolicy enforcer for --contain-egress; that one is announced
    and becomes this run's."""
    if platform.machine() in ("arm64", "aarch64"):
        print("note: BlazeMeter images are amd64-only -- your docker runtime's "
              "x86 emulation must be enabled for pods to run on this host")
    existed = minikube_profile_exists()
    st = kube.quiet(["minikube", "status", "-p", MINIKUBE_PROFILE,
                     "--format", "{{.Host}}"])
    running = existed and st.stdout.strip() == "Running"
    recreated = False
    # Switch context first: policy_enforced() reads whatever kubectl points at.
    if existed:
        kube.run(["kubectl", "config", "use-context", MINIKUBE_PROFILE], check=False)
    if running and cni and not policy_enforced():
        # minikube's default CNI accepts NetworkPolicies and enforces nothing,
        # and --cni applies only at creation.
        print(f"recreating the '{MINIKUBE_PROFILE}' profile: egress containment "
              f"needs --cni={cni}, and the running profile has no policy enforcer")
        kube.run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        running, recreated = False, True
    if not running:
        cmd = ["minikube", "start", "-p", MINIKUBE_PROFILE, "--driver=docker",
               "--cpus=4", "--memory=6g", "--wait=all"]
        if insecure_registry:
            cmd.append(f"--insecure-registry={insecure_registry}")
        if cni:
            cmd.append(f"--cni={cni}")
        kube.run(cmd)
    if existed and not recreated:
        if announce:
            print(f"reusing the minikube profile '{MINIKUBE_PROFILE}' -- "
                  f"this run will not delete it")
        if insecure_registry:
            print(f"note: it must already trust insecure registry "
                  f"{insecure_registry} (flag only applies at creation)")
    kube.run(["kubectl", "config", "use-context", MINIKUBE_PROFILE])
    if cni:
        kube.run(["kubectl", "-n", "kube-system", "rollout", "status",
                  "daemonset/calico-node", "--timeout=300s"], check=False)
    return recreated or not existed


def policy_enforced():
    """Is there a CNI in the cluster that actually enforces NetworkPolicy?"""
    out = kube.quiet(["kubectl", "-n", "kube-system", "get", "pods",
                      "-l", "k8s-app=calico-node", "-o", "name"])
    return bool(out.stdout.strip())


def ensure_cluster(cluster, insecure_registry=None, cni=None, announce=True):
    """Idempotent. Returns whether this run created the cluster -- the only
    thing that licenses teardown to delete it. `current` is never ours."""
    if cluster == "kind":
        return ensure_kind(announce=announce)
    if cluster == "minikube":
        return ensure_minikube(insecure_registry, cni=cni, announce=announce)
    return False


# -- private registry ------------------------------------------------------------

def ensure_registry(port):
    out = kube.quiet(["docker", "inspect", "-f", "{{.State.Running}}", REGISTRY_NAME])
    if out.stdout.strip() != "true":
        kube.run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
        kube.run(["docker", "run", "-d", "--name", REGISTRY_NAME,
                  "-p", f"{port}:5000", REGISTRY_IMAGE])


def mirror_images(facts, port, arch="linux/amd64"):
    """Pull the location's images (amd64) and push them to the local registry
    under the names generate() writes.

    Destinations come from generate.cluster_composed_targets, so the push and
    IMAGE_OVERRIDES agree by construction. The rig pushes to localhost:<port>
    and the node reaches the same registry as host.minikube.internal:<port>, so
    only the path below the host must match. Crane's own image falls through to
    the short form generate._crane_image writes."""
    refs = image_refs(facts)
    reg = f"localhost:{port}"
    composed = cluster_composed_targets(facts, {"private_registry": reg})
    for ref in refs:
        target = composed.get(ref, f"{reg}/{ref.rsplit('/', 1)[-1]}")
        kube.run(["docker", "pull", "--platform", arch, ref])
        kube.run(["docker", "tag", ref, target])
        kube.run(["docker", "push", target])
    return refs


def blackhole_public_registries(facts, cluster, private_registry):
    """Route the public image hosts to 127.0.0.1 on the node, so an image
    IMAGE_OVERRIDES failed to rewrite is an ImagePullBackOff here rather than a
    silent public pull. minikube only."""
    if cluster != "minikube":
        print("note: registry blackhole needs minikube; skipping")
        return []
    refs = image_refs(facts)
    private_host = (private_registry or "").split("/")[0]
    hosts = sorted({r.split("/")[0] for r in refs
                    if "." in r.split("/")[0]} - {private_host})
    for h in hosts:
        kube.run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
                  f"grep -q ' {h}$' /etc/hosts || echo '127.0.0.1 {h}' | sudo tee -a /etc/hosts"],
                 check=False, capture=True)
        # A cached copy would satisfy a wrong override without any pull.
        kube.run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
                  f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' "
                  f"| grep '^{h}/' | xargs -r docker rmi -f"],
                 check=False, capture=True)
    print(f"blackholed public registries on the node: {', '.join(hosts)}")
    return hosts


def unblackhole(hosts):
    """Undo blackhole_public_registries on a node that outlives the run; left in
    place it breaks every later public pull there. Removed cached images are
    not restored (a re-pull is a cost, not a fault)."""
    for h in hosts:
        kube.run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
                  f"sudo sed -i '/^127.0.0.1 {h}$/d' /etc/hosts"],
                 check=False, capture=True)
    if hosts:
        print(f"restored the node's /etc/hosts: {', '.join(hosts)}")


# -- MITM proxy ----------------------------------------------------------------

def ensure_proxy(cluster, user=None, password=None, timeout=60):
    """Start mitmdump on the cluster's own docker network. Returns (address,
    PEM trust bundle): the mitm CA appended to public roots, as a corporate
    bundle is.

    Not published to a host port: the node would then reach whatever already
    owns that port on the machine, and the proxy log would silently stay empty.
    """
    kube.run(["docker", "rm", "-f", PROXY_NAME], check=False, capture=True)
    cmd = ["docker", "run", "-d", "--name", PROXY_NAME,
           # mitmdump's flow log is block-buffered without a tty; we parse it.
           "-e", "PYTHONUNBUFFERED=1", PROXY_IMAGE, "mitmdump"]
    if user:
        cmd += ["--proxyauth", f"{user}:{password}" if password else user]
    kube.run(cmd)
    ca = _proxy_exec(["cat", PROXY_CA_PATH], wait=timeout)
    roots = _proxy_exec(["python", "-c",
                         "import certifi;print(open(certifi.where()).read())"])
    return _attach_to_cluster_net(cluster), ca.rstrip() + "\n" + roots.lstrip()


def _attach_to_cluster_net(cluster):
    """Join the proxy to the cluster nodes' network; the address pods use."""
    net = {"minikube": MINIKUBE_PROFILE, "kind": "kind"}.get(cluster)
    if not net:
        raise RuntimeError(f"--local-proxy supports minikube/kind, not '{cluster}'")
    kube.run(["docker", "network", "connect", net, PROXY_NAME], check=False)
    out = kube.quiet(["docker", "inspect", "-f",
                      '{{(index .NetworkSettings.Networks "' + net + '").IPAddress}}',
                      PROXY_NAME])
    ip = out.stdout.strip()
    if not ip:
        raise RuntimeError(f"proxy did not get an address on docker network "
                           f"'{net}': {out.stderr.strip()}")
    return ip


def _proxy_exec(argv, wait=0):
    """docker exec into the proxy, retrying for `wait` seconds (the CA is
    generated a moment after start)."""
    last = []

    def attempt():
        out = kube.quiet(["docker", "exec", PROXY_NAME] + argv)
        last[:] = [out]
        return out.stdout if out.returncode == 0 and out.stdout.strip() else None

    got = kube.poll_until(attempt, wait, 2)
    if got is None:
        raise RuntimeError(f"proxy container {PROXY_NAME}: "
                           f"{' '.join(argv)} failed: {last[0].stderr.strip()}")
    return got


def _proxy_log():
    out = kube.quiet(["docker", "logs", PROXY_NAME])
    return (out.stdout + out.stderr).splitlines()


def proxy_flows(host_substr="blazemeter.com"):
    """Lines mitmdump logged mentioning `host_substr`."""
    return [line for line in _proxy_log() if host_substr in line]


def proxy_overlay(host, port, ca_pem, user=None, password=None,
                  ca_mode="inline"):
    """generate() options pointing the agent at the local proxy and trusting its
    CA, in one of the CA modes:

      inline    the generator owns the ConfigMap and writes the PEM into it
      existing  the rig creates a ConfigMap of its own name; the bundle names it
      file      the bundle names a certificate file and creates no ConfigMap;
                the rig builds generate.CA_CONFIGMAP from it, as a customer's
                pipeline would

    Starts from generate.no_ca() so every other mode is cleared: the overlay is
    merged onto a profile that may carry any of them, and two is a refusal."""
    url = f"http://{host}:{port}"
    proxy = {"http": url, "https": url, "no_proxy": PROXY_NO_PROXY}
    if user:
        proxy["username"] = user
        if password:
            proxy["password"] = password
    mode = {
        "existing": {"ca_existing_configmap": bundle_check.CA_RIG_CONFIGMAP,
                     "ca_configmap_key": bundle_check.CA_RIG_KEY},
        # Not `ca-bundle.crt`, _ca_cfg's fallback, so the key must really land.
        "file": {"ca_bundle_slot": True, "ca_cert_file": bundle_check.CA_RIG_KEY},
        "inline": {"ca_bundle": ca_pem},
    }[ca_mode]
    return {"proxy": proxy, **generate.no_ca(), **mode}


def ensure_ca_configmap(cli, namespace, ca_pem, name=None, key=None):
    """Create the rig's trust-bundle ConfigMap. True if this run created it.

    Refuses a name that already exists: this replaces a trust bundle's whole
    content, and one the rig did not create is somebody else's. Uses the
    explicit `--from-file=<key>=<path>`; the bare form keys the entry on the
    temp file's name, so the pod would mount an empty bundle."""
    name = name or bundle_check.CA_RIG_CONFIGMAP
    key = key or bundle_check.CA_RIG_KEY
    if kube.quiet([cli, "-n", namespace, "get", "cm", name]).returncode == 0:
        raise RuntimeError(
            f"a ConfigMap named {name} already exists in namespace "
            f"{namespace}; this run did not create it and will not replace its "
            f"contents. Remove it, or run against a namespace of your own.")
    fd, path = tempfile.mkstemp(prefix="bzm-opl-ca-", suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(ca_pem)
        kube.run([cli, "-n", namespace, "create", "configmap", name,
                  f"--from-file={key}={path}"], check=True)
    finally:
        os.unlink(path)
    print(f"created ConfigMap {name} in {namespace} holding the MITM CA "
          f"under key {key}")
    return True


def verify_proxy_reachable(host, cluster, user=None, password=None):
    """CONNECT through the proxy from the node and require the attempt in OUR
    proxy's log: another listener on that address would answer identically."""
    if cluster != "minikube":
        return True
    creds = f"{user}:{password}@" if user else ""
    before = len(proxy_flows("client connect"))
    kube.run(["minikube", "ssh", "-p", MINIKUBE_PROFILE, "--",
              f"curl -s -o /dev/null --max-time 10 -x http://{creds}{host}:{PROXY_PORT} "
              f"-p https://example.com"], check=False, capture=True)
    if len(proxy_flows("client connect")) > before:
        return True
    print(f"FAILED: {host}:{PROXY_PORT} answered nothing that reached the proxy "
          f"container -- the address in the manifests is not our proxy")
    return False


def _internal_flows():
    return proxy_flows(":6443") + proxy_flows("kubernetes.default")


def proxy_log_marks():
    """Counts proxy_log_failures() compares against, so it can ignore what the
    negative control's deliberately broken run logged."""
    return {"407": len(proxy_flows("407")), "internal": len(_internal_flows())}


def proxy_log_failures(before=None):
    """What the proxy log shows that 'agent online' cannot: rejected
    credentials, and in-cluster traffic NO_PROXY should have excluded."""
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
    """Deploy the same bundle with no CA trust and require
    CERTIFICATE_VERIFY_FAILED; a rig that cannot fail proves nothing.

    Every CA mode is cleared (generate.no_ca()), not just the inline PEM: a
    leftover existing/file reference mounts a missing ConfigMap, the pod never
    starts, and the control fails without testing anything."""
    print("negative control: deploying without the CA bundle, expecting TLS failure")
    regenerate({**overlay, **generate.no_ca()})
    stale = os.path.join(manifest_dir, "bzm_cacerts.yaml")
    if os.path.exists(stale):
        os.remove(stale)          # else deploy() re-applies the previous render
    cli = kube.cli_tool()
    kube.run([cli, "-n", namespace, "delete", "cm", CA_CONFIGMAP, "--ignore-not-found"],
             check=False, capture=True)
    deploy(manifest_dir, namespace, cluster)

    def verify_failed():
        logs = kube.quiet([cli, "-n", namespace, "logs", "deploy/crane",
                           "--tail=200"]).stdout
        return "CERTIFICATE_VERIFY_FAILED" in logs

    if kube.poll_until(verify_failed, timeout, 10):
        print("negative control OK: without the CA the agent cannot verify "
              "BlazeMeter's certificate")
        return True
    print("FAILED: negative control -- the agent did not fail without the CA, so "
          "a pass would not prove the CA trust config works")
    return False


# -- egress containment ----------------------------------------------------------

EGRESS_POLICY_NAME = "bzm-opl-egress-containment"


def egress_policy(namespace, proxy_ip, api_targets):
    """Default-deny egress for the namespace with holes for DNS, the Kubernetes
    API (crane creates engines through it) and the proxy. Rig-only, never in a
    customer bundle.

    api_targets is [(ip, port)] holding both the Service ClusterIP and the
    endpoint: policy is evaluated after kube-proxy's DNAT."""
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
    """Namespace and policy before the workload, so no pod starts unpoliced."""
    kube.run([cli, "create", "ns", namespace], check=False, capture=True)
    targets = _apiserver_targets(cli)
    path = os.path.join(manifest_dir, ".egress-policy.yaml")   # dot: deploy() globs *.yaml
    with open(path, "w") as f:
        f.write(egress_policy(namespace, proxy_ip, targets))
    kube.run([cli, "-n", namespace, "apply", "-f", path])
    allowed = ", ".join(f"{ip}:{port}" for ip, port in targets)
    print(f"egress contained: DNS + apiserver ({allowed}) + proxy "
          f"{proxy_ip}:{PROXY_PORT}, everything else denied")


def _apiserver_targets(cli):
    """The API Service's ClusterIP and the endpoints behind it."""
    svc = kube.kget(cli, "default", "svc", "kubernetes")
    targets = [(svc["spec"]["clusterIP"], p["port"]) for p in svc["spec"]["ports"]] \
        if svc else []
    eps = kube.kget(cli, "default", "endpoints", "kubernetes")
    for sub in eps.get("subsets", []):
        for addr in sub.get("addresses", []):
            for port in sub.get("ports", []):
                targets.append((addr["ip"], port["port"]))
    if not targets:
        raise RuntimeError("could not resolve the Kubernetes API address")
    return targets


# curl exit codes: 6 = DNS, 7 = refused, 28 = timeout. Anything else means the
# TCP connection got somewhere.
CURL_DNS, CURL_BLOCKED = 6, (7, 28)


def crane_curl(cli, namespace, args):
    """curl inside the crane pod; its exit code. Not python: the image's
    python3 is a crane-agent shim, not an interpreter."""
    out = kube.crane_exec(cli, namespace, f"curl {args}; echo rc=$?")
    for line in reversed(out.splitlines()):
        if line.startswith("rc="):
            return int(line[3:])
    return -1


def assert_egress_contained(cli, namespace, host="a.blazemeter.com"):
    """From the crane pod, BlazeMeter must be unreachable directly and reachable
    through the proxy. The second probe is what makes the first mean anything:
    a policy that blocks everything also fails the direct probe."""
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


# -- a real engine (--run-test) --------------------------------------------------

def engine_pods(cli, namespace):
    """Pods crane created for a run: everything in the namespace but crane."""
    items = kube.kget(cli, namespace, "pods").get("items", [])
    return [p for p in items if not p["metadata"]["name"].startswith("crane-")]


def wait_for_engine_pod(cli, namespace, timeout=420, poll=10):
    """Wait for crane to create an engine (it does so only once BlazeMeter hands
    it the run) and for the pod to get an IP. Returns the pod, or the last one
    seen without an IP -- its spec is still checkable -- or None."""
    seen = []

    def with_ip():
        pods = engine_pods(cli, namespace)
        if not pods:
            return None
        seen[:] = pods[:1]
        return pods[0] if pods[0]["status"].get("podIP") else None

    pod = kube.poll_until(with_ip, timeout, poll)
    if pod:
        print(f"  engine pod {pod['metadata']['name']} "
              f"({pod['status'].get('phase')}, {pod['status']['podIP']})")
        return pod
    return seen[0] if seen else None


def wait_master_done(client, master_id, timeout=900, poll=20):
    """Poll the run to a terminal status; the last status seen on timeout."""
    last = [None]

    def ended():
        st = client.master_status(master_id)
        status = st.get("status") if isinstance(st, dict) else st
        if status != last[0]:
            print(f"  master {master_id}: {status}")
            last[0] = status
        return status if status in ("ENDED", "ABORTED", "FAILED") else None

    return kube.poll_until(ended, timeout, poll) or last[0]


# Engine traffic is identified by ENGINE_UPLOAD_HOSTS, which only engines use:
# pod traffic is SNAT'd to the node address before the proxy sees it, so every
# flow in the log has the same source.

def engine_upload_marks():
    return {h: len(proxy_flows(h)) for h in ENGINE_UPLOAD_HOSTS}


def engine_proxy_evidence(before):
    """Did the engine's own upload traffic go through the proxy? The env var can
    be set and still ignored by whatever the engine runs."""
    new = {h: len(proxy_flows(h)) - before.get(h, 0) for h in ENGINE_UPLOAD_HOSTS}
    print("  proxy saw engine upload traffic: " +
          ", ".join(f"{h}={n}" for h, n in new.items()))
    if sum(new.values()):
        return []
    return ["the engine's results never went through the proxy (no new "
            f"{' / '.join(ENGINE_UPLOAD_HOSTS)} flows) -- engines egress around it"]


def sut_hosts_via_proxy():
    """Non-BlazeMeter hosts the proxy was asked to reach: the engine's traffic
    to the system under test. Reported, not asserted -- a SUT is often internal
    and legitimately in NO_PROXY, and JMeter ignores HTTP(S)_PROXY anyway."""
    hosts = set()
    for line in _proxy_log():
        m = re.search(r"server connect ([^:\s]+):\d+", line)
        if m and not m.group(1).endswith("blazemeter.com"):
            hosts.add(m.group(1))
    return sorted(hosts)


def assert_engine_did_work(client, master_id):
    """Did the engine generate load? A dummy-sampler script reaches ENDED
    without a request leaving the pod."""
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
        return ["the run produced no samples -- the engine never issued a "
                "request, so nothing about its egress was exercised"]
    if errors and errors >= hits:
        return [f"every one of the {hits} samples failed -- the engine could not "
                f"reach the target from inside the cluster"]
    return []


_TAURUS_EXIT = re.compile(r"Taurus completed \(Exit: (\d+)\)")


def assert_engine_exited_cleanly(client, master_id):
    """Did the engine finish, or die partway and get reported as ENDED?

    The Taurus exit code in the run's events is the only signal. Measured on
    memory-starved engines: every one reached ENDED with zero failures, and the
    starved runs showed *better* latency than a healthy one, because an engine
    that dies early only samples the gentle part of the ramp:

        limit    samples   avg     exit
        1536MB     1,139   338ms   1
        2560MB    31,130   322ms   1
        3072MB    61,348   322ms   0
    """
    try:
        events = (client.master_status(master_id) or {}).get("events") or []
    except Exception as e:
        return [f"could not read the run's events for master {master_id}: {e}"]
    codes = [m.group(1) for m in
             (_TAURUS_EXIT.search(e.get("message") or "") for e in events) if m]
    if not codes:
        # Absent is not zero: aged-out events or an unknown shape are unverified.
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
    """Start a real test on the location so crane spawns an engine, then check
    what the engine was given. The test's locations are repointed at the
    private location and restored afterwards."""
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
        # kget reports an unreadable node as {}; the pool check needs None.
        node_name = pod["spec"].get("nodeName")
        node = kube.kget(cli, None, "node", node_name) if node_name else None
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
        # After the summary, so the exit code can contradict a starved engine's
        # healthy-looking summary.
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
    """What crane passed to the engine it spawned: the engine image override,
    the CA bundle (KUBERNETES_CA_BUNDLE_MOUNT) and the proxy env -- all
    invisible to a crane-only run."""
    fails = []
    containers = pod["spec"].get("containers", [])
    env = {e["name"]: e.get("value") for c in containers for e in c.get("env", [])}
    images = [c.get("image", "") for c in containers]

    reg = opts.get("private_registry")
    if reg and not all(i.startswith(reg.split("/")[0]) for i in images):
        fails.append(f"engine image is not from the private registry: {images} "
                     f"-- IMAGE_OVERRIDES does not cover the engine")
    if any(opts.get(k) for k in generate.CA_MODES):
        # Crane mounts /var/cm as a directory; the engine gets the bundle file
        # itself (/var/cm/ca-bundle.crt, subPath). Accept both.
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
    """The engine's limits are the ones the bundle configured. Crane reads them
    from KUBERNETES_RESOURCES_LIMITS_*, so a mismatch means the ConfigMap never
    reached the engine. Requests are crane's own (see engine_request_gap)."""
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
    """The JVM heap the engine was started with, or None where it was not found.

    Searched across env, command and args: the heap is a location setting
    BlazeMeter pushes, so where it lands is not ours to choose."""
    for c in pod["spec"].get("containers", []):
        haystack = [e.get("value") or "" for e in c.get("env", [])]
        haystack += list(c.get("command") or []) + list(c.get("args") or [])
        for value in haystack:
            m = _XMX.search(value)
            if m:
                return int(m.group(1)) * _XMX_UNIT[m.group(2).lower()]
    return None


def engine_heap_note(pod):
    """The heap against the container limit, as a line to print. Reported, not
    asserted: the pairing is the location's to fix. A heap over the limit is an
    OOMKill; one far under it is node capacity reserved and unused."""
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
        # Inclusive, matching doctor.check_engine_heap: the default pairing
        # (4096MB in 8Gi) is exactly half.
        verdict = " -- at or under half the limit, so the rest is reserved and unused"
    return (f"engine JVM heap is {format_memory(heap)} against a "
            f"{format_memory(limit)} limit ({pct}%){verdict}")


def assert_engine_pool(pod, node, opts):
    """The engine landed on the pool it was aimed at (split pools only). `node`
    is None when it could not be read, which is reported and not a failure."""
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
    """The gap between an engine's limits and its requests, or None.

    Reported, not asserted: requests come from the location's
    overrideCPU/overrideMemory (250m/256Mi when unset), which no manifest sets,
    and crane sets them explicitly so a LimitRange's defaultRequest cannot fill
    them either. The scheduler packs nodes on requests."""
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


# -- deploy, verify, tear down ------------------------------------------------------

def assert_live_config(cli, namespace, facts, opts):
    """Read the deployed objects back and check what the generator claims about
    them: an agent that comes online is not necessarily configured correctly."""
    fails = []
    cm = kube.kget(cli, namespace, "configmap", "blazemeter-configmap").get("data", {})
    if not cm:
        return ["blazemeter-configmap not found in the cluster"]

    if opts.get("use_secret", True):
        if "AUTH_TOKEN" in cm:
            fails.append("AUTH_TOKEN is in the ConfigMap despite use_secret")
        leaked = [k for k in ("HTTP_PROXY", "HTTPS_PROXY")
                  if "@" in cm.get(k, "")]
        if leaked:
            fails.append(f"proxy credentials readable in the ConfigMap: {leaked}")

    # Judged against the resolved option, which defaults to false under a
    # private registry (auto-update would pull from the blackholed public one).
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
        for img in kube.pod_images(cli, namespace):
            if not img.startswith(reg.split("/")[0]):
                fails.append(f"running image is not from the private registry: {img}")

    ca_path = cm.get("REQUESTS_CA_BUNDLE")
    if ca_path:
        n = kube.crane_exec(cli, namespace,
                            f'grep -c "BEGIN CERTIFICATE" {ca_path} 2>/dev/null || echo 0')
        if n.strip() in ("", "0"):
            fails.append(f"{ca_path} is missing or holds no certificates in the "
                         f"crane pod -- the CA ConfigMap never reached the process")
        else:
            print(f"  CA bundle in pod: {n.strip()} certificates at {ca_path}")
    return fails


def deploy(manifest_dir, namespace, cluster="current", insecure_registry=None):
    # announce=False and the answer dropped: run() already asked and kept it,
    # and this second call would report "reusing" a cluster run() just built.
    ensure_cluster(cluster, insecure_registry, announce=False)
    cli = kube.cli_tool()
    kube.run([cli, "get", "ns", namespace], check=False)
    kube.run([cli, "create", "ns", namespace], check=False)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        kube.apply(cli, namespace, f)
    kube.run([cli, "-n", namespace, "rollout", "status", "deploy/crane", "--timeout=300s"])
    return cli


def wait_online(client, harbor_id, ship_id, timeout=600, poll=15):
    """Poll BlazeMeter until the ship is idle/running with a heartbeat newer
    than the start of the wait."""
    start = time.time()

    def online():
        harbor = client.private_location(harbor_id)
        ship = next((s for s in harbor.get("ships", []) if s["id"] == ship_id), None)
        if not ship:
            return False
        hb, state = ship.get("lastHeartBeat") or 0, ship.get("state")
        print(f"  ship={ship_id} state={state} heartbeat_age={time.time()-hb:.0f}s")
        return hb >= start - 60 and state in ("idle", "running")

    return bool(kube.poll_until(online, timeout, poll))


def teardown(manifest_dir, namespace, cluster="current", owned=None):
    """Delete what this run created (`owned`), and nothing else.

    A cluster the run did not create survives, and so must be emptied of what
    the run put there: the namespace if the run created it, else the applied
    manifests, the egress policy (a dotfile no glob reaches) and the rig's CA
    ConfigMap. A leftover default-deny policy would make the next run wait out
    its timeout."""
    owned = owned or Owned()
    if owned.cluster and cluster == "kind":
        kube.run(["kind", "delete", "cluster", "--name", KIND_CLUSTER], check=False)
        return
    if owned.cluster and cluster == "minikube":
        kube.run(["minikube", "delete", "-p", MINIKUBE_PROFILE], check=False)
        return
    if cluster in ("kind", "minikube"):
        print(f"leaving the {cluster} cluster up: this run did not create it.")
    cli = kube.cli_tool()
    if cluster == "minikube":
        unblackhole(owned.blackholed)
    if owned.namespace:
        print(f"deleting the namespace '{namespace}', which this run created")
        kube.run([cli, "delete", "ns", namespace, "--ignore-not-found"], check=False)
        return
    # By recorded name: `--ca-mode file` creates the generator's ConfigMap, and
    # ensure_ca_configmap would refuse a leftover on the next run.
    if owned.ca_configmap:
        kube.run([cli, "-n", namespace, "delete", "cm", owned.ca_configmap,
                  "--ignore-not-found"], check=False)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        kube.run([cli, "-n", namespace, "delete", "-f", f, "--ignore-not-found"],
                 check=False)
    kube.run([cli, "-n", namespace, "delete", "networkpolicy", EGRESS_POLICY_NAME,
              "--ignore-not-found"], check=False)


# -- the compose rig -----------------------------------------------------------
#
# Up, online, down, and nothing more: the registry, proxy, containment and
# negative-control rigs are cluster-shaped. What this leaves unproven (notably
# `-u 0`, which matters only once crane starts an engine) is in docs/live-test.md.

COMPOSE_TOOL = ["docker", "compose"]
# Log lines printed on failure: `up -d` returns as soon as the container exists,
# so a crash-looping crane and a slow one look identical from here.
COMPOSE_LOG_LINES = 40


def compose_tool():
    """`docker compose` version, checked up front. `docker-compose` (v1) is a
    different command with different file precedence."""
    try:
        out = kube.quiet(COMPOSE_TOOL + ["version"])
    except FileNotFoundError as e:
        raise RuntimeError(_NO_COMPOSE) from e
    if out.returncode:
        raise RuntimeError(_NO_COMPOSE)
    return out.stdout.strip()


_NO_COMPOSE = ("`docker compose version` does not work here, so this bundle "
               "cannot be started: a docker daemon with the Compose v2 plugin is "
               "the whole of what the compose path needs")


def _compose(manifest_dir, *args, check=True):
    # -f, not a cwd change: compose resolves env_file and relative binds against
    # the first -f file's directory, which is the bundle.
    return kube.run(COMPOSE_TOOL + ["-f", bundle_check.compose_path(manifest_dir),
                                    *args], check=check)


def compose_up(manifest_dir):
    print(f"docker compose: {compose_tool()}")
    _compose(manifest_dir, "up", "-d")


def compose_logs(manifest_dir, lines=COMPOSE_LOG_LINES):
    _compose(manifest_dir, "logs", "--tail", str(lines), check=False)


def compose_down(manifest_dir, container_name=None):
    """`down`, then remove the named container if it survived: `down` is a
    no-op for a container whose compose file was rewritten since, and the
    leftover holds the name the next run needs."""
    _compose(manifest_dir, "down", "--remove-orphans", check=False)
    if not container_name:
        return
    left = kube.quiet(["docker", "ps", "-aq", "--filter", f"name=^{container_name}$"])
    if left.stdout.strip():
        print(f"note: {container_name} survived `compose down` -- removing it "
              f"by name so it does not hold the name for the next run")
        kube.run(["docker", "rm", "-f", container_name], check=False, capture=True)


def run_compose(client, manifest_dir, harbor_id, ship_id, timeout=600,
                keep=False, opts=None):
    """Start the docker bundle on this host's daemon, wait for the agent to
    report online in BlazeMeter, and stop it again."""
    # Before the try: its finally would `compose down` a project never started.
    bad = bundle_check.bundle_check(manifest_dir, harbor_id, ship_id, opts).report()
    if bad:
        raise bundle_check.BundleMismatch(bad)
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


# -- the cluster rig -------------------------------------------------------------

def run(client, manifest_dir, namespace, harbor_id, ship_id,
        cluster="current", timeout=600, keep=False,
        facts=None, local_registry=None,
        local_proxy=None, proxy_user=None, proxy_pass=None, regenerate=None,
        negative_control_check=True, opts=None, contain_egress=False,
        run_test=None, engine_cpu="1", engine_mem="4Gi", ca_mode=None):
    """Deploy the manifests in `manifest_dir` and verify the agent comes online.

    regenerate(overlay) -- re-renders the manifests with extra generate()
    options merged in. Required with --local-proxy (the CA exists only once the
    proxy is up) and --run-test (engines are sized down to fit).

    ca_mode -- the CA configuration under test with --local-proxy: "inline",
    "existing" or "file". None resolves to the bundle's own mode.

    opts -- the options the manifests were built from; enables the read-back
    checks in assert_live_config().

    Raises BundleMismatch, before anything is created, for a docker bundle or
    one built for another agent.
    """
    # Checked here as well as in the CLI because the MCP server calls run()
    # directly, and outside the try so its finally tears down nothing.
    if bundle_check.bundle_platform(manifest_dir, opts) == bundle_check.PLATFORM_COMPOSE:
        raise bundle_check.BundleMismatch(
            f"{manifest_dir}/ is a docker bundle -- one container on a host, "
            f"not a cluster deployment -- and this is the rig that applies "
            f"manifests with kubectl. Start it with `bzm-opl-gen livetest "
            f"--manifests {manifest_dir}` (no --namespace, no --cluster), "
            f"which brings it up with docker compose, waits for the agent, and "
            f"takes it down again")
    bad = bundle_check.bundle_check(manifest_dir, harbor_id, ship_id, opts).report()
    if bad:
        raise bundle_check.BundleMismatch(bad)
    ca_mode = bundle_check.resolved_ca_mode(opts, ca_mode)
    ok = False
    owned = Owned()        # filled in as the run creates things; read by teardown
    try:
        insecure = None
        if local_registry:
            ensure_registry(local_registry)
            refs = mirror_images(facts, local_registry)
            print(f"mirrored {len(refs)} images into localhost:{local_registry}")
            insecure = f"{REGISTRY_CLUSTER_HOST}:{local_registry}"
        owned = owned._replace(cluster=ensure_cluster(
            cluster, insecure, cni="calico" if contain_egress else None))
        owned = owned._replace(namespace=kube.ensure_namespace(kube.cli_tool(), namespace))
        if local_registry:
            owned = owned._replace(blackholed=blackhole_public_registries(
                facts, cluster, (opts or {}).get("private_registry")))
        # Engines sized down to fit a laptop cluster; 2 CPU / 8Gi sits Pending.
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
                                       proxy_user, proxy_pass, ca_mode),
                       **engine_overlay}
            if contain_egress:
                apply_egress_policy(kube.cli_tool(), namespace, host, manifest_dir)
            if negative_control_check and not negative_control(
                    regenerate, overlay, manifest_dir, namespace, cluster):
                return False
            # After the negative control, which deletes CA_CONFIGMAP by name --
            # the very object `file` mode has the rig create.
            if ca_mode in ("existing", "file"):
                cm_name = (generate.CA_CONFIGMAP if ca_mode == "file"
                           else bundle_check.CA_RIG_CONFIGMAP)
                ensure_ca_configmap(kube.cli_tool(), namespace, ca_pem, name=cm_name)
                owned = owned._replace(ca_configmap=cm_name)
            regenerate(overlay)
            opts = dict(opts or {}, **overlay)
            # Only count what the proxy logs from here, not the control's run.
            mark, marks = len(proxy_flows()), proxy_log_marks()
        deploy(manifest_dir, namespace, cluster, insecure_registry=insecure)
        print(f"waiting up to {timeout}s for agent to report online in BlazeMeter...")
        ok = wait_online(client, harbor_id, ship_id, timeout)
        why = "agent online in BlazeMeter" if ok else "agent never reported online"
        if local_proxy:
            flows = proxy_flows()[mark:]
            print(f"proxy saw {len(flows)} blazemeter.com log lines")
            for line in flows[-5:]:
                print("  " + line)
            if ok and not flows:
                # Online with nothing in the proxy log: the agent went around it.
                ok, why = False, ("agent online but no blazemeter.com traffic "
                                  "through the proxy -- proxy settings bypassed")
            elif ok:
                why = ("agent online via the MITM proxy -- proxy env and CA "
                       "trust both in force")
        if ok:
            cli = kube.cli_tool()
            fails = assert_live_config(cli, namespace, facts, opts or {})
            if local_proxy:
                fails += proxy_log_failures(marks)
            if contain_egress:
                fails += assert_egress_contained(cli, namespace)
            if run_test:
                fails += run_engine_test(client, cli, namespace,
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
                kube.run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
            if local_proxy:
                kube.run(["docker", "rm", "-f", PROXY_NAME], check=False, capture=True)
    return ok
