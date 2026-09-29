// The rule BlazeMeter applies to a new location's `slots`, applied first. The
// minimums come from fixtures.ts, the one copy of the served table.
import { describe, expect, it } from "vitest";
import { SlotMinimum } from "./api";
import { SLOT_MINIMUMS } from "./fixtures";
import { slotRule, slotsBlockedBy } from "./slots";

describe("slotRule", () => {
  it("names the rule a declaration has to satisfy", () => {
    expect(slotRule(["functionalGui"], SLOT_MINIMUMS)?.label)
      .toBe("GUI Functional");
    expect(slotRule(["performance", "functionalGui"], SLOT_MINIMUMS)?.minimum)
      .toBe(2);
  });

  it("has nothing to say about a declaration no rule reaches", () => {
    // No minimum means no rule: slots is a real cost and not raised for anybody.
    expect(slotRule(["performance"], SLOT_MINIMUMS)).toBeNull();
    expect(slotRule([], SLOT_MINIMUMS)).toBeNull();
  });

  it("takes the strictest of the rules that apply", () => {
    // The strictest applies, whatever order the boxes were ticked in.
    const two: Record<string, SlotMinimum> = {
      ...SLOT_MINIMUMS,
      somethingBigger: { label: "Something Bigger", minimum: 4, message: "x" },
    };
    expect(slotRule(["functionalGui", "somethingBigger"], two)?.minimum).toBe(4);
    expect(slotRule(["somethingBigger", "functionalGui"], two)?.minimum).toBe(4);
  });

  it("refuses nothing while the table has not been read", () => {
    // Empty is "not read yet": no rule.
    expect(slotRule(["functionalGui"], {})).toBeNull();
  });
});

describe("slotsBlockedBy", () => {
  it("gives BlazeMeter's own sentence, and only that", () => {
    // BlazeMeter's sentence, verbatim.
    expect(slotsBlockedBy(["functionalGui"], 1, SLOT_MINIMUMS))
      .toBe(SLOT_MINIMUMS.functionalGui.message);
  });

  it("lets through what the account would accept", () => {
    expect(slotsBlockedBy(["functionalGui"], 2, SLOT_MINIMUMS)).toBe("");
    expect(slotsBlockedBy(["functionalGui"], 7, SLOT_MINIMUMS)).toBe("");
    expect(slotsBlockedBy(["performance"], 1, SLOT_MINIMUMS)).toBe("");
  });

  it("blocks a slots field somebody has emptied", () => {
    // A blank field (NaN) is below the minimum.
    expect(slotsBlockedBy(["functionalGui"], NaN, SLOT_MINIMUMS)).not.toBe("");
    expect(slotsBlockedBy(["functionalGui"], 0, SLOT_MINIMUMS)).not.toBe("");
  });
});
