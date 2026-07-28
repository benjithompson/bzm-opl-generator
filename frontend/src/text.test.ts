import { describe, expect, it } from "vitest";
import { counted, plural } from "./text";

// The pair exists because one of them used to be asked to do both jobs:
// `plural(n, "passed", "passed")` reads as a pluralisation and is not one.
describe("plural", () => {
  it("inflects with a default -s so regular words are not spelled twice", () => {
    expect(plural(1, "warning")).toBe("1 warning");
    expect(plural(2, "warning")).toBe("2 warnings");
  });

  it("takes an explicit plural for words the default gets wrong", () => {
    expect(plural(2, "entry", "entries")).toBe("2 entries");
  });

  it("inflects on zero, which reads as a plural in English", () => {
    expect(plural(0, "warning")).toBe("0 warnings");
  });
});

describe("counted", () => {
  it("leaves a word that does not inflect alone at every count", () => {
    expect(counted(1, "passed")).toBe("1 passed");
    expect(counted(2, "passed")).toBe("2 passed");
    expect(counted(0, "to apply")).toBe("0 to apply");
  });
});
