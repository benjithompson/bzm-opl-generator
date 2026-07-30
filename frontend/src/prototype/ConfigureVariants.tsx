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
import { Check, inputCls } from "../components";
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

const STATE_CHIP: Record<FeatState, { text: string; cls: string } | null> = {
  runs: { text: "this location runs it", cls: "bg-emerald-100 text-emerald-700" },
  declared: { text: "declared", cls: "bg-bzm/15 text-bzm-dark" },
  "not-run": { text: "not enabled here", cls: "bg-slate-200 text-slate-500" },
  undeclared: null,
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

// == A -- shared deck, then a card per feature ================================
// Nothing appears or disappears when a feature is touched: the shared deck is
// the same deck whatever the location runs, and each feature owns a card beside
// it. What a feature costs you is inside its own card, next to its own options.
export function VariantA(p: ProtoProps) {
  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
          Every deployment
        </h3>
        <div className="rounded-xl border border-slate-200 p-3 space-y-3">
          <CoreFields {...p} />
          <div className="border border-slate-200 rounded-lg divide-y divide-slate-100">
            {rows(p, SHARED, "")}
          </div>
        </div>
      </section>

      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
          What this agent serves
        </h3>
        {/* Stacked, not side by side: the configure column is ~600px and two
            cards of group rows in it read as two cramped tables. */}
        <div className="space-y-3">
          {p.features.map((f) => {
            const st = featState(p, f);
            const chip = STATE_CHIP[st];
            const own = ownedBy(f.id);
            const dim = st === "not-run" && p.sourceMode === "connect";
            const stranded = dim ? own.filter((g) => g.detect(p.options)) : [];
            return (
              <div key={f.id}
                className={"rounded-xl border " + (dim
                  ? "border-slate-200 bg-slate-50/70"
                  : "border-bzm/40 bg-bzm/[0.03]")}>
                <div className="px-3 py-2.5 border-b border-slate-100">
                  <p className={"text-sm font-medium " + (dim ? "text-slate-500" : "text-slate-900")}>
                    {f.label}
                    {chip && (
                      <span className={"ml-2 text-[10px] font-semibold uppercase tracking-wide rounded px-1.5 py-0.5 " + chip.cls}>
                        {chip.text}
                      </span>
                    )}
                  </p>
                  <p className="text-[11px] text-slate-400">{f.hint}</p>
                  {/* Manual mode has no account to read the answer off, so the
                      card asks for it in place -- rather than a row of buttons
                      above that also happen to move the view. */}
                  {p.sourceMode === "manual" && (
                    <label className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-600">
                      <input type="radio" checked={p.feature === f.id}
                        onChange={() => p.pickFeature(f.id)} />
                      this location runs {f.label.toLowerCase()}
                    </label>
                  )}
                </div>
                <div className={dim ? "opacity-60" : ""}>
                  {own.length
                    ? <div className="divide-y divide-slate-100">{rows(p, own, "")}</div>
                    : <p className="px-3 py-3 text-[11px] text-slate-400">
                        nothing extra to configure — it uses the settings above
                      </p>}
                </div>
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
