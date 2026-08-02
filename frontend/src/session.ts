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
export const VERSION = 5;
const KEY = "bzm-opl-gen.session";

export interface Session {
  v: number;
  sourceMode: "connect" | "manual";
  accountId: number | null;
  workspaceId: number | null;
  harborId: string | null;
  /** Re-selected only if the location still has it -- see App. */
  shipId: string | null;
  manual: { harbor_id: string; ship_id: string };
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
