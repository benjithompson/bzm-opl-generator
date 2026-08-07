import { describe, expect, it } from "vitest";
import {
  ignoredFor, isDocker, keysApply, optionApplies, OUTPUT_FORMATS, whyIgnored,
} from "./formats";
import { IGNORED_BY_FORMAT as IGNORED } from "./fixtures";
import { GROUP_BY_ID, groupsFor, OPTION_GROUPS, SHARED_GROUPS } from "./optionGroups";

// What the page does with the served table -- the half no Python test can see.
// The table itself is the generator's, and `fixtures.IGNORED_BY_FORMAT` is the
// one copy of it; tests/test_server.py holds both the route and that copy equal
// to generate.IGNORED_BY_FORMAT, so nothing here can be right about a stale
// table.

it("offers the three formats the generator has", () => {
  expect(OUTPUT_FORMATS.map((f) => f.id))
    .toEqual(["manifests", "helm", "docker"]);
  // Each says what you get and how you install it: the control is the first
  // thing on the configure step now, so it is where the difference is read.
  for (const f of OUTPUT_FORMATS) expect(f.hint.length).toBeGreaterThan(20);
});

it("hides each platform's service-virtualization options from the other", () => {
  // The table is symmetric (#182): a virtual service is published with
  // disjoint variables per platform, so each set is the other's ignored
  // options and exactly one of the two groups is ever on screen. Everything
  // else docker drops is Kubernetes vocabulary and still reaches a cluster
  // bundle, which is the other half of this.
  const svDockerKeys = ["sv_hostname", "sv_tls_cert", "sv_tls_key"];
  for (const format of ["manifests", "helm"]) {
    expect(isDocker(format)).toBe(false);
    const applies = (k: string) => optionApplies(k, format, IGNORED);
    // Everything docker drops still reaches a cluster bundle, the four
    // KUBERNETES_WEB_EXPOSE_* ones included -- that is what makes them the
    // *other* platform's rather than nobody's.
    for (const key of Object.keys(IGNORED.docker)) expect(applies(key)).toBe(true);
    for (const key of svDockerKeys) expect(applies(key)).toBe(false);
    expect(groupsFor(OPTION_GROUPS, applies).map((g) => g.id))
      .toEqual(OPTION_GROUPS.map((g) => g.id).filter((id) => id !== "svDocker"));
  }
  const docker = (k: string) => optionApplies(k, "docker", IGNORED);
  for (const key of svDockerKeys) expect(docker(key)).toBe(true);
  expect(groupsFor(OPTION_GROUPS, docker).map((g) => g.id)).not.toContain("sv");
});

describe("read, and dropping nothing, is not the same as unread", () => {
  // The distinction the per-format table exists to keep. Both answers show
  // every field, so nothing on screen tells them apart -- which is exactly why
  // the *values* have to, or a page that never reached the server looks like
  // one that read "helm drops nothing".

  it("answers a format's own table, and null where there is none", () => {
    // Read, and the fixture states every format rather than leaving one out,
    // because leaving it out is the other answer. Both are non-empty since
    // #182; what is asserted is that an answer came back at all.
    expect(Object.keys(ignoredFor("helm", IGNORED) ?? {})).not.toEqual([]);
    expect(Object.keys(ignoredFor("docker", IGNORED) ?? {})).not.toEqual([]);
    // Nothing read: the mount state, the fetch that failed, and a format the
    // answer did not carry. All three are "nobody has said", and none of them
    // is a format that drops nothing.
    expect(ignoredFor("helm", {})).toBe(null);
    expect(ignoredFor("docker", {})).toBe(null);
    expect(ignoredFor("kustomize", IGNORED)).toBe(null);
  });

  it("shows every option either way", () => {
    // The safe direction, and the same one for both: hiding a required field
    // on a guess is the mistake worth being wrong about the other way.
    // Unread is the case: with no table at all, every option applies and every
    // group is on screen -- including both service-virtualization groups,
    // which is a field too many rather than a required one hidden on a guess.
    const unread = (k: string) => optionApplies(k, "helm", {});
    expect(groupsFor(OPTION_GROUPS, unread)).toEqual(OPTION_GROUPS);
    for (const table of [IGNORED, {}]) {
      const applies = (k: string) => optionApplies(k, "helm", table);
      expect(applies("namespace")).toBe(true);
      expect(applies("engine_cpu_limit")).toBe(true);
    }
  });

  it("has no sentence to give for a field it is not hiding", () => {
    expect(whyIgnored("namespace", "helm", IGNORED)).toBe(null);
    expect(whyIgnored("namespace", "helm", {})).toBe(null);
    expect(whyIgnored("namespace", "docker", {})).toBe(null);
    // ...and the generator's own sentence where it is.
    expect(whyIgnored("namespace", "docker", IGNORED))
      .toBe(IGNORED.docker.namespace);
  });
});

it("hides what a non-docker format drops, when one does", () => {
  // The case the per-format table exists for: `manifests` and `helm` have empty
  // entries today and #182 fills them, so the reader must already be answering
  // by format rather than by asking whether this is docker. A local table, not
  // the fixture -- the fixture is the generator's answer, and this is the shape
  // of the next one.
  const table = {
    manifests: { sv_hostname_override: "a cluster agent returns DNS-based URLs" },
    helm: {},
    docker: IGNORED.docker,
  };
  expect(optionApplies("sv_hostname_override", "manifests", table)).toBe(false);
  expect(whyIgnored("sv_hostname_override", "manifests", table))
    .toBe("a cluster agent returns DNS-based URLs");
  // ...and it is this format's table that is read, not any other's: helm has an
  // entry of its own and keeps the field, and docker's keys stay docker's.
  expect(optionApplies("sv_hostname_override", "helm", table)).toBe(true);
  expect(optionApplies("namespace", "manifests", table)).toBe(true);
  expect(optionApplies("namespace", "docker", table)).toBe(false);
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
    // Scheduling is the only shared group a docker bundle loses whole. The
    // environment area survives too and is not in this list: it stopped being
    // a group when it stopped being a switch, and the panel asks `keysApply`
    // over extra_env for it -- which is in the ignored table nowhere, because
    // there it is `--env` flags.
    expect(kept.map((g) => g.id))
      .toEqual(["registry", "proxy", "ca", "security"]);
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
