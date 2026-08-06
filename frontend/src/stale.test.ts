import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { goneNotice, isGone, vanishedNotice } from "./stale";

describe("isGone", () => {
  it("is 404 and nothing else", () => {
    expect(isGone(new ApiError("no such location", 404))).toBe(true);
  });

  // The whole reason this module exists. Every one of these used to arrive as
  // the same 502 as a deleted location, because core._upstream threw the status
  // away -- so the page could only ever say "something went wrong", and the two
  // remedies are opposites: press Refresh, or wait.
  it.each([
    [401, "a key the account has stopped accepting"],
    [403, "an endpoint this account restricts"],
    [502, "BlazeMeter answering badly"],
    [500, "something broken in here"],
  ])("does not read %i as a deletion (%s)", (status) => {
    expect(isGone(new ApiError("nope", status))).toBe(false);
  });

  it("does not read a failure with no status as a deletion", () => {
    // fetch rejecting because nothing is listening, a TypeError from anywhere
    // else: no status, so no evidence, so not this rule's business. Answering
    // "the location was deleted" to a server that is not running is the false
    // "there is nothing there" the whole module is against.
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
    // A reload is not the same action and is not free: an AUTH_TOKEN that was
    // pasted does not survive one, so a sentence saying "reload" can cost a
    // credential that cannot be read back.
    const msg = goneNotice(new ApiError("not found", 404), "location")!;
    expect(msg).toContain("Refresh");
    expect(msg.toLowerCase()).not.toContain("reload");
    expect(msg.toLowerCase()).not.toContain("refresh the page");
  });

  it("is null for anything that is not a deletion", () => {
    // Null rather than a fallback sentence: the caller has BlazeMeter's own
    // words and somewhere to put them, and a fallback here would render this
    // module's wording over an error it knows more about.
    expect(goneNotice(new ApiError("expired", 401), "agent")).toBeNull();
    expect(goneNotice(new Error("Failed to fetch"), "location")).toBeNull();
  });
});

describe("vanishedNotice", () => {
  it("does not ask for the refresh that just happened", () => {
    // Discovered by a Refresh coming back without it, so telling somebody to
    // press Refresh would be telling them to repeat what found this out.
    const msg = vanishedNotice("location");
    expect(msg).toContain("location");
    expect(msg).not.toContain("Refresh");
  });
});
