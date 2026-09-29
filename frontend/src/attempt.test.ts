// Each constructor yields a whole attempt, so no field from a previous one
// survives into the next.
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
