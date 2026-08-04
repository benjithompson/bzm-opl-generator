import { describe, expect, it } from "vitest";
import {
  customSeed, ENGINE_POOL, placementOf, placementPatch, rowsToSelector,
  rowsToTolerations, selectorToRows, tolerationField, tolerationsToRows,
  withTolerationField,
} from "./sched";

describe("placementOf", () => {
  it("both unset is: engines follow crane", () => {
    expect(placementOf({})).toBe("crane");
    expect(placementOf({ engine_node_selector: null, engine_tolerations: null }))
      .toBe("crane");
  });

  it("both set and empty is: engines run anywhere", () => {
    expect(placementOf({ engine_node_selector: {}, engine_tolerations: [] }))
      .toBe("anywhere");
  });

  it("a non-empty selector is a separate pool, whatever it is named", () => {
    // The prescribed prefill...
    expect(placementOf(placementPatch("separate"))).toBe("separate");
    // ...and a hand-renamed pool, which must not knock the radio off.
    expect(placementOf({
      engine_node_selector: { "bzm-pool": "engine" },
      engine_tolerations: [],
    })).toBe("separate");
  });

  it("states no choice produces read as custom, never as a near miss", () => {
    // Half-set: selector without tolerations stated, and the reverse.
    expect(placementOf({ engine_node_selector: { pool: "x" } })).toBe("custom");
    expect(placementOf({ engine_tolerations: [] })).toBe("custom");
    // Selector emptied while tolerations remain.
    expect(placementOf({
      engine_node_selector: {},
      engine_tolerations: [{ key: "pool", operator: "Exists" }],
    })).toBe("custom");
  });
});

describe("placementPatch", () => {
  it("round-trips through placementOf", () => {
    for (const p of ["crane", "separate", "anywhere"] as const) {
      expect(placementOf(placementPatch(p))).toBe(p);
    }
  });

  it("separate prescribes a matched label/taint pair on one vocabulary", () => {
    const patch = placementPatch("separate");
    expect(patch.engine_node_selector).toEqual({ pool: ENGINE_POOL });
    expect(patch.engine_tolerations).toEqual([
      { key: "pool", operator: "Equal", value: ENGINE_POOL, effect: "NoSchedule" },
    ]);
  });

  it("hands out fresh objects, so editing one choice cannot haunt the next", () => {
    const a = placementPatch("separate");
    (a.engine_node_selector as Record<string, string>).pool = "edited";
    expect(placementPatch("separate").engine_node_selector)
      .toEqual({ pool: ENGINE_POOL });
  });
});

describe("selector rows", () => {
  it("round-trips an object through the table shape", () => {
    const sel = { "bzm-pool": "engine", zone: "a" };
    expect(rowsToSelector(selectorToRows(sel))).toEqual(sel);
  });

  it("a row still missing its key stays out of the option", () => {
    expect(rowsToSelector([{ key: "", value: "engine" },
                           { key: "  ", value: "x" },
                           { key: "pool", value: "engine" }]))
      .toEqual({ pool: "engine" });
  });

  it("reads nothing from a value that is not an object", () => {
    expect(selectorToRows(null)).toEqual([]);
    expect(selectorToRows([{ key: "not", value: "a selector" }])).toEqual([]);
  });
});

describe("toleration rows", () => {
  it("reads only a list, and only its objects", () => {
    expect(tolerationsToRows(null)).toEqual([]);
    expect(tolerationsToRows(["oops", { key: "pool" }])).toEqual([{ key: "pool" }]);
  });

  it("edits by spreading, so fields the editor does not know survive", () => {
    const row = { key: "pool", operator: "Equal", value: "bzm-engines",
                  effect: "NoExecute", tolerationSeconds: 300 };
    const edited = withTolerationField(row, "key", "spot");
    expect(edited.tolerationSeconds).toBe(300);
    expect(edited.key).toBe("spot");
    expect(row.key).toBe("pool");
  });

  it("a blanked field is removed, not sent as empty", () => {
    expect(withTolerationField({ key: "pool", effect: "NoSchedule" }, "effect", ""))
      .toEqual({ key: "pool" });
  });

  it("switching to Exists drops the value Equal was comparing against", () => {
    expect(withTolerationField(
      { key: "pool", operator: "Equal", value: "bzm-engines" }, "operator", "Exists",
    )).toEqual({ key: "pool", operator: "Exists" });
  });

  it("reads a missing or non-string field as blank", () => {
    expect(tolerationField({ key: "pool" }, "value")).toBe("");
    expect(tolerationField({ tolerationSeconds: 300 }, "tolerationSeconds")).toBe("");
  });

  it("a row with nothing typed yet stays out of the option", () => {
    expect(rowsToTolerations([{}, { key: "pool" }])).toEqual([{ key: "pool" }]);
  });
});

describe("customSeed", () => {
  it("starts from crane's value, as a copy", () => {
    const crane = { pool: "infra" };
    const seed = customSeed(crane, {});
    expect(seed).toEqual(crane);
    expect(seed).not.toBe(crane);
  });

  it("starts empty only when crane has nothing to inherit", () => {
    expect(customSeed(null, {})).toEqual({});
    expect(customSeed(undefined, [])).toEqual([]);
  });
});
