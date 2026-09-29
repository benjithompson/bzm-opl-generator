// Service virtualization as one record, tested as data. `patch` is the write
// the page applies, so its settling is checked here directly.
import { describe, expect, it } from "vitest";
import { Options, SvConstants } from "./api";
import { IGNORED_BY_FORMAT } from "./fixtures";
import { optionApplies } from "./formats";
import { SV_NONE, toggleDeclared } from "./optionGroups";
import { exclusiveWith, svMixedWithEngines, svState } from "./sv";

// The funcId meaning "runs mockServices" is served, not spelled out here.
const CONST: SvConstants = {
  func_ids: ["mock-services"],
  ingress_types: ["nginx", "istio", "contour", "openshift"],
  // Shape-only names: the real table is pinned in test_server.py.
  backends: {
    nginx: { group: "networking.k8s.io", resources: ["ingresses"],
             creates: "Ingress", nodeport_ok: true },
    contour: { group: "projectcontour.io", resources: ["httpproxies"],
               creates: "HTTPProxy", nodeport_ok: false },
    istio: { group: "networking.istio.io", resources: ["gateways"],
             creates: "Gateway + VirtualService", nodeport_ok: false },
    openshift: { group: "route.openshift.io", resources: ["routes"],
                 creates: "Route", nodeport_ok: true },
  },
};

const SV_LOC = ["performance", "mock-services"];
const PERF_LOC = ["performance"];

/** A complete SV configuration, for the tests that vary one thing about it. */
const CONFIGURED: Options = {
  sv_ingress: "nginx", sv_subdomain: "apps.example.com",
  sv_tls_secret: "wildcard-credential",
};

/** The page's `applies` for the format these options name. */
const appliesIn = (o: Options) => (k: string) =>
  optionApplies(k, String(o.output_format ?? "manifests"), IGNORED_BY_FORMAT);

const sv = (funcIds: string[] | undefined, o: Options = {}, runs = true) =>
  svState(funcIds, o, CONST, runs, appliesIn(o));

// -- what the location asks for ----------------------------------------------

describe("whether the location demands service virtualization", () => {
  it("is not an SV location when none of its funcIds are served ones", () => {
    const s = sv(PERF_LOC);
    expect(s.location).toBe(false);
    expect(s.required).toBe(false);
    expect(s.declined).toBe(false);
  });

  it("is required by the funcIds, not by anything configured", () => {
    const s = sv(SV_LOC);
    expect(s.location).toBe(true);
    expect(s.required).toBe(true);
  });

  it("is not required once the demand has been answered no", () => {
    // Wanted for performance alone: the demand is answered.
    const s = sv(SV_LOC, { sv_ingress: SV_NONE });
    expect(s.location).toBe(true);
    expect(s.declined).toBe(true);
    expect(s.required).toBe(false);
  });

  it("does not read a decline on a location that never asked as declining", () => {
    // A performance location has nothing to decline.
    expect(sv(PERF_LOC, { sv_ingress: SV_NONE }).groupDeclined.sv).toBe(false);
    expect(sv(SV_LOC, { sv_ingress: SV_NONE }).groupDeclined.sv).toBe(true);
  });

  it("says nothing at all before the location is known", () => {
    const s = sv(undefined);
    expect(s.location).toBe(false);
    expect(s.required).toBe(false);
  });

  it("hands the group table what the options cannot say", () => {
    expect(sv(SV_LOC).groupRequired.sv).toBe(true);
    expect(sv(PERF_LOC).groupRequired.sv).toBe(false);
  });

  it("is configured only by a real backend", () => {
    expect(sv(SV_LOC, { sv_ingress: "nginx" }).configured).toBe(true);
    expect(sv(SV_LOC, { sv_ingress: SV_NONE }).configured).toBe(false);
    expect(sv(SV_LOC).configured).toBe(false);
  });
});

// -- is it finished? ---------------------------------------------------------

describe("whether the configuration is finished", () => {
  it("is finished when nothing asks for it", () => {
    expect(sv(PERF_LOC).ok).toBe(true);
  });

  it("is unfinished on an SV location with nothing set", () => {
    // ...as the options stand; `patch` seeds a backend.
    expect(sv(SV_LOC).ok).toBe(false);
  });

  it("needs the domain and the TLS secret once a backend is chosen", () => {
    expect(sv(PERF_LOC, { sv_ingress: "nginx" }).ok).toBe(false);
    expect(sv(PERF_LOC, { sv_ingress: "nginx", sv_subdomain: "a.b" }).ok)
      .toBe(false);
    expect(sv(PERF_LOC, CONFIGURED).ok).toBe(true);
  });

  it("is finished once the demand is declined", () => {
    expect(sv(SV_LOC, { sv_ingress: SV_NONE }).ok).toBe(true);
  });

  it("blocks a service type the chosen backend cannot publish over", () => {
    const bad = sv(SV_LOC, { ...CONFIGURED, sv_ingress: "contour",
                             service_type: "NODEPORT" });
    expect(bad.ok).toBe(false);
    expect(bad.nodePortConflict).toBe(true);
    const good = sv(SV_LOC, { ...CONFIGURED, service_type: "NODEPORT" });
    expect(good.ok).toBe(true);
    expect(good.nodePortConflict).toBe(false);
  });

  it("does not call an empty field a service-type conflict", () => {
    // Computed on its own, so an empty domain does not show the nodePort sentence.
    expect(sv(SV_LOC, { sv_ingress: "contour" }).nodePortConflict).toBe(false);
  });
});

// -- every format publishes a virtual service ---------------------------------

describe("the formats a virtual service may be generated as", () => {
  it("takes no format away from an SV location", () => {
    // No format refuses a virtual service, and the correction never moves one.
    for (const fmt of ["manifests", "helm", "docker"]) {
      expect(sv(SV_LOC, { ...CONFIGURED, output_format: fmt }).patch?.output_format)
        .toBeUndefined();
    }
  });

  it("leaves a configuration nobody demanded on the format it arrived with", () => {
    // A full SV configuration on a location running no served functionality
    // needs no format correction.
    const s = sv(PERF_LOC, { ...CONFIGURED, output_format: "helm" });
    expect(s.required).toBe(false);
    expect(s.patch).toBeNull();
  });
});

// -- what the panels render against ------------------------------------------

describe("the prerequisite context", () => {
  it("substitutes what is filled in and names its own placeholders", () => {
    const empty = sv(SV_LOC).ctx;
    expect(empty).toEqual({ ns: "<namespace>", dom: "<domain>",
                            secret: "<tls-secret>", gateway: "" });
    const full = sv(SV_LOC, { namespace: "bzm", sv_subdomain: " apps.x.com ",
                              sv_tls_secret: "wild",
                              sv_istio_gateway: "bzm-gw" }).ctx;
    expect(full).toEqual({ ns: "bzm", dom: "apps.x.com", secret: "wild",
                           gateway: "bzm-gw" });
  });

  it("keeps the fields as typed for the inputs themselves", () => {
    // Untrimmed for the controlled inputs.
    expect(sv(SV_LOC, { sv_subdomain: "apps.x.com " }).fields.subdomain)
      .toBe("apps.x.com ");
    expect(sv(SV_LOC, {}).fields).toEqual(
      { subdomain: "", tlsSecret: "", gateway: "" });
  });

  it("takes the Role prose off the served table, keyed by the backend", () => {
    expect(sv(SV_LOC, { sv_ingress: " nginx " }).rbac).toBe(CONST.backends.nginx);
    expect(sv(SV_LOC, { sv_ingress: "made-up" }).rbac).toBeUndefined();
    expect(sv(SV_LOC).rbac).toBeUndefined();
  });

  it("probes the scheme the TLS secret decides", () => {
    expect(sv(SV_LOC, CONFIGURED).scheme).toBe("https");
    expect(sv(SV_LOC, { sv_ingress: "nginx" }).scheme).toBe("http");
  });

  it("offers a Route backend only on OpenShift", () => {
    // No OpenShift Route off OpenShift: generate() refuses it.
    expect(sv(SV_LOC, { platform: "k8s" }).ingressTypes)
      .toEqual(["nginx", "istio", "contour"]);
    // `openshift_cluster` stated: it defaults to false.
    expect(sv(SV_LOC, { platform: "openshift", openshift_cluster: true })
      .ingressTypes).toEqual(CONST.ingress_types);
    // The SCC-friendly posture alone does not make a cluster OpenShift.
    expect(sv(SV_LOC, { platform: "openshift", openshift_cluster: false })
      .ingressTypes).toEqual(["nginx", "istio", "contour"]);
  });
});

// -- the correction ------------------------------------------------------------
// Applying `patch` must settle.

describe("the option patch", () => {
  /** Apply the patch until there is none, or give up. */
  const settle = (funcIds: string[] | undefined, o: Options, runs = true) => {
    let cur = o;
    for (let i = 0; i < 5; i += 1) {
      const { patch } = svState(funcIds, cur, CONST, runs, appliesIn(cur));
      if (!patch) return cur;
      cur = { ...cur, ...patch };
    }
    throw new Error("the patch never settled");
  };

  it("is null when there is nothing to correct", () => {
    expect(sv(PERF_LOC).patch).toBeNull();
    expect(sv(SV_LOC, CONFIGURED).patch).toBeNull();
    expect(sv(SV_LOC, { sv_ingress: SV_NONE }).patch).toBeNull();
  });

  it("seeds a backend for a location that demands one", () => {
    // Imports and required rows never call enable(), so the ingress is seeded.
    expect(sv(SV_LOC).patch).toEqual({ sv_ingress: "nginx" });
    expect(settle(SV_LOC, {}).sv_ingress).toBe("nginx");
  });

  it("seeds nothing for a bundle that no longer carries the functionality", () => {
    // When the bundle no longer carries SV (notRunPatch is clearing it), no
    // re-seed, or the two writes would loop. In manual entry the facts trail the
    // declaration, so this is the normal case.
    expect(sv(SV_LOC, {}, false).patch).toBeNull();
    expect(sv(SV_LOC, {}, false).required).toBe(false);
    expect(sv(SV_LOC, {}, false).groupRequired.sv).toBe(false);
  });

  it("rescues a profile stranded on the OpenShift backend", () => {
    // An openshift ingress off OpenShift falls back to nginx.
    expect(sv(PERF_LOC, { sv_ingress: "openshift", platform: "k8s" }).patch)
      .toEqual({ sv_ingress: "nginx" });
    expect(sv(SV_LOC, { sv_ingress: "openshift", platform: "openshift",
                        openshift_cluster: true }).patch)
      .toBeNull();
    // Likewise when the cluster toggle says not OpenShift.
    expect(sv(PERF_LOC, { sv_ingress: "openshift", platform: "openshift",
                          openshift_cluster: false }).patch)
      .toEqual({ sv_ingress: "nginx" });
  });

  it("drops a gateway no backend will read", () => {
    // A gateway name is dropped for any backend but istio.
    expect(sv(SV_LOC, { ...CONFIGURED, sv_istio_gateway: "gw" }).patch)
      .toEqual({ sv_istio_gateway: null });
    expect(sv(SV_LOC, { ...CONFIGURED, sv_ingress: "istio",
                        sv_istio_gateway: "gw" }).patch).toBeNull();
  });

  it("clears the gateway the seeded backend cannot read either", () => {
    // Both in one pass, the gateway judged against the seeded backend.
    expect(sv(SV_LOC, { sv_istio_gateway: "gw" }).patch)
      .toEqual({ sv_ingress: "nginx", sv_istio_gateway: null });
  });

  it("never moves the format the bundle was asked for", () => {
    // The format is never changed.
    expect(sv(SV_LOC, { ...CONFIGURED, output_format: "helm" }).patch).toBeNull();
    expect(sv(SV_LOC, { output_format: "helm" }).patch)
      .toEqual({ sv_ingress: "nginx" });
    expect(sv(PERF_LOC, { output_format: "helm" }).patch).toBeNull();
    expect(sv(SV_LOC, { sv_ingress: SV_NONE, output_format: "helm" }).patch)
      .toBeNull();
  });

  it("seeds no ingress into a bundle whose format has no such field", () => {
    // A docker bundle has no ingress field, so nothing is seeded.
    expect(sv(SV_LOC, { output_format: "docker" }).patch).toBeNull();
    expect(sv(SV_LOC, { output_format: "docker" }).required).toBe(false);
    expect(sv(SV_LOC, { output_format: "docker" }).groupRequired.sv).toBe(false);
    // ...while the location still demands SV.
    expect(sv(SV_LOC, { output_format: "docker" }).location).toBe(true);
  });

  it("settles in one pass from every state that needs correcting", () => {
    expect(settle(SV_LOC, { output_format: "helm", sv_istio_gateway: "gw" }))
      .toEqual({ output_format: "helm", sv_ingress: "nginx",
                 sv_istio_gateway: null });
    // Docker settles at once, untouched.
    expect(settle(SV_LOC, { output_format: "docker" }))
      .toEqual({ output_format: "docker" });
    expect(settle(PERF_LOC, { sv_ingress: "openshift", platform: "k8s",
                              sv_istio_gateway: "gw" }))
      .toEqual({ sv_ingress: "nginx", platform: "k8s",
                 sv_istio_gateway: null });
  });

  it("leaves a declined location's format alone", () => {
    // A decline settles on any format.
    expect(settle(SV_LOC, { sv_ingress: SV_NONE, output_format: "helm" }))
      .toEqual({ sv_ingress: SV_NONE, output_format: "helm" });
    expect(settle(SV_LOC, { sv_ingress: SV_NONE, output_format: "docker" }))
      .toEqual({ sv_ingress: SV_NONE, output_format: "docker" });
  });

  it("keeps the format of a bundle whose SV options are being cleared", () => {
    // A location running something else: the format is kept while notRunPatch
    // clears the options.
    expect(settle(PERF_LOC, { ...CONFIGURED, output_format: "docker" }, false))
      .toEqual({ ...CONFIGURED, output_format: "docker" });
    // ...and a stranded backend does not reset it either.
    expect(settle(PERF_LOC, { sv_ingress: "openshift", platform: "k8s",
                              output_format: "docker" }, false).output_format)
      .toBe("docker");
  });
});

// -- SV does not share a location ----------------------------------------------
// Crane applies one limit pair to every pod: enforced where a location is
// being decided, warned about where it exists.

describe("service virtualization on a location of its own", () => {
  const ORDER = ["performance", "functionalGui", "mockServices"];
  // The funcIds whose agent runs an engine, as served (`runs_engine`).
  const ENGINES = ["performance", "functionalGui"];
  const tick = (d: string[], id: string, on: boolean) =>
    toggleDeclared(d, id, on, ORDER, exclusiveWith(ENGINES));

  it("clears the engine functionalities when it is declared", () => {
    expect(tick(["performance", "functionalGui"], "mockServices", true))
      .toEqual(["mockServices"]);
  });

  it("...and is cleared by either of them", () => {
    // Both ways round: the latest tick wins.
    expect(tick(["mockServices"], "performance", true)).toEqual(["performance"]);
    expect(tick(["mockServices"], "functionalGui", true))
      .toEqual(["functionalGui"]);
  });

  it("leaves the two engine functionalities alone together", () => {
    // Two engine functionalities share a location fine.
    expect(tick(["performance"], "functionalGui", true))
      .toEqual(["performance", "functionalGui"]);
  });

  it("says nothing about a funcId neither side names", () => {
    // Unmodelled funcIds exclude nothing.
    expect(tick(["mockServices"], "tdm", true)).toEqual(["mockServices", "tdm"]);
    expect(exclusiveWith(ENGINES)("tdm")).toEqual([]);
  });

  it("names a location that already mixes the two, and only such a one", () => {
    // Connect mode's warning: true only of the mixture.
    expect(svMixedWithEngines(["performance", "mockServices"], ENGINES)).toBe(true);
    expect(svMixedWithEngines(["functionalGui", "mockServices"], ENGINES)).toBe(true);
    expect(svMixedWithEngines(["mockServices"], ENGINES)).toBe(false);
    expect(svMixedWithEngines(["performance", "functionalGui"], ENGINES)).toBe(false);
    // ...and an unclaimed funcId beside it is not an engine.
    expect(svMixedWithEngines(["tdm", "mockServices"], ENGINES)).toBe(false);
  });
});
