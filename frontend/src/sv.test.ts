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
import { IGNORED_BY_FORMAT } from "./fixtures";
import { optionApplies } from "./formats";
import { SV_NONE, toggleDeclared } from "./optionGroups";
import {
  exclusiveWith, SV_FUNCTIONALITY, svMixedWithEngines, svState,
} from "./sv";

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

/** The predicate the page hands in: does this option reach anything in a
 *  bundle of the format these options name? Derived from the served table
 *  rather than from a format test, because that is what App does and because
 *  since #182 it is what decides which platform's SV options this record is
 *  about at all -- the two sets are each other's ignored ones. */
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

// -- the formats that cannot publish a virtual service ------------------------

describe("the blocked formats", () => {
  it("names the chart, for a location that needs virtual services", () => {
    // One refusal, and it is generate()'s: the chart carries no ingress, no
    // RBAC for one and no wildcard TLS secret, so one emitted anyway deploys,
    // reports idle and stalls at WAITING_FOR_DOMAIN.
    const blocked = sv(SV_LOC).blockedFormats;
    expect(blocked.helm).toMatch(/ingress, its RBAC and a TLS secret/);
    // Never manifests: it is the one that carries the ingress, and the
    // fallback every correction below points at.
    expect(blocked.manifests).toBeUndefined();
    // ...and docker was here until #182. The old sentence said a docker agent
    // "does not carry" HOSTNAME_OVERRIDE and a TLS pair, which was true of the
    // bundle and never of the agent: it publishes virtual services now, with
    // its own three options, so nothing about the format is refused.
    expect(blocked.docker).toBeUndefined();
  });

  it("blocks nothing where nothing needs an ingress", () => {
    expect(sv(PERF_LOC).blockedFormats).toEqual({});
    expect(sv(SV_LOC, { sv_ingress: SV_NONE }).blockedFormats).toEqual({});
  });

  it("blocks on what is configured, not on what the location asked for", () => {
    // #115. generate() refuses on _sv_cfg returning a config, and _sv_cfg
    // never looks at the funcIds before it does -- so an SV configuration on a
    // location that demanded nothing is a docker bundle the server refuses.
    // Read off `required`, this said nothing about it and the segment stayed
    // enabled: an off-screen blocker, which is the shape the page keeps
    // removing.
    const s = sv(PERF_LOC, CONFIGURED);
    expect(s.required).toBe(false);
    expect(s.blockedFormats.helm).toMatch(/ingress, its RBAC and a TLS secret/);
  });

  it("still takes the chart away from a bundle whose ingress is stranded", () => {
    // The one thing `carries` is deliberately NOT gated by the format for.
    // A docker bundle can hold an `sv_ingress` -- it is one of that format's
    // ignored options, kept and named rather than wiped -- and it becomes live
    // the moment somebody picks helm. So what may be picked follows what the
    // configuration would mean *there*, not what it means here.
    expect(sv(PERF_LOC, { ...CONFIGURED, output_format: "docker" })
      .blockedFormats.helm).toMatch(/this chart does not carry/);
  });

  it("blocks nothing for options that are on their way out", () => {
    // ...and the other end of the same question. A location known to run
    // something else has notRunPatch clearing every SV option through the
    // group's own disable(), so an sv_ingress still in the options is not a
    // bundle anybody will generate -- and taking the docker segment away for
    // it would lose a format choice that was valid all along.
    expect(svState(PERF_LOC, CONFIGURED, CONST, false).blockedFormats)
      .toEqual({});
  });

  it("keeps 'no virtual services' apart from 'the constants have not landed'", () => {
    // Read off `required` these shared an answer: func_ids arrives empty
    // before /api/sv-constants lands, which makes every location a non-SV one
    // and every format available. What is configured is an option this page
    // wrote, so an unread table cannot make it lie.
    const unread: SvConstants = { func_ids: [], ingress_types: [], backends: {} };
    expect(svState(SV_LOC, CONFIGURED, unread).blockedFormats.helm)
      .toMatch(/this chart does not carry/);
  });
});

// -- and the same refusal read from the other end -----------------------------

describe("the functionality a format cannot serve", () => {
  it("names the format's own refusal, keyed by functionality", () => {
    // The card renders this instead of its switches: a chart offering an
    // ingress, a subdomain and a TLS secret is offering three fields that make
    // the whole bundle unbuildable.
    // Keyed by the funcId, which is what a functionality id is (#149) -- and
    // no longer the `sv` group id it used to be spelled the same as.
    expect(SV_FUNCTIONALITY).toBe("mockServices");
    expect(sv(SV_LOC, { output_format: "helm" })
      .functionalityBlocked[SV_FUNCTIONALITY])
      .toMatch(/ingress, its RBAC and a TLS secret/);
    // Docker serves it now (#182), so the card offers switches rather than a
    // sentence -- the `svDocker` group's three, which are that format's way of
    // publishing the same thing.
    expect(sv(SV_LOC, { output_format: "docker" }).functionalityBlocked)
      .toEqual({});
  });

  it("says nothing about a format that can serve it", () => {
    expect(sv(SV_LOC, { output_format: "manifests" }).functionalityBlocked).toEqual({});
    expect(sv(SV_LOC, {}).functionalityBlocked).toEqual({});
  });

  it("leaves a functionality the location does not run to say so itself", () => {
    // "Not enabled here" and "not possible in this format" are different
    // answers and the card must not give the second where the first is true --
    // the location's own funcIds are what that card is about.
    expect(svState(PERF_LOC, { output_format: "docker" }, CONST, false)
      .functionalityBlocked).toEqual({});
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
    // ...and the SCC-friendly posture is not the same answer: it is recommended
    // on vanilla Kubernetes too, which is where a Route would have been offered
    // for every bundle that took the default.
    expect(sv(SV_LOC, { platform: "openshift", openshift_cluster: false })
      .ingressTypes).toEqual(["nginx", "istio", "contour"]);
  });
});

// -- the write that used to be a loop ----------------------------------------
// Two effects: one wrote sv_ingress, the other read it to decide the same
// question. Here it is one value, and the test that matters is that applying
// it settles.

describe("the option patch", () => {
  /** Apply the patch until there is none, or give up. A loop that does not
   *  settle is the bug this shape exists to make visible. */
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
    // Neither an imported profile nor the row opening via `required` goes
    // through the group's enable(), so without this the select shows its nginx
    // default over a state that is still null.
    expect(sv(SV_LOC).patch).toEqual({ sv_ingress: "nginx" });
    expect(settle(SV_LOC, {}).sv_ingress).toBe("nginx");
  });

  it("seeds nothing for a bundle that no longer carries the functionality", () => {
    // The other writer's turn. `notRunPatch` clears these options through the
    // group's own disable() the moment the bundle stops carrying mockServices,
    // and this used to re-seed an ingress from a demand read off funcIds that
    // had not caught up -- two writers, one question, two sources, and an
    // effect loop that never settled. In manual entry that is not a race but
    // the normal case: `runs` is the declaration and the funcIds are the facts
    // fetched for the previous one, a debounce behind it (#151).
    expect(sv(SV_LOC, {}, false).patch).toBeNull();
    expect(sv(SV_LOC, {}, false).required).toBe(false);
    expect(sv(SV_LOC, {}, false).groupRequired.sv).toBe(false);
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
    // The other way to stop being OpenShift, which the cluster toggle is: same
    // stranding, and the same rescue.
    expect(sv(PERF_LOC, { sv_ingress: "openshift", platform: "openshift",
                          openshift_cluster: false }).patch)
      .toEqual({ sv_ingress: "nginx" });
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

  it("falls back from a format this location cannot have", () => {
    // A location can turn out to be an SV one after the format was picked, and
    // an imported profile can arrive already set to one that refuses it.
    // Leaving it selected fails every generate call against a disabled segment.
    expect(sv(SV_LOC, { ...CONFIGURED, output_format: "helm" }).patch)
      .toEqual({ output_format: "manifests" });
    expect(sv(PERF_LOC, { output_format: "helm" }).patch).toBeNull();
    expect(sv(SV_LOC, { sv_ingress: SV_NONE, output_format: "helm" }).patch)
      .toBeNull();
  });

  it("seeds no ingress into a bundle whose format has no such field", () => {
    // The demand is the location's either way, but the ingress group is not
    // where a docker bundle answers it -- the `svDocker` three are (#182). So
    // the seed is gated on the format having the field at all: writing
    // `sv_ingress: nginx` here would set an ignored option off a control that
    // format's page does not show, and then report it as set-and-not-carried.
    expect(sv(SV_LOC, { output_format: "docker" }).patch).toBeNull();
    expect(sv(SV_LOC, { output_format: "docker" }).required).toBe(false);
    expect(sv(SV_LOC, { output_format: "docker" }).groupRequired.sv).toBe(false);
    // ...and the location still demands it. `location` is what the funcIds say
    // and does not move with the format.
    expect(sv(SV_LOC, { output_format: "docker" }).location).toBe(true);
  });

  it("settles in one pass from every state that needs correcting", () => {
    expect(settle(SV_LOC, { output_format: "helm", sv_istio_gateway: "gw" }))
      .toEqual({ output_format: "manifests", sv_ingress: "nginx",
                 sv_istio_gateway: null });
    // Docker settles at once and untouched: the format is not refused and the
    // ingress is not its field to seed.
    expect(settle(SV_LOC, { output_format: "docker" }))
      .toEqual({ output_format: "docker" });
    expect(settle(PERF_LOC, { sv_ingress: "openshift", platform: "k8s",
                              sv_istio_gateway: "gw" }))
      .toEqual({ sv_ingress: "nginx", platform: "k8s",
                 sv_istio_gateway: null });
  });

  it("leaves a declined location's format alone", () => {
    // Declining is an answer, and the chart -- or the container -- is
    // generated for the performance bundle it leaves behind.
    expect(settle(SV_LOC, { sv_ingress: SV_NONE, output_format: "helm" }))
      .toEqual({ sv_ingress: SV_NONE, output_format: "helm" });
    expect(settle(SV_LOC, { sv_ingress: SV_NONE, output_format: "docker" }))
      .toEqual({ sv_ingress: SV_NONE, output_format: "docker" });
  });

  it("falls back from a format a configuration nobody demanded refuses", () => {
    // #115: the same correction, for the state the demand could not see. An
    // imported profile can carry docker and a full SV configuration for a
    // location whose funcIds carry no served functionality at all, and nothing was
    // clearing either half. Helm now, since docker serves it (#182) -- what
    // the state is about is a configuration nobody demanded, not which format
    // happened to be refused when it was written.
    expect(settle(PERF_LOC, { ...CONFIGURED, output_format: "helm" }))
      .toEqual({ ...CONFIGURED, output_format: "manifests" });
  });

  it("keeps the format of a bundle whose SV options are being cleared", () => {
    // The regression the `runs` input exists to stop. A location known to run
    // something else has notRunPatch clearing these options through the
    // group's own disable(); resetting the format on the way past would take
    // away a docker choice that was valid all along, and the options it was
    // reset for are gone by the next render.
    expect(settle(PERF_LOC, { ...CONFIGURED, output_format: "docker" }, false))
      .toEqual({ ...CONFIGURED, output_format: "docker" });
    // ...including the stranded backend, which is a correction of its own and
    // must not become a format reset by the back door.
    expect(settle(PERF_LOC, { sv_ingress: "openshift", platform: "k8s",
                              output_format: "docker" }, false).output_format)
      .toBe("docker");
  });
});

// -- and the one thing SV is not: something to share a location with ----------
// #151. Crane applies one KUBERNETES_RESOURCES_LIMITS_CPU/_MEMORY pair to every
// pod it creates, so a location running both an engine and a mock has one
// number for two sizing problems. Where the location is being *decided* -- manual
// entry, the new-location form -- the opinion is free and the rule is enforced;
// where it already exists nothing on this page can un-mix it, so it is warned
// about and never refused.

describe("service virtualization on a location of its own", () => {
  const ORDER = ["performance", "functionalGui", "mockServices"];
  // The funcIds whose agent carries a taurus engine, as /api/functionalities
  // serves them (`runs_engine`). The rule takes them rather than knowing them:
  // the answer is facts.CATEGORY_BY_FUNC's, and test_server.py holds the served
  // pair to the planner's own table.
  const ENGINES = ["performance", "functionalGui"];
  const tick = (d: string[], id: string, on: boolean) =>
    toggleDeclared(d, id, on, ORDER, exclusiveWith(ENGINES));

  it("clears the engine functionalities when it is declared", () => {
    expect(tick(["performance", "functionalGui"], "mockServices", true))
      .toEqual(["mockServices"]);
  });

  it("...and is cleared by either of them", () => {
    // Both ways round, because both are the same statement about one limit
    // pair: whichever is ticked second is the one somebody just asked for.
    expect(tick(["mockServices"], "performance", true)).toEqual(["performance"]);
    expect(tick(["mockServices"], "functionalGui", true))
      .toEqual(["functionalGui"]);
  });

  it("leaves the two engine functionalities alone together", () => {
    // The pair the exclusivity is not about: one agent, one engine pod size,
    // and 71 of 168 locations in one real account run exactly this.
    expect(tick(["performance"], "functionalGui", true))
      .toEqual(["performance", "functionalGui"]);
  });

  it("says nothing about a funcId neither side names", () => {
    // tdm, dataPublisher, delphix: real funcIds this tool models no
    // functionality for. Nothing here knows what they cost, so nothing here
    // clears anything for them -- the create-location form offers the account's
    // whole vocabulary and must not edit what it cannot judge.
    expect(tick(["mockServices"], "tdm", true)).toEqual(["mockServices", "tdm"]);
    expect(exclusiveWith(ENGINES)("tdm")).toEqual([]);
  });

  it("names a location that already mixes the two, and only such a one", () => {
    // Connect mode's answer. A location that exists is BlazeMeter's own UI's to
    // change -- #113 removed the one route here that did -- so this is a
    // sentence, never a refusal, and it is only true of the mixture.
    expect(svMixedWithEngines(["performance", "mockServices"], ENGINES)).toBe(true);
    expect(svMixedWithEngines(["functionalGui", "mockServices"], ENGINES)).toBe(true);
    expect(svMixedWithEngines(["mockServices"], ENGINES)).toBe(false);
    expect(svMixedWithEngines(["performance", "functionalGui"], ENGINES)).toBe(false);
    // ...and an unclaimed funcId beside it is not an engine.
    expect(svMixedWithEngines(["tdm", "mockServices"], ENGINES)).toBe(false);
  });
});
