// What survives a browser refresh, and the one thing that must not.
//
// The API key lives in the server process, so a refresh never disconnected
// anything -- the page simply forgot which account, location and agent it had
// been pointed at, and asked for all of it again. This is that memory, and it
// is deliberately small: ids and options, never a credential.
//
// **The AUTH_TOKEN is never written here.** The page says it is held for this
// browser session and that nothing writes it down, and sessionStorage is
// writing it down -- it lands in the profile the browser keeps on disk. It is
// also the one value in the options that cannot be recovered by asking
// BlazeMeter again, so persisting it trades the only real secret on the page
// for saving one paste. `strip` is where that decision lives, and it is tested.
//
// Plain data in, data out: no React here, which is what makes session.test.ts
// possible without a DOM.
import { Options } from "./api";
// The planner's form, as the planner declares it. A second copy of the
// shape here is a field that gets added to one and not the other.
import type { PlanInputs } from "./usePlan";
// ...and the saved ones, as `sizings` declares them, for the same reason.
import type { SavedSizing } from "./sizings";

/** Bumped when the shape changes. A snapshot from an older build is dropped
 *  rather than half-read: the fields are ids and options that other code
 *  believes, and a partially-understood one is worse than starting over.
 *
 *  Exported for the test that forges a snapshot at the *current* version --
 *  written against a literal, it started passing for the wrong reason the
 *  first time this was bumped. */
// 3: the planner grew an `agents` field. 4: it lost it again -- a v3 snapshot
// would restore a key PlanInputs no longer has, and the panel would carry a
// value nothing reads. Half-reading either way is what the version stops.
// 5: the planner became step 1's sizing card. Its engine size moved into
// the bundle options (which are stored here already), so PlanInputs is down to
// the two figures it owns -- and `view` no longer has a "plan" to restore, so a
// v4 snapshot would land the page on a view that does not exist.
// 6: step 1 grew the two confirmations. A v5 snapshot has none, and a missing
// pair would read as "confirmed nothing" -- which is the honest answer, but it
// is the same answer as a deliberate one, and this is the file whose whole rule
// is that a half-understood snapshot is worse than starting over.
// 7: manual entry's functionality declaration joined the ids it belongs with.
// A v6 snapshot cannot say what a typed identity was declared to run, and its
// absence reads as "declared nothing" -- which is what fell back to the first
// served functionality and gathered another one's images (#118).
// 8: the imported preflight -- its verdicts, its suggestions, the name of the
// file -- and the undo history for what was applied from it. A v7 snapshot
// carries the applied *values*, because those are options, and nothing that can
// explain or take them back (#119). Read half, it would put an undo history
// back against verdicts that are not there, which is the state the issue calls
// worse than restoring neither.
// 9: the preflight left the page, and its field with it. A v8 snapshot holds
// options that were written from a suggestion beside verdicts nothing renders
// any more -- the same half-a-pair #119 is about, arrived at from the other
// side, so it is dropped rather than part-read.
// 10: `declaredFeature` is `declaredFunctionality` (#155). A pure rename, and
// still a bump rather than a migration: `load()` returns null on a version it
// does not know, and reading a v9 snapshot under this shape would restore every
// id and option *except* the declaration -- a typed identity landing back on the
// first served functionality, which is what 7 was added to stop.
// 11: a functionality id is BlazeMeter's funcId (#149), so the declaration a
// v10 snapshot holds is spelled `sv` where this build says `mockServices`.
// Version 10's own reasoning, one rename along: the vocabulary check would drop
// a declaration it cannot find and restore every other id and option -- a typed
// SV identity landing back on Performance with its namespace intact, which is
// the half-read state 7 was added to stop. Dropped whole instead.
// 12: the declaration is a *list* (#151). A v11 snapshot holds a string where
// this build reads an array, and every reader of it -- the vocabulary check,
// the funcIds the facts are gathered for, the cards -- would take the string's
// characters for ids. Migrating it to a one-element list is the obvious
// alternative and is exactly what this file refuses to do: `load()` returns
// null on a version it does not know, and a migration is a second shape of the
// snapshot to keep right forever so that one refresh keeps a namespace. Dropped
// whole, as 10 and 11 were.
// 13: the sizing grew from two figures to three models (#154). A v12 snapshot
// holds `plan: {users, vusPerEngine}` where this build reads which
// functionalities are being sized and a target per unit, and it holds no saved
// sizings at all. Read half, the card would come back sizing nothing with a
// target nothing renders -- and a session whose whole subject is a number
// somebody typed must not come back holding it somewhere invisible. Dropped
// whole, as 10, 11 and 12 were.
export const VERSION = 13;
const KEY = "bzm-opl-gen.session";

export interface Session {
  v: number;
  sourceMode: "connect" | "manual";
  accountId: number | null;
  workspaceId: number | null;
  harborId: string | null;
  /** Re-selected only if the location still has it -- see App. */
  shipId: string | null;
  /** Which location and which agent were confirmed on step 1.
   *
   *  The ids, matching what App holds, and not two booleans -- for the reason
   *  they are ids there: a confirmation is about a selection, so it has to
   *  withdraw when the selection changes. Stored as flags they would survive a
   *  restore that put back a *different* agent, and the step would come back
   *  finished for a pairing this session never checked. Restored as ids they
   *  simply fail to match, which is the same rule doing the same work.
   *
   *  Kept for the same reason the selections are: a refresh is not a decision,
   *  and re-confirming what was already confirmed is asking somebody to repeat
   *  themselves to prove the browser was listening. */
  confirmed: { loc: string | null; ship: string | null };
  manual: { harbor_id: string; ship_id: string };
  /** What manual entry declared the typed identity runs.
   *
   *  Kept with the ids because it is one of them: in manual entry a
   *  functionality is not a view over a location, it is the declaration -- it
   *  names the funcIds the facts are gathered for, which name the images the
   *  bundle carries. It was the one input deciding the bundle that a refresh
   *  did not restore, so a service-virtualization identity came back a
   *  performance one (#118).
   *
   *  A list since #151, because one id could not say what a real location is:
   *  71 of 168 locations in one account run performance and GUI functional
   *  together. Dropping a member the vocabulary no longer offers must not drop
   *  the rest, which is `App`'s check rather than this file's.
   *
   *  Empty in connect mode, and structurally rather than by convention: there
   *  the functionalities are derived from the location's funcIds, so a value
   *  written here would pin a restored page to whatever was last on screen
   *  instead of to what the account says. Restoring it is `App`'s, and it checks
   *  it against the served vocabulary first -- the same rule the confirmations
   *  keep by being stored as the ids they were made against. */
  declaredFunctionalities: string[];
  options: Options;
  step: number;
  /** Which of the two views is open. The account rollup is not a step, so the
   *  step number cannot say. */
  view: "flow" | "capacity";
  /** What was typed into the sizing. Kept for the same reason the
   *  option values are: they were typed, and nothing else can recover them --
   *  and somebody who refreshes while sizing a run must not come back to an
   *  empty target. */
  plan: PlanInputs;
  /** The sizings saved under a name, defaults included.
   *
   *  Here rather than in localStorage, and that is the issue's choice as much
   *  as this file's: a sizing belongs to the session that is sizing, in the way
   *  the account, the location and the typed target do, and this is where all
   *  of those live. It survives a refresh and not a closed tab. The defaults
   *  are stored alongside the saved ones rather than merged in on read, so
   *  removing one stays removed -- a default that came back on the next load
   *  would be a list nobody could edit. */
  sizings: SavedSizing[];
}


/** The options minus anything that must not be written down. Exported because
 *  it is the whole safety argument: one function, one test, one place to look
 *  when a new secret-ish option appears. */
export function strip(options: Options): Options {
  const { auth_token: _drop, ...rest } = options;
  return rest;
}

export function save(s: Omit<Session, "v">): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(
      { ...s, v: VERSION, options: strip(s.options) }));
  } catch {
    // Private-mode quotas and disabled storage both throw. Losing the snapshot
    // is a worse page, not a broken one, so it is not worth surfacing.
  }
}

/** The stored session, or null if there is none, it is unreadable, or it was
 *  written by a build that shaped it differently. */
export function load(): Session | null {
  let raw: string | null = null;
  try { raw = sessionStorage.getItem(KEY); } catch { return null; }
  if (!raw) return null;
  try {
    const s = JSON.parse(raw);
    if (!s || typeof s !== "object" || s.v !== VERSION) return null;
    // The token cannot arrive this way even if something wrote one: a snapshot
    // from a build that persisted it must not put it back into the options.
    return { ...s, options: strip(s.options ?? {}) } as Session;
  } catch { return null; }
}

export function clear(): void {
  try { sessionStorage.removeItem(KEY); } catch { /* see save() */ }
}
