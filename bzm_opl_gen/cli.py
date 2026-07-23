"""bzm-opl-gen: generate + live-test BlazeMeter OPL k8s/OpenShift deployments
from a customer's actual BlazeMeter account.

Subcommands:
  facts     query the account, write facts.json (harbor, ships, images, features)
  generate  render manifests from facts + customer parameters
  images    list / pull / mirror the images the location actually needs
  livetest  apply manifests to a cluster and verify the agent comes online
"""

import argparse
import json
import subprocess
import sys

from . import api, facts as facts_mod, generate as gen_mod, livetest


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
        _docker(["pull", ref], a.dry_run)
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
                      cluster=a.cluster, timeout=a.timeout, keep=a.keep)
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(prog="bzm-opl-gen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("facts", help="gather account facts -> facts.json")
    f.add_argument("--api-key", required=True)
    f.add_argument("--harbor-id", required=True)
    f.add_argument("-o", "--output", default="facts.json")
    f.set_defaults(fn=cmd_facts)

    g = sub.add_parser("generate", help="render manifests from facts")
    g.add_argument("--facts", default="facts.json")
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
    i.add_argument("--dry-run", action="store_true")
    i.set_defaults(fn=cmd_images)

    t = sub.add_parser("livetest", help="deploy to a cluster, verify agent online")
    t.add_argument("--api-key", required=True)
    t.add_argument("--facts", default="facts.json")
    t.add_argument("--manifests", default="out")
    t.add_argument("--namespace", required=True)
    t.add_argument("--ship-id", dest="ship_id")
    t.add_argument("--cluster", choices=["current", "kind"], default="current")
    t.add_argument("--timeout", type=int, default=600)
    t.add_argument("--keep", action="store_true", help="skip teardown")
    t.set_defaults(fn=cmd_livetest)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
