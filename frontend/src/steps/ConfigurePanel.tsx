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
// them belong to no feature -- registry, proxy, CA trust, scheduling, security
// -- and are here whatever the location runs; the rest belong to one, and live
// in that feature's own card. There used to be a feature *selector* switching
// between two views of the same six groups, so pressing it changed a single row
// while reading as though it changed the step. Nothing is hidden by a *view*
// now, so nothing has to be recovered: no "also in this bundle", no "not in
// view". What the format takes off screen is a different thing entirely -- not
// a view over a bundle's options, but options that bundle has no such thing as.
//
// The rail is orientation, not navigation-with-a-dot: it names what is set, so
// "what is in this bundle" is answered without scrolling the form.
import { ReactNode, useState } from "react";
import { Feature, Options } from "../api";
import {
  Button, Check, Field, inputCls, RequiredMark, SegmentedControl, Switch,
} from "../components";
import { Applies, keysApply, OUTPUT_FORMATS } from "../formats";
import { GroupRow } from "../groups/GroupRow";
import {
  GroupFlags, GroupId, groupsFor, groupsOf, OptionGroup, runsFeature,
  SHARED_GROUPS, SIZING_FEATURE,
} from "../optionGroups";

export interface ConfigurePanelProps {
  features: Feature[];
  /** `suggestNs` is passed only where picking is a declaration rather than a
   *  view -- manual entry's radio. See its call site. */
  pickFeature: (id: string, suggestNs?: boolean) => void;
  sourceMode: "connect" | "manual";
  /** The funcIds this location carries that no feature claims. */
  locUnclaimed: string[];
  /** Which features this location runs, or null while nobody has answered --
   *  see optionGroups.enabledFeatures. A feature not in it is stated by its
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
   *  a feature at all, by feature id. The card renders it instead of its
   *  switches, so a docker bundle stops offering an ingress, a subdomain and a
   *  TLS secret that would make the whole bundle unbuildable. */
  featureBlocked: Record<string, string>;
  /** The format the SV correction replaced, and why -- or null. A format the
   *  user picked is never swapped in silence; see the effect in App. */
  formatNotice: { was: string; why: string } | null;
  /** Does this option reach anything in a bundle of this format? Everything
   *  below hides by it -- whole groups, the placement card, the crane-hook row,
   *  Advanced, and the individual fields inside a group's own body. From
   *  formats.ts, over the generator's DOCKER_IGNORED. */
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
  /** Groups in use but unfinished, so the download is blocked. */
  incomplete: OptionGroup[];
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

// -- crane-hook --------------------------------------------------------------
// github.com/Blazemeter/crane-hook: BlazeMeter's own cluster-readiness checker,
// a one-shot Pod plus its own Role and RoleBinding. Every variable it takes --
// namespace, the RBAC names, the expose type and its TLS secret, the registry,
// the proxy -- is something this page already decides, which is why it is a
// switch here rather than a page in a doc. It is not a group: groups are
// declared in optionGroups.ts and own option keys that shape the agent, and
// this shapes nothing about the agent -- it adds a check that runs beside it.
function CraneHookRow(p: ConfigurePanelProps) {
  const on = !!p.options.crane_hook;
  return (
    <div className="px-3 py-2.5">
      <div className="flex items-center gap-3">
        <Switch on={on} onChange={(v) => p.set("crane_hook", v || null)} />
        <div className="min-w-0 grow">
          <p className={`text-sm font-medium ${on ? "text-slate-900" : "text-slate-500"}`}>
            Cluster check (crane-hook)
          </p>
          <p className="text-[11px] text-slate-400 truncate">
            a one-shot Pod that checks capacity, egress, RBAC and ingress before the agent runs
          </p>
        </div>
      </div>
      {on && (
        <p className="mt-2 pl-12 text-[11px] text-slate-500">
          Adds <span className="font-mono">bzm_cranehook.yaml</span> — a Pod, a
          Role and a RoleBinding, configured from the settings above. It runs
          once and exits 0 or 1; delete it when it has. Under Helm it is the
          chart&apos;s own <span className="font-mono">helm test</span> hook.
        </p>
      )}
    </div>
  );
}

/** Advanced, as a row in the settings list rather than a dashed box under it.
 *  It is two fields with the same weight as any other pair here; what makes it
 *  advanced is that it is closed, not that it sits outside the form. */
function AdvancedRow(p: ConfigurePanelProps) {
  const [open, setOpen] = useState(false);
  const openshift = p.options.platform === "openshift";
  return (
    <div className="px-3 py-2.5">
      <button className="w-full flex items-center gap-3 text-left"
        onClick={() => setOpen(!open)}>
        <span className="text-slate-400 text-xs w-3">{open ? "▾" : "▸"}</span>
        <span className="min-w-0 grow">
          <span className="block text-sm font-medium text-slate-500">Advanced</span>
          <span className="block text-[11px] text-slate-400">
            security posture and UID — you should not need these
          </span>
        </span>
      </button>
      {open && (
        <div className="mt-3 pl-6 grid grid-cols-2 gap-3">
          <Field label="Security posture"
            hint="SCC-friendly works on OpenShift and vanilla k8s; the pinned-UID variant is only for clusters that reject it">
            <select className={inputCls} value={String(p.options.platform)}
              onChange={(e) => p.set("platform", e.target.value)}>
              <option value="openshift">Unified SCC-friendly (recommended)</option>
              <option value="k8s">Legacy pinned-UID k8s</option>
            </select>
          </Field>
          {!openshift && (
            <Field label="runAsUser / runAsGroup">
              <input type="number" className={inputCls}
                value={Number(p.options.run_as_user ?? 1337)}
                onChange={(e) => p.set("run_as_user", Number(e.target.value))} />
            </Field>
          )}
        </div>
      )}
    </div>
  );
}

/** One feature: whether it is on, and -- only where it is -- the options it
 *  owns.
 *
 *  A feature the location does not run is *stated* and nothing more (#113). It
 *  used to be half-configurable: the card offered "Enable on this location…",
 *  which PATCHed the location's funcIds, and its group rows sat under a div
 *  carrying both `pointer-events-none` and the click handler that opened that
 *  offer -- so the rows were simply dead, and a restored session that had
 *  opened the SV group left a switch nobody could reach. In manual mode the
 *  guard was `!on && !manual && known`, which is no guard at all: the switch
 *  flipped, seeded an ingress with no domain behind it, and blocked the step.
 *
 *  Turning a funcId on changes what the location *is*, which is BlazeMeter's
 *  own UI's to do -- unlike this page's other two writes, which change an
 *  agent's credential and a location's concurrency. So the card says where. */
function FeatureCard(
    p: ConfigurePanelProps & { feat: Feature; own: OptionGroup[] }) {
  const { feat, own } = p;
  // The engine-size statement renders where the sizing group used to sit:
  // under the feature whose bundles start engines. Read-only by design --
  // the size is the location's, and this card only states it.
  const note = feat.id === SIZING_FEATURE ? p.engineNote : null;
  const manual = p.sourceMode === "manual";
  // Enabled means the location runs it -- or, in manual mode, that this is what
  // the typed identity was declared to be. Unanswered reads as on: see
  // runsFeature for why that direction is the safe one.
  const on = runsFeature(p.enabled, feat.id);
  // Before the account has been read there is nothing to say: `enabled` is null
  // then, and claiming "enabled" from an unanswered question is the collapse
  // this codebase keeps refusing to make.
  const known = p.enabled != null;
  // ...and the third thing that can be true of a card, which is about the
  // bundle rather than the location: this format cannot serve this feature at
  // all. Only asked where the location does run it -- "not enabled here" and
  // "not possible in this format" are different answers and the card may give
  // only the one that is true.
  const noFormat = on ? p.featureBlocked[feat.id] : undefined;
  return (
    <div id={"cfg-f-" + feat.id}
      className={"scroll-mt-4 rounded-xl border " + (on
        ? "border-bzm/40 bg-bzm/[0.03]" : "border-slate-200 bg-slate-50/70")}>
      <div className="px-3 py-2.5 border-b border-slate-100">
        {/* State first: whether the feature is on is what the card is about.
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
              onChange={() => p.pickFeature(feat.id, true)} />
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

      {/* Four answers, and they stay four. Not run: where to turn it on, and no
          control. Run but not by a bundle of this format: which format would,
          and no control either -- the switches would configure a bundle the
          generator refuses outright. Run with nothing of its own: said so,
          rather than left blank. Run: its rows. */}
      {known && !on ? (
        <p className="px-3 py-3 text-[11px] text-slate-500">
          {manual ? (
            <>Not what this identity was declared to run — pick <b>Enabled</b>{" "}
              above to configure it.</>
          ) : (
            <>Not enabled on this location. Add{" "}
              <span className="font-mono">{feat.func_ids[0]}</span> to it in
              BlazeMeter (Settings → Private Locations), then pick the location
              again — an agent is only asked to serve what its location says it
              runs.</>
          )}
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
const ADVANCED_KEYS = ["platform", "run_as_user"];

export function ConfigurePanel(p: ConfigurePanelProps) {
  // Placement is a section of the form only where the bundle has one, and its
  // groups are the format's rather than the feature's. Both are answered once
  // and shared with the rail below: derived twice, the rail and the form are
  // free to disagree about what is in this bundle, which is the one thing the
  // rail is for.
  const placed = keysApply(PLACEMENT_KEYS, p.applies);
  const secs = [
    ...p.features.map((f) => ({
      id: "f-" + f.id, label: f.label,
      // A feature the location does not run owns nothing here, and neither does
      // one this format cannot serve: the card states it either way and the
      // rail agrees, rather than listing groups the card does not show. The
      // options of a feature that is not run are cleared in App -- hiding a row
      // does not empty it, and the group is what does. A format's refusal
      // clears nothing, because the format is what gives way (see sv.patch).
      gs: runsFeature(p.enabled, f.id) && !p.featureBlocked[f.id]
        ? groupsFor(groupsOf(f.id), p.applies) : [],
      // ...and the rail says which of the two "no groups set" is: a feature
      // running on defaults, or one the location does not run at all.
      off: p.enabled != null && !runsFeature(p.enabled, f.id),
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
            const todo = s.id === "core"
              ? !(p.namespaceOk && p.saOk)
              : s.gs.some((g) => p.incomplete.includes(g));
            const detail = todo ? "needs attention"
              : s.id === "core" ? String(p.options.namespace ?? "")
              : s.off ? "not enabled"
              : set.length ? set.map((g) => g.title).join(", ")
              : "defaults";
            return (
              <a key={s.id} href={"#" + s.anchor}
                className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
                <span className={"mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full "
                  + (todo ? "bg-red-500"
                    : set.length || s.id === "core" ? "bg-emerald-500"
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
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Deployment features
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
              {p.features.map((f) => (
                <FeatureCard key={f.id} {...p} feat={f}
                  own={groupsIn("f-" + f.id)} />
              ))}
            </div>
            {p.locUnclaimed.length > 0 && (
              <p className="text-[11px] text-slate-500 mt-1.5">
                Also runs{" "}
                <span className="font-mono">{p.locUnclaimed.join(", ")}</span> —
                no options here for those; nothing about them is generated or
                removed.
              </p>
            )}
          </section>

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
              {/* Both are Kubernetes objects rather than settings: crane-hook
                  is a Pod, and Advanced is the SCC posture and the UID a pod
                  runs as. Neither is a group, so neither is in `shared` --
                  each asks the predicate for the keys it writes. */}
              {p.applies("crane_hook") && <CraneHookRow {...p} />}
              {keysApply(ADVANCED_KEYS, p.applies) && <AdvancedRow {...p} />}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
