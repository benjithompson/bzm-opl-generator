// One attempt at a time -- the property the four constructors exist for.
//
// The four fields used to be four pieces of state, and the residue was real: a
// download after a failed save showed the save's error under the new bundle's
// token report, because clearing the other three was written out at each call
// site and one of them was always missed. Constructed, that cannot happen, and
// this is the test that says so rather than a comment claiming it.
import { expect, test } from "vitest";

import { SavedBundle, TokenReport } from "./api";
import {
  Attempt, NO_ATTEMPT, downloadFailed, downloaded, saveFailed, savedTo,
} from "./attempt";

const report: TokenReport =
  { branch: "rotated", ship_id: "s-1", message: "a NEW AUTH_TOKEN was issued" };
const bundle: SavedBundle = {
  out_dir: "/home/me/bzm-opl/blazemeter",
  files: [{ name: "crane.yaml", bytes: 12 }],
  token: { branch: "reused", ship_id: "s-1", message: "kept the one in place" },
};

/** Which fields an attempt actually claims. */
const filled = (a: Attempt) =>
  Object.entries(a).filter(([, v]) => v != null).map(([k]) => k).sort();

test("nothing has been attempted, and no field claims otherwise", () => {
  expect(filled(NO_ATTEMPT)).toEqual([]);
});

test("each outcome fills only its own fields", () => {
  expect(filled(downloaded(report))).toEqual(["token"]);
  expect(filled(downloadFailed("no route"))).toEqual(["downloadError"]);
  expect(filled(saveFailed("no such folder"))).toEqual(["saveError"]);
  // Two, because a save reports where it landed *and* what it did to the
  // credential -- and the second is only knowable from the save's own answer.
  expect(filled(savedTo(bundle))).toEqual(["saved", "token"]);
});

test("a save reports the credential the server acted on, not a guess", () => {
  expect(savedTo(bundle).token).toBe(bundle.token);
});
