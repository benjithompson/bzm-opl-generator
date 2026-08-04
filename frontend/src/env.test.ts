import { describe, expect, it } from "vitest";
import { envIncomplete, envRowError, envToRows, rowsToEnv } from "./env";
// The served table, from the one copy of it -- held equal to
// generate.RESERVED_ENV by tests/test_server.py.
import { RESERVED_ENV } from "./fixtures";

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
    // direction as an empty docker-ignored table. generate() refuses
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
