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
  // Exactly at the window: stale.
  expect(shipOnline(beat(HEARTBEAT_FRESH_S, NOW), NOW)).toBe(false);
});

test("no heartbeat at all is not online", () => {
  // No heartbeat, or 0: never heard from.
  expect(shipOnline({ id: "s", name: "a", state: "IDLE" }, NOW)).toBe(false);
  expect(shipOnline({ id: "s", name: "a", state: "IDLE", lastHeartBeat: 0 },
                    NOW)).toBe(false);
});

test("a clock in the future does not read as online", () => {
  // A negative age (clock skew) is not online.
  expect(shipOnline(beat(-3600, NOW), NOW)).toBe(false);
});

test("counting them takes a list, so nothing passes an index as the clock", () => {
  const ships = [beat(5, NOW), beat(9_000, NOW), beat(10, NOW)];
  expect(onlineCount(ships, NOW)).toBe(2);
  // A location whose listing carried no ships at all: none, not a crash.
  expect(onlineCount(undefined, NOW)).toBe(0);
});
