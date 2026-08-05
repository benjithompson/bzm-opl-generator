// Step 2: what goes in the bundle.
//
// It opens with the platform, because that is the question every other one on
// this page depends on. It used to be asked on the download step instead, one
// step too late: this form asks for a namespace, a ServiceAccount, node
// selectors and engine limits, and a docker bundle -- one agent as one
// container -- carries none of them. The generator names what it dropped in the
// bundle's README, which is honest and arrives after the fact; a control that
// is not on screen cannot be believed to have applied. So the format is chosen
// first and the form follows it, from formats.optionApplies over the
// generator's own DOCKER_IGNORED.
//
// The option groups split two ways and the page says which is which. Most of
// them belong to no functionality -- registry, proxy, CA trust, scheduling, security
// -- and are here whatever the location runs; the rest belong to one, and live
// in that functionality's own card, which is on screen only for a functionality the
// location actually runs. There used to be a functionality *selector* switching
// between two views of the same six groups, so pressing it changed a single row
// while reading as though it changed the step. Nothing is hidden by a *view*
// now, so nothing has to be recovered: no "also in this bundle", no "not in
// view". What the format takes off screen is a different thing entirely -- not
// a view over a bundle's options, but options that bundle has no such thing as.
//
// The rail is orientation, not navigation-with-a-dot: it names what is set, so
// "what is in this bundle" is answered without scrolling the form.
import { ReactNode, useState } from "react";
import { Functionality, Options } from "../api";
import {
  Button, Check, Field, inputCls, RequiredMark, SegmentedControl,
} from "../components";
import { envToRows } from "../env";
import { Applies, keysApply, OUTPUT_FORMATS } from "../formats";
import { GroupRow } from "../groups/GroupRow";
import {
  GroupFlags, GroupId, groupsFor, groupsOf, OptionGroup, runsFunctionality,
  SHARED_GROUPS, SIZING_FUNCTIONALITY,
} from "../optionGroups";
import { placeholderWarning } from "../placeholder";

export interface ConfigurePanelProps {
  functionalities: Functionality[];
  /** `suggestNs` is passed only where picking is a declaration rather than a
   *  view -- manual entry's radio. See its call site. */
  pickFunctionality: (id: string, suggestNs?: boolean) => void;
  sourceMode: "connect" | "manual";
  /** The funcIds this location carries that no functionality claims. */
  locUnclaimed: string[];
  /** Which functionalities this location runs, or null while nobody has answered --
   *  see optionGroups.enabledFunctionalities. A functionality not in it is stated by its
   *  card and configured nowhere. */
  enabled: string[] | null;
  options: Options;
  set: (k: string, v: unknown) => void;
  /** What the bundle is, and the one option this step writes as a write rather
   *  than as a key. First on the page because it decides what the rest of it
   *  asks: see the header. */
  format: string;
  setFormat: (v: string) => void;
  /** Why a format is unavailable for this bundle, by format id -- from sv.ts,
   *  and empty where nothing configured needs an ingress. The segment says
   *  so rather than disappearing: a format that vanishes leaves the page unable
   *  to explain the error the server would have given. */
  blockedFormats: Record<string, string>;
  /** ...and the same refusal from the other end: why this format cannot serve
   *  a functionality at all, by functionality id. The card renders it instead of its
   *  switches, so a docker bundle stops offering an ingress, a subdomain and a
   *  TLS secret that would make the whole bundle unbuildable. */
  functionalityBlocked: Record<string, string>;
  /** The format the SV correction replaced, and why -- or null. A format the
   *  user picked is never swapped in silence; see the effect in App. */
  formatNotice: { was: string; why: string } | null;
  /** Does this option reach anything in a bundle of this format? Everything
   *  below hides by it -- whole groups, the placement card, Advanced, and the
   *  individual fields inside a group's own body. From formats.ts, over the
   *  generator's DOCKER_IGNORED. */
  applies: Applies;
  grpOn: GroupFlags;
  grpRequired: Partial<GroupFlags>;
  grpDeclined: Partial<GroupFlags>;
  /** The engine size this bundle will carry, as prose -- a statement, not an
   *  editor (#132): the size derives from the location's engine requests and
   *  is set there (Location settings), so there is nothing here to toggle,
   *  fill in or leave blank. Null where the format has no such env (docker),
   *  and the performance card is where it renders, in the slot the sizing
   *  group used to hold. */
  engineNote: string | null;
  flipGroup: (id: GroupId, on: boolean) => void;
  groupBody: Record<GroupId, ReactNode>;
  /** The environment variables, which are not a group: a list of everything
   *  BlazeMeter documents that no group here already writes, closed by default
   *  like Advanced. Assembled in App with the rest of the domain state; this
   *  panel decides only where it sits. */
  envArea: ReactNode;
  /** Groups in use but unfinished. Some of these block the step and some only
   *  say so on their own row -- see blockingGroups. */
  incomplete: OptionGroup[];
  /** Required fields left empty, which will carry `<PLACEHOLDER>` into the
   *  bundle. Not a blocker: the step advances and the bundle says of itself
   *  that it is unfinished. Named here so the person can fill them in while
   *  looking at them, which is the one place that is easy. */
  blanks: string[];
  namespaceOk: boolean;
  saOk: boolean;
  saCreate: boolean;
  exportProfile: () => void;
  importProfile: (f: File) => void;
}

function rows(p: ConfigurePanelProps, gs: OptionGroup[]) {
  return gs.map((g) => (
    <GroupRow key={g.id} group={g} on={p.grpOn[g.id]}
      required={!!p.grpRequired[g.id]} declined={!!p.grpDeclined[g.id]}
      applies="" onFlip={(v) => p.flipGroup(g.id, v)}>
      {p.groupBody[g.id]}
    </GroupRow>
  ));
}

/** Namespace and service account. Not behind a switch like everything else
 *  here: a deployment into a cluster has both, and putting the required half of
 *  a pair behind a toggle makes it look optional.
 *
 *  Its own card rather than the first rows of the settings list, because it is
 *  the part of this step a docker bundle does not have at all -- containers are
 *  not namespaced and there is no ServiceAccount to run as. A section that
 *  appears and disappears has to be a section. */
function CoreFields(p: ConfigurePanelProps) {
  // The asterisk says the field is required; the input's border says whether it
  // has been filled in. Two jobs, and the badge that used to do both said
  // "REQUIRED" in red on a form where nothing was wrong yet.
  const ok = () => <RequiredMark />;
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
          Namespace{ok()}
        </span>
        <input className={inputCls + (p.namespaceOk ? " border-emerald-400" : " border-red-300")}
          value={String(p.options.namespace ?? "")} placeholder="e.g. blazemeter"
          onChange={(e) => p.set("namespace", e.target.value)} />
      </label>
      <div className="grid grid-cols-[1fr_auto] gap-4 items-start">
        <label className="block">
          <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
            Service account{ok()}
          </span>
          <input className={inputCls + (p.saOk ? "" : " border-red-300")}
            value={String(p.options.service_account_name ?? "")}
            placeholder="e.g. crane"
            onChange={(e) => p.set("service_account_name", e.target.value)} />
          <span className="text-[11px] text-slate-400">
            what the agent runs as, and what the RoleBinding grants to
          </span>
        </label>
        <div className="pt-5 w-52">
          <Check label="Create it"
            hint={p.saCreate ? "the bundle includes the ServiceAccount"
              : "referenced, not created — a wrong name leaves the agent pod unscheduled"}
            checked={p.saCreate}
            onChange={(v) => p.set("service_account_create", v)} />
        </div>
      </div>
    </div>
  );
}

/** A row in the settings list that has no switch: a title, a hint, and a body
 *  that is closed until it is opened.
 *
 *  Advanced was the only one, and the environment variables became the second
 *  when they stopped being a group (#131 made them one). A switch belongs on a
 *  group because OFF is an answer -- it wipes the options behind it -- and
 *  neither of these has one to give: the security posture is always set to
 *  something, and a list of variables is not on or off, its rows are.
 */
function FoldRow(props: {
  title: string; hint: string; children: ReactNode;
  /** A word or two visible while closed. A fold that says nothing is one you
   *  have to open to find out whether you needed to. */
  summary?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="px-3 py-2.5">
      <button className="w-full flex items-center gap-3 text-left"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="text-slate-400 text-xs w-3">{open ? "▾" : "▸"}</span>
        <span className="min-w-0 grow">
          <span className="block text-sm font-medium text-slate-500">
            {props.title}
            {props.summary && (
              <span className="ml-2 text-[11px] font-normal text-bzm">
                {props.summary}
              </span>
            )}
          </span>
          <span className="block text-[11px] text-slate-400">{props.hint}</span>
        </span>
      </button>
      {open && <div className="mt-3 pl-6">{props.children}</div>}
    </div>
  );
}

/** Advanced, as a row in the settings list rather than a dashed box under it.
 *  It is two fields with the same weight as any other pair here; what makes it
 *  advanced is that it is closed, not that it sits outside the form.
 *
 *  The posture and the cluster are two questions, and the recommended posture is
 *  exactly where they come apart: SCC-friendly means the cluster assigns the
 *  UID, which vanilla Kubernetes does too, so `platform: openshift` was
 *  answering "is this OpenShift?" for every bundle that took the default -- and
 *  answering it yes. What that reached is everything the bundle tells somebody
 *  to *run*: a plain Kubernetes customer was handed a README, a verify block and
 *  a node-pool recipe written in `oc`. The pinned-UID posture is named `k8s` and
 *  says so, which is why the second question is asked under one of the two. */
function AdvancedRow(p: ConfigurePanelProps) {
  const posture = p.options.platform === "openshift";
  // Absent is the default, which is on -- the same reading as the generator's,
  // and the reason the control is a select rather than a checkbox reading
  // Boolean(): an untouched bundle is an OpenShift one and has to show as one.
  const openshift = p.options.openshift_cluster !== false;
  return (
    <FoldRow title="Advanced"
      hint="security posture, cluster and UID — you should not need these">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Security posture"
          hint="SCC-friendly works on OpenShift and vanilla k8s; the pinned-UID variant is only for clusters that reject it">
          <select className={inputCls} value={String(p.options.platform)}
            onChange={(e) => p.set("platform", e.target.value)}>
            <option value="openshift">Unified SCC-friendly (recommended)</option>
            <option value="k8s">Legacy pinned-UID k8s</option>
          </select>
        </Field>
        {posture && p.applies("openshift_cluster") && (
          <Field label="Cluster"
            hint="which commands the bundle's instructions are written in, and whether OpenShift-only options are offered">
            <select className={inputCls} value={openshift ? "openshift" : "k8s"}
              onChange={(e) => {
                const on = e.target.value === "openshift";
                p.set("openshift_cluster", on);
                // Hiding the radio is only half of it: OpenShift injection off
                // OpenShift emits a labeled ConfigMap nothing ever fills, so the
                // agent trusts nothing extra and the bundle looks configured.
                // Same rule as notRunPatch -- clear what the control that
                // wrote it can no longer show.
                if (!on) p.set("ca_openshift_inject", false);
              }}>
              <option value="openshift">OpenShift — oc</option>
              <option value="k8s">Plain Kubernetes — kubectl</option>
            </select>
          </Field>
        )}
        {!posture && (
          <Field label="runAsUser / runAsGroup">
            <input type="number" className={inputCls}
              value={Number(p.options.run_as_user ?? 1337)}
              onChange={(e) => p.set("run_as_user", Number(e.target.value))} />
          </Field>
        )}
      </div>
    </FoldRow>
  );
}

/** One functionality: whether it is on, and -- only where it is -- the options it
 *  owns.
 *
 *  In connect mode every card rendered is one the location runs: the panel
 *  filters the rest out before this is reached. It has been three things in
 *  turn. Half-configurable first: the card offered "Enable on this location…",
 *  which PATCHed the location's funcIds, and its group rows sat under a div
 *  carrying both `pointer-events-none` and the click handler that opened that
 *  offer -- so the rows were simply dead. Then stated and nothing more (#113),
 *  because turning a funcId on changes what the location *is*, which is
 *  BlazeMeter's own UI's to do. Now not rendered at all, because a card that
 *  can only be read is a card that only takes up the step.
 *
 *  So `!on` here means manual entry, where the radio is the declaration rather
 *  than a report of one, and an undeclared functionality has to stay on screen to be
 *  declarable. That is why the branch below has one sentence and not two. */
function FunctionalityCard(
    p: ConfigurePanelProps & { feat: Functionality; own: OptionGroup[] }) {
  const { feat, own } = p;
  // The engine-size statement renders where the sizing group used to sit:
  // under the functionality whose bundles start engines. Read-only by design --
  // the size is the location's, and this card only states it.
  const note = feat.id === SIZING_FUNCTIONALITY ? p.engineNote : null;
  const manual = p.sourceMode === "manual";
  // Enabled means the location runs it -- or, in manual mode, that this is what
  // the typed identity was declared to be. Unanswered reads as on: see
  // runsFunctionality for why that direction is the safe one.
  const on = runsFunctionality(p.enabled, feat.id);
  // Before the account has been read there is nothing to say: `enabled` is null
  // then, and claiming "enabled" from an unanswered question is the collapse
  // this codebase keeps refusing to make.
  const known = p.enabled != null;
  // ...and the third thing that can be true of a card, which is about the
  // bundle rather than the location: this format cannot serve this functionality at
  // all. Only asked where the location does run it -- "not enabled here" and
  // "not possible in this format" are different answers and the card may give
  // only the one that is true.
  const noFormat = on ? p.functionalityBlocked[feat.id] : undefined;
  return (
    <div id={"cfg-f-" + feat.id}
      className={"scroll-mt-4 rounded-xl border " + (on
        ? "border-bzm/40 bg-bzm/[0.03]" : "border-slate-200 bg-slate-50/70")}>
      <div className="px-3 py-2.5 border-b border-slate-100">
        {/* State first: whether the functionality is on is what the card is about.
            Manual mode has no account to read the answer off, so there it is
            the control rather than a chip. */}
        {manual ? (
          <label className="flex items-center gap-2 text-[11px] font-medium text-slate-600 mb-1">
            {/* ...and it suggests a namespace, which the same control does not
                do connected. The rule there is that switching a *view* must not
                change the bundle; here the radio is not a view, it is the
                declaration -- picking service virtualization is choosing to
                build an SV bundle, and connected the equivalent act (picking an
                SV location) suggests one too. It only ever replaces a namespace
                nothing has typed over (suggestNamespace), so a hand-written one
                still wins. */}
            <input type="radio" checked={on}
              onChange={() => p.pickFunctionality(feat.id, true)} />
            Enabled
          </label>
        ) : known && (
          <span className={"inline-block mb-1 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 "
            + (on ? "bg-emerald-100 text-emerald-700" : "bg-slate-200 text-slate-500")}>
            {on ? "Enabled" : "Not enabled"}
          </span>
        )}
        <p className={"text-sm font-medium " + (on ? "text-slate-900" : "text-slate-500")}>
          {feat.label}
        </p>
        <p className="text-[11px] text-slate-400">{feat.hint}</p>
      </div>

      {/* Four answers, and they stay four. Not declared (manual entry only --
          see the docstring): which control declares it, and none of its own.
          Run but not by a bundle of this format: which format would, and no
          control either -- the switches would configure a bundle the generator
          refuses outright. Run with nothing of its own: said so, rather than
          left blank. Run: its rows.

          `!on` rather than `manual && !on` on purpose: if a card the location
          does not run ever reached this again, a sentence is the safe thing to
          land on and live switches are not. */}
      {!on ? (
        <p className="px-3 py-3 text-[11px] text-slate-500">
          Not what this identity was declared to run — pick <b>Enabled</b> above
          to configure it.
        </p>
      ) : noFormat ? (
        <p className="px-3 py-3 text-[11px] text-slate-500">
          Not possible in this bundle — {noFormat}. Pick{" "}
          <b>Kubernetes manifests</b> above to configure it.
        </p>
      ) : own.length || note ? (
        <div className="divide-y divide-slate-100">
          {rows(p, own)}
          {note && (
            <p className="px-3 py-3 text-[11px] text-slate-500">
              <span className="font-medium text-slate-700">Engine size.</span>{" "}
              {note}
            </p>
          )}
        </div>
      ) : (
        <p className="px-3 py-3 text-[11px] text-slate-400">
          nothing extra to configure — it uses the settings above
        </p>
      )}
    </div>
  );
}

/** Every option the placement card owns, and every option Advanced owns.
 *  Neither is a declared group, so neither can be filtered by `groupsFor`, and
 *  both own more than one key -- Advanced tested only `platform` for a while,
 *  which happened to be right and would have stopped being so the moment
 *  `run_as_user` and it parted company. */
const PLACEMENT_KEYS = ["namespace", "service_account_name",
                        "service_account_create"];
const ADVANCED_KEYS = ["platform", "openshift_cluster", "run_as_user"];
// One key, and it applies to every format -- the ConfigMap for manifests,
// `extraEnv` in the overlay for helm, `--env` flags for docker. Asked anyway:
// a section that reads the table is one that keeps agreeing with it.
const ENV_KEYS = ["extra_env"];

export function ConfigurePanel(p: ConfigurePanelProps) {
  // Placement is a section of the form only where the bundle has one, and its
  // groups are the format's rather than the functionality's. Both are answered once
  // and shared with the rail below: derived twice, the rail and the form are
  // free to disagree about what is in this bundle, which is the one thing the
  // rail is for.
  const placed = keysApply(PLACEMENT_KEYS, p.applies);
  // How many variables are set, for the fold's own summary and for the rail:
  // the environment area is not a group, so `grpOn` says nothing about it and
  // the two would otherwise disagree about what is in this bundle -- the one
  // job the rail has.
  const envCount = envToRows(p.options.extra_env).length;
  // A functionality the location does not run is not on this page at all.
  //
  // It used to be a card that stated it and named the funcId to add (#113) --
  // true, and nothing the reader of this step can act on: what a location runs
  // is BlazeMeter's own UI's to change, and this step is what the *bundle*
  // carries. On a performance location, which is most of them, that was half
  // the section given over to a functionality nobody asked for. The options are still
  // cleared rather than merely hidden -- notRunPatch, in App -- because hiding a
  // row does not empty it, and generate() refuses an sv_ingress with no
  // subdomain whatever the location runs.
  //
  // Manual entry is the exception and structurally so: there the card *is* the
  // declaration (#118) -- its radio is what says which functionality the typed
  // identity was gathered for -- so filtering by the answer would take away the
  // control that gives it. Unanswered (`enabled == null`) keeps every card, the
  // same direction runsFunctionality reads it in.
  const functionalities = p.sourceMode === "manual"
    ? p.functionalities
    : p.functionalities.filter((f) => runsFunctionality(p.enabled, f.id));
  const secs = [
    ...functionalities.map((f) => ({
      id: "f-" + f.id, label: f.label,
      // A functionality this format cannot serve owns nothing here: the card states
      // that instead of its switches, and the rail agrees rather than listing
      // groups the card does not show. A format's refusal clears no options,
      // because the format is what gives way (see sv.patch). The not-run case
      // no longer reaches this in connect mode -- it is filtered above -- but
      // `runsFunctionality` stays in the test for manual entry, where an undeclared
      // functionality is still a card and must still own nothing.
      gs: runsFunctionality(p.enabled, f.id) && !p.functionalityBlocked[f.id]
        ? groupsFor(groupsOf(f.id), p.applies) : [],
      // ...and the rail says which of the two "no groups set" is: a functionality
      // running on defaults, or one this identity was not declared to run.
      off: p.enabled != null && !runsFunctionality(p.enabled, f.id),
      anchor: "cfg-f-" + f.id,
    })),
    ...(placed
      ? [{ id: "core", label: "Placement", gs: [] as OptionGroup[],
           off: false, anchor: "cfg-core" }]
      : []),
    { id: "shared", label: "Agent settings", off: false,
      gs: groupsFor(SHARED_GROUPS, p.applies), anchor: "cfg-shared" },
  ];
  /** A section's groups, as the rail worked them out. */
  const groupsIn = (id: string) => secs.find((s) => s.id === id)?.gs ?? [];
  return (
    <div className="space-y-4">
      {/* First on the page, and full width: it is the one choice here that
          decides which of the others are asked at all. */}
      <SegmentedControl
        label="Output format"
        value={p.format}
        onChange={p.setFormat}
        options={OUTPUT_FORMATS.map((f) => ({
          value: f.id, label: f.label, hint: f.hint,
          // The lead-in is this reader's; sv.ts hands over the clause. What
          // takes a segment away is what is configured, never the location --
          // a location that runs mockServices and was answered no generates
          // any of the three.
          disabledReason: p.blockedFormats[f.id]
            && `Not for this configuration — ${p.blockedFormats[f.id]}.`,
        }))} />

      {/* A format the user picked is never replaced in silence. It is the one
          correction on this page that overrides a choice made on it, and it
          survives until a format is picked -- which is the answer to it. */}
      {p.formatNotice && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
          Switched to <b>Kubernetes manifests</b>: the{" "}
          {OUTPUT_FORMATS.find((f) => f.id === p.formatNotice!.was)?.label
            ?? p.formatNotice.was}{" "}
          bundle you had chosen cannot serve service virtualization
          {p.formatNotice.why ? ` — ${p.formatNotice.why}` : ""}. Switch it off
          in <b>Service virtualization</b> below to pick that format again.
        </p>
      )}

      {/* Required fields nobody filled in. Amber and not red, and beside Next
          rather than in front of it: the bundle generates, and what it carries
          says so. Listed by option key -- the same names the bundle's README
          and the manifests use, so the sentence here and the one in the file
          are searchable as the same thing. */}
      {p.blanks.length > 0 && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
          {placeholderWarning(p.blanks)}
        </p>
      )}

      <div className="flex gap-2 items-center flex-wrap">
        <span className="flex-1" />
        <Button kind="ghost" onClick={p.exportProfile}>Export</Button>
        <label className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 cursor-pointer">
          Import
          <input type="file" accept=".json" className="hidden"
            onChange={(e) => e.target.files?.[0] && p.importProfile(e.target.files[0])} />
        </label>
      </div>

      <div className="grid grid-cols-[13rem_1fr] gap-6 items-start">
        <nav className="sticky top-4 space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 px-2">
            In this bundle
          </p>
          {secs.map((s) => {
            // The switches, not detect(): a group turned on and not yet filled
            // in has to appear here or the rail contradicts the form beside it.
            const set = s.gs.filter((g) => p.grpOn[g.id]);
            // ...and the one thing in this section that is not a group. It has
            // no switch to read, so what counts is what is set -- the same
            // question, asked of the option instead of a flag.
            const names = [
              ...set.map((g) => g.title),
              ...(s.id === "shared" && envCount
                ? [`${envCount} environment variable${envCount === 1 ? "" : "s"}`]
                : []),
            ];
            const todo = s.id === "core"
              ? !(p.namespaceOk && p.saOk)
              : s.gs.some((g) => p.incomplete.includes(g));
            const detail = todo ? "needs attention"
              : s.id === "core" ? String(p.options.namespace ?? "")
              : s.off ? "not enabled"
              : names.length ? names.join(", ")
              : "defaults";
            return (
              <a key={s.id} href={"#" + s.anchor}
                className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
                <span className={"mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full "
                  + (todo ? "bg-red-500"
                    : names.length || s.id === "core" ? "bg-emerald-500"
                    : "bg-slate-300")} />
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-slate-700">
                    {s.label}
                  </span>
                  <span className={"block text-[10px] "
                    + (todo ? "text-red-600" : "text-slate-400")}>
                    {detail}
                  </span>
                </span>
              </a>
            );
          })}
        </nav>

        <div className="min-w-0 space-y-5">
          {/* The heading goes with its cards. Nothing the account can say
              leaves this empty -- a location with no served functionality answers
              `enabled == null`, which keeps every card -- but a heading over
              nothing is what the filter above would produce if that ever
              stopped being true, and it would read as a section that failed to
              load. */}
          {(functionalities.length > 0 || p.locUnclaimed.length > 0) && (
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Deployment functionalities
            </h3>
            {/* Stacked, not side by side: a card holds group rows, and two
                columns of those read as two cramped tables. */}
            <div className="space-y-3">
              {/* The groups are the rail's own answer, handed over rather than
                  worked out again here: the card and the rail listing different
                  things is the rail failing at the only job it has. Empty is a
                  real answer -- the engine-size statement is
                  KUBERNETES_RESOURCES_LIMITS_*, so a docker performance card
                  says "nothing extra to configure" rather than stating a size
                  nothing reads. */}
              {functionalities.map((f) => (
                <FunctionalityCard key={f.id} {...p} feat={f}
                  own={groupsIn("f-" + f.id)} />
              ))}
            </div>
            {/* Names, not ids: the account's own display names where one has
                been read, and the raw funcId only where none has -- so this
                sentence reads as BlazeMeter's UI reads. Not mono for that
                reason; "Data Orchestration" set in a code face reads as
                something to type. */}
            {p.locUnclaimed.length > 0 && (
              <p className="text-[11px] text-slate-500 mt-1.5">
                Also runs{" "}
                <span className="text-slate-600">{p.locUnclaimed.join(", ")}</span> —
                no options here for those; nothing about them is generated or
                removed.
              </p>
            )}
          </section>
          )}

          {/* Where the agent goes in the cluster -- its own section, because a
              docker bundle has no such place and the whole card goes with the
              format. Titled as the rail titles it: two names for one section
              is the rail disagreeing with the form in the smallest way it
              can. */}
          {placed && (
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                Placement
              </h3>
              <div id="cfg-core"
                className="scroll-mt-4 rounded-xl border border-slate-200 p-3">
                <CoreFields {...p} />
              </div>
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Agent settings
            </h3>
            <div id="cfg-shared"
              className="scroll-mt-4 rounded-xl border border-slate-200 divide-y divide-slate-100">
              {rows(p, groupsIn("shared"))}
              {/* The environment variables: every documented agent variable
                  the groups above do not already write, offered as a list. Not
                  a group and not behind a switch -- see FoldRow -- and carried
                  by every format, so it asks for its key like the rest rather
                  than assuming so. */}
              {keysApply(ENV_KEYS, p.applies) && (
                <FoldRow title="Environment variables"
                  hint="agent variables with no setting of their own above"
                  summary={envCount ? `${envCount} set` : undefined}>
                  {p.envArea}
                </FoldRow>
              )}
              {/* Advanced is not a group either -- it is the SCC posture and
                  the UID a pod runs as -- so it asks the predicate for the
                  keys it writes rather than appearing in `shared`.
                  crane-hook used to sit beside it and is on the download step
                  now (#130): it shapes nothing about the agent, and what it
                  is about is the cluster the bundle is going to, which is the
                  question that step asks. */}
              {keysApply(ADVANCED_KEYS, p.applies) && <AdvancedRow {...p} />}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
