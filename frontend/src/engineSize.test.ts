import { describe, expect, test } from "vitest";

import { applyCost, cpuCores, memMb, sizeState } from "./engineSize";

// One figure, two writers (#132): the bundle's limits and the location's
// overrideCPU/overrideMemory requests. This module is the comparison and the
// prose; App owns the write. The states matter more than the arithmetic --
// "no location to read" and "the location sets nothing" must never share a
// representation, because only the second deserves a warning.

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

describe("sizeState", () => {
  test("no location is its own state, not a warning", () => {
    // Manual entry, or the list still loading: nothing can be read, so
    // nothing may be said about what the location holds.
    expect(sizeState(null, null, null).kind).toBe("noLocation");
  });

  test("limits mid-edit are unjudged, not diverging", () => {
    expect(sizeState("2x", "8Gi", { overrideCPU: 2, overrideMemory: 8192 })
      .kind).toBe("unjudged");
  });

  test("a location holding nothing is unset, with the matching patch", () => {
    const s = sizeState(null, null,
      { overrideCPU: null, overrideMemory: null });
    if (s.kind !== "unset") throw new Error(s.kind);
    // Unset limits mean the documented default, which is what the bundle now
    // always carries.
    expect(s.patch).toEqual({ override_cpu: 2, override_memory: 8192 });
    expect(s.warning).toContain("250m");
    expect(s.warning).toContain("requests");
    // Plain prose: it renders as text in the panel and Markdown elsewhere.
    expect(s.warning).not.toContain("`");
    expect(s.warning).not.toContain("--");
  });

  test("matching requests are said to match", () => {
    const s = sizeState("2", "8Gi", { overrideCPU: 2, overrideMemory: 8192 });
    if (s.kind !== "match") throw new Error(s.kind);
    expect(s.note).toContain("2 CPU");
    expect(s.note).toContain("8192 MB");
  });

  test("divergence names both sides and allows itself", () => {
    const s = sizeState("2", "8Gi", { overrideCPU: 1, overrideMemory: 4096 });
    if (s.kind !== "diverge") throw new Error(s.kind);
    expect(s.warning).toContain("1 CPU");
    expect(s.warning).toContain("4096 MB");
    expect(s.warning).toContain("2 CPU");
    expect(s.warning).toContain("8Gi");
    // Match is the default, never an invariant: the sentence must leave the
    // divergence standing as a legitimate choice.
    expect(s.warning).toMatch(/legitimate/);
    expect(s.patch).toEqual({ override_cpu: 2, override_memory: 8192 });
    expect(s.warning).not.toContain("`");
    expect(s.warning).not.toContain("--");
  });

  test("half-set requests diverge against the defaulted half", () => {
    const s = sizeState("2", "8Gi", { overrideCPU: 2, overrideMemory: null });
    if (s.kind !== "diverge") throw new Error(s.kind);
    expect(s.warning).toContain("256Mi");
  });

  test("a fractional CPU limit has no expressible CPU request", () => {
    // overrideCPU takes whole cores only; null is "cannot be written",
    // never "write zero".
    const s = sizeState("500m", "1Gi",
      { overrideCPU: null, overrideMemory: null });
    if (s.kind !== "unset") throw new Error(s.kind);
    expect(s.patch).toEqual({ override_cpu: null, override_memory: 1024 });
  });
});

describe("applyCost", () => {
  test("says what is written and that it is an account write", () => {
    const s = sizeState("2", "8Gi", { overrideCPU: null, overrideMemory: null });
    if (s.kind !== "unset") throw new Error(s.kind);
    const cost = applyCost(s);
    expect(cost).toContain("2");
    expect(cost).toContain("8192 MB");
    expect(cost).toMatch(/every agent/);
    expect(cost).toMatch(/every test/);
  });

  test("an unwritable CPU request is named, not skipped in silence", () => {
    const s = sizeState("500m", "1Gi",
      { overrideCPU: null, overrideMemory: null });
    if (s.kind !== "unset") throw new Error(s.kind);
    const cost = applyCost(s);
    expect(cost).toContain("1024 MB");
    expect(cost).toContain("whole cores");
    expect(cost).toContain("500m");
  });
});
