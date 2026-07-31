// THROWAWAY -- three layouts for the Configure step, switchable with ?variant=.
//
// The question: the feature buttons currently switch a VIEW, and five of the
// six groups are in both views, so pressing them changes one row and reads as
// though it changed everything. Everything here splits the shared configuration
// from the per-feature configuration instead, and each variant disagrees about
// how the per-feature part is presented.
//
//   A  two decks -- shared deck, then a card per feature, all on screen at once
//   B  a status rail down the left, one long form on the right, nothing hidden
//   C  one list; a feature is a parent row whose children are its own groups
//
// No layout here writes an option that the shipped one does not: every switch
// goes through the same flipGroup, and the feature declaration in manual mode
// goes through the same pickFeature. Prototype rules apply -- no tests, no
// error handling, shared code kept to the two fields that are not the question.

import { ReactNode, useState } from "react";
import { Feature, Options } from "../api";
import { Button, Check, Field, inputCls, Switch } from "../components";
import { GroupRow } from "../groups/GroupRow";
import {
  ANY_DEPLOYMENT, GroupFlags, GroupId, OPTION_GROUPS, OptionGroup,
} from "../optionGroups";

export interface ProtoProps {
  features: Feature[];
  feature: string | null;
  pickFeature: (id: string) => void;
  sourceMode: "connect" | "manual";
  /** Features the location actually runs. Empty in manual mode. */
  locFeatures: string[];
  unavailable: string[];
  locUnclaimed: string[];
  funcIds: string[];
  options: Options;
  set: (k: string, v: unknown) => void;
  grpOn: GroupFlags;
  grpRequired: Partial<GroupFlags>;
  grpDeclined: Partial<GroupFlags>;
  flipGroup: (id: GroupId, on: boolean) => void;
  groupBody: Record<GroupId, ReactNode>;
  incomplete: OptionGroup[];
  namespaceOk: boolean;
  saOk: boolean;
  saCreate: boolean;
}

// -- the split this whole prototype is about --------------------------------
const SHARED = OPTION_GROUPS.filter((g) => !g.features.length);
const ownedBy = (id: string) =>
  OPTION_GROUPS.filter((g) => g.features.includes(id));

/** How a feature stands for this location. Manual mode has no account to read,
 *  so the declaration is the answer; connected, the funcIds are. `undeclared`
 *  is deliberately not `not-run`: nobody said no, they only said nothing, and
 *  the shipped page collapses those two the same way the generator is careful
 *  not to. */
type FeatState = "runs" | "declared" | "undeclared" | "not-run" | "unknown";
function featState(p: ProtoProps, f: Feature): FeatState {
  if (p.sourceMode === "manual")
    return p.feature === f.id ? "declared" : "undeclared";
  if (!p.locFeatures.length) return "unknown";
  return p.locFeatures.includes(f.id) ? "runs" : "not-run";
}

// Two words, because there are two answers. The state used to be spelled out
// per case ("this location runs it", "not enabled here") which is the same
// sentence the section header already implies, said again on every card.
const STATE_CHIP: Record<FeatState, { text: string; cls: string } | null> = {
  runs: { text: "Enabled", cls: "bg-emerald-100 text-emerald-700" },
  declared: { text: "Enabled", cls: "bg-bzm/15 text-bzm-dark" },
  "not-run": { text: "Not enabled", cls: "bg-slate-200 text-slate-500" },
  undeclared: { text: "Not enabled", cls: "bg-slate-200 text-slate-500" },
  unknown: null,
};

function rows(p: ProtoProps, gs: OptionGroup[], applies: string) {
  return gs.map((g) => (
    <GroupRow key={g.id} group={g} on={p.grpOn[g.id]}
      required={!!p.grpRequired[g.id]} declined={!!p.grpDeclined[g.id]}
      applies={applies} onFlip={(v) => p.flipGroup(g.id, v)}>
      {p.groupBody[g.id]}
    </GroupRow>
  ));
}

/** Namespace + service account. Not what the variants disagree about, so it is
 *  written once and merely placed differently by each. */
function CoreFields(p: ProtoProps) {
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
          Namespace
          {p.namespaceOk
            ? <span className="text-[10px] font-bold uppercase tracking-wide bg-emerald-100 text-emerald-700 rounded px-1.5 py-0.5">✓ set</span>
            : <span className="text-[10px] font-bold uppercase tracking-wide bg-red-100 text-red-700 rounded px-1.5 py-0.5">required</span>}
        </span>
        <input className={inputCls + (p.namespaceOk ? " border-emerald-400" : " border-red-300")}
          value={String(p.options.namespace ?? "")} placeholder="e.g. blazemeter"
          onChange={(e) => p.set("namespace", e.target.value)} />
      </label>
      <div className="grid grid-cols-[1fr_auto] gap-4 items-start">
        <label className="block">
          <span className="text-xs font-medium text-slate-600 flex items-center gap-2">
            Service account
            {p.saOk
              ? <span className="text-[10px] font-bold uppercase tracking-wide bg-emerald-100 text-emerald-700 rounded px-1.5 py-0.5">✓ set</span>
              : <span className="text-[10px] font-bold uppercase tracking-wide bg-red-100 text-red-700 rounded px-1.5 py-0.5">required</span>}
          </span>
          <input className={inputCls + (p.saOk ? "" : " border-red-300")}
            value={String(p.options.service_account_name ?? "")} placeholder="e.g. crane"
            onChange={(e) => p.set("service_account_name", e.target.value)} />
        </label>
        <div className="pt-5 w-52">
          <Check label="Create it"
            hint={p.saCreate ? "the bundle includes the ServiceAccount"
              : "already exists: referenced, not created"}
            checked={p.saCreate}
            onChange={(v) => p.set("service_account_create", v)} />
        </div>
      </div>
    </div>
  );
}


// -- crane-hook --------------------------------------------------------------
// github.com/Blazemeter/crane-hook: BlazeMeter's own cluster-readiness checker.
// It ships as a Pod plus its own Role and RoleBinding, runs once
// (restartPolicy: Never), and exits 0 or 1 having checked node capacity,
// egress to BlazeMeter and the registries, the RBAC the agent needs, and --
// for service virtualization -- the ingress/Istio setup and its TLS secret.
// helm-crane 1.4.0+ packages the same image as a `helm test` hook.
//
// Every env var it wants is a value this page already holds: WORKING_NAMESPACE,
// ROLE_NAME / ROLE_BINDING_NAME / SERVICE_ACCOUNT_NAME, KUBERNETES_WEB_EXPOSE_TYPE
// and _TLS_SECRET_NAME, DOCKER_REGISTRY, and the proxy settings. That is why it
// belongs beside the other deployment settings rather than in a doc: the
// bundle is the only place those are all decided at once.
//
// PROTOTYPE: the toggle writes `crane_hook` into the options and nothing
// generates from it yet -- generate.py would need the template, and helm parity
// and the options registry would need the same row. The note under the switch
// says so rather than letting the bundle look bigger than it is.
function CraneHookRow(p: ProtoProps) {
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
        <div className="mt-2 pl-12 space-y-1">
          <p className="text-[11px] text-slate-500">
            Adds a Pod, a Role and a RoleBinding
            (<span className="font-mono">cranehook.yaml</span>), configured from
            the namespace, service account and ingress settings above. Runs once
            and exits 0 or 1; delete it when it has. With Helm it is the chart&apos;s
            own <span className="font-mono">helm test</span> hook.
          </p>
          <p className="text-[11px] font-semibold text-amber-700">
            PROTOTYPE: nothing is emitted yet — the generator has no template for it.
          </p>
        </div>
      )}
    </div>
  );
}


/** Advanced, as a row in the settings list rather than a dashed box under it.
 *  It is two fields with the same weight as any other pair here; what makes it
 *  advanced is that it is closed, not that it sits outside the form. */
function AdvancedRow(p: ProtoProps) {
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

// == A -- shared deck, then a card per feature ================================
// Nothing appears or disappears when a feature is touched: the shared deck is
// the same deck whatever the location runs, and each feature owns a card beside
// it. What a feature costs you is inside its own card, next to its own options.
export function VariantA(p: ProtoProps) {
  // A feature the location does not run, switched on here anyway. Local: making
  // it true in the account is a PATCH of the location's funcIds, and this build
  // has no call for it -- so the card says the account still disagrees rather
  // than implying the write happened.
  const [enabledHere, setEnabledHere] = useState<Record<string, boolean>>({});
  // Which card is asking. One at a time, and clearing on any answer, so a
  // half-asked question never sits under a card you have moved on from.
  const [asking, setAsking] = useState<string | null>(null);
  const enable = (id: string) => {
    setEnabledHere((e) => ({ ...e, [id]: true }));
    setAsking(null);
    // "the toggle turns on and the feature pane expands" -- the pane is the
    // group's own body, so turning the group on is what expands it. Its enable
    // seeds whatever the group needs (SV lands on nginx rather than no backend).
    const first = ownedBy(id)[0];
    if (first && !p.grpOn[first.id]) p.flipGroup(first.id, true);
  };
  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
          Deployment features
        </h3>
        {/* Stacked, not side by side: the configure column is ~600px and two
            cards of group rows in it read as two cramped tables. */}
        <div className="space-y-3">
          {p.features.map((f) => {
            const st = featState(p, f);
            const on = !!enabledHere[f.id];
            const chip = on ? STATE_CHIP.runs : STATE_CHIP[st];
            const own = ownedBy(f.id);
            const dim = st === "not-run" && p.sourceMode === "connect" && !on;
            const stranded = dim ? own.filter((g) => g.detect(p.options)) : [];
            return (
              <div key={f.id} id={"proto-f-" + f.id}
                className={"scroll-mt-4 rounded-xl border " + (dim
                  ? "border-slate-200 bg-slate-50/70"
                  : "border-bzm/40 bg-bzm/[0.03]")}>
                <div className="px-3 py-2.5 border-b border-slate-100">
                  {/* State first: whether the feature is on is what the card is
                      about, and it used to be a footnote under the hint.
                      Manual mode has no account to read the answer off, so
                      there it is the control rather than a chip. */}
                  {p.sourceMode === "manual" ? (
                    <label className="flex items-center gap-2 text-[11px] font-medium text-slate-600 mb-1">
                      <input type="radio" checked={p.feature === f.id}
                        onChange={() => p.pickFeature(f.id)} />
                      Enabled
                    </label>
                  ) : chip && (
                    <span className={"inline-block mb-1 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 " + chip.cls}>
                      {chip.text}
                    </span>
                  )}
                  <p className={"text-sm font-medium " + (dim ? "text-slate-500" : "text-slate-900")}>
                    {f.label}
                  </p>
                  <p className="text-[11px] text-slate-400">{f.hint}</p>
                </div>
                {/* Off on this location, so its switches are inert until the
                    question under them is answered. The click still lands --
                    it is what asks -- but nothing is written by it. */}
                {dim && (
                  asking === f.id ? (
                    <div className="px-3 py-2.5 border-b border-slate-100 bg-white">
                      <p className="text-xs text-slate-700">
                        <b>{f.label}</b> is not enabled on this location. Enable
                        it and configure it here?
                      </p>
                      <p className="text-[11px] text-amber-700 mt-0.5">
                        PROTOTYPE: this build has no call that changes the
                        location&apos;s features — it enables the settings here only.
                      </p>
                      <div className="flex gap-2 mt-2">
                        <Button onClick={() => enable(f.id)}>Enable on location</Button>
                        <Button kind="ghost" onClick={() => setAsking(null)}>Cancel</Button>
                      </div>
                    </div>
                  ) : (
                    <button className="w-full text-left px-3 py-1.5 text-[11px] text-bzm hover:underline"
                      onClick={() => setAsking(f.id)}>
                      Enable on this location…
                    </button>
                  )
                )}
                <div className={dim ? "opacity-60 pointer-events-none select-none" : ""}
                  onClickCapture={dim ? (e) => {
                    e.preventDefault(); setAsking(f.id);
                  } : undefined}>
                  {own.length
                    ? <div className="divide-y divide-slate-100">{rows(p, own, "")}</div>
                    : <p className="px-3 py-3 text-[11px] text-slate-400">
                        nothing extra to configure — it uses the settings above
                      </p>}
                </div>
                {on && st === "not-run" && (
                  <p className="px-3 py-2 text-[11px] text-amber-700 border-t border-amber-100 bg-amber-50">
                    Enabled here, but the account still says this location does
                    not run it — enable it in BlazeMeter too, or the agent will
                    never be asked to.
                  </p>
                )}
                {stranded.length > 0 && (
                  <p className="px-3 py-2 text-[11px] text-amber-700 border-t border-amber-100 bg-amber-50">
                    set here and still generated, though this location does not
                    run {f.label.toLowerCase()}
                  </p>
                )}
              </div>
            );
          })}
        </div>
        {p.locUnclaimed.length > 0 && (
          <p className="text-[11px] text-slate-500 mt-1.5">
            Also runs <span className="font-mono">{p.locUnclaimed.join(", ")}</span> —
            no options here for those; nothing about them is generated or removed.
          </p>
        )}
      </section>

      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
          Deployment settings
        </h3>
        <div className="rounded-xl border border-slate-200 p-3 space-y-3">
          <div id="proto-core" className="scroll-mt-4"><CoreFields {...p} /></div>
          <div id="proto-shared"
            className="scroll-mt-4 border border-slate-200 rounded-lg divide-y divide-slate-100">
            {rows(p, SHARED, "")}
            <CraneHookRow {...p} />
            <AdvancedRow {...p} />
          </div>
        </div>
      </section>

    </div>
  );
}

// == D -- A's cards with B's rail, full width =================================
// The chosen pair: A's split (shared deck, then a card per feature, nothing
// hidden) with B's rail beside it, now that the preview no longer owns half the
// page. The rail is not navigation with a status dot bolted on -- it NAMES what
// is set, so "what is in this bundle" is answered without scrolling the form.
export function VariantD(p: ProtoProps) {
  // Same order as the pane: features first, then the settings under them.
  const secs = [
    ...p.features.map((f) => ({
      id: "f-" + f.id, label: f.label, gs: ownedBy(f.id),
    })),
    { id: "core", label: "Placement", gs: [] as OptionGroup[] },
    { id: "shared", label: "Configure agent", gs: SHARED },
  ];
  return (
    <div className="grid grid-cols-[13rem_1fr] gap-6 items-start">
      <nav className="sticky top-4 space-y-1">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 px-2">
          In this bundle
        </p>
        {secs.map((s) => {
          // The switches, not detect(): the rail is orientation, and a group
          // the user just turned on and has not filled in yet has to appear
          // here or the rail contradicts the form beside it.
          const set = s.gs.filter((g) => p.grpOn[g.id]);
          const todo = s.id === "core"
            ? !(p.namespaceOk && p.saOk)
            : s.gs.some((g) => p.incomplete.includes(g));
          // What each line says, in order of what the reader needs: unfinished
          // first, then what is actually set, then nothing-yet.
          const detail = todo ? "needs attention"
            : s.id === "core" ? String(p.options.namespace ?? "")
            : set.length ? set.map((g) => g.title).join(", ")
            : "defaults";
          return (
            <a key={s.id} href={"#proto-" + s.id}
              className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
              <span className={"mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full "
                + (todo ? "bg-red-500" : set.length || s.id === "core"
                  ? "bg-emerald-500" : "bg-slate-300")} />
              <span className="min-w-0">
                <span className="block text-xs font-medium text-slate-700">{s.label}</span>
                <span className={"block text-[10px] " + (todo ? "text-red-600" : "text-slate-400")}>
                  {detail}
                </span>
              </span>
            </a>
          );
        })}
      </nav>
      <div className="min-w-0">
        <VariantA {...p} />
      </div>
    </div>
  );
}

// == B -- status rail + one long form =========================================
// Every option is on one page, and the rail is the only navigation. The rail
// carries state, so "the download is blocked and the reason is somewhere else"
// stops being possible: the section holding the unfinished group is marked, and
// clicking it goes there.
export function VariantB(p: ProtoProps) {
  const secs = [
    { id: "core", label: "Placement", gs: [] as OptionGroup[], note: "namespace, service account" },
    { id: "shared", label: "Cluster environment", gs: SHARED, note: ANY_DEPLOYMENT },
    ...p.features.map((f) => ({
      id: "f-" + f.id, label: f.label, gs: ownedBy(f.id),
      note: STATE_CHIP[featState(p, f)]?.text ?? (f.hint ?? ""),
    })),
  ];
  const status = (s: typeof secs[number]) => {
    if (s.id === "core") return p.namespaceOk && p.saOk ? "ok" : "todo";
    if (s.gs.some((g) => p.incomplete.includes(g))) return "todo";
    if (s.gs.some((g) => p.grpOn[g.id])) return "ok";
    return "idle";
  };
  const DOT = { ok: "bg-emerald-500", todo: "bg-red-500", idle: "bg-slate-300" };
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-4 items-start">
      <nav className="sticky top-4 space-y-0.5">
        {secs.map((s) => {
          const st = status(s) as keyof typeof DOT;
          return (
            <a key={s.id} href={"#proto-" + s.id}
              className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-slate-50">
              <span className={"mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full " + DOT[st]} />
              <span className="min-w-0">
                <span className="block text-xs font-medium text-slate-700">{s.label}</span>
                <span className="block text-[10px] text-slate-400 truncate">
                  {st === "todo" ? "needs attention" : s.note}
                </span>
              </span>
            </a>
          );
        })}
      </nav>
      <div className="space-y-5 min-w-0">
        {secs.map((s) => (
          <section key={s.id} id={"proto-" + s.id} className="scroll-mt-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              {s.label}
              {s.id.startsWith("f-") && p.sourceMode === "manual" && (
                <label className="ml-3 inline-flex items-center gap-1.5 text-[11px] font-normal normal-case text-slate-600">
                  <input type="radio"
                    checked={"f-" + p.feature === s.id}
                    onChange={() => p.pickFeature(s.id.slice(2))} />
                  this location runs it
                </label>
              )}
            </h3>
            {s.id === "core"
              ? <div className="rounded-xl border border-slate-200 p-3"><CoreFields {...p} /></div>
              : s.gs.length
                ? <div className="rounded-xl border border-slate-200 divide-y divide-slate-100">
                    {rows(p, s.gs, "")}
                  </div>
                : <p className="text-[11px] text-slate-400 px-1">
                    nothing of its own — it runs on the settings above
                  </p>}
          </section>
        ))}
      </div>
    </div>
  );
}

// == C -- one list, features as parent rows ===================================
// There is no feature selector at all. A feature is a row like any other, and
// its options are its children; the shared groups sit above it at the top
// level. Adding a feature adds a parent row, which is the same shape as adding
// a group -- the point being that the page never has two kinds of thing on it.
export function VariantC(p: ProtoProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-slate-200 p-3">
        <CoreFields {...p} />
      </div>
      <div className="rounded-xl border border-slate-200 divide-y divide-slate-100">
        <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400 bg-slate-50/70">
          Applies however this agent is used
        </p>
        {rows(p, SHARED, "")}
        <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-400 bg-slate-50/70">
          What it serves
        </p>
        {p.features.map((f) => {
          const st = featState(p, f);
          const own = ownedBy(f.id);
          const chip = STATE_CHIP[st];
          const isOpen = open[f.id] ?? st !== "not-run";
          const touched = own.filter((g) => p.grpOn[g.id]).length;
          const needs = own.some((g) => p.incomplete.includes(g));
          return (
            <div key={f.id}>
              <button className="w-full text-left px-3 py-2.5 hover:bg-slate-50/70 flex items-center gap-3"
                onClick={() => setOpen((o) => ({ ...o, [f.id]: !isOpen }))}>
                <span className="text-slate-400 text-xs w-3">{isOpen ? "▾" : "▸"}</span>
                <span className="min-w-0 grow">
                  <span className="block text-sm font-medium text-slate-900">
                    {f.label}
                    {chip && (
                      <span className={"ml-2 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 " + chip.cls}>
                        {chip.text}
                      </span>
                    )}
                    {needs && (
                      <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-red-600">
                        unfinished
                      </span>
                    )}
                  </span>
                  <span className="block text-[11px] text-slate-400">
                    {own.length
                      ? `${touched} of ${own.length} settings on · ${f.hint ?? ""}`
                      : "no settings of its own"}
                  </span>
                </span>
                {p.sourceMode === "manual" && (
                  <span className="shrink-0"
                    onClick={(e) => { e.stopPropagation(); p.pickFeature(f.id); }}>
                    <input type="radio" readOnly checked={p.feature === f.id} />
                  </span>
                )}
              </button>
              {isOpen && own.length > 0 && (
                <div className="pl-6 border-l-2 border-slate-100 ml-4 mb-1 divide-y divide-slate-100">
                  {rows(p, own, "")}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {p.locUnclaimed.length > 0 && (
        <p className="text-[11px] text-slate-500">
          Also runs <span className="font-mono">{p.locUnclaimed.join(", ")}</span> —
          no options here for those.
        </p>
      )}
    </div>
  );
}
