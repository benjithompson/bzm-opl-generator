// Acting on what a cluster's evidence implies: what each suggestion offers the
// user, what clicking it writes, and how to take it back.
//
// Nothing here decides what the evidence means. The suggestions are suggest.py's
// (DECISIVE settles it, SUGGESTIVE narrows it), and how each one stands against
// the configuration -- SETTLED, FILL, CHOOSE, CONFLICT -- is suggest.merge()'s,
// served on the row. What this file owns is the third thing: what a person may
// click, which is the only place the constraint can be kept.
//
// The constraint: a value somebody set is never overwritten without being shown.
// Every applying action here is one the user takes, on a row showing both the
// value it would write and the one it would replace -- and a suggestive
// suggestion has no single value to write at all, so it can only ever be a
// shortlist to pick from. Narrowing to one is still not choosing.
//
// Plain data in, plain data out like optionGroups.ts and preflight.ts, which is
// what makes suggestions.test.ts possible without a DOM.

import { Options, Strength, Suggestion } from "./api";
import { OptionPatch } from "./optionGroups";
import { counted, plural } from "./text";

/** How the two strengths read. Colour is not the distinction -- the label is,
 *  for the same reason the verdict badges carry one. */
export const STRENGTH_STYLE: Record<
    Strength, { label: string; badge: string; hint: string }> = {
  DECISIVE: { label: "DECISIVE", badge: "bg-sky-100 text-sky-800",
              hint: "the evidence settles this one" },
  SUGGESTIVE: { label: "SUGGESTIVE", badge: "bg-slate-200 text-slate-700",
                hint: "narrowed, not decided — pick one" },
};

/** A value and the string it was served as. The two travel together wherever a
 *  value is both written and shown: `value` is what applying puts in the
 *  options, `shown` is suggest.shown's rendering of it, and nothing here
 *  derives the second from the first. */
export interface Shown { value: unknown; shown: string }

/** What a row lets you do.
 *
 *  `apply`   a decisive suggestion for an option nobody moved: one click, and
 *            it replaces only what the generator would have used anyway.
 *  `replace` the same click over a value somebody chose. A different word
 *            because it is a different act, and the row shows both values.
 *  `choose`  a shortlist. Never carries a value: the pick is the user's, at one
 *            candidate as much as at three.
 *  `blocked` applying would need another option cleared first -- the row says
 *            which, in generate's words.
 *  `none`    nothing to do -- already configured this way, or the evidence
 *            ruled every candidate out and the finding is the detail.
 */
export type Offer =
  | { kind: "none" }
  | { kind: "apply"; value: unknown; shown: string }
  | { kind: "replace"; value: unknown; shown: string }
  | { kind: "choose"; candidates: Shown[] }
  | { kind: "blocked"; because: string };

/** What may be clicked on this row. Takes no options: both facts about the
 *  configuration it needs -- how the suggestion stands against it (`state`) and
 *  why it cannot be written (`blocked`) -- were judged against the options that
 *  were sent, and arrive on the row. It used to read the options for the second
 *  one, which meant a row could be offered against one configuration and
 *  refused against another in the same render. */
export function offer(s: Suggestion): Offer {
  if (s.state === "SETTLED") return { kind: "none" };
  // Why a row cannot be offered is generate's rule -- CA trust is one-of, and
  // clearing the mode that holds a value is the silent overwrite this feature
  // may not make -- so the sentence arrives already written.
  if (s.blocked) return { kind: "blocked", because: s.blocked };
  if (s.strength === "SUGGESTIVE") {
    // An empty shortlist is a finding, not an action: nothing this cluster
    // serves can be picked, which the detail says.
    return s.candidates.length
      ? { kind: "choose",
          candidates: s.candidates.map(
            (value, i) => ({ value, shown: s.candidates_shown[i] })) }
      : { kind: "none" };
  }
  return { kind: s.state === "CONFLICT" ? "replace" : "apply",
           value: s.value, shown: s.value_shown };
}

/** What applying writes: the option, and the value. Nothing else -- no marker,
 *  no provenance, nothing downstream can read to tell this apart from a value
 *  somebody typed. That is the same promise facts.manual() keeps on the account
 *  side, and it is what makes the bundle and the profile round-trip identical
 *  either way. */
export function applyPatch(option: string, value: unknown): OptionPatch {
  return { [option]: value };
}

/** What was applied from the panel this session, by option: the value it wrote
 *  and the one that was there first. Reversible within the session means the
 *  previous value is recoverable without being re-entered, and this is where it
 *  is kept -- the options themselves carry no history.
 *
 *  `previousShown` is how that value read on the row it came off. Kept rather
 *  than re-derived: by the time the undo is offered the row has been rendered
 *  again against the configuration as it now is, so the server's string for the
 *  value undo would restore is not on screen anywhere else. */
export type Applied = Record<
  string, { previous: unknown; previousShown: string; value: unknown }>;

export const NOTHING_APPLIED: Applied = {};

/** Record an application. The FIRST previous value is the one kept: picking a
 *  second candidate off the same shortlist must not make the first pick the
 *  thing undo returns to -- what a person wants back is the configuration they
 *  had before the panel touched it. */
export function record(
    prev: Applied, option: string, current: Shown, value: unknown): Applied {
  // Key presence, not `??`: an option that held null (most of them, unset) has a
  // perfectly good previous value, and `??` would step over it back to whatever
  // the second apply replaced.
  const held = prev[option];
  return { ...prev, [option]: {
    previous: held ? held.previous : current.value,
    previousShown: held ? held.previousShown : current.shown,
    value } };
}

/** Applying a row: what to write, and what to remember so it can be taken back.
 *
 *  The previous value comes off the suggestion -- `current`, the value the row
 *  displayed -- and never out of `options`. The two are not the same value: the
 *  server fills `current` from the generator's default for an option nobody
 *  set, and the options object need not carry the key at all. Recording the
 *  options' side of that gave "Undo → not set" on a row reading "now openshift",
 *  and undoing it wrote an explicit null rather than putting the default back.
 *
 *  Both halves are here rather than in the caller for the same reason: what is
 *  written, what is shown as recoverable, and what undo restores are one
 *  decision, and splitting it across a component is how they came apart. */
export function apply(prev: Applied, s: Suggestion, value: unknown):
    { patch: OptionPatch; applied: Applied } {
  return { patch: applyPatch(s.option, value),
           applied: record(prev, s.option,
                           { value: s.current, shown: s.current_shown },
                           value) };
}

/** Is the undo still the panel's to offer? Only while the option still holds
 *  what was applied. Undo restores what was there BEFORE that write, so putting
 *  it back over a value typed afterwards would overwrite that value -- the one
 *  thing this feature may not do. Compared by value rather than identity: the
 *  proxy suggestion applies an object. */
export function canUndo(prev: Applied, option: string, o: Options): boolean {
  const rec = prev[option];
  return !!rec && JSON.stringify(o[option] ?? null)
    === JSON.stringify(rec.value ?? null);
}

/** Undo: the patch that puts the option back, and the record without it. Null
 *  when the panel never wrote this option -- a value the user typed is not the
 *  panel's to take away. */
export function undo(
    prev: Applied, option: string): { patch: OptionPatch; applied: Applied } | null {
  if (!(option in prev)) return null;
  const { [option]: _undone, ...rest } = prev;
  return { patch: applyPatch(option, prev[option].previous), applied: rest };
}

// How a value reads at all -- JSON as profile.json would carry it, unset said
// in words -- is suggest.shown's, and every row arrives with its values already
// written that way. There was a second copy of that rule here, and the two do
// not even agree about the space after a colon in an object.

/** A served value, cut to what a button can hold. The row itself shows it in
 *  full: what must not happen is the label deciding the panel's width, which
 *  the proxy suggestion -- three URLs in one JSON object -- does. */
export function clipValue(shown: string, max = 22): string {
  return shown.length > max ? `${shown.slice(0, max - 1)}…` : shown;
}

/** The one-line summary of what the evidence implies, in the states' own terms.
 *  Empty for an empty list, deliberately: an option no suggestion names was not
 *  checked at all, and a summary over nothing would imply it was. */
export function suggestionLine(suggestions: Suggestion[]): string {
  if (!suggestions.length) return "";
  const n = (...states: string[]) =>
    suggestions.filter((s) => states.includes(s.state)).length;
  const parts = [];
  if (n("FILL")) parts.push(counted(n("FILL"), "to apply"));
  if (n("CHOOSE")) parts.push(plural(n("CHOOSE"), "shortlist"));
  if (n("CONFLICT")) parts.push(plural(n("CONFLICT"), "disagreement"));
  if (n("SETTLED")) parts.push(`${n("SETTLED")} already configured this way`);
  return parts.join(", ");
}
