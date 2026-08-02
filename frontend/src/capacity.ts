// Grouping the account's locations by workspace, away from the view that draws
// them -- the arithmetic here is the part worth arguing about, and a component
// is not where an argument can be tested.
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

/** A location in more than one workspace is claimable from either, so adding it
 *  into both totals counts engines that cannot run twice. It is flagged in each,
 *  and counted once in the account figure -- which is why the workspace numbers
 *  add up to more than the account's.
 *
 *  Grouping only. Filtering is `matching` below, so the view can group once per
 *  account and filter that -- this walks every location for every workspace,
 *  which on a real account is 171 x 166 and was being redone per keystroke. */
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
    // A workspace holding no location is not a row with a zero in it: the
    // account has ~100 of them and they said "100 workspaces" about the 54
    // that carry anything.
    .filter((w) => w.locs.length > 0)
    .sort((a, b) => b.total - a.total);
}

/** The rows a search matches.
 *
 *  On the *workspace*, which is the grouping. Matching locations instead would
 *  leave a workspace on screen showing a total its visible rows do not add up
 *  to. Blank matches everything -- a search nobody has typed is not a search
 *  that excludes everything. */
export function matching(rows: WorkspaceRollup[], filter: string) {
  const q = filter.trim().toLowerCase();
  return q ? rows.filter((w) => w.name.toLowerCase().includes(q)) : rows;
}

export interface AccountBand {
  /** Workspace id, or one of the two synthetic buckets below. */
  key: string;
  name: string;
  vus: number;
  /** Claimable from more than one workspace, so it belongs to none of them. */
  shared?: boolean;
  /** In no workspace this listing names. */
  orphan?: boolean;
}

/** The account's total, split into segments that add up to it.
 *
 *  Not the workspace totals: those double-count, because a shared location is
 *  claimable from either workspace and appears in both. Adding them into a
 *  single bar would draw more capacity than the account has, which is the one
 *  thing the account figure is there to avoid. So each workspace's segment is
 *  what only it can claim, and everything shared is one segment of its own --
 *  counted once, like the account total counts it.
 *
 *  A location in no workspace at all gets a segment too rather than being
 *  dropped: it is capacity the account has, and a bar quietly shorter than its
 *  own headline is worse than an awkward segment. */
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
