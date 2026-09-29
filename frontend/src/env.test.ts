import { describe, expect, it } from "vitest";
import {
  boolChoice, boolWrite, envIncomplete, envRowError, envToRows, jsonToKv,
  kvToJson, offeredVars, otherRows, rowsToEnv, setVar, varError, varSet,
  varValue,
} from "./env";
// RESERVED_ENV is the served table's one copy; AGENT_ENV is a sample.
import { AGENT_ENV, RESERVED_ENV } from "./fixtures";

describe("rows and the option", () => {
  it("round-trips what a bundle carries", () => {
    const env = { PREFERRED_INTERFACE: "eth1", DODUO_PORT: "8080" };
    expect(rowsToEnv(envToRows(env))).toEqual(env);
  });

  it("reads a value of any scalar shape as the text it will be", () => {
    // A profile can carry non-strings; the row shows the value.
    expect(envToRows({ A: 8080, B: true, C: null }))
      .toEqual([{ name: "A", value: "8080" }, { name: "B", value: "true" },
                { name: "C", value: "" }]);
  });

  it("answers nothing for an option that is not a map", () => {
    // Absent or malformed never throws.
    for (const bad of [null, undefined, [], "PREFERRED_INTERFACE=eth1", 7]) {
      expect(envToRows(bad)).toEqual([]);
    }
  });

  it("keeps a row still being typed out of the option", () => {
    // A nameless row stays out, so typing in it does not re-POST the preview.
    expect(rowsToEnv([{ name: "", value: "eth1" },
                      { name: " A ", value: "1" }])).toEqual({ A: "1" });
  });
});

describe("what a row refuses", () => {
  const rows = (...names: string[]) => names.map((name) => ({ name, value: "x" }));

  it("accepts a name a process could read", () => {
    expect(envRowError(rows("PREFERRED_INTERFACE"), 0, {})).toBe("");
    expect(envRowError(rows("_x9"), 0, {})).toBe("");
  });

  it("refuses a name no process could read", () => {
    // Would apply cleanly and reach no process.
    for (const bad of ["my-var", "9lives", "a.b", "A B"]) {
      expect(envRowError(rows(bad), 0, {})).toMatch(/letters, digits/);
    }
  });

  it("says nothing about a row with no name yet", () => {
    expect(envRowError(rows(""), 0, {})).toBe("");
  });

  it("names the option that already writes the variable", () => {
    // Names the owning option.
    expect(envRowError(rows("KUBERNETES_SERVICE_USE_TYPE"), 0, RESERVED_ENV))
      .toContain("service_type");
    // ...or says no option owns it.
    const identity = envRowError(rows("SHIP_ID"), 0, RESERVED_ENV);
    expect(identity).toContain("SHIP_ID");
    expect(identity).not.toContain("null");
  });

  it("refuses nothing while the table has not been read", () => {
    // Empty is "not read yet": nothing is refused.
    expect(envRowError(rows("KUBERNETES_SERVICE_USE_TYPE"), 0, {})).toBe("");
  });

  it("catches the second of two rows with one name", () => {
    // Two rows of one name would collapse into one key.
    const two = rows("A", "A");
    expect(envRowError(two, 0, {})).toBe("");
    expect(envRowError(two, 1, {})).toBe("already set above");
  });
});

describe("what blocks the download", () => {
  it("blocks on a name that reached the option malformed", () => {
    expect(envIncomplete({ extra_env: { "my-var": "x" } })).toBe(true);
  });

  it("does not block on an empty or absent area", () => {
    expect(envIncomplete({})).toBe(false);
    expect(envIncomplete({ extra_env: {} })).toBe(false);
  });

  it("leaves a reserved name to generate() and to the row", () => {
    // Reserved names are caught per row, not here.
    expect(envIncomplete({ extra_env: { SHIP_ID: "x" } })).toBe(false);
  });
});

describe("the offered variables", () => {
  it("shows one side of BlazeMeter's two tables", () => {
    // One platform's table at a time. Every docker-only variable is reserved
    // now, so the Kubernetes-only row is what exercises the filter.
    expect(offeredVars(AGENT_ENV, true).map((v) => v.name))
      .toEqual(["PREFERRED_INTERFACE", "VERIFY_SSL", "DODUO_PORT",
                "KUBERNETES_LABELS", "KUBERNETES_USE_APIPA"]);
    expect(offeredVars(AGENT_ENV, false).map((v) => v.name))
      .toEqual(["PREFERRED_INTERFACE", "VERIFY_SSL", "DODUO_PORT"]);
  });

  it("offers nothing before the list has landed", () => {
    // Unread offers nothing; a variable can still be named by hand.
    expect(offeredVars([], true)).toEqual([]);
  });
});

describe("writing one variable", () => {
  const env = { A: "1", B: "2" };

  it("keeps the others, and its own place", () => {
    // An edited variable keeps its position.
    expect(setVar(env, "A", "9")).toEqual({ A: "9", B: "2" });
    expect(setVar(env, "C", "3")).toEqual({ A: "1", B: "2", C: "3" });
  });

  it("clears with null, and gives back the option's own default when empty", () => {
    expect(setVar(env, "A", null)).toEqual({ B: "2" });
    // Null, not `{}`, when nothing is left.
    expect(setVar({ A: "1" }, "A", null)).toBe(null);
  });

  it("says whether a variable is set apart from what it is set to", () => {
    expect(varSet({ A: "" }, "A")).toBe(true);
    expect(varValue({ A: "" }, "A")).toBe("");
    expect(varSet({}, "A")).toBe(false);
  });
});

describe("a boolean's three answers", () => {
  it("reads unset as the agent's default rather than as off", () => {
    // "Nobody said" is not "no".
    expect(boolChoice({}, "VERIFY_SSL")).toBe("default");
    expect(boolChoice({ VERIFY_SSL: "true" }, "VERIFY_SSL")).toBe("true");
    expect(boolChoice({ VERIFY_SSL: "false" }, "VERIFY_SSL")).toBe("false");
  });

  it("reads a value it did not write as off rather than as unset", () => {
    // Set to something other than "true": set, and off.
    expect(boolChoice({ VERIFY_SSL: "yes" }, "VERIFY_SSL")).toBe("false");
    expect(boolChoice({ VERIFY_SSL: "TRUE" }, "VERIFY_SSL")).toBe("true");
  });

  it("writes the lower-case word, or nothing at all", () => {
    expect(boolWrite("default")).toBe(null);
    expect(boolWrite("true")).toBe("true");
    expect(boolWrite("false")).toBe("false");
  });
});

describe("a JSON-object variable as a table", () => {
  it("round-trips an object of strings", () => {
    expect(jsonToKv('{"team":"perf"}')).toEqual([{ key: "team", value: "perf" }]);
    expect(kvToJson([{ key: "team", value: "perf" }])).toBe('{"team":"perf"}');
  });

  it("tells an empty value from one it could not read", () => {
    // Unreadable JSON is null, never an empty table.
    expect(jsonToKv("")).toEqual([]);
    expect(jsonToKv("   ")).toEqual([]);
    for (const bad of ["not json", "[1,2]", '{"a":{"b":1}}', '"a"']) {
      expect(jsonToKv(bad)).toBe(null);
    }
  });

  it("clears the variable rather than writing an empty object", () => {
    expect(kvToJson([])).toBe(null);
    expect(kvToJson([{ key: "", value: "x" }])).toBe(null);
  });
});

describe("what has no control above it", () => {
  it("keeps a variable the list does not carry", () => {
    // A set variable with no row above keeps the name/value editor.
    expect(otherRows({ A: "1", VERIFY_SSL: "false" }, ["VERIFY_SSL"]))
      .toEqual([{ name: "A", value: "1" }]);
    expect(otherRows({ VERIFY_SSL: "false" }, ["VERIFY_SSL"])).toEqual([]);
  });

  it("keeps one the location's own catalogue leaves out", () => {
    // Scoping narrows what is offered, never what is carried.
    const scoped = AGENT_ENV.filter((v) => !v.functionalities.length);
    expect(scoped.map((v) => v.name)).not.toContain("DODUO_PORT");
    expect(otherRows({ DODUO_PORT: "8080" }, scoped.map((v) => v.name)))
      .toEqual([{ name: "DODUO_PORT", value: "8080" }]);
  });
});

describe("what a typed value refuses", () => {
  const int = AGENT_ENV.find((v) => v.type === "int")!;

  it("refuses what a whole number is not, and keeps it on screen", () => {
    expect(varError(int, "8O00")).toMatch(/whole number/);
    expect(varError(int, "8080")).toBe("");
    expect(varError(int, "")).toBe("");
  });
});
