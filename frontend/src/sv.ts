// Service virtualization, in one place.
//
// It used to be four blocks of App deriving a dozen values, two effects -- one
// of which WROTE the option the other READ to decide the same question -- and a
// reach through the option-group table to borrow the group's completeness rule.
// Nothing could test any of it: the write loop needed a rendered page and two
// renders to show itself at all.
//
// So: what the location runs, what the options currently say, and the served
// constants go in; the answer every consumer needs comes out. The page calls it
// once and hands the record on. Like optionGroups.ts, nothing here imports
// React and nothing here reaches a route -- which is what makes sv.test.ts
// possible without a DOM.
//
// The completeness rule is NOT restated here. It is the sv group's own
// (optionGroups.svIncomplete), because a group declaring when it is finished is
// what keeps "adding a feature needs no frontend change" true.
import { Options, SvBackend, SvConstants, SvScheme } from "./api";
import {
  GroupFlags, OptionPatch, SV_NONE, svConfigured, svIncomplete,
  svNodePortConflict,
} from "./optionGroups";
import { SvCtx } from "./SvPrereqs";

/** Why the chart segment is disabled for an SV location. A chart without the
 *  ingress stalls at WAITING_FOR_DOMAIN, so `--format helm` refuses one
 *  outright; the segment says so rather than disappearing. */
const HELM_BLOCKED =
  "Not for this location — service virtualization needs an ingress, its RBAC "
  + "and a TLS secret, which this chart does not carry.";

/** Everything the page, the group and the download step ask about service
 *  virtualization. One record, so a consumer takes this instead of eleven
 *  props that can only be assembled correctly one way. */
export interface Sv {
  /** Does this location advertise mockServices? Read off the served funcIds,
   *  never a copy of them here -- adding one must not leave the UI silently
   *  disagreeing with the generator. */
  location: boolean;
  /** The location's demand, answered no. A location can carry mockServices and
   *  be wanted for performance alone; the options can hold that (SV_NONE) and
   *  generate() accepts it. */
  declined: boolean;
  /** The demand *not yet answered* -- the state that blocks the download. */
  required: boolean;
  /** Is a real backend chosen? SV_NONE is an answer, not a configuration. */
  configured: boolean;
  /** Finished enough to generate. */
  ok: boolean;
  /** True when the block is the service type rather than an empty field. */
  nodePortConflict: boolean;
  /** The chosen backend, or null while nothing is chosen: the select still
   *  shows its nginx default, but no backend's prose is claimed until one is
   *  picked. */
  ingress: string | null;
  /** The backends that may be offered here -- the served list, minus the
   *  OpenShift Route on a platform that serves no route.openshift.io, which
   *  generate() refuses. */
  ingressTypes: string[];
  /** As typed, for the controlled inputs. Trimming these would stop the user
   *  typing a space; the trimmed reads are in `ctx` and the lookups. */
  fields: { subdomain: string; tlsSecret: string; gateway: string };
  /** What the prerequisite list and the endpoint host render against: filled-in
   *  values substituted for real, empty ones as their own placeholder. */
  ctx: SvCtx;
  /** What the Role grants, from /api/sv-constants -- generate.py's to state.
   *  Undefined for a backend the table does not carry. */
  rbac?: SvBackend;
  /** What a published endpoint is probed over. Follows the TLS secret, because
   *  that is what decides whether the endpoint terminates TLS. */
  scheme: SvScheme;
  /** The sentence the chart segment is disabled with, or undefined when the
   *  chart is available. */
  helmBlocked?: string;
  /** What a group cannot read off the options: SV is required by the location,
   *  not by anything configured. Keyed by group id so the walk over the groups
   *  never has to test for one by name. */
  groupRequired: Partial<GroupFlags>;
  /** ...and the same demand switched off anyway, which the row has to say
   *  rather than falling silent the moment it stopped blocking. */
  groupDeclined: Partial<GroupFlags>;
  /** Options that must change for this configuration to be generatable, or
   *  null when none must.
   *
   *  This is the write loop as a value. Two effects used to do it: one wrote
   *  `sv_ingress`, and the next render's derivation read it back to decide
   *  whether SV was configured -- untestable, and impossible to reason about
   *  from either end. Nothing here writes; the page applies the patch in one
   *  effect, and applying it makes the next answer null, which is the property
   *  sv.test.ts runs to a standstill. */
  patch: OptionPatch | null;
}

/** One read of a text option, trimmed. The `.trim()` written out per site kept
 *  getting forgotten -- an ingress pasted with a trailing space missed the
 *  backend lookup and the panel silently lost its prose. */
const txt = (o: Options, k: string) => String(o[k] ?? "").trim();

/** Everything about service virtualization, for this location and these
 *  options. Pure: the same three inputs always give the same record. */
export function svState(
    funcIds: string[] | undefined, o: Options,
    constants: SvConstants): Sv {
  const location = (funcIds ?? []).some((f) => constants.func_ids.includes(f));
  const declined = o.sv_ingress === SV_NONE;
  const required = location && !declined;

  // Everything below reads the options as they are, never as the patch will
  // leave them: a record that answered for a value nothing has written yet
  // would claim a backend nobody picked, and the patch would then be judged
  // against a state that was never on screen. The correction is one render
  // away, and one render is what it has always been.
  const ingress = txt(o, "sv_ingress");
  const openshift = o.platform === "openshift";
  return {
    location,
    declined,
    required,
    configured: svConfigured(o.sv_ingress),
    ok: !svIncomplete(o, required, constants.backends),
    nodePortConflict: svNodePortConflict(o, constants.backends),
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
    helmBlocked: required ? HELM_BLOCKED : undefined,
    groupRequired: { sv: required },
    groupDeclined: { sv: location && declined },
    patch: correction(o, required),
  };
}

/** What has to change, given how the options arrived. Every branch here is a
 *  state the *options* can reach without anyone choosing it -- an imported
 *  profile, a preset, or a location that turned out to be an SV one after the
 *  form was filled in -- so none of them can be fixed in an onChange handler.
 *
 *  Each branch is written so that applying it makes its own condition false;
 *  that is what stops the page's one effect from writing forever.
 *
 *  service_type is deliberately not touched. This used to rewrite a NODEPORT to
 *  CLUSTERIP whenever an ingress was configured; #60 showed the pairing works,
 *  so an imported profile keeps the value it arrived with. */
function correction(o: Options, required: boolean): OptionPatch | null {
  // The openshift backend publishes a route.openshift.io Route, so switching
  // the platform away from OpenShift strands sv_ingress on a value generate()
  // now refuses -- and the option itself disappears from the select, leaving
  // nothing on screen to explain the error. Fall back to nginx, which works
  // anywhere.
  const stranded = o.sv_ingress === "openshift" && o.platform !== "openshift";
  // An imported profile sets the SV options without ever calling the group's
  // enable(), and a row opened by `required` goes through detectGroups, so
  // neither path would otherwise seed sv_ingress -- leaving the select showing
  // "NGINX" off its own fallback while the state stayed null.
  const toNginx = stranded || (required && !o.sv_ingress);
  const ingress = toNginx ? "nginx" : o.sv_ingress;
  // Only crane's istio backend reads KUBERNETES_ISTIO_GATEWAY_NAME, so
  // generate() refuses it anywhere else; an imported profile pairing it with
  // another ingress would hit that error with nothing in the UI to explain it.
  // Dropped here rather than only in the select's onChange for that reason.
  const clearGateway = !!ingress && ingress !== "istio" && !!o.sv_istio_gateway;
  // A location can turn out to be an SV one after the format was picked, and an
  // imported profile can arrive already set to helm. Fall back rather than
  // leaving a disabled segment selected and every generate call failing.
  const fromHelm = required && o.output_format === "helm";
  if (!toNginx && !clearGateway && !fromHelm) return null;
  return {
    ...(toNginx ? { sv_ingress: "nginx" } : {}),
    ...(clearGateway ? { sv_istio_gateway: null } : {}),
    ...(fromHelm ? { output_format: "manifests" } : {}),
  };
}
