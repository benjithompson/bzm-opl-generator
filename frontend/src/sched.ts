// Where engines run, as data. The Scheduling group's radio prescribes the
// two-pool shape docs/preflight.md recommends; this module is the mapping
// between that choice and the four scheduling options, plus the row shapes the
// structured editors work in -- nobody types JSON (#127).
//
// The choice is derived from the options, never stored, the same rule as
// caModeOf: a mode kept beside the values it summarises is a mode that can
// disagree with them. Deriving from value *shape* rather than from equality
// with the prefill keeps the radio meaningful after a hand edit -- a renamed
// pool label is still "separate nodes".

import { Options } from "./api";
import { OptionPatch } from "./optionGroups";

export type Placement = "crane" | "separate" | "anywhere" | "custom";

/** The pool name the "separate nodes" choice prescribes. One word, used as
 *  both the label value and the taint value so the capacity request's
 *  node-pools recipe names a single vocabulary for the platform team. */
export const ENGINE_POOL = "bzm-engines";

const SEPARATE_PATCH: OptionPatch = {
  engine_node_selector: { pool: ENGINE_POOL },
  engine_tolerations: [
    { key: "pool", operator: "Equal", value: ENGINE_POOL, effect: "NoSchedule" },
  ],
};

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Which radio state the engine options currently are.
 *
 *  null-vs-empty is the whole grammar here (docs/preflight.md): unset means
 *  "engines go wherever crane goes", `{}`/`[]` means "no selector or
 *  toleration of their own". So: both unset = with crane; both set and empty =
 *  anywhere; a non-empty selector = a pool of their own, whatever it is named
 *  and however it is tainted. Anything else -- one side unset, or a selector
 *  emptied while tolerations remain -- fits no choice, and saying "custom"
 *  beats silently rewriting the half that does not fit. */
export function placementOf(o: Options): Placement {
  const sel = o.engine_node_selector;
  const tol = o.engine_tolerations;
  if (sel == null && tol == null) return "crane";
  if (isObj(sel) && Object.keys(sel).length > 0 && tol != null) return "separate";
  if (isObj(sel) && Object.keys(sel).length === 0
    && Array.isArray(tol) && tol.length === 0) return "anywhere";
  return "custom";
}

/** What picking a radio choice writes. "custom" is not a choice -- it is the
 *  name of every state the other three do not produce -- so it has no patch. */
export function placementPatch(p: Exclude<Placement, "custom">): OptionPatch {
  if (p === "crane") return { engine_node_selector: null, engine_tolerations: null };
  if (p === "anywhere") return { engine_node_selector: {}, engine_tolerations: [] };
  return structuredClone(SEPARATE_PATCH);
}

// -- the editors' row shapes ---------------------------------------------------

/** A node selector as rows the key/value table edits. Order is the object's
 *  own; a row whose key is blank is still being typed and stays out of the
 *  option until it has one (rowsToSelector). */
export function selectorToRows(sel: unknown): { key: string; value: string }[] {
  if (!isObj(sel)) return [];
  return Object.entries(sel).map(([key, value]) => ({ key, value: String(value) }));
}

export function rowsToSelector(rows: { key: string; value: string }[]):
  Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.key.trim()) out[r.key.trim()] = r.value;
  return out;
}

/** One toleration, as the editor sees it. The row *is* the underlying object:
 *  the editor reads the four fields generate reads (key, operator, value,
 *  effect) and writes by spreading over the original, so a field it does not
 *  know -- tolerationSeconds, say -- survives a round trip through the table.
 *  generate passes the whole list into the podspec and the engines' env, so
 *  dropping unknown fields here would be the UI quietly rewriting a bundle. */
export type TolerationRow = Record<string, unknown>;

export const TOLERATION_OPERATORS = ["Equal", "Exists"] as const;
/** "" is "any effect", which Kubernetes expresses by omitting the field. */
export const TOLERATION_EFFECTS = ["NoSchedule", "PreferNoSchedule", "NoExecute", ""] as const;

export function tolerationField(row: TolerationRow, field: string): string {
  const v = row[field];
  return typeof v === "string" ? v : "";
}

/** Set one field, dropping it entirely when blanked: `effect: ""` and no
 *  effect mean the same thing to Kubernetes, but only one of them is what a
 *  hand-written bundle carries, and a diff between the two is noise. */
export function withTolerationField(
  row: TolerationRow, field: string, value: string,
): TolerationRow {
  const out = { ...row };
  if (value === "") delete out[field];
  else out[field] = value;
  // An Exists toleration matches on the key alone; a value left behind from
  // the Equal days would be sent, and Kubernetes rejects the combination.
  if (field === "operator" && value === "Exists") delete out.value;
  return out;
}

export function tolerationsToRows(tol: unknown): TolerationRow[] {
  if (!Array.isArray(tol)) return [];
  return tol.filter(isObj);
}

/** A just-added row with nothing typed yet stays out of the option, exactly
 *  like a selector row still missing its key: an empty toleration object is
 *  not "nothing" to Kubernetes, it tolerates every taint. */
export function rowsToTolerations(rows: TolerationRow[]): TolerationRow[] {
  return rows.filter((r) => Object.keys(r).length > 0);
}

/** What switching an engine field to Custom starts from: crane's own value,
 *  so "custom" begins as "what you would have inherited" rather than as the
 *  quietly load-bearing empty ("no selector at all"). Cloned -- the two must
 *  stop being the same object the moment they stop being the same setting. */
export function customSeed(craneValue: unknown, empty: object): unknown {
  return craneValue == null ? empty : structuredClone(craneValue);
}
