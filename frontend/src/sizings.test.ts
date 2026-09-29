import { describe, expect, test } from "vitest";

import { SIZING_MODELS } from "./fixtures";
import { EMPTY_PLAN_INPUTS, PlanInputs } from "./usePlan";
import { defaultSizings, remove, save, sizingNamed } from "./sizings";

// Built from the served models' one copy (fixtures.ts).
const DEFAULT_SIZINGS = defaultSizings(SIZING_MODELS);

const perf = (users: string): PlanInputs => ({
  ...EMPTY_PLAN_INPUTS, functionalities: ["performance"],
  targets: { performance: users },
});

describe("the sizings a session remembers", () => {
  test("is one sizing per served model, so the control is never empty", () => {
    expect(DEFAULT_SIZINGS.map((s) => s.name).length).toBeGreaterThan(0);
    for (const s of DEFAULT_SIZINGS) {
      expect(s.inputs.functionalities.length).toBeGreaterThan(0);
    }
    // One default per served model, each naming what it sizes.
    expect(new Set(DEFAULT_SIZINGS.flatMap((s) => s.inputs.functionalities)))
      .toEqual(new Set(SIZING_MODELS.map((m) => m.functionality)));
    // ...and its target is the model's own example, in the model's own unit.
    expect(DEFAULT_SIZINGS.map((s) => s.name))
      .toEqual(SIZING_MODELS.map(
        (m) => `${m.label}: ${m.example_target.toLocaleString()} ${m.unit}`));
  });

  test("a default is a starting point and can be edited away from", () => {
    // A copy: editing the fields does not change the stored sizing.
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
    // Replaced in place.
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
    // Not there is null, not an empty sizing.
    expect(sizingNamed(DEFAULT_SIZINGS, "nope")).toBeNull();
  });
});
