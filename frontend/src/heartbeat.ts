// Is this agent reporting? `state` alone cannot say: an agent that stops keeps
// its last state. An online agent is already running somewhere, so the page will
// not auto-pick it. The window is longer than core.HEARTBEAT_FRESH_S because this
// judges a workspace listing, refreshed only when the workspace is.
import { Ship } from "./api";

/** How stale a heartbeat may be and still count as online, in seconds. */
export const HEARTBEAT_FRESH_S = 300;

/** Whether this agent is reporting now (`now` in ms, a parameter for tests).
 *  No heartbeat is "never heard from"; a negative age is clock skew, not evidence. */
export function shipOnline(s: Ship, now = Date.now()): boolean {
  if (!s.lastHeartBeat) return false;
  const age = now / 1000 - s.lastHeartBeat;
  return age >= 0 && age < HEARTBEAT_FRESH_S;
}

/** How many of a location's agents are reporting. Not `filter(shipOnline)`,
 *  which would pass the index as the clock. */
export function onlineCount(ships: Ship[] | undefined, now = Date.now()): number {
  return (ships ?? []).filter((s) => shipOnline(s, now)).length;
}
