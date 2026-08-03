import { beforeEach, describe, expect, it, vi } from "vitest";
import { clear, load, save, strip, VERSION } from "./session";

// A memory stand-in for sessionStorage: these tests are about what is written,
// not about a browser, and the module deliberately guards every call so that a
// storage that throws degrades to no memory rather than a broken page.
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
  // Confirmed, and of *these* ids: step 1 is finished when somebody has said
  // so, and a refresh is not a reason to ask again.
  confirmed: { loc: "h1", ship: "s1" },
  options: { namespace: "ns1", auth_token: "SECRET-TOKEN" },
  step: 1,
  view: "flow" as const,
  // The two figures the capacity profile owns. Its engine size is a bundle
  // option and is remembered with the rest of them.
  plan: { users: "5000", vusPerEngine: "750" },
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

  it("returns null when nothing was stored", () => {
    expect(load()).toBeNull();
  });

  it("remembers which view was open, and what was typed into the profile", () => {
    // A refresh while sizing a run must not come back to an empty target: the
    // profile is the first thing on step 1 and nothing else can recover what
    // was typed into it.
    save({ ...BASE, view: "capacity" });
    const back = load();
    expect(back?.view).toBe("capacity");
    expect(back?.plan.users).toBe("5000");
    expect(back?.plan.vusPerEngine).toBe("750");
  });

  it("drops a snapshot from a build that shaped it differently", () => {
    sessionStorage.setItem("bzm-opl-gen.session",
                           JSON.stringify({ ...BASE, v: 999 }));
    // Half-reading it would leave other code believing ids it does not
    // understand; starting over is the cheaper wrong answer.
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
    // Not "load() drops it" -- the point is that it was never written. The page
    // promises the token is held for this browser session and that nothing
    // writes it down, and sessionStorage is a file in the browser's profile.
    expect(sessionStorage.getItem("bzm-opl-gen.session"))
      .not.toContain("SECRET-TOKEN");
    expect(load()?.options.auth_token).toBeUndefined();
  });

  it("does not put one back if something else wrote one", () => {
    // Written at the *current* version on purpose: at an old one this would
    // pass because the whole snapshot was dropped, which is not the property
    // being asserted. Hence VERSION rather than a literal — as a literal it
    // silently became that weaker test the first time the version moved.
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
