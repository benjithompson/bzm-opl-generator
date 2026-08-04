import { describe, expect, test } from "vitest";

import { cpuCores, memMb, sizeStatement } from "./engineSize";

// The engine size is one figure and the location is where it is set (#132):
// generate derives the bundle's limits from the location's overrideCPU /
// overrideMemory when no explicit option names them. The configure step no
// longer edits the size -- this module is the read-only statement it renders
// instead: what the bundle will carry, where that came from, and where to
// change it. The states matter more than the arithmetic: "no location to
// read" and "the location sets nothing" must never share a representation.

describe("cpuCores", () => {
  test("whole cores and millicores", () => {
    expect(cpuCores("2")).toBe(2);
    expect(cpuCores("500m")).toBe(0.5);
    expect(cpuCores("1.5")).toBe(1.5);
  });
  test("unparseable is null, never zero", () => {
    expect(cpuCores("")).toBeNull();
    expect(cpuCores("two")).toBeNull();
    expect(cpuCores("2Gi")).toBeNull();
  });
});

describe("memMb", () => {
  test("Gi and Mi in MB", () => {
    expect(memMb("8Gi")).toBe(8192);
    expect(memMb("512Mi")).toBe(512);
    expect(memMb("4Gi")).toBe(4096);
  });
  test("unparseable is null, never zero", () => {
    expect(memMb("")).toBeNull();
    expect(memMb("8 gigs")).toBeNull();
    // A bare number is bytes to Kubernetes and almost never what was meant;
    // refusing to guess beats comparing against the wrong unit.
    expect(memMb("8192")).toBeNull();
  });
});

describe("sizeStatement", () => {
  test("a location's requests become the bundle's size", () => {
    // The derivation the generator applies, restated for the screen: 4096 MB
    // reads as Mi and lands on the Gi form, the same as format_memory.
    const s = sizeStatement(null, null, { overrideCPU: 1, overrideMemory: 4096 });
    expect(s.kind).toBe("location");
    expect(s.cpu).toBe("1");
    expect(s.mem).toBe("4Gi");
    expect(s.text).toContain("1 CPU / 4Gi");
    expect(s.text).toContain("Location settings");
    // Plain prose: no backticks, no double dash.
    expect(s.text).not.toContain("`");
    expect(s.text).not.toContain("--");
  });

  test("an odd MB value stays in Mi rather than being rounded", () => {
    const s = sizeStatement(null, null,
      { overrideCPU: null, overrideMemory: 8196 });
    expect(s.mem).toBe("8196Mi");
    // The unset half falls to its own default.
    expect(s.cpu).toBe("2");
    expect(s.kind).toBe("location");
  });

  test("a location read and holding nothing is the default, said so", () => {
    const s = sizeStatement(null, null,
      { overrideCPU: null, overrideMemory: null });
    expect(s.kind).toBe("default");
    expect(s.text).toContain("2 CPU / 8Gi");
    expect(s.text).toContain("default");
    // ...and it names where to change it, because that is the whole point of
    // stating it: the location is the one place the size is set.
    expect(s.text).toContain("Location settings");
    expect(s.text).not.toContain("--");
  });

  test("no location to read is its own state, not the default's wording", () => {
    // Manual entry, or the list still loading: nothing may claim the
    // location sets nothing, because nobody could read it.
    const s = sizeStatement(null, null, null);
    expect(s.kind).toBe("noLocation");
    expect(s.cpu).toBe("2");
    expect(s.text).not.toContain("sets no engine requests");
  });

  test("explicit options are the bundle's own size and outrank the location",
    () => {
      const s = sizeStatement("2", "8Gi",
        { overrideCPU: 1, overrideMemory: 4096 });
      expect(s.kind).toBe("override");
      expect(s.cpu).toBe("2");
      expect(s.mem).toBe("8Gi");
      // Both sides named: what the bundle carries and what the location asks.
      expect(s.text).toContain("2 CPU / 8Gi");
      expect(s.text).toContain("1 CPU / 4Gi");
      expect(s.text).toContain("overrides");
      expect(s.text).not.toContain("--");
    });

  test("options matching the location are not an override", () => {
    const s = sizeStatement("1", "4Gi",
      { overrideCPU: 1, overrideMemory: 4096 });
    expect(s.kind).toBe("bundle");
    expect(s.text).toContain("match");
  });

  test("options against a location holding nothing say what that costs", () => {
    const s = sizeStatement("4", "16Gi",
      { overrideCPU: null, overrideMemory: null });
    expect(s.kind).toBe("bundle");
    expect(s.text).toContain("4 CPU / 16Gi");
    // The packing gap: requests stay at their default while limits are set.
    expect(s.text).toContain("no engine requests");
  });

  test("options with nothing to read stay a plain statement", () => {
    const s = sizeStatement("4", "16Gi", null);
    expect(s.kind).toBe("bundle");
    expect(s.text).not.toContain("no engine requests");
  });
});
