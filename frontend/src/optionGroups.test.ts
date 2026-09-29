import { describe, expect, it } from "vitest";
import { FuncIdVocabulary, Functionality, Options } from "./api";
import {
  allGroupsOff, blockingGroups, configureBlockedBy, detectGroups,
  enabledFunctionalities, groupsOf,
  SHARED_GROUPS, functionalitiesOf, GROUP_BY_ID, GroupId,
  incompleteGroups, isOpenshift, notRunPatch, OPTION_GROUPS, OptionGroup,
  runsFunctionality,
  serviceAccountOk, startFunctionality,
  reservedList, reservedWhere,
  suggestNamespace, SV_NONE, svConfigured, toggleDeclared as declared,
  unclaimedFuncIds,
} from "./optionGroups";
import { RESERVED_ENV } from "./fixtures";

// A group's lifecycle is data in, data out: detect, the enable patch, the
// disable patch. Tested as data, with no DOM.

/** Every key that, set on its own, must open its group, with a triggering
 *  value. Written out rather than read off `keys`, since some owned keys do not
 *  detect (WRITE_ONLY). */
const DETECTS: [GroupId, string, unknown][] = [
  ["registry", "private_registry", "registry.corp.com/bzm"],
  ["registry", "pull_secret", "bzm-pull"],
  ["registry", "registry_auth", true],
  ["proxy", "proxy", { http: "http://proxy:3128" }],
  ["ca", "ca_existing_configmap", "corp-trust-bundle"],
  ["ca", "ca_bundle", "-----BEGIN CERTIFICATE-----"],
  ["ca", "ca_bundle_slot", true],
  ["ca", "ca_openshift_inject", true],
  ["sched", "tolerations", [{ key: "lifecycle" }]],
  ["sched", "node_selector", { pool: "loadtest" }],
  // `!= null`, not truthiness: {} and [] are real, falsy settings.
  ["sched", "engine_tolerations", []],
  ["sched", "engine_node_selector", {}],
  ["security", "use_secret", false],
  ["security", "cluster_rbac", true],
  ["security", "service_type", "NODEPORT"],
  // On by default, so only an explicit false is a departure.
  ["security", "restrict_engines", false],
  // Tri-state, so both booleans are a departure; `true` is the one worth pinning.
  ["security", "auto_update", true],
  ["sv", "sv_ingress", "nginx"],
  // Every docker SV key detects: a hostname alone, or a certificate alone, is
  // a real configuration.
  ["svDocker", "sv_hostname", "C123ABCXYZ"],
  ["svDocker", "sv_tls_cert", "-----BEGIN CERTIFICATE-----"],
  ["svDocker", "sv_tls_key", "-----BEGIN PRIVATE KEY-----"],
];

/** Owned and written, but never a reason to open the group on its own. Listed
 *  so that every declared key is accounted for by one table or the other. */
const WRITE_ONLY: [GroupId, string][] = [
  // Only meaningful beside ca_existing_configmap, which does the detecting.
  ["ca", "ca_configmap_key"],
  // Names the file the file mode reads; `ca_bundle_slot` is what turns it on.
  ["ca", "ca_cert_file"],
  // Without an ingress this is not an SV configuration.
  ["sv", "sv_subdomain"],
  ["sv", "sv_tls_secret"],
  ["sv", "sv_istio_gateway"],
];

/** Every group's keys set, plus two keys no group owns, which no disable may touch. */
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
  ca_bundle_slot: true,
  ca_cert_file: "corp-root.crt",
  ca_openshift_inject: true,
  sv_hostname: "C123ABCXYZ",
  sv_tls_cert: "-----BEGIN CERTIFICATE-----",
  sv_tls_key: "-----BEGIN PRIVATE KEY-----",
  tolerations: [{ key: "lifecycle" }],
  node_selector: { pool: "loadtest" },
  engine_cpu_limit: "4",
  engine_mem_limit: "16Gi",
  use_secret: false,
  cluster_rbac: true,
  service_type: "NODEPORT",
  restrict_engines: false,
  auto_update: false,
  extra_env: { PREFERRED_INTERFACE: "eth1" },
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
    // SV has no opinion on service_type; which backends publish over NODEPORT
    // is `incomplete`'s rule.
    const owners = OPTION_GROUPS.filter((g) => g.keys.includes("service_type"));
    expect(owners.map((g) => g.id)).toEqual(["security"]);
  });
});

describe("the cluster, which the posture is not", () => {
  it("reads the product rather than the UID posture", () => {
    // The SCC-friendly posture is used on vanilla Kubernetes too.
    expect(isOpenshift({ platform: "openshift", openshift_cluster: false }))
      .toBe(false);
    expect(isOpenshift({ platform: "k8s" })).toBe(false);
    expect(isOpenshift({ platform: "openshift", openshift_cluster: true }))
      .toBe(true);
  });

  it("reads absent as the default, which is off", () => {
    // Unanswered is no, as in the generator.
    expect(isOpenshift({ platform: "openshift" })).toBe(false);
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
    // ...and nothing else.
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
    // On a location that demands SV, off is recorded as SV_NONE, or the group
    // would come straight back on and generate() would refuse.
    expect(GROUP_BY_ID.sv.disable(FULL, true)).toEqual({
      sv_ingress: SV_NONE, sv_subdomain: null, sv_tls_secret: null,
      sv_istio_gateway: null,
    });
    const after = { ...FULL, ...GROUP_BY_ID.sv.disable(FULL, true) };
    expect(detectGroups(after, allGroupsOff(), { sv: false }).sv).toBe(false);
  });

  // The exact wipes, spelled out. `required` false throughout.
  it("clears exactly what it cleared before", () => {
    const wipes: Record<GroupId, Options> = {
      registry: { private_registry: null, pull_secret: null, registry_auth: false },
      proxy: { proxy: null },
      ca: {
        ca_existing_configmap: null, ca_configmap_key: null,
        ca_bundle: null, ca_bundle_slot: false, ca_cert_file: null,
        ca_openshift_inject: false,
      },
      sched: { tolerations: null, node_selector: null,
               engine_tolerations: null, engine_node_selector: null },
      security: { use_secret: true, cluster_rbac: false,
                  service_type: "CLUSTERIP", restrict_engines: true,
                  // Back to unset: the tri-state's default is null.
                  auto_update: null },
      sv: {
        sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
        sv_istio_gateway: null,
      },
      // No SV_NONE to record: nothing is refused over an empty hostname.
      svDocker: { sv_hostname: null, sv_tls_cert: null, sv_tls_key: null },
    };
    for (const g of OPTION_GROUPS) expect(g.disable(FULL, false)).toEqual(wipes[g.id]);
  });

  it("leaves service_type alone when service virtualization goes off", () => {
    // A wipe must never rewrite the user's service type.
    expect(GROUP_BY_ID.sv.disable(FULL, false)).not.toHaveProperty("service_type");
  });

  it("re-detects nothing from what it left behind", () => {
    // An off group is not dragged open again by detection.
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

  it("seeds an ingress and keeps the chosen service type", () => {
    // A chosen NODEPORT survives switching SV on.
    expect(GROUP_BY_ID.sv.enable({ service_type: "NODEPORT" }))
      .toEqual({ sv_ingress: "nginx" });
  });

  it("seeds nothing over an ingress that was already chosen", () => {
    // Empty patch, so the options keep their identity.
    expect(GROUP_BY_ID.sv.enable({ sv_ingress: "contour" })).toEqual({});
  });

  it("starts CA trust on the slot, which is complete the moment it is picked", () => {
    /** It lands on the file mode, which needs nothing typed to download. */
    const patch = GROUP_BY_ID.ca.enable({});
    expect(detectGroups({ ...patch }, allGroupsOff()).ca).toBe(true);
    expect(patch).toEqual({
      ca_existing_configmap: null, ca_configmap_key: null,
      ca_bundle: null, ca_bundle_slot: true, ca_cert_file: null,
      ca_openshift_inject: false,
    });
    expect(GROUP_BY_ID.ca.requires!({ ...patch })).toEqual([]);
  });

  it("does not carry a named ConfigMap into the mode that has no use for one", () => {
    /** Enabling sets every CA key for the file mode; the old ConfigMap name is
     *  not kept, since two modes at once is what the generator refuses. */
    expect(GROUP_BY_ID.ca.enable({ ca_existing_configmap: "corp", ca_configmap_key: "k" }))
      .toEqual({
        ca_existing_configmap: null, ca_configmap_key: null,
        ca_bundle: null, ca_bundle_slot: true, ca_cert_file: null,
        ca_openshift_inject: false,
      });
  });
});

// -- the served functionalities ---------------------------------------------
// Groups tag themselves with served functionality ids; the vocabulary below
// stands in for /api/functionalities, and "added" tests extend it the way the
// server would.

// A functionality id is its funcId.
const PERF: Functionality = {
  id: "performance", label: "Performance",
  hint: "load tests", namespace: "blazemeter", runs_engine: true,
};
const GUI: Functionality = {
  id: "functionalGui", label: "GUI Functional",
  hint: "browser tests", namespace: "blazemeter-gui", runs_engine: true,
};
const SV: Functionality = {
  id: "mockServices", label: "Service Virtualization",
  // No taurus engine: the one runs_engine false.
  hint: "virtual services", namespace: "blazemeter-sv", runs_engine: false,
};
const FUNCTIONALITIES = [PERF, GUI, SV];
/** A new functionality: one served entry and no frontend edit. */
const SECRETS: Functionality = {
  id: "secretsPrivateVault", label: "Secrets Private Vault",
  hint: "secrets from a vault", namespace: "blazemeter-vault",
  runs_engine: false,
};
/** funcIds this tool does not model, which real locations carry. */
const UNMODELLED = ["tdm", "dataPublisher", "delphix"];
/** funcIds the account no longer serves, still on older locations. */
const RETIRED = ["functionalApi", "sv-bridge"];
/** Browser pins of `functionalGui`; several, to catch a list-vs-set mistake. */
const PINS = ["chrome:default", "firefox:139", "safari:15"];
/** The account's funcId vocabulary: display names, `covered` false for what
 *  this tool has no options for, pins under their parent. NO_ACCOUNT is the
 *  keyless baseline. */
const VOCABULARY: FuncIdVocabulary = { source: "account", choices: [
  { id: "performance", label: "Performance", changes_images: true, covered: true,
    sub_func_ids: [] },
  { id: "functionalGui", label: "GUI Functional", changes_images: true, covered: true,
    sub_func_ids: PINS },
  { id: "mockServices", label: "Service Virtualization", changes_images: true,
    covered: true, sub_func_ids: [] },
  { id: "tdm", label: "TDM Integration", changes_images: false, covered: false,
    sub_func_ids: [] },
  { id: "dataPublisher", label: "Data Orchestration", changes_images: false,
    covered: false, sub_func_ids: [] },
  { id: "delphix", label: "Delphix Integration", changes_images: false, covered: false,
    sub_func_ids: [] },
] };
const NO_ACCOUNT: FuncIdVocabulary = {
  source: "baseline",
  choices: VOCABULARY.choices
    .filter((c) => c.covered).map((c) => ({ ...c, sub_func_ids: [] })),
};

describe("the split the configure step is built on", () => {
  it("puts every group in exactly one bucket", () => {
    const owned = OPTION_GROUPS.filter((g) => g.functionalities.length);
    expect([...SHARED_GROUPS, ...owned].length).toBe(OPTION_GROUPS.length);
    // Every group is in exactly one bucket.
    for (const g of SHARED_GROUPS) expect(g.functionalities).toEqual([]);
  });

  it("gives a functionality the groups tagged with it, and only those", () => {
    // Tagged with the funcId (`sv` is only the group id). Two groups, one per
    // platform, never on screen together.
    expect(groupsOf("mockServices").map((g) => g.id)).toEqual(["sv", "svDocker"]);
    // The engine size is stated, not a group.
    expect(groupsOf("performance")).toEqual([]);
    expect(groupsOf("functionalGui")).toEqual([]);
  });

  it("answers a functionality nothing is tagged with, rather than throwing", () => {
    // A functionality nothing tags yet has no groups, and its card says so.
    expect(groupsOf("secretsPrivateVault")).toEqual([]);
  });

  it("keeps the shared groups shared", () => {
    expect(SHARED_GROUPS.map((g) => g.id))
      .toEqual(["registry", "proxy", "ca", "sched", "security"]);
  });
});

describe("which functionality a location starts on", () => {
  it("picks the functionality its funcIds carry", () => {
    expect(startFunctionality(["mockServices"], FUNCTIONALITIES))
      .toBe("mockServices");
    // A GUI Functional location opens on GUI Functional.
    expect(startFunctionality(["functionalGui"], FUNCTIONALITIES))
      .toBe("functionalGui");
  });

  it("picks the first served functionality for a location carrying both", () => {
    // A location doing both opens on performance.
    expect(startFunctionality(["mockServices", "performance"], FUNCTIONALITIES))
      .toBe("performance");
    expect(functionalitiesOf(["mockServices", "performance"], FUNCTIONALITIES))
      .toEqual(["performance", "mockServices"]);
  });

  it("is not broken by a funcId the tool does not model", () => {
    // Unmodelled funcIds claim nothing: ignored beside a modelled one, the
    // default when alone.
    expect(startFunctionality([...UNMODELLED, "mockServices"], FUNCTIONALITIES))
      .toBe("mockServices");
    expect(startFunctionality(UNMODELLED, FUNCTIONALITIES)).toBe("performance");
    expect(functionalitiesOf(UNMODELLED, FUNCTIONALITIES)).toEqual([]);
    expect(unclaimedFuncIds([...UNMODELLED, "performance"], FUNCTIONALITIES,
                            VOCABULARY).uncovered)
      .toHaveLength(UNMODELLED.length);
    expect(unclaimedFuncIds(["performance", "mockServices"], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: [], retired: [] });
    expect(startFunctionality([], FUNCTIONALITIES)).toBe("performance");
    expect(startFunctionality(undefined, FUNCTIONALITIES)).toBe("performance");
  });

  it("leaves a retired funcId unclaimed rather than folding it into a card", () => {
    // Neither is covered, so a location carrying only these claims nothing.
    expect(functionalitiesOf(["functionalApi", "proxyRecorder"], FUNCTIONALITIES))
      .toEqual([]);
    expect(unclaimedFuncIds(["performance", "functionalApi"], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: [], retired: ["functionalApi"] });
  });

  it("names the funcIds it has no options for, in the account's own words", () => {
    // Named in BlazeMeter's words, since silence would read as coverage.
    expect(unclaimedFuncIds([...UNMODELLED, "performance"], FUNCTIONALITIES,
                            VOCABULARY).uncovered)
      .toEqual(["TDM Integration", "Data Orchestration", "Delphix Integration"]);
  });

  it("tells a funcId the account retired from one it never had options for", () => {
    // Served-but-unconfigured and retired are two answers, told apart on
    // `source`. Retired ones keep their raw id.
    expect(unclaimedFuncIds([...RETIRED, "tdm", "performance"], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: ["TDM Integration"], retired: RETIRED });
    expect(unclaimedFuncIds(["functionalApi", "tdm"], [SV], VOCABULARY))
      .toEqual({ uncovered: ["TDM Integration"], retired: ["functionalApi"] });
  });

  it("never names a browser pin, whether or not its parent is covered", () => {
    // Browser pins are parameters of `functionalGui`, not unclaimed funcIds.
    expect(unclaimedFuncIds(["functionalGui", ...PINS], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: [], retired: [] });
    // ...also when the parent is uncovered.
    expect(unclaimedFuncIds(["functionalGui", ...PINS, "sv-bridge"], [SV],
                            VOCABULARY))
      .toEqual({ uncovered: ["GUI Functional"], retired: ["sv-bridge"] });
  });

  it("says nothing at all where no account has been read", () => {
    // The keyless baseline cannot tell pins, unmodelled and retired apart, so
    // it names nothing.
    expect(unclaimedFuncIds([...UNMODELLED, ...PINS, ...RETIRED], FUNCTIONALITIES,
                            NO_ACCOUNT))
      .toEqual({ uncovered: [], retired: [] });
    // `source` says which vocabulary this is.
    expect(NO_ACCOUNT.source).toBe("baseline");
  });

  it("offers a functionality added to the vocabulary, with no change here", () => {
    // A new served functionality is where a location using it starts.
    const served = [...FUNCTIONALITIES, SECRETS];
    expect(startFunctionality(["secretsPrivateVault"], served))
      .toBe("secretsPrivateVault");
    expect(functionalitiesOf(["secretsPrivateVault", "performance"], served))
      .toEqual(["performance", "secretsPrivateVault"]);
  });

  it("claims nothing when the vocabulary has not arrived", () => {
    expect(startFunctionality(["performance"], [])).toBe(null);
  });
});

// -- a functionality the location does not run --------------------------------
// Three answers that must stay three: runs, does not run, nobody has said.

describe("which functionalities a location runs", () => {
  const ORDER = ["performance", "functionalGui", "mockServices"];
  /** `toggleDeclared` with nothing excluded: exclusion is sv.ts's rule, tested there. */
  const toggleDeclared = (d: string[], id: string, on: boolean, o: string[]) =>
    declared(d, id, on, o, () => []);

  it("takes manual mode's declaration, and nothing else", () => {
    // Manual entry's declaration is always an answer, never null.
    expect(enabledFunctionalities("manual", ["performance"], []))
      .toEqual(["performance"]);
    expect(enabledFunctionalities("manual", ["mockServices"], ["performance"]))
      .toEqual(["mockServices"]);
    expect(enabledFunctionalities("manual", [], [])).toEqual([]);
  });

  it("carries every functionality manual entry declared, not the first", () => {
    // A declaration can name several functionalities.
    expect(enabledFunctionalities(
      "manual", ["performance", "functionalGui"], []))
      .toEqual(["performance", "functionalGui"]);
  });

  it("ticks and unticks a member, in the order the boxes are drawn", () => {
    // Kept in served order, so a tick does not reshuffle the funcIds.
    expect(toggleDeclared(["functionalGui"], "performance", true, ORDER))
      .toEqual(["performance", "functionalGui"]);
    expect(toggleDeclared(["performance", "functionalGui"], "performance",
                          false, ORDER)).toEqual(["functionalGui"]);
    // Ticking what is already ticked is not a second copy of it.
    expect(toggleDeclared(["performance"], "performance", true, ORDER))
      .toEqual(["performance"]);
    // Emptying is allowed.
    expect(toggleDeclared(["performance"], "performance", false, ORDER))
      .toEqual([]);
  });

  it("keeps an id the vocabulary on screen does not carry", () => {
    // An id the order lacks (a retired funcId) is kept.
    expect(toggleDeclared(["functionalApi"], "performance", true,
                          ["performance", "mockServices"]))
      .toEqual(["performance", "functionalApi"]);
  });

  it("keeps unanswered distinct from answered-none", () => {
    expect(enabledFunctionalities("connect", ["performance"], [])).toBe(null);
    expect(enabledFunctionalities("connect", ["performance"], ["mockServices"]))
      .toEqual(["mockServices"]);
  });

  it("treats unanswered as running everything", () => {
    // Unanswered reads as yes.
    expect(runsFunctionality(null, "mockServices")).toBe(true);
    expect(runsFunctionality(["performance"], "mockServices")).toBe(false);
    expect(runsFunctionality(["performance", "mockServices"], "mockServices"))
      .toBe(true);
    expect(runsFunctionality([], "mockServices")).toBe(false);
  });
});

describe("options set for a functionality the location does not run", () => {
  const perfOnly = ["performance"];

  it("clears them, through the group's own disable", () => {
    // A state the options reach that no control on the page can undo.
    expect(notRunPatch({ sv_ingress: "nginx" }, perfOnly))
      .toEqual({ sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
                 sv_istio_gateway: null });
  });

  it("settles in one pass", () => {
    // Applying the patch makes the next one null.
    const o: Options = { sv_ingress: "nginx", sv_subdomain: "apps.x.com" };
    const once = { ...o, ...notRunPatch(o, perfOnly) };
    expect(notRunPatch(once, perfOnly)).toBe(null);
  });

  it("leaves the shared groups alone", () => {
    // A shared group is never cleared.
    expect(notRunPatch({ private_registry: "reg.corp/bzm" }, perfOnly))
      .toBe(null);
  });

  it("never clears the pod limits, whatever the location runs", () => {
    // The engine limits reach every pod crane creates, so they are never
    // cleared for a functionality.
    const sized = { engine_cpu_limit: "2", engine_mem_limit: "8Gi" };
    expect(notRunPatch(sized, ["mockServices"])).toBe(null);
    expect(notRunPatch(sized, ["functionalGui"])).toBe(null);
    expect(notRunPatch(sized, [])).toBe(null);
    expect(notRunPatch(sized, null)).toBe(null);
    // ...while that functionality's own options still go.
    expect(notRunPatch({ ...sized, sv_ingress: "nginx" }, perfOnly))
      .toEqual({ sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
                 sv_istio_gateway: null });
  });

  it("clears nothing while nobody has answered", () => {
    // An account still being read clears nothing.
    expect(notRunPatch({ sv_ingress: "nginx" }, null)).toBe(null);
  });

  it("declines rather than clears where the location demands the functionality", () => {
    // SV_NONE is an answer, not a configuration: nothing to clear.
    expect(notRunPatch({ sv_ingress: SV_NONE }, perfOnly)).toBe(null);
  });
});

describe("the suggested namespace", () => {
  it("suggests the functionality's namespace when the field is empty", () => {
    expect(suggestNamespace("", SV, FUNCTIONALITIES)).toBe("blazemeter-sv");
    expect(suggestNamespace("   ", SV, FUNCTIONALITIES)).toBe("blazemeter-sv");
  });

  it("replaces a namespace another functionality suggested", () => {
    expect(suggestNamespace("blazemeter", SV, FUNCTIONALITIES)).toBe("blazemeter-sv");
    expect(suggestNamespace("blazemeter-sv", PERF, FUNCTIONALITIES)).toBe("blazemeter");
  });

  it("never overwrites something typed", () => {
    expect(suggestNamespace("bzm-prod", SV, FUNCTIONALITIES)).toBe(null);
    expect(suggestNamespace("blazemeter2", SV, FUNCTIONALITIES)).toBe(null);
  });

  it("suggests nothing when the namespace is already the suggestion", () => {
    // The same value back would be a pointless write.
    expect(suggestNamespace("blazemeter-sv", SV, FUNCTIONALITIES)).toBe(null);
    expect(suggestNamespace(" blazemeter-sv ", SV, FUNCTIONALITIES)).toBe(null);
  });

  it("counts a namespace suggested by a functionality added later", () => {
    // What counts as suggested is read off the served list.
    const served = [...FUNCTIONALITIES, SECRETS];
    expect(suggestNamespace("blazemeter-vault", SV, served)).toBe("blazemeter-sv");
    expect(suggestNamespace("blazemeter", SECRETS, served)).toBe("blazemeter-vault");
  });
});


// -- completeness is the group's own business ---------------------------------

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
    // generate() accepts SV_NONE, so it does not block, even when required.
    expect(sv.incomplete?.({ sv_ingress: SV_NONE }, true)).toBe(false);
    expect(sv.incomplete?.({ sv_ingress: SV_NONE }, false)).toBe(false);
    // ...and no field of an ingress that is not configured can revive it.
    expect(sv.incomplete?.(
      { sv_ingress: SV_NONE, sv_subdomain: "", sv_tls_secret: "" },
      true, BACKENDS)).toBe(false);
  });

  it("does not treat the decline as a configuration", () => {
    // A decline keeps the group closed, and enabling picks a real backend.
    expect(sv.detect({ sv_ingress: SV_NONE })).toBe(false);
    expect(sv.enable({ sv_ingress: SV_NONE })).toEqual({ sv_ingress: "nginx" });
    expect(svConfigured(SV_NONE)).toBe(false);
    expect(svConfigured("nginx")).toBe(true);
    expect(svConfigured(null)).toBe(false);
  });

  // Shape-only backend names: the real table is pinned in test_server.py.
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
    // Unknown backend: not evidence of a conflict, so it does not block.
    expect(sv.incomplete?.(withNodePort("does_not"), false)).toBe(false);
    expect(sv.incomplete?.(withNodePort("does_not"), false, {})).toBe(false);
  });

  it("still blocks on an empty field whatever the service type", () => {
    expect(sv.incomplete?.(
      { ...withNodePort("publishes"), sv_tls_secret: "" }, false, BACKENDS)).toBe(true);
  });

  it("groups with no completeness rule never block", () => {
    // SV is the only group with an `incomplete` rule.
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
  // An empty service account name is not usable.

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
    // Before the defaults land there is no name either.
    expect(serviceAccountOk({})).toBe(false);
  });

  it("does not belong to any option group", () => {
    // The fields are not any group's keys, so no group hides them.
    const owned = OPTION_GROUPS.flatMap((g: OptionGroup) => g.keys);
    expect(owned).not.toContain("service_account_name");
    expect(owned).not.toContain("service_account_create");
  });
});

// -- what the configure step still needs -------------------------------------
// A blank field is a marker, not a blocker; what is left is a group whose state
// generate() refuses and an env name no process could read. placeholder.test.ts
// covers the blank fields.

describe("configureBlockedBy", () => {
  it("says nothing when nothing is outstanding", () => {
    // Empty ticks the step off.
    expect(configureBlockedBy({}, [])).toBe("");
  });

  it("does not block on a field that is merely empty", () => {
    // Blank namespace and service account do not block.
    expect(configureBlockedBy({ namespace: "", service_account_name: "" }, []))
      .toBe("");
  });

  it("names the group by the title on its own row", () => {
    // The sentence names the rows by their titles.
    expect(configureBlockedBy({}, [GROUP_BY_ID.sv, GROUP_BY_ID.ca]))
      .toBe(`${GROUP_BY_ID.sv.title} and ${GROUP_BY_ID.ca.title} first`);
  });

  it("names an environment variable no process could read", () => {
    // A bad name, unlike a blank field, still blocks.
    expect(configureBlockedBy({ extra_env: { "not a name": "v" } }, []))
      .toBe("the environment variables first");
  });

  it("joins three the way a sentence does", () => {
    expect(configureBlockedBy(
      { extra_env: { "not a name": "v" } },
      [GROUP_BY_ID.sv, GROUP_BY_ID.ca],
    )).toBe(`${GROUP_BY_ID.sv.title}, ${GROUP_BY_ID.ca.title} `
      + "and the environment variables first");
  });
});

// -- which groups stop the step, and which only look unfinished ---------------

describe("blockingGroups", () => {
  it("lets an SV group with an empty subdomain past", () => {
    // Still `incomplete` on its row, but not blocking.
    const o = { sv_ingress: "nginx", sv_subdomain: "", sv_tls_secret: "" };
    expect(incompleteGroups(o, { sv: true })).toHaveLength(1);
    expect(blockingGroups(o, { sv: true })).toEqual([]);
  });

  it("still stops on a question nobody answered", () => {
    // No ingress on a mockServices location: generate() refuses it.
    expect(blockingGroups({}, { sv: true })).toHaveLength(1);
  });

  it("still stops on two answers that contradict each other", () => {
    const o = { sv_ingress: "contour", sv_subdomain: "a.example.com",
                sv_tls_secret: "wild", service_type: "NODEPORT" };
    const backends = { contour: { nodeport_ok: false } };
    expect(blockingGroups(o, { sv: true }, backends)).toHaveLength(1);
  });
});

// -- where a variable this bundle writes itself is set ------------------------

describe("reservedWhere", () => {
  it("names the option, and the section of the step holding it", () => {
    // A variable the bundle writes itself names its option and that option's
    // section.
    expect(reservedWhere("AUTO_KUBERNETES_UPDATE", RESERVED_ENV)).toEqual({
      name: "AUTO_KUBERNETES_UPDATE", owner: "auto_update",
      where: "Security & RBAC",
    });
    expect(reservedWhere("IMAGE_OVERRIDES", RESERVED_ENV)).toEqual({
      name: "IMAGE_OVERRIDES", owner: "private_registry",
      where: "Private registry",
    });
  });

  it("names an owner without inventing a place for it", () => {
    // An option no group owns has an owner and no section.
    expect(reservedWhere("KUBERNETES_RESOURCES_LIMITS_CPU", RESERVED_ENV))
      .toEqual({ name: "KUBERNETES_RESOURCES_LIMITS_CPU",
                 owner: "engine_cpu_limit", where: null });
  });

  it("follows a one-of pair to the group that holds both", () => {
    // A one-of owner is split, and resolves to the group holding both.
    expect(reservedWhere("REQUESTS_CA_BUNDLE", RESERVED_ENV)?.where)
      .toBe("Custom CA trust");
  });

  it("keeps 'no option owns it' apart from 'this name is not reserved'", () => {
    // A null owner is a real answer: no option to send somebody to.
    expect(reservedWhere("SHIP_ID", RESERVED_ENV))
      .toEqual({ name: "SHIP_ID", owner: null, where: null });
    // A name the table does not carry is not reserved.
    expect(reservedWhere("VERIFY_SSL", RESERVED_ENV)).toBe(null);
    expect(reservedWhere("AUTO_KUBERNETES_UPDATE", {})).toBe(null);
  });

  it("lists every reserved name, in the order the table is served in", () => {
    // Every reserved name, for a list the browser's find can search.
    const all = reservedList(RESERVED_ENV);
    expect(all.map((r) => r.name)).toEqual(Object.keys(RESERVED_ENV));
    expect(all.find((r) => r.name === "AUTO_KUBERNETES_UPDATE")?.where)
      .toBe("Security & RBAC");
    expect(reservedList({})).toEqual([]);
  });
});
