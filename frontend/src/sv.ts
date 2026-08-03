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

/** The served feature these options belong to, as the card and the rail key it.
 *  A literal for the same reason `groupRequired: { sv: ... }` is one: this
 *  module is service virtualization, and the id it answers under is the one
 *  thing about it that cannot be derived from its inputs.
 *
 *  It is a *feature* id, not the group id it happens to match, so it joins to
 *  core.FEATURES rather than to the group table -- and test_server.py holds it
 *  there, beside the group tags, because a rename on the server would
 *  otherwise leave the card offering switches for a bundle that cannot carry
 *  them with both suites green. */
const SV_FEATURE = "sv";

/** Why a format cannot carry service virtualization, by format.
 *
 *  Both refusals are generate()'s, and both are about the same missing thing:
 *  publishing a virtual service needs an ingress, its RBAC and a TLS secret.
 *  A chart without them stalls at WAITING_FOR_DOMAIN and a docker agent
 *  publishes nothing at all, so each segment says so rather than disappearing
 *  -- a format that vanishes leaves the page unable to explain the error the
 *  server would have given.
 *
 *  Keyed by format because there are two of them now and there is one segmented
 *  control: the panel looks each segment up rather than testing for helm and
 *  then for docker.
 *
 *  A clause, lower case and unpunctuated, as DOCKER_IGNORED's reasons are and
 *  for the same reason: three places say it now -- the disabled segment, the
 *  feature card that offers no switches, and the notice when the correction
 *  moves a format -- and each needs its own lead-in. Written as one finished
 *  sentence it read "Not for this location" in all three, which had stopped
 *  being true of any of them: what a format is refused over is the
 *  configuration, not the location (see `blockedFormats`). */
const BLOCKED_FORMATS: Record<string, string> = {
  helm: "service virtualization needs an ingress, its RBAC and a TLS secret, "
    + "which this chart does not carry",
  docker: "a docker agent publishes virtual services with HOSTNAME_OVERRIDE "
    + "and a TLS pair, which this bundle does not carry",
};

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
  /** Why each output format is unavailable, by format name. Empty when this
   *  bundle carries no virtual services, which is when all three are.
   *
   *  Keyed off what is **configured**, not off what the location demands.
   *  generate() refuses on `_sv_cfg` returning a config, and `_sv_cfg` never
   *  looks at the funcIds before it does -- so a location that demands nothing
   *  can still be configured for SV, and a docker bundle of it is refused by
   *  the server. Read off the demand, this said nothing about that case and
   *  the segment stayed enabled: the off-screen blocker, from the one end
   *  #113 left open (see `runs`).
   *
   *  It also stops answering two questions with one empty object. Read off
   *  `required` this was empty both when the location ran no virtual services
   *  and when /api/sv-constants had not landed -- `func_ids: []` makes every
   *  location a non-SV one. `svConfigured` reads an option this page wrote, so
   *  the served table cannot make it lie. */
  blockedFormats: Record<string, string>;
  /** Why this bundle's format cannot serve the feature *at all*, by feature id,
   *  or empty where it can.
   *
   *  The mirror of `blockedFormats`: that one answers "which formats may I pick
   *  given this configuration", this one answers "may I configure this at all
   *  given the format I picked". Both come from the same table, because they
   *  are the same refusal read from its two ends.
   *
   *  Keyed by feature id so the walk over the feature cards never tests for one
   *  by name, exactly as `groupRequired` is keyed by group id. Deliberately not
   *  a served "which features does a format refuse" vocabulary: helm and docker
   *  refuse *this* feature, nothing else refuses any, and one served table for
   *  one feature is a shape invented ahead of its second caller. */
  featureBlocked: Record<string, string>;
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
 *  options. Pure: the same four inputs always give the same record.
 *
 *  `runs` is `optionGroups.runsFeature(enabled, "sv")` -- does this bundle
 *  still carry SV options at all? It is not the same question as `location`,
 *  and the difference is the whole reason it is a parameter rather than
 *  derived from `funcIds` here. Three states reach this:
 *
 *  - the location runs mockServices: `runs` and `location` agree.
 *  - the location is known to run something else: `notRunPatch` is about to
 *    clear every SV option through the group's own `disable()`, so an
 *    `sv_ingress` still in the options is on its way out and must not block a
 *    format. Read from the options alone, a profile arriving with docker *and*
 *    a stranded ingress would have had its format reset on the way to having
 *    the ingress cleared -- losing a docker choice that was valid all along.
 *  - **nobody has answered** (`enabled == null` -- a location whose funcIds
 *    carry no served feature, which real accounts have: tdm, dataPublisher,
 *    delphix). Nothing clears the options there, so a configuration really can
 *    reach generate(), and this is the state the blocked formats were blind to.
 *
 *  It defaults to `true` for the same reason `runsFeature` reads an unanswered
 *  question as yes, and the direction is safe here too: over-blocking costs a
 *  segment that comes back the moment SV is declined, where under-blocking is
 *  a server refusal with nothing on screen to clear. */
export function svState(
    funcIds: string[] | undefined, o: Options,
    constants: SvConstants, runs = true): Sv {
  const location = (funcIds ?? []).some((f) => constants.func_ids.includes(f));
  const declined = o.sv_ingress === SV_NONE;
  const required = location && !declined;
  // What a helm or docker bundle is refused over: an SV configuration this
  // bundle carries, or a demand that is one render from becoming one (the seed
  // below chooses nginx for it). `required` implies `runs` -- a demand comes
  // from the funcIds a served feature is read off.
  //
  // One value because the two readers must not disagree: the control disables
  // a segment with it and `correction` moves off a selected one with it, and a
  // segment shown enabled that resets itself the moment it is picked is worse
  // than either mistake alone. It is what caught the split when they were two.
  const carries = (runs && svConfigured(o.sv_ingress)) || required;
  const blockedHere = BLOCKED_FORMATS[String(o.output_format ?? "")];

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
    blockedFormats: carries ? BLOCKED_FORMATS : {},
    // Only where the feature is still the location's to configure: a card for
    // a feature that is not run already says so, and "not enabled here" and
    // "not possible in this format" have to stay different answers.
    featureBlocked: runs && blockedHere ? { [SV_FEATURE]: blockedHere } : {},
    groupRequired: { sv: required },
    groupDeclined: { sv: location && declined },
    patch: correction(o, required, carries),
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
function correction(
    o: Options, required: boolean, carries: boolean): OptionPatch | null {
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
  // imported profile can arrive already set to one of the two that refuse it.
  // Fall back rather than leaving a disabled segment selected and every
  // generate call failing.
  //
  // `carries` is the same value the disabled segment is drawn from, so the
  // control and this cannot disagree about which formats are available. It is
  // deliberately not `stranded`, which can hold for a location known to run
  // something else: notRunPatch is clearing those options anyway, and
  // resetting the format on the way past would take away a docker choice that
  // was valid all along.
  const fromBlocked = carries
    && !!BLOCKED_FORMATS[String(o.output_format ?? "")];
  if (!toNginx && !clearGateway && !fromBlocked) return null;
  return {
    ...(toNginx ? { sv_ingress: "nginx" } : {}),
    ...(clearGateway ? { sv_istio_gateway: null } : {}),
    ...(fromBlocked ? { output_format: "manifests" } : {}),
  };
}
