import { describe, expect, it } from "vitest";
import {
  checkId, HARBOR, manualComplete, SHIP, tidy, TOKEN,
} from "./manualIds";

const id = "6a63a79dcc45dccca90bf440";        // 24 hex, as an account issues them
const tok = "af1736ce6c96ec3ecd2c3838ad20ed3c".repeat(2);   // 64 hex

describe("checkId", () => {
  it("accepts what BlazeMeter actually issues", () => {
    expect(checkId(HARBOR, id)).toBeNull();
    expect(checkId(SHIP, id)).toBeNull();
    expect(checkId(TOKEN, tok)).toBeNull();
  });

  it("accepts upper case, which is the same id", () => {
    expect(checkId(HARBOR, id.toUpperCase())).toBeNull();
  });

  it("says nothing about a blank field", () => {
    // Whether it is *needed* is the step's question, not this one's -- a red
    // message under a field nobody has reached yet is noise.
    expect(checkId(HARBOR, "")).toBeNull();
    expect(checkId(TOKEN, "   ")).toBeNull();
  });

  it("counts a truncated paste and says which way it is wrong", () => {
    const short = checkId(HARBOR, id.slice(0, 20));
    expect(short).toMatch(/24 characters — this is 20/);
    expect(short).toMatch(/missing/);
    expect(checkId(HARBOR, id + "aa")).toMatch(/this is 26/);
    expect(checkId(HARBOR, id + "aa")).not.toMatch(/missing/);
  });

  it("names the character that is not hexadecimal", () => {
    // The reason it names it: one character from the wrong keyboard layout is
    // invisible in a 24-character string.
    expect(checkId(HARBOR, "6a63a79dcc45dccca90bf44g")).toMatch(/“g”/);
    expect(checkId(HARBOR, "6a63a79dcc45dccca90bf4 0")).toMatch(/space/);
  });

  it("holds the token to its own length, not the ids'", () => {
    expect(checkId(TOKEN, id)).toMatch(/64 characters — this is 24/);
    expect(checkId(HARBOR, tok)).toMatch(/24 characters — this is 64/);
  });
});

describe("tidy", () => {
  it("removes the newline a wrapped install command pastes in", () => {
    expect(tidy("6a63a79dcc45\ndccca90bf440")).toBe(id);
    expect(tidy("  6a63a79dcc45 dccca90bf440 ")).toBe(id);
  });
});

describe("manualComplete", () => {
  it("wants both ids, and a token only if one was typed", () => {
    expect(manualComplete(id, id, "")).toBe(true);
    expect(manualComplete(id, id, tok)).toBe(true);
    expect(manualComplete(id, "", tok)).toBe(false);
    expect(manualComplete("", id, tok)).toBe(false);
  });

  it("is false while any field is the wrong shape", () => {
    expect(manualComplete(id.slice(0, 12), id, "")).toBe(false);
    expect(manualComplete(id, id, "not-a-token")).toBe(false);
  });
});
