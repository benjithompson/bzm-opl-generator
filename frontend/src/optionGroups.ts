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

import { Feature, Options } from "./api";

export type GroupId =
  "registry" | "proxy" | "ca" | "sched" | "sizing" | "security" | "sv";

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
  /** Every option key this group writes. Overlap is legal: `service_type` is
   *  displayed by Security and forced by Service virtualization, and is listed
   *  by both so that the sharing is visible here rather than only to someone
   *  who reads two lifecycle functions. The list is also what a later view can
   *  use to say "set, but not currently shown". */
  keys: string[];
  /** The ids of the served features this group belongs to (see Feature in
   *  api.ts). Empty means every deployment needs it whatever is being
   *  configured -- registry, proxy, CA trust, scheduling -- and such a group is
   *  never hidden, so it can never be the reason a download is blocked off
   *  screen. This tag is the whole frontend half of adding a feature: the list
   *  of features itself is served, never enumerated here. */
  features: string[];
  /** Does this config already mean the group is on? Runs on every option
   *  change, including the ones a preset or an imported profile brings in. */
  detect: (o: Options) => boolean;
  /** Applied when the switch goes on -- empty for a group that only reveals
   *  fields it does not have to seed. */
  enable: (o: Options) => OptionPatch;
  /** Applied when the switch goes off. OFF hides the fields AND wipes their
   *  options, so nothing hidden ever reaches the manifests. */
  disable: (o: Options) => OptionPatch;
  /** Is this group in use but not yet finished, so the bundle cannot generate?
   *  Declared here with everything else about the group, because the page
   *  holding one group's rule is how "adding a feature needs no frontend
   *  change" breaks: the next feature with required options would otherwise
   *  need its own check in App, its own entry in a list, and its own arm on the
   *  download guard. `required` is the location's own demand -- funcIds can make
   *  a group mandatory when nothing is set yet. Absent means never blocks. */
  incomplete?: (o: Options, required: boolean) => boolean;
}

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
export const ENGINE_SIZES = [
  { id: "small", cpu: "1", mem: "4Gi", label: "Small — 1 CPU / 4Gi (dev clusters, light tests)" },
  { id: "standard", cpu: "2", mem: "8Gi", label: "Standard — 2 CPU / 8Gi (BlazeMeter default)" },
  { id: "large", cpu: "4", mem: "16Gi", label: "Large — 4 CPU / 16Gi (heavy scripts)" },
];

/** Which preset the two limits are, derived rather than stored, so an imported
 *  or preset config lands on the right entry and anything unrecognised shows
 *  as Custom. */
export function enginePreset(o: Options): string {
  return ENGINE_SIZES.find(
    (s) => s.cpu === o.engine_cpu_limit && s.mem === o.engine_mem_limit)?.id ?? "custom";
}

// -- the groups, in the order the form shows them ----------------------------
export const OPTION_GROUPS: OptionGroup[] = [
  {
    id: "registry",
    title: "Private registry",
    hint: "mirror images into your own registry (air-gapped)",
    features: [],
    keys: ["private_registry", "pull_secret", "registry_auth"],
    detect: (o) => !!(o.private_registry || o.pull_secret || o.registry_auth),
    enable: () => ({}),
    disable: () => ({ private_registry: null, pull_secret: null, registry_auth: false }),
  },
  {
    id: "proxy",
    title: "HTTP(S) proxy",
    hint: "egress via a corporate proxy, optional authentication",
    features: [],
    keys: ["proxy"],
    detect: (o) => !!o.proxy,
    enable: () => ({}),
    disable: () => ({ proxy: null }),
  },
  {
    id: "ca",
    title: "Custom CA trust",
    hint: "TLS-intercepting proxy / private CAs — mounted into crane + engines",
    features: [],
    keys: ["ca_existing_configmap", "ca_configmap_key", "ca_bundle", "ca_openshift_inject"],
    detect: (o) => caModeOf(o) !== "none",
    // On lands on the recommended mode rather than on no mode at all, which
    // would show three radios and no fields.
    enable: (o) => caModePatch(o, "existing"),
    disable: (o) => caModePatch(o, "none"),
  },
  {
    id: "sched",
    title: "Scheduling",
    hint: "tolerations + nodeSelector for crane & engines",
    features: [],
    keys: ["tolerations", "node_selector"],
    detect: (o) => !!(o.tolerations || o.node_selector),
    enable: () => ({}),
    disable: () => ({ tolerations: null, node_selector: null }),
  },
  {
    id: "sizing",
    title: "Engine sizing",
    hint: "CPU / memory limits for load engines (default 2 CPU / 8Gi)",
    // The only group that is about engines: a location running mocks alone
    // never starts one, so this is off screen while service virtualization is.
    features: ["performance"],
    keys: ["engine_cpu_limit", "engine_mem_limit", "emit_limitrange"],
    detect: (o) => !!(o.engine_cpu_limit || o.engine_mem_limit || o.emit_limitrange),
    // Seeded only when the two limits are not already a known shape: opening
    // the group must not overwrite a size a preset or profile just brought in.
    enable: (o) => {
      if (enginePreset(o) !== "custom") return {};
      const d = ENGINE_SIZES.find((s) => s.id === "standard")!;
      return { engine_cpu_limit: d.cpu, engine_mem_limit: d.mem };
    },
    disable: () => ({
      engine_cpu_limit: null, engine_mem_limit: null, emit_limitrange: false }),
  },
  {
    id: "security",
    title: "Security & RBAC",
    hint: "defaults: token in a Secret, CLUSTERIP, no cluster RBAC",
    // Untagged deliberately, though it shares service_type with SV below: how
    // the auth token is stored and whether the bundle asks for cluster RBAC are
    // questions every deployment answers, and hiding the field that the SV
    // group's own NODEPORT error points at would be the worst of both.
    features: [],
    // service_type is shared with the SV group below.
    keys: ["use_secret", "cluster_rbac", "service_type"],
    // Absent service_type means the backend default (CLUSTERIP), so only an
    // explicit NODEPORT is a departure worth opening the group for -- the same
    // `!= null` treatment the SV validation uses.
    detect: (o) => o.use_secret === false || !!o.cluster_rbac
      || (o.service_type != null && o.service_type !== "CLUSTERIP"),
    enable: () => ({}),
    disable: () => ({ use_secret: true, cluster_rbac: false, service_type: "CLUSTERIP" }),
  },
  {
    id: "sv",
    title: "Service virtualization",
    hint: "only for locations with the mockServices feature",
    requiredHint: "this location runs mockServices — virtual services need an ingress",
    features: ["sv"],
    // service_type is written here and owned by Security too; see the note on
    // `disable` for why only one of the two directions restores it.
    keys: ["sv_ingress", "sv_subdomain", "sv_tls_secret", "sv_istio_gateway",
           "service_type"],
    // The ingress is what the group is: a domain or TLS secret arriving without
    // one is not an SV configuration, and an SV *location* is flagged required
    // by the caller rather than found in the options at all.
    detect: (o) => !!o.sv_ingress,
    // The ingress path only works on CLUSTERIP; NODEPORT would send crane to
    // the cluster-scoped Node object instead.
    // Mirrors _sv_cfg in generate.py: with an ingress chosen, the domain and
    // the TLS secret are both mandatory (the secret even for plain HTTP --
    // crane validates it at startup), and NODEPORT is refused because it sends
    // crane to the cluster-scoped Node object. With none chosen, only a
    // location whose funcIds demand SV is unfinished.
    incomplete: (o, required) => (o.sv_ingress
      ? !String(o.sv_subdomain ?? "").trim()
        || !String(o.sv_tls_secret ?? "").trim()
        || (o.service_type != null && o.service_type !== "CLUSTERIP")
      : required),
    enable: (o) => ({ sv_ingress: o.sv_ingress || "nginx", service_type: "CLUSTERIP" }),
    // Deliberately does NOT restore service_type: CLUSTERIP is the safe value
    // and the one the backend defaults to, and putting a NODEPORT back is not
    // something switching a group off should do behind the user's back. This
    // asymmetry with `enable` is intended -- don't "fix" it.
    disable: () => ({ sv_ingress: null, sv_subdomain: null, sv_tls_secret: null,
      sv_istio_gateway: null }),
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

// -- the feature view --------------------------------------------------------
// The configure step shows one feature at a time, chosen from the list
// /api/features serves. Nothing here enumerates features: a group names the
// feature ids it belongs to, and labels, suggested namespaces and which funcIds
// mean which feature are all read off the served vocabulary. Adding functional
// testing, secrets or API monitoring is then a backend entry plus a tag above.
//
// The selector is a VIEW, not a scope. One crane is deployed for the selected
// location and that location's funcIds decide what ships, so nothing below
// writes or clears an option -- every function here is a selection over the
// declarations. suggestNamespace is the single exception, and it hands back a
// string for the caller to apply only when the field still holds a suggestion.

/** What a group with no features is attributed to, in one place because the row
 *  and any future summary must not word it differently. */
export const ANY_DEPLOYMENT = "any deployment";

/** How a group's attribution reads. An id the vocabulary has not named falls
 *  back to the id itself -- the same choice /api/func-ids makes -- so a group
 *  tagged with a feature this backend does not serve is still attributed rather
 *  than silently unlabelled. */
export function appliesTo(g: OptionGroup, features: Feature[]): string {
  if (!g.features.length) return ANY_DEPLOYMENT;
  return g.features
    .map((id) => features.find((f) => f.id === id)?.label ?? id).join(" · ");
}

/** The groups on screen while `feature` is being configured: its own, plus the
 *  ones that apply to any deployment. `null` -- nothing chosen yet, or the
 *  vocabulary fetch failed -- shows everything: a view that cannot be chosen
 *  must not take options off the page. */
export function visibleGroups(feature: string | null): OptionGroup[] {
  if (!feature) return OPTION_GROUPS;
  return OPTION_GROUPS.filter(
    (g) => !g.features.length || g.features.includes(feature));
}

function hiddenGroups(feature: string | null): OptionGroup[] {
  const shown = new Set(visibleGroups(feature));
  return OPTION_GROUPS.filter((g) => !shown.has(g));
}

/** Groups holding configuration while off screen. Reported near the preview:
 *  what a hidden group owns still ships, and the point of the view being a view
 *  is that it never quietly drops it. `detect` is the test rather than the
 *  toggle state, because a group switched on with nothing in it adds nothing to
 *  the manifests and saying otherwise would be noise. */
export function setButHidden(
    o: Options, feature: string | null): OptionGroup[] {
  return hiddenGroups(feature).filter((g) => g.detect(o));
}

/** Groups in use but not finished, so the download is blocked. Derived from the
 *  declarations rather than passed in: the caller knowing which groups can be
 *  incomplete is the coupling this exists to remove. */
export function incompleteGroups(
    o: Options, required: Partial<Record<GroupId, boolean>>): OptionGroup[] {
  return OPTION_GROUPS.filter((g) => g.incomplete?.(o, !!required[g.id]));
}

/** Why the download is blocked when the reason is not on screen -- the failure
 *  this view is meant to remove is a disabled button whose cause is elsewhere
 *  on the page. Which groups are unfinished is `incompleteGroups`; this is the
 *  subset of those the current view is hiding. A group that applies to any
 *  deployment is never hidden, so it can never appear here -- and its feature
 *  to switch to is `g.features[0]`, with `appliesTo` giving the label, rather
 *  than a second label lookup that could disagree with the one beside it. */
export function hiddenBlockers(
    incomplete: OptionGroup[], feature: string | null): OptionGroup[] {
  const hidden = new Set(hiddenGroups(feature));
  return incomplete.filter((g) => hidden.has(g));
}

/** The features a location's funcIds carry, in served order. funcIds the tool
 *  does not model (tdm, dataPublisher, delphix, secretsPrivateVault) match no
 *  feature and are simply not a signal -- never an error, and never a reason to
 *  leave the selector empty. */
export function featuresOf(
    funcIds: string[] | undefined, features: Feature[]): string[] {
  return features
    .filter((f) => (funcIds ?? []).some((id) => f.func_ids.includes(id)))
    .map((f) => f.id);
}

/** The funcIds a location has that no served feature claims. Named on screen
 *  rather than dropped: the tool models five funcIds and accounts already carry
 *  more, and "this location also runs X, which there are no options for" is a
 *  truthful thing to say where silence reads as coverage. */
export function unclaimedFuncIds(
    funcIds: string[] | undefined, features: Feature[]): string[] {
  return (funcIds ?? []).filter(
    (id) => !features.some((f) => f.func_ids.includes(id)));
}

/** Which feature to open a location on: the first served feature its funcIds
 *  carry, else the first served feature. A location carrying both therefore
 *  starts on the first -- performance, the common case -- and is routed to the
 *  other by the download-button block if that is where the missing settings
 *  are. `null` only before the vocabulary lands. */
export function startFeature(
    funcIds: string[] | undefined, features: Feature[]): string | null {
  return featuresOf(funcIds, features)[0] ?? features[0]?.id ?? null;
}

/** The namespace to suggest as the view moves to `feature`, or null to leave
 *  the field alone. Suggested only while it still holds a namespace some
 *  feature suggested (or nothing at all): a name that was typed outranks any
 *  suggestion, and returning the value it already has would be a state write
 *  that re-POSTs the preview for no change. Which names count as suggestions is
 *  read off the served vocabulary, so a feature added later brings its own. */
export function suggestNamespace(
    current: string, feature: Feature, features: Feature[]): string | null {
  const ns = current.trim();
  const suggested = !ns || features.some((f) => f.namespace === ns);
  return suggested && ns !== feature.namespace ? feature.namespace : null;
}
