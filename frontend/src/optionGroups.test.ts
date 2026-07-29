import { describe, expect, it } from "vitest";
import { Feature, Options } from "./api";
import {
  allGroupsOff, ANY_DEPLOYMENT, appliesTo, detectGroups, ENGINE_SIZES,
  featuresOf, GROUP_BY_ID, GroupId, hiddenBlockers, incompleteGroups,
  serviceAccountOk, unavailableFeatures,
  OPTION_GROUPS, OptionGroup,
  setButHidden, startFeature, suggestNamespace, unclaimedFuncIds, visibleGroups,
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
  ["security", "use_secret", false],
  ["security", "cluster_rbac", true],
  ["security", "service_type", "NODEPORT"],
  // The one entry here whose detecting value is `false`: restrict_engines is
  // on by default, so absent means restricted and only an explicit false is
  // a departure the group should open for.
  ["security", "restrict_engines", false],
  // A tri-state, so unlike the two above BOTH booleans are a departure --
  // absent means the registry decides. `true` is the one worth pinning: it is
  // also the resolved default without a private registry, and a detection rule
  // written as "only false matters" would leave a bundle that asks for
  // auto-update alongside a mirror showing a closed group.
  ["security", "auto_update", true],
  ["sv", "sv_ingress", "nginx"],
];

/** Owned and written, but never a reason to open the group on its own. Listed
 *  so that every declared key is accounted for by one table or the other. */
const WRITE_ONLY: [GroupId, string][] = [
  // Only meaningful beside ca_existing_configmap, which does the detecting.
  ["ca", "ca_configmap_key"],
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
  use_secret: false,
  cluster_rbac: true,
  service_type: "NODEPORT",
  restrict_engines: false,
  auto_update: false,
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

  it("leaves service_type to Security alone", () => {
    // The SV group co-owned it to force CLUSTERIP unconditionally. #60 ran all
    // four backends: two publish fine over NODEPORT and two do not, so the rule
    // is per-backend now and lives in `incomplete`, not in a second writer.
    const owners = OPTION_GROUPS.filter((g) => g.keys.includes("service_type"));
    expect(owners.map((g) => g.id)).toEqual(["security"]);
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
      sizing: { engine_cpu_limit: null, engine_mem_limit: null },
      security: { use_secret: true, cluster_rbac: false,
                  service_type: "CLUSTERIP", restrict_engines: true,
                  // Back to unset, not to a boolean: the generator's default
                  // is the tri-state's null, and writing `true` here would
                  // pin auto-update on for a bundle with a private registry.
                  auto_update: null },
      sv: {
        sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
        sv_istio_gateway: null,
      },
    };
    for (const g of OPTION_GROUPS) expect(g.disable(FULL)).toEqual(wipes[g.id]);
  });

  it("leaves service_type alone when service virtualization goes off", () => {
    // It never writes service_type in either direction now -- but a wipe that
    // reached it would still silently rewrite the user's choice, so pin it.
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

  it("seeds an ingress and keeps the chosen service type", () => {
    // It used to force CLUSTERIP here. #60 deployed a virtual service over
    // NODEPORT on namespaced RBAC and it served, so a NODEPORT the user chose
    // survives switching SV on.
    expect(GROUP_BY_ID.sv.enable({ service_type: "NODEPORT" }))
      .toEqual({ sv_ingress: "nginx" });
  });

  it("seeds nothing over an ingress that was already chosen", () => {
    // An empty patch, not the value echoed back: flipGroup hands back the same
    // options object for one, and a fresh identity re-POSTs /api/generate.
    expect(GROUP_BY_ID.sv.enable({ sv_ingress: "contour" })).toEqual({});
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

// -- the feature view ---------------------------------------------------------
// One feature is configured at a time, chosen from a list /api/features serves.
// Nothing in the frontend enumerates features: groups tag themselves with the
// feature ids they belong to, and everything else -- labels, suggested
// namespaces, which funcIds mean which feature -- is read off the served list.
// So the vocabulary below is a fixture standing in for that response, and the
// "a feature was added" tests extend it exactly the way server.py would.

const PERF: Feature = {
  id: "performance", label: "Performance & functional testing",
  hint: "load and functional tests", namespace: "blazemeter",
  func_ids: ["performance", "functionalApi", "functionalGui", "proxyRecorder"],
};
const SV: Feature = {
  id: "sv", label: "Service virtualization", hint: "virtual services",
  namespace: "blazemeter-sv", func_ids: ["mockServices"],
};
const FEATURES = [PERF, SV];
/** Added the way a real new feature is: one entry in the served vocabulary, and
 *  no frontend edit at all until some group wants to be tagged with it. */
const SECRETS: Feature = {
  id: "secrets", label: "Private vault", hint: "secrets from a vault",
  namespace: "blazemeter-vault", func_ids: ["secretsPrivateVault"],
};
/** funcIds the tool does not model. Real locations carry them today. */
const UNMODELLED = ["tdm", "dataPublisher", "delphix"];

const ids = (gs: OptionGroup[]) => gs.map((g) => g.id);
/** The groups no feature owns -- registry, proxy, CA, scheduling: they apply to
 *  any deployment, so they are on screen whatever is being configured. */
const UNIVERSAL = ids(OPTION_GROUPS.filter((g) => !g.features.length));

describe("group attribution", () => {
  it("tags every group with features the served vocabulary knows", () => {
    for (const g of OPTION_GROUPS) {
      for (const f of g.features) expect(FEATURES.map((x) => x.id)).toContain(f);
    }
  });

  it("says which feature a group belongs to, or that it applies to any", () => {
    expect(appliesTo(GROUP_BY_ID.sv, FEATURES)).toBe(SV.label);
    expect(appliesTo(GROUP_BY_ID.sizing, FEATURES)).toBe(PERF.label);
    expect(appliesTo(GROUP_BY_ID.proxy, FEATURES)).toBe(ANY_DEPLOYMENT);
  });

  it("falls back to the raw id for a feature the vocabulary has not named", () => {
    // The same deliberate failure mode as the funcId labels: an unlabelled
    // feature is shown under its id rather than leaving a group unattributed.
    expect(appliesTo(GROUP_BY_ID.sv, [])).toBe("sv");
  });
});

describe("which groups are in view", () => {
  it("shows a feature's own groups plus the ones that apply to any", () => {
    // sv is the last declaration, so its view is the universal groups then it.
    expect(ids(visibleGroups("sv"))).toEqual([...UNIVERSAL, "sv"]);
    expect(ids(visibleGroups("performance"))).toContain("sizing");
    expect(ids(visibleGroups("performance"))).not.toContain("sv");
    expect(ids(visibleGroups("sv"))).not.toContain("sizing");
  });

  it("keeps the declaration order, so the form does not reshuffle", () => {
    const shown = ids(visibleGroups("performance"));
    expect(shown).toEqual(ids(OPTION_GROUPS).filter((id) => shown.includes(id)));
  });

  it("shows the any-deployment groups for a feature no group is tagged with", () => {
    // What a newly added feature looks like before anything is tagged with it:
    // the registry/proxy/CA/scheduling options, and nothing claimed falsely.
    expect(ids(visibleGroups(SECRETS.id))).toEqual(UNIVERSAL);
  });

  it("hides nothing until a feature is chosen", () => {
    // The vocabulary is fetched; a failed or pending fetch must not take
    // options off the page.
    expect(ids(visibleGroups(null))).toEqual(ids(OPTION_GROUPS));
  });

  it("is a selection, not a patch -- the options are never touched", () => {
    // The selector is a view, not a scope: narrowing it may not change what the
    // manifests contain, so none of these may write an option. A frozen input
    // makes an attempt throw rather than pass silently.
    const frozen = Object.freeze({ ...FULL });
    expect(() => {
      visibleGroups("sv");
      setButHidden(frozen, "sv");
      hiddenBlockers([GROUP_BY_ID.sv], "performance");
    }).not.toThrow();
    expect(frozen).toEqual(FULL);
  });
});

describe("set but not in view", () => {
  it("reports a group configured under another feature", () => {
    // FULL has every group's fields set, so viewing performance leaves the SV
    // ingress set and off screen -- exactly what must not ship invisibly.
    expect(ids(setButHidden(FULL, "performance"))).toEqual(["sv"]);
    expect(ids(setButHidden(FULL, "sv"))).toEqual(["sizing"]);
  });

  it("reports nothing when the hidden groups hold nothing", () => {
    expect(setButHidden({ namespace: "blazemeter" }, "performance")).toEqual([]);
  });

  it("never reports a group that is on screen", () => {
    for (const f of [...FEATURES, SECRETS]) {
      const shown = new Set(ids(visibleGroups(f.id)));
      for (const g of setButHidden(FULL, f.id)) expect(shown.has(g.id)).toBe(false);
    }
  });

  it("reports every group when a feature owning them is not in view", () => {
    // A feature with no groups of its own still hides the other features':
    // both tagged groups are set in FULL and neither is on screen.
    expect(ids(setButHidden(FULL, SECRETS.id))).toEqual(["sizing", "sv"]);
  });
});

describe("required but not in view", () => {
  const unfinished = incompleteGroups({ sv_ingress: "nginx" }, {});

  it("is the unfinished groups the current view is hiding", () => {
    // Which groups are unfinished is each group's own rule; this only says
    // whether the reason is even on screen.
    expect(hiddenBlockers(unfinished, "performance")).toEqual([GROUP_BY_ID.sv]);
  });

  it("says nothing when the group needing attention is on screen", () => {
    // Then the group renders its own error, which is where it belongs.
    expect(hiddenBlockers(unfinished, "sv")).toEqual([]);
  });

  it("says nothing when nothing is incomplete", () => {
    expect(hiddenBlockers([], "performance")).toEqual([]);
  });

  it("never blocks on a group that applies to any deployment", () => {
    // It cannot be off screen, so it can never be the hidden reason.
    expect(hiddenBlockers([GROUP_BY_ID.registry, GROUP_BY_ID.proxy], "sv"))
      .toEqual([]);
  });

  it("labels the switch from the served vocabulary, like every other row", () => {
    // One label lookup, so the button and the "also in this bundle" line
    // beside it cannot disagree about what a group is called.
    const [g] = hiddenBlockers(unfinished, "performance");
    expect(appliesTo(g, FEATURES)).toBe(SV.label);
    expect(appliesTo(g, [])).toBe("sv");     // unnamed feature falls back
  });
});

describe("which feature a location starts on", () => {
  it("picks the feature its funcIds carry", () => {
    expect(startFeature(["mockServices"], FEATURES)).toBe("sv");
    expect(startFeature(["functionalGui"], FEATURES)).toBe("performance");
  });

  it("picks the first served feature for a location carrying both", () => {
    // Deliberate: a location doing both is a performance location that also
    // serves mocks, and the download-button block routes to the SV settings
    // when they are what is missing.
    expect(startFeature(["mockServices", "performance"], FEATURES)).toBe("performance");
    expect(featuresOf(["mockServices", "performance"], FEATURES))
      .toEqual(["performance", "sv"]);
  });

  it("is not broken by a funcId the tool does not model", () => {
    // Real locations carry tdm/dataPublisher/delphix today. An unmodelled
    // funcId claims no feature: alongside a modelled one it is ignored, and
    // alone it leaves the default rather than an empty selector.
    expect(startFeature([...UNMODELLED, "mockServices"], FEATURES)).toBe("sv");
    expect(startFeature(UNMODELLED, FEATURES)).toBe("performance");
    expect(featuresOf(UNMODELLED, FEATURES)).toEqual([]);
    // …and they are nameable, so the page can say what it has no options for
    // rather than pretending the location is only what it models.
    expect(unclaimedFuncIds([...UNMODELLED, "performance"], FEATURES))
      .toEqual(UNMODELLED);
    expect(unclaimedFuncIds(["performance", "mockServices"], FEATURES)).toEqual([]);
    expect(startFeature([], FEATURES)).toBe("performance");
    expect(startFeature(undefined, FEATURES)).toBe("performance");
  });

  it("offers a feature added to the vocabulary, with no change here", () => {
    // The acceptance test of "adding a feature is a backend change": the
    // vocabulary gains an entry and the location starts on it.
    const served = [...FEATURES, SECRETS];
    expect(startFeature(["secretsPrivateVault"], served)).toBe("secrets");
    expect(featuresOf(["secretsPrivateVault", "performance"], served))
      .toEqual(["performance", "secrets"]);
  });

  it("claims nothing when the vocabulary has not arrived", () => {
    expect(startFeature(["performance"], [])).toBe(null);
  });
});

describe("the suggested namespace", () => {
  it("suggests the feature's namespace when the field is empty", () => {
    expect(suggestNamespace("", SV, FEATURES)).toBe("blazemeter-sv");
    expect(suggestNamespace("   ", SV, FEATURES)).toBe("blazemeter-sv");
  });

  it("replaces a namespace another feature suggested", () => {
    expect(suggestNamespace("blazemeter", SV, FEATURES)).toBe("blazemeter-sv");
    expect(suggestNamespace("blazemeter-sv", PERF, FEATURES)).toBe("blazemeter");
  });

  it("never overwrites something typed", () => {
    expect(suggestNamespace("bzm-prod", SV, FEATURES)).toBe(null);
    expect(suggestNamespace("blazemeter2", SV, FEATURES)).toBe(null);
  });

  it("suggests nothing when the namespace is already the suggestion", () => {
    // Same value back would still be a state write, and every option change
    // re-POSTs the preview.
    expect(suggestNamespace("blazemeter-sv", SV, FEATURES)).toBe(null);
    expect(suggestNamespace(" blazemeter-sv ", SV, FEATURES)).toBe(null);
  });

  it("counts a namespace suggested by a feature added later", () => {
    // The rule is "still holding a suggested value", read off the served list
    // -- not a list of names in the frontend, which is what would go stale.
    const served = [...FEATURES, SECRETS];
    expect(suggestNamespace("blazemeter-vault", SV, served)).toBe("blazemeter-sv");
    expect(suggestNamespace("blazemeter", SECRETS, served)).toBe("blazemeter-vault");
  });
});


// -- completeness is the group's own business ---------------------------------
// The download used to be gated by a rule the page held about one group. That
// is the promise "adding a feature needs no frontend change" quietly breaking:
// the second feature with required options would need its own check in App, its
// own entry in the incomplete list, and its own arm on the download guard.

describe("a group declares whether its own configuration is finished", () => {
  const sv = GROUP_BY_ID.sv;

  it("is complete when it is not in use at all", () => {
    expect(sv.incomplete?.({}, false)).toBe(false);
  });

  it("is incomplete once an ingress is chosen without a domain and secret", () => {
    expect(sv.incomplete?.({ sv_ingress: "nginx" }, false)).toBe(true);
    expect(sv.incomplete?.(
      { sv_ingress: "nginx", sv_subdomain: "apps.x.com" }, false)).toBe(true);
    expect(sv.incomplete?.(
      { sv_ingress: "nginx", sv_subdomain: "apps.x.com",
        sv_tls_secret: "wild" }, false)).toBe(false);
  });

  it("is incomplete when the location requires it and nothing is set", () => {
    expect(sv.incomplete?.({}, true)).toBe(true);
  });

  // Deliberately NOT the real backend names. Which backends publish over
  // NODEPORT is the server's fact, pinned against the generator in
  // tests/test_server.py; restating it here would be a second copy free to go
  // stale. What this file owns is whether `incomplete` consults the table it is
  // handed, which two shape-only entries exercise exactly as well.
  const BACKENDS = { publishes: { nodeport_ok: true },
                     does_not: { nodeport_ok: false } };
  const withNodePort = (ingress: string) =>
    ({ sv_ingress: ingress, sv_subdomain: "a.b", sv_tls_secret: "w",
       service_type: "NODEPORT" });

  it("counts NODEPORT as complete for a backend that publishes over it", () => {
    for (const ingress of ["publishes"]) {
      expect(sv.incomplete?.(withNodePort(ingress), false, BACKENDS)).toBe(false);
    }
  });

  it("counts NODEPORT as incomplete for one that does not", () => {
    for (const ingress of ["does_not"]) {
      expect(sv.incomplete?.(withNodePort(ingress), false, BACKENDS)).toBe(true);
    }
  });

  it("does not block before the backend table has loaded", () => {
    // Undefined is "we have not been told", not "it is broken". Blocking on a
    // guess would grey out the download for a configuration that generates
    // fine, and generate() refuses authoritatively either way.
    expect(sv.incomplete?.(withNodePort("does_not"), false)).toBe(false);
    expect(sv.incomplete?.(withNodePort("does_not"), false, {})).toBe(false);
  });

  it("still blocks on an empty field whatever the service type", () => {
    expect(sv.incomplete?.(
      { ...withNodePort("publishes"), sv_tls_secret: "" }, false, BACKENDS)).toBe(true);
  });

  it("groups with no completeness rule never block", () => {
    for (const g of OPTION_GROUPS.filter((x) => x.id !== "sv")) {
      expect(g.incomplete).toBeUndefined();
    }
  });

  it("incompleteGroups derives the list rather than being handed one", () => {
    expect(incompleteGroups({ sv_ingress: "nginx" }, { sv: false })
      .map((g) => g.id)).toEqual(["sv"]);
    expect(incompleteGroups({}, { sv: false })).toEqual([]);
    expect(incompleteGroups({}, { sv: true }).map((g) => g.id)).toEqual(["sv"]);
  });
});

describe("unavailableFeatures", () => {
  it("names the features the location does not run", () => {
    expect(unavailableFeatures(true, ["performance"], FEATURES)).toEqual(["sv"]);
    expect(unavailableFeatures(true, ["sv"], FEATURES)).toEqual(["performance"]);
  });

  it("says nothing when the location runs both", () => {
    expect(unavailableFeatures(true, ["performance", "sv"], FEATURES)).toEqual([]);
  });

  it("says nothing before the answer is known", () => {
    // Manual entry declares rather than reads, and no location chosen yet is
    // "not asked" -- an empty locFeatures must not read as "none enabled", or
    // every feature greys out on first load.
    expect(unavailableFeatures(false, [], FEATURES)).toEqual([]);
    expect(unavailableFeatures(false, ["performance"], FEATURES)).toEqual([]);
  });

  it("says nothing for a location whose funcIds claim no feature", () => {
    // The dead end this guards: 10 of 169 locations in the account checked are
    // sv-bridge / tdm / dataPublisher only. Marking every feature unavailable
    // there would leave nothing configurable and no way forward.
    expect(featuresOf(UNMODELLED, FEATURES)).toEqual([]);
    expect(unavailableFeatures(true, [], FEATURES)).toEqual([]);
  });

  it("extends with the served vocabulary", () => {
    // A third feature nobody has tagged a group with is still a feature a
    // location can lack, so it must be nameable without an edit here.
    const served = [PERF, SV, SECRETS];
    expect(unavailableFeatures(true, ["performance"], served))
      .toEqual(["sv", SECRETS.id]);
  });
});

describe("serviceAccountOk", () => {
  // The name is required whether or not the bundle creates the account, which
  // is the one place the UI's rule and generate.service_account() have to say
  // the same thing -- an empty name produces no bundle at all, so the field
  // shows it rather than the download button failing with a server error.

  it("accepts a name with either setting of create", () => {
    expect(serviceAccountOk({ service_account_name: "crane" })).toBe(true);
    expect(serviceAccountOk({
      service_account_name: "platform-sa", service_account_create: false,
    })).toBe(true);
  });

  it("rejects an empty or whitespace name", () => {
    expect(serviceAccountOk({ service_account_name: "" })).toBe(false);
    expect(serviceAccountOk({ service_account_name: "   " })).toBe(false);
  });

  it("rejects a config that has not named one at all", () => {
    // Before /api/option-defaults lands there is no key. That is not a valid
    // bundle either, and treating it as one would show a green field for a
    // download that cannot happen.
    expect(serviceAccountOk({})).toBe(false);
  });

  it("does not belong to any option group", () => {
    // The fields sit beside the namespace and are always on screen. If they
    // ever became a group's keys, `setButHidden` could report them as hidden
    // and a feature view could take a required field off the page.
    const owned = OPTION_GROUPS.flatMap((g: OptionGroup) => g.keys);
    expect(owned).not.toContain("service_account_name");
    expect(owned).not.toContain("service_account_create");
  });
});
