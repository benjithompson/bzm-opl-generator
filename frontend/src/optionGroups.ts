// The option groups of the configure step, each declared once: title, hint,
// the option keys it writes, and its lifecycle functions. Plain data with no
// React, so optionGroups.test.ts needs no DOM.

import { FuncIdVocabulary, Functionality, Options } from "./api";
import { envIncomplete } from "./env";
import { Applies, keysApply } from "./formats";

export type GroupId =
  "registry" | "proxy" | "ca" | "sched" | "security" | "sv" | "svDocker";

/** Merged over the current options. `null` clears a key back to its default;
 *  a key with no default must be left out instead, or profile.json gains it. */
export type OptionPatch = Record<string, unknown>;

export interface OptionGroup {
  id: GroupId;
  title: string;
  /** Sub-title on the row, whether the group is on or off. */
  hint: string;
  /** Shown instead of `hint` on a row the caller flags as required. */
  requiredHint?: string;
  /** Shown instead of `hint` when the location demands the group and it was
   *  switched off anyway, saying what was given up. */
  declinedHint?: string;
  /** Every option key this group writes. */
  keys: string[];
  /** The served functionality ids this group belongs to; empty means every
   *  deployment needs it. Each must be a served id (test_server.py checks), or
   *  notRunPatch would clear the group without anyone seeing why. */
  functionalities: string[];
  /** Does this config already mean the group is on? Runs on every option change. */
  detect: (o: Options) => boolean;
  /** Applied when the switch goes on; empty when there is nothing to seed. */
  enable: (o: Options) => OptionPatch;
  /** Applied when the switch goes off: clears the group's options so nothing
   *  hidden reaches the bundle. `required` is the location's demand, which a
   *  group may record as a refusal (SV does, with SV_NONE). */
  disable: (o: Options, required: boolean) => OptionPatch;
  /** In use but unfinished? `required` is the location's demand; `backends` is
   *  the served SV backend table, undefined until loaded. Absent: never. */
  incomplete?: (o: Options, required: boolean,
                backends?: Record<string, { nodeport_ok: boolean }>) => boolean;
  /** The part of `incomplete` that still stops the step. Defaults to it. */
  blocks?: (o: Options, required: boolean,
            backends?: Record<string, { nodeport_ok: boolean }>) => boolean;
  /** The keys that must be filled while the group is on, given what is chosen
   *  in it. The server cannot tell a blank registry from none, so this side says. */
  requires?: (o: Options) => string[];
}

// -- the cluster -----------------------------------------------------------------

/** Is the target cluster OpenShift itself (bundle_options.is_openshift)? `platform`
 *  is only the UID posture, which vanilla Kubernetes may share. An unanswered
 *  `openshift_cluster` is no, as in the generator. */
export const isOpenshift = (o: Options) =>
  o.platform === "openshift" && o.openshift_cluster === true;

// -- CA trust ----------------------------------------------------------------
// One-of. `file` is what this page offers: the bundle names the certificate
// file and builds the ConfigMap from it. The other ConfigMap modes come from
// the CLI or a profile.
export type CaMode = "none" | "file" | "inline" | "existing" | "inject";

export function caModeOf(o: Options): CaMode {
  // Before `inline`: the same ConfigMap, and file is the more specific answer.
  return o.ca_bundle_slot ? "file"
    : o.ca_existing_configmap != null ? "existing"
    : o.ca_bundle != null ? "inline"
    : o.ca_openshift_inject ? "inject" : "none";
}

/** The patch that puts CA trust in `mode`. The group's enable and disable use
 *  it too, so the switch and the mode control agree. */
export function caModePatch(o: Options, mode: CaMode): OptionPatch {
  return {
    ca_existing_configmap: mode === "existing" ? (o.ca_existing_configmap ?? "") : null,
    ca_configmap_key: mode === "existing" ? o.ca_configmap_key : null,
    // Never both: the generator refuses a file mode beside a PEM.
    ca_bundle: mode === "inline" ? (o.ca_bundle ?? "") : null,
    ca_bundle_slot: mode === "file",
    // Kept across mode switches, as the one typed value; cleared only when CA
    // trust is switched off.
    ca_cert_file: mode === "none" ? null : (o.ca_cert_file ?? null),
    ca_openshift_inject: mode === "inject",
  };
}

// -- engine sizing -----------------------------------------------------------
// Presets for the sizing card. Not a group: generate always emits limits,
// from the location's overrides when no option names them.
export const ENGINE_SIZES = [
  { id: "small", cpu: "1", mem: "4Gi", label: "Small — 1 CPU / 4Gi (dev clusters, light tests)" },
  { id: "standard", cpu: "2", mem: "8Gi", label: "Standard — 2 CPU / 8Gi (BlazeMeter default)" },
  { id: "large", cpu: "4", mem: "16Gi", label: "Large — 4 CPU / 16Gi (heavy scripts)" },
];

/** BlazeMeter's documented default: what the generator emits when nothing
 *  names a size. */
export const STANDARD_SIZE = ENGINE_SIZES.find((s) => s.id === "standard")!;

/** The functionalities whose agent runs a taurus engine, off the served
 *  `runs_engine`. Decides only where an engine size is stated: crane applies
 *  one limit pair to every pod, so the limits are never cleared for any. */
export function engineFunctionalities(fs: Functionality[]): string[] {
  return fs.filter((f) => f.runs_engine).map((f) => f.id);
}

// -- service account ---------------------------------------------------------
// Not a group: every deployment runs as some account.

/** Is a service account named? An empty name would fall back to the
 *  namespace's `default`, handing crane's Role to every pod there. */
export function serviceAccountOk(o: Options): boolean {
  return !!String(o.service_account_name ?? "").trim();
}

// -- service virtualization --------------------------------------------------

/** `sv_ingress` meaning "answered: no virtual services" on a mockServices
 *  location; unset means unanswered. Must equal service_virt.SV_INGRESS_NONE
 *  (test_server.py reads this literal). */
export const SV_NONE = "none";

/** Is this an SV configuration at all? SV_NONE is an answer, not one. */
export function svConfigured(ingress: unknown): boolean {
  return !!ingress && ingress !== SV_NONE;
}

/** Is SV in use but unfinished? Mirrors service_virt.sv_cfg: with an ingress
 *  chosen, the domain and TLS secret are required and NODEPORT must suit the
 *  backend; with none, only a demanding location is unfinished. An unknown
 *  backend (table not loaded) does not block; generate() refuses the real case. */
export function svIncomplete(
    o: Options, required: boolean,
    backends?: Record<string, { nodeport_ok: boolean }>): boolean {
  if (o.sv_ingress === SV_NONE) return false;
  if (!o.sv_ingress) return required;
  return !String(o.sv_subdomain ?? "").trim()
    || !String(o.sv_tls_secret ?? "").trim()
    || svNodePortConflict(o, backends);
}

/** What of svIncomplete still stops the step, now that a blank field becomes a
 *  marker: an unanswered ingress on a demanding location, or a service type the
 *  backend cannot publish over. */
function svBlocking(
    o: Options, required: boolean,
    backends?: Record<string, { nodeport_ok: boolean }>): boolean {
  if (o.sv_ingress === SV_NONE) return false;
  if (!o.sv_ingress) return required;
  return svNodePortConflict(o, backends);
}

/** A service type the chosen backend cannot publish over. The SV panel names
 *  it separately because its fix is elsewhere on the page. */
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
    // The host alone: a pull secret is optional and registry_auth is a switch.
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
    // One URL is a working proxy, and HTTPS is what BlazeMeter's traffic uses.
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
    keys: ["ca_existing_configmap", "ca_configmap_key", "ca_bundle",
           "ca_bundle_slot", "ca_cert_file", "ca_openshift_inject"],
    // Per mode. The key has a default, and injection fills a ConfigMap the
    // bundle names itself.
    requires: (o) => {
      const mode = caModeOf(o);
      if (mode === "existing") return ["ca_existing_configmap"];
      if (mode === "inline") return ["ca_bundle"];
      // `file` requires nothing: a blank name becomes <CA_CERT_FILE> and is
      // warned about.
      return [];
    },
    detect: (o) => caModeOf(o) !== "none",
    // On lands on `file`, which is complete as soon as it is picked.
    enable: (o) => caModePatch(o, "file"),
    disable: (o) => caModePatch(o, "none"),
  },
  {
    id: "sched",
    title: "Scheduling",
    hint: "node pools for crane & engines (separate pools optional)",
    functionalities: [],
    keys: ["tolerations", "node_selector", "engine_tolerations",
           "engine_node_selector"],
    // `!= null`: an empty engine override is a real setting ("take none").
    detect: (o) => !!(o.tolerations || o.node_selector)
      || o.engine_tolerations != null || o.engine_node_selector != null,
    enable: () => ({}),
    disable: () => ({ tolerations: null, node_selector: null,
                      engine_tolerations: null, engine_node_selector: null }),
  },
  {
    id: "security",
    title: "Security & RBAC",
    // True of every format; each format's defaults are in the fields.
    hint: "defaults: the credential kept apart from the configuration, no agent self-update",
    // Untagged: every deployment answers these.
    functionalities: [],
    // The sole owner of service_type.
    keys: ["use_secret", "cluster_rbac", "service_type", "restrict_engines",
           "auto_update"],
    // Only a departure from the backend default opens the group: an explicit
    // NODEPORT, restrict_engines false, or any stated auto_update.
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
    // Declining on a location that runs mockServices is allowed; this says
    // what it costs.
    declinedHint: "performance only — virtual services deployed here will stall at WAITING_FOR_DOMAIN",
    // The funcId; `sv` is only this row's id.
    functionalities: ["mockServices"],
    // Not service_type: Security owns it, and an ingress works over NODEPORT.
    keys: ["sv_ingress", "sv_subdomain", "sv_tls_secret", "sv_istio_gateway"],
    // Only once a real backend is chosen. Without one, the ingress is what is
    // missing, which is `incomplete`'s arm.
    requires: (o) => (svConfigured(o.sv_ingress)
      ? ["sv_subdomain", "sv_tls_secret"] : []),
    detect: (o) => svConfigured(o.sv_ingress),
    // Shared with sv.ts, so the row and the panel use one rule.
    incomplete: svIncomplete,
    blocks: svBlocking,
    // `{}` when an ingress is already chosen, so the options keep their
    // identity. SV_NONE has to become a real backend.
    enable: (o) => (svConfigured(o.sv_ingress) ? {} : { sv_ingress: "nginx" }),
    // On a demanding location, off is recorded as SV_NONE: null would mean
    // unanswered, which generate() refuses.
    disable: (_o, required) => ({ sv_ingress: required ? SV_NONE : null,
      sv_subdomain: null, sv_tls_secret: null, sv_istio_gateway: null }),
  },
  {
    // Service virtualization the docker agent's way. Each SV group's keys are
    // the other format's ignored options, so only one is ever on screen.
    id: "svDocker",
    title: "Virtual service endpoints",
    hint: "the hostname this agent advertises, and the certificate it serves them with",
    functionalities: ["mockServices"],
    keys: ["sv_hostname", "sv_tls_cert", "sv_tls_key"],
    // The hostname is what the group is: endpoint URLs are built from it, or
    // from the host's IP without one. The cert/key pair joins once either is set.
    requires: (o) => ["sv_hostname",
      ...(o.sv_tls_cert || o.sv_tls_key ? ["sv_tls_cert", "sv_tls_key"] : [])],
    detect: (o) => !!(o.sv_hostname || o.sv_tls_cert || o.sv_tls_key),
    // Nothing to seed: the hostname is for the customer's DNS to settle.
    enable: () => ({}),
    disable: () => ({ sv_hostname: null, sv_tls_cert: null, sv_tls_key: null }),
    // No `incomplete`: a blank field becomes a marker, and what generate()
    // refuses (an unreadable key, an uncovered hostname) is typed, not blank.
  },
];

export const GROUP_BY_ID = Object.fromEntries(
  OPTION_GROUPS.map((g) => [g.id, g])) as Record<GroupId, OptionGroup>;

export type GroupFlags = Record<GroupId, boolean>;

export const allGroupsOff = (): GroupFlags =>
  Object.fromEntries(OPTION_GROUPS.map((g) => [g.id, false])) as GroupFlags;

/** Which groups are open. Sticky: it only ever opens one. `required` opens a
 *  group the options cannot, like SV on an SV location. */
export function detectGroups(
    o: Options, prev: GroupFlags,
    required: Partial<GroupFlags> = {}): GroupFlags {
  return Object.fromEntries(OPTION_GROUPS.map((g) =>
    [g.id, prev[g.id] || g.detect(o) || !!required[g.id]])) as GroupFlags;
}

// -- the split the configure step is built on --------------------------------
// A group belongs to no functionality (in every bundle) or to one (in its card).

/** Groups no functionality owns: every deployment gets them. */
export const SHARED_GROUPS = OPTION_GROUPS.filter((g) => !g.functionalities.length);

/** The groups a functionality owns. Empty means it adds no options, and its
 *  card says so. */
export function groupsOf(functionalityId: string): OptionGroup[] {
  return OPTION_GROUPS.filter((g) => g.functionalities.includes(functionalityId));
}

// -- where an option is set ---------------------------------------------------
// For a reserved variable somebody looks for in the environment area: the
// option that writes it and the section holding that option, off each group's keys.

/** A reserved variable, and where the thing that writes it is set. */
interface ReservedWhere {
  name: string;
  /** The option that writes it, or null where none does (the identity, a fixed
   *  posture). */
  owner: string | null;
  /** The title of the group holding that option, or null (the engine limits
   *  are stated, not edited, so they have none). */
  where: string | null;
}

/** Where `name` is set, or null if it is not reserved. Also null before the
 *  table lands: claiming a name is taken on no evidence is the worse error. */
export function reservedWhere(
    name: string, reserved: Record<string, string | null>): ReservedWhere | null {
  if (!(name in reserved)) return null;
  const owner = reserved[name];
  // A one-of owner such as "ca_bundle | ca_existing_configmap" is split.
  const keys = owner ? owner.split("|").map((k) => k.trim()) : [];
  const group = OPTION_GROUPS.find((g) => keys.some((k) => g.keys.includes(k)));
  return { name, owner, where: group?.title ?? null };
}

/** ...and all of them in served order, rendered as a list the browser's find
 *  can search. */
export function reservedList(
    reserved: Record<string, string | null>): ReservedWhere[] {
  return Object.keys(reserved)
    .map((name) => reservedWhere(name, reserved))
    .filter((r): r is ReservedWhere => r !== null);
}

// -- a functionality the location does not run --------------------------------
// Not shown on the configure step, except in manual entry where the card is
// the declaration. notRunPatch clears its options.

/** Which functionalities this location runs, or null while nobody has said.
 *  Manual entry answers with its declaration; a connected location with its
 *  funcIds, where none recognised is null rather than []. */
export function enabledFunctionalities(
    mode: "connect" | "manual", declared: string[],
    locFunctionalities: string[]): string[] | null {
  // Manual entry declares, so nothing is outstanding.
  if (mode === "manual") return declared;
  // Not read yet, or no served functionality among the funcIds.
  return locFunctionalities.length ? locFunctionalities : null;
}

/** The declaration after ticking or unticking `id`, kept in `order` so the
 *  funcIds do not reshuffle. Ids `order` lacks are kept (a location may hold a
 *  retired funcId); `excludes(id)` lists what a tick removes. May empty the list. */
export function toggleDeclared(
    declared: string[], id: string, on: boolean, order: string[],
    excludes: (id: string) => string[]): string[] {
  const want = new Set(declared);
  if (on) {
    want.add(id);
    for (const gone of excludes(id)) want.delete(gone);
  } else want.delete(id);
  // Ids `order` lacks keep their declared order, with a new one last.
  return [...order.filter((f) => want.has(f)),
          ...[...want].filter((f) => !order.includes(f))];
}

/** Does this location run the functionality? Unanswered counts as yes: a
 *  switch shown too early is corrected, one hidden on a guess strands a location. */
export function runsFunctionality(
    enabled: string[] | null, functionalityId: string): boolean {
  return enabled == null || enabled.includes(functionalityId);
}

/** The patch clearing options for functionalities the location does not run,
 *  or null. Built from each group's own `disable()`; applying it makes the next
 *  answer null, so the page settles in one pass. The engine limits reach every
 *  pod and are never cleared. */
export function notRunPatch(
    o: Options, enabled: string[] | null): OptionPatch | null {
  const patch: OptionPatch = {};
  for (const g of OPTION_GROUPS) {
    if (g.functionalities.length && g.detect(o)
        && !g.functionalities.some((f) => runsFunctionality(enabled, f))) {
      Object.assign(patch, g.disable(o, false));
    }
  }
  return Object.keys(patch).length ? patch : null;
}

/** ...and of those, the ones this format can carry: a group whose every key
 *  is ignored is dropped, one with some keeps its row. */
export function groupsFor(gs: OptionGroup[], applies: Applies): OptionGroup[] {
  return gs.filter((g) => keysApply(g.keys, applies));
}

/** Groups in use but unfinished. Only groups this format shows count, so a
 *  hidden one never blocks; `applies` absent means every field applies. */
export function incompleteGroups(
    o: Options, required: Partial<Record<GroupId, boolean>>,
    backends?: Record<string, { nodeport_ok: boolean }>,
    applies: Applies = () => true): OptionGroup[] {
  return groupsFor(OPTION_GROUPS, applies)
    .filter((g) => g.incomplete?.(o, !!required[g.id], backends));
}

/** Groups the step cannot go past: each one's `blocks ?? incomplete`. */
export function blockingGroups(
    o: Options, required: Partial<Record<GroupId, boolean>>,
    backends?: Record<string, { nodeport_ok: boolean }>,
    applies: Applies = () => true): OptionGroup[] {
  return groupsFor(OPTION_GROUPS, applies)
    .filter((g) => (g.blocks ?? g.incomplete)?.(o, !!required[g.id], backends));
}

/** What stops the configure step, as a sentence naming the rows to fix, or ""
 *  when nothing does (which marks the step done). */
export function configureBlockedBy(
    o: Options, blocking: OptionGroup[]): string {
  const needs = [
    // A blank namespace or service account becomes a marker: warned, not blocking.
    ...blocking.map((g) => g.title),
    // Not a group, but its editor can hold a name no process could read.
    envIncomplete(o) ? "the environment variables" : "",
  ].filter(Boolean);
  if (!needs.length) return "";
  const list = needs.length === 1 ? needs[0]
    : `${needs.slice(0, -1).join(", ")} and ${needs[needs.length - 1]}`;
  return `${list} first`;
}

// -- the served vocabulary ---------------------------------------------------
// Functionalities are never enumerated here: labels, namespaces and ids come
// from /api/functionalities.

/** The functionalities a location's funcIds carry, in served order. A covered
 *  funcId is a functionality id; the rest match nothing. */
export function functionalitiesOf(
    funcIds: string[] | undefined, functionalities: Functionality[]): string[] {
  return functionalities
    .filter((f) => (funcIds ?? []).includes(f.id))
    .map((f) => f.id);
}

/** The funcIds a location carries that no card claims, named rather than
 *  dropped so silence does not read as coverage. `uncovered` are served by the
 *  account (display names); `retired` are not (raw ids). Browser pins are
 *  parameters and skipped. Both are empty until the account's vocabulary is read. */
export type UnclaimedFuncIds = {
  /** Served by the account, configured nowhere here. Display names. */
  uncovered: string[];
  /** Not served by the account any more. Raw funcIds. */
  retired: string[];
};

export function unclaimedFuncIds(
    funcIds: string[] | undefined, functionalities: Functionality[],
    vocabulary: FuncIdVocabulary): UnclaimedFuncIds {
  if (vocabulary.source !== "account") return { uncovered: [], retired: [] };
  const pins = new Set(vocabulary.choices.flatMap((c) => c.sub_func_ids));
  const served = new Map(vocabulary.choices.map((c) => [c.id, c.label]));
  const rest = (funcIds ?? []).filter(
    (id) => !functionalities.some((f) => f.id === id) && !pins.has(id));
  return {
    // A served row always has a label; the server falls back to the id.
    uncovered: rest.flatMap((id) => served.get(id) ?? []),
    retired: rest.filter((id) => !served.has(id)),
  };
}

/** Which functionality to open a location on: the first served one its
 *  funcIds carry, else the first served. Null only before the vocabulary lands. */
export function startFunctionality(
    funcIds: string[] | undefined, functionalities: Functionality[]): string | null {
  return functionalitiesOf(funcIds, functionalities)[0]
    ?? functionalities[0]?.id ?? null;
}

/** The namespace to suggest for `functionality`, or null to leave the field
 *  alone: only a blank or suggested name is replaced, and never with itself. */
export function suggestNamespace(
    current: string, functionality: Functionality,
    functionalities: Functionality[]): string | null {
  const ns = current.trim();
  const suggested = !ns || functionalities.some((f) => f.namespace === ns);
  return suggested && ns !== functionality.namespace ? functionality.namespace : null;
}
