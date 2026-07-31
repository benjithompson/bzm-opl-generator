// PROTOTYPE — throwaway. Not production code, no tests, no error handling.
//
// Question: what should the account-level capacity view look like? It is
// high-level information -- how much load this account can generate, where it
// lives, and which of it is double-claimed -- and it earns its own view rather
// than more weight on step 1.
//
// Three variants on ?variant=A|B|C, against the real account. The data is the
// same in all three (GET /api/capacity); what differs is what the page is *for*:
//
//   A — Ledger. Every location, grouped by workspace, with subtotals. The view
//       you take into a meeting about who is using what.
//   B — Composition. One bar per workspace, segmented by location, width by
//       rated capacity. The view that answers "where is it all?" at a glance.
//   C — Answer first. The account number, one sentence about how it is made up,
//       and everything else folded away until asked for.
//
// The shared-location problem is the thing to judge them on. Five locations in
// this account belong to more than one workspace, so their capacity is
// claimable from either and the workspace totals cannot simply be added -- the
// account is not the sum of its parts. Each variant says that differently.
import { useEffect, useState } from "react";

import { Button } from "../components";

export const VARIANTS = [
  { id: "A", name: "Ledger — every location, by workspace" },
  { id: "B", name: "Composition — where the capacity is" },
  { id: "C", name: "Answer first, details on demand" },
] as const;

export type VariantId = typeof VARIANTS[number]["id"];

export interface CapLocation {
  id: string;
  name: string;
  func_ids: string[];
  agents: number;
  agents_reporting: number;
  slots: number | null;
  threads_per_engine: number | null;
  engines: number;
  rated_vus: number | null;
  workspace_ids: number[];
  workspace_names: string[];
  shared: boolean;
}

export interface Capacity {
  account_id: number;
  workspaces: { id: number; name: string }[];
  locations: CapLocation[];
  rated_vus: number;
  unrated: number;
}

export function useVariant(): VariantId | null {
  const read = () => {
    const v = new URLSearchParams(window.location.search).get("variant");
    return VARIANTS.some((x) => x.id === v) ? (v as VariantId) : null;
  };
  const [variant, setVariant] = useState<VariantId | null>(read);
  useEffect(() => {
    const onPop = () => setVariant(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return variant;
}

const n = (x: number) => x.toLocaleString();

/** Locations of a workspace, and what they add up to. `shared` is kept apart
 *  because adding it into two workspaces counts engines that cannot run
 *  twice -- the one arithmetic mistake this whole view exists to avoid. */
function byWorkspace(cap: Capacity) {
  return cap.workspaces.map((w) => {
    const locs = cap.locations.filter((l) => l.workspace_ids.includes(w.id));
    const own = locs.filter((l) => !l.shared);
    const shared = locs.filter((l) => l.shared);
    const sum = (xs: CapLocation[]) => xs.reduce((t, l) => t + (l.rated_vus ?? 0), 0);
    return { ...w, locs, own, shared, ownVus: sum(own), sharedVus: sum(shared),
             total: sum(locs) };
  }).filter((w) => w.locs.length > 0)
    .sort((a, b) => b.total - a.total);
}

// -- A -- the ledger ----------------------------------------------------------

export function VariantA({ cap }: { cap: Capacity }) {
  const spaces = byWorkspace(cap);
  return (
    <div className="space-y-4">
      <Header cap={cap} />
      {spaces.map((w) => (
        <div key={w.id} className="bg-white border border-slate-200 rounded-lg overflow-hidden">
          <div className="flex items-baseline gap-2 px-3 py-2 bg-slate-50 border-b border-slate-200">
            <span className="text-sm font-semibold text-slate-800">{w.name}</span>
            <span className="text-xs text-slate-500">
              {w.locs.length} location{w.locs.length === 1 ? "" : "s"}
            </span>
            <span className="grow" />
            <span className="text-sm font-bold text-slate-900">{n(w.total)}</span>
            <span className="text-[11px] text-slate-500">rated VUs</span>
          </div>
          <table className="w-full text-xs">
            <thead className="text-slate-500">
              <tr className="border-b border-slate-100">
                <th className="text-left font-medium px-3 py-1.5">location</th>
                <th className="text-right font-medium px-2">agents</th>
                <th className="text-right font-medium px-2">engines/agent</th>
                <th className="text-right font-medium px-2">engines</th>
                <th className="text-right font-medium px-2">VUs/engine</th>
                <th className="text-right font-medium px-3">rated VUs</th>
              </tr>
            </thead>
            <tbody>
              {w.locs.slice().sort((a, b) => (b.rated_vus ?? 0) - (a.rated_vus ?? 0))
                .map((l, i) => (
                <tr key={l.id} className={i % 2 ? "bg-slate-50/60" : ""}>
                  <td className="px-3 py-1.5">
                    <span className="font-medium text-slate-800">{l.name}</span>
                    {l.shared && (
                      <span className="ml-1.5 text-[10px] font-bold uppercase tracking-wide
                                       bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
                        shared
                      </span>
                    )}
                    {l.agents_reporting < l.agents && (
                      <span className="ml-1.5 text-[10px] text-slate-400">
                        {l.agents - l.agents_reporting} not reporting
                      </span>
                    )}
                  </td>
                  <td className="text-right px-2 tabular-nums">{l.agents}</td>
                  <td className="text-right px-2 tabular-nums text-slate-500">{l.slots ?? "—"}</td>
                  <td className="text-right px-2 tabular-nums">{l.engines}</td>
                  <td className="text-right px-2 tabular-nums text-slate-500">
                    {l.threads_per_engine ?? "—"}
                  </td>
                  <td className="text-right px-3 tabular-nums font-medium">
                    {l.rated_vus === null ? "—" : n(l.rated_vus)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {w.shared.length > 0 && (
            <p className="px-3 py-1.5 text-[11px] text-amber-800 bg-amber-50 border-t border-amber-200">
              {n(w.sharedVus)} of that is {w.shared.length} shared location
              {w.shared.length === 1 ? "" : "s"} — also claimable from{" "}
              {[...new Set(w.shared.flatMap((l) => l.workspace_names)
                .filter((x) => x !== w.name))].join(", ")}. Running there leaves
              none of it here.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

// -- B -- the composition -----------------------------------------------------

const BAND = ["bg-bzm", "bg-sky-400", "bg-emerald-400", "bg-violet-400",
              "bg-amber-400", "bg-rose-400", "bg-teal-400", "bg-indigo-400"];

export function VariantB({ cap }: { cap: Capacity }) {
  const spaces = byWorkspace(cap);
  const widest = Math.max(...spaces.map((w) => w.total), 1);
  return (
    <div className="space-y-4">
      <Header cap={cap} />
      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-4">
        {spaces.map((w) => (
          <div key={w.id}>
            <div className="flex items-baseline gap-2 mb-1">
              <span className="text-sm font-medium text-slate-800">{w.name}</span>
              <span className="grow" />
              <span className="text-sm font-bold tabular-nums">{n(w.total)}</span>
              <span className="text-[11px] text-slate-400">
                {Math.round((w.total / cap.rated_vus) * 100)}% of the account
              </span>
            </div>
            {/* One bar, segmented by location. Width is capacity, so the eye
                lands on the location that holds it rather than on the one with
                the longest name. */}
            <div className="flex h-6 rounded overflow-hidden bg-slate-100"
                 style={{ width: `${Math.max((w.total / widest) * 100, 2)}%` }}>
              {w.locs.slice().sort((a, b) => (b.rated_vus ?? 0) - (a.rated_vus ?? 0))
                .map((l, i) => (
                <div key={l.id}
                  title={`${l.name} — ${n(l.rated_vus ?? 0)} rated VUs`
                    + (l.shared ? ` (shared with ${l.workspace_names.join(", ")})` : "")}
                  className={(l.shared ? "bg-amber-300 border-r border-amber-500"
                                       : BAND[i % BAND.length])
                    + " h-full"}
                  style={{ width: `${((l.rated_vus ?? 0) / w.total) * 100}%` }} />
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1">
              {w.locs.slice().sort((a, b) => (b.rated_vus ?? 0) - (a.rated_vus ?? 0))
                .slice(0, 6).map((l, i) => (
                <span key={l.id} className="flex items-center gap-1 text-[10px] text-slate-500">
                  <span className={"inline-block w-2 h-2 rounded-sm "
                    + (l.shared ? "bg-amber-300" : BAND[i % BAND.length])} />
                  {l.name} {n(l.rated_vus ?? 0)}
                </span>
              ))}
              {w.locs.length > 6 && (
                <span className="text-[10px] text-slate-400">
                  +{w.locs.length - 6} smaller
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
        Amber segments are <b>shared</b> locations — in more than one workspace,
        so the same engines appear in more than one bar. The account total counts
        them once, which is why the bars add up to more than it.
      </p>
    </div>
  );
}

// -- C -- the answer, then the detail -----------------------------------------

export function VariantC({ cap }: { cap: Capacity }) {
  const spaces = byWorkspace(cap);
  const [open, setOpen] = useState<number | null>(null);
  const shared = cap.locations.filter((l) => l.shared);
  const biggest = [...cap.locations].sort(
    (a, b) => (b.rated_vus ?? 0) - (a.rated_vus ?? 0))[0];
  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg p-5">
        <div className="text-4xl font-bold text-slate-900 leading-none tabular-nums">
          {n(cap.rated_vus)}
        </div>
        <div className="text-sm text-slate-600 mt-1">
          virtual users this account is rated for
        </div>
        <p className="text-xs text-slate-500 mt-3 max-w-2xl">
          Across <b>{cap.locations.length} private locations</b> in{" "}
          <b>{spaces.length} workspaces</b>, the largest being{" "}
          <b>{biggest?.name}</b> at {n(biggest?.rated_vus ?? 0)}.{" "}
          {shared.length > 0 && (
            <>
              <b>{shared.length}</b> of those locations belong to more than one
              workspace, so their capacity is claimable from either — counted
              once here, which is why the workspace figures below add up to more
              than this number.
            </>
          )}
        </p>
        <p className="text-[11px] text-slate-400 mt-2 max-w-2xl">
          Rated, not enforced: <b>agents × engines per agent</b> is the engine
          count and BlazeMeter holds you to it, but the virtual users per engine
          is what those engines are sized for — a run asking for more is packed
          onto them rather than refused.
        </p>
      </div>

      <div className="bg-white border border-slate-200 rounded-lg divide-y divide-slate-100">
        {spaces.map((w) => (
          <div key={w.id}>
            <button onClick={() => setOpen(open === w.id ? null : w.id)}
              className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-50">
              <span className={"text-slate-400 text-xs transition-transform duration-150 "
                + (open === w.id ? "rotate-90" : "")}>›</span>
              <span className="text-sm font-medium text-slate-800">{w.name}</span>
              {w.shared.length > 0 && (
                <span className="text-[10px] font-bold uppercase tracking-wide
                                 bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
                  {w.shared.length} shared
                </span>
              )}
              <span className="grow" />
              <span className="text-xs text-slate-400">{w.locs.length} locations</span>
              <span className="text-sm font-bold tabular-nums w-20 text-right">
                {n(w.total)}
              </span>
            </button>
            {open === w.id && (
              <div className="px-3 pb-3 pl-9 space-y-1">
                {w.locs.slice().sort((a, b) => (b.rated_vus ?? 0) - (a.rated_vus ?? 0))
                  .map((l) => (
                  <div key={l.id} className="flex items-baseline gap-2 text-xs">
                    <span className="text-slate-700">{l.name}</span>
                    {l.shared && (
                      <span className="text-[10px] text-amber-700">
                        also in {l.workspace_names.filter((x) => x !== w.name).join(", ")}
                      </span>
                    )}
                    <span className="grow border-b border-dotted border-slate-200" />
                    <span className="text-slate-400">
                      {l.agents}×{l.slots ?? "?"}×{l.threads_per_engine ?? "?"}
                    </span>
                    <span className="tabular-nums font-medium w-16 text-right">
                      {l.rated_vus === null ? "—" : n(l.rated_vus)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Header({ cap }: { cap: Capacity }) {
  const shared = cap.locations.filter((l) => l.shared).length;
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4 flex items-baseline gap-4 flex-wrap">
      <div>
        <div className="text-2xl font-bold text-slate-900 tabular-nums leading-none">
          {n(cap.rated_vus)}
        </div>
        <div className="text-[11px] text-slate-500 mt-0.5">account rated VUs</div>
      </div>
      <div className="text-xs text-slate-500">
        {cap.locations.length} locations · {cap.workspaces.length} workspaces
        {shared > 0 && <> · <b className="text-amber-700">{shared} shared</b></>}
      </div>
    </div>
  );
}

export function Variant(props: { id: VariantId; cap: Capacity }) {
  if (props.id === "A") return <VariantA cap={props.cap} />;
  if (props.id === "B") return <VariantB cap={props.cap} />;
  return <VariantC cap={props.cap} />;
}

export function PrototypeSwitcher({ current }: { current: VariantId }) {
  const i = VARIANTS.findIndex((v) => v.id === current);
  const go = (d: number) => {
    const next = VARIANTS[(i + d + VARIANTS.length) % VARIANTS.length].id;
    const url = new URL(window.location.href);
    url.searchParams.set("variant", next);
    window.history.replaceState({}, "", url);
    window.dispatchEvent(new PopStateEvent("popstate"));
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (el instanceof HTMLElement
          && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "ArrowRight") go(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [i]);
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center
                    gap-1 rounded-full bg-slate-900 text-white shadow-lg px-2 py-1.5">
      <button className="px-2 py-0.5 rounded-full hover:bg-white/15" onClick={() => go(-1)}>←</button>
      <span className="text-xs font-medium px-2">
        PROTOTYPE · {current} — {VARIANTS[i].name}
      </span>
      <button className="px-2 py-0.5 rounded-full hover:bg-white/15" onClick={() => go(1)}>→</button>
      <span className="w-px h-4 bg-white/25 mx-1" />
      <Button kind="ghost" onClick={() => {
        const url = new URL(window.location.href);
        url.searchParams.delete("variant");
        window.location.href = url.toString();
      }}>
        <span className="text-white text-xs">exit</span>
      </Button>
    </div>
  );
}
