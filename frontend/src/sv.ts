// Service virtualization, answered once: what the location runs, the options
// and the served constants go in, one record comes out. No React and no routes,
// so sv.test.ts needs no DOM. Completeness is the sv group's own rule
// (optionGroups.svIncomplete), not restated here.
import { Options, SvBackend, SvConstants, SvScheme } from "./api";
import { Applies } from "./formats";
import {
  GroupFlags, isOpenshift, OptionPatch, SV_NONE,
  svConfigured, svIncomplete, svNodePortConflict,
} from "./optionGroups";

/** What the SV prerequisite prose is rendered against. */
export type SvCtx = { ns: string; dom: string; secret: string; gateway: string };

/** The functionality id (the funcId) these options belong to. test_server.py
 *  holds it to the served list; App uses this rather than its own literal. */
export const SV_FUNCTIONALITY = "mockServices";

// -- and the location it wants to itself --------------------------------------
// Crane applies one CPU/memory limit pair to every pod it creates, and an SV
// agent runs no engine, so SV and the engine functionalities should not share a
// location. Where a location is being decided (manual entry, the create form)
// that is applied; in connect mode the location exists, so it is only warned.

/** What declaring `id` takes away, given the funcIds that run an engine. A
 *  funcId neither side names excludes nothing. Curried for toggleDeclared. */
export function exclusiveWith(engines: string[]): (id: string) => string[] {
  return (id) => {
    if (id === SV_FUNCTIONALITY) return engines;
    return engines.includes(id) ? [SV_FUNCTIONALITY] : [];
  };
}

/** ...and why, in the sentence both deciding surfaces show. Plain text. */
export const SV_ALONE =
  "Service virtualization is declared on its own: the agent applies one CPU "
  + "and memory limit pair to every pod it creates, so engine sizing and mock "
  + "throughput cannot be set apart. Ticking it clears Performance and GUI "
  + "Functional, and ticking either of those clears it.";

/** Does this location already mix SV with an engine functionality? A warning
 *  in connect mode, never a blocker. */
export function svMixedWithEngines(ids: string[], engines: string[]): boolean {
  return ids.includes(SV_FUNCTIONALITY) && ids.some((f) => engines.includes(f));
}

/** ...said, naming where it can be changed, since this page cannot. */
export const SV_MIXED =
  "This location runs service virtualization alongside load or browser tests. "
  + "The agent applies one CPU and memory limit pair to every pod it creates, "
  + "so a single number sizes both the engines and the mocks; service "
  + "virtualization is better off on a location of its own. Nothing here "
  + "changes what a location runs, which is BlazeMeter's own location "
  + "settings.";


/** Everything the page, the group and the download step ask about service
 *  virtualization, as one record. */
export interface Sv {
  /** Does this location advertise mockServices? Read off the served funcIds. */
  location: boolean;
  /** The location's demand, answered no (SV_NONE): wanted for performance alone. */
  declined: boolean;
  /** The demand *not yet answered* -- the state that blocks the download. */
  required: boolean;
  /** Is a real backend chosen? SV_NONE is an answer, not a configuration. */
  configured: boolean;
  /** Finished enough to generate. */
  ok: boolean;
  /** True when the block is the service type rather than an empty field. */
  nodePortConflict: boolean;
  /** The chosen backend, or null while none is chosen (the select still shows
   *  nginx, but no backend's prose is claimed). */
  ingress: string | null;
  /** The backends that may be offered: the served list, minus the OpenShift
   *  Route where the cluster is not OpenShift. */
  ingressTypes: string[];
  /** As typed, for the controlled inputs; `ctx` has the trimmed reads. */
  fields: { subdomain: string; tlsSecret: string; gateway: string };
  /** What the prerequisite list and the endpoint host render against; blanks
   *  show as their own placeholder. */
  ctx: SvCtx;
  /** What the Role grants for this backend, from the served table. */
  rbac?: SvBackend;
  /** What a published endpoint is probed over: https when a TLS secret is set. */
  scheme: SvScheme;
  /** SV required by the location rather than by the options, keyed by group id. */
  groupRequired: Partial<GroupFlags>;
  /** ...and that demand switched off anyway, which the row states. */
  groupDeclined: Partial<GroupFlags>;
  /** Options that must change for this to be generatable, or null. Applying it
   *  makes the next answer null, so one effect settles it (sv.test.ts checks). */
  patch: OptionPatch | null;
}

/** A text option, trimmed. */
const txt = (o: Options, k: string) => String(o[k] ?? "").trim();

/** Everything about service virtualization for this location and these
 *  options. Pure.
 *
 *  `runs` is whether the bundle still carries SV at all (runsFunctionality),
 *  which differs from `location` in manual entry and while notRunPatch is
 *  clearing a stranded configuration. Defaults to true, as unanswered does.
 *  `applies` says whether the ingress options reach this format. */
export function svState(
    funcIds: string[] | undefined, o: Options,
    constants: SvConstants, runs = true, applies: Applies = () => true): Sv {
  const location = (funcIds ?? []).some((f) => constants.func_ids.includes(f));
  // Docker publishes virtual services with its own options, so everything
  // about an ingress is gated on the ingress option applying to this format.
  const k8s = applies("sv_ingress");
  const declined = o.sv_ingress === SV_NONE;
  // `runs` is a conjunct: in manual entry the facts trail the declaration, and
  // without it notRunPatch and the correction would fight over sv_ingress.
  // `required` is also the ingress group's, which a docker bundle's is not.
  const demand = runs && location && !declined;
  const required = k8s && demand;

  // Read from the options as they are, never as the patch will leave them.
  const ingress = txt(o, "sv_ingress");
  const openshift = isOpenshift(o);
  return {
    location,
    declined,
    required,
    configured: svConfigured(o.sv_ingress),
    // Nothing is unfinished about ingress fields a format does not have.
    ok: !k8s || !svIncomplete(o, required, constants.backends),
    nodePortConflict: k8s && svNodePortConflict(o, constants.backends),
    ingress: o.sv_ingress == null ? null : String(o.sv_ingress),
    ingressTypes: constants.ingress_types.filter(
      (t) => t !== "openshift" || openshift),
    fields: {
      subdomain: String(o.sv_subdomain ?? ""),
      tlsSecret: String(o.sv_tls_secret ?? ""),
      gateway: String(o.sv_istio_gateway ?? ""),
    },
    ctx: {
      ns: txt(o, "namespace") || "<namespace>",
      dom: txt(o, "sv_subdomain") || "<domain>",
      secret: txt(o, "sv_tls_secret") || "<tls-secret>",
      gateway: txt(o, "sv_istio_gateway"),
    },
    rbac: constants.backends[ingress],
    scheme: txt(o, "sv_tls_secret") ? "https" : "http",
    groupRequired: { sv: required },
    // Both are about the ingress group; svDocker has no required state.
    groupDeclined: { sv: k8s && location && declined },
    patch: correction(o, required, k8s),
  };
}

/** What has to change, for states the options reach without anyone choosing
 *  them (an import, a preset, a location read later). Each branch makes its own
 *  condition false once applied. service_type is never touched. */
function correction(
    o: Options, required: boolean, k8s: boolean): OptionPatch | null {
  // The openshift backend needs an OpenShift cluster; strand it and fall back
  // to nginx. Only where the format has an ingress field at all.
  const stranded = k8s && o.sv_ingress === "openshift" && !isOpenshift(o);
  // An import or a required row never calls the group's enable(), so seed it.
  const toNginx = stranded || (required && !o.sv_ingress);
  const ingress = toNginx ? "nginx" : o.sv_ingress;
  // Only the istio backend reads a gateway name; generate() refuses it elsewhere.
  const clearGateway = !!ingress && ingress !== "istio" && !!o.sv_istio_gateway;
  if (!toNginx && !clearGateway) return null;
  return {
    ...(toNginx ? { sv_ingress: "nginx" } : {}),
    ...(clearGateway ? { sv_istio_gateway: null } : {}),
  };
}
