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
 *  `filter` matches the workspace, which is the grouping. Matching locations
 *  instead would leave a workspace on screen showing a total its visible rows
 *  do not add up to. */
export function byWorkspace(cap: Capacity, filter: string): WorkspaceRollup[] {
  const q = filter.trim().toLowerCase();
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
    .filter((w) => !q || w.name.toLowerCase().includes(q))
    .sort((a, b) => b.total - a.total);
}
