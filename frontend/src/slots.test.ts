// The rule BlazeMeter would apply to the new-location form, applied before it.
//
// Data in, data out -- the funcIds ticked, the number typed, and the served
// table -- so it runs with no DOM, like the option groups and sv.ts. The
// minimums are a fixture rather than literals here: the number came off a live
// POST and the sentence is BlazeMeter's own, and `fixtures.ts` is the one copy
// of both, held equal to core by tests/test_server.py.
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
    // `slots` is engines per agent and a real cost -- accounts run 17 agents
    // at slots=1 -- so a form that offered the higher number to everybody
    // would be this page raising a setting nobody asked it to.
    expect(slotRule(["performance"], SLOT_MINIMUMS)).toBeNull();
    expect(slotRule([], SLOT_MINIMUMS)).toBeNull();
  });

  it("takes the strictest of the rules that apply", () => {
    // Two funcIds with minimums is not a case the account has today; the
    // answer still cannot depend on which box was ticked first.
    const two: Record<string, SlotMinimum> = {
      ...SLOT_MINIMUMS,
      somethingBigger: { label: "Something Bigger", minimum: 4, message: "x" },
    };
    expect(slotRule(["functionalGui", "somethingBigger"], two)?.minimum).toBe(4);
    expect(slotRule(["somethingBigger", "functionalGui"], two)?.minimum).toBe(4);
  });

  it("refuses nothing while the table has not been read", () => {
    // Empty is "not asked yet", never "no rules" -- the same direction the
    // docker-ignored table goes. A create the account then rejects beats a
    // form refusing on a guess.
    expect(slotRule(["functionalGui"], {})).toBeNull();
  });
});

describe("slotsBlockedBy", () => {
  it("gives BlazeMeter's own sentence, and only that", () => {
    // Verbatim, because it is what a customer meeting this rule in
    // BlazeMeter's UI reads -- one refusal, one spelling. What to type is
    // said on the field, which is where the number is.
    expect(slotsBlockedBy(["functionalGui"], 1, SLOT_MINIMUMS))
      .toBe(SLOT_MINIMUMS.functionalGui.message);
  });

  it("lets through what the account would accept", () => {
    expect(slotsBlockedBy(["functionalGui"], 2, SLOT_MINIMUMS)).toBe("");
    expect(slotsBlockedBy(["functionalGui"], 7, SLOT_MINIMUMS)).toBe("");
    expect(slotsBlockedBy(["performance"], 1, SLOT_MINIMUMS)).toBe("");
  });

  it("blocks a slots field somebody has emptied", () => {
    // NumberInput hands back "" as NaN, and NaN < 2 is false -- so a blank
    // field would have sailed past a bare comparison into the POST.
    expect(slotsBlockedBy(["functionalGui"], NaN, SLOT_MINIMUMS)).not.toBe("");
    expect(slotsBlockedBy(["functionalGui"], 0, SLOT_MINIMUMS)).not.toBe("");
  });
});
