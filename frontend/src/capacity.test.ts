import { describe, expect, it } from "vitest";
import { Capacity, CapLocation } from "./api";
import { accountBands, byWorkspace } from "./capacity";

// How the account's locations become workspace rows. The figures themselves are
// core.account_capacity's and are tested in tests/test_capacity.py; what is
// decided here is which workspace a location lands in, in what order, and what
// the filter does to the totals beside it.

const loc = (over: Partial<CapLocation> = {}): CapLocation => ({
  id: "l1", name: "one", func_ids: ["taurus"], agents: 1,
  agents_reporting: 1, agents_unknown: 0, slots: 2, threads_per_engine: 50,
  engines: 2, rated_vus: 100, workspace_ids: [1], workspace_names: ["A"],
  shared: false, ...over,
});

const cap = (locations: CapLocation[], workspaces = [
  { id: 1, name: "Alpha" }, { id: 2, name: "Beta" },
]): Capacity => ({
  account_id: 1, workspaces, locations,
  rated_vus: locations.reduce((t, l) => t + (l.rated_vus ?? 0), 0),
  unrated: locations.filter((l) => l.rated_vus === null).length,
});

describe("byWorkspace", () => {
  it("puts a location in every workspace that can claim it", () => {
    const shared = loc({ id: "s", rated_vus: 300, shared: true,
      workspace_ids: [1, 2], workspace_names: ["Alpha", "Beta"] });
    const rows = byWorkspace(cap([shared]), "");
    expect(rows.map((w) => w.name)).toEqual(["Alpha", "Beta"]);
    // Counted in both, which is why the two totals exceed the account's 300.
    expect(rows.map((w) => w.total)).toEqual([300, 300]);
    expect(rows.every((w) => w.shared.length === 1)).toBe(true);
  });

  it("drops a workspace holding no location rather than showing a zero", () => {
    const rows = byWorkspace(cap([loc({ workspace_ids: [1] })]), "");
    expect(rows.map((w) => w.name)).toEqual(["Alpha"]);
  });

  it("orders workspaces and their locations by capacity", () => {
    const rows = byWorkspace(cap([
      loc({ id: "a", rated_vus: 10, workspace_ids: [1] }),
      loc({ id: "b", rated_vus: 90, workspace_ids: [1] }),
      loc({ id: "c", rated_vus: 500, workspace_ids: [2] }),
    ]), "");
    expect(rows.map((w) => w.name)).toEqual(["Beta", "Alpha"]);
    expect(rows[1].locs.map((l) => l.id)).toEqual(["b", "a"]);
  });

  it("treats an unrated location as nothing to add, not as a hole", () => {
    const rows = byWorkspace(cap([
      loc({ id: "a", rated_vus: null, slots: null, workspace_ids: [1] }),
      loc({ id: "b", rated_vus: 40, workspace_ids: [1] }),
    ]), "");
    expect(rows[0].total).toBe(40);
    expect(rows[0].locs).toHaveLength(2);
  });

  it("counts shared VUs apart from the workspace total", () => {
    const rows = byWorkspace(cap([
      loc({ id: "a", rated_vus: 100, workspace_ids: [1] }),
      loc({ id: "s", rated_vus: 250, shared: true, workspace_ids: [1, 2] }),
    ]), "");
    const alpha = rows.find((w) => w.name === "Alpha")!;
    expect(alpha.total).toBe(350);
    expect(alpha.sharedVus).toBe(250);
  });

  it("reports a shared location with no agents as shared and worth nothing", () => {
    // The case that produced "0 of 2,650 is claimable" on the real account: a
    // location shared with another workspace that has never been deployed to.
    const rows = byWorkspace(cap([
      loc({ id: "a", rated_vus: 500, workspace_ids: [1] }),
      loc({ id: "s", agents: 0, agents_reporting: 0, engines: 0, rated_vus: 0,
        shared: true, workspace_ids: [1, 2] }),
    ]), "");
    const alpha = rows.find((w) => w.name === "Alpha")!;
    expect(alpha.shared).toHaveLength(1);
    expect(alpha.sharedVus).toBe(0);
  });

  it("filters on the workspace, so a row's total still matches its rows", () => {
    const rows = byWorkspace(cap([
      loc({ id: "a", name: "beta-ish", rated_vus: 100, workspace_ids: [1] }),
      loc({ id: "b", rated_vus: 40, workspace_ids: [2] }),
    ]), "beta");
    expect(rows.map((w) => w.name)).toEqual(["Beta"]);
    // The location *named* beta-ish lives in Alpha and does not drag it in.
    expect(rows[0].locs.map((l) => l.id)).toEqual(["b"]);
  });

  it("ignores case and surrounding space in the filter", () => {
    expect(byWorkspace(cap([loc()]), "  ALPHA ")).toHaveLength(1);
    expect(byWorkspace(cap([loc()]), "   ")).toHaveLength(1);
    expect(byWorkspace(cap([loc()]), "gamma")).toHaveLength(0);
  });
});

describe("accountBands", () => {
  // The property the whole bar rests on: it is drawn as a share of the account
  // total, so segments that summed to anything else would draw an account
  // bigger or smaller than its own headline.
  const sums = (c: Capacity) =>
    accountBands(c).reduce((t, b) => t + b.vus, 0);

  it("adds up to the account total", () => {
    const c = cap([
      loc({ id: "a", rated_vus: 100, workspace_ids: [1] }),
      loc({ id: "b", rated_vus: 40, workspace_ids: [2] }),
    ]);
    expect(sums(c)).toBe(c.rated_vus);
  });

  it("counts a shared location once, in its own segment", () => {
    // Both workspaces can claim it, so neither owns it -- adding it to both
    // would draw 400 of capacity out of an account that has 250.
    const c: Capacity = {
      account_id: 1,
      workspaces: [{ id: 1, name: "Alpha" }, { id: 2, name: "Beta" }],
      locations: [
        loc({ id: "a", rated_vus: 100, workspace_ids: [1] }),
        loc({ id: "s", rated_vus: 150, shared: true, workspace_ids: [1, 2] }),
      ],
      rated_vus: 250, unrated: 0,
    };
    const bands = accountBands(c);
    expect(bands.map((b) => [b.name, b.vus])).toEqual([
      ["Alpha", 100], ["shared between workspaces", 150],
    ]);
    expect(bands.find((b) => b.shared)?.shared).toBe(true);
    expect(sums(c)).toBe(250);
  });

  it("keeps capacity that is in no workspace rather than dropping it", () => {
    // A bar quietly shorter than the headline above it is worse than an
    // awkward segment.
    const c: Capacity = {
      account_id: 1, workspaces: [{ id: 1, name: "Alpha" }],
      locations: [
        loc({ id: "a", rated_vus: 60, workspace_ids: [1] }),
        loc({ id: "x", rated_vus: 40, workspace_ids: [], workspace_names: [] }),
        // In a workspace the listing does not name, which is the same
        // situation from the other side.
        loc({ id: "y", rated_vus: 10, workspace_ids: [99] }),
      ],
      rated_vus: 110, unrated: 0,
    };
    const bands = accountBands(c);
    expect(bands.find((b) => b.orphan)?.vus).toBe(50);
    expect(sums(c)).toBe(110);
  });

  it("orders by size and skips workspaces with nothing to draw", () => {
    const c = cap([
      loc({ id: "a", rated_vus: 10, workspace_ids: [1] }),
      loc({ id: "b", rated_vus: 90, workspace_ids: [2] }),
      loc({ id: "c", rated_vus: null, workspace_ids: [1] }),
    ]);
    expect(accountBands(c).map((b) => b.name)).toEqual(["Beta", "Alpha"]);
  });

  it("draws nothing at all for an account with no rated capacity", () => {
    expect(accountBands(cap([loc({ rated_vus: null })]))).toEqual([]);
  });
});
