import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { goneNotice, isGone, vanishedNotice } from "./stale";

describe("isGone", () => {
  it("is 404 and nothing else", () => {
    expect(isGone(new ApiError("no such location", 404))).toBe(true);
  });

  // Only a 404 means gone; these may come right on their own.
  it.each([
    [401, "a key the account has stopped accepting"],
    [403, "an endpoint this account restricts"],
    [502, "BlazeMeter answering badly"],
    [500, "something broken in here"],
  ])("does not read %i as a deletion (%s)", (status) => {
    expect(isGone(new ApiError("nope", status))).toBe(false);
  });

  it("does not read a failure with no status as a deletion", () => {
  // No status at all (the server not running): not evidence of anything.
    expect(isGone(new Error("Failed to fetch"))).toBe(false);
    expect(isGone(null)).toBe(false);
    expect(isGone({ status: 404 })).toBe(false);
  });
});

describe("goneNotice", () => {
  it("names what was asked about", () => {
    const e = new ApiError("not found", 404);
    expect(goneNotice(e, "location")).toContain("location");
    expect(goneNotice(e, "agent")).toContain("agent");
  });

  it("sends the reader to Refresh and never to a page reload", () => {
    // Refresh, never reload: a pasted token would not survive one.
    const msg = goneNotice(new ApiError("not found", 404), "location")!;
    expect(msg).toContain("Refresh");
    expect(msg.toLowerCase()).not.toContain("reload");
    expect(msg.toLowerCase()).not.toContain("refresh the page");
  });

  it("is null for anything that is not a deletion", () => {
    // Null, so the caller's own message is used.
    expect(goneNotice(new ApiError("expired", 401), "agent")).toBeNull();
    expect(goneNotice(new Error("Failed to fetch"), "location")).toBeNull();
  });
});

describe("vanishedNotice", () => {
  it("does not ask for the refresh that just happened", () => {
    // Found by a Refresh, so it does not say to press Refresh.
    const msg = vanishedNotice("location");
    expect(msg).toContain("location");
    expect(msg).not.toContain("Refresh");
  });
});
