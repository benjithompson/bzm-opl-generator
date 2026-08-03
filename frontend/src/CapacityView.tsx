// What this account can generate, and where it lives.
//
// The number people actually want is one: how many virtual users can this
// account run at once. Everything else on the page exists to make that number
// checkable -- which workspace holds it, which location, and out of how many
// agents and engines -- because a total nobody can take apart is a total nobody
// believes.
//
// The bar and the table are one thing, not two. The bar says where the capacity
// is; the row beneath, sharing its colour, says how that location gets there.
// That is why there is no separate legend: it would repeat the table a
// centimetre higher while carrying four fewer figures.
//
// They part company in one place, and only one: a folded workspace keeps its
// bar and loses its table. The bar is the total the header line states, drawn
// against the widest workspace on the account, so a page of folded cards is a
// ranking; the table is the only part that is per location, and it is what
// makes the page several screens long.
//
// "Rated" is the load-bearing word, and it was measured rather than assumed --
// see core.account_capacity. `agents x engines-per-agent` is the engine count
// and BlazeMeter enforces it; multiplying by virtual users per engine gives
// what those engines are *sized* for, which a run may exceed and be packed
// onto them instead.
import { useMemo, useState } from "react";

import { Capacity, CapLocation } from "./api";
import {
  accountBands, byWorkspace, matching, WorkspaceRollup,
} from "./capacity";
import { Button, cardCls, inputCls } from "./components";
import { useFoldSet } from "./foldSet";

const n = (x: number) => x.toLocaleString();

const BAND = ["bg-bzm", "bg-sky-400", "bg-emerald-400", "bg-violet-400",
              "bg-amber-400", "bg-rose-400", "bg-teal-400", "bg-indigo-400"];

// Shared locations are striped rather than given a colour: amber was both "this
// is shared" and the fifth palette entry, so a shared segment and an ordinary
// one were the same swatch. The stripe rides on whatever colour the segment
// already has, so it reads as a texture rather than another category.
const STRIPE = "repeating-linear-gradient(45deg, rgba(255,255,255,.55) 0 3px,"
  + " rgba(255,255,255,0) 3px 7px)";

/** The colour chip that ties a row to its segment. `i` picks from BAND;
 *  `className` overrides that where the colour comes from somewhere else (the
 *  account bar assigns per workspace, not per row). */
function Swatch(props: { i?: number; shared?: boolean; className?: string }) {
  const colour = props.className
    ?? BAND[(props.i ?? 0) % BAND.length]
      + (props.shared ? " ring-1 ring-amber-600" : "");
  return (
    <span className={"inline-block w-2.5 h-2.5 rounded-sm shrink-0 " + colour}
      style={props.shared ? { backgroundImage: STRIPE } : undefined} />
  );
}

export function CapacityView({ cap }: { cap: Capacity }) {
  const [filter, setFilter] = useState("");
  // Grouped once per account, filtered from that. It used to group the whole
  // account twice on mount (once here, once for `widest`) and again on every
  // keystroke -- 171 locations against 166 workspaces each time, which also
  // handed every surviving card a new object and re-rendered its rows.
  const all = useMemo(() => byWorkspace(cap), [cap]);
  // The account's own bar: one segment per workspace, sized by what only that
  // workspace can claim, plus one for everything claimable from more than one.
  // They add up to the headline beside them -- see accountBands.
  const bands = useMemo(() => accountBands(cap), [cap]);
  // Colour by workspace name rather than by position in `bands`, so a
  // workspace card below can wear the same swatch without either side
  // recomputing the other's ordering.
  const bandColour = useMemo(() => {
    const m = new Map<string, string>();
    bands.forEach((b, i) => {
      if (!b.shared && !b.orphan) m.set(b.name, BAND[i % BAND.length]);
    });
    return m;
  }, [bands]);
  const q = filter.trim().toLowerCase();
  // Against the largest workspace *on the account*, not the largest match, so
  // filtering does not silently rescale every bar and make a small workspace
  // look like the whole account.
  const widest = useMemo(
    () => Math.max(...all.map((w) => w.total), 1), [all]);
  const spaces = useMemo(() => matching(all, filter), [all, filter]);
  const holding = new Set(cap.locations.flatMap((l) => l.workspace_ids)).size;
  const sharedCount = cap.locations.filter((l) => l.shared).length;
  // Which workspaces are folded away. An account has tens of these and each
  // carries a table as long as its location count, so the page is several
  // screens before it says anything; folded, it is an index of the account.
  const fold = useFoldSet();
  // Which way the one control goes, judged on what is on screen -- a button
  // offering to expand when everything visible is already open is a button
  // about workspaces the filter is hiding.
  const allFolded = fold.allFolded(spaces.map((w) => w.id));

  return (
    <div className="space-y-4">
      <div className={cardCls}>
        <div className="flex items-center gap-4 flex-wrap">
          <div>
            <div className="text-2xl font-bold text-slate-900 tabular-nums leading-none">
              {n(cap.rated_vus)}
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">account rated VUs</div>
          </div>
          <div className="text-xs text-slate-500">
            {/* Workspaces that hold a location. The account has far more, and
                counting those said "100 workspaces" about the 54 that matter. */}
            {cap.locations.length} locations · {holding} workspaces
            {sharedCount > 0 && <> · <b className="text-amber-700">{sharedCount} shared</b></>}
            {/* A location with no engines-per-agent or no virtual users per
                engine has no rating to state, and core sends `null` rather
                than 0 for exactly that reason -- 0 would read as "no
                capacity" when the truth is "nobody has said". The bars have to
                add it as nothing, so the count is said here instead: without
                it the page silently rounds an unanswered question down. */}
            {cap.unrated > 0 && (
              <> · <span title="no engines per agent or no virtual users per engine set, so there is no rating to state">
                {cap.unrated} unrated
              </span></>
            )}
          </div>
          <span className="grow" />
          {/* Folds every workspace on the account, not every one on screen:
              leaving the ones a filter is hiding open is a state that only
              shows itself when the filter is cleared. */}
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
            {/* The account total does not move with the filter: it is the
                account's, and a headline that changed as you typed would read as
                the sum of what is on screen. */}
            {filter.trim() && (
              <p className="text-[11px] text-slate-400 mt-1">
                {spaces.length} of {holding} shown ·{" "}
                {n(spaces.reduce((t, w) => t + w.total, 0))} rated VUs in view
              </p>
            )}
          </div>
        </div>

        {/* The headline, drawn. Full width and always the full width, because
            it is the whole account -- the workspace bars below are the ones
            that are shorter than the page, and they are shorter *against* this
            one. Each workspace card below carries the same swatch, which is
            what makes this readable without a legend. */}
        {bands.length > 0 && (
          <div>
            <div className="flex h-6 rounded overflow-hidden bg-slate-100">
              {bands.map((b) => (
                <div key={b.key}
                  title={`${b.name} — ${n(b.vus)} rated VUs`
                    + ` (${Math.round((b.vus / (cap.rated_vus || 1)) * 100)}%)`}
                  className={(b.shared || b.orphan ? "bg-slate-300"
                    : bandColour.get(b.name)) + " h-full transition-opacity "
                    // Filtering dims rather than removes: the bar is the
                    // account and stays the account, and what a search matches
                    // is worth pointing at inside it.
                    + (q && !b.shared && !b.orphan
                      && !b.name.toLowerCase().includes(q) ? "opacity-25" : "")}
                  style={{
                    width: `${(b.vus / (cap.rated_vus || 1)) * 100}%`,
                    backgroundImage: b.shared ? STRIPE : undefined,
                  }} />
              ))}
            </div>
            <p className="text-[11px] text-slate-400 mt-1">
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

/** One workspace: what it holds, folded or not.
 *
 *  Its own component because the fold made the map body longer than the view
 *  around it, and because `open` and `onToggle` are the whole of what the card
 *  needs to know about folding -- the set that decides it stays in the view, in
 *  one place, where "collapse all" can reach it. */
function WorkspaceCard(props: {
  w: WorkspaceRollup;
  /** The account total, for the percentage. */
  accountVus: number;
  /** The largest workspace on the account, which every bar is drawn against --
   *  see the view: not the largest *match*, or filtering would rescale them. */
  widest: number;
  /** The colour it wears in the account bar, or undefined where it has no
   *  segment up there to match. */
  colour?: string;
  open: boolean;
  onToggle: () => void;
}) {
  const { w, open } = props;
  const locs = w.locs;
  // Named so the header can point at what it folds, which is also how a
  // test says "this is hidden" about something CSS is hiding.
  const body = `workspace-${w.id}-detail`;
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      {/* The whole header line is the control. What stays visible when it
          is folded is the summary it already carried -- name, share,
          location count, total and percentage -- so a folded account
          reads as an index rather than as a list of names. What folds is
          the bar and the table, which are one thing (see the top of this
          file) and are the detail behind that summary. */}
      <button onClick={props.onToggle} aria-expanded={open} aria-controls={body}
        className="w-full text-left px-3 pt-2.5 pb-2 hover:bg-slate-50
                   transition-colors">
        <div className="flex items-baseline gap-2">
          {/* The colour this workspace has in the account bar above. A
              workspace whose capacity is *all* shared has no segment up
              there and so has no swatch here either, rather than being
              given a colour that appears nowhere. */}
          {props.colour && (
            <Swatch className={"self-center " + props.colour} />
          )}
          <span className="text-sm font-semibold text-slate-800">{w.name}</span>
          {w.shared.length > 0 && (
            <span className="text-[10px] font-bold uppercase tracking-wide
                             bg-amber-100 text-amber-800 rounded px-1.5 py-0.5">
              {w.shared.length} shared
            </span>
          )}
          <span className="text-xs text-slate-400">
            {w.locs.length} location{w.locs.length === 1 ? "" : "s"}
          </span>
          <span className="grow" />
          <span className="text-sm font-bold tabular-nums">{n(w.total)}</span>
          <span className="text-[11px] text-slate-400">
            {Math.round((w.total / (props.accountVus || 1)) * 100)}% of the account
          </span>
          <span className={"text-slate-400 text-xs self-center "
            + "transition-transform duration-150 "
            + (open ? "rotate-90" : "")}>›</span>
        </div>

        {/* The bar stays out of the fold. It is the same total the line above
            states, drawn -- against the largest workspace on the account, so
            the folded page is a ranking rather than 54 unrelated numbers. The
            table is what folds: it is the only part that is per location. */}
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

      {/* The same 0fr -> 1fr grid every other fold on this page uses: a
          card's height is not knowable in advance and `height: auto` does
          not transition. `invisible` as well as clipped, like the capacity
          profile's own disclosure: a folded table is out of the tab order and
          out of the accessibility tree, rather than merely nought pixels
          tall. */}
      <div id={body} aria-hidden={!open}
        className={"grid transition-[grid-template-rows] duration-[180ms] "
          + "ease-out " + (open ? "grid-rows-[1fr]" : "grid-rows-[0fr] invisible")}>
      <div className="overflow-hidden">
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
      {/* A shared location with no agents yet has no segment to stripe,
          and the sentence about the stripe then explains something that
          is not on screen -- and says "0 of 2,650 is claimable", which
          reads as a rounding error rather than as "nothing is deployed
          there". Both are worth saying; they are not the same sentence. */}
      {w.shared.length > 0 && (
        <p className="px-3 py-1.5 text-[11px] text-amber-800 bg-amber-50 border-t border-amber-200">
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
        </div>
      </div>
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
            <span className="text-[10px] text-amber-700">
              also in {elsewhere.join(", ")}
            </span>
          )}
          {/* Down and unlooked-at are different claims. A locations listing
              need not carry a heartbeat at all, and saying "not reporting"
              about an agent nothing asked after is how a working agent gets
              redeployed. The rating covers both either way -- it is what the
              location is sized for, not what is up this minute. */}
          {down > 0 && (
            <span className="text-[10px] text-amber-700">
              {down} not reporting
            </span>
          )}
          {l.agents_unknown > 0 && (
            <span className="text-[10px] text-slate-400"
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
