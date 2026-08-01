// Service virtualization, as one answer rather than twelve.
//
// Everything here is data in, data out -- what the location runs, the current
// options, the served constants -- so it is tested the way the option groups
// are, with no DOM. The state this file exists for is the one the page could
// not test at all: an effect that WROTE `sv_ingress` and another that READ it
// to decide the same question. `patch` is that write as a value, so the loop
// can be run to a standstill here in a millisecond instead of being watched in
// a browser.
import { describe, expect, it } from "vitest";
import { Options, SvConstants } from "./api";
import { SV_NONE } from "./optionGroups";
import { svState } from "./sv";

// The funcId a location carries to mean "runs mockServices" is served, not
// spelled here -- the same reason the page reads it off /api/sv-constants.
const CONST: SvConstants = {
  func_ids: ["mock-services"],
  ingress_types: ["nginx", "istio", "contour", "openshift"],
  // Shape only, deliberately not the real backend names: which backends
  // publish over NODEPORT is the server's fact, pinned against the generator
  // in tests/test_server.py. What this file owns is whether the record
  // consults the table it is handed.
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

const sv = (funcIds: string[] | undefined, o: Options = {}) =>
  svState(funcIds, o, CONST);

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
    // A location can carry mockServices and be wanted for performance alone.
    // generate() accepts that, so the demand is answered rather than pending.
    const s = sv(SV_LOC, { sv_ingress: SV_NONE });
    expect(s.location).toBe(true);
    expect(s.declined).toBe(true);
    expect(s.required).toBe(false);
  });

  it("does not read a decline on a location that never asked as declining", () => {
    // The row's declined hint is about giving something up; a performance
    // location has nothing to give up.
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
// One rule, stated in the group declaration and read from here -- the page used
// to reach through the group table with a non-null assertion to ask it.

describe("whether the configuration is finished", () => {
  it("is finished when nothing asks for it", () => {
    expect(sv(PERF_LOC).ok).toBe(true);
  });

  it("is unfinished on an SV location with nothing set", () => {
    // ...as the options stand. The page seeds a backend for exactly this
    // state; see `patch` below.
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
    // Computed, not deduced from the absence of other reasons: the panel would
    // otherwise show the nodePort sentence for an empty domain.
    expect(sv(SV_LOC, { sv_ingress: "contour" }).nodePortConflict).toBe(false);
  });
});

// -- the chart ---------------------------------------------------------------

describe("the Helm chart", () => {
  it("is refused for a location that needs virtual services", () => {
    expect(sv(SV_LOC).helmBlocked).toBe(
      "Not for this location — service virtualization needs an ingress, its RBAC "
      + "and a TLS secret, which this chart does not carry.");
  });

  it("is offered where nothing needs an ingress", () => {
    expect(sv(PERF_LOC).helmBlocked).toBeUndefined();
    expect(sv(SV_LOC, { sv_ingress: SV_NONE }).helmBlocked).toBeUndefined();
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
    // Trimmed for the lookups, untrimmed for the controlled inputs -- trimming
    // one of those would stop the user typing a space.
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
    // generate() refuses the combination -- a plain API server serves no
    // route.openshift.io -- so it is not on the select to be picked.
    expect(sv(SV_LOC, { platform: "k8s" }).ingressTypes)
      .toEqual(["nginx", "istio", "contour"]);
    expect(sv(SV_LOC, { platform: "openshift" }).ingressTypes)
      .toEqual(CONST.ingress_types);
  });
});

// -- the write that used to be a loop ----------------------------------------
// Two effects: one wrote sv_ingress, the other read it to decide the same
// question. Here it is one value, and the test that matters is that applying
// it settles.

describe("the option patch", () => {
  /** Apply the patch until there is none, or give up. A loop that does not
   *  settle is the bug this shape exists to make visible. */
  const settle = (funcIds: string[] | undefined, o: Options) => {
    let cur = o;
    for (let i = 0; i < 5; i += 1) {
      const { patch } = svState(funcIds, cur, CONST);
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
    // Neither an imported profile nor the row opening via `required` goes
    // through the group's enable(), so without this the select shows its nginx
    // default over a state that is still null.
    expect(sv(SV_LOC).patch).toEqual({ sv_ingress: "nginx" });
    expect(settle(SV_LOC, {}).sv_ingress).toBe("nginx");
  });

  it("rescues a profile stranded on the OpenShift backend", () => {
    // An imported profile can arrive with sv_ingress "openshift" while the
    // platform is not OpenShift, which generate() refuses -- and the option
    // disappears from the select, leaving nothing on screen to explain the
    // error. nginx works anywhere.
    expect(sv(PERF_LOC, { sv_ingress: "openshift", platform: "k8s" }).patch)
      .toEqual({ sv_ingress: "nginx" });
    expect(sv(SV_LOC, { sv_ingress: "openshift", platform: "openshift" }).patch)
      .toBeNull();
  });

  it("drops a gateway no backend will read", () => {
    // Only crane's istio backend reads KUBERNETES_ISTIO_GATEWAY_NAME, so
    // generate() refuses it anywhere else -- and an imported profile pairing
    // the two would hit that with nothing in the UI to explain it.
    expect(sv(SV_LOC, { ...CONFIGURED, sv_istio_gateway: "gw" }).patch)
      .toEqual({ sv_istio_gateway: null });
    expect(sv(SV_LOC, { ...CONFIGURED, sv_ingress: "istio",
                        sv_istio_gateway: "gw" }).patch).toBeNull();
  });

  it("clears the gateway the seeded backend cannot read either", () => {
    // Both halves in one pass: the seed decides the backend, and it is the
    // seeded one the gateway is judged against.
    expect(sv(SV_LOC, { sv_istio_gateway: "gw" }).patch)
      .toEqual({ sv_ingress: "nginx", sv_istio_gateway: null });
  });

  it("falls back from a chart this location cannot have", () => {
    // A location can turn out to be an SV one after the format was picked, and
    // an imported profile can arrive already set to helm. Leaving it selected
    // fails every generate call against a disabled segment.
    expect(sv(SV_LOC, { ...CONFIGURED, output_format: "helm" }).patch)
      .toEqual({ output_format: "manifests" });
    expect(sv(PERF_LOC, { output_format: "helm" }).patch).toBeNull();
    expect(sv(SV_LOC, { sv_ingress: SV_NONE, output_format: "helm" }).patch)
      .toBeNull();
  });

  it("settles in one pass from every state that needs correcting", () => {
    expect(settle(SV_LOC, { output_format: "helm", sv_istio_gateway: "gw" }))
      .toEqual({ output_format: "manifests", sv_ingress: "nginx",
                 sv_istio_gateway: null });
    expect(settle(PERF_LOC, { sv_ingress: "openshift", platform: "k8s",
                              sv_istio_gateway: "gw" }))
      .toEqual({ sv_ingress: "nginx", platform: "k8s",
                 sv_istio_gateway: null });
  });

  it("leaves a declined location's format alone", () => {
    // Declining is an answer, and the chart is generated for the performance
    // bundle it leaves behind.
    expect(settle(SV_LOC, { sv_ingress: SV_NONE, output_format: "helm" }))
      .toEqual({ sv_ingress: SV_NONE, output_format: "helm" });
  });
});
