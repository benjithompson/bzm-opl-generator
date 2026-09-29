// The account rollup's arithmetic, kept out of the view so it can be tested.
import { Capacity, CapLocation } from "./api";

export interface WorkspaceRollup {
  id: number;
  name: string;
  locs: CapLocation[];
  /** The subset claimable from another workspace too. */
  shared: CapLocation[];
  total: number;
  sharedVus: number;
}

/** Locations grouped by workspace, largest first, empty workspaces dropped. A
 *  shared location is in each of its workspaces' totals, so those add up to
 *  more than the account's. */
export function byWorkspace(cap: Capacity): WorkspaceRollup[] {
  return cap.workspaces
    .map((w) => {
      const locs = cap.locations
        .filter((l) => l.workspace_ids.includes(w.id))
        .sort((a, b) => (b.rated_vus ?? 0) - (a.rated_vus ?? 0));
      const shared = locs.filter((l) => l.shared);
      return {
        id: w.id,
        name: w.name,
        locs,
        shared,
        total: locs.reduce((t, l) => t + (l.rated_vus ?? 0), 0),
        sharedVus: shared.reduce((t, l) => t + (l.rated_vus ?? 0), 0),
      };
    })
    // An empty workspace is not a row with a zero in it.
    .filter((w) => w.locs.length > 0)
    .sort((a, b) => b.total - a.total);
}

/** The workspaces a search matches, by workspace name so totals stay whole.
 *  Blank matches everything. */
export function matching(rows: WorkspaceRollup[], filter: string) {
  const q = filter.trim().toLowerCase();
  return q ? rows.filter((w) => w.name.toLowerCase().includes(q)) : rows;
}

interface AccountBand {
  /** Workspace id, or one of the two synthetic buckets below. */
  key: string;
  name: string;
  vus: number;
  /** Claimable from more than one workspace, so it belongs to none of them. */
  shared?: boolean;
  /** In no workspace this listing names. */
  orphan?: boolean;
}

/** The account total as segments that add up to it: what only each workspace
 *  can claim, then everything shared (counted once), then anything in no
 *  workspace. */
export function accountBands(cap: Capacity): AccountBand[] {
  const byId = new Map(cap.workspaces.map((w) => [w.id, w.name]));
  const own = new Map<number, number>();
  let shared = 0;
  let orphan = 0;
  for (const l of cap.locations) {
    const vus = l.rated_vus ?? 0;
    if (!vus) continue;
    if (l.shared) { shared += vus; continue; }
    const id = l.workspace_ids[0];
    if (id === undefined || !byId.has(id)) { orphan += vus; continue; }
    own.set(id, (own.get(id) ?? 0) + vus);
  }
  const bands: AccountBand[] = [...own.entries()]
    .map(([id, vus]) => ({ key: String(id), name: byId.get(id)!, vus }))
    .sort((a, b) => b.vus - a.vus);
  if (shared > 0) {
    bands.push({ key: "shared", name: "shared between workspaces",
                 vus: shared, shared: true });
  }
  if (orphan > 0) {
    bands.push({ key: "orphan", name: "in no workspace", vus: orphan,
                 orphan: true });
  }
  return bands;
}
