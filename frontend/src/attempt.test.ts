// One attempt at a time -- the property the constructors exist for.
//
// The fields used to be separate pieces of state, and the residue was real: a
// download after a failed save showed the save's error under the new bundle's
// token report, because clearing the others was written out at each call site
// and one of them was always missed. Constructed, that cannot happen, and this
// is the test that says so rather than a comment claiming it.
//
// Two outcomes went with the Save to folder button, and the property is what
// made that a deletion rather than a rewrite: there was no reset to unpick,
// only a constructor nobody calls.
import { expect, test } from "vitest";

import { TokenReport } from "./api";
import { Attempt, NO_ATTEMPT, downloadFailed, downloaded } from "./attempt";

const report: TokenReport =
  { branch: "rotated", ship_id: "s-1", message: "a NEW AUTH_TOKEN was issued" };

/** Which fields an attempt actually claims. */
const filled = (a: Attempt) =>
  Object.entries(a).filter(([, v]) => v != null).map(([k]) => k).sort();

test("nothing has been attempted, and no field claims otherwise", () => {
  expect(filled(NO_ATTEMPT)).toEqual([]);
});

test("each outcome fills only its own fields", () => {
  expect(filled(downloaded(report))).toEqual(["token"]);
  expect(filled(downloadFailed("no route"))).toEqual(["downloadError"]);
});

test("a download reports the credential the server acted on, not a guess", () => {
  expect(downloaded(report).token).toBe(report);
});
