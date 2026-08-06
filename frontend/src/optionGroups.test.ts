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
    // The SV group co-owned it to force CLUSTERIP unconditionally. #60 ran all
    // four backends: two publish fine over NODEPORT and two do not, so the rule
    // is per-backend now and lives in `incomplete`, not in a second writer.
    const owners = OPTION_GROUPS.filter((g) => g.keys.includes("service_type"));
    expect(owners.map((g) => g.id)).toEqual(["security"]);
  });
});

describe("the cluster, which the posture is not", () => {
  it("reads the product rather than the UID posture", () => {
    // The SCC-friendly posture is recommended on vanilla Kubernetes too, so it
    // was answering "is this OpenShift?" yes for every bundle that took the
    // default -- which is what put `oc` in a plain Kubernetes customer's README
    // and offered them a trust-injection ConfigMap nothing would ever fill.
    expect(isOpenshift({ platform: "openshift", openshift_cluster: false }))
      .toBe(false);
    expect(isOpenshift({ platform: "k8s" })).toBe(false);
    expect(isOpenshift({ platform: "openshift", openshift_cluster: true }))
      .toBe(true);
  });

  it("reads absent as the default, which is on", () => {
    // Boolean() here would read every bundle generated before the option
    // existed -- and every one still being typed -- as plain Kubernetes, taking
    // two controls off screen that were being offered a moment earlier.
    expect(isOpenshift({ platform: "openshift" })).toBe(true);
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

// -- the functionality view ---------------------------------------------------
// One functionality is configured at a time, chosen from the list
// /api/functionalities serves.
// Nothing in the frontend enumerates functionalities: groups tag themselves with the
// functionality ids they belong to, and everything else -- labels, suggested
// namespaces, which funcIds mean which functionality -- is read off the served list.
// So the vocabulary below is a fixture standing in for that response, and the
// "a functionality was added" tests extend it exactly the way server.py would.

// One entry per covered funcId, and the id *is* the funcId (#149) -- so a
// location's funcIds join to these by equality, with no table in between.
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
  // No taurus engine at all: crane, group-gateway and service-mock. The one
  // false in the served vocabulary, and what `engineFunctionalities` filters on.
  hint: "virtual services", namespace: "blazemeter-sv", runs_engine: false,
};
const FUNCTIONALITIES = [PERF, GUI, SV];
/** Added the way a real new functionality is: one entry in the served vocabulary, and
 *  no frontend edit at all until some group wants to be tagged with it. */
const SECRETS: Functionality = {
  id: "secretsPrivateVault", label: "Secrets Private Vault",
  hint: "secrets from a vault", namespace: "blazemeter-vault",
  runs_engine: false,
};
/** funcIds the tool does not model. Real locations carry them today. */
const UNMODELLED = ["tdm", "dataPublisher", "delphix"];
/** funcIds no account serves any more, still carried by the locations created
 *  before they were retired -- 43 and 62 of one account's 171. */
const RETIRED = ["functionalApi", "sv-bridge"];
/** Three of `functionalGui`'s 117 browser pins. Several, not one: a reader
 *  keeping them in a list rather than a set looks right on one. */
const PINS = ["chrome:default", "firefox:139", "safari:15"];
/** The funcId vocabulary /api/func-ids serves once an account has been read --
 *  the account's own display names, `covered` false for every funcId this tool
 *  has no options for, and the browser pins under the parent they are a
 *  parameter of. Keyless it is the three covered ones with no pins, which is
 *  what NO_ACCOUNT stands in for. */
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
    // A group in both -- or in neither -- is a group on screen twice, or not at
    // all. That was the functionality view's failure and it is structural now.
    for (const g of SHARED_GROUPS) expect(g.functionalities).toEqual([]);
  });

  it("gives a functionality the groups tagged with it, and only those", () => {
    // Tagged with the funcId, because that is what a functionality id is now.
    // `sv` is still the *group* id -- what the row is called on the page -- and
    // the two no longer coincide, which is the point: one is a bundle's
    // options, the other is a thing the account enables.
    expect(groupsOf("mockServices").map((g) => g.id)).toEqual(["sv"]);
    // Performance owns no group any more: the engine size stopped being one
    // (#132) -- it derives from the location, and the panel states it.
    expect(groupsOf("performance")).toEqual([]);
    expect(groupsOf("functionalGui")).toEqual([]);
  });

  it("answers a functionality nothing is tagged with, rather than throwing", () => {
    // A backend that grows a functionality before the frontend tags a group to it is
    // a card that says "nothing extra to configure" -- not a crash, and not a
    // missing card.
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
    // The reported bug, in one line: this used to answer "performance",
    // because one entry claimed four funcIds and its label had to name them
    // all. A GUI Functional location now opens on GUI Functional.
    expect(startFunctionality(["functionalGui"], FUNCTIONALITIES))
      .toBe("functionalGui");
  });

  it("picks the first served functionality for a location carrying both", () => {
    // Deliberate: a location doing both is a performance location that also
    // serves mocks, and the download-button block routes to the SV settings
    // when they are what is missing.
    expect(startFunctionality(["mockServices", "performance"], FUNCTIONALITIES))
      .toBe("performance");
    expect(functionalitiesOf(["mockServices", "performance"], FUNCTIONALITIES))
      .toEqual(["performance", "mockServices"]);
  });

  it("is not broken by a funcId the tool does not model", () => {
    // Real locations carry tdm/dataPublisher/delphix today. An unmodelled
    // funcId claims no functionality: alongside a modelled one it is ignored, and
    // alone it leaves the default rather than an empty selector.
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
    // `functionalApi` and `proxyRecorder` were two of the four `performance`
    // used to claim (#149). Neither is covered -- BlazeMeter retired one and
    // this tool has options for neither -- so they are named beside the cards
    // instead, which is the same answer tdm has always got. A location
    // carrying only those claims nothing, and an empty answer is read one
    // level up as nobody having said.
    expect(functionalitiesOf(["functionalApi", "proxyRecorder"], FUNCTIONALITIES))
      .toEqual([]);
    expect(unclaimedFuncIds(["performance", "functionalApi"], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: [], retired: ["functionalApi"] });
  });

  it("names the funcIds it has no options for, in the account's own words", () => {
    // Silence would read as coverage. The location runs these, this tool
    // configures none of them, and BlazeMeter has a name for each -- so the
    // sentence on the configure step is "also runs TDM Integration", not a
    // camelCase id the reader has to recognise.
    expect(unclaimedFuncIds([...UNMODELLED, "performance"], FUNCTIONALITIES,
                            VOCABULARY).uncovered)
      .toEqual(["TDM Integration", "Data Orchestration", "Delphix Integration"]);
  });

  it("tells a funcId the account retired from one it never had options for", () => {
    // Two sentences, because they are two answers (#160). `tdm` is served by
    // the account and configured nowhere here; `sv-bridge` is not served at
    // all, and the only way a location has one is that it predates the
    // removal. Told apart on `source`: with the account's own vocabulary in
    // hand, absent *is* retired, and there is no third case -- a funcId
    // nobody ever served cannot get onto a location.
    //
    // Retired ones keep their raw id, and honestly: the display name is the
    // account's, and the account no longer has a row to read one off.
    expect(unclaimedFuncIds([...RETIRED, "tdm", "performance"], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: ["TDM Integration"], retired: RETIRED });
    expect(unclaimedFuncIds(["functionalApi", "tdm"], [SV], VOCABULARY))
      .toEqual({ uncovered: ["TDM Integration"], retired: ["functionalApi"] });
  });

  it("never names a browser pin, whether or not its parent is covered", () => {
    // The reported bug (#160). A pin is a *parameter* of `functionalGui` --
    // which browser it runs -- not a capability of its own, and 43% of one
    // account's locations carry at least one, 41 on the worst. Tested against
    // the top-level vocabulary alone every one of them fell through as
    // something this tool has no options for, and buried the two funcIds the
    // sentence exists for underneath.
    expect(unclaimedFuncIds(["functionalGui", ...PINS], FUNCTIONALITIES,
                            VOCABULARY))
      .toEqual({ uncovered: [], retired: [] });
    // ...and with the parent uncovered too: the pin is a parameter of the
    // parent whether or not this tool configures the parent, so a page that
    // named the parent once and its pins 41 times would be no better.
    expect(unclaimedFuncIds(["functionalGui", ...PINS, "sv-bridge"], [SV],
                            VOCABULARY))
      .toEqual({ uncovered: ["GUI Functional"], retired: ["sv-bridge"] });
  });

  it("says nothing at all where no account has been read", () => {
    // The keyless vocabulary is the three covered funcIds and no pins, so
    // nothing here can tell `tdm` from `chrome:default` from a funcId the
    // account retired -- and the honest answer to a question nobody has read
    // the answer to is silence, which is the direction DOCKER_IGNORED and the
    // reserved-env table already take. It used to name the raw funcIds, which
    // is how a GUI Functional location got 41 lines of browser.
    expect(unclaimedFuncIds([...UNMODELLED, ...PINS, ...RETIRED], FUNCTIONALITIES,
                            NO_ACCOUNT))
      .toEqual({ uncovered: [], retired: [] });
    // Empty is not "there is nothing there" here, and nothing has to remember
    // which: `source` is on the answer the vocabulary came in.
    expect(NO_ACCOUNT.source).toBe("baseline");
  });

  it("offers a functionality added to the vocabulary, with no change here", () => {
    // The acceptance test of "adding a functionality is a backend change": the
    // vocabulary gains an entry and the location starts on it.
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
// #113. Stated on the page, configured nowhere. The three answers below have to
// stay three: run, not run, and nobody has said -- collapse the third into
// either and the page either takes the switches off a location that does run
// the functionality, or claims an enablement no account has confirmed.

describe("which functionalities a location runs", () => {
  const ORDER = ["performance", "functionalGui", "mockServices"];
  /** `toggleDeclared` with nothing excluding anything, on purpose: which
   *  functionalities cannot share a location is a fact about crane's one
   *  pod-limit pair, stated and tested in sv.ts. This file owns only the list
   *  mechanics -- added once, removed, and in the order the boxes are drawn. */
  const toggleDeclared = (d: string[], id: string, on: boolean, o: string[]) =>
    declared(d, id, on, o, () => []);

  it("takes manual mode's declaration, and nothing else", () => {
    // No account to read, so the declaration is the whole answer -- and it is
    // an answer, which is why this is never null.
    expect(enabledFunctionalities("manual", ["performance"], []))
      .toEqual(["performance"]);
    expect(enabledFunctionalities("manual", ["mockServices"], ["performance"]))
      .toEqual(["mockServices"]);
    expect(enabledFunctionalities("manual", [], [])).toEqual([]);
  });

  it("carries every functionality manual entry declared, not the first", () => {
    // #151. A location running performance and GUI functional together is 71 of
    // 168 in one real account, so a declaration that could only name one made a
    // bundle nobody would create -- and the card for the other one was on
    // screen saying it had not been declared.
    expect(enabledFunctionalities(
      "manual", ["performance", "functionalGui"], []))
      .toEqual(["performance", "functionalGui"]);
  });

  it("ticks and unticks a member, in the order the boxes are drawn", () => {
    // Added in served order rather than in click order: these ids become the
    // funcIds the facts are gathered for, and a list that reshuffles on a tick
    // is a new request for a declaration that did not change.
    expect(toggleDeclared(["functionalGui"], "performance", true, ORDER))
      .toEqual(["performance", "functionalGui"]);
    expect(toggleDeclared(["performance", "functionalGui"], "performance",
                          false, ORDER)).toEqual(["functionalGui"]);
    // Ticking what is already ticked is not a second copy of it.
    expect(toggleDeclared(["performance"], "performance", true, ORDER))
      .toEqual(["performance"]);
    // Emptying is a state, not a refusal: a checkbox that will not untick is an
    // off-screen blocker in one control, and the surface says what empty means.
    expect(toggleDeclared(["performance"], "performance", false, ORDER))
      .toEqual([]);
  });

  it("keeps an id the vocabulary on screen does not carry", () => {
    // The create-location form offers the account's whole funcId list, and a
    // location can already hold one BlazeMeter has retired -- 43 in one account
    // still carry functionalApi. Dropping it here would be a form editing
    // something it never showed.
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
    // The safe direction: a switch shown for a functionality that turns out not to
    // apply is corrected the moment the account answers, where one hidden on a
    // guess leaves a location with nowhere to configure what it does run.
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

  it("leaves the shared groups alone", () => {
    // Registry belongs to no functionality, so no location is without it.
    expect(notRunPatch({ private_registry: "reg.corp/bzm" }, perfOnly))
      .toBe(null);
  });

  it("never clears the pod limits, whatever the location runs", () => {
    // #149. They were cleared for a location that ran no performance, on the
    // reading that they size an engine and a mocks location has none. Crane
    // applies KUBERNETES_RESOURCES_LIMITS_CPU/_MEMORY to every pod it creates
    // -- there is one pair and no per-functionality second one -- so cleared,
    // an SV-only or GUI-only agent's pods land on crane's 250m/256Mi defaults,
    // which is the silent failure this repo's LimitRange note is about.
    const sized = { engine_cpu_limit: "2", engine_mem_limit: "8Gi" };
    expect(notRunPatch(sized, ["mockServices"])).toBe(null);
    expect(notRunPatch(sized, ["functionalGui"])).toBe(null);
    expect(notRunPatch(sized, [])).toBe(null);
    expect(notRunPatch(sized, null)).toBe(null);
    // ...and a functionality's own options still go, in the same options dict:
    // the two answers are separate, which is what stopped being true.
    expect(notRunPatch({ ...sized, sv_ingress: "nginx" }, perfOnly))
      .toEqual({ sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
                 sv_istio_gateway: null });
  });

  it("clears nothing while nobody has answered", () => {
    // An account still being read must not have its options wiped on the way.
    expect(notRunPatch({ sv_ingress: "nginx" }, null)).toBe(null);
  });

  it("declines rather than clears where the location demands the functionality", () => {
    // SV_NONE is the recorded decline of a location that runs mockServices --
    // an answer, not a configuration -- so `detect` is false and there is
    // nothing here to do even when the functionality is not in `enabled`.
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
    // Same value back would still be a state write, and every option change
    // re-POSTs the preview.
    expect(suggestNamespace("blazemeter-sv", SV, FUNCTIONALITIES)).toBe(null);
    expect(suggestNamespace(" blazemeter-sv ", SV, FUNCTIONALITIES)).toBe(null);
  });

  it("counts a namespace suggested by a functionality added later", () => {
    // The rule is "still holding a suggested value", read off the served list
    // -- not a list of names in the frontend, which is what would go stale.
    const served = [...FUNCTIONALITIES, SECRETS];
    expect(suggestNamespace("blazemeter-vault", SV, served)).toBe("blazemeter-sv");
    expect(suggestNamespace("blazemeter", SECRETS, served)).toBe("blazemeter-vault");
  });
});


// -- completeness is the group's own business ---------------------------------
// The download used to be gated by a rule the page held about one group. That
// is the promise "adding a functionality needs no frontend change" quietly breaking:
// the second functionality with required options would need its own check in App, its
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
    // One has one: SV's required backend and domain. Everything else is a
    // switch over fields that are legal empty -- the environment variables had
    // the other, and stopped being a group when they stopped being a switch
    // (see configureBlockedBy, which is where that rule reads from now).
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
    // and a functionality view could take a required field off the page.
    const owned = OPTION_GROUPS.flatMap((g: OptionGroup) => g.keys);
    expect(owned).not.toContain("service_account_name");
    expect(owned).not.toContain("service_account_create");
  });
});

// -- what the configure step still needs -------------------------------------
// One derivation behind two things on screen: the tick beside step 2 and the
// line saying why it has none.
//
// It used to name a namespace and a service account too, and those are gone:
// an empty one is a `<PLACEHOLDER>` now, which the bundle carries and complains
// about itself, so blocking the step as well would be the same answer twice and
// the worse half of it. What is asserted here is what is *left* -- a group
// whose state generate() genuinely refuses, and an environment variable with a
// name no process could read. See placeholder.test.ts for the other half, which
// kept this block's hardest-won rule: never name a field this format has not
// got. (The bug that rule came from: a docker bundle, which has neither of
// those two fields by design, being told to go and fix both.)

describe("configureBlockedBy", () => {
  it("says nothing when nothing is outstanding", () => {
    // Empty is what ticks the step off, so this is the same assertion twice.
    expect(configureBlockedBy({}, [])).toBe("");
  });

  it("does not block on a field that is merely empty", () => {
    // The whole change in posture. Both of these used to be sentences here.
    expect(configureBlockedBy({ namespace: "", service_account_name: "" }, []))
      .toBe("");
  });

  it("names the group by the title on its own row", () => {
    // The sentence is a way back to a control, so it says what that control
    // says. A second wording here would be a second name for one row.
    expect(configureBlockedBy({}, [GROUP_BY_ID.sv, GROUP_BY_ID.ca]))
      .toBe(`${GROUP_BY_ID.sv.title} and ${GROUP_BY_ID.ca.title} first`);
  });

  it("names an environment variable no process could read", () => {
    // Not a blank field: a bad one. A marker can say "nobody filled this in"
    // and cannot say "this name is not a name", so this one still blocks.
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
    // It is still `incomplete` -- the row says so -- and the two fields become
    // markers the API server rejects. What it is not any more is a closed door.
    const o = { sv_ingress: "nginx", sv_subdomain: "", sv_tls_secret: "" };
    expect(incompleteGroups(o, { sv: true })).toHaveLength(1);
    expect(blockingGroups(o, { sv: true })).toEqual([]);
  });

  it("still stops on a question nobody answered", () => {
    // No ingress chosen on a location that runs mockServices. generate()
    // refuses this outright, and there is no field for a marker to go in.
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
    // The complaint behind #150 was that Kubernetes auto-update is missing from
    // the environment list. It is not missing -- AUTO_KUBERNETES_UPDATE is
    // written by the bundle itself, off the `auto_update` option -- but that
    // option is a tri-state inside a group about RBAC, so the only route to it
    // was to open the wrong question and read to the bottom. The env area
    // answers instead: the served table says which option, and the group that
    // owns the key says where that option is.
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
    // An option no group owns -- the engine limits, which the configure step
    // states from the location and does not edit -- has an owner and no
    // section. "Somewhere on this step" is not a place, and the owner alone is
    // still the answer to "then where is it set".
    expect(reservedWhere("KUBERNETES_RESOURCES_LIMITS_CPU", RESERVED_ENV))
      .toEqual({ name: "KUBERNETES_RESOURCES_LIMITS_CPU",
                 owner: "engine_cpu_limit", where: null });
  });

  it("follows a one-of pair to the group that holds both", () => {
    // The CA trio names a pair, which is what the option table itself calls
    // them. Splitting on the served separator rather than looking the whole
    // string up: `ca_bundle | ca_existing_configmap` is no option's name.
    expect(reservedWhere("REQUESTS_CA_BUNDLE", RESERVED_ENV)?.where)
      .toBe("Custom CA trust");
  });

  it("keeps 'no option owns it' apart from 'this name is not reserved'", () => {
    // The served table answers null for the identity and the fixed posture, and
    // that is a real answer: the refusal stands, and inventing an option to
    // send somebody to would be worse than saying there is not one.
    expect(reservedWhere("SHIP_ID", RESERVED_ENV))
      .toEqual({ name: "SHIP_ID", owner: null, where: null });
    // A name the table does not carry is not reserved at all, and an empty
    // table is "not read yet" -- neither is a claim about where anything is.
    expect(reservedWhere("VERIFY_SSL", RESERVED_ENV)).toBe(null);
    expect(reservedWhere("AUTO_KUBERNETES_UPDATE", {})).toBe(null);
  });

  it("lists every reserved name, in the order the table is served in", () => {
    // On the page rather than behind a search box: the whole failure is
    // somebody looking for a name and finding nothing, and a list that is
    // rendered is one the browser's own find can land in.
    const all = reservedList(RESERVED_ENV);
    expect(all.map((r) => r.name)).toEqual(Object.keys(RESERVED_ENV));
    expect(all.find((r) => r.name === "AUTO_KUBERNETES_UPDATE")?.where)
      .toBe("Security & RBAC");
    expect(reservedList({})).toEqual([]);
  });
});
