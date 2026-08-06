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
import { SizingModel } from "./api";
import { EMPTY_PLAN_INPUTS, PlanInputs } from "./usePlan";

export interface SavedSizing {
  /** What it is called, and the key: two sizings with one name are one sizing
   *  somebody saved twice. */
  name: string;
  inputs: PlanInputs;
}

/** One sizing per served model, so the list is never empty and every unit has
 *  been seen once before anybody types a number.
 *
 *  Mapped over the models rather than written out. It was three records naming
 *  all three funcIds and inventing a target for each, under a comment claiming
 *  "one per functionality" -- which is a promise a file on this side cannot
 *  keep: a fourth model reaches the card by being added to `plan.SIZING_MODELS`
 *  and would have reached this list never. The target is that table's
 *  `example_target` for the same reason: a figure invented here, for a unit
 *  the page has only just been told the name of, is the one thing this whole
 *  card is arranged not to do.
 *
 *  Starting points and not recommendations: nothing anywhere knows what a
 *  customer runs, and the per-pod figures each model then applies are the ones
 *  the plan states as assumed. They exist so the control has something in it
 *  and so the units are discoverable, which they are not when the card opens on
 *  a single empty box.
 *
 *  The service-virtualization one carries a request rate and produces no plan,
 *  which is the honest demonstration of the thing this whole card has to say:
 *  the figure that would size it has not been measured, so the server refuses
 *  rather than inventing one, and the refusal is the explanation. */
export function defaultSizings(models: SizingModel[]): SavedSizing[] {
  return models.map((m) => ({
    // Named from what it is, so a model nobody here has heard of still arrives
    // with a name that says which unit it is in.
    name: `${m.label}: ${m.example_target.toLocaleString()} ${m.unit}`,
    inputs: {
      ...EMPTY_PLAN_INPUTS,
      functionalities: [m.functionality],
      targets: { [m.functionality]: String(m.example_target) },
      figures: {},
    },
  }));
}

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
