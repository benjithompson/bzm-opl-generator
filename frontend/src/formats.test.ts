import { describe, expect, it } from "vitest";
import {
  ignoredFor, isDocker, keysApply, optionApplies, OUTPUT_FORMATS, whyIgnored,
} from "./formats";
import { IGNORED_BY_FORMAT as IGNORED } from "./fixtures";
import { GROUP_BY_ID, groupsFor, OPTION_GROUPS, SHARED_GROUPS } from "./optionGroups";

// What the page does with the served table. The table is the generator's;
// fixtures.IGNORED_BY_FORMAT is its one copy, held equal by test_server.py.

it("offers the three formats the generator has", () => {
  expect(OUTPUT_FORMATS.map((f) => f.id))
    .toEqual(["manifests", "helm", "docker"]);
  // Each says what you get and how you install it.
  for (const f of OUTPUT_FORMATS) expect(f.hint.length).toBeGreaterThan(20);
});

it("hides each platform's service-virtualization options from the other", () => {
  // Symmetric for service virtualization: each platform's SV options are the
  // other's ignored ones.
  const svDockerKeys = ["sv_hostname", "sv_tls_cert", "sv_tls_key"];
  for (const format of ["manifests", "helm"]) {
    expect(isDocker(format)).toBe(false);
    const applies = (k: string) => optionApplies(k, format, IGNORED);
    // Everything docker drops still reaches a cluster bundle.
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
  // Both answers show every field, so only the values tell them apart.

  it("answers a format's own table, and null where there is none", () => {
    // Read: an answer came back.
    expect(Object.keys(ignoredFor("helm", IGNORED) ?? {})).not.toEqual([]);
    expect(Object.keys(ignoredFor("docker", IGNORED) ?? {})).not.toEqual([]);
    // Nothing read, for whatever reason, is null, never "drops nothing".
    expect(ignoredFor("helm", {})).toBe(null);
    expect(ignoredFor("docker", {})).toBe(null);
    expect(ignoredFor("kustomize", IGNORED)).toBe(null);
  });

  it("shows every option either way", () => {
    // Unread shows every field, both SV groups included.
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
  // Answered by format, not by asking whether it is docker. A local table,
  // shaped like a future one.
  const table = {
    manifests: { sv_hostname_override: "a cluster agent returns DNS-based URLs" },
    helm: {},
    docker: IGNORED.docker,
  };
  expect(optionApplies("sv_hostname_override", "manifests", table)).toBe(false);
  expect(whyIgnored("sv_hostname_override", "manifests", table))
    .toBe("a cluster agent returns DNS-based URLs");
  // ...and only this format's table is read.
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
    // ...and what a container does have: the credential choice and AUTO_UPDATE.
    expect(applies("use_secret")).toBe(true);
    expect(applies("auto_update")).toBe(true);
    expect(applies("private_registry")).toBe(true);
    expect(applies("proxy")).toBe(true);
    expect(applies("ca_bundle")).toBe(true);
  });

  it("takes a group off screen only when none of it applies", () => {
    // Every key ignored: the row would be a switch over an empty body.
    expect(groupsFor([GROUP_BY_ID.sched], applies)).toEqual([]);
    // Some ignored: the group stays and its body hides the rest.
    const kept = [GROUP_BY_ID.registry, GROUP_BY_ID.security,
                  GROUP_BY_ID.ca, GROUP_BY_ID.proxy];
    expect(groupsFor(kept, applies)).toEqual(kept);
  });

  it("keeps a section whose fields are not all gone", () => {
    // A section that is not a group goes only when none of its keys applies.
    expect(keysApply(["namespace", "service_account_name"], applies)).toBe(false);
    expect(keysApply(["platform", "run_as_user"], applies)).toBe(false);
    // ...one surviving key keeps it.
    expect(keysApply(["cluster_rbac", "use_secret"], applies)).toBe(true);
  });

  it("leaves the shared rows in their declared order", () => {
    const kept = groupsFor(SHARED_GROUPS, applies);
    // Scheduling is the only shared group docker loses whole.
    expect(kept.map((g) => g.id))
      .toEqual(["registry", "proxy", "ca", "security"]);
  });

  it("shows everything while the table has not been read", () => {
    // Empty is "could not read": every field shows.
    const unread = (k: string) => optionApplies(k, "docker", {});
    expect(unread("namespace")).toBe(true);
    expect(groupsFor(OPTION_GROUPS, unread)).toEqual(OPTION_GROUPS);
  });
});
