// What this account can run at once, in rated virtual users, and where that
// capacity lives: by workspace, then by location. A row shares its segment's
// colour, so there is no legend. A folded workspace keeps its bar and hides its
// table. "Rated" is agents x engines per agent x virtual users per engine: what
// the engines are sized for, not a limit.
import { useMemo, useState } from "react";

import { Capacity, CapLocation } from "./api";
import {
  accountBands, byWorkspace, matching, WorkspaceRollup,
} from "./capacity";
import { Button, cardCls, Chevron, Collapse, inputCls } from "./components";
import { useFoldSet } from "./foldSet";
import { plural } from "./text";

const n = (x: number) => x.toLocaleString();

const BAND = ["bg-bzm", "bg-sky-400", "bg-emerald-400", "bg-violet-400",
              "bg-amber-400", "bg-rose-400", "bg-teal-400", "bg-indigo-400"];

// Shared locations are striped over their colour rather than given one.
const STRIPE = "repeating-linear-gradient(45deg, rgba(255,255,255,.55) 0 3px,"
  + " rgba(255,255,255,0) 3px 7px)";

/** The colour chip tying a row to its segment: `i` picks from BAND, or
 *  `className` gives the colour directly. */
function Swatch(props: { i?: number; shared?: boolean; className?: string }) {
  const colour = props.className
    ?? BAND[(props.i ?? 0) % BAND.length]
      + (props.shared ? " ring-1 ring-amber-600" : "");
  return (
    <span className={"inline-block w-2.5 h-2.5 rounded-sm shrink-0 " + colour}
      style={props.shared ? { backgroundImage: STRIPE } : undefined} />
  );
}

export function CapacityView({ cap, refresh, refreshing }: {
  cap: Capacity;
  /** Re-read the whole account past the server's cache. */
  refresh: () => void;
  refreshing: boolean;
}) {
  const [filter, setFilter] = useState("");
  // Grouped once per account, then filtered.
  const all = useMemo(() => byWorkspace(cap), [cap]);
  // The account bar: what only each workspace can claim, plus one shared
  // segment; they add up to the headline.
  const bands = useMemo(() => accountBands(cap), [cap]);
  // Colour by workspace name, so its card below wears the same swatch.
  const bandColour = useMemo(() => {
    const m = new Map<string, string>();
    bands.forEach((b, i) => {
      if (!b.shared && !b.orphan) m.set(b.name, BAND[i % BAND.length]);
    });
    return m;
  }, [bands]);
  const q = filter.trim().toLowerCase();
  // Against the largest workspace on the account, not the largest match, so a
  // filter does not rescale the bars.
  const widest = useMemo(
    () => Math.max(...all.map((w) => w.total), 1), [all]);
  const spaces = useMemo(() => matching(all, filter), [all, filter]);
  const holding = new Set(cap.locations.flatMap((l) => l.workspace_ids)).size;
  const sharedCount = cap.locations.filter((l) => l.shared).length;
  // Folded workspaces: folded, the page is an index of the account.
  const fold = useFoldSet();
  // Judged on what is on screen.
  const allFolded = fold.allFolded(spaces.map((w) => w.id));

  return (
    <div className="space-y-4">
      <div className={cardCls}>
        <div className="flex items-center gap-4 flex-wrap">
          <div>
            <div className="text-2xl font-bold text-slate-900 tabular-nums leading-none">
              {n(cap.rated_vus)}
            </div>
            <div className="text-2xs text-slate-500 mt-0.5">account rated VUs</div>
          </div>
          <div className="text-xs text-slate-500">
            {/* Only workspaces that hold a location are counted. */}
            {cap.locations.length} locations · {holding} workspaces
            {sharedCount > 0 && <> · <b className="text-amber-700">{sharedCount} shared</b></>}
            {/* A location with no rating (null, not 0) adds nothing to the
                bars, so the count of those is said here. */}
            {cap.unrated > 0 && (
              <> · <span title="no engines per agent or no virtual users per engine set, so there is no rating to state">
                {cap.unrated} unrated
              </span></>
            )}
          </div>
          <span className="grow" />
          {/* On failure what is on screen stays. */}
          <Button kind="ghost" onClick={refresh} busy={refreshing}>Refresh</Button>
          {/* Folds every workspace on the account, not only those on screen. */}
          <Button kind="ghost"
            onClick={() => (allFolded ? fold.unfoldAll()
                                      : fold.foldAll(all.map((w) => w.id)))}>
            {allFolded ? "Expand all" : "Collapse all"}
          </Button>
          <div className="w-56 max-w-full">
            <input className={inputCls} value={filter} type="search"
              placeholder={`Filter ${holding} workspaces…`}
              aria-label="Filter workspaces"
              onChange={(e) => setFilter(e.target.value)} />
            {/* The account total does not move with the filter. */}
            {filter.trim() && (
              <p className="text-2xs text-slate-400 mt-1">
                {spaces.length} of {holding} shown ·{" "}
                {n(spaces.reduce((t, w) => t + w.total, 0))} rated VUs in view
              </p>
            )}
          </div>
        </div>

        {/* The headline as a full-width bar; workspace bars below are drawn
            against it. */}
        {bands.length > 0 && (
          <div>
            <div className="flex h-6 rounded overflow-hidden bg-slate-100">
              {bands.map((b) => (
                <div key={b.key}
                  title={`${b.name} — ${n(b.vus)} rated VUs`
                    + ` (${Math.round((b.vus / (cap.rated_vus || 1)) * 100)}%)`}
                  className={(b.shared || b.orphan ? "bg-slate-300"
                    : bandColour.get(b.name)) + " h-full transition-opacity "
                    // A filter dims the other segments rather than removing them.
                    + (q && !b.shared && !b.orphan
                      && !b.name.toLowerCase().includes(q) ? "opacity-25" : "")}
                  style={{
                    width: `${(b.vus / (cap.rated_vus || 1)) * 100}%`,
                    backgroundImage: b.shared ? STRIPE : undefined,
                  }} />
              ))}
            </div>
            <p className="text-2xs text-slate-400 mt-1">
              One segment per workspace, sized by what only it can claim.
              {bands.some((b) => b.shared) && (
                <> The striped segment is capacity two or more workspaces can
                  claim, counted once here and shown in each of them below.</>
              )}
              {bands.some((b) => b.orphan) && (
                <> The grey segment is in no workspace this listing names.</>
              )}
            </p>
          </div>
        )}
      </div>

      {spaces.length === 0 && (
        <p className="text-sm text-slate-500">no workspace matches “{filter}”.</p>
      )}

      {spaces.map((w) => (
        <WorkspaceCard key={w.id} w={w}
          accountVus={cap.rated_vus} widest={widest}
          colour={bandColour.get(w.name)}
          open={!fold.folded(w.id)} onToggle={() => fold.toggle(w.id)} />
      ))}
    </div>
  );
}

/** One workspace, folded or not. The fold state lives in the view, so
 *  "collapse all" can reach it. */
function WorkspaceCard(props: {
  w: WorkspaceRollup;
  /** The account total, for the percentage. */
  accountVus: number;
  /** The largest workspace on the account, which every bar is drawn against. */
  widest: number;
  /** Its colour in the account bar, or undefined where it has no segment. */
  colour?: string;
  open: boolean;
  onToggle: () => void;
}) {
  const { w, open } = props;
  const locs = w.locs;
  // Named so the header can point at what it folds (and tests can find it).
  const body = `workspace-${w.id}-detail`;
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      {/* The whole header line is the control; folded, the summary stays. */}
      <button type="button" onClick={props.onToggle} aria-expanded={open} aria-controls={body}
        className="w-full text-left px-3 pt-2.5 pb-2 hover:bg-slate-50
                   transition-colors">
        <div className="flex items-baseline gap-2">
          {/* A workspace whose capacity is all shared has no segment, so no swatch. */}
          {props.colour && (
            <Swatch className={"self-center " + props.colour} />
          )}
          <span className="text-sm font-semibold text-slate-800">{w.name}</span>
          {w.shared.length > 0 && (
            <span className="text-3xs font-bold uppercase tracking-wide
                             bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
              {w.shared.length} shared
            </span>
          )}
          <span className="text-xs text-slate-400">
            {plural(w.locs.length, "location")}
          </span>
          <span className="grow" />
          <span className="text-sm font-bold tabular-nums">{n(w.total)}</span>
          <span className="text-2xs text-slate-400">
            {Math.round((w.total / (props.accountVus || 1)) * 100)}% of the account
          </span>
          <Chevron open={open} className="text-xs self-center" />
        </div>

        {/* The bar stays out of the fold; only the table folds. */}
        <div className="flex h-5 rounded overflow-hidden bg-slate-100 mt-1.5"
             style={{ width: `${Math.max((w.total / props.widest) * 100, 2)}%` }}>
          {locs.map((l, i) => (
            <div key={l.id}
              title={`${l.name} — ${n(l.rated_vus ?? 0)} rated VUs`
                + (l.shared ? " (shared)" : "")}
              className={BAND[i % BAND.length] + " h-full"}
              style={{
                width: `${((l.rated_vus ?? 0) / (w.total || 1)) * 100}%`,
                backgroundImage: l.shared ? STRIPE : undefined,
              }} />
          ))}
        </div>
      </button>

      <Collapse id={body} open={open}>
      <table className="w-full text-xs border-t border-slate-100">
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
          {locs.map((l, i) => <Row key={l.id} l={l} i={i} workspace={w.name} />)}
        </tbody>
      </table>
      {/* Shared locations with no agents yet have no stripe to explain, so
          they get a different sentence. */}
      {w.shared.length > 0 && (
        <p className="px-3 py-1.5 text-2xs text-amber-800 bg-amber-50 border-t border-amber-200">
          {w.sharedVus > 0 ? (
            <>
              Striped segments are shared — {n(w.sharedVus)} of this
              workspace&apos;s {n(w.total)} is claimable from another
              workspace too. Running it there leaves none of it here, and
              the account total counts it once.
            </>
          ) : (
            <>
              {w.shared.length === 1 ? "One location here is" : `${w.shared.length} locations here are`}
              {" "}shared with another workspace, but {w.shared.length === 1 ? "has" : "have"}
              {" "}no agents yet — so none of this workspace&apos;s {n(w.total)}
              {" "}is claimable elsewhere. Adding agents there changes that.
            </>
          )}
        </p>
      )}
      </Collapse>
    </div>
  );
}

function Row({ l, i, workspace }: { l: CapLocation; i: number; workspace: string }) {
  const elsewhere = l.workspace_names.filter((x) => x !== workspace);
  const down = l.agents - l.agents_reporting - l.agents_unknown;
  return (
    <tr className={i % 2 ? "bg-slate-50/60" : ""}>
      <td className="px-3 py-1.5">
        <span className="flex items-center gap-1.5 flex-wrap">
          <Swatch i={i} shared={l.shared} />
          <span className="font-medium text-slate-800">{l.name}</span>
          {l.shared && elsewhere.length > 0 && (
            <span className="text-3xs text-amber-700">
              also in {elsewhere.join(", ")}
            </span>
          )}
          {/* Down and unknown are different claims: a listing need not carry
              a heartbeat. */}
          {down > 0 && (
            <span className="text-3xs text-amber-700">
              {down} not reporting
            </span>
          )}
          {l.agents_unknown > 0 && (
            <span className="text-3xs text-slate-400"
              title="this listing carries no heartbeat for them — ask the agent itself">
              {l.agents_unknown} unchecked
            </span>
          )}
        </span>
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
  );
}
