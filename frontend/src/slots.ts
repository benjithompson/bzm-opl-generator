// What BlazeMeter requires of a location's `slots` before it will create it
// (GUI Functional refuses slots=1), so the form can say it before the write.
// The table is served (/api/slot-minimums); `message` is BlazeMeter's own words.
import { SlotMinimum } from "./api";

/** The rule a declaration has to satisfy, or null: the strictest where several
 *  apply. An empty table is "not read yet" and yields none. */
export function slotRule(
  funcIds: string[], minimums: Record<string, SlotMinimum>,
): SlotMinimum | null {
  const rules = funcIds.map((id) => minimums[id]).filter(Boolean);
  return rules.reduce<SlotMinimum | null>(
    (worst, r) => (!worst || r.minimum > worst.minimum ? r : worst), null);
}

/** Why BlazeMeter would refuse this location, in its own words, or "". A blank
 *  field arrives as NaN, which is treated as below the minimum. */
export function slotsBlockedBy(
  funcIds: string[], slots: number, minimums: Record<string, SlotMinimum>,
): string {
  const rule = slotRule(funcIds, minimums);
  if (!rule) return "";
  return Number.isFinite(slots) && slots >= rule.minimum ? "" : rule.message;
}
