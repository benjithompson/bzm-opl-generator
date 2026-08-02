import { expect, test } from "vitest";

import { Ship } from "./api";
import { HEARTBEAT_FRESH_S, onlineCount, shipOnline } from "./heartbeat";

/** A ship whose last heartbeat was `ago` seconds before `now`. */
const beat = (ago: number, now: number): Ship =>
  ({ id: "s", name: "agent", state: "IDLE", lastHeartBeat: now / 1000 - ago });

const NOW = 1_700_000_000_000;

test("a fresh heartbeat is online, a stale one is not", () => {
  expect(shipOnline(beat(5, NOW), NOW)).toBe(true);
  expect(shipOnline(beat(HEARTBEAT_FRESH_S + 1, NOW), NOW)).toBe(false);
});

test("the window is a boundary, not a range with a hole in it", () => {
  expect(shipOnline(beat(HEARTBEAT_FRESH_S - 1, NOW), NOW)).toBe(true);
  // Exactly at the window: stale. One of the two has to be, and an agent that
  // has not been heard from for the whole window is the one to redeploy over.
  expect(shipOnline(beat(HEARTBEAT_FRESH_S, NOW), NOW)).toBe(false);
});

test("no heartbeat at all is not online", () => {
  // The listing carries none for an agent that has never reported, and 0 is
  // what BlazeMeter sends for one -- both mean "has not been heard from",
  // which is the state a freshly created agent is in.
  expect(shipOnline({ id: "s", name: "a", state: "IDLE" }, NOW)).toBe(false);
  expect(shipOnline({ id: "s", name: "a", state: "IDLE", lastHeartBeat: 0 },
                    NOW)).toBe(false);
});

test("a clock in the future does not read as online", () => {
  // A machine whose clock is behind BlazeMeter's produces a negative age. It is
  // not evidence of an agent reporting, and `< window` alone would take it.
  expect(shipOnline(beat(-3600, NOW), NOW)).toBe(false);
});

test("counting them takes a list, so nothing passes an index as the clock", () => {
  const ships = [beat(5, NOW), beat(9_000, NOW), beat(10, NOW)];
  expect(onlineCount(ships, NOW)).toBe(2);
  // A location whose listing carried no ships at all: none, not a crash.
  expect(onlineCount(undefined, NOW)).toBe(0);
});
