// What BlazeMeter requires of a location's `slots` before it will make it.
//
// One entry today, found on a live POST (#159): a location carrying GUI
// Functional is refused at slots=1, which is this form's default, so every one
// the page created was a 400. `core.create_location` refuses it too -- the
// server is where a rule about the account belongs -- and this exists so the
// form can say it *first*, beside the field holding the number, rather than
// only after a write the account threw away.
//
// The table itself is never written here. It arrives from /api/slot-minimums,
// because the minimum was found live and `message` is BlazeMeter's own
// sentence: a second copy in TypeScript is how the rule and what the form says
// about it stop being the same rule. Nothing in this file reaches a route or
// imports React, which is what makes slots.test.ts possible with no DOM.
import { SlotMinimum } from "./api";

/** The rule a declaration has to satisfy, or null if none does.
 *
 *  The strictest where more than one applies -- the account has no second
 *  entry today, and an answer that depended on which box was ticked first
 *  would be a bug waiting for it.
 *
 *  An empty table is "not read yet", never "no rules": the same direction
 *  `optionApplies` takes an empty docker-ignored table. A create the account
 *  then rejects is a worse answer than a form refusing on a guess only if the
 *  form is right, and before the fetch lands it cannot be.
 */
export function slotRule(
  funcIds: string[], minimums: Record<string, SlotMinimum>,
): SlotMinimum | null {
  const rules = funcIds.map((id) => minimums[id]).filter(Boolean);
  return rules.reduce<SlotMinimum | null>(
    (worst, r) => (!worst || r.minimum > worst.minimum ? r : worst), null);
}

/** Why BlazeMeter would refuse this location, or "" if it would not.
 *
 *  Its own words, and only its own: the customer who meets this rule in
 *  BlazeMeter's UI reads that sentence, so the page paraphrasing it would give
 *  the same refusal two spellings. What to do about it is on the field
 *  instead, where the number is.
 *
 *  `slots` is compared with `>=` against a non-finite guard rather than a bare
 *  `<`: NumberInput hands back an emptied field as NaN, and every comparison
 *  with NaN is false, so a blank would have gone to the POST as satisfying a
 *  minimum.
 */
export function slotsBlockedBy(
  funcIds: string[], slots: number, minimums: Record<string, SlotMinimum>,
): string {
  const rule = slotRule(funcIds, minimums);
  if (!rule) return "";
  return Number.isFinite(slots) && slots >= rule.minimum ? "" : rule.message;
}
