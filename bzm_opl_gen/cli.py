"""bzm-opl-gen: generate + live-test BlazeMeter OPL k8s/OpenShift deployments
from a customer's actual BlazeMeter account.

Subcommands:
  plan         how much infrastructure a load target needs -- no account, no cluster
  locations    list private locations (harbors) across the account
  create-agent create an agent in a location, print id + AUTH_TOKEN
  facts        query the account, write facts.json (harbor, agents, images,
               functionalities)
  generate     render manifests from facts + customer parameters
  doctor       preflight a cluster: can it schedule the location's concurrency?
  triage       after deploying: name the known failures in the namespace, with fixes
  suggest      what a cluster's evidence implies about the generate options
  ca-check     does a CA bundle verify the chain this network presents?
  sv-expose    emit a working Service+Ingress per deployed virtual service
  images       list / explain / pull / mirror / verify the images the location
               actually needs
  livetest     start a bundle for real (a cluster, or docker compose) and
               verify the agent comes online
"""

import argparse
import collections
import json
import os
import sys

from . import (api, bundle_check, ca_check, core, doctor, facts as facts_mod,
               generate as gen_mod, kube, livetest, plan, suggest as suggest_mod,
               sv_read, triage as triage_mod, verdict, workstation)
from . import bundle_names, bundle_options, ca_trust, footprint, service_virt


def _client(a):
    """The account client from --api-key, or the environment without one."""
    return core.client_from_key(a.api_key)


def _resolve_account(client, a):
    """--account-id wins; --account-name matches a case-insensitive substring;
    otherwise the key's default account."""
    if a.account_id:
        return a.account_id
    accounts = core.accounts(client)
    if a.account_name:
        hits = [x for x in accounts if a.account_name.lower() in (x.get("name") or "").lower()]
        if len(hits) != 1:
            sys.exit(f"--account-name '{a.account_name}' matched {len(hits)} accounts: "
                     f"{[(x['id'], x.get('name')) for x in hits or accounts]}")
        return hits[0]["id"]
    account_id = core.default_account_id(client)
    if account_id is None:
        sys.exit("this API key names no default account -- pass --account-id "
                 "or --account-name")
    return account_id


def cmd_locations(a):
    client = _client(a)
    account_id = _resolve_account(client, a)
    # No cap: a terminal scrolls.
    locs = core.locations(client, account_id)
    print(f"account {account_id}: {len(locs)} private locations")
    for l in locs:
        ships = ", ".join(f"{s['id']} ({s.get('name')}, {s.get('state')})"
                          for s in l.get("ships", [])) or "none"
        print(f"  {l['id']}  {l.get('name')!r}  slots={l.get('slots')}  "
              f"funcIds={l.get('funcIds')}\n      ships: {ships}")


def cmd_create_location(a):
    client = _client(a)
    account_id = _resolve_account(client, a)
    if a.workspace_id:
        wsid = a.workspace_id
    else:
        if not a.workspace_name:
            sys.exit("--workspace-id or --workspace-name required")
        wss = core.workspaces(client, account_id)
        hits = [w for w in wss if a.workspace_name.lower() in (w.get("name") or "").lower()]
        if len(hits) != 1:
            sys.exit(f"--workspace-name '{a.workspace_name}' matched {len(hits)}: "
                     f"{[(w['id'], w.get('name')) for w in hits]}")
        wsid = hits[0]["id"]
    made = core.create_location(client, a.name, account_id, wsid,
                                func_ids=a.func_ids, slots=a.slots,
                                threads_per_engine=a.threads_per_engine)
    h = made["location"]
    print(f"created location '{h.get('name')}' harbor_id={h['id']} "
          f"(account {account_id}, workspace {wsid}, funcIds={a.func_ids}, "
          f"slots={h.get('slots')}, threadsPerEngine={h.get('threadsPerEngine')})")
    if made["warning"]:
        print(made["warning"], file=sys.stderr)
    print(f"next: bzm-opl-gen create-agent --api-key {a.api_key} --harbor-id {h['id']} --name <agent-name>")


def cmd_delete_location(a):
    gone = core.delete_location(_client(a), a.harbor_id)
    print(f"deleted location '{gone['name']}' ({gone['deleted']}) and its "
          f"{len(gone['ships_deleted'])} ship(s)")


def cmd_create_agent(a):
    made = core.create_agent(_client(a), a.harbor_id, a.name)
    ship = made["ship"]
    # The ids first: the agent exists whatever the token endpoint answered.
    print(f"harbor_id:  {a.harbor_id}")
    print(f"ship_id:    {ship['id']}  (name: {ship.get('name')})")
    if made["token_error"]:
        sys.exit(made["token_error"])
    print(f"auth_token: {made['auth_token']}")
    print("\nKeep that auth_token: it is the durable artifact of this command. "
          "Nothing here records it, and issuing another one (reveal_token, or "
          "generate --rotate-token) invalidates this one along with any agent "
          "running on it.")
    print(f"\nnext: bzm-opl-gen facts --api-key {a.api_key} --harbor-id {a.harbor_id}")
    print(f"      bzm-opl-gen generate --ship-id {ship['id']} "
          f"--auth-token <the auth_token above> ...")


def cmd_facts(a):
    """Gather facts from the account, or -- with --manual -- build them from the
    ids BlazeMeter shows on the agent, for an account nobody here can reach."""
    if a.manual:
        # Either id may be blank; facts.manual writes its marker instead. The
        # images are pinned to BlazeMeter's newest releases where it answers.
        f = core.manual_facts(a.harbor_id, a.ship_id,
                              func_ids=a.func_ids)["facts"]
    else:
        if not a.api_key:
            sys.exit("facts needs --api-key, or --manual to build them from "
                     "values you already have")
        if not a.harbor_id:
            sys.exit("facts needs --harbor-id: it is the location to read. "
                     "--manual takes it too, and takes it blank")
        f = core.gather_facts(_client(a), a.harbor_id)
    facts_mod.save(f, a.output)
    print(f"wrote {a.output}: location '{f['harbor_name'] or f['harbor_id']}' "
          f"funcIds={f['func_ids']} ships={len(f['ships'])} "
          f"images={len(f['images'])} ({f['images_source']})")
    for warning in core.facts_warnings(f):
        print(f"note: {warning}", file=sys.stderr)


def cmd_generate(a):
    f = facts_mod.load(a.facts)
    opts = {}
    if a.profile:
        with open(a.profile) as fh:
            opts.update(json.load(fh))
    for key in ("platform", "openshift_cluster",
                "namespace", "ship_id", "auth_token", "output_format",
                "private_registry", "pull_secret", "service_type",
                # Tri-state: --no-auto-update's False must override a profile.
                "auto_update",
                "service_account_name",
                "sv_ingress", "sv_subdomain", "sv_tls_secret", "sv_istio_gateway",
                "sv_hostname"):
        v = getattr(a, key, None)
        if v is not None:
            opts[key] = v
    if a.no_secret:
        opts["use_secret"] = False
    if a.no_create_service_account:
        opts["service_account_create"] = False
    if a.cluster_rbac:
        opts["cluster_rbac"] = True
    if getattr(a, "crane_hook", False):
        opts["crane_hook"] = True
    if a.no_restrict_engines:
        opts["restrict_engines"] = False
    if a.tolerations:
        opts["tolerations"] = json.loads(a.tolerations)
    if a.node_selector:
        opts["node_selector"] = json.loads(a.node_selector)
    # `is not None`: an explicit '{}' or '[]' differs from not passing the flag.
    if a.engines_per_node is not None:
        opts["engines_per_node"] = a.engines_per_node
    if a.engine_tolerations is not None:
        opts["engine_tolerations"] = json.loads(a.engine_tolerations)
    if a.engine_node_selector is not None:
        opts["engine_node_selector"] = json.loads(a.engine_node_selector)
    if a.ca_bundle:
        # DER and PKCS#7 exports arrive as PEM; the lint below says the rest.
        opts["ca_bundle"] = core.ca_bundle_pem(core.read_ca_bundle(a.ca_bundle))
    # PEM flags take a file; the option carries its content. The key is never
    # written to profile.json, so a replay must pass --sv-tls-key again.
    for flag, key in (("sv_tls_cert", "sv_tls_cert"),
                      ("sv_tls_key", "sv_tls_key")):
        path = getattr(a, flag, None)
        if path:
            with open(path) as fh:
                opts[key] = fh.read()
    if getattr(a, "ca_bundle_slot", False):
        opts["ca_bundle_slot"] = True
    # A file name, not a path: the ConfigMap is built from it at install time.
    if getattr(a, "ca_cert_file", None):
        opts["ca_cert_file"] = a.ca_cert_file
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
    if a.env:
        # Merged over a profile's; names are validated at generate time.
        env = dict(opts.get("extra_env") or {})
        for item in a.env:
            name, sep, value = item.partition("=")
            if not sep:
                sys.exit(f"--env {item}: expected NAME=VALUE")
            env[name] = value
        opts["extra_env"] = env
    # A client only for the flag that mints, so a bad key file is not read on a
    # run that never touches the account.
    client = _client(a) if a.api_key and a.rotate_token else None
    if a.api_key and not a.rotate_token:
        print("note: --api-key has no effect on `generate` without "
              "--rotate-token. Fetching an AUTH_TOKEN issues a new one and "
              "revokes the token the running agent holds, so --api-key is only "
              "the credential for --rotate-token.",
              file=sys.stderr)
    # announce=print on stdout, so the rotation warning keeps its place ahead of
    # the mint in a pipe or a CI log.
    built = core.build_bundle(f, opts, client=client, rotate=a.rotate_token,
                              out_dir=os.path.abspath(a.output), write=True,
                              announce=print)
    print(built.token.message)
    notice = ca_trust.ca_slot_notice(opts)
    if notice:
        print(notice)
    for warning in core.ca_bundle_warnings(opts):
        print(warning, file=sys.stderr)
    print(f"wrote {len(built.written)} files to {a.output}/: "
          + ", ".join(sorted(w["name"] for w in built.written)))


# More certificates than this and only the ones worth reading are listed.
CA_LIST_LIMIT = 25


def _ca_line(n, d):
    role = d["role"] if d["role"] != "leaf" else "leaf (not a CA)"
    return (f"  [{n}] {role:<16} {d['subject']}\n"
            f"       issuer {d['issuer']}; expires {d['not_after'][:10]} "
            f"({d['days_left']} days); SHA256 {d['sha256'][:23]}...")


def print_ca_lint(lint, verbose=False):
    """The lint as lines: the certificates, then each finding."""
    certs = lint["certificates"]
    form = {"pem": "PEM", "der": "a DER certificate",
            "pkcs7": "a DER PKCS#7 bundle"}.get(lint["form"],
                                                 "nothing recognisable")
    print(f"CA bundle: {len(certs)} certificate(s), read as {form}")
    flagged = {f["cert"] for f in lint["findings"]}
    shown = [(n, d) for n, d in enumerate(certs, 1)
             if verbose or len(certs) <= CA_LIST_LIMIT or n in flagged
             or d["role"] != "root"]
    for n, d in shown:
        print(_ca_line(n, d))
    if len(shown) < len(certs):
        print(f"  ...and {len(certs) - len(shown)} more root(s) with no "
              f"finding (--verbose lists them)")
    for f in lint["findings"]:
        print(f"  {f['severity']:<4}  {f['message']}")
    if not lint["findings"]:
        print("  no findings")


def cmd_ca_check(a):
    """Lint a CA bundle and verify the chain each host presents against it."""
    data = core.read_ca_bundle(a.ca_bundle)
    result = core.ca_check(data, hosts=a.host, proxy=a.proxy,
                           registry=a.registry)
    if a.json:
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 1)
    print_ca_lint(result["lint"], a.verbose)
    for r in result["hosts"]:
        via = f" via {r['proxy']}" if r["proxy"] else ""
        print(f"\n{r['host']}:{r['port']}{via}: "
              f"{r['status'].replace('_', ' ').upper()}"
              + (f" ({r['detail']})" if r["detail"] else ""))
        for i, d in enumerate(r["chain"]):
            lead = "  presented" if i == 0 else "           "
            print(f"{lead} {d['subject']}  <-  issued by {d['issuer']}"
                  if d else f"{lead} (a certificate that does not parse)")
        if r["missing_issuer"]:
            print(f"  missing: {r['missing_issuer']}\n"
                  f"  Ask your security team for this CA certificate, and "
                  f"its own issuers up to the root, and add them to the "
                  f"bundle.")
    count = collections.Counter(r["status"] for r in result["hosts"])
    fails = sum(f["severity"] == verdict.FAIL
                for f in result["lint"]["findings"])
    print(f"\n{count[ca_check.VERIFIED]} verified, "
          f"{count[ca_check.NOT_VERIFIED]} not verified, "
          f"{count[ca_check.UNREACHABLE]} unreachable, "
          f"{fails} lint failure(s)")
    if count[ca_check.NOT_VERIFIED]:
        print("Crane and its engines would fail TLS to the hosts not "
              "verified. Fix the bundle before deploying.")
    if count[ca_check.UNREACHABLE]:
        print("An unreachable host was not judged. Run this from a machine "
              "on the agent's network, with the proxy the agent will use.")
    sys.exit(0 if result["ok"] else 1)


def cmd_sv_expose(a):
    """Emit a working Service+Ingress per deployed virtual service.

    Run after the virtual services are deployed: the mocks are read off the
    running pods, which carry the identity crane actually used."""
    opts = gen_mod.load_profile(a.manifests) if a.manifests else {}
    for key in ("sv_subdomain", "sv_tls_secret", "namespace"):
        v = getattr(a, key, None)
        if v is not None:
            opts[key] = v
    opts["namespace"] = opts.get("namespace") or a.namespace
    if a.ingress_class:
        opts["sv_ingress_class"] = a.ingress_class
    mocks = sv_read.sv_mocks(kube.cli_tool(), opts["namespace"])
    if not mocks:
        sys.exit(f"no virtual-service pods in namespace {opts['namespace']} -- "
                 f"deploy the virtual service in BlazeMeter first, then re-run")
    out = service_virt.sv_expose(mocks, opts["namespace"],
                            service_virt.sv_publish_cfg(opts))
    with open(a.output, "w") as fh:
        fh.write(out)
    names = ", ".join(f"{m['name']}:{m['port']}" for m in mocks)
    print(f"wrote {a.output}: {len(mocks)} virtual service(s) -- {names}")
    print(f"apply with: kubectl apply -n {opts['namespace']} -f {a.output}")


def cmd_plan(a):
    """Size the infrastructure a sizing needs, before any of it exists."""
    # The per-model flags are named after SIZING_MODELS' fields, so the
    # namespace is already keyed the way sizings_from reads it.
    sizings = plan.sizings_from(vars(a))
    p = core.capacity_plan(
        a.users, vus_per_engine=a.vus_per_engine,
        engine_cpu=a.engine_cpu_limit, engine_mem=a.engine_mem_limit,
        engines_per_node=a.engines_per_node, agents=a.agents,
        sizings=sizings)
    if a.json:
        print(json.dumps(p, indent=2))
        return
    if a.markdown:
        print(p["document"])
        return

    eng, node = p["engine"], p["node"]
    for r in p["sizings"]:
        if r["per_pod"] is None:
            print(f"{r['target']:,} {r['unit']}: not sized here, no "
                  f"{r['per_pod_unit']} figure has been measured")
            continue
        print(f"{r['target']:,} {r['unit']} at {r['per_pod']:,} per "
              f"{r['pod']}"
              + ("  (assumed -- what a pod this size is rated for)"
                 if r["per_pod_source"] == "assumed" else ""))
    print(f"  {p['engines']} engines of {eng['cpu']} CPU / {eng['memory']} / "
          f"{eng['disk_gb']}GB disk"
          + (f", from the {plan.SIZING_MODELS[p['driven_by']]['name']} sizing "
             f"(the largest)" if len(p["sizings"]) > 1 else ""))
    print(f"  {p['engines_per_agent']} engines per agent across {p['agents']} "
          f"agent(s) -- the location's slots")
    print(f"  {p['nodes_per_agent']} node(s) per agent of {node['cpu']} vCPU / "
          f"{node['memory']} capacity, at {p['engines_per_node']} engine(s) each"
          + (f" ({p['nodes']} nodes in all)" if p["agents"] > 1 else ""))
    print(f"  peak {p['peak']['cpu']} vCPU / {p['peak']['memory']} per agent's "
          f"cluster; 0 between runs")
    print(f"  agent: 1 small always-on node ({p['crane']['cpu_limit']} CPU / "
          f"{p['crane']['memory_limit']})")
    # BlazeMeter's own field names: this is what to type into them.
    print(f"  location: slots={p['location']['slots']} (engines per agent), "
          f"threadsPerEngine={p['location']['threads_per_engine']} (virtual "
          f"users per engine),")
    print(f"            overrideCPU={p['location']['override_cpu']}, "
          f"overrideMemory={p['location']['override_memory']}")
    for w in p["warnings"]:
        print(f"  ! {w}")
    if a.output:
        out = os.path.abspath(a.output)
        core.write_bundle({p["document_file"]: p["document"]}, out)
        print(f"\nwrote {os.path.join(out, p['document_file'])} -- the request "
              f"to hand to whoever provisions the cluster")
    else:
        print(f"\n-o DIR writes {p['document_file']}: the same numbers as a "
              f"document to raise the infrastructure request with "
              f"(--markdown prints it here)")


def cmd_doctor(a):
    """Preflight the cluster against the location's advertised concurrency."""
    if a.harbor_id:
        if not a.api_key:
            sys.exit("--harbor-id needs --api-key (or drop both and use --facts)")
        f = core.gather_facts(_client(a), a.harbor_id)
    else:
        f = facts_mod.load(a.facts)
    # The generated profile is what the checks measure against.
    try:
        opts = gen_mod.load_profile(a.manifests)
    except FileNotFoundError:
        opts = {}
        print(f"note: no {a.manifests}/profile.json -- checking against the "
              f"documented engine size and no scheduling constraints")
    doc = (core.evidence_document(a.cluster_evidence)
           if a.cluster_evidence else None)
    imported, namespace = core.preflight_cluster(doc, opts, a.namespace)
    # doctor.run rather than core.preflight: this command prints the report.
    checks = doctor.run(f, opts, namespace, evidence=imported)
    sys.exit(1 if doctor.has_failures(checks) else 0)


def cmd_suggest(a):
    """Say what a cluster's evidence implies about the generate options.

    Its own command: doctor answers whether a deployment survives the cluster,
    this answers how it should be configured. Nothing is applied. The
    suggestions are printed bare, with no configuration to merge against.
    """
    doc = core.evidence_document(a.cluster_evidence)
    try:
        suggestions = suggest_mod.from_evidence(doc)
    except ValueError as e:
        sys.exit(str(e))
    if a.json:
        print(json.dumps([suggest_mod.as_dict(s) for s in suggestions], indent=2))
    else:
        suggest_mod.report(doc, suggestions)


def cmd_triage(a):
    """Read a deployed agent's namespace and name the known failures in it.

    Exit 1 for a known failure only: a denied read or an unrecognised warning
    is reported and exits 0, as in doctor."""
    doc = core.triage(a.namespace, since=a.since, log_lines=a.crane_log_lines)
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        triage_mod.report(doc)
    sys.exit(0 if doc["ok"] else 1)


def cmd_toolcheck(a):
    """Preflight the workstation against the rig flags you intend to pass."""
    opts = {"cluster": a.cluster, "local_registry": a.local_registry,
            "local_proxy": a.local_proxy}
    # workstation.run prints the report, which is the whole of this command.
    checks = workstation.run(opts)
    sys.exit(0 if not doctor.has_failures(checks) else 1)


EXPLAIN_COLUMNS = ["ref", "key", "category", "functionalities", "required",
                   "verified", "tag_mutable", "source", "purpose",
                   "pulled_when", "registry_state", "registry_detail",
                   "digest", "size_mb", "resolves_to", "newest_tag",
                   "update_available"]


def _explain_cell(value):
    if isinstance(value, list):
        return ";".join(value)
    return "" if value is None else str(value)


def _print_explain(cat, fmt):
    """The catalogue rows in the format asked for; json is core's answer as
    is."""
    if fmt == "json":
        print(json.dumps(cat, indent=2))
        return
    rows = cat["images"]
    if fmt == "csv":
        import csv
        w = csv.writer(sys.stdout)
        w.writerow(EXPLAIN_COLUMNS)
        for r in rows:
            w.writerow([_explain_cell(r[c]) for c in EXPLAIN_COLUMNS])
        return
    looked = cat["registry_lookup"]["state"] != "not-asked"
    if fmt == "md":
        cols = ["Image", "What it does", "Functionality", "When it is pulled",
                "Seen in a live run"] + (["Size (MB)", "Is now", "Newest tag"]
                                         if looked else [])
        print("| " + " | ".join(cols) + " |")
        print("|" + "---|" * len(cols))
        for r in rows:
            cells = [f"`{r['ref']}`", r["purpose"], ", ".join(r["functionalities"]),
                     r["pulled_when"], "yes" if r["verified"] else "no"]
            if looked:
                cells += [_explain_cell(r["size_mb"]),
                          _explain_cell(r["resolves_to"]),
                          _explain_cell(r["newest_tag"])]
            print("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        return
    loc = cat["location"]
    print(f"location {loc['name'] or loc['harbor_id']} ({loc['harbor_id']}), "
          f"funcIds {loc['func_ids']}, image list {cat['image_list_state']}"
          if loc else "the built-in catalogue (no location)")
    for r in rows:
        need = {True: "required", False: "not required", None: ""}[r["required"]]
        flags = [f for f in (need, "floating tag" if r["tag_mutable"] else "",
                             "" if r["verified"] else "not seen in a live run") if f]
        print(f"\n{r['ref']}  [{', '.join(r['functionalities']) or 'none'}]"
              + (f"  ({'; '.join(flags)})" if flags else ""))
        print(f"    {r['purpose']}\n    pulled: {r['pulled_when']}")
        if looked:
            if r["registry_state"] == "read":
                now = (f", is {r['resolves_to']}" if r["resolves_to"] else "")
                newer = (f", newer tag {r['newest_tag']}" if r["update_available"]
                         else "")
                print(f"    registry: {r['digest'] or 'no such tag'}, "
                      f"{_explain_cell(r['size_mb']) or '?'} MB{now}{newer}")
            if r["registry_detail"]:
                print(f"    registry: {r['registry_detail']}")
    if looked and cat["registry_lookup"]["detail"]:
        print(f"\nWARN: {cat['registry_lookup']['detail']}", file=sys.stderr)


def _profile_options(a):
    """The bundle options --profile names, or None for a Kubernetes bundle's."""
    if not a.profile:
        return None
    try:
        with open(a.profile) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        sys.exit(f"--profile {a.profile}: {e}")


def _verify(f, a):
    out = core.verify_mirror(f, a.verify, options=_profile_options(a),
                             ca_file=a.ca_file)
    print(f"checking {out['registry']} (credentials: {out['credentials']})")
    for i in out["images"]:
        state = i["state"].upper() if i["state"] == "missing" else i["state"]
        print(f"  {state:8} {i['target']}"
              + (f"  {i['digest']}" if i["digest"] else "")
              + (f"  -- {i['detail']}" if i["state"] != "present" else ""))
    print(f"{out['present']} present, {out['missing']} missing, "
          f"{out['unread']} unread")
    if out["unread"]:
        print("WARN: an unread image may or may not be there; the registry did "
              "not say.", file=sys.stderr)
    if out["missing"]:
        sys.exit(1)


def cmd_images(a):
    f = facts_mod.load(a.facts) if a.facts else None
    if a.explain and f is None and not a.harbor_id:
        # No location named: the whole catalogue.
        _print_explain(core.image_catalog(None, lookup=a.lookup), a.format)
        return
    if f is None:
        f = core.gather_facts(_client(a), a.harbor_id)
    if a.verify:
        return _verify(f, a)
    if a.explain:
        _print_explain(core.image_catalog(f, lookup=a.lookup,
                                          all_images=a.all), a.format)
        return
    imgs = core.bundle_images(f, all_images=a.all)
    for ref in imgs:
        print(ref)
    if not a.pull:
        return
    # core runs the pull/tag/push, so this, the MCP tool and the bundle's
    # mirror script agree on targets.
    for cmd in core.mirror_images(f, mirror=a.mirror, platform=a.platform,
                                  dry_run=a.dry_run, all_images=a.all,
                                  options=_profile_options(a))["commands"]:
        print(("DRY-RUN: " if a.dry_run else "+ ") + cmd)


def _regenerator(facts, a, ship_id, auth_token):
    """Re-render the manifests in place with extra options merged onto their
    profile.json -- for rig flags whose values exist only once the rig is up.

    `auth_token` is one value for the whole run: minting per render would
    revoke the token the previous deploy is running on.
    """
    def regenerate(overlay):
        opts = gen_mod.load_profile(a.manifests)
        opts.update(overlay)
        opts["namespace"] = a.namespace
        opts["ship_id"] = opts.get("ship_id") or ship_id
        opts["auth_token"] = auth_token
        built = core.build_bundle(facts, opts,
                                  out_dir=os.path.abspath(a.manifests),
                                  write=True)
        print(f"regenerated {len(built.written)} files in {a.manifests}/ with "
              f"proxy + CA trust: "
              + ", ".join(sorted(w["name"] for w in built.written)))
    return regenerate


def _cluster_shaped(a):
    """The livetest flags only a cluster has, named for a refusal: a compose
    run that quietly dropped one would claim something it never tested."""
    return [name for name, on in (
        (f"--cluster {a.cluster}", a.cluster != "current"),
        ("--local-registry", a.local_registry),
        ("--local-proxy", a.local_proxy),
        ("--contain-egress", a.contain_egress),
        ("--run-test", a.run_test),
    ) if on]


def _livetest_compose(a, client, facts, ship_id, opts):
    """`livetest` for a docker bundle: up, online, down. Exits; never returns.

    Nothing is re-rendered, so no credential is minted: the bundle deployed is
    the bundle on disk. What this does not prove is in docs/live-test.md.
    """
    bad = bundle_check.bundle_check(a.manifests, facts["harbor_id"], ship_id,
                                opts).report()
    if bad:
        sys.exit(bad)
    unusable = _cluster_shaped(a)
    if unusable:
        sys.exit(
            f"{a.manifests}/ is a docker bundle, which this command starts with "
            f"`docker compose up -d` on this host -- there is no cluster and no "
            f"node here, so {', '.join(unusable)} would reach nothing. Every one "
            f"of them is cluster-shaped (a registry blackholed on a node, a "
            f"NetworkPolicy, an engine pod), and a run that accepted them and "
            f"passed would be claiming things it never tested. Drop "
            f"{'them' if len(unusable) > 1 else 'it'}, or run the cluster rig "
            f"against a --format manifests bundle.")
    if a.namespace:
        print(f"note: --namespace {a.namespace} reaches nothing here -- a "
              f"docker bundle is one container on this host and has no "
              f"namespace")
    ok = livetest.run_compose(client, a.manifests, facts["harbor_id"], ship_id,
                              timeout=a.timeout, keep=a.keep, opts=opts)
    sys.exit(0 if ok else 1)


def cmd_livetest(a):
    f = facts_mod.load(a.facts)
    client = _client(a)
    ship_id = core.sole_ship_id(f, a.ship_id)
    if not ship_id:
        sys.exit(f"--ship-id required (location has {len(f['ships'])} ships)")
    # The options the manifests were rendered from, for the read-back checks.
    try:
        opts = gen_mod.load_profile(a.manifests)
    except FileNotFoundError:
        opts = None
        print(f"note: no {a.manifests}/profile.json -- skipping the read-back "
              f"configuration checks (regenerate to enable them)")
    # A chart has nothing at the top level for kubectl to apply.
    if opts and opts.get("output_format") == "helm":
        sys.exit(
            f"{a.manifests}/ holds a Helm chart, and livetest deploys manifests "
            f"with kubectl. Re-generate that directory with --format manifests "
            f"(the two render the same objects), or install the chart yourself "
            f"and watch it with: bzm-opl-gen doctor / kubectl -n "
            f"{a.namespace or '<namespace>'} logs -l role=role-crane -f")
    # The bundle picks the rig, never a flag: see bundle_check.bundle_platform.
    if bundle_check.bundle_platform(a.manifests, opts) == bundle_check.PLATFORM_COMPOSE:
        _livetest_compose(a, client, f, ship_id, opts)
    if not a.namespace:
        sys.exit("--namespace is required for a manifests bundle: livetest "
                 "creates it and deploys into it")
    # The bundle guards below all run before any mint: a run about to be
    # refused must not rotate a credential another agent is holding.
    bad = bundle_check.bundle_check(a.manifests, f["harbor_id"], ship_id,
                                opts).report()
    if bad:
        sys.exit(bad)
    # The rig creates the namespace, so a ServiceAccount the bundle does not
    # create never exists and no pod ever starts.
    if opts and not opts.get("service_account_create", True):
        sa = opts.get("service_account_name")
        sys.exit(
            f"{a.manifests}/ references ServiceAccount '{sa}' without creating "
            f"it, and livetest deploys into a namespace it creates itself, "
            f"where that account will not exist. Re-generate without "
            f"--no-create-service-account, or create '{sa}' in {a.namespace} "
            f"yourself before starting the run")
    # Likewise the CA ConfigMap of the `file` and `existing` modes.
    ca_bad = bundle_check.ca_configmap_refusal(opts, a.local_proxy)
    if ca_bad:
        sys.exit(ca_bad)
    # A run that re-renders nothing deploys the token on disk; a placeholder
    # there means an agent that can never authenticate.
    if not (a.local_proxy or a.run_test) and not a.auth_token \
            and gen_mod.existing_auth_token(a.manifests) is None:
        sys.exit(
            f"{a.manifests}/ carries no usable AUTH_TOKEN -- it is still the "
            f"{bundle_options.DEFAULT_OPTIONS['auth_token']} placeholder, and this run "
            f"re-renders nothing, so it would deploy that. The agent could not "
            f"authenticate, and the rig would wait out its whole timeout to say "
            f"only that it never came online. "
            f"{core.token_recovery_hint(opts)}")
    proxy_user = proxy_pass = None
    # Read off the flag as typed, so a file-mode bundle is not refused for a
    # flag nobody passed.
    if a.ca_mode and a.ca_mode != "inline" and not a.local_proxy:
        sys.exit("--ca-mode needs --local-proxy: the CA under test is the "
                 "proxy's, and a run without one configures no CA trust at all")
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
    # A warning, not a refusal: --local-registry covers crane's image, but only
    # --run-test pulls the engine image. Silent for a location with no engine
    # (empty funcIds are the performance case).
    func_ids = f.get("func_ids") or []
    engine_here = not func_ids or any(facts_mod.runs_engine(i) for i in func_ids)
    if a.local_registry and not a.run_test and engine_here:
        # The blackhole is minikube's alone.
        covered = ("crane's own image and the public registries blackholed on "
                   "the node" if a.cluster == "minikube" else "crane's own image")
        print(f"warning: --local-registry without --run-test does not cover "
              f"the engine image. This run starts crane and no engine, so "
              f"nothing pulls the engine reference the bundle composed, and a "
              f"wrong one cannot fail here. What it does cover is {covered}. "
              f"Add --run-test <TEST_ID> to cover the engine.")
    # Unsaid, the CA mode under test is the one the bundle was generated with.
    ca_mode = bundle_check.resolved_ca_mode(opts, a.ca_mode)
    if a.local_proxy:
        print(bundle_check.ca_mode_notice(opts, ca_mode))
    # --local-proxy and --run-test re-render onto the profile. Only they mint:
    # a run that renders nothing deploys the bundle's own token, and minting
    # would revoke it.
    regenerate = None
    if opts is not None and (a.local_proxy or a.run_test):
        # One credential for the whole run, after every guard. Not reused from
        # a.manifests: it may have been rotated since, and a dead token looks
        # like a slow boot to the rig.
        token_opts = {"ship_id": ship_id}
        if a.auth_token:
            token_opts["auth_token"] = a.auth_token
        source = core.resolve_auth_token(f, token_opts, client=client,
                                         rotate=not a.auth_token,
                                         announce=print)
        print(source.message)
        if source.branch == core.TOKEN_PLACEHOLDER:
            # --auth-token given the placeholder string itself.
            sys.exit(source.message)
        regenerate = _regenerator(f, a, ship_id, token_opts["auth_token"])
    ok = livetest.run(client, a.manifests, a.namespace, f["harbor_id"], ship_id,
                      cluster=a.cluster, timeout=a.timeout, keep=a.keep,
                      facts=f, local_registry=a.local_registry,
                      local_proxy=a.local_proxy, proxy_user=proxy_user,
                      proxy_pass=proxy_pass, regenerate=regenerate, opts=opts,
                      negative_control_check=not a.skip_negative_control,
                      contain_egress=a.contain_egress, run_test=a.run_test,
                      engine_cpu=a.engine_cpu, engine_mem=a.engine_mem,
                      ca_mode=ca_mode)
    sys.exit(0 if ok else 1)


def cmd_mcp(a):
    """Serve the MCP tools on stdio. Nothing may be printed to stdout."""
    try:
        from . import mcp_server
    except ImportError:
        sys.exit("MCP dependencies missing -- pip install 'bzm-opl-gen[mcp]'")
    mcp_server.main()


def cmd_ui(a):
    if a.install_service or a.uninstall_service:
        # Before the server import: installing the agent needs no fastapi.
        from . import service
        try:
            if a.uninstall_service:
                out = service.uninstall()
                print(f"removed {out['removed']}" if out["removed"]
                      else "nothing installed -- no plist to remove")
            else:
                out = service.install(port=a.port, host=a.host,
                                      api_key_path=a.api_key)
                print(f"installed {out['plist']}\n"
                      f"serving {out['url']} from login onward "
                      f"(restarts if it dies)\n"
                      f"logs: {out['log']}\n"
                      f"remove with: bzm-opl-gen ui --uninstall-service")
        except service.ServiceError as e:
            sys.exit(str(e))
        return
    try:
        from . import server
    except ImportError:
        sys.exit("UI dependencies missing -- pip install 'bzm-opl-gen[ui]'")
    # Print an address a browser can open, not the wildcard bind.
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    print(f"bzm-opl-gen ui -> http://{shown}:{a.port}  (Ctrl-C to stop)",
          flush=True)
    server.main(port=a.port, open_browser=not a.no_browser, api_key_path=a.api_key,
                dev=a.dev, host=a.host)


def main():
    p = argparse.ArgumentParser(prog="bzm-opl-gen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    # No --api-key and no --facts: this comes before both.
    pl = sub.add_parser("plan",
                        help="how much infrastructure a load target needs "
                             "(no account, no cluster)")
    pl.add_argument("--users", metavar="N",
                    help="virtual users the test has to reach")
    # The other models' flags come from plan.SIZING_MODELS: the flag is the
    # model's target_field, the name sizings_from and the refusals use.
    for fid, m in plan.SIZING_MODELS.items():
        if fid == plan.PERFORMANCE:
            continue
        target_help = (f"{m['unit']} to size for -- the {m['name']} sizing's "
                       f"target, in its own unit")
        if m["baseline"] is None:
            target_help += (f". Stated in the plan and not sized from: how "
                            f"many {m['unit']} one {m['pod']} carries has not "
                            f"been measured, and nothing is assumed in its "
                            f"place")
        pl.add_argument("--" + m["target_field"].replace("_", "-"),
                        dest=m["target_field"], metavar="N", help=target_help)
        if not m["figure_field"]:
            continue
        pl.add_argument("--" + m["figure_field"].replace("_", "-"),
                        dest=m["figure_field"], metavar="N",
                        help=f"{m['figure_unit']} (default about "
                             f"{m['baseline']} for the "
                             f"{footprint.ENGINE_DEFAULT_CPU} CPU / "
                             f"{footprint.ENGINE_DEFAULT_MEM} engine, scaled "
                             f"from there). An estimate from the account "
                             f"owner, not a measurement")
    pl.add_argument("--vus-per-engine", dest="vus_per_engine",
                    help=f"virtual users one engine carries (BlazeMeter's "
                         f"`threadsPerEngine`). Default is what an engine of "
                         f"the chosen size is rated for -- "
                         f"{footprint.DEFAULT_THREADS_PER_ENGINE} for the "
                         f"{footprint.ENGINE_DEFAULT_CPU} CPU / "
                         f"{footprint.ENGINE_DEFAULT_MEM} engine, scaled from "
                         f"there. Your script decides the real number: measure "
                         f"it against one engine and re-run this")
    pl.add_argument("--engine-cpu-limit", dest="engine_cpu_limit",
                    help=f'engine CPU limit (default {footprint.ENGINE_DEFAULT_CPU})')
    pl.add_argument("--engine-mem-limit", dest="engine_mem_limit",
                    help=f'engine memory limit (default {footprint.ENGINE_DEFAULT_MEM})')
    pl.add_argument("--agents",
                    help="agents that will serve this location (default 1). "
                         "BlazeMeter's `slots` is engines per *agent*, so the "
                         "run is divided by this")
    pl.add_argument("--engines-per-node", dest="engines_per_node",
                    help="engines to a node (default 1; more is cheaper and "
                         "they contend)")
    pl.add_argument("-o", "--output", metavar="DIR",
                    help=f"write {plan.DOCUMENT_FILE} here")
    pl.add_argument("--markdown", action="store_true",
                    help="print that document instead of the summary")
    pl.add_argument("--json", action="store_true", help="the plan as data")
    pl.set_defaults(fn=cmd_plan)

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
    cl.add_argument("--func-ids", nargs="+", default=list(api.DEFAULT_FUNC_IDS))
    cl.add_argument("--slots", type=int, default=1,
                    help="concurrent engines this location's agent may run "
                         "(default 1); "
                         + "; ".join(
                             f"{r['label']} needs at least {r['minimum']}"
                             for r in core.SLOT_MINIMUMS.values()))
    cl.add_argument("--threads-per-engine", type=int,
                    default=footprint.DEFAULT_THREADS_PER_ENGINE,
                    help=f"max threads per engine (default "
                         f"{footprint.DEFAULT_THREADS_PER_ENGINE}); a location with "
                         f"this unset cannot start tests")
    cl.set_defaults(fn=cmd_create_location)

    dl = sub.add_parser("delete-location", help="delete a private location and its ships")
    dl.add_argument("--api-key", required=True)
    dl.add_argument("--harbor-id", required=True)
    dl.set_defaults(fn=cmd_delete_location)

    # `create-ship` stays as an alias: it is in docs and customer scripts.
    cs = sub.add_parser("create-agent", aliases=["create-ship"],
                        help="create an agent, print id + AUTH_TOKEN")
    cs.add_argument("--api-key", required=True)
    cs.add_argument("--harbor-id", required=True)
    cs.add_argument("--name", required=True)
    cs.set_defaults(fn=cmd_create_agent)

    f = sub.add_parser("facts", help="gather account facts -> facts.json")
    f.add_argument("--api-key")
    # Not argparse-required: --manual takes it blank, and cmd_facts says so.
    f.add_argument("--harbor-id")
    f.add_argument("--manual", action="store_true",
                   help="build facts from the ids BlazeMeter shows you, without "
                        "an API key, for a location whose account you cannot "
                        "reach. Both ids are optional. Images come from the "
                        "built-in catalogue")
    f.add_argument("--ship-id", dest="ship_id",
                   help="the agent, with --manual. Leave either id out and the "
                        "bundle carries <HARBOR_ID>/<SHIP_ID> and names them -- "
                        "for a location BlazeMeter has not issued ids for yet")
    f.add_argument("--func-ids", dest="func_ids", nargs="+",
                   default=list(api.DEFAULT_FUNC_IDS),
                   help="with --manual: the location's functionalities, which "
                        "decide which images the bundle names (default: "
                        "performance)")
    f.add_argument("-o", "--output", default="facts.json")
    f.set_defaults(fn=cmd_facts)

    g = sub.add_parser("generate", help="render manifests from facts")
    g.add_argument("--facts", default="facts.json")
    g.add_argument("--api-key",
                   help="the credential for --rotate-token, and nothing else "
                        "here: on its own it changes nothing, because fetching "
                        "an AUTH_TOKEN issues a new one and kills the agent "
                        "running on the old")
    g.add_argument("--rotate-token", dest="rotate_token", action="store_true",
                   help="issue a NEW AUTH_TOKEN for the ship (needs --api-key). "
                        "The previous one stops working at once and the agent "
                        "holding it sits at 0/1 Running until this bundle is "
                        "re-applied, Secret included. Without this flag the "
                        "token comes from --auth-token, or from the bundle "
                        "already in -o, or stays the placeholder")
    g.add_argument("--profile", help="JSON options file (see profiles/)")
    g.add_argument("--format", dest="output_format",
                   choices=list(bundle_options.OUTPUT_FORMATS),
                   help="manifests (default): flat YAML to kubectl apply. "
                        "helm: a chart in helm/ with values.yaml filled in from "
                        "the account -- both render the same objects. docker: a "
                        "docker run script for one agent on a host with a docker "
                        "daemon, where most of the options below mean nothing "
                        "and virtual services are published by hostname rather "
                        "than by ingress")
    g.add_argument("--platform", choices=["openshift", "k8s"])
    # The cluster defaults to plain Kubernetes; the posture (--platform) is a
    # separate question. Tri-state so a flag overrides a --profile either way.
    ocp = g.add_mutually_exclusive_group()
    ocp.add_argument("--openshift", dest="openshift_cluster",
                     action="store_true", default=None,
                     help="the target cluster is OpenShift: commands use oc, "
                          "sv_ingress=openshift (a Route) is allowed, and the "
                          "injected cluster trust bundle is offered")
    ocp.add_argument("--not-openshift", dest="openshift_cluster",
                     action="store_false", default=None,
                     help="the target cluster is plain Kubernetes (the "
                          "default): commands use kubectl")
    g.add_argument("--namespace")
    g.add_argument("--ship-id", dest="ship_id")
    g.add_argument("--auth-token", dest="auth_token",
                   help="the agent's AUTH_TOKEN, as create-agent printed it or "
                        "as the BlazeMeter UI shows it on the agent. Wins over "
                        "every other source and issues nothing, so an agent "
                        "already running on it keeps working")
    g.add_argument("--private-registry", dest="private_registry")
    g.add_argument("--pull-secret", dest="pull_secret")
    # Tri-state so profile.json records which one a bundle asked for.
    au = g.add_mutually_exclusive_group()
    au.add_argument("--auto-update", dest="auto_update", action="store_true",
                    default=None,
                    help="AUTO_KUBERNETES_UPDATE=true: let crane update its own "
                         "Deployment when BlazeMeter ships a newer agent. NOT "
                         "the default here, though it is in BlazeMeter's own "
                         "manifest -- crane takes field ownership doing it, so "
                         "`helm upgrade` then fails on a conflict that "
                         "--force-conflicts cannot resolve, and changing "
                         "anything means uninstall + install")
    au.add_argument("--no-auto-update", dest="auto_update", action="store_false",
                    help="AUTO_KUBERNETES_UPDATE=false, which is already the "
                         "default -- pass it to record the choice in "
                         "profile.json. The agent stays on the image in this "
                         "bundle until you re-generate, and one far enough "
                         "behind loses support")
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
                   choices=list(service_virt.SV_INGRESS_TYPES) + [service_virt.SV_INGRESS_NONE],
                   help="service virtualization: ingress controller to publish "
                        "virtual services through (required for a mockServices "
                        f"location, or {service_virt.SV_INGRESS_NONE} to generate such "
                        "a location for performance testing alone)")
    g.add_argument("--sv-subdomain", dest="sv_subdomain", metavar="DOMAIN",
                   help="wildcard domain your ingress controller serves, e.g. apps.example.com")
    g.add_argument("--sv-tls-secret", dest="sv_tls_secret", metavar="NAME",
                   help="wildcard TLS secret in the agent's own namespace, not "
                        "default; required even for HTTP")
    g.add_argument("--sv-istio-gateway", dest="sv_istio_gateway", metavar="NAME",
                   help="istio only, optional: reuse this Gateway instead of one per service")
    # The docker agent's own way of publishing virtual services.
    g.add_argument("--sv-hostname", dest="sv_hostname", metavar="HOST",
                   help="docker only: HOSTNAME_OVERRIDE -- the hostname this "
                        "agent advertises its virtual services under")
    g.add_argument("--sv-tls-cert", dest="sv_tls_cert", metavar="PEM_FILE",
                   help="docker only: PEM certificate the agent serves virtual "
                        "services with; must cover --sv-hostname")
    g.add_argument("--sv-tls-key", dest="sv_tls_key", metavar="PEM_FILE",
                   help="docker only: its private key, PEM with PKCS#8 syntax "
                        "(never recorded in profile.json)")
    g.add_argument("--no-secret", action="store_true", help="AUTH_TOKEN in ConfigMap")
    g.add_argument("--tolerations", help='crane pod (and engines, unless --engine-tolerations). JSON list, e.g. \'[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]\'')
    g.add_argument("--node-selector", dest="node_selector", help='crane pod (and engines, unless --engine-node-selector). JSON object, e.g. \'{"pool":"crane"}\'')
    g.add_argument("--engine-tolerations", dest="engine_tolerations",
                   help='engines only, overriding --tolerations. JSON list. Pass \'[]\' for "no tolerations, even though crane has some".')
    g.add_argument("--engines-per-node", dest="engines_per_node", type=int,
                   help="how many engines a node of the engine pool should hold (default 1). Sizes nodepools.md; reaches no manifest.")
    g.add_argument("--engine-node-selector", dest="engine_node_selector",
                   help='engines only, overriding --node-selector -- the dedicated engine pool. JSON object, e.g. \'{"pool":"bzm-engines"}\'. Pass \'{}\' to let engines land anywhere.')
    g.add_argument("--ca-bundle", dest="ca_bundle", metavar="PEM_FILE",
                   help="inline CA mode: PEM file -> generator creates the ConfigMap")
    g.add_argument("--ca-placeholder", dest="ca_bundle_slot", action="store_true",
                   help="file CA mode: the certificate is a file you supply, "
                        "named by --ca-cert-file. The bundle carries no PEM and "
                        "the ConfigMap is built from that file -- BlazeMeter's "
                        "own shape, and the answer when crane is failing TLS "
                        "and the certificate is still with the platform team")
    g.add_argument("--ca-cert-file", dest="ca_cert_file", metavar="NAME",
                   help="the certificate's file name, used as the ConfigMap "
                        "key, the mounted filename and (helm) the file read "
                        "from the chart directory. Blank -> <CA_CERT_FILE>")
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
    g.add_argument("--env", action="append", metavar="NAME=VALUE",
                   help="an agent environment variable this tool has no option "
                        "for, e.g. --env PREFERRED_INTERFACE=eth1. Repeatable. "
                        "Reaches the crane pod, not the engines it spawns; a "
                        "name the bundle already writes is refused, naming the "
                        "option that owns it")
    g.add_argument("--cluster-rbac", action="store_true", help="include optional ClusterRole")
    g.add_argument("--crane-hook", action="store_true",
                   help="add crane-hook: a one-shot Pod (plus its own read-only "
                        "Role and RoleBinding) that checks capacity, egress, RBAC "
                        "and ingress, then exits 0 or 1. Not part of the agent -- "
                        "`kubectl logs cranehook` is the report, and deleting it "
                        "changes nothing about the deployment")
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
    e.add_argument("-o", "--output", default=bundle_names.SV_EXPOSE_FILE)
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

    tr = sub.add_parser("triage",
                        help="after deploying: read the namespace and name "
                             "each known failure with its fix")
    tr.add_argument("-n", "--namespace", required=True,
                    help="the namespace the agent was deployed to")
    tr.add_argument("--since", default="1h",
                    help="how far back to read events and crane's log, e.g. "
                         "30m, 1h, 2h (default 1h; events expire after about "
                         "an hour on most clusters)")
    tr.add_argument("--crane-log-lines", type=int, default=500, metavar="N",
                    help="read at most the last N lines of each crane log "
                         "(default 500)")
    tr.add_argument("--json", action="store_true",
                    help="the report as data: findings, unrecognised "
                         "warnings, and what could not be read")
    tr.set_defaults(fn=cmd_triage)

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

    i = sub.add_parser("images", help="list/explain/pull/mirror/verify the "
                                      "location's images")
    i.add_argument("--facts")
    i.add_argument("--api-key")
    i.add_argument("--harbor-id")
    i.add_argument("--all", action="store_true")
    i.add_argument("--pull", action="store_true")
    i.add_argument("--mirror", metavar="REGISTRY")
    i.add_argument("--platform", default="linux/amd64",
                   help="pull arch (BlazeMeter images are amd64-only)")
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--explain", action="store_true",
                   help="what each image is for, which functionality needs it "
                        "and when it is pulled. Without --facts or --harbor-id, "
                        "every image the built-in catalogue knows")
    i.add_argument("--format", choices=["table", "md", "csv", "json"],
                   default="table", help="--explain output format")
    i.add_argument("--lookup", action="store_true",
                   help="with --explain: read each image's digest, size and "
                        "newest tag from BlazeMeter's public registry")
    i.add_argument("--verify", metavar="REGISTRY",
                   help="check that each image is in REGISTRY under the name "
                        "the mirror script pushes it to; exits 1 if one is "
                        "missing. Credentials come from BZM_REGISTRY_USER and "
                        "BZM_REGISTRY_PASSWORD, or the docker config. Prefix "
                        "http:// for a plain-HTTP registry")
    i.add_argument("--ca-file", metavar="PEM",
                   help="with --verify: the CA that signed REGISTRY's "
                        "certificate")
    i.add_argument("--profile", metavar="PROFILE_JSON",
                   help="with --verify or --mirror: the bundle's profile.json, "
                        "whose format and crane_hook decide the names "
                        "(default: a Kubernetes bundle)")
    i.set_defaults(fn=cmd_images)

    t = sub.add_parser("livetest", help="start a bundle for real, verify the "
                                        "agent comes online")
    t.add_argument("--api-key", required=True)
    t.add_argument("--facts", default="facts.json")
    t.add_argument("--manifests", default="out")
    t.add_argument("--namespace",
                   help="required for a manifests bundle: the namespace the rig "
                        "creates and deploys into. A --format docker bundle is "
                        "one container on this host and has none, so it is "
                        "started with docker compose and takes neither this nor "
                        "--cluster")
    t.add_argument("--ship-id", dest="ship_id")
    t.add_argument("--auth-token", dest="auth_token",
                   help="the agent's AUTH_TOKEN, if you are holding the one "
                        "create-agent printed. Without it the run issues exactly "
                        "one, once, and every render it makes uses that -- "
                        "which revokes the credential of anything already "
                        "deployed against this agent")
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
    t.add_argument("--ca-mode", choices=bundle_check.RIG_CA_MODES,
                   help="with --local-proxy: which CA-trust configuration to "
                        "deploy. 'inline' writes the MITM CA into a ConfigMap "
                        "the generator owns; 'existing' has the rig create one "
                        "under a name of its own and the bundle only reference "
                        "it; 'file' -- the bundle names a certificate file and "
                        "creates no ConfigMap, and the rig builds it the way a "
                        "customer's pipeline does. Default: the mode the bundle "
                        "was generated for, else inline")
    t.add_argument("--proxy-auth", metavar="USER:PASS", default="bzm:s3cr3t",
                   help="credentials the local proxy demands ('none' for an open "
                        "proxy); they get URL-encoded into HTTP(S)_PROXY")
    t.set_defaults(fn=cmd_livetest)

    c = sub.add_parser("ca-check",
                       help="check a CA bundle before deploying: lint the "
                            "file, then verify the chain each BlazeMeter host "
                            "presents on this network against it")
    c.add_argument("--ca-bundle", required=True, metavar="FILE",
                   help="the trust bundle: PEM, a DER .cer or a PKCS#7 .p7b")
    c.add_argument("--proxy", metavar="URL",
                   help="HTTP CONNECT proxy, http://[user:pass@]host:port. "
                        "Default: HTTPS_PROXY, minus NO_PROXY. Credentials are "
                        "never printed")
    c.add_argument("--host", action="append", metavar="HOST[:PORT]",
                   help="a host to verify, repeatable; replaces the default "
                        "set (the BlazeMeter API and the engine upload hosts)")
    c.add_argument("--registry", metavar="HOST",
                   help="also verify this registry (a prefix such as "
                        "reg.corp:5001/blazemeter is fine)")
    c.add_argument("--verbose", action="store_true",
                   help="list every certificate, not only the ones worth "
                        "reading in a large bundle")
    c.add_argument("--json", action="store_true",
                   help="the result as data")
    c.set_defaults(fn=cmd_ca_check)

    m = sub.add_parser("mcp", help="serve the MCP tools on stdio (for an AI "
                                   "session; see docs/mcp.md)")
    m.set_defaults(fn=cmd_mcp)

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
    u.add_argument("--install-service", action="store_true",
                   help="macOS: install a LaunchAgent that serves the UI from "
                        "login onward (with --port/--host/--api-key as given) "
                        "instead of serving now; uses this python, so "
                        "reinstall if the venv moves")
    u.add_argument("--uninstall-service", action="store_true",
                   help="macOS: unload and remove the LaunchAgent")
    u.set_defaults(fn=cmd_ui)

    a = p.parse_args()
    try:
        a.fn(a)
    except core.CoreError as e:
        # The one place a core refusal becomes an exit: it is already a
        # sentence for whoever ran the command.
        sys.exit(str(e))


if __name__ == "__main__":
    main()
