import { describe, expect, test } from "vitest";

import { EMPTY_PLAN_INPUTS, PlanInputs } from "./usePlan";
import { DEFAULT_SIZINGS, remove, save, sizingNamed } from "./sizings";

const perf = (users: string): PlanInputs => ({
  ...EMPTY_PLAN_INPUTS, functionalities: ["performance"],
  targets: { performance: users },
});

describe("the sizings a session remembers", () => {
  test("ships with one per functionality, so the control is never empty", () => {
    expect(DEFAULT_SIZINGS.map((s) => s.name).length).toBeGreaterThan(0);
    for (const s of DEFAULT_SIZINGS) {
      expect(s.inputs.functionalities.length).toBeGreaterThan(0);
    }
    // Every default names exactly what it sizes, so picking one from a list of
    // names is picking a functionality as much as a number.
    expect(new Set(DEFAULT_SIZINGS.flatMap((s) => s.inputs.functionalities)))
      .toEqual(new Set(["performance", "functionalGui", "mockServices"]));
  });

  test("a default is a starting point and can be edited away from", () => {
    // The fields *are* the sizing: picking one fills them and nothing is
    // bound to it afterwards. What this asserts is that the stored inputs are
    // not the object the page then mutates.
    const [first] = DEFAULT_SIZINGS;
    const edited = { ...first.inputs, targets: { performance: "99" } };
    expect(first.inputs.targets).not.toEqual(edited.targets);
  });

  test("saving under a new name adds it, keeping the defaults", () => {
    const out = save(DEFAULT_SIZINGS, "Black Friday", perf("40000"));
    expect(out.length).toBe(DEFAULT_SIZINGS.length + 1);
    expect(sizingNamed(out, "Black Friday")?.targets.performance).toBe("40000");
  });

  test("saving over a name replaces it in place, rather than twice", () => {
    const once = save(DEFAULT_SIZINGS, "Black Friday", perf("40000"));
    const twice = save(once, "Black Friday", perf("50000"));
    expect(twice.length).toBe(once.length);
    expect(sizingNamed(twice, "Black Friday")?.targets.performance)
      .toBe("50000");
    // In place: a re-save is a correction, not a reordering of the list
    // somebody is reading.
    expect(twice.map((s) => s.name)).toEqual(once.map((s) => s.name));
  });

  test("a name is trimmed, and a blank one saves nothing", () => {
    const out = save(DEFAULT_SIZINGS, "  Peak  ", perf("100"));
    expect(out[out.length - 1].name).toBe("Peak");
    expect(save(DEFAULT_SIZINGS, "   ", perf("100"))).toEqual(DEFAULT_SIZINGS);
  });

  test("removing takes only the one named", () => {
    const with_ = save(DEFAULT_SIZINGS, "Peak", perf("100"));
    const out = remove(with_, "Peak");
    expect(sizingNamed(out, "Peak")).toBeNull();
    expect(out.length).toBe(DEFAULT_SIZINGS.length);
  });

  test("a name nobody saved is null, not an empty sizing", () => {
    // The distinction this repo keeps everywhere: a sizing that is not there
    // and a sizing with nothing in it are different answers, and a caller
    // handed an empty one would fill the form with blanks and call it applied.
    expect(sizingNamed(DEFAULT_SIZINGS, "nope")).toBeNull();
  });
});
