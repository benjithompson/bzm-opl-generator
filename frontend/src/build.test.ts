import { expect, test } from "vitest";
import { buildNotice, REBUILD } from "./build";

test("a page not built from this code is a warning", () => {
  const notice = buildNotice(true);
  expect(notice?.tone).toBe("warning");
  expect(notice?.heading).toMatch(/not built from the code serving it/i);
  expect(notice?.command).toBe(REBUILD);
});

test("a page that records nothing is a note, never a warning", () => {
  // Built before the fingerprint existed: not known wrong, so a quiet note.
  const notice = buildNotice("unrecorded");
  expect(notice?.tone).toBe("note");
  expect(notice?.heading).toMatch(/records nothing about what it was built/i);
  expect(notice?.detail).toMatch(/has not been checked/i);
  expect(notice?.command).toBe(REBUILD);
});

test("the two answers that are not a stale page say nothing", () => {
  // Matching (false) and a wheel (null) both say nothing.
  expect(buildNotice(false)).toBeNull();
  expect(buildNotice(null)).toBeNull();
});

test("each of the four answers is its own outcome", () => {
  // No two answers share a wording.
  const seen = ([true, false, "unrecorded", null] as const)
    .map((s) => JSON.stringify(buildNotice(s)));
  expect(new Set(seen).size).toBe(3);      // two of them are the same silence
  expect(seen[1]).toBe(seen[3]);           // false and null, both null
  expect(seen[0]).not.toBe(seen[2]);       // stale and unrecorded, never alike
});
