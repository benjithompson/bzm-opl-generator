import { describe, expect, it } from "vitest";
import { Feature, Options } from "./api";
import {
  allGroupsOff, configureBlockedBy, detectGroups, enabledFeatures, groupsOf,
  SHARED_GROUPS, ENGINE_SIZES, featuresOf, GROUP_BY_ID, GroupId,
  incompleteGroups, notRunPatch, OPTION_GROUPS, OptionGroup, runsFeature,
  serviceAccountOk, startFeature,
  suggestNamespace, SV_NONE, svConfigured, unclaimedFuncIds,
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
  // Engine placement is detected on `!= null`, not truthiness: {} and [] are
  // real settings ("engines take neither, even though crane does") and falsy,
  // so a truthy detect would collapse the group on a bundle that has them.
  ["sched", "engine_tolerations", []],
  ["sched", "engine_node_selector", {}],
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
      const patch = GROUP_BY_ID[id].disable(FULL, false);
      expect(Object.keys(patch).sort())
        .toEqual(Object.keys(patch).filter((k) => GROUP_BY_ID[id].keys.includes(k)).sort());
      const after = { ...FULL, ...patch };
      for (const k of Object.keys(FULL)) {
        if (!GROUP_BY_ID[id].keys.includes(k)) expect(after[k]).toEqual(FULL[k]);
      }
    });

  it("records the decision when the location demanded the group", () => {
    // The bug this fixes: SV came back on the moment it was switched off. The
    // location demands the group, `null` is "nobody answered", detectGroups
    // ORs the demand back in, and generate() refuses -- so an account whose
    // location carries both funcIds could not produce a performance bundle at
    // all. SV_NONE is the answer, and it has to be written by the switch,
    // because that click is the whole of the user saying it.
    expect(GROUP_BY_ID.sv.disable(FULL, true)).toEqual({
      sv_ingress: SV_NONE, sv_subdomain: null, sv_tls_secret: null,
      sv_istio_gateway: null,
    });
    const after = { ...FULL, ...GROUP_BY_ID.sv.disable(FULL, true) };
    expect(detectGroups(after, allGroupsOff(), { sv: false }).sv).toBe(false);
  });

  // The exact wipes, spelled out: "clears what it used to clear" is the whole
  // acceptance test of this refactor, and a generic assertion cannot state it.
  // `required` false throughout: that is every group but SV, and SV's other
  // answer is the test above.
  it("clears exactly what it cleared before", () => {
    const wipes: Record<GroupId, Options> = {
      registry: { private_registry: null, pull_secret: null, registry_auth: false },
      proxy: { proxy: null },
      ca: {
        ca_existing_configmap: null, ca_configmap_key: null,
        ca_bundle: null, ca_openshift_inject: false,
      },
      sched: { tolerations: null, node_selector: null,
               engine_tolerations: null, engine_node_selector: null },
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
    for (const g of OPTION_GROUPS) expect(g.disable(FULL, false)).toEqual(wipes[g.id]);
  });

  it("leaves service_type alone when service virtualization goes off", () => {
    // It never writes service_type in either direction now -- but a wipe that
    // reached it would still silently rewrite the user's choice, so pin it.
    expect(GROUP_BY_ID.sv.disable(FULL, false)).not.toHaveProperty("service_type");
  });

  it("re-detects nothing from what it left behind", () => {
    // The point of the wipe: an off group must not be dragged open again by the
    // effect that watches the options.
    for (const g of OPTION_GROUPS) {
      const after = { ...FULL, ...g.disable(FULL, false) };
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

describe("the split the configure step is built on", () => {
  it("puts every group in exactly one bucket", () => {
    const owned = OPTION_GROUPS.filter((g) => g.features.length);
    expect([...SHARED_GROUPS, ...owned].length).toBe(OPTION_GROUPS.length);
    // A group in both -- or in neither -- is a group on screen twice, or not at
    // all. That was the feature view's failure and it is structural now.
    for (const g of SHARED_GROUPS) expect(g.features).toEqual([]);
  });

  it("gives a feature the groups tagged with it, and only those", () => {
    expect(groupsOf("sv").map((g) => g.id)).toEqual(["sv"]);
    expect(groupsOf("performance").map((g) => g.id)).toEqual(["sizing"]);
  });

  it("answers a feature nothing is tagged with, rather than throwing", () => {
    // A backend that grows a feature before the frontend tags a group to it is
    // a card that says "nothing extra to configure" -- not a crash, and not a
    // missing card.
    expect(groupsOf("secrets")).toEqual([]);
  });

  it("keeps the shared groups shared", () => {
    expect(SHARED_GROUPS.map((g) => g.id))
      .toEqual(["registry", "proxy", "ca", "sched", "security"]);
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

// -- a feature the location does not run --------------------------------------
// #113. Stated on the page, configured nowhere. The three answers below have to
// stay three: run, not run, and nobody has said -- collapse the third into
// either and the page either takes the switches off a location that does run
// the feature, or claims an enablement no account has confirmed.

describe("which features a location runs", () => {
  it("takes manual mode's declaration, and nothing else", () => {
    // No account to read, so the declaration is the whole answer -- and it is
    // an answer, which is why this is never null.
    expect(enabledFeatures("manual", "performance", [])).toEqual(["performance"]);
    expect(enabledFeatures("manual", "sv", ["performance"])).toEqual(["sv"]);
    expect(enabledFeatures("manual", null, [])).toEqual([]);
  });

  it("keeps unanswered distinct from answered-none", () => {
    expect(enabledFeatures("connect", "performance", [])).toBe(null);
    expect(enabledFeatures("connect", "performance", ["sv"])).toEqual(["sv"]);
  });

  it("treats unanswered as running everything", () => {
    // The safe direction: a switch shown for a feature that turns out not to
    // apply is corrected the moment the account answers, where one hidden on a
    // guess leaves a location with nowhere to configure what it does run.
    expect(runsFeature(null, "sv")).toBe(true);
    expect(runsFeature(["performance"], "sv")).toBe(false);
    expect(runsFeature(["performance", "sv"], "sv")).toBe(true);
    expect(runsFeature([], "sv")).toBe(false);
  });
});

describe("options set for a feature the location does not run", () => {
  const perfOnly = ["performance"];

  it("clears them, through the group's own disable", () => {
    // The state a profile, a restored session or a location picked after the
    // form was filled in can all reach, and which no control on the page can
    // undo -- there is no SV switch on a location that does not run mocks.
    expect(notRunPatch({ sv_ingress: "nginx" }, perfOnly))
      .toEqual({ sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
                 sv_istio_gateway: null });
  });

  it("settles in one pass", () => {
    // Applying the patch has to make its own condition false, or the page's
    // effect writes forever -- the property sv.correction is held to as well.
    const o: Options = { sv_ingress: "nginx", sv_subdomain: "apps.x.com" };
    const once = { ...o, ...notRunPatch(o, perfOnly) };
    expect(notRunPatch(once, perfOnly)).toBe(null);
  });

  it("leaves the shared groups and the features that are run alone", () => {
    // Registry belongs to no feature, so no location is without it; sizing
    // belongs to performance, which this one runs.
    expect(notRunPatch({ private_registry: "reg.corp/bzm",
                         engine_cpu_limit: "2" }, perfOnly)).toBe(null);
  });

  it("clears nothing while nobody has answered", () => {
    // An account still being read must not have its options wiped on the way.
    expect(notRunPatch({ sv_ingress: "nginx" }, null)).toBe(null);
  });

  it("declines rather than clears where the location demands the feature", () => {
    // SV_NONE is the recorded decline of a location that runs mockServices --
    // an answer, not a configuration -- so `detect` is false and there is
    // nothing here to do even when the feature is not in `enabled`.
    expect(notRunPatch({ sv_ingress: SV_NONE }, perfOnly)).toBe(null);
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

  it("is finished once the location's demand has been declined", () => {
    // generate() accepts SV_NONE for a mockServices location, so blocking the
    // download on it would be the UI refusing what the backend allows -- and
    // `required` is still true here, because the location has not changed.
    expect(sv.incomplete?.({ sv_ingress: SV_NONE }, true)).toBe(false);
    expect(sv.incomplete?.({ sv_ingress: SV_NONE }, false)).toBe(false);
    // ...and no field of an ingress that is not configured can revive it.
    expect(sv.incomplete?.(
      { sv_ingress: SV_NONE, sv_subdomain: "", sv_tls_secret: "" },
      true, BACKENDS)).toBe(false);
  });

  it("does not treat the decline as a configuration", () => {
    // detect keeps the group closed for it, so an imported profile that
    // declined does not open a panel offering to configure what it declined --
    // and enable() has to pick a real backend, not echo the sentinel into the
    // select and leave it showing nginx over a value that is not nginx.
    expect(sv.detect({ sv_ingress: SV_NONE })).toBe(false);
    expect(sv.enable({ sv_ingress: SV_NONE })).toEqual({ sv_ingress: "nginx" });
    expect(svConfigured(SV_NONE)).toBe(false);
    expect(svConfigured("nginx")).toBe(true);
    expect(svConfigured(null)).toBe(false);
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

// -- what the configure step still needs -------------------------------------
// One derivation behind two things on screen: the tick beside step 2 and the
// line saying why it has none. The line used to be a fixed string naming a
// namespace, a service account and "any unfinished group" whatever the bundle
// was, so a docker bundle -- which has neither of the first two, by design --
// was told to fix two fields that are deliberately not on the page.

describe("configureBlockedBy", () => {
  /** Every option applies: a Kubernetes bundle. */
  const k8s = () => true;
  /** ...and one where the placement fields are not fields at all. */
  const docker = (k: string) =>
    !["namespace", "service_account_name", "service_account_create"].includes(k);
  const filled = { namespace: "blazemeter", service_account_name: "crane" };

  it("says nothing when nothing is outstanding", () => {
    // Empty is what ticks the step off, so this is the same assertion twice.
    expect(configureBlockedBy(filled, k8s, [])).toBe("");
  });

  it("names the placement fields a cluster bundle is missing", () => {
    expect(configureBlockedBy({}, k8s, [])).toBe(
      "a namespace and a service account first");
    expect(configureBlockedBy({ namespace: "ns" }, k8s, []))
      .toBe("a service account first");
  });

  it("never names a field this format does not have", () => {
    // The bug: both were named for a docker bundle, which has no namespace and
    // no ServiceAccount -- and the fields are not on screen to be corrected.
    expect(configureBlockedBy({}, docker, [])).toBe("");
    // ...and an unfinished group is still named, because that one is real.
    expect(configureBlockedBy({}, docker, [GROUP_BY_ID.sv]))
      .toBe("Service virtualization first");
  });

  it("asks the predicate rather than trusting a filled-in field", () => {
    // A docker bundle carries a namespace in its options -- the value is kept,
    // not wiped -- so "is it filled in" cannot answer "is it a field here".
    expect(configureBlockedBy({ namespace: "" }, docker, [])).toBe("");
  });

  it("names the group by the title on its own row", () => {
    // The sentence is a way back to a control, so it says what that control
    // says. A second wording here would be a second name for one row.
    expect(configureBlockedBy(filled, k8s, [GROUP_BY_ID.sv, GROUP_BY_ID.ca]))
      .toBe(`${GROUP_BY_ID.sv.title} and ${GROUP_BY_ID.ca.title} first`);
  });

  it("joins three the way a sentence does", () => {
    expect(configureBlockedBy({}, k8s, [GROUP_BY_ID.sv])).toBe(
      "a namespace, a service account and Service virtualization first");
  });
});
