// The rule both of the settings panel's buttons read: is this field a change?
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

  // Blank means "leave alone": it matches only blank.
  it("matches blank only against blank", () => {
    expect(same("", "")).toBe(true);
    expect(same("", "4")).toBe(false);
    expect(same("4", "")).toBe(false);
    expect(same("", "0")).toBe(false);
  });
});
