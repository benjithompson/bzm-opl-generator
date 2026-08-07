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
  suggest      what a cluster's evidence implies about the generate options
  sv-expose    emit a working Service+Ingress per deployed virtual service
  images       list / pull / mirror the images the location actually needs
  livetest     start a bundle for real (a cluster, or docker compose) and
               verify the agent comes online
"""

import argparse
import json
import os
import sys

from . import (api, core, doctor, facts as facts_mod, generate as gen_mod,
               livetest, plan, suggest as suggest_mod, workstation)


def _client(a):
    """The account client, from whatever --api-key this command was given.

    One construction for every command, and it is core's: it reads the key file
    itself and refuses a bad one with a CoreError -- the sentence the web page
    and an MCP session get -- where the constructor used to read the file and
    raise SystemExit from inside it. A command with no --api-key at all reaches
    the environment rather than a TypeError from `open(None)`.
    """
    return core.client_from_key(a.api_key)


def _resolve_account(client, a):
    """--account-id wins; --account-name matches case-insensitive substring.

    The matching is the terminal's own -- there is no other surface where an
    account is named by typing part of it -- so it stays here. What the account
    tree *is* comes from core, which is what turns a refused key into a
    sentence instead of a BzmApiError nobody caught.
    """
    if a.account_id:
        return a.account_id
    accounts = core.accounts(client)
    if a.account_name:
        hits = [x for x in accounts if a.account_name.lower() in (x.get("name") or "").lower()]
        if len(hits) != 1:
            sys.exit(f"--account-name '{a.account_name}' matched {len(hits)} accounts: "
                     f"{[(x['id'], x.get('name')) for x in hits or accounts]}")
        return hits[0]["id"]
    u = core.user(client)
    return u["defaultProject"]["accountId"]


def cmd_locations(a):
    client = _client(a)
    account_id = _resolve_account(client, a)
    # No narrowing and no cap: a terminal scrolls, and select_locations' limit
    # exists for the caller with a result ceiling. See its docstring.
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
    # core's sentence, not one written here: a location that cannot start a
    # test 403s the same way whoever created it, and the web page and an MCP
    # session had no warning at all while this one did.
    if made["warning"]:
        print(made["warning"], file=sys.stderr)
    print(f"next: bzm-opl-gen create-agent --api-key {a.api_key} --harbor-id {h['id']} --name <agent-name>")


def cmd_delete_location(a):
    gone = core.delete_location(_client(a), a.harbor_id)
    print(f"deleted location '{gone['name']}' ({gone['deleted']}) and its "
          f"{len(gone['ships_deleted'])} ship(s)")


def cmd_create_agent(a):
    client = _client(a)
    ship = core.create_ship(client, a.harbor_id, a.name)
    # The ids first, then the token: the agent exists whatever the token endpoint
    # answers, and an account that refuses the fetch would otherwise leave the
    # only record of it in a traceback -- so the next attempt creates a second
    # agent for the same location.
    print(f"harbor_id:  {a.harbor_id}")
    print(f"ship_id:    {ship['id']}  (name: {ship.get('name')})")
    try:
        # core's fetch, for its refusal: the raw 403 body names no ship and no
        # way on. Exit on it rather than raise -- the message is the answer.
        token = core.fetch_ship_token(client, a.harbor_id, ship["id"])
    except core.CoreError as e:
        sys.exit(str(e))
    print(f"auth_token: {token}")
    # This is the one command that issues a credential as a matter of course,
    # and its output is the only copy: nothing here stores it, and `generate`
    # deliberately will not go and get another one -- fetching mints, and minting
    # revokes whatever is deployed. So say the durability out loud, and hand on a
    # next step that *takes* the token. `generate --api-key` was printed here,
    # and after #64 that would write a placeholder bundle.
    print("\nKeep that auth_token: it is the durable artifact of this command. "
          "Nothing here records it, and issuing another one (reveal_token, or "
          "generate --rotate-token) invalidates this one along with any agent "
          "running on it.")
    print(f"\nnext: bzm-opl-gen facts --api-key {a.api_key} --harbor-id {a.harbor_id}")
    print(f"      bzm-opl-gen generate --ship-id {ship['id']} "
          f"--auth-token <the auth_token above> ...")


def cmd_facts(a):
    """Gather facts from the account, or -- with --manual -- build them from the
    three values BlazeMeter shows on the agent, for a customer whose account
    nobody here can reach."""
    if a.manual:
        if not a.ship_id:
            sys.exit("--manual needs --ship-id: it is what identifies this agent "
                     "to BlazeMeter, and the API is not there to look it up")
        # facts.manual, not core.manual_facts: it reaches nothing, so there is
        # no refusal for core to carry, and its wrapper's second field is the
        # gui note this command already prints for both branches at once.
        f = facts_mod.manual(a.harbor_id, a.ship_id, func_ids=a.func_ids)
    else:
        if not a.api_key:
            sys.exit("facts needs --api-key, or --manual --ship-id to build them "
                     "from values you already have")
        f = core.gather_facts(_client(a), a.harbor_id)
    facts_mod.save(f, a.output)
    # Manual facts carry no location name -- nothing knows it -- so fall back to
    # the id rather than printing "location 'None'".
    print(f"wrote {a.output}: location '{f['harbor_name'] or f['harbor_id']}' "
          f"funcIds={f['func_ids']} ships={len(f['ships'])} "
          f"images={len(f['images'])} ({f['images_source']})")
    # A refused image list is the one state worth a line of its own: the images
    # below it are a catalogue's, and nothing in the count above says so. An
    # empty answer is not this -- that is the location saying it runs nothing --
    # and a location with no agent has nothing to refuse.
    if facts_mod.image_list_state(f) == facts_mod.IMAGE_LIST_UNREAD:
        print(f"note: the location's own image list could not be read "
              f"({f['image_list']['detail']}), so the images above are the "
              f"fallback catalogue's rather than this location's. Versions may "
              f"be wrong and browser images are missing.", file=sys.stderr)
    if facts_mod.gui_images_incomplete(f):
        print("note: functionalGui needs a version-pinned browser image "
              "(charmander/chrome_*, firefox_*, ...) that no catalogue can pick "
              "for you. The account names it, so gather facts with an API key; "
              "otherwise add the key to IMAGE_OVERRIDES by hand. Fine as it is "
              "against the public registry, not against a private one.",
              file=sys.stderr)


def cmd_generate(a):
    f = facts_mod.load(a.facts)
    opts = {}
    if a.profile:
        with open(a.profile) as fh:
            opts.update(json.load(fh))
    for key in ("platform", "openshift_cluster",
                "namespace", "ship_id", "auth_token", "output_format",
                "private_registry", "pull_secret", "service_type",
                # Tri-state, and `is not None` is what carries it: --no-auto-update
                # sets False, which must override a profile's true rather than
                # read as "not given".
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
    # `is not None`, not truthiness: `--engine-node-selector '{}'` means "engines
    # take no selector even though crane has one", which is a different bundle
    # from not passing the flag at all (engines follow crane).
    if a.engines_per_node is not None:
        opts["engines_per_node"] = a.engines_per_node
    if a.engine_tolerations is not None:
        opts["engine_tolerations"] = json.loads(a.engine_tolerations)
    if a.engine_node_selector is not None:
        opts["engine_node_selector"] = json.loads(a.engine_node_selector)
    if a.ca_bundle:
        with open(a.ca_bundle) as fh:
            opts["ca_bundle"] = fh.read()
    # A PEM is unpasteable on a command line, so these take a file and the
    # option carries what was in it -- --ca-bundle's shape, and for the same
    # reason. The key is not in profile.json (SECRET_OPTIONS), so replaying a
    # profile means passing --sv-tls-key again; without it the bundle carries a
    # <PLACEHOLDER> key file and the README says so, rather than a key file that
    # silently is not one.
    for flag, key in (("sv_tls_cert", "sv_tls_cert"),
                      ("sv_tls_key", "sv_tls_key")):
        path = getattr(a, flag, None)
        if path:
            with open(path) as fh:
                opts[key] = fh.read()
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
        # Merged over a profile's rather than replacing it, so `--profile x
        # --env A=1` adds one variable to the bundle x describes. Everything
        # about the *names* -- what is legal, what is already taken -- is
        # generate.extra_env's, and it is asked once, at generate time.
        env = dict(opts.get("extra_env") or {})
        for item in a.env:
            name, sep, value = item.partition("=")
            if not sep:
                sys.exit(f"--env {item}: expected NAME=VALUE")
            env[name] = value
        opts["extra_env"] = env
    # Where the token comes from is core.resolve_auth_token's, all four
    # branches of it. What is left here is the flags: a client is built only
    # for the one that mints, so a bad key file is not read on a run that was
    # never going to touch the account.
    client = _client(a) if a.api_key and a.rotate_token else None
    if a.api_key and not a.rotate_token:
        print("note: --api-key has no effect on `generate`. It no longer "
              "fetches an AUTH_TOKEN, because that fetch issues a new one and "
              "revokes the token the running agent holds -- it is the "
              "credential for --rotate-token, and nothing else here mints.",
              file=sys.stderr)
    # announce=print, on stdout beside the report rather than on stderr: the
    # warning is only worth anything ahead of the mint, and two streams do not
    # keep their order in a pipe or a CI log.
    source = core.resolve_auth_token(f, opts, client=client,
                                     rotate=a.rotate_token, out_dir=a.output,
                                     announce=print)
    # Always, for every branch, and unprefixed -- each message names the token
    # itself. Which of the four happened decides whether an agent is still
    # running, and the run that said nothing was the one that rotated (#64).
    print(source.message)
    # Through core, so every refusal generate() writes -- an engine limit that
    # is not a quantity, a service account named as the empty string -- arrives
    # as the sentence it was written as rather than at the foot of a traceback.
    # The token is already in `opts`, so this resolution takes the first branch.
    files = core.generate_bundle(f, opts)
    written = core.write_bundle(files, os.path.abspath(a.output))
    # `a.output` as it was typed, not the absolute path core needs: a shell is
    # the one caller that chose its own working directory. Sorted, because that
    # is the order this line has always listed them in -- preview_order is for
    # a reader being shown the files, and this is a receipt.
    print(f"wrote {len(written)} files to {a.output}/: "
          + ", ".join(sorted(w["name"] for w in written)))


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


def cmd_plan(a):
    """Size the infrastructure a sizing needs, before any of it exists."""
    # One row per model the command was given a target for, off the planner's
    # own table -- the flags below are its `target_field`/`figure_field` names,
    # so the namespace is already keyed the way `sizings_from` reads. `--users`
    # stays the performance model's own flag rather than becoming
    # --performance: it is what every existing script and every doc calls it,
    # and the planner takes it under that name too.
    sizings = plan.sizings_from(vars(a))
    try:
        p = core.capacity_plan(
            a.users, vus_per_engine=a.vus_per_engine,
            engine_cpu=a.engine_cpu_limit, engine_mem=a.engine_mem_limit,
            engines_per_node=a.engines_per_node, agents=a.agents,
            sizings=sizings)
    except core.CoreError as e:
        sys.exit(str(e))
    if a.json:
        print(json.dumps(p, indent=2))
        return
    if a.markdown:
        print(p["document"])
        return

    eng, node = p["engine"], p["node"]
    # A line per sizing, each in its own unit, and never a virtual-user line
    # for a browser suite. The unmeasured one has no "at N per engine" to state
    # and says what it has instead, because a target that produced no arithmetic
    # is the one somebody looks for.
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
    # The location block keeps BlazeMeter's own field names: it is what to type
    # into those fields, not a description of the plan.
    print(f"  location: slots={p['location']['slots']} (engines per agent), "
          f"threadsPerEngine={p['location']['threads_per_engine']} (virtual "
          f"users per engine),")
    print(f"            overrideCPU={p['location']['override_cpu']}, "
          f"overrideMemory={p['location']['override_memory']}")
    for w in p["warnings"]:
        print(f"  ! {w}")
    if a.output:
        # Through core so the absolute-path rule is the same one every other
        # written artifact obeys, rather than a second opinion about paths.
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
    # The generated profile is what the checks measure against -- engine size,
    # nodeSelector, registry, proxy/CA. Without it we can only assume defaults.
    try:
        opts = gen_mod.load_profile(a.manifests)
    except FileNotFoundError:
        opts = {}
        print(f"note: no {a.manifests}/profile.json -- checking against the "
              f"documented engine size and no scheduling constraints")
    # What is being preflighted, and against which namespace, is core's --
    # `preflight_cluster` is the same call the web UI's panel makes, and the
    # precedence between -n, the bundle and the file used to be stated here as
    # well as there. An unreadable or wrong file is a CoreError, which main()
    # exits on with the sentence doctor wrote.
    doc = (core.evidence_document(a.cluster_evidence)
           if a.cluster_evidence else None)
    imported, namespace = core.preflight_cluster(doc, opts, a.namespace)
    # doctor.run rather than core.preflight, and that is the whole of what stays
    # here: run() prints the verdict list and evaluate() does not, which is the
    # split every non-terminal caller depends on. The suggestions preflight()
    # returns alongside are `suggest`'s, which answers a different question.
    checks = doctor.run(f, opts, namespace, evidence=imported)
    sys.exit(1 if doctor.has_failures(checks) else 0)


def cmd_suggest(a):
    """Say what a cluster's evidence implies about the generate options.

    Deliberately its own command rather than a flag on `doctor`: that one
    answers whether a deployment survives this cluster and exits non-zero when
    it would not, and this one answers how it should have been configured. Same
    file, different question, and nothing here is applied to anything.
    """
    # The read is core's -- the same EvidenceUnreadable a browser and an MCP
    # session get for a file that is not there or will not parse.
    #
    # The suggestions are not, and that is this ticket's one deliberate
    # omission: `core.suggestions_from_evidence` merges each one against a
    # configuration (state, current) for a panel that has one, and this command
    # has no bundle to merge against -- `--json` is the bare suggestion, and
    # `report` prints the objects rather than dicts. Folding the two together
    # would change what this command answers, which #93 asked not to do.
    doc = core.evidence_document(a.cluster_evidence)
    try:
        suggestions = suggest_mod.from_evidence(doc)
    except ValueError as e:
        sys.exit(str(e))
    if a.json:
        print(json.dumps([suggest_mod.as_dict(s) for s in suggestions], indent=2))
    else:
        suggest_mod.report(doc, suggestions)


def cmd_toolcheck(a):
    """Preflight the workstation against the rig flags you intend to pass."""
    opts = {"cluster": a.cluster, "local_registry": a.local_registry,
            "local_proxy": a.local_proxy}
    checks = workstation.run(opts)
    # `workstation.run`, not `core.toolcheck`, and for the reason cmd_doctor
    # keeps doctor.run: core answers without printing, because for the MCP
    # server stdout is the JSON-RPC channel, and the report is the whole of what
    # this command is. It reaches no account and no cluster, so there is no
    # refusal for core to be carrying either.
    sys.exit(0 if not doctor.has_failures(checks) else 1)


def cmd_images(a):
    f = facts_mod.load(a.facts) if a.facts else None
    if f is None:
        f = core.gather_facts(_client(a), a.harbor_id)
    imgs = core.bundle_images(f, all_images=a.all)
    for ref in imgs:
        print(ref)
    if not a.pull:
        return
    # The pull/tag/push is core's, so this command and the MCP tool cannot
    # disagree about which name the target registry gets -- and core is the only
    # thing that shells out. This loop reports what it did; it does not do it.
    # There used to be a `subprocess.run` after the loop, on the loop variable,
    # guarded by a name that did not exist -- so every `images --pull` raised
    # NameError, and had it not, it would have re-run just the last command.
    for cmd in core.mirror_images(imgs, mirror=a.mirror, platform=a.platform,
                                  dry_run=a.dry_run)["commands"]:
        print(("DRY-RUN: " if a.dry_run else "+ ") + cmd)


def _regenerator(facts, a, ship_id, auth_token):
    """Re-render the manifests in place with extra generate() options merged
    onto the ones they were built from (out/profile.json). Used by
    --local-proxy, whose CA and address only exist once the rig is up.

    `auth_token` is one value for the whole run, passed in rather than fetched
    here. It used to call the token endpoint on every invocation, and a run
    makes several -- the negative control renders twice, then --run-test and
    --local-proxy each do -- so each render minted a credential that revoked the
    one the previous deploy was running on. The agent then sat `0/1 Running`,
    which the rig cannot tell from a slow boot; plausibly a real source of its
    intermittent failures.
    """
    def regenerate(overlay):
        opts = gen_mod.load_profile(a.manifests)
        opts.update(overlay)
        opts["namespace"] = a.namespace
        opts["ship_id"] = opts.get("ship_id") or ship_id
        opts["auth_token"] = auth_token
        written = core.write_bundle(core.generate_bundle(facts, opts),
                                    os.path.abspath(a.manifests))
        print(f"regenerated {len(written)} files in {a.manifests}/ with "
              f"proxy + CA trust: "
              + ", ".join(sorted(w["name"] for w in written)))
    return regenerate


# Everything on `livetest` that only a cluster has, as (what to call it in the
# refusal, is it on). Named individually rather than counted, because "some of
# your flags do not apply here" is a message somebody has to guess at -- and the
# guess is expensive: these are the flags whose absence makes a pass mean less
# than the person reading it thinks. Refused rather than ignored for that
# reason: a run that quietly dropped --contain-egress would report a pass that
# proved nothing about containment.
def _cluster_shaped(a):
    return [name for name, on in (
        (f"--cluster {a.cluster}", a.cluster != "current"),
        ("--local-registry", a.local_registry),
        ("--local-proxy", a.local_proxy),
        ("--contain-egress", a.contain_egress),
        ("--run-test", a.run_test),
    ) if on]


def _livetest_compose(a, client, facts, ship_id, opts):
    """`livetest` for a docker bundle: up, online, down. Exits; never returns.

    The one live proof `--format docker` has. It is the cheap end of this
    command -- a docker daemon, no cluster build, minutes rather than tens of
    minutes -- and it is deliberately the plain shape: no re-render, so no
    credential is minted and the bundle deployed is the bundle on disk, byte for
    byte. What it does not prove is in docs/live-test.md.
    """
    # First, exactly as on the cluster path: is this directory this agent's
    # bundle at all? Here as well as inside run_compose, so the CLI reports it
    # as a sentence rather than as the traceback of an exception the MCP server
    # needs run_compose to raise.
    bad = livetest.bundle_check(a.manifests, facts["harbor_id"], ship_id,
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
            f"against a --format manifests bundle. Engines on docker are "
            f"issue #184.")
    if a.namespace:
        # Named rather than refused, which is the rule the docker bundle's own
        # ignored options already keep: the value is somebody's habit from the
        # other rig, not a claim about this run.
        print(f"note: --namespace {a.namespace} reaches nothing here -- a "
              f"docker bundle is one container on this host and has no "
              f"namespace")
    # No mint, and so nothing above this had to be ordered around one: a compose
    # run re-renders nothing, so issuing a token would revoke the one the bundle
    # is carrying and deploy the bundle anyway. The credential this run uses is
    # whatever `generate` wrote, and bundle_check refuses one still carrying the
    # blank-value guard before the container exists.
    ok = livetest.run_compose(client, a.manifests, facts["harbor_id"], ship_id,
                              timeout=a.timeout, keep=a.keep, opts=opts)
    sys.exit(0 if ok else 1)


def cmd_livetest(a):
    f = facts_mod.load(a.facts)
    client = _client(a)
    ship_id = core.sole_ship_id(f, a.ship_id)
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
            f"and watch it with: bzm-opl-gen doctor / kubectl -n "
            f"{a.namespace or '<namespace>'} logs -l role=role-crane -f")
    # A docker bundle used to be refused here for the same reason the chart is
    # -- no cluster, so the *.yaml glob came back empty, every object "applied",
    # no pod was created and the run waited out its timeout. It has its own rig
    # now (#179): one container, started with docker compose on this host. Which
    # rig a run gets is read off the bundle rather than asked for, because a
    # flag saying it is a second place to get it wrong and both wrong answers
    # are that same silent run. See livetest.bundle_platform.
    if livetest.bundle_platform(a.manifests, opts) == livetest.PLATFORM_COMPOSE:
        _livetest_compose(a, client, f, ship_id, opts)
    if not a.namespace:
        # argparse used to require it, which was right for the one rig there was
        # and asks a compose run for a value that reaches nothing. Required here
        # instead, once the platform is known -- and not defaulted, because a
        # namespace nobody chose is a namespace this rig would then create.
        sys.exit("--namespace is required for a manifests bundle: livetest "
                 "creates it and deploys into it")
    # Is the directory this agent's bundle at all? --manifests defaults to out/,
    # which holds whatever the last `generate` left there, and the rig applies
    # every *.yaml in it. First of the bundle guards and before the mint below,
    # because a run that is about to be refused must not rotate a credential
    # some other agent is holding. See livetest.bundle_check for the incident.
    bad = livetest.bundle_check(a.manifests, f["harbor_id"], ship_id,
                                opts).report()
    if bad:
        sys.exit(bad)
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
    # Third guard of the same shape, and the one the mint below cannot cover: a
    # run that re-renders nothing deploys what is on disk, so if that bundle
    # carries the placeholder the agent can never authenticate. Every object
    # applies, no heartbeat arrives, and the run spends its whole 12-20 minutes
    # reporting only that the agent never came online. Checked here because the
    # paths that *do* re-render write a fresh token over it, so a placeholder on
    # disk is not a problem for them -- see the mint below.
    if not (a.local_proxy or a.run_test) and not a.auth_token \
            and gen_mod.existing_auth_token(a.manifests) is None:
        sys.exit(
            f"{a.manifests}/ carries no usable AUTH_TOKEN -- it is still the "
            f"{gen_mod.DEFAULT_OPTIONS['auth_token']} placeholder, and this run "
            f"re-renders nothing, so it would deploy that. The agent could not "
            f"authenticate, and the rig would wait out its whole timeout to say "
            f"only that it never came online. "
            f"{core.token_recovery_hint(opts)}")
    proxy_user = proxy_pass = None
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
    # Both --local-proxy and --run-test re-render the manifests (the proxy's CA,
    # the engine sizing); the callback needs a profile to merge onto, so it is
    # only available when one was found.
    #
    # And a run with neither renders nothing, so it deploys the bundle exactly as
    # it sits on disk -- which is why the mint below is inside this condition
    # rather than at the top of the command. Issuing a token a run is never going
    # to write would revoke the one that bundle is carrying, i.e. break the
    # deployment this rig is here to verify.
    regenerate = None
    if opts is not None and (a.local_proxy or a.run_test):
        # One credential for the whole run, minted here rather than per render,
        # and after every guard above so a run that is about to exit does not
        # rotate anything first. No flag asks for it: bringing an agent online is
        # what this command is for, so a rotation is implied by running it --
        # --auth-token is how a caller who already holds one keeps it, and
        # resolve_auth_token's first branch is what honours that.
        #
        # out_dir is deliberately not passed. Reusing whatever token
        # a.manifests already holds looks appealing and is the wrong risk here:
        # it may have been rotated since that bundle was written, and a dead
        # token is exactly the `0/1 Running` this rig cannot tell from a slow
        # boot -- so the rig would fail with no way to say why.
        token_opts = {"ship_id": ship_id}
        if a.auth_token:
            token_opts["auth_token"] = a.auth_token
        source = core.resolve_auth_token(f, token_opts, client=client,
                                         rotate=not a.auth_token,
                                         announce=print)
        print(source.message)
        if source.branch == core.TOKEN_PLACEHOLDER:
            # Reachable one way: --auth-token given the placeholder string
            # itself. Rendering it deploys an agent that can never come online,
            # and the run's only report would be that it never did -- so the
            # message resolve_auth_token already wrote becomes the exit.
            sys.exit(source.message)
        regenerate = _regenerator(f, a, ship_id, token_opts["auth_token"])
    ok = livetest.run(client, a.manifests, a.namespace, f["harbor_id"], ship_id,
                      cluster=a.cluster, timeout=a.timeout, keep=a.keep,
                      facts=f, local_registry=a.local_registry,
                      local_proxy=a.local_proxy, proxy_user=proxy_user,
                      proxy_pass=proxy_pass, regenerate=regenerate, opts=opts,
                      negative_control_check=not a.skip_negative_control,
                      contain_egress=a.contain_egress, run_test=a.run_test,
                      engine_cpu=a.engine_cpu, engine_mem=a.engine_mem)
    sys.exit(0 if ok else 1)


def cmd_mcp(a):
    """Serve the MCP tools on stdio.

    No "starting..." line, and nothing else on stdout either -- see
    mcp_server._answer for what stdout is once this is running.
    """
    try:
        from . import mcp_server
    except ImportError:
        sys.exit("MCP dependencies missing -- pip install 'bzm-opl-gen[mcp]'")
    mcp_server.main()


def cmd_ui(a):
    if a.install_service or a.uninstall_service:
        # Before the server import: installing the agent needs no fastapi, and
        # the point of the service is that *launchd's* python serves -- this
        # process only writes the plist and hands it over.
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

    # Deliberately takes no --api-key and no --facts. It answers the question
    # that comes before both: how much cluster to ask for. Requiring either
    # would put it behind the thing it exists to help get funded.
    pl = sub.add_parser("plan",
                        help="how much infrastructure a load target needs "
                             "(no account, no cluster)")
    # Not required, because it is the performance model's target rather than
    # the only sizing there is -- a GUI Functional customer has no load target.
    # A run with none of the three is still refused, by the planner, naming
    # this field.
    pl.add_argument("--users", metavar="N",
                    help="virtual users the test has to reach")
    # ...and the rest of the models, walked off plan.SIZING_MODELS rather than
    # written out. The flag *is* the model's `target_field`, which is the name
    # the planner's refusals use and the name `sizings_from` reads back out of
    # the namespace, so a fourth model gets its flags by being added to that
    # table -- as it already did for the route and the MCP tool. Performance's
    # two are declared by hand above and below: `--users` is capacity_plan's own
    # argument, and both carry help nothing in the table could supply.
    for fid, m in plan.SIZING_MODELS.items():
        if fid == plan.PERFORMANCE:
            continue
        target_help = (f"{m['unit']} to size for -- the {m['name']} sizing's "
                       f"target, in its own unit")
        if m["baseline"] is None:
            # No measured per-pod figure, so the target is stated rather than
            # sized from. Off `baseline`, because that is what says so.
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
                             f"{gen_mod.ENGINE_DEFAULT_CPU} CPU / "
                             f"{gen_mod.ENGINE_DEFAULT_MEM} engine, scaled "
                             f"from there). An estimate from the account "
                             f"owner, not a measurement")
    pl.add_argument("--vus-per-engine", dest="vus_per_engine",
                    help=f"virtual users one engine carries (BlazeMeter's "
                         f"`threadsPerEngine`). Default is what an engine of "
                         f"the chosen size is rated for -- "
                         f"{api.DEFAULT_THREADS_PER_ENGINE} for the "
                         f"{gen_mod.ENGINE_DEFAULT_CPU} CPU / "
                         f"{gen_mod.ENGINE_DEFAULT_MEM} engine, scaled from "
                         f"there. Your script decides the real number: measure "
                         f"it against one engine and re-run this")
    pl.add_argument("--engine-cpu-limit", dest="engine_cpu_limit",
                    help=f'engine CPU limit (default {gen_mod.ENGINE_DEFAULT_CPU})')
    pl.add_argument("--engine-mem-limit", dest="engine_mem_limit",
                    help=f'engine memory limit (default {gen_mod.ENGINE_DEFAULT_MEM})')
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
    cl.add_argument("--func-ids", nargs="+", default=["performance"])
    # The minimums are read out of core rather than written here: BlazeMeter
    # refuses the create outright below one (#159), and the flag is where
    # somebody reads what to type before typing it. Generated from the table so
    # a second entry reaches the terminal with no edit here.
    cl.add_argument("--slots", type=int, default=1,
                    help="concurrent engines this location's agent may run "
                         "(default 1); "
                         + "; ".join(
                             f"{r['label']} needs at least {r['minimum']}"
                             for r in core.SLOT_MINIMUMS.values()))
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

    # `create-ship` kept as an alias: it is in the README, in docs/live-test.md,
    # and in whatever a customer copied out of them. `ship` is the account's
    # field name (ship_id) and nothing more -- one deployment inside a private
    # location is an agent, which is what this creates.
    cs = sub.add_parser("create-agent", aliases=["create-ship"],
                        help="create an agent, print id + AUTH_TOKEN")
    cs.add_argument("--api-key", required=True)
    cs.add_argument("--harbor-id", required=True)
    cs.add_argument("--name", required=True)
    cs.set_defaults(fn=cmd_create_agent)

    f = sub.add_parser("facts", help="gather account facts -> facts.json")
    f.add_argument("--api-key")
    f.add_argument("--harbor-id", required=True)
    f.add_argument("--manual", action="store_true",
                   help="build facts from --harbor-id + --ship-id without an API "
                        "key, for a location whose account you cannot reach. "
                        "Images come from the built-in catalogue")
    f.add_argument("--ship-id", dest="ship_id", help="required with --manual")
    f.add_argument("--func-ids", dest="func_ids", nargs="+", default=["performance"],
                   help="with --manual: the location's functionalities, which "
                        "decide "
                        "which images the bundle names (default: performance)")
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
                   choices=list(gen_mod.OUTPUT_FORMATS),
                   help="manifests (default): flat YAML to kubectl apply. "
                        "helm: a chart in helm/ with values.yaml filled in from "
                        "the account -- both render the same objects. docker: a "
                        "docker run script for one agent on a host with a docker "
                        "daemon, where most of the options below mean nothing. "
                        "helm and docker cover performance testing only")
    g.add_argument("--platform", choices=["openshift", "k8s"])
    # The posture above is not the product: it installs on vanilla Kubernetes
    # too. Only the negative has a flag, because the default posture is
    # OpenShift's and so is the default cluster.
    g.add_argument("--not-openshift", dest="openshift_cluster",
                   action="store_false", default=None,
                   help="the SCC-friendly posture on a cluster that is not "
                        "OpenShift: every command the bundle prints is written "
                        "with kubectl, sv_ingress=openshift is refused, and no "
                        "inject-trusted-cabundle ConfigMap is offered")
    g.add_argument("--namespace")
    g.add_argument("--ship-id", dest="ship_id")
    g.add_argument("--auth-token", dest="auth_token",
                   help="the agent's AUTH_TOKEN, as create-agent printed it or "
                        "as the BlazeMeter UI shows it on the agent. Wins over "
                        "every other source and issues nothing, so an agent "
                        "already running on it keeps working")
    g.add_argument("--private-registry", dest="private_registry")
    g.add_argument("--pull-secret", dest="pull_secret")
    # Tri-state so profile.json records which of the two a bundle asked for,
    # but both unset and --no-auto-update resolve the same way now: off. See
    # generate.auto_update for why the default departs from BlazeMeter's.
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
                   choices=list(gen_mod.SV_INGRESS_TYPES) + [gen_mod.SV_INGRESS_NONE],
                   help="service virtualization: ingress controller to publish "
                        "virtual services through (required for a mockServices "
                        f"location, or {gen_mod.SV_INGRESS_NONE} to generate such "
                        "a location for performance testing alone)")
    g.add_argument("--sv-subdomain", dest="sv_subdomain", metavar="DOMAIN",
                   help="wildcard domain your ingress controller serves, e.g. apps.example.com")
    g.add_argument("--sv-tls-secret", dest="sv_tls_secret", metavar="NAME",
                   help="wildcard TLS secret in the agent's own namespace, not "
                        "default; required even for HTTP")
    g.add_argument("--sv-istio-gateway", dest="sv_istio_gateway", metavar="NAME",
                   help="istio only, optional: reuse this Gateway instead of one per service")
    # The docker agent's own way of publishing the same thing. The two PEMs are
    # files here and content in the option, exactly as --ca-bundle is: a path on
    # a command line is convenient, a path in the *option* would mean a bundle
    # could not be generated for a host nobody here can see.
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
    t.add_argument("--proxy-auth", metavar="USER:PASS", default="bzm:s3cr3t",
                   help="credentials the local proxy demands ('none' for an open "
                        "proxy); they get URL-encoded into HTTP(S)_PROXY")
    t.set_defaults(fn=cmd_livetest)

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
        # One place turns a refusal into an exit, for the same reason
        # `server._answer` is the only thing that turns one into an
        # HTTPException: a CoreError is already a sentence written for whoever
        # ran the command, and a traceback around it only buries it. Commands
        # that need to print something *before* exiting still catch it
        # themselves -- `create-agent` does, so the agent it just made is
        # reported whatever the token endpoint answers.
        sys.exit(str(e))


if __name__ == "__main__":
    main()
