// What survives a browser refresh: ids, options and typed inputs, in
// sessionStorage. Never the AUTH_TOKEN, which cannot be recovered and must not
// be written to disk; `strip` is where that is decided, and it is tested.
import { Options } from "./api";
import type { PlanInputs } from "./usePlan";
import type { SavedSizing } from "./sizings";

/** Bumped whenever the shape changes. A snapshot of another version is dropped
 *  whole rather than half-read, since other code believes its ids and options.
 *  Exported so the test can forge a snapshot at the current version. */
export const VERSION = 13;
const KEY = "bzm-opl-gen.session";

export interface Session {
  v: number;
  sourceMode: "connect" | "manual";
  accountId: number | null;
  workspaceId: number | null;
  harborId: string | null;
  /** Re-selected only if the location still has it. */
  shipId: string | null;
  /** Which location and agent were confirmed, as ids: a restore that puts back
   *  a different one simply fails to match. */
  confirmed: { loc: string | null; ship: string | null };
  manual: { harbor_id: string; ship_id: string };
  /** What manual entry declared the typed identity runs; it decides the
   *  images. Always empty in connect mode, where the location's funcIds say. App
   *  checks it against the served vocabulary on restore. */
  declaredFunctionalities: string[];
  options: Options;
  step: number;
  /** Which view is open; the capacity rollup is not a step. */
  view: "flow" | "capacity";
  /** What was typed into the sizing card. */
  plan: PlanInputs;
  /** The saved sizings, defaults included, or null before anything decided
   *  them. Null differs from `[]`, which is a list somebody emptied. */
  sizings: SavedSizing[] | null;
}


/** The options minus anything that must not be written down: the token. */
export function strip(options: Options): Options {
  const { auth_token: _drop, ...rest } = options;
  return rest;
}

export function save(s: Omit<Session, "v">): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(
      { ...s, v: VERSION, options: strip(s.options) }));
  } catch {
    // Private mode and disabled storage throw; a lost snapshot is not worth surfacing.
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
    // A snapshot written by some older build must not put a token back.
    return { ...s, options: strip(s.options ?? {}) } as Session;
  } catch { return null; }
}

export function clear(): void {
  try { sessionStorage.removeItem(KEY); } catch { /* see save() */ }
}
