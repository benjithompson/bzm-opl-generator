// Is this agent reporting, or is it a record of one that was?
//
// `state` alone says nothing: an agent that stops reporting keeps whatever
// state it last had, so a location listing shows IDLE agents that have been
// gone for weeks. The heartbeat is what separates the two, and how stale one
// may be is a rule -- a number and three edge cases -- rather than a fact the
// payload carries.
//
// It lived in App as a closure over `Date.now()`, handed down to the panel that
// reads it twice (the per-location "N online" count and the per-agent row). A
// rule passed as a function is a rule with no tests of its own: nothing could
// state the window, the missing heartbeat or the clock skew without rendering a
// page first. Here it is plain data in, boolean out.
//
// What it decides is not cosmetic. An agent that is online is already running
// somewhere, so the page will not auto-pick it for a new deployment and warns
// if you pick it by hand -- a second deployment on one identity conflicts with
// the install that is working.
//
// The server has its own statement of this (core.HEARTBEAT_FRESH_S, 120s, and
// `state in (idle, running)` with it) for a different read: /api/status asks
// after ONE agent in one location, where both fields are reliable and the
// answer is acted on immediately. This side judges a workspace *listing*, which
// is refreshed only when the workspace is, so its window is the longer one.
// Two reads, deliberately -- but only one of them is stated here, and if the
// windows are ever to converge the number to serve is core's.
import { Ship } from "./api";

/** How stale a heartbeat may be and still count as online, in seconds. */
export const HEARTBEAT_FRESH_S = 300;

/** Whether this agent is reporting now.
 *
 *  `now` is milliseconds and is a parameter so the rule can be stated in a test
 *  without a fake clock. Absent or 0 is "never heard from", which is what a
 *  freshly created agent looks like and must not read as offline-because-dead;
 *  a negative age is a machine whose clock is behind BlazeMeter's, and is no
 *  evidence of anything. */
export function shipOnline(s: Ship, now = Date.now()): boolean {
  if (!s.lastHeartBeat) return false;
  const age = now / 1000 - s.lastHeartBeat;
  return age >= 0 && age < HEARTBEAT_FRESH_S;
}

/** How many of a location's agents are reporting.
 *
 *  A function over the list rather than `ships.filter(shipOnline)` at each call
 *  site: `filter` hands its callback the index as a second argument, which
 *  `shipOnline` would take as the clock -- every agent judged against 1970 and
 *  every one of them offline. Undefined is a listing that carried no ships. */
export function onlineCount(ships: Ship[] | undefined, now = Date.now()): number {
  return (ships ?? []).filter((s) => shipOnline(s, now)).length;
}
