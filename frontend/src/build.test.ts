import { expect, test } from "vitest";
import { buildNotice, REBUILD } from "./build";

test("a page not built from this code is a warning", () => {
  const notice = buildNotice(true);
  expect(notice?.tone).toBe("warning");
  expect(notice?.heading).toMatch(/not built from the code serving it/i);
  expect(notice?.command).toBe(REBUILD);
});

test("a page that records nothing is a note, never a warning", () => {
  // The fourth state (#238). It is *not read*: the page was built before the
  // fingerprint existed, so nothing is known to be wrong with it. An amber bar
  // here would appear on every such checkout, which is the crying-wolf defect
  // the fingerprint replaced mtimes to stop.
  const notice = buildNotice("unrecorded");
  expect(notice?.tone).toBe("note");
  expect(notice?.heading).toMatch(/records nothing about what it was built/i);
  expect(notice?.detail).toMatch(/has not been checked/i);
  expect(notice?.command).toBe(REBUILD);
});

test("the two answers that are not a stale page say nothing", () => {
  // False is compared-and-current; null is a wheel, with no sources for the
  // question to be about. Both are silence, and neither reaches the other's
  // sentence -- which is the whole reason the four are four.
  expect(buildNotice(false)).toBeNull();
  expect(buildNotice(null)).toBeNull();
});

test("each of the four answers is its own outcome", () => {
  // Stated as a set, because the failure this guards against is two of them
  // collapsing into one -- the same wording, or the same silence, for answers
  // with different remedies.
  const seen = ([true, false, "unrecorded", null] as const)
    .map((s) => JSON.stringify(buildNotice(s)));
  expect(new Set(seen).size).toBe(3);      // two of them are the same silence
  expect(seen[1]).toBe(seen[3]);           // false and null, both null
  expect(seen[0]).not.toBe(seen[2]);       // stale and unrecorded, never alike
});
