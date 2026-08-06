// The option groups of the configure step, declared once each.
//
// A group used to be four things in three places -- a title and hint in JSX, a
// clause in the detection effect, and an enable and a disable arm of one
// switch -- so the wipe list drifted from the fields it was meant to clear.
// Here each group states its title, its hint, the option keys it writes, and
// its three lifecycle functions; App renders by walking the list, and the
// bodies are separate components taking only what they use.
//
// Nothing in this file imports React: it is plain data in, plain data out,
// which is what makes optionGroups.test.ts possible without a DOM.

import { FuncIdChoice, Functionality, Options } from "./api";
// The environment area's own rule about what blocks the step. The area is not
// a group -- it is a list of the variables no group here writes, with a
// name/value editor under it (see EnvVars) -- so the only thing this file wants
// from it is that one answer, in configureBlockedBy.
import { envIncomplete } from "./env";
// What the bundle is. Only two things here need it: the filter at the foot of
// this file, and the one group whose recommended mode depends on the platform.
// formats.ts imports nothing of ours, so this direction is the only one.
import { Applies, isDocker, keysApply } from "./formats";

export type GroupId =
  "registry" | "proxy" | "ca" | "sched" | "security" | "sv";

/** Merged over the current options. `null` clears a key that has a default --
 *  A key with no default must be REMOVED rather than nulled -- generate()
 *  spreads options over the defaults and profile.json dumps what survives, so
 *  an explicit null adds a key that was never there and the bundle stops being
 *  byte-identical to one generated without it. No option needs that today; the
 *  helper that did it went with the ingress-class field it existed for. */
export type OptionPatch = Record<string, unknown>;

export interface OptionGroup {
  id: GroupId;
  title: string;
  /** Sub-title on the row, whether the group is on or off. */
  hint: string;
  /** Shown instead of `hint` on a row the caller flags as required. */
  requiredHint?: string;
  /** Shown instead of `hint` on a row the location demands and the user has
   *  switched off anyway. A group that can be required and declined has to say
   *  what was given up, or the row goes quiet at exactly the moment it stopped
   *  blocking the download. */
  declinedHint?: string;
  /** Every option key this group writes. Overlap is legal and would be listed
   *  by each owner, so the sharing is visible here rather than only to someone
   *  who reads two lifecycle functions -- there is none at present, since
   *  Service virtualization gave up its claim on `service_type` (#60). The list
   *  is also what a later view can use to say "set, but not currently shown". */
  keys: string[];
  /** The ids of the served functionalities this group belongs to (see Functionality in
   *  api.ts). Empty means every deployment needs it whatever is being
   *  configured -- registry, proxy, CA trust, scheduling -- and such a group is
   *  never hidden, so it can never be the reason a download is blocked off
   *  screen. This tag is the whole frontend half of adding a functionality: the list
   *  of functionalities itself is served, never enumerated here.
   *
   *  A tag must name a functionality the server serves. It always had to -- a group
   *  tagged with anything else is on no card and reachable from nowhere -- but
   *  since notRunPatch clears the groups of a functionality the location does
   *  not run, an unserved tag would clear itself silently as well. Held to
   *  core.FUNCTIONALITIES by test_server.py, which reads the tags out of this
   *  file. */
  functionalities: string[];
  /** Does this config already mean the group is on? Runs on every option
   *  change, including the ones a preset or an imported profile brings in. */
  detect: (o: Options) => boolean;
  /** Applied when the switch goes on -- empty for a group that only reveals
   *  fields it does not have to seed. */
  enable: (o: Options) => OptionPatch;
  /** Applied when the switch goes off. OFF hides the fields AND wipes their
   *  options, so nothing hidden ever reaches the manifests. `required` is the
   *  location's demand, as in `incomplete`: switching a group off that the
   *  location asks for is a decision, and only the group knows whether its
   *  options can record one (SV's can -- see SV_NONE). */
  disable: (o: Options, required: boolean) => OptionPatch;
  /** Is this group in use but not yet finished, so the bundle cannot generate?
   *  Declared here with everything else about the group, because the page
   *  holding one group's rule is how "adding a functionality needs no frontend
   *  change" breaks: the next functionality with required options would otherwise
   *  need its own check in App, its own entry in a list, and its own arm on the
   *  download guard. `required` is the location's own demand -- funcIds can make
   *  a group mandatory when nothing is set yet. Absent means never blocks.
   *
   *  `backends` is the served SV backend table; only the SV group reads it, and
   *  only to answer whether the chosen backend tolerates NODEPORT -- a
   *  per-backend fact that lives on the server and cannot be stated here
   *  without keeping a second copy of it. Undefined before the constants load. */
  incomplete?: (o: Options, required: boolean,
                backends?: Record<string, { nodeport_ok: boolean }>) => boolean;
  /** Of that, what still stops the step -- defaulting to `incomplete` when a
   *  group draws no distinction. Only Service virtualization does: see
   *  svBlocking for why the row and the step now answer differently. */
  blocks?: (o: Options, required: boolean,
            backends?: Record<string, { nodeport_ok: boolean }>) => boolean;
  /** The keys this group cannot produce a working bundle without, given what is
   *  set on it — read only while the group is ON, and returning those of them
   *  that are still empty is `blankRequired`'s job, not this one's.
   *
   *  Declared here for the same reason `incomplete` is: the alternative is a
   *  table somewhere else listing which of a group's keys matter, which is the
   *  group's own answer kept twice. It is a *function* because the answer
   *  depends on what has been chosen inside the group — the CA group needs a
   *  ConfigMap name in one mode, a PEM in another and nothing in the third.
   *
   *  This is the half the generator cannot work out for itself. A registry, a
   *  proxy and a CA are configured by *having a value*, so blank and "not using
   *  one" are the same options dict on the server; the switch that tells them
   *  apart is here. Absent means the group has no required text field. */
  requires?: (o: Options) => string[];
}

// -- the cluster, which the posture is not ------------------------------------

/** Is the target cluster OpenShift itself? `generate.is_openshift`, and the one
 *  copy of that reading on this side.
 *
 *  `platform` is a *posture* -- who assigns the pod's UID -- and the
 *  SCC-friendly one is recommended on vanilla Kubernetes too, so it can never
 *  answer which binary the person deploying types. Two readers here need the
 *  product rather than the posture: the SV backends (only OpenShift serves a
 *  route.openshift.io Route) and CA trust (only OpenShift fills a labeled
 *  ConfigMap in). `!== false` because absent means the default, which is on --
 *  Boolean() would read an untouched bundle as plain Kubernetes and hide two
 *  controls that were being offered a moment ago. */
export const isOpenshift = (o: Options) =>
  o.platform === "openshift" && o.openshift_cluster !== false;

// -- CA trust ----------------------------------------------------------------
// One-of: existing ConfigMap | inline PEM | OpenShift injection.
export type CaMode = "none" | "existing" | "inline" | "inject";

export function caModeOf(o: Options): CaMode {
  return o.ca_existing_configmap != null ? "existing"
    : o.ca_bundle != null ? "inline"
    : o.ca_openshift_inject ? "inject" : "none";
}

/** The patch that puts CA trust in `mode`. The group's enable and disable are
 *  this same function at "existing" and "none", so the radio buttons and the
 *  switch cannot end up disagreeing about what a mode means. */
export function caModePatch(o: Options, mode: CaMode): OptionPatch {
  return {
    ca_existing_configmap: mode === "existing" ? (o.ca_existing_configmap ?? "") : null,
    ca_configmap_key: mode === "existing" ? o.ca_configmap_key : null,
    ca_bundle: mode === "inline" ? (o.ca_bundle ?? "") : null,
    ca_openshift_inject: mode === "inject",
  };
}

// -- engine sizing -----------------------------------------------------------
// Engine pod limits. Standard is BlazeMeter's own sizing; Small is validated
// to run real tests and fits dev clusters (CRC/minikube) that can't spare 8Gi.
//
// NOT a group any more (#132): the size is never optional -- generate always
// emits the limits, deriving them from the location's overrideCPU /
// overrideMemory when no option names them -- so a switch that could be off,
// and fields that could be blank, misdescribed it. The configure step renders
// engineSize.sizeStatement instead; the presets below serve the capacity
// profile on step 1, which still writes the two options as a prescription.
export const ENGINE_SIZES = [
  { id: "small", cpu: "1", mem: "4Gi", label: "Small — 1 CPU / 4Gi (dev clusters, light tests)" },
  { id: "standard", cpu: "2", mem: "8Gi", label: "Standard — 2 CPU / 8Gi (BlazeMeter default)" },
  { id: "large", cpu: "4", mem: "16Gi", label: "Large — 4 CPU / 16Gi (heavy scripts)" },
];

/** BlazeMeter's documented default — what the generator emits when nothing
 *  else names a size (ENGINE_DEFAULT_CPU/MEM on that side), so the one TS
 *  copy of the 2/8Gi figure. engineSize.ts renders it. */
export const STANDARD_SIZE = ENGINE_SIZES.find((s) => s.id === "standard")!;

/** The functionalities whose agent carries a taurus engine, so that "engine
 *  size" is a true statement about its pod limits.
 *
 *  Placement only, and that is the whole of what survives #149. It was
 *  `SIZING_FUNCTIONALITY`, one id doing two jobs: where the statement renders,
 *  and which locations had `engine_cpu_limit`/`engine_mem_limit` cleared out
 *  from under them. The second job was wrong. Crane applies
 *  KUBERNETES_RESOURCES_LIMITS_CPU/_MEMORY to **every pod it creates** -- one
 *  pair, with no per-functionality second one -- so the limits belong to no
 *  functionality and are never cleared for one; see notRunPatch.
 *
 *  Read off real single-functionality locations' /versions rather than assumed:
 *  performance carries apm/crane/v4 and functionalGui adds doduo and a browser
 *  to the same three, while an SV-only agent carries crane, group-gateway and
 *  service-mock -- **no taurus engine at all**. Its limits still reach its mock
 *  pods and are still emitted; what they mean there is a sizing model that does
 *  not exist yet (#154), and stating an engine size over it would be inventing
 *  one. So it gets no statement rather than a wrong one. */
export const ENGINE_FUNCTIONALITIES = ["performance", "functionalGui"];

// -- service account ---------------------------------------------------------
// Deliberately not a group. A group is a switch that hides its fields when it is
// off, and these two are neither optional nor functionality-specific: every
// deployment runs as some account, so they sit beside the namespace and are
// always sent. What lives here rather than in App is the one rule that must not
// be restated -- generate.service_account() refuses an empty name in both
// output formats, and this is that refusal, in time to be shown on the field.

/** Is the service account usable? `create` may be either way; only an empty
 *  name blocks, because with nothing creating the account the name is the only
 *  thing saying which existing one crane runs as -- and the alternative,
 *  falling back to the namespace's `default`, hands crane's Role to every other
 *  pod in the namespace. */
export function serviceAccountOk(o: Options): boolean {
  return !!String(o.service_account_name ?? "").trim();
}

// -- service virtualization --------------------------------------------------

/** `sv_ingress` when the location advertises mockServices and the bundle is
 *  wanted for performance alone. It is *not* an ingress type: unset means
 *  nobody has answered and is refused for such a location, this means answered
 *  no.
 *
 *  generate.SV_INGRESS_NONE is the authority. It is not served on
 *  /api/sv-constants the way `ingress_types` is -- that response is what the
 *  backend picker is built from, and this is not a backend -- and the functions
 *  below are handed options and nothing else, so it could not arrive that way
 *  regardless. So it is a literal, and tests/test_server.py parses it out of
 *  this file and holds it equal to the generator's, the same way the
 *  TokenBranch union is pinned: a rename on either side otherwise leaves both
 *  compiling. */
export const SV_NONE = "none";

/** Is this an SV configuration at all? "none" is a value like any other to
 *  everything that reads options, so the one place that knows better is here
 *  rather than at each `!!o.sv_ingress` -- which is what the row, the group's
 *  own detection and the mock-status poll were all separately getting wrong. */
export function svConfigured(ingress: unknown): boolean {
  return !!ingress && ingress !== SV_NONE;
}

/** Is the SV configuration in use but not finished, so the bundle cannot
 *  generate? The `incomplete` of the sv group below, named so that sv.ts can
 *  ask it directly: the page used to reach `GROUP_BY_ID.sv.incomplete!` through
 *  the table, which is a hole in the promise that a group's rules are the
 *  group's own. A second copy of the rule beside that caller would be worse
 *  still -- #60 relaxed it and had to edit both.
 *
 *  Mirrors _sv_cfg in generate.py: with an ingress chosen, the domain and the
 *  TLS secret are both mandatory (the secret even for plain HTTP -- crane
 *  validates it at startup), and NODEPORT is refused for a backend that cannot
 *  publish over it. With none chosen, only a location whose funcIds demand SV
 *  is unfinished.
 *
 *  An unknown backend does NOT block, and that covers three states this one
 *  value cannot tell apart -- not fetched yet, fetch failed, table served
 *  empty. Usually the repo insists those stay distinct; here they genuinely
 *  share an answer, because none of them is evidence that the pairing is
 *  broken. Blocking on any of them would grey out the download for a
 *  configuration that generates fine, and generate() refuses authoritatively
 *  in the case that is actually broken.
 *
 *  SV_NONE is finished by declaration -- generate() accepts it for a
 *  mockServices location, so blocking the download on it would be the UI
 *  refusing what the backend allows. */
export function svIncomplete(
    o: Options, required: boolean,
    backends?: Record<string, { nodeport_ok: boolean }>): boolean {
  if (o.sv_ingress === SV_NONE) return false;
  if (!o.sv_ingress) return required;
  return !String(o.sv_subdomain ?? "").trim()
    || !String(o.sv_tls_secret ?? "").trim()
    || svNodePortConflict(o, backends);
}

/** The arms of the rule above that still stop the step, now that a blank field
 *  does not.
 *
 *  Deliberately a second predicate rather than a narrowing of the first: the
 *  row must go on saying it is unfinished with an empty subdomain -- that is
 *  what `incomplete` is for and it is still true -- while the *step* lets you
 *  past, because the bundle now carries `<PLACEHOLDER>` and says so about
 *  itself. What is left here is the two things a marker cannot stand in for and
 *  `generate()` still refuses outright: no ingress chosen at all on a location
 *  that runs mockServices, which is an unanswered question rather than an empty
 *  box, and a service type the chosen backend cannot publish over, which is a
 *  conflict between two answers that were both given. */
export function svBlocking(
    o: Options, required: boolean,
    backends?: Record<string, { nodeport_ok: boolean }>): boolean {
  if (o.sv_ingress === SV_NONE) return false;
  if (!o.sv_ingress) return required;
  return svNodePortConflict(o, backends);
}

/** The one arm of the rule above that the SV panel has to name on its own: a
 *  service type the chosen backend cannot publish over needs a different
 *  sentence from an empty field, because only one of them names a fix that is
 *  somewhere else on the page. Computed rather than deduced from the absence of
 *  the other reasons: deduced, it would inherit whatever a later completeness
 *  rule adds, and the panel would show the nodePort sentence for it. */
export function svNodePortConflict(
    o: Options,
    backends?: Record<string, { nodeport_ok: boolean }>): boolean {
  return svConfigured(o.sv_ingress)
    && o.service_type != null && o.service_type !== "CLUSTERIP"
    && backends?.[String(o.sv_ingress).trim()]?.nodeport_ok === false;
}

// -- the groups, in the order the form shows them ----------------------------
export const OPTION_GROUPS: OptionGroup[] = [
  {
    id: "registry",
    title: "Private registry",
    hint: "mirror images into your own registry (air-gapped)",
    functionalities: [],
    keys: ["private_registry", "pull_secret", "registry_auth"],
    // The host alone. A pull secret is optional (a registry may be anonymous)
    // and registry_auth is a switch, which cannot be blank.
    requires: () => ["private_registry"],
    detect: (o) => !!(o.private_registry || o.pull_secret || o.registry_auth),
    enable: () => ({}),
    disable: () => ({ private_registry: null, pull_secret: null, registry_auth: false }),
  },
  {
    id: "proxy",
    title: "HTTP(S) proxy",
    hint: "egress via a corporate proxy, optional authentication",
    functionalities: [],
    keys: ["proxy"],
    // Not both: one URL is a working proxy configuration, and BlazeMeter's
    // traffic is HTTPS, so that is the one a proxy group with nothing in it is
    // missing. Marking both would put two rows in the README for one thing to
    // go and find out.
    requires: (o) => {
      const p = (o.proxy ?? {}) as Record<string, unknown>;
      const has = (k: string) => !!String(p[k] ?? "").trim();
      return has("http") || has("https") ? [] : ["proxy.https"];
    },
    detect: (o) => !!o.proxy,
    enable: () => ({}),
    disable: () => ({ proxy: null }),
  },
  {
    id: "ca",
    title: "Custom CA trust",
    hint: "TLS-intercepting proxy / private CAs — mounted into crane + engines",
    functionalities: [],
    keys: ["ca_existing_configmap", "ca_configmap_key", "ca_bundle", "ca_openshift_inject"],
    // Per mode, which is why this is a function. `ca_configmap_key` is not here
    // in either: it defaults to ca-bundle.crt, and OpenShift injection fills a
    // ConfigMap this bundle names itself, so that mode needs nothing typed.
    requires: (o) => {
      const mode = caModeOf(o);
      if (mode === "existing") return ["ca_existing_configmap"];
      if (mode === "inline") return ["ca_bundle"];
      return [];
    },
    detect: (o) => caModeOf(o) !== "none",
    // On lands on the recommended mode rather than on no mode at all, which
    // would show three radios and no fields. Which one is recommended depends
    // on the format, and this is the one place in this file that reads it: the
    // other two modes name a ConfigMap, and a docker bundle has none -- so
    // seeding "existing" there would write an option the bundle's README then
    // reports as set-and-not-carried, off a switch nobody aimed at it.
    enable: (o) => caModePatch(
      o, isDocker(String(o.output_format ?? "")) ? "inline" : "existing"),
    disable: (o) => caModePatch(o, "none"),
  },
  {
    id: "sched",
    title: "Scheduling",
    hint: "node pools for crane & engines (separate pools optional)",
    functionalities: [],
    keys: ["tolerations", "node_selector", "engine_tolerations",
           "engine_node_selector"],
    // `!= null`, not truthiness: an engine override of {} or [] is a real
    // setting ("engines take neither, even though crane does") and a falsy one,
    // so a truthy detect would leave the group collapsed on a bundle that has
    // it and then clear it on the next save.
    detect: (o) => !!(o.tolerations || o.node_selector)
      || o.engine_tolerations != null || o.engine_node_selector != null,
    enable: () => ({}),
    disable: () => ({ tolerations: null, node_selector: null,
                      engine_tolerations: null, engine_node_selector: null }),
  },
  {
    id: "security",
    title: "Security & RBAC",
    // Says only what is true of every format. It used to enumerate the
    // Kubernetes defaults -- "token in a Secret, CLUSTERIP, no cluster RBAC" --
    // and a docker bundle has none of those three, so the row named settings
    // its own body had just hidden. What each format's defaults actually are is
    // in the fields, which is where they can be changed.
    hint: "defaults: the credential kept apart from the configuration, no agent self-update",
    // Untagged deliberately: how the auth token is stored and whether the
    // bundle asks for cluster RBAC are questions every deployment answers.
    functionalities: [],
    // Sole owner of service_type -- the SV group gave up its claim once #60
    // showed an ingress publishes fine over NODEPORT.
    keys: ["use_secret", "cluster_rbac", "service_type", "restrict_engines",
           "auto_update"],
    // Absent service_type means the backend default (CLUSTERIP), so only an
    // explicit NODEPORT is a departure worth opening the group for -- the same
    // `!= null` treatment the SV validation uses. restrict_engines is the same
    // shape the other way up: absent means the backend default, which is on,
    // so only an explicit false is a departure. auto_update is a tri-state
    // whose absent value resolves off like `restrict_engines`, but BOTH
    // booleans still open the group: `false` is worth showing because a bundle
    // that states it deliberately is not the same as one that never asked.
    detect: (o) => o.use_secret === false || !!o.cluster_rbac
      || o.restrict_engines === false || o.auto_update != null
      || (o.service_type != null && o.service_type !== "CLUSTERIP"),
    enable: () => ({}),
    disable: () => ({ use_secret: true, cluster_rbac: false,
                      service_type: "CLUSTERIP", restrict_engines: true,
                      auto_update: null }),
  },
  {
    id: "sv",
    title: "Service virtualization",
    hint: "only for locations with the mockServices functionality",
    requiredHint: "this location runs mockServices — virtual services need an ingress",
    // Switched off on a location that runs mockServices. Allowed, because a
    // location often carries both funcIds and the customer runs tests on it and
    // no virtual services at all -- but the bundle it produces really is the
    // performance one, so the row says what it costs rather than falling silent
    // the moment it stopped blocking the download.
    declinedHint: "performance only — virtual services deployed here will stall at WAITING_FOR_DOMAIN",
    // The funcId, which is what a functionality id is (#149). Not the group id
    // beside it: `sv` names a row on this page, `mockServices` names something
    // the account enables, and the two coinciding was how one could be read for
    // the other.
    functionalities: ["mockServices"],
    // service_type is *not* here. This group used to own it as well, to force
    // CLUSTERIP; a live run (#60) showed the ingress path works over NODEPORT
    // on namespaced RBAC, so SV has no opinion on it and Security owns it
    // alone.
    keys: ["sv_ingress", "sv_subdomain", "sv_tls_secret", "sv_istio_gateway"],
    // The ingress is what the group is: a domain or TLS secret arriving without
    // one is not an SV configuration, and an SV *location* is flagged required
    // by the caller rather than found in the options at all. SV_NONE is an
    // answer, not a configuration, so it leaves the group closed -- an imported
    // profile that declined must not re-open it.
    // Only once a real backend is chosen. SV_NONE and "nobody has answered"
    // need an ingress picked before either field means anything, and that is
    // `incomplete`'s arm below rather than a blank text box.
    requires: (o) => (svConfigured(o.sv_ingress)
      ? ["sv_subdomain", "sv_tls_secret"] : []),
    detect: (o) => svConfigured(o.sv_ingress),
    // Stated above, as svIncomplete: sv.ts answers the same question for the
    // panel that has to explain it, and the two must not be two rules.
    incomplete: svIncomplete,
    blocks: svBlocking,
    // `{}` when an ingress is already chosen, like every other group that has
    // nothing to seed: a patch with a key in it mints a fresh options identity
    // and re-POSTs /api/generate for a configuration that did not change.
    // SV_NONE is not a chosen one -- switching the group back on has to pick a
    // real backend or the select would show nginx over a value that is not it.
    enable: (o) => (svConfigured(o.sv_ingress) ? {} : { sv_ingress: "nginx" }),
    // On a location that demands SV, off is a decision and is recorded as one:
    // null would be "not answered", which generate() refuses, so the switch
    // would snap back on and the download stay blocked -- which is exactly what
    // it used to do. Everywhere else null is still right: nobody asked.
    disable: (_o, required) => ({ sv_ingress: required ? SV_NONE : null,
      sv_subdomain: null, sv_tls_secret: null, sv_istio_gateway: null }),
  },
];

export const GROUP_BY_ID = Object.fromEntries(
  OPTION_GROUPS.map((g) => [g.id, g])) as Record<GroupId, OptionGroup>;

export type GroupFlags = Record<GroupId, boolean>;

export const allGroupsOff = (): GroupFlags =>
  Object.fromEntries(OPTION_GROUPS.map((g) => [g.id, false])) as GroupFlags;

/** Which groups are open, given the config and what was open a moment ago.
 *  Sticky: a group the user opened by hand stays open with nothing set in it,
 *  and a preset or an imported profile only ever opens groups. `required`
 *  carries what the options cannot say -- an SV location needs the SV group
 *  whether or not anything is configured in it. */
export function detectGroups(
    o: Options, prev: GroupFlags,
    required: Partial<GroupFlags> = {}): GroupFlags {
  return Object.fromEntries(OPTION_GROUPS.map((g) =>
    [g.id, prev[g.id] || g.detect(o) || !!required[g.id]])) as GroupFlags;
}

// -- the split the configure step is built on --------------------------------
// Two buckets, derived from the declarations rather than listed anywhere: a
// group belongs to no functionality and is therefore in every bundle, or it belongs
// to one and lives in that functionality's card. Every group is in exactly one of
// them, which is what stops a group being on screen twice or not at all -- the
// failure the functionality *view* had, where five of six groups were in both views.

/** Groups no functionality owns: every deployment gets them. */
export const SHARED_GROUPS = OPTION_GROUPS.filter((g) => !g.functionalities.length);

/** The groups a functionality owns. Empty is legal and means the functionality adds no
 *  options of its own -- its card says so rather than being left out, because
 *  "nothing to configure" and "not shown" are different answers. */
export function groupsOf(functionalityId: string): OptionGroup[] {
  return OPTION_GROUPS.filter((g) => g.functionalities.includes(functionalityId));
}

// -- a functionality the location does not run --------------------------------
// Not on the configure step at all, and configured nowhere. It was stated there
// for a while (#113) -- a card naming the funcId to add -- which is true and
// nothing the reader of that step can act on, so the panel now filters it out;
// only manual entry, where the card is the declaration, still renders one.
// Everything below is unchanged by that: hiding a row does not empty it, and
// notRunPatch is what empties it. Half-configurable was the state before: manual
// mode had no guard at all, so flipping Service virtualization on for an
// identity declared as performance seeded `sv_ingress: nginx` over empty
// subdomain and TLS fields, and the step went red for something nothing on the
// page had asked for.

/** Which functionalities this location runs, or null while nobody has answered.
 *
 *  Three states in one value, and the third is why it is not a plain array.
 *  Manual entry *declares* a functionality; a location read off the account carries
 *  the funcIds its functionalities come from; before either has happened the question
 *  is simply unanswered. Answering the unanswered case with `[]` takes every
 *  card's switches off the page while the account is still being read, and
 *  answering it with the whole list claims an enablement nobody has confirmed
 *  -- the same collapse from either end. */
export function enabledFunctionalities(
    mode: "connect" | "manual", declared: string | null,
    locFunctionalities: string[]): string[] | null {
  // Manual declares rather than reads, so there is nothing outstanding: the
  // answer is the declaration, and no declaration yet is an empty one.
  if (mode === "manual") return declared ? [declared] : [];
  // An account that has not been read and a location whose funcIds carry no
  // served functionality both arrive as an empty list, and "nothing has said" is the
  // honest answer to both.
  return locFunctionalities.length ? locFunctionalities : null;
}

/** Does this location run the functionality? Unanswered counts as yes, deliberately:
 *  a switch shown for a functionality that turns out not to apply is corrected the
 *  moment the account answers, where one hidden on a guess leaves a location
 *  that does run the functionality with nowhere to configure it. */
export function runsFunctionality(
    enabled: string[] | null, functionalityId: string): boolean {
  return enabled == null || enabled.includes(functionalityId);
}

/** What must be cleared because it configures a functionality the location does not
 *  run, or null when nothing must.
 *
 *  The options can reach that state without anyone choosing it -- an imported
 *  profile, a restored session, or a location picked after the form was filled
 *  in -- and the switch that would clear it is deliberately not on screen. Left
 *  set they are an off-screen blocker twice over: `incompleteGroups` counts the
 *  group, and generate() refuses outright (an `sv_ingress` with no subdomain is
 *  a hard error whatever the location runs).
 *
 *  Each group's own `disable()`, never a wipe list written here -- the drift
 *  between the two is what this file exists to stop. `required` is false by
 *  construction: a demand comes from the location's funcIds, and these are the
 *  functionalities those funcIds do not carry. Applying the patch makes every `detect`
 *  it fired on false, so the next answer is null and the page settles in one
 *  pass -- the property sv.correction is written to hold too. */
export function notRunPatch(
    o: Options, enabled: string[] | null): OptionPatch | null {
  const patch: OptionPatch = {};
  for (const g of OPTION_GROUPS) {
    if (g.functionalities.length && g.detect(o)
        && !g.functionalities.some((f) => runsFunctionality(enabled, f))) {
      Object.assign(patch, g.disable(o, false));
    }
  }
  // The pod limits used to be cleared here too, on the reading that they size
  // an engine and a location running no performance has none. #149 removed
  // that: crane applies KUBERNETES_RESOURCES_LIMITS_CPU/_MEMORY to every pod it
  // creates, so one pair covers engines, browser pods and mock-service pods
  // alike and there is no per-functionality second one. Cleared, an SV-only or
  // GUI-only agent's pods fall to crane's 250m/256Mi defaults -- the silent
  // failure the LimitRange note in CLAUDE.md is about, arrived at from the page
  // instead. Nothing replaces the clause: a value that reaches every pod is
  // never wrong for the ones a location does not run.
  return Object.keys(patch).length ? patch : null;
}

/** ...and of those, the ones this bundle's format can carry.
 *
 *  A third filter over the same list, so it lives with the other two rather
 *  than beside the predicate it takes. A group whose every declared key is
 *  ignored is not on screen at all -- Scheduling, for docker.
 *  One with some is on screen with the rest of its fields hidden by the
 *  predicate itself: Private registry keeps the registry and loses the
 *  imagePullSecret. Derived from `keys`, so a group gaining an option needs
 *  nothing here.
 *
 *  Unlike the two above this is not a *view*: a functionality hides nothing, and a
 *  group dropped here is one the bundle has no such thing as. */
export function groupsFor(gs: OptionGroup[], applies: Applies): OptionGroup[] {
  return gs.filter((g) => keysApply(g.keys, applies));
}

/** Groups in use but not finished, so the download is blocked. Derived from the
 *  declarations rather than passed in: the caller knowing which groups can be
 *  incomplete is the coupling this exists to remove. */
export function incompleteGroups(
    o: Options, required: Partial<Record<GroupId, boolean>>,
    backends?: Record<string, { nodeport_ok: boolean }>): OptionGroup[] {
  return OPTION_GROUPS.filter((g) => g.incomplete?.(o, !!required[g.id], backends));
}

/** Groups whose state the step genuinely cannot go past, which since blank
 *  fields became markers is a subset of the above. `blocks ?? incomplete` so a
 *  group that draws no distinction needs no second declaration. */
export function blockingGroups(
    o: Options, required: Partial<Record<GroupId, boolean>>,
    backends?: Record<string, { nodeport_ok: boolean }>): OptionGroup[] {
  return OPTION_GROUPS.filter(
    (g) => (g.blocks ?? g.incomplete)?.(o, !!required[g.id], backends));
}

/** What is stopping the configure step being finished, as the sentence that
 *  says so -- and "" when nothing is, which is what marks the step done.
 *
 *  One derivation for both, because they are one answer: a tick beside a step
 *  and a line saying what it still needs cannot be allowed to disagree.
 *
 *  Named rather than listed. It used to be the fixed string "namespace, service
 *  account and any unfinished group first", which named the same three things
 *  whatever the bundle was -- and a docker bundle has no namespace and no
 *  ServiceAccount, so two thirds of the only sentence telling somebody what to
 *  fix pointed at fields that are deliberately not on the page. A group's title
 *  is what the row beside it says, so the sentence names the row to go back to.
 *
 *  It used to take `applies` for those two fields, to keep "filled in" and
 *  "this format has no such field" from collapsing into one `true`. They are
 *  gone from here -- an empty one is a marker now, not a blocker -- and the
 *  distinction moved with them, into `blankRequired`, which asks the same
 *  predicate for the same reason: a warning must not name a field the form for
 *  this format does not show.
 */
export function configureBlockedBy(
    o: Options, blocking: OptionGroup[]): string {
  const needs = [
    // The namespace and the service account used to be here, and are not any
    // more: an empty one carries `<PLACEHOLDER>` into the bundle, which says
    // what it is and is refused by the API server at apply time with the field
    // named. Blocking as well would be the same answer twice, and the worse
    // half of it -- a step that will not advance, on a page that had already
    // let the field be emptied. `blankRequired` warns instead.
    ...blocking.map((g) => g.title),
    // The environment area, which is not a group and so is not in `incomplete`
    // -- it is a list of variables with a name/value editor under it, and only
    // that editor can produce a name no process could read. Named here rather
    // than left to the server's refusal for the same reason every other blocker
    // is: the field is on screen (#114), and the row beside it already says
    // what is wrong with it.
    envIncomplete(o) ? "the environment variables" : "",
  ].filter(Boolean);
  if (!needs.length) return "";
  const list = needs.length === 1 ? needs[0]
    : `${needs.slice(0, -1).join(", ")} and ${needs[needs.length - 1]}`;
  return `${list} first`;
}

// -- the served vocabulary ---------------------------------------------------
// Nothing here enumerates functionalities: a group names the ids it belongs
// to, and labels, suggested namespaces and which funcIds mean which
// functionality are all read off /api/functionalities. Adding functional testing, secrets or API
// monitoring is then a backend entry plus a tag on the groups it owns.
//
// The configure step shows every group at once -- the shared ones, then each
// functionality's own inside its card -- so nothing here selects a *view*. It used
// to: visibleGroups, setButHidden and hiddenBlockers existed to work out what
// the view was hiding and hand it back somewhere else, and went with the view.
// Nothing below writes or clears an option; suggestNamespace comes closest, and
// it hands a string back for the caller to apply only while the field still
// holds a suggestion.

/** The functionalities a location's funcIds carry, in served order.
 *
 *  A funcId this tool covers *is* a functionality id (#149), so the join is
 *  equality and there is no table between the two vocabularies. funcIds nothing
 *  covers -- tdm, dataPublisher, delphix, secretsPrivateVault, and since the
 *  split the retired functionalApi and proxyRecorder -- match nothing and are
 *  simply not a signal: never an error, and never a reason to leave the page
 *  empty. */
export function functionalitiesOf(
    funcIds: string[] | undefined, functionalities: Functionality[]): string[] {
  return functionalities
    .filter((f) => (funcIds ?? []).includes(f.id))
    .map((f) => f.id);
}

/** What to call the funcIds a location has that no served functionality claims.
 *
 *  Named on screen rather than dropped: this tool covers three funcIds, accounts
 *  serve nine, and "this location also runs X, which there are no options for"
 *  is a truthful thing to say where silence reads as coverage.
 *
 *  Names, not ids, and that is what `choices` is for (#148). /api/func-ids is
 *  the account's own vocabulary once one has been read, so an unclaimed funcId
 *  gets BlazeMeter's display name -- "TDM Integration", the words the customer
 *  sees in their own UI. Where no account has been read the served list is the
 *  covered baseline and holds none of these, so the answer is the raw funcId:
 *  what the location literally carries, which is worse than a display name and
 *  much better than a guess. `functionalApi` is the same case *with* an
 *  account -- BlazeMeter retired it and locations still have it -- so the
 *  fallback is not only a startup state.
 *
 *  Since the split (#149) `functionalApi` and `proxyRecorder` arrive here too:
 *  `performance` used to claim them, which is what made its label name four
 *  things at once. Named beside the cards is the honest place for a funcId
 *  nothing here configures. */
export function unclaimedFuncIds(
    funcIds: string[] | undefined, functionalities: Functionality[],
    choices: FuncIdChoice[]): string[] {
  return (funcIds ?? [])
    .filter((id) => !functionalities.some((f) => f.id === id))
    .map((id) => choices.find((c) => c.id === id)?.label ?? id);
}

/** Which functionality to open a location on: the first served one its funcIds
 *  carry, else the first served of all. A location carrying both therefore
 *  starts on the first -- performance, the common case -- and is routed to the
 *  other by the download-button block if that is where the missing settings
 *  are. `null` only before the vocabulary lands. */
export function startFunctionality(
    funcIds: string[] | undefined, functionalities: Functionality[]): string | null {
  return functionalitiesOf(funcIds, functionalities)[0]
    ?? functionalities[0]?.id ?? null;
}

/** The namespace to suggest as the view moves to `functionality`, or null to leave
 *  the field alone. Suggested only while it still holds a namespace some
 *  functionality suggested (or nothing at all): a name that was typed outranks any
 *  suggestion, and returning the value it already has would be a state write
 *  that re-POSTs the preview for no change. Which names count as suggestions is
 *  read off the served vocabulary, so a functionality added later brings its own. */
export function suggestNamespace(
    current: string, functionality: Functionality,
    functionalities: Functionality[]): string | null {
  const ns = current.trim();
  const suggested = !ns || functionalities.some((f) => f.namespace === ns);
  return suggested && ns !== functionality.namespace ? functionality.namespace : null;
}
