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
               On Apple Silicon the BlazeMeter images are amd64-only: enable
               Docker Desktop's Rosetta emulation (works, but engines are slow)
"""

import glob
import json
import os
import platform
import subprocess
import time

KIND_CLUSTER = "bzm-opl-test"
MINIKUBE_PROFILE = "bzm-opl-test"
REGISTRY_NAME = "bzm-opl-registry"
# What the minikube node calls the host's registry. generate() manifests must
# use this as --private-registry when livetesting with --local-registry.
REGISTRY_CLUSTER_HOST = "host.minikube.internal"

PROXY_NAME = "bzm-opl-proxy"
# mitmproxy >= 12 dies with SIGILL on Apple-silicon VMs (colima/VZ, Docker
# Desktop); 11.1.3 is the newest tag that runs there. Pin it.
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


def _cli_for(manifest_dir):
    """oc if available (OpenShift-friendly), else kubectl."""
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


def ensure_minikube(insecure_registry=None):
    if platform.machine() in ("arm64", "aarch64"):
        print("note: BlazeMeter images are amd64-only -- Docker Desktop's Rosetta "
              "emulation must be enabled for pods to run on this machine")
    st = subprocess.run(["minikube", "status", "-p", MINIKUBE_PROFILE,
                         "--format", "{{.Host}}"], capture_output=True, text=True)
    if st.stdout.strip() != "Running":
        cmd = ["minikube", "start", "-p", MINIKUBE_PROFILE, "--driver=docker",
               "--cpus=4", "--memory=6g", "--wait=all"]
        if insecure_registry:
            cmd.append(f"--insecure-registry={insecure_registry}")
        _run(cmd)
    elif insecure_registry:
        print(f"note: reusing running minikube profile -- it must already trust "
              f"insecure registry {insecure_registry} (flag only applies at creation)")
    _run(["kubectl", "config", "use-context", MINIKUBE_PROFILE])


def ensure_registry(port):
    out = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                          REGISTRY_NAME], capture_output=True, text=True)
    if out.stdout.strip() != "true":
        _run(["docker", "rm", "-f", REGISTRY_NAME], check=False, capture=True)
        _run(["docker", "run", "-d", "--name", REGISTRY_NAME,
              "-p", f"{port}:5000", "registry:2"])


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


def ensure_cluster(cluster, insecure_registry=None):
    """Idempotent -- safe to call before deploy() when something (the proxy
    overlay) needs the node up early."""
    if cluster == "kind":
        ensure_kind()
    elif cluster == "minikube":
        ensure_minikube(insecure_registry)


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


def _kget(cli, namespace, kind, name):
    out = subprocess.run([cli, "-n", namespace, "get", kind, name, "-o", "json"],
                         capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 else {}


def assert_live_config(cli, namespace, facts, opts):
    """Read the deployed objects back and check what the generator claims about
    them. A manifest that renders, and even one whose agent comes online, is not
    the same as one that is configured correctly."""
    from .facts import select_images
    fails = []
    cm = _kget(cli, namespace, "configmap", "blazemeter-configmap").get("data", {})
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
    cli = _cli_for(manifest_dir)
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
    cli = _cli_for(manifest_dir)
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
    cli = _cli_for(manifest_dir)
    for f in sorted(glob.glob(os.path.join(manifest_dir, "*.yaml"))):
        _run([cli, "-n", namespace, "delete", "-f", f, "--ignore-not-found"], check=False)


def run(client, manifest_dir, namespace, harbor_id, ship_id,
        cluster="current", timeout=600, keep=False,
        facts=None, local_registry=None,
        local_proxy=None, proxy_user=None, proxy_pass=None, regenerate=None,
        negative_control_check=True, opts=None):
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
        ensure_cluster(cluster, insecure)
        if local_registry:
            blackhole_public_registries(facts, cluster,
                                        (opts or {}).get("private_registry"))
        if local_proxy:
            if not regenerate:
                raise RuntimeError("--local-proxy needs a regenerate callback")
            host, ca_pem = ensure_proxy(cluster, proxy_user, proxy_pass)
            print(f"proxy up at {host}:{PROXY_PORT} "
                  f"({'authenticated' if proxy_user else 'open'}), "
                  f"MITM CA bundle {len(ca_pem)} bytes")
            if not verify_proxy_reachable(host, cluster, proxy_user, proxy_pass):
                return False
            overlay = proxy_overlay(host, PROXY_PORT, ca_pem, proxy_user, proxy_pass)
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
            fails = assert_live_config(_cli_for(manifest_dir), namespace,
                                       facts, opts or {})
            if local_proxy:
                fails += proxy_log_failures(marks)
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
