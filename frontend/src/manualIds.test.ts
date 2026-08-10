import { describe, expect, it } from "vitest";
import {
  blankManualIds, checkId, HARBOR, manualComplete, SHIP, tidy, TOKEN,
} from "./manualIds";

const id = "0a1b2c3d4e5f60718293a4b5";        // 24 hex, as an account issues them
const tok = "1a2b3c4d5e6f708192a3b4c5d6e7f809".repeat(2);   // 64 hex

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
    expect(tidy("0a1b2c3d4e5f\n60718293a4b5")).toBe(id);
    expect(tidy("  0a1b2c3d4e5f 60718293a4b5 ")).toBe(id);
  });
});

describe("manualComplete", () => {
  it("takes every field blank, because the bundle carries the markers", () => {
    // The use case this exists for: a customer whose private location has not
    // been created yet needs the manifests before BlazeMeter has issued any of
    // these values. The bundle then carries <HARBOR_ID>, <SHIP_ID> and
    // <AUTH_TOKEN>, the page says so, and the cluster refuses to apply it.
    expect(manualComplete("", "", "")).toBe(true);
    expect(manualComplete(id, "", "")).toBe(true);
    expect(manualComplete("", id, tok)).toBe(true);
    expect(manualComplete(id, id, tok)).toBe(true);
  });

  it("is false while any field is the wrong shape", () => {
    // Which is the failure it was written for, and is untouched: a truncated
    // paste renders a bundle that applies cleanly and joins nothing.
    expect(manualComplete(id.slice(0, 12), id, "")).toBe(false);
    expect(manualComplete(id, id, "not-a-token")).toBe(false);
  });
});

describe("blankManualIds", () => {
  it("names the fields in the order the form asks for them", () => {
    expect(blankManualIds("", "", "")).toEqual(
      ["harbor_id", "ship_id", "auth_token"]);
    expect(blankManualIds(id, "", tok)).toEqual(["ship_id"]);
    expect(blankManualIds(id, id, tok)).toEqual([]);
  });

  it("says nothing about a token it was not asked about", () => {
    // The download step's own line answers for the credential, off the branch
    // the server reports rather than off this form -- so it leaves the argument
    // out, and two sentences about one credential never appear together.
    expect(blankManualIds("", "")).toEqual(["harbor_id", "ship_id"]);
    expect(blankManualIds(id, id)).toEqual([]);
  });

  it("reads whitespace as blank, as every other reader here does", () => {
    expect(blankManualIds("   ", id)).toEqual(["harbor_id"]);
  });
});
