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
// **The imported evidence document is not written here either**, and `strip`
// does not grow a second clause for it: the snapshot has no field it could
// arrive in (see SavedPreflight). That is deliberate and it is the stronger
// version of the same argument -- a value that cannot be expressed cannot be
// forgotten on the way out, where a value stripped in one function is one
// refactor away from being kept. The document is somebody else's cluster,
// collected by somebody else, and it is the one thing on this page that is
// neither an id nor a value typed into a form. What *is* written is the
// answer doctor gave about it -- the verdicts and the suggestions, which is
// what the panel is already showing to whoever is at this browser.
//
// Plain data in, data out: no React here, which is what makes session.test.ts
// possible without a DOM.
import { Options, PreflightOut } from "./api";
// The planner's form, as the planner declares it. A second copy of the
// shape here is a field that gets added to one and not the other.
import type { PlanInputs } from "./usePlan";
// What the preflight panel wrote into the options and what each of them held
// first -- suggestions.ts's own record, stored as it stands for the same reason
// PlanInputs is: the shape belongs to the module that reads it back.
import type { Applied } from "./suggestions";

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
// 5: the planner became step 1's capacity profile. Its engine size moved into
// the bundle options (which are stored here already), so PlanInputs is down to
// the two figures it owns -- and `view` no longer has a "plan" to restore, so a
// v4 snapshot would land the page on a view that does not exist.
// 6: step 1 grew the two confirmations. A v5 snapshot has none, and a missing
// pair would read as "confirmed nothing" -- which is the honest answer, but it
// is the same answer as a deliberate one, and this is the file whose whole rule
// is that a half-understood snapshot is worse than starting over.
// 7: manual entry's feature declaration joined the ids it belongs with. A v6
// snapshot cannot say what a typed identity was declared to run, and its
// absence reads as "declared nothing" -- which is what fell back to the first
// served feature and gathered another feature's images (#118).
// 8: the imported preflight -- its verdicts, its suggestions, the name of the
// file -- and the undo history for what was applied from it. A v7 snapshot
// carries the applied *values*, because those are options, and nothing that can
// explain or take them back (#119). Read half, it would put an undo history
// back against verdicts that are not there, which is the state the issue calls
// worse than restoring neither.
export const VERSION = 8;
const KEY = "bzm-opl-gen.session";

/** An imported cluster read, as much of it as is worth writing down.
 *
 *  **One field rather than two.** The verdicts and the undo history are one
 *  piece of work: the undo is only ever offered on a suggestion row, so a
 *  history restored without the list it is rendered in is an undo nothing can
 *  reach, and verdicts restored without the history are a panel that explains a
 *  change it can no longer reverse. Stored together they cannot come apart.
 *
 *  **What is not here is the evidence document**, and that is the size
 *  decision. The document grows with the cluster and the answer does not: a
 *  synthetic file of 3 realistic nodes is 32KB, the same file at 20 nodes is
 *  206KB and at 60 nodes 615KB, while the preflight answer stays at 4.2KB
 *  throughout -- it is bounded by doctor's check list (13) and suggest.py's
 *  rules (9, a dozen suggestions at the most), neither of which is a property
 *  of the cluster. Against a 5MB sessionStorage budget the answer is
 *  comfortable and the document is the one field that could exhaust it, and a
 *  quota refusal costs the *whole* snapshot -- `save` swallows it, so the ids,
 *  the options and this would all silently stop being written, not just the
 *  part that grew. That is why the unbounded half is left out rather than left
 *  in and guarded: there is nothing to guard it with that does not have to
 *  guess what will fit.
 *
 *  The cost is that a restored preflight cannot be re-judged -- re-running it
 *  needs the document -- so the verdicts stop following the configuration.
 *  `preflight.restored` is where that is carried and the panel is where it is
 *  said. */
export interface SavedPreflight {
  /** The file the verdicts came from. Named on screen, so a restored answer
   *  can still be traced back to the file that produced it -- and re-picked,
   *  where whoever is at the browser still has it. */
  file: string;
  out: PreflightOut;
  applied: Applied;
}

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
  /** What manual entry declared the typed identity runs, or null.
   *
   *  Kept with the ids because it is one of them: in manual entry the feature
   *  is not a view over a location, it is the declaration -- it names the funcId
   *  the facts are gathered for, which names the images the bundle carries. It
   *  was the one input deciding the bundle that a refresh did not restore, so a
   *  service-virtualization identity came back a performance one (#118).
   *
   *  Null in connect mode, and structurally rather than by convention: there the
   *  feature is derived from the location's funcIds, so a value written here
   *  would pin a restored page to whatever was last on screen instead of to what
   *  the account says. Restoring it is `App`'s, and it checks it against the
   *  served vocabulary first -- the same rule the confirmations keep by being
   *  stored as the ids they were made against. */
  declaredFeature: string | null;
  /** The imported cluster read, or null where no file has been imported.
   *
   *  Null is "nobody imported anything", and it is the only thing null says
   *  here -- the two situations this codebase keeps apart everywhere else are
   *  kept apart by there being no second way to arrive at it. A file that was
   *  imported is either written down whole (the file name, the verdicts and the
   *  history together) or the write failed and the previous snapshot stands; no
   *  path produces a half of one. What a *restore* then makes of it is
   *  preflight.ts's, through `fromSnapshot()`, because the page holds no
   *  document afterwards and the panel has to say so. */
  preflight: SavedPreflight | null;
  options: Options;
  step: number;
  /** Which of the two views is open. The account rollup is not a step, so the
   *  step number cannot say. */
  view: "flow" | "capacity";
  /** What was typed into the capacity profile. Kept for the same reason the
   *  option values are: they were typed, and nothing else can recover them --
   *  and somebody who refreshes while sizing a run must not come back to an
   *  empty target. */
  plan: PlanInputs;
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
