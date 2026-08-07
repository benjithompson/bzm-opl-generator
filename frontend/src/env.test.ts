import { describe, expect, it } from "vitest";
import {
  boolChoice, boolWrite, envIncomplete, envRowError, envToRows, jsonToKv,
  kvToJson, offeredVars, otherRows, rowsToEnv, setVar, varError, varSet,
  varValue,
} from "./env";
// The served tables, from the one copy of each -- RESERVED_ENV held equal to
// generate.RESERVED_ENV by tests/test_server.py, AGENT_ENV a sample rather than
// a copy (see fixtures.ts: nothing on the page has to agree with it).
import { AGENT_ENV, RESERVED_ENV } from "./fixtures";

describe("rows and the option", () => {
  it("round-trips what a bundle carries", () => {
    const env = { PREFERRED_INTERFACE: "eth1", DODUO_PORT: "8080" };
    expect(rowsToEnv(envToRows(env))).toEqual(env);
  });

  it("reads a value of any scalar shape as the text it will be", () => {
    // An imported profile is JSON, and nothing stops it carrying a number the
    // generator would stringify. A row showing [object Object] is the failure
    // this is about; a row showing 8080 is the value.
    expect(envToRows({ A: 8080, B: true, C: null }))
      .toEqual([{ name: "A", value: "8080" }, { name: "B", value: "true" },
                { name: "C", value: "" }]);
  });

  it("answers nothing for an option that is not a map", () => {
    // Absent, and the shapes a hand-edited profile can hold. None of them is a
    // reason to throw inside a render.
    for (const bad of [null, undefined, [], "PREFERRED_INTERFACE=eth1", 7]) {
      expect(envToRows(bad)).toEqual([]);
    }
  });

  it("keeps a row still being typed out of the option", () => {
    // Same rule as a selector row without its key (sched.ts): otherwise every
    // keystroke in a blank row re-POSTs the preview.
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
    // A ConfigMap key may hold dots and dashes and the bundle would apply
    // cleanly; the variable simply would not exist for the agent.
    for (const bad of ["my-var", "9lives", "a.b", "A B"]) {
      expect(envRowError(rows(bad), 0, {})).toMatch(/letters, digits/);
    }
  });

  it("says nothing about a row with no name yet", () => {
    expect(envRowError(rows(""), 0, {})).toBe("");
  });

  it("names the option that already writes the variable", () => {
    // The whole answer is "set it there", so the sentence carries it.
    expect(envRowError(rows("KUBERNETES_SERVICE_USE_TYPE"), 0, RESERVED_ENV))
      .toContain("service_type");
    // ...and where no option owns it, it says so rather than inventing one.
    const identity = envRowError(rows("SHIP_ID"), 0, RESERVED_ENV);
    expect(identity).toContain("SHIP_ID");
    expect(identity).not.toContain("null");
  });

  it("refuses nothing while the table has not been read", () => {
    // Empty is "not read yet", and it means everything is allowed -- the same
    // direction as an unread ignored-options table. generate() refuses
    // authoritatively either way, and a row rejected on a guess is the worse
    // half of being wrong.
    expect(envRowError(rows("KUBERNETES_SERVICE_USE_TYPE"), 0, {})).toBe("");
  });

  it("catches the second of two rows with one name", () => {
    // They collapse into one key on the way to the option, so without this the
    // second silently replaces the first and neither row says anything.
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
    // Deliberately not blocked here: a group's `incomplete` is handed the
    // options and nothing else, so it cannot see the served table -- and the
    // refusal arrives anyway, in the sentence naming the owning option.
    expect(envIncomplete({ extra_env: { SHIP_ID: "x" } })).toBe(false);
  });
});

describe("the offered variables", () => {
  it("shows one side of BlazeMeter's two tables", () => {
    // Two tables, not one: the KUBERNETES_* half is crane's and the rest are
    // the container agent's, so offering all of them would offer a setting the
    // agent under this bundle has no reader for.
    //
    // The docker side of the fixture has no row of its own, and that is a fact
    // rather than a gap: every variable in BlazeMeter's Docker-only column is
    // written off an option now -- HOSTNAME_OVERRIDE, TLS_CERT and TLS_KEY
    // since #182 -- so all of them are reserved and none is served. What the
    // predicate is exercised on is the kubernetes-only row, in both directions.
    expect(offeredVars(AGENT_ENV, true).map((v) => v.name))
      .toEqual(["PREFERRED_INTERFACE", "VERIFY_SSL", "DODUO_PORT",
                "KUBERNETES_LABELS", "KUBERNETES_USE_APIPA"]);
    expect(offeredVars(AGENT_ENV, false).map((v) => v.name))
      .toEqual(["PREFERRED_INTERFACE", "VERIFY_SSL", "DODUO_PORT"]);
  });

  it("offers nothing before the list has landed", () => {
    // Which is not a claim that there is nothing: the area falls back to naming
    // a variable by hand, so an unread list costs a control, never a value.
    expect(offeredVars([], true)).toEqual([]);
  });
});

describe("writing one variable", () => {
  const env = { A: "1", B: "2" };

  it("keeps the others, and its own place", () => {
    // A variable that jumped to the end of the ConfigMap every time it was
    // edited would make the preview churn for a value that had not changed.
    expect(setVar(env, "A", "9")).toEqual({ A: "9", B: "2" });
    expect(setVar(env, "C", "3")).toEqual({ A: "1", B: "2", C: "3" });
  });

  it("clears with null, and gives back the option's own default when empty", () => {
    expect(setVar(env, "A", null)).toEqual({ B: "2" });
    // `null`, not `{}`: an empty object would show up in profile.json as a key
    // a bundle generated without this area never had.
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
    // The distinction this whole codebase keeps: "nobody said" is not "no".
    expect(boolChoice({}, "VERIFY_SSL")).toBe("default");
    expect(boolChoice({ VERIFY_SSL: "true" }, "VERIFY_SSL")).toBe("true");
    expect(boolChoice({ VERIFY_SSL: "false" }, "VERIFY_SSL")).toBe("false");
  });

  it("reads a value it did not write as off rather than as unset", () => {
    // An imported profile can carry `1`, `yes` or a typo. It is set -- saying
    // otherwise would hide a variable the bundle carries -- and it is not the
    // word the agent reads as true.
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
    // The rule this codebase is built on, in the one place a table could
    // quietly offer to save `{}` over a value it had merely failed to parse.
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
    // The half that makes the catalogue a list rather than a filter: the served
    // vocabulary can lose a name, and a form showing nothing for a variable the
    // bundle is about to write is the failure these rules are about.
    expect(otherRows({ A: "1", VERIFY_SSL: "false" }, ["VERIFY_SSL"]))
      .toEqual([{ name: "A", value: "1" }]);
    expect(otherRows({ VERIFY_SSL: "false" }, ["VERIFY_SSL"])).toEqual([]);
  });

  it("keeps one the location's own catalogue leaves out", () => {
    // #150 gave the served list a second reason to be short: it is scoped to
    // what the location runs, so a performance location is served no
    // DODUO_PORT. That is a filter on what is *offered* and never on what is
    // carried -- a profile written for a GUI location, or a location changed
    // after the form was filled in, still has the value, and the bundle still
    // writes it. Hiding it would be a form denying a variable the ConfigMap
    // has.
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
