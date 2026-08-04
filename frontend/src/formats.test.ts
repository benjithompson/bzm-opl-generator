import { describe, expect, it } from "vitest";
import { isDocker, keysApply, optionApplies, OUTPUT_FORMATS } from "./formats";
import { DOCKER_IGNORED as IGNORED } from "./fixtures";
import { GROUP_BY_ID, groupsFor, OPTION_GROUPS, SHARED_GROUPS } from "./optionGroups";

// What the page does with the served table -- the half no Python test can see.
// The table itself is the generator's, and `fixtures.DOCKER_IGNORED` is the one
// copy of it; tests/test_server.py holds both the route and that copy equal to
// generate.DOCKER_IGNORED, so nothing here can be right about a stale table.

it("offers the three formats the generator has", () => {
  expect(OUTPUT_FORMATS.map((f) => f.id))
    .toEqual(["manifests", "helm", "docker"]);
  // Each says what you get and how you install it: the control is the first
  // thing on the configure step now, so it is where the difference is read.
  for (const f of OUTPUT_FORMATS) expect(f.hint.length).toBeGreaterThan(20);
});

it("hides nothing from the two cluster formats", () => {
  for (const format of ["manifests", "helm"]) {
    expect(isDocker(format)).toBe(false);
    const applies = (k: string) => optionApplies(k, format, IGNORED);
    for (const key of Object.keys(IGNORED)) expect(applies(key)).toBe(true);
    expect(groupsFor(OPTION_GROUPS, applies)).toEqual(OPTION_GROUPS);
  }
});

describe("docker", () => {
  const applies = (k: string) => optionApplies(k, "docker", IGNORED);

  it("drops the Kubernetes vocabulary and keeps the rest", () => {
    expect(applies("namespace")).toBe(false);
    expect(applies("service_account_name")).toBe(false);
    expect(applies("node_selector")).toBe(false);
    // ...and what a container genuinely has. use_secret decides whether the
    // token goes in an --env-file rather than on the command line, and
    // auto_update is AUTO_UPDATE here -- both real, both stay.
    expect(applies("use_secret")).toBe(true);
    expect(applies("auto_update")).toBe(true);
    expect(applies("private_registry")).toBe(true);
    expect(applies("proxy")).toBe(true);
    expect(applies("ca_bundle")).toBe(true);
  });

  it("takes a group off screen only when none of it applies", () => {
    // Every key ignored: the row would be a switch over an empty body.
    expect(groupsFor([GROUP_BY_ID.sched], applies)).toEqual([]);
    // Some ignored: the group stays and its own body hides the rest. Losing
    // Private registry with docker would take the mirror script's own setting
    // with it, and a docker agent is exactly where an air-gapped host needs it.
    const kept = [GROUP_BY_ID.registry, GROUP_BY_ID.security,
                  GROUP_BY_ID.ca, GROUP_BY_ID.proxy];
    expect(groupsFor(kept, applies)).toEqual(kept);
  });

  it("keeps a section whose fields are not all gone", () => {
    // What the placement card and Advanced hide by -- neither is a declared
    // group, and both own more than one key. Whole means whole: the card goes
    // only when nothing in it reaches anything.
    expect(keysApply(["namespace", "service_account_name"], applies)).toBe(false);
    expect(keysApply(["platform", "run_as_user"], applies)).toBe(false);
    // ...and one surviving key is enough, which is what stops Security & RBAC
    // disappearing over the three fields of it a container has no use for.
    expect(keysApply(["cluster_rbac", "use_secret"], applies)).toBe(true);
  });

  it("leaves the shared rows in their declared order", () => {
    const kept = groupsFor(SHARED_GROUPS, applies);
    expect(kept.map((g) => g.id)).toEqual(["registry", "proxy", "ca", "security"]);
  });

  it("shows everything while the table has not been read", () => {
    // Empty is "could not read", not "nothing is ignored" -- and the two want
    // opposite renderings only where guessing wrong is cheap. Here it is not:
    // hiding the namespace field on a Kubernetes bundle because a fetch had not
    // landed would be a required field missing from a form nobody could fix.
    const unread = (k: string) => optionApplies(k, "docker", {});
    expect(unread("namespace")).toBe(true);
    expect(groupsFor(OPTION_GROUPS, unread)).toEqual(OPTION_GROUPS);
  });
});
