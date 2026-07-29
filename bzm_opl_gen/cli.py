"""bzm-opl-gen: generate + live-test BlazeMeter OPL k8s/OpenShift deployments
from a customer's actual BlazeMeter account.

Subcommands:
  locations   list private locations (harbors) across the account
  create-ship create an agent (ship) in a location, print id + AUTH_TOKEN
  facts       query the account, write facts.json (harbor, ships, images, features)
  generate    render manifests from facts + customer parameters
  doctor      preflight a cluster: can it schedule the location's concurrency?
  suggest     what a cluster's evidence implies about the generate options
  sv-expose   emit a working Service+Ingress per deployed virtual service
  images      list / pull / mirror the images the location actually needs
  livetest    apply manifests to a cluster and verify the agent comes online
"""

import argparse
import json
import subprocess
import sys

from . import (api, doctor, facts as facts_mod, generate as gen_mod, livetest,
               suggest as suggest_mod, workstation)


def _resolve_account(client, a):
    """--account-id wins; --account-name matches case-insensitive substring."""
    if a.account_id:
        return a.account_id
    accounts = client.accounts()
    if a.account_name:
        hits = [x for x in accounts if a.account_name.lower() in (x.get("name") or "").lower()]
        if len(hits) != 1:
            sys.exit(f"--account-name '{a.account_name}' matched {len(hits)} accounts: "
                     f"{[(x['id'], x.get('name')) for x in hits or accounts]}")
        return hits[0]["id"]
    u = client.user()
    return u["defaultProject"]["accountId"]


def cmd_locations(a):
    client = api.BzmClient(a.api_key)
    account_id = _resolve_account(client, a)
    locs = client.private_locations(account_id)
    print(f"account {account_id}: {len(locs)} private locations")
    for l in locs:
        ships = ", ".join(f"{s['id']} ({s.get('name')}, {s.get('state')})"
                          for s in l.get("ships", [])) or "none"
        print(f"  {l['id']}  {l.get('name')!r}  slots={l.get('slots')}  "
              f"funcIds={l.get('funcIds')}\n      ships: {ships}")


def cmd_create_location(a):
    client = api.BzmClient(a.api_key)
    account_id = _resolve_account(client, a)
    if a.workspace_id:
        wsid = a.workspace_id
    else:
        if not a.workspace_name:
            sys.exit("--workspace-id or --workspace-name required")
        wss = client.workspaces(account_id)
        hits = [w for w in wss if a.workspace_name.lower() in (w.get("name") or "").lower()]
        if len(hits) != 1:
            sys.exit(f"--workspace-name '{a.workspace_name}' matched {len(hits)}: "
                     f"{[(w['id'], w.get('name')) for w in hits]}")
        wsid = hits[0]["id"]
    h = client.create_private_location(a.name, account_id, [wsid],
                                       func_ids=a.func_ids, slots=a.slots,
                                       threads_per_engine=a.threads_per_engine)
    print(f"created location '{h.get('name')}' harbor_id={h['id']} "
          f"(account {account_id}, workspace {wsid}, funcIds={a.func_ids}, "
          f"slots={h.get('slots')}, threadsPerEngine={h.get('threadsPerEngine')})")
    if not h.get("slots") or not h.get("threadsPerEngine"):
        print("WARNING: location is not runnable -- tests will fail to start with "
              "403 'Not enough available resources'. Set the missing field(s) in "
              "the BlazeMeter UI (Settings -> Private Locations).", file=sys.stderr)
    print(f"next: bzm-opl-gen create-ship --api-key {a.api_key} --harbor-id {h['id']} --name <agent-name>")


def cmd_delete_location(a):
    client = api.BzmClient(a.api_key)
    h = client.private_location(a.harbor_id)
    client.delete_private_location(a.harbor_id)
    print(f"deleted location '{h.get('name')}' ({a.harbor_id}) and its "
          f"{len(h.get('ships', []))} ship(s)")


def cmd_create_ship(a):
    client = api.BzmClient(a.api_key)
    ship = client.create_ship(a.harbor_id, a.name)
    token = client.auth_token(a.harbor_id, ship["id"])
    print(f"harbor_id:  {a.harbor_id}")
    print(f"ship_id:    {ship['id']}  (name: {ship.get('name')})")
    print(f"auth_token: {token}")
    print(f"\nnext: bzm-opl-gen facts --api-key {a.api_key} --harbor-id {a.harbor_id}")
    print(f"      bzm-opl-gen generate --ship-id {ship['id']} --api-key {a.api_key} ...")


def cmd_facts(a):
    """Gather facts from the account, or -- with --manual -- build them from the
    three values BlazeMeter shows on the agent, for a customer whose account
    nobody here can reach."""
    if a.manual:
        if not a.ship_id:
            sys.exit("--manual needs --ship-id: it is what identifies this agent "
                     "to BlazeMeter, and the API is not there to look it up")
        f = facts_mod.manual(a.harbor_id, a.ship_id, func_ids=a.func_ids)
    else:
        if not a.api_key:
            sys.exit("facts needs --api-key, or --manual --ship-id to build them "
                     "from values you already have")
        f = facts_mod.gather(api.BzmClient(a.api_key), a.harbor_id)
    facts_mod.save(f, a.output)
    # Manual facts carry no location name -- nothing knows it -- so fall back to
    # the id rather than printing "location 'None'".
    print(f"wrote {a.output}: location '{f['harbor_name'] or f['harbor_id']}' "
          f"funcIds={f['func_ids']} ships={len(f['ships'])} "
          f"images={len(f['images'])} ({f['images_source']})")
    if facts_mod.gui_images_incomplete(f):
        print("note: functionalGui needs a version-pinned browser image "
              "(charmander/chrome_*, firefox_*, ...) that no catalogue can pick "
              "for you. Fine against the public registry; for a private one, add "
              "the key to IMAGE_OVERRIDES by hand or gather facts with an API key.",
              file=sys.stderr)


def cmd_generate(a):
    f = facts_mod.load(a.facts)
    opts = {}
    if a.profile:
        with open(a.profile) as fh:
            opts.update(json.load(fh))
    for key in ("platform", "namespace", "ship_id", "auth_token", "output_format",
                "private_registry", "pull_secret", "service_type",
                "service_account_name",
                "sv_ingress", "sv_subdomain", "sv_tls_secret", "sv_istio_gateway"):
        v = getattr(a, key, None)
        if v is not None:
            opts[key] = v
    if a.no_secret:
        opts["use_secret"] = False
    if a.no_create_service_account:
        opts["service_account_create"] = False
    if a.cluster_rbac:
        opts["cluster_rbac"] = True
    if a.no_restrict_engines:
        opts["restrict_engines"] = False
    if a.tolerations:
        opts["tolerations"] = json.loads(a.tolerations)
    if a.node_selector:
        opts["node_selector"] = json.loads(a.node_selector)
    if a.ca_bundle:
        with open(a.ca_bundle) as fh:
            opts["ca_bundle"] = fh.read()
    if a.ca_configmap:
        name, _, key = a.ca_configmap.partition(":")
        opts["ca_existing_configmap"] = name
        if key:
            opts["ca_configmap_key"] = key
    if a.ca_openshift_inject:
        opts["ca_openshift_inject"] = True
    proxy = dict(opts.get("proxy") or {})
    for flag, key in (("proxy_http", "http"), ("proxy_https", "https"),
                      ("no_proxy", "no_proxy"), ("proxy_user", "username"),
                      ("proxy_pass", "password")):
        v = getattr(a, flag, None)
        if v is not None:
            proxy[key] = v
    if proxy:
        opts["proxy"] = proxy
    for key in ("engine_cpu_limit", "engine_mem_limit", "crane_ephemeral_storage"):
        v = getattr(a, key, None)
        if v is not None:
            opts[key] = v
    if a.api_key and not opts.get("auth_token"):
        ship_id = opts.get("ship_id") or (f["ships"][0]["id"] if len(f["ships"]) == 1 else None)
        if ship_id:
            client = api.BzmClient(a.api_key)
            opts["auth_token"] = client.auth_token(f["harbor_id"], ship_id)
            print(f"fetched AUTH_TOKEN for ship {ship_id} from BlazeMeter API")
    files = gen_mod.generate(f, opts)
    written = gen_mod.write(files, a.output)
    print(f"wrote {len(written)} files to {a.output}/: " + ", ".join(written))


def cmd_sv_expose(a):
    """Emit a working Service+Ingress per deployed virtual service.

    Runs after the virtual services are deployed, not at generate time: the
    mocks are read off the running pods, because the v4 API exposes no
    virtual-service endpoint and the pod carries the identity crane actually
    used."""
    opts = gen_mod.load_profile(a.manifests) if a.manifests else {}
    for key in ("sv_subdomain", "sv_tls_secret", "namespace"):
        v = getattr(a, key, None)
        if v is not None:
            opts[key] = v
    opts["namespace"] = opts.get("namespace") or a.namespace
    if a.ingress_class:
        opts["sv_ingress_class"] = a.ingress_class
    mocks = livetest.sv_mocks(livetest.cli_tool(), opts["namespace"])
    if not mocks:
        sys.exit(f"no virtual-service pods in namespace {opts['namespace']} -- "
                 f"deploy the virtual service in BlazeMeter first, then re-run")
    out = gen_mod.sv_expose(mocks, opts["namespace"],
                            gen_mod.sv_publish_cfg(opts))
    with open(a.output, "w") as fh:
        fh.write(out)
    names = ", ".join(f"{m['name']}:{m['port']}" for m in mocks)
    print(f"wrote {a.output}: {len(mocks)} virtual service(s) -- {names}")
    print(f"apply with: kubectl apply -n {opts['namespace']} -f {a.output}")


def cmd_doctor(a):
    """Preflight the cluster against the location's advertised concurrency."""
    if a.harbor_id:
        if not a.api_key:
            sys.exit("--harbor-id needs --api-key (or drop both and use --facts)")
        f = facts_mod.gather(api.BzmClient(a.api_key), a.harbor_id)
    else:
        f = facts_mod.load(a.facts)
    # The generated profile is what the checks measure against -- engine size,
    # nodeSelector, registry, proxy/CA. Without it we can only assume defaults.
    try:
        opts = gen_mod.load_profile(a.manifests)
    except FileNotFoundError:
        opts = {}
        print(f"note: no {a.manifests}/profile.json -- checking against the "
              f"documented engine size and no scheduling constraints")
    # The three things an evidence file contributes travel together as one
    # Evidence, from here to the `evidence=` doctor takes, so an absent file is
    # an empty Evidence rather than a None the call site then has to test three
    # times over. Empty says exactly what doctor's own defaults say: no cluster
    # data, no probes, and no verdicts reached before the checks ran.
    imported = doctor.Evidence(None, None, ())
    if a.cluster_evidence:
        try:
            doc = doctor.load_evidence(a.cluster_evidence)
            # The namespace the evidence was collected for is the last fallback,
            # not the first: an explicit -n or the bundle's own namespace is what
            # the user is preflighting, and evidence for a different one is
            # reported by cluster_from_evidence rather than silently adopted.
            namespace = (a.namespace or opts.get("namespace")
                         or doc.get("namespace"))
            imported = doctor.cluster_from_evidence(doc, namespace)
        except ValueError as e:
            sys.exit(str(e))
    else:
        namespace = a.namespace
    checks = doctor.run(f, opts, namespace, evidence=imported)
    sys.exit(1 if doctor.has_failures(checks) else 0)


def cmd_suggest(a):
    """Say what a cluster's evidence implies about the generate options.

    Deliberately its own command rather than a flag on `doctor`: that one
    answers whether a deployment survives this cluster and exits non-zero when
    it would not, and this one answers how it should have been configured. Same
    file, different question, and nothing here is applied to anything.
    """
    try:
        doc = doctor.load_evidence(a.cluster_evidence)
        suggestions = suggest_mod.from_evidence(doc)
    except ValueError as e:
        sys.exit(str(e))
    if a.json:
        print(json.dumps([suggest_mod.as_dict(s) for s in suggestions], indent=2))
    else:
        suggest_mod.report(doc, suggestions)


def cmd_toolcheck(a):
    """Preflight the workstation against the rig flags you intend to pass."""
    checks = workstation.run({"cluster": a.cluster,
                              "local_registry": a.local_registry,
                              "local_proxy": a.local_proxy})
    sys.exit(1 if doctor.has_failures(checks) else 0)


def cmd_images(a):
    f = facts_mod.load(a.facts) if a.facts else None
    if f is None:
        client = api.BzmClient(a.api_key)
        f = facts_mod.gather(client, a.harbor_id)
    imgs = [f["crane_image"]] + [
        f"{i['repo']}:{i['tag']}"
        for i in facts_mod.select_images(f, all_images=a.all)
    ]
    for ref in imgs:
        print(ref)
    if not a.pull:
        return
    for ref in imgs:
        _docker(["pull", "--platform", a.platform, ref], a.dry_run)
        if a.mirror:
            name = ref.rsplit("/", 1)[-1]
            target = f"{a.mirror.rstrip('/')}/{name}"
            _docker(["tag", ref, target], a.dry_run)
            _docker(["push", target], a.dry_run)


def _docker(args, dry):
    cmd = ["docker"] + args
    print(("DRY-RUN: " if dry else "+ ") + " ".join(cmd))
    if not dry:
        subprocess.run(cmd, check=True)


def _regenerator(client, facts, a, ship_id):
    """Re-render the manifests in place with extra generate() options merged
    onto the ones they were built from (out/profile.json). Used by
    --local-proxy, whose CA and address only exist once the rig is up."""
    def regenerate(overlay):
        opts = gen_mod.load_profile(a.manifests)
        opts.update(overlay)
        opts["namespace"] = a.namespace
        opts["ship_id"] = opts.get("ship_id") or ship_id
        opts["auth_token"] = client.auth_token(facts["harbor_id"], opts["ship_id"])
        written = gen_mod.write(gen_mod.generate(facts, opts), a.manifests)
        print(f"regenerated {len(written)} files in {a.manifests}/ with "
              f"proxy + CA trust: " + ", ".join(written))
    return regenerate


def cmd_livetest(a):
    f = facts_mod.load(a.facts)
    client = api.BzmClient(a.api_key)
    ship_id = a.ship_id or (f["ships"][0]["id"] if len(f["ships"]) == 1 else None)
    if not ship_id:
        sys.exit(f"--ship-id required (location has {len(f['ships'])} ships)")
    # The options the manifests were rendered from -- lets livetest check the
    # deployed objects against what was asked for. Absent on hand-made dirs.
    try:
        opts = gen_mod.load_profile(a.manifests)
    except FileNotFoundError:
        opts = None
        print(f"note: no {a.manifests}/profile.json -- skipping the read-back "
              f"configuration checks (regenerate to enable them)")
    # The rig applies YAML with kubectl and reads it back object by object, so a
    # chart bundle has nothing at the top level for it to apply. Say so here
    # rather than letting the glob come back empty and the agent never appear.
    if opts and opts.get("output_format") == "helm":
        sys.exit(
            f"{a.manifests}/ holds a Helm chart, and livetest deploys manifests "
            f"with kubectl. Re-generate that directory with --format manifests "
            f"(the two render the same objects), or install the chart yourself "
            f"and watch it with: bzm-opl-gen doctor / kubectl -n {a.namespace} "
            f"logs -l role=role-crane -f")
    # Same shape of guard, for the same reason. The rig deploys into a namespace
    # it creates itself, so a ServiceAccount the bundle does not create is never
    # there: every object applies, no pod is ever created, and the run burns its
    # whole timeout waiting for a heartbeat that cannot come.
    if opts and not opts.get("service_account_create", True):
        sa = opts.get("service_account_name")
        sys.exit(
            f"{a.manifests}/ references ServiceAccount '{sa}' without creating "
            f"it, and livetest deploys into a namespace it creates itself, "
            f"where that account will not exist. Re-generate without "
            f"--no-create-service-account, or create '{sa}' in {a.namespace} "
            f"yourself before starting the run")
    proxy_user = proxy_pass = None
    # Both --local-proxy and --run-test re-render the manifests (the proxy's CA,
    # the engine sizing); the callback needs a profile to merge onto, so it is
    # only available when one was found.
    regenerate = (_regenerator(client, f, a, ship_id)
                  if opts is not None and (a.local_proxy or a.run_test) else None)
    if a.contain_egress and not (a.local_proxy and a.cluster == "minikube"):
        sys.exit("--contain-egress needs --local-proxy and --cluster minikube: "
                 "the policy denies everything except DNS, the apiserver, and "
                 "that proxy, so without it the agent has no way out at all")
    if a.local_proxy:
        if a.cluster not in ("minikube", "kind"):
            sys.exit("--local-proxy needs --cluster minikube|kind (the proxy "
                     "joins that cluster's docker network)")
        if a.proxy_auth and a.proxy_auth.lower() != "none":
            proxy_user, _, proxy_pass = a.proxy_auth.partition(":")
    ok = livetest.run(client, a.manifests, a.namespace, f["harbor_id"], ship_id,
                      cluster=a.cluster, timeout=a.timeout, keep=a.keep,
                      facts=f, local_registry=a.local_registry,
                      local_proxy=a.local_proxy, proxy_user=proxy_user,
                      proxy_pass=proxy_pass, regenerate=regenerate, opts=opts,
                      negative_control_check=not a.skip_negative_control,
                      contain_egress=a.contain_egress, run_test=a.run_test,
                      engine_cpu=a.engine_cpu, engine_mem=a.engine_mem)
    sys.exit(0 if ok else 1)


def cmd_ui(a):
    try:
        from . import server
    except ImportError:
        sys.exit("UI dependencies missing -- pip install 'bzm-opl-gen[ui]'")
    # 0.0.0.0 is what was asked for, not somewhere to point a browser -- print
    # an address that resolves.
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    print(f"bzm-opl-gen ui -> http://{shown}:{a.port}  (Ctrl-C to stop)",
          flush=True)
    server.main(port=a.port, open_browser=not a.no_browser, api_key_path=a.api_key,
                dev=a.dev, host=a.host)


def main():
    p = argparse.ArgumentParser(prog="bzm-opl-gen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("locations", help="list private locations in an account")
    l.add_argument("--api-key", required=True)
    l.add_argument("--account-id", type=int)
    l.add_argument("--account-name", help="case-insensitive substring, must match one")
    l.set_defaults(fn=cmd_locations)

    cl = sub.add_parser("create-location", help="create a private location (harbor)")
    cl.add_argument("--api-key", required=True)
    cl.add_argument("--account-id", type=int)
    cl.add_argument("--account-name")
    cl.add_argument("--workspace-id", type=int)
    cl.add_argument("--workspace-name", help="case-insensitive substring, must match one")
    cl.add_argument("--name", required=True)
    cl.add_argument("--func-ids", nargs="+", default=["performance"])
    cl.add_argument("--slots", type=int, default=1,
                    help="concurrent engines the location may run (default 1)")
    cl.add_argument("--threads-per-engine", type=int,
                    default=api.DEFAULT_THREADS_PER_ENGINE,
                    help=f"max threads per engine (default "
                         f"{api.DEFAULT_THREADS_PER_ENGINE}); a location with "
                         f"this unset cannot start tests")
    cl.set_defaults(fn=cmd_create_location)

    dl = sub.add_parser("delete-location", help="delete a private location and its ships")
    dl.add_argument("--api-key", required=True)
    dl.add_argument("--harbor-id", required=True)
    dl.set_defaults(fn=cmd_delete_location)

    cs = sub.add_parser("create-ship", help="create an agent (ship), print id + AUTH_TOKEN")
    cs.add_argument("--api-key", required=True)
    cs.add_argument("--harbor-id", required=True)
    cs.add_argument("--name", required=True)
    cs.set_defaults(fn=cmd_create_ship)

    f = sub.add_parser("facts", help="gather account facts -> facts.json")
    f.add_argument("--api-key")
    f.add_argument("--harbor-id", required=True)
    f.add_argument("--manual", action="store_true",
                   help="build facts from --harbor-id + --ship-id without an API "
                        "key, for a location whose account you cannot reach. "
                        "Images come from the built-in catalogue")
    f.add_argument("--ship-id", dest="ship_id", help="required with --manual")
    f.add_argument("--func-ids", dest="func_ids", nargs="+", default=["performance"],
                   help="with --manual: the location's features, which decide "
                        "which images the bundle names (default: performance)")
    f.add_argument("-o", "--output", default="facts.json")
    f.set_defaults(fn=cmd_facts)

    g = sub.add_parser("generate", help="render manifests from facts")
    g.add_argument("--facts", default="facts.json")
    g.add_argument("--api-key", help="fetch AUTH_TOKEN from the API if not given")
    g.add_argument("--profile", help="JSON options file (see profiles/)")
    g.add_argument("--format", dest="output_format",
                   choices=list(gen_mod.OUTPUT_FORMATS),
                   help="manifests (default): flat YAML to kubectl apply. "
                        "helm: a chart in helm/ with values.yaml filled in from "
                        "the account. Both render the same objects; helm covers "
                        "performance testing only")
    g.add_argument("--platform", choices=["openshift", "k8s"])
    g.add_argument("--namespace")
    g.add_argument("--ship-id", dest="ship_id")
    g.add_argument("--auth-token", dest="auth_token")
    g.add_argument("--private-registry", dest="private_registry")
    g.add_argument("--pull-secret", dest="pull_secret")
    g.add_argument("--service-type", dest="service_type", choices=["CLUSTERIP", "NODEPORT"])
    g.add_argument("--service-account", dest="service_account_name", metavar="NAME",
                   help="ServiceAccount the agent runs as (default crane). Used "
                        "whether or not the bundle creates it")
    g.add_argument("--no-create-service-account", dest="no_create_service_account",
                   action="store_true",
                   help="the ServiceAccount already exists in the namespace: "
                        "reference it from the Deployment and the RBAC subjects, "
                        "but do not emit the object")
    g.add_argument("--sv-ingress", dest="sv_ingress",
                   choices=list(gen_mod.SV_INGRESS_TYPES),
                   help="service virtualization: ingress controller to publish "
                        "virtual services through (required for a mockServices location)")
    g.add_argument("--sv-subdomain", dest="sv_subdomain", metavar="DOMAIN",
                   help="wildcard domain your ingress controller serves, e.g. apps.example.com")
    g.add_argument("--sv-tls-secret", dest="sv_tls_secret", metavar="NAME",
                   help="wildcard TLS secret in the agent namespace; required even for HTTP")
    g.add_argument("--sv-istio-gateway", dest="sv_istio_gateway", metavar="NAME",
                   help="istio only, optional: reuse this Gateway instead of one per service")
    g.add_argument("--no-secret", action="store_true", help="AUTH_TOKEN in ConfigMap")
    g.add_argument("--tolerations", help='JSON list, e.g. \'[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]\'')
    g.add_argument("--node-selector", dest="node_selector", help='JSON object, e.g. \'{"pool":"loadtest"}\'')
    g.add_argument("--ca-bundle", dest="ca_bundle", metavar="PEM_FILE",
                   help="inline CA mode: PEM file -> generator creates the ConfigMap")
    g.add_argument("--ca-configmap", dest="ca_configmap", metavar="NAME[:KEY]",
                   help="reference an existing trust-bundle ConfigMap the platform "
                        "team owns (key defaults to ca-bundle.crt)")
    g.add_argument("--ca-openshift-inject", dest="ca_openshift_inject",
                   action="store_true",
                   help="OpenShift: emit a labeled ConfigMap; the cluster injects "
                        "its trust bundle (no PEM handling)")
    g.add_argument("--proxy-http", dest="proxy_http", metavar="URL",
                   help="HTTP_PROXY, e.g. http://proxy:3128")
    g.add_argument("--proxy-https", dest="proxy_https", metavar="URL",
                   help="HTTPS_PROXY (http:// or https:// URL)")
    g.add_argument("--no-proxy", dest="no_proxy", metavar="LIST",
                   help="NO_PROXY comma list (default kubernetes.default,127.0.0.1,localhost)")
    g.add_argument("--proxy-user", dest="proxy_user",
                   help="optional proxy username -- embedded in the proxy URL")
    g.add_argument("--proxy-pass", dest="proxy_pass",
                   help="optional proxy password -- embedded in the proxy URL; "
                        "lands in the Secret unless --no-secret")
    g.add_argument("--engine-cpu-limit", dest="engine_cpu_limit", help='e.g. "2"')
    g.add_argument("--engine-mem-limit", dest="engine_mem_limit", help='e.g. "8Gi"')
    g.add_argument("--crane-ephemeral-storage", dest="crane_ephemeral_storage",
                   metavar="SIZE",
                   help='crane pod ephemeral storage, request and limit both '
                        '(default 1Gi). One value because GKE Autopilot '
                        'rewrites the limit down to the request')
    g.add_argument("--no-restrict-engines", dest="no_restrict_engines",
                   action="store_true",
                   help="let crane spawn engines with its own default security "
                        "context (privileged). Only for an image that needs a "
                        "capability -- a privileged engine is refused by "
                        "restricted PodSecurity, OpenShift SCC and GKE Autopilot, "
                        "and the run hangs at BOOT_STARTING when it is. It is "
                        "all-or-nothing: the posture goes from every container "
                        "crane creates, not from the one image that wanted "
                        "something. docs/hardened-engines.md records which "
                        "images have run under it and what they were observed "
                        "to be given")
    g.add_argument("--cluster-rbac", action="store_true", help="include optional ClusterRole")
    g.add_argument("-o", "--output", default="out")
    g.set_defaults(fn=cmd_generate)

    e = sub.add_parser("sv-expose",
                       help="emit a working Service+Ingress per deployed virtual service")
    e.add_argument("--manifests", default="out",
                   help="directory holding profile.json -- supplies the "
                        "namespace, wildcard domain and TLS secret")
    e.add_argument("-n", "--namespace", help="override the profile's namespace")
    e.add_argument("--sv-subdomain", dest="sv_subdomain",
                   help="override the profile's wildcard domain")
    e.add_argument("--sv-tls-secret", dest="sv_tls_secret",
                   help="override the profile's wildcard TLS secret")
    e.add_argument("--ingress-class", dest="ingress_class",
                   help="IngressClass to put on the Ingress. Defaults to nginx; "
                        "on OpenShift use openshift-default and no alias is needed")
    e.add_argument("-o", "--output", default=gen_mod.SV_EXPOSE_FILE)
    e.set_defaults(fn=cmd_sv_expose)

    d = sub.add_parser("doctor", help="can this cluster run the location's concurrency?")
    d.add_argument("--api-key", help="required with --harbor-id (facts are "
                                     "gathered live); otherwise --facts is read")
    d.add_argument("--harbor-id", help="gather facts from the API instead of --facts")
    d.add_argument("--facts", default="facts.json")
    d.add_argument("--manifests", default="out",
                   help="directory holding profile.json -- the options the "
                        "checks measure the cluster against")
    d.add_argument("-n", "--namespace",
                   help="target namespace (default: the profile's, or the "
                        "one --cluster-evidence was collected for)")
    d.add_argument("--cluster-evidence", metavar="FILE",
                   help="preflight a cluster you have no access to, from the "
                        "JSON scripts/bzm-cluster-evidence.sh produced there. "
                        "The checks are the same ones; egress, which needs a "
                        "pod in the namespace, reports as unverified")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("suggest",
                       help="what a cluster's evidence implies about the "
                            "generate options")
    s.add_argument("--cluster-evidence", metavar="FILE", required=True,
                   help="the JSON scripts/bzm-cluster-evidence.sh produced on "
                        "the cluster. No API key and no cluster access needed: "
                        "every answer comes out of this file")
    s.add_argument("--json", action="store_true",
                   help="the suggestions as data -- option, strength, value, "
                        "candidates, the evidence each came from")
    s.set_defaults(fn=cmd_suggest)

    w = sub.add_parser("toolcheck",
                       help="does this workstation have what livetest shells "
                            "out to? (run before a 12-20 minute rig run)")
    w.add_argument("--cluster", choices=["current", "kind", "minikube"],
                   default="current", help="the --cluster you intend to use")
    w.add_argument("--local-registry", type=int, nargs="?", const=5001,
                   metavar="PORT", help="check the registry rig too")
    w.add_argument("--local-proxy", action="store_true",
                   help="check the proxy rig too")
    w.set_defaults(fn=cmd_toolcheck)

    i = sub.add_parser("images", help="list/pull/mirror the location's images")
    i.add_argument("--facts")
    i.add_argument("--api-key")
    i.add_argument("--harbor-id")
    i.add_argument("--all", action="store_true")
    i.add_argument("--pull", action="store_true")
    i.add_argument("--mirror", metavar="REGISTRY")
    i.add_argument("--platform", default="linux/amd64",
                   help="pull arch (BlazeMeter images are amd64-only)")
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(fn=cmd_images)

    t = sub.add_parser("livetest", help="deploy to a cluster, verify agent online")
    t.add_argument("--api-key", required=True)
    t.add_argument("--facts", default="facts.json")
    t.add_argument("--manifests", default="out")
    t.add_argument("--namespace", required=True)
    t.add_argument("--ship-id", dest="ship_id")
    t.add_argument("--cluster", choices=["current", "kind", "minikube"], default="current")
    t.add_argument("--timeout", type=int, default=600)
    t.add_argument("--keep", action="store_true", help="skip teardown")
    t.add_argument("--local-registry", type=int, nargs="?", const=5001, metavar="PORT",
                   help="start a registry:2 container, mirror the location's images "
                        "into it, and make minikube trust it (generate manifests "
                        "with --private-registry host.minikube.internal:PORT)")
    t.add_argument("--local-proxy", action="store_true",
                   help="start a mitmproxy container on the cluster's docker "
                        "network -- an HTTP proxy that also terminates TLS with "
                        "its own CA -- regenerate the manifests (from "
                        "out/profile.json) with HTTP(S)_PROXY + that CA, and "
                        "require the agent's blazemeter.com traffic to show up "
                        "in the proxy log. minikube/kind only")
    t.add_argument("--run-test", dest="run_test", metavar="TEST_ID",
                   help="after the agent is online, run this existing BlazeMeter "
                        "test on the location so crane actually spawns an engine, "
                        "then check the engine's image, CA mount and proxy env. "
                        "The test's locations are repointed at the private "
                        "location and restored afterwards")
    t.add_argument("--engine-cpu", default="1",
                   help="engine CPU limit while running --run-test (default 1; "
                        "the documented 2 CPU / 8Gi will not schedule on a laptop)")
    t.add_argument("--engine-mem", default="4Gi",
                   help="engine memory limit while running --run-test (default 4Gi)")
    t.add_argument("--contain-egress", action="store_true",
                   help="with --local-proxy: start minikube with calico and apply "
                        "a default-deny egress NetworkPolicy (DNS + apiserver + "
                        "proxy only), then prove from inside the crane pod that "
                        "BlazeMeter is unreachable except through the proxy")
    t.add_argument("--skip-negative-control", action="store_true",
                   help="with --local-proxy, skip the pre-run deploy that strips "
                        "the CA and must fail (saves ~2 min, at the cost of not "
                        "knowing whether the rig can fail at all)")
    t.add_argument("--proxy-auth", metavar="USER:PASS", default="bzm:s3cr3t",
                   help="credentials the local proxy demands ('none' for an open "
                        "proxy); they get URL-encoded into HTTP(S)_PROXY")
    t.set_defaults(fn=cmd_livetest)

    u = sub.add_parser("ui", help="start the local web UI")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default 127.0.0.1, this machine "
                        "only). Widening it makes the page -- and so your API "
                        "key -- reachable from the network; an SSH tunnel to "
                        "the default is usually the better answer")
    u.add_argument("--api-key", help="preload this api-key.json")
    u.add_argument("--no-browser", action="store_true")
    u.add_argument("--dev", action="store_true",
                   help="auto-restart on backend code changes; pair with "
                        "`npm run dev` in frontend/ for UI hot-reload")
    u.set_defaults(fn=cmd_ui)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
