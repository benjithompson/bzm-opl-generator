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

import { Options } from "./api";

export type GroupId =
  "registry" | "proxy" | "ca" | "sched" | "sizing" | "security" | "sv";

/** Merged over the current options. `null` clears a key that has a default --
 *  see App's setOptional for the keys that must be removed instead. */
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
  /** Does this config already mean the group is on? Runs on every option
   *  change, including the ones a preset or an imported profile brings in. */
  detect: (o: Options) => boolean;
  /** Applied when the switch goes on -- empty for a group that only reveals
   *  fields it does not have to seed. */
  enable: (o: Options) => OptionPatch;
  /** Applied when the switch goes off. OFF hides the fields AND wipes their
   *  options, so nothing hidden ever reaches the manifests. */
  disable: (o: Options) => OptionPatch;
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
    keys: ["private_registry", "pull_secret", "registry_auth"],
    detect: (o) => !!(o.private_registry || o.pull_secret || o.registry_auth),
    enable: () => ({}),
    disable: () => ({ private_registry: null, pull_secret: null, registry_auth: false }),
  },
  {
    id: "proxy",
    title: "HTTP(S) proxy",
    hint: "egress via a corporate proxy, optional authentication",
    keys: ["proxy"],
    detect: (o) => !!o.proxy,
    enable: () => ({}),
    disable: () => ({ proxy: null }),
  },
  {
    id: "ca",
    title: "Custom CA trust",
    hint: "TLS-intercepting proxy / private CAs — mounted into crane + engines",
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
    keys: ["tolerations", "node_selector"],
    detect: (o) => !!(o.tolerations || o.node_selector),
    enable: () => ({}),
    disable: () => ({ tolerations: null, node_selector: null }),
  },
  {
    id: "sizing",
    title: "Engine sizing",
    hint: "CPU / memory limits for load engines (default 2 CPU / 8Gi)",
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
