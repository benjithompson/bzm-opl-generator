// Where engines run, as data: the Scheduling group's placement choice mapped to
// the four scheduling options, and the row shapes its editors use. The choice
// is derived from the options' shape, never stored.

import { Options } from "./api";
import { OptionPatch } from "./optionGroups";

export type Placement = "crane" | "separate" | "anywhere" | "custom";

/** The pool name "separate nodes" prescribes, as both label and taint value. */
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

/** Which placement the engine options describe. Unset means follow crane,
 *  `{}`/`[]` means none of their own, a non-empty selector is a pool of their
 *  own; anything else is "custom" rather than being rewritten. */
export function placementOf(o: Options): Placement {
  const sel = o.engine_node_selector;
  const tol = o.engine_tolerations;
  if (sel == null && tol == null) return "crane";
  if (isObj(sel) && Object.keys(sel).length > 0 && tol != null) return "separate";
  if (isObj(sel) && Object.keys(sel).length === 0
    && Array.isArray(tol) && tol.length === 0) return "anywhere";
  return "custom";
}

/** What picking a choice writes. "custom" is not a choice, so it has no patch. */
export function placementPatch(p: Exclude<Placement, "custom">): OptionPatch {
  if (p === "crane") return { engine_node_selector: null, engine_tolerations: null };
  if (p === "anywhere") return { engine_node_selector: {}, engine_tolerations: [] };
  return structuredClone(SEPARATE_PATCH);
}

// -- the editors' row shapes ---------------------------------------------------

/** A node selector as rows; a row with a blank key stays out (rowsToSelector). */
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

/** One toleration as the editor sees it: the object itself, edited by
 *  spreading, so fields the editor does not show survive a round trip. */
export type TolerationRow = Record<string, unknown>;

export const TOLERATION_OPERATORS = ["Equal", "Exists"] as const;
/** "" is "any effect", which Kubernetes expresses by omitting the field. */
export const TOLERATION_EFFECTS = ["NoSchedule", "PreferNoSchedule", "NoExecute", ""] as const;

export function tolerationField(row: TolerationRow, field: string): string {
  const v = row[field];
  return typeof v === "string" ? v : "";
}

/** Set one field, dropping it when blanked (no `effect: ""` in the bundle). */
export function withTolerationField(
  row: TolerationRow, field: string, value: string,
): TolerationRow {
  const out = { ...row };
  if (value === "") delete out[field];
  else out[field] = value;
  // An Exists toleration may not carry a value.
  if (field === "operator" && value === "Exists") delete out.value;
  return out;
}

export function tolerationsToRows(tol: unknown): TolerationRow[] {
  if (!Array.isArray(tol)) return [];
  return tol.filter(isObj);
}

/** Drops rows with nothing typed: an empty toleration tolerates every taint. */
export function rowsToTolerations(rows: TolerationRow[]): TolerationRow[] {
  return rows.filter((r) => Object.keys(r).length > 0);
}

/** What switching to an engine override starts from: a copy of crane's value. */
export function customSeed(craneValue: unknown, empty: object): unknown {
  return craneValue == null ? empty : structuredClone(craneValue);
}
