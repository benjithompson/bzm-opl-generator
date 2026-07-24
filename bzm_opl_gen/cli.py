"""bzm-opl-gen: generate + live-test BlazeMeter OPL k8s/OpenShift deployments
from a customer's actual BlazeMeter account.

Subcommands:
  locations   list private locations (harbors) across the account
  create-ship create an agent (ship) in a location, print id + AUTH_TOKEN
  facts       query the account, write facts.json (harbor, ships, images, features)
  generate    render manifests from facts + customer parameters
  images      list / pull / mirror the images the location actually needs
  livetest    apply manifests to a cluster and verify the agent comes online
"""

import argparse
import json
import subprocess
import sys

from . import api, facts as facts_mod, generate as gen_mod, livetest


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
                                       func_ids=a.func_ids, slots=a.slots)
    print(f"created location '{h.get('name')}' harbor_id={h['id']} "
          f"(account {account_id}, workspace {wsid}, funcIds={a.func_ids}, slots={a.slots})")
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
    client = api.BzmClient(a.api_key)
    f = facts_mod.gather(client, a.harbor_id)
    facts_mod.save(f, a.output)
    print(f"wrote {a.output}: location '{f['harbor_name']}' funcIds={f['func_ids']} "
          f"ships={len(f['ships'])} images={len(f['images'])} ({f['images_source']})")


def cmd_generate(a):
    f = facts_mod.load(a.facts)
    opts = {}
    if a.profile:
        with open(a.profile) as fh:
            opts.update(json.load(fh))
    for key in ("platform", "namespace", "ship_id", "auth_token",
                "private_registry", "pull_secret", "service_type"):
        v = getattr(a, key, None)
        if v is not None:
            opts[key] = v
    if a.no_secret:
        opts["use_secret"] = False
    if a.cluster_rbac:
        opts["cluster_rbac"] = True
    if a.gui:
        opts["gui"] = True
    if a.api_key and not opts.get("auth_token"):
        ship_id = opts.get("ship_id") or (f["ships"][0]["id"] if len(f["ships"]) == 1 else None)
        if ship_id:
            client = api.BzmClient(a.api_key)
            opts["auth_token"] = client.auth_token(f["harbor_id"], ship_id)
            print(f"fetched AUTH_TOKEN for ship {ship_id} from BlazeMeter API")
    files = gen_mod.generate(f, opts)
    written = gen_mod.write(files, a.output)
    print(f"wrote {len(written)} files to {a.output}/: " + ", ".join(written))


def cmd_images(a):
    f = facts_mod.load(a.facts) if a.facts else None
    if f is None:
        client = api.BzmClient(a.api_key)
        f = facts_mod.gather(client, a.harbor_id)
    imgs = [f["crane_image"]] + [
        f"{i['repo']}:{i['tag']}" for i in f["images"]
        if i.get("key") and (a.all or i.get("performance", True))
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


def cmd_livetest(a):
    f = facts_mod.load(a.facts)
    client = api.BzmClient(a.api_key)
    ship_id = a.ship_id or (f["ships"][0]["id"] if len(f["ships"]) == 1 else None)
    if not ship_id:
        sys.exit(f"--ship-id required (location has {len(f['ships'])} ships)")
    ok = livetest.run(client, a.manifests, a.namespace, f["harbor_id"], ship_id,
                      cluster=a.cluster, timeout=a.timeout, keep=a.keep,
                      facts=f, local_registry=a.local_registry)
    sys.exit(0 if ok else 1)


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
    cl.add_argument("--slots", type=int, default=1)
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
    f.add_argument("--api-key", required=True)
    f.add_argument("--harbor-id", required=True)
    f.add_argument("-o", "--output", default="facts.json")
    f.set_defaults(fn=cmd_facts)

    g = sub.add_parser("generate", help="render manifests from facts")
    g.add_argument("--facts", default="facts.json")
    g.add_argument("--api-key", help="fetch AUTH_TOKEN from the API if not given")
    g.add_argument("--profile", help="JSON options file (see profiles/)")
    g.add_argument("--platform", choices=["openshift", "k8s"])
    g.add_argument("--namespace")
    g.add_argument("--ship-id", dest="ship_id")
    g.add_argument("--auth-token", dest="auth_token")
    g.add_argument("--private-registry", dest="private_registry")
    g.add_argument("--pull-secret", dest="pull_secret")
    g.add_argument("--service-type", dest="service_type", choices=["CLUSTERIP", "NODEPORT"])
    g.add_argument("--no-secret", action="store_true", help="AUTH_TOKEN in ConfigMap")
    g.add_argument("--cluster-rbac", action="store_true", help="include optional ClusterRole")
    g.add_argument("--gui", action="store_true", help="include GUI-functional images")
    g.add_argument("-o", "--output", default="out")
    g.set_defaults(fn=cmd_generate)

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
    t.set_defaults(fn=cmd_livetest)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
