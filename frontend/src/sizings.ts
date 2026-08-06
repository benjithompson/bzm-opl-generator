// Sizings a session remembers by name.
//
// A **sizing**, in CONTEXT.md's sense: a named statement of the capacity one
// functionality needs. Not a `profile` -- that is a JSON file of generator
// options, in three places already -- and not a `preset`, which is an engine
// size and is `EngineSizeSelect`'s prop. Three words, three meanings, and this
// is the third.
//
// Picking one is the only thing on the sizing card that could be called
// "apply", and it is not one: it fills the fields, and the fields *are* the
// sizing. Nothing here is bound to what was picked afterwards, which is why
// `sizingNamed` hands back a copy of the inputs rather than the stored record
// -- an editor holding the stored object would rewrite a saved sizing as
// somebody typed.
//
// Plain data in, data out, like session.ts beside it: no React, so the rules
// are testable without a DOM, and the shape is one the session snapshot can
// hold as it stands.
import { EMPTY_PLAN_INPUTS, PlanInputs } from "./usePlan";

export interface SavedSizing {
  /** What it is called, and the key: two sizings with one name are one sizing
   *  somebody saved twice. */
  name: string;
  inputs: PlanInputs;
}

const sizing = (name: string, functionality: string,
                target: string): SavedSizing => ({
  name,
  inputs: { ...EMPTY_PLAN_INPUTS, functionalities: [functionality],
            targets: { [functionality]: target }, figures: {} },
});

/** One per functionality, so the list is never empty and every unit has been
 *  seen once before anybody types a number.
 *
 *  Starting points and not recommendations: nothing here knows what a customer
 *  runs, and the figures each model then applies are the ones the plan states
 *  as assumed. They exist so the control has something in it and so the three
 *  units are discoverable, which they are not when the card opens on a single
 *  empty box.
 *
 *  The service-virtualization one carries a request rate and produces no plan,
 *  which is the honest demonstration of the thing this whole card has to say:
 *  the figure that would size it has not been measured, so the server refuses
 *  rather than inventing one, and the refusal is the explanation. */
export const DEFAULT_SIZINGS: SavedSizing[] = [
  sizing("Load test, 5,000 users", "performance", "5000"),
  sizing("Browser suite, 20 in parallel", "functionalGui", "20"),
  sizing("Virtual services, 2,000 rps", "mockServices", "2000"),
];

/** The inputs saved under this name, or null if none were.
 *
 *  Null rather than an empty sizing, for the reason everything in this repo
 *  keeps the two apart: a caller handed blanks would fill the form with them
 *  and call it applied. */
export function sizingNamed(all: SavedSizing[],
                            name: string): PlanInputs | null {
  const found = all.find((s) => s.name === name);
  // Copied on the way out, including the two records: the card edits what it
  // is given, and a stored sizing must not move under somebody typing in a
  // field it filled.
  return found
    ? { ...found.inputs,
        functionalities: [...found.inputs.functionalities],
        targets: { ...found.inputs.targets },
        figures: { ...found.inputs.figures } }
    : null;
}

/** Save `inputs` under `name`, replacing a sizing of that name in place.
 *
 *  In place rather than appended, because a re-save is a correction and a list
 *  that reordered itself under one would move the row somebody was reading. A
 *  blank name saves nothing: it is the state the box is in before anybody has
 *  said what this is, and an unnamed sizing cannot be picked again. */
export function save(all: SavedSizing[], name: string,
                     inputs: PlanInputs): SavedSizing[] {
  const trimmed = name.trim();
  if (!trimmed) return all;
  const record = { name: trimmed, inputs };
  return all.some((s) => s.name === trimmed)
    ? all.map((s) => (s.name === trimmed ? record : s))
    : [...all, record];
}

export function remove(all: SavedSizing[], name: string): SavedSizing[] {
  return all.filter((s) => s.name !== name);
}
