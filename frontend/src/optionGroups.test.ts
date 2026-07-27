import { describe, expect, it } from "vitest";
import { Options } from "./api";
import {
  allGroupsOff, detectGroups, ENGINE_SIZES, GROUP_BY_ID, GroupId, OPTION_GROUPS,
} from "./optionGroups";

// The lifecycle of a group is data in, data out -- a detect over the options, a
// patch to merge on enable, a patch to merge on disable -- so it is tested as
// such. Nothing here renders a component: what used to break was a wipe list
// drifting from the fields above it, and no amount of DOM would have shown that.

/** Every key that, set on its own, must open its group. Written out rather than
 *  read off `keys`, because some keys a group owns are deliberately not part of
 *  its detection rule (WRITE_ONLY below) -- a table derived from the
 *  declarations could not tell the two apart, which is the drift being caught.
 *  The values are the ones that trigger detection: `use_secret` is a departure
 *  from the default only when false, `service_type` only when not CLUSTERIP. */
const DETECTS: [GroupId, string, unknown][] = [
  ["registry", "private_registry", "registry.corp.com/bzm"],
  ["registry", "pull_secret", "bzm-pull"],
  ["registry", "registry_auth", true],
  ["proxy", "proxy", { http: "http://proxy:3128" }],
  ["ca", "ca_existing_configmap", "corp-trust-bundle"],
  ["ca", "ca_bundle", "-----BEGIN CERTIFICATE-----"],
  ["ca", "ca_openshift_inject", true],
  ["sched", "tolerations", [{ key: "lifecycle" }]],
  ["sched", "node_selector", { pool: "loadtest" }],
  ["sizing", "engine_cpu_limit", "2"],
  ["sizing", "engine_mem_limit", "8Gi"],
  ["sizing", "emit_limitrange", true],
  ["security", "use_secret", false],
  ["security", "cluster_rbac", true],
  ["security", "service_type", "NODEPORT"],
  ["sv", "sv_ingress", "nginx"],
];

/** Owned and written, but never a reason to open the group on its own. Listed
 *  so that every declared key is accounted for by one table or the other. */
const WRITE_ONLY: [GroupId, string][] = [
  // Only meaningful beside ca_existing_configmap, which does the detecting.
  ["ca", "ca_configmap_key"],
  // Written by two groups; only Security's CLUSTERIP departure opens anything.
  ["sv", "service_type"],
  // An imported profile carrying these without an ingress is not an SV config;
  // the ingress is what the group is.
  ["sv", "sv_subdomain"],
  ["sv", "sv_tls_secret"],
  ["sv", "sv_istio_gateway"],
];

/** Every group's keys set to something the group would have to clear, plus two
 *  keys no group owns -- the fixture a disable must not overreach into. */
const FULL: Options = {
  namespace: "blazemeter",
  platform: "openshift",
  private_registry: "registry.corp.com/bzm",
  pull_secret: "bzm-pull",
  registry_auth: true,
  proxy: { http: "http://proxy:3128" },
  ca_existing_configmap: "corp-trust-bundle",
  ca_configmap_key: "ca-bundle.crt",
  ca_bundle: "-----BEGIN CERTIFICATE-----",
  ca_openshift_inject: true,
  tolerations: [{ key: "lifecycle" }],
  node_selector: { pool: "loadtest" },
  engine_cpu_limit: "4",
  engine_mem_limit: "16Gi",
  emit_limitrange: true,
  use_secret: false,
  cluster_rbac: true,
  service_type: "NODEPORT",
  sv_ingress: "istio",
  sv_subdomain: "apps.example.com",
  sv_tls_secret: "wildcard-credential",
  sv_istio_gateway: "bzm-gateway",
};

const only = (key: string, value: unknown): Options => ({ [key]: value });

describe("the declarations", () => {
  it("account for every owned key in exactly one detection table", () => {
    for (const g of OPTION_GROUPS) {
      const named = [
        ...DETECTS.filter(([id]) => id === g.id).map(([, k]) => k),
        ...WRITE_ONLY.filter(([id]) => id === g.id).map(([, k]) => k),
      ];
      expect([...named].sort()).toEqual([...g.keys].sort());
    }
  });

  it("show the two groups that both write service_type", () => {
    const owners = OPTION_GROUPS.filter((g) => g.keys.includes("service_type"));
    expect(owners.map((g) => g.id)).toEqual(["security", "sv"]);
  });
});

describe("detection", () => {
  it("opens nothing for an empty config", () => {
    const on = detectGroups({}, allGroupsOff());
    expect(Object.values(on).some(Boolean)).toBe(false);
  });

  it.each(DETECTS)("opens %s from %s alone", (id, key, value) => {
    const on = detectGroups(only(key, value), allGroupsOff());
    expect(on[id]).toBe(true);
    // …and nothing else: an imported profile must not light up a group whose
    // fields are all still at their defaults.
    const others = OPTION_GROUPS.filter((g) => g.id !== id).map((g) => on[g.id]);
    expect(others.some(Boolean)).toBe(false);
  });

  it.each(WRITE_ONLY)("does not open %s from %s alone", (id, key) => {
    expect(detectGroups(only(key, FULL[key]), allGroupsOff())[id]).toBe(false);
  });

  it("never closes a group the user opened by hand", () => {
    const on = detectGroups({}, { ...allGroupsOff(), proxy: true });
    expect(on.proxy).toBe(true);
  });

  it("opens a group its config does not mention when it is required", () => {
    expect(detectGroups({}, allGroupsOff(), { sv: true }).sv).toBe(true);
  });
});

describe("switching a group off", () => {
  it.each(OPTION_GROUPS.map((g) => [g.id] as const))(
    "%s touches only the keys it declares", (id) => {
      const patch = GROUP_BY_ID[id].disable(FULL);
      expect(Object.keys(patch).sort())
        .toEqual(Object.keys(patch).filter((k) => GROUP_BY_ID[id].keys.includes(k)).sort());
      const after = { ...FULL, ...patch };
      for (const k of Object.keys(FULL)) {
        if (!GROUP_BY_ID[id].keys.includes(k)) expect(after[k]).toEqual(FULL[k]);
      }
    });

  // The exact wipes, spelled out: "clears what it used to clear" is the whole
  // acceptance test of this refactor, and a generic assertion cannot state it.
  it("clears exactly what it cleared before", () => {
    const wipes: Record<GroupId, Options> = {
      registry: { private_registry: null, pull_secret: null, registry_auth: false },
      proxy: { proxy: null },
      ca: {
        ca_existing_configmap: null, ca_configmap_key: null,
        ca_bundle: null, ca_openshift_inject: false,
      },
      sched: { tolerations: null, node_selector: null },
      sizing: { engine_cpu_limit: null, engine_mem_limit: null, emit_limitrange: false },
      security: { use_secret: true, cluster_rbac: false, service_type: "CLUSTERIP" },
      sv: {
        sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
        sv_istio_gateway: null,
      },
    };
    for (const g of OPTION_GROUPS) expect(g.disable(FULL)).toEqual(wipes[g.id]);
  });

  it("leaves service_type alone when service virtualization goes off", () => {
    // Deliberate asymmetry, preserved from the original switch: enabling SV
    // forces CLUSTERIP, disabling it does not put NODEPORT back.
    expect(GROUP_BY_ID.sv.disable(FULL)).not.toHaveProperty("service_type");
  });

  it("re-detects nothing from what it left behind", () => {
    // The point of the wipe: an off group must not be dragged open again by the
    // effect that watches the options.
    for (const g of OPTION_GROUPS) {
      const after = { ...FULL, ...g.disable(FULL) };
      expect(detectGroups(after, allGroupsOff())[g.id]).toBe(false);
    }
  });
});

describe("switching a group on", () => {
  it("changes nothing for the groups that only reveal fields", () => {
    for (const id of ["registry", "proxy", "sched", "security"] as GroupId[]) {
      expect(GROUP_BY_ID[id].enable(FULL)).toEqual({});
      expect(GROUP_BY_ID[id].enable({})).toEqual({});
    }
  });

  it("seeds the standard engine size when no size is recognised", () => {
    const standard = ENGINE_SIZES.find((s) => s.id === "standard")!;
    expect(GROUP_BY_ID.sizing.enable({}))
      .toEqual({ engine_cpu_limit: standard.cpu, engine_mem_limit: standard.mem });
  });

  it("keeps a size that is already one of the presets", () => {
    const small = ENGINE_SIZES.find((s) => s.id === "small")!;
    expect(GROUP_BY_ID.sizing.enable(
      { engine_cpu_limit: small.cpu, engine_mem_limit: small.mem })).toEqual({});
  });

  it("seeds an ingress and forces CLUSTERIP for service virtualization", () => {
    // NODEPORT would send crane at the cluster-scoped Node object, so the
    // service type is forced rather than merely defaulted.
    expect(GROUP_BY_ID.sv.enable({ service_type: "NODEPORT" }))
      .toEqual({ sv_ingress: "nginx", service_type: "CLUSTERIP" });
  });

  it("keeps an ingress that was already chosen", () => {
    expect(GROUP_BY_ID.sv.enable({ sv_ingress: "contour" }))
      .toEqual({ sv_ingress: "contour", service_type: "CLUSTERIP" });
  });

  it("starts CA trust on the existing-ConfigMap mode", () => {
    const patch = GROUP_BY_ID.ca.enable({});
    expect(detectGroups({ ...patch }, allGroupsOff()).ca).toBe(true);
    expect(patch).toEqual({
      ca_existing_configmap: "", ca_configmap_key: undefined,
      ca_bundle: null, ca_openshift_inject: false,
    });
  });

  it("keeps a CA ConfigMap that is already named", () => {
    expect(GROUP_BY_ID.ca.enable({ ca_existing_configmap: "corp", ca_configmap_key: "k" }))
      .toEqual({
        ca_existing_configmap: "corp", ca_configmap_key: "k",
        ca_bundle: null, ca_openshift_inject: false,
      });
  });
});
