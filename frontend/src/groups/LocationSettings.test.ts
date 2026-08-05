// The one rule the settings panel's two buttons read: is this field a change?
//
// Everything visible follows it -- the label (Confirm vs Save), whether Reset
// is live, whether the arrow lights up, and which fields are sent -- so the
// panel's own state is not what needs a test; this is.
import { describe, expect, it } from "vitest";

import { same } from "./LocationSettings";

describe("same", () => {
  it("is not a change when the number is the same, however it is written", () => {
    expect(same("4", "4")).toBe(true);
    expect(same("4.0", "4")).toBe(true);
    expect(same(" 4 ", "4")).toBe(true);
    expect(same("04", "4")).toBe(true);
    expect(same("8192", "8192")).toBe(true);
  });

  it("is a change when the number differs", () => {
    expect(same("4", "5")).toBe(false);
    expect(same("500", "1000")).toBe(false);
  });

  // Blank means "leave this one alone", so it is neither zero nor a wildcard:
  // filling an unset setting is a change, and clearing a set one is not one
  // this panel makes.
  it("matches blank only against blank", () => {
    expect(same("", "")).toBe(true);
    expect(same("", "4")).toBe(false);
    expect(same("4", "")).toBe(false);
    expect(same("", "0")).toBe(false);
  });
});
