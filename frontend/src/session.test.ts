import { beforeEach, describe, expect, it, vi } from "vitest";
import { clear, load, save, strip, VERSION } from "./session";

// A memory stand-in for sessionStorage; `overrides` makes calls throw.
function fakeStorage(overrides: Partial<Storage> = {}) {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => { data.set(k, v); },
    removeItem: (k: string) => { data.delete(k); },
    ...overrides,
  } as Storage;
}

const BASE = {
  sourceMode: "connect" as const,
  accountId: 7,
  workspaceId: 42,
  harborId: "h1",
  shipId: "s1",
  manual: { harbor_id: "", ship_id: "" },
  // Connected, so nothing is declared.
  declaredFunctionalities: [] as string[],
  // Confirmed, as the ids confirmed.
  confirmed: { loc: "h1", ship: "s1" },
  options: { namespace: "ns1", auth_token: "SECRET-TOKEN" },
  step: 1,
  view: "flow" as const,
  // Which functionalities are sized, and a target in each one's unit.
  plan: { functionalities: ["performance", "functionalGui"],
          targets: { performance: "5000", functionalGui: "20" },
          figures: { performance: "750" } },
  // ...and the saved sizings, defaults included.
  sizings: [{ name: "Black Friday",
              inputs: { functionalities: ["performance"],
                        targets: { performance: "40000" }, figures: {} } }],
};

beforeEach(() => {
  vi.stubGlobal("sessionStorage", fakeStorage());
});

describe("what is remembered", () => {
  it("round-trips the ids and options a refresh would otherwise lose", () => {
    save(BASE);
    const back = load();
    expect(back?.accountId).toBe(7);
    expect(back?.harborId).toBe("h1");
    expect(back?.shipId).toBe("s1");
    expect(back?.step).toBe(1);
    expect(back?.options.namespace).toBe("ns1");
    expect(back?.confirmed).toEqual({ loc: "h1", ship: "s1" });
  });

  it("round-trips what manual entry declared the identity runs", () => {
    // Manual entry's declaration, a list, comes back whole.
    save({ ...BASE, sourceMode: "manual",
           declaredFunctionalities: ["performance", "functionalGui"] });
    expect(load()?.declaredFunctionalities)
      .toEqual(["performance", "functionalGui"]);
  });

  it("returns null when nothing was stored", () => {
    expect(load()).toBeNull();
  });

  it("remembers which view was open, and what was typed into the profile", () => {
    // The sizing's typed targets survive a refresh.
    save({ ...BASE, view: "capacity" });
    const back = load();
    expect(back?.view).toBe("capacity");
    expect(back?.plan.targets.performance).toBe("5000");
    expect(back?.plan.figures.performance).toBe("750");
    expect(back?.plan.functionalities).toEqual(["performance", "functionalGui"]);
    // A saved sizing survives whole.
    expect(back?.sizings?.[0].name).toBe("Black Friday");
    expect(back?.sizings?.[0].inputs.targets.performance).toBe("40000");
    // Every view, the images one included.
    save({ ...BASE, view: "images" });
    expect(load()?.view).toBe("images");
  });

  it("drops a snapshot from a build that shaped it differently", () => {
    sessionStorage.setItem("bzm-opl-gen.session",
                           JSON.stringify({ ...BASE, v: 999 }));
    // Another version is dropped whole rather than half-read.
    expect(load()).toBeNull();
  });

  it("survives a corrupted value", () => {
    sessionStorage.setItem("bzm-opl-gen.session", "{not json");
    expect(load()).toBeNull();
  });

  it("clears", () => {
    save(BASE);
    clear();
    expect(load()).toBeNull();
  });
});

describe("what is never remembered", () => {
  it("keeps the AUTH_TOKEN out of storage entirely", () => {
    save(BASE);
    // The token is never written, not merely dropped on load.
    expect(sessionStorage.getItem("bzm-opl-gen.session"))
      .not.toContain("SECRET-TOKEN");
    expect(load()?.options.auth_token).toBeUndefined();
  });

  it("does not put one back if something else wrote one", () => {
    // Written at the current VERSION, so the snapshot is not dropped for its
    // version and only the token is under test.
    sessionStorage.setItem("bzm-opl-gen.session", JSON.stringify(
      { ...BASE, v: VERSION,
        options: { namespace: "ns1", auth_token: "LEAKED" } }));
    expect(load()?.harborId).toBe("h1");
    expect(load()?.options.auth_token).toBeUndefined();
  });

  it("strips only the credential", () => {
    const out = strip({ namespace: "ns1", auth_token: "t", proxy: { http: "x" } });
    expect(out).toEqual({ namespace: "ns1", proxy: { http: "x" } });
  });
});

describe("a browser that will not store", () => {
  it("treats a throwing storage as no memory rather than an error", () => {
    vi.stubGlobal("sessionStorage", fakeStorage({
      setItem: () => { throw new Error("QuotaExceededError"); },
      getItem: () => { throw new Error("SecurityError"); },
    }));
    expect(() => save(BASE)).not.toThrow();
    expect(load()).toBeNull();
    expect(() => clear()).not.toThrow();
  });
});
