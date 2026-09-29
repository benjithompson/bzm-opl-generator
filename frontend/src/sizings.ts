// Sizings a session remembers by name (a sizing in CONTEXT.md's sense, not a
// profile). Picking one fills the fields and binds nothing afterwards, so
// `sizingNamed` hands back a copy. No React; the shape fits the session snapshot.
import { SizingModel } from "./api";
import { EMPTY_PLAN_INPUTS, PlanInputs } from "./usePlan";

export interface SavedSizing {
  /** Its name, which is also its key. */
  name: string;
  inputs: PlanInputs;
}

/** One sizing per served model, targeted at the model's `example_target`, so
 *  every unit is discoverable before anybody types. Starting points, not
 *  recommendations. */
export function defaultSizings(models: SizingModel[]): SavedSizing[] {
  return models.map((m) => ({
    // Named from the model, so any model arrives with a name saying its unit.
    name: `${m.label}: ${m.example_target.toLocaleString()} ${m.unit}`,
    inputs: {
      ...EMPTY_PLAN_INPUTS,
      functionalities: [m.functionality],
      targets: { [m.functionality]: String(m.example_target) },
      figures: {},
    },
  }));
}

/** A copy of the inputs saved under this name, or null if there are none. */
export function sizingNamed(all: SavedSizing[],
                            name: string): PlanInputs | null {
  const found = all.find((s) => s.name === name);
  // Copied deeply enough that editing the fields cannot change the stored sizing.
  return found
    ? { ...found.inputs,
        functionalities: [...found.inputs.functionalities],
        targets: { ...found.inputs.targets },
        figures: { ...found.inputs.figures } }
    : null;
}

/** Save `inputs` under `name`, replacing a sizing of that name in place. A
 *  blank name saves nothing. */
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
