// The environment variables area, as data: the mapping between the rows on
// screen and `extra_env`, and the judgements the area makes about a name or a
// value. The offered variables and the reserved names are both served; only
// the name pattern is restated. Values are always strings.

import { AgentEnvVar, Options } from "./api";

/** Names the generator accepts (generate.ENV_NAME_RE; test_server.py holds
 *  them equal). Dots and dashes would apply cleanly and reach no process. */
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** The served table: variable name -> the option that writes it, or null. Empty
 *  means not read yet, and refuses nothing. */
export type Reserved = Record<string, string | null>;

export interface EnvRow { name: string; value: string }

/** The option as rows, in the object's own order. */
export function envToRows(env: unknown): EnvRow[] {
  if (typeof env !== "object" || env === null || Array.isArray(env)) return [];
  return Object.entries(env as Record<string, unknown>)
    .map(([name, value]) => ({ name, value: value == null ? "" : String(value) }));
}

/** ...and back. A row with no name yet stays out, so typing does not re-POST
 *  the preview; a row with a bad name stays in, so generate refuses it and the
 *  row says why. */
export function rowsToEnv(rows: EnvRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.name.trim()) out[r.name.trim()] = r.value;
  return out;
}

/** Why this row cannot be used, or "". Duplicates are caught per row, since two
 *  rows of one name collapse into one key. */
export function envRowError(
    rows: EnvRow[], i: number, reserved: Reserved): string {
  const name = rows[i].name.trim();
  if (!name) return "";
  if (!NAME_RE.test(name)) {
    return "letters, digits and underscore only, and not starting with a digit";
  }
  if (name in reserved) {
    const owner = reserved[name];
    return owner
      ? `this bundle already sets ${name} — set it with ${owner} instead`
      : `${name} is written by the bundle itself and cannot be set here`;
  }
  if (rows.some((r, j) => j < i && r.name.trim() === name)) {
    return "already set above";
  }
  return "";
}

/** Does the area block the download? Only for a name no process could read,
 *  judged without the served table; reserved names and duplicates are caught
 *  per row. */
export function envIncomplete(o: Options): boolean {
  const rows = envToRows(o.extra_env);
  return rows.some((_r, i) => !!envRowError(rows, i, {}));
}

// -- the offered variables ----------------------------------------------------

/** The served variables this bundle's agent reads: the Kubernetes table for a
 *  cluster bundle, the docker one otherwise. A set variable from the other
 *  table is still shown, below, by otherRows. */
export function offeredVars(vars: AgentEnvVar[], cluster: boolean): AgentEnvVar[] {
  const want = cluster ? "kubernetes" : "docker";
  return vars.filter((v) => v.platforms.includes(want));
}

/** What this variable is set to, or "". Unset and empty write the same bundle. */
export function varValue(env: unknown, name: string): string {
  const found = envToRows(env).find((r) => r.name === name);
  return found ? found.value : "";
}

/** Is it set at all? What counts a row as configured; a boolean back at the
 *  agent's default is not set. */
export function varSet(env: unknown, name: string): boolean {
  return envToRows(env).some((r) => r.name === name);
}

/** Write one variable, or clear it with `null`. Returns the whole option, null
 *  when nothing is set (not `{}`). An existing variable keeps its position. */
export function setVar(
    env: unknown, name: string, value: string | null): Record<string, string> | null {
  const rows = envToRows(env);
  const at = rows.findIndex((r) => r.name === name);
  const next = value === null
    ? rows.filter((r) => r.name !== name)
    : at >= 0
      ? rows.map((r, i) => (i === at ? { name, value } : r))
      : [...rows, { name, value }];
  const kv = rowsToEnv(next);
  return Object.keys(kv).length ? kv : null;
}

/** A boolean row's three answers: the agent's default (nothing written), on,
 *  and off. Defaults run both ways, so "off" is not always the default. */
export type BoolChoice = "default" | "true" | "false";

export function boolChoice(env: unknown, name: string): BoolChoice {
  if (!varSet(env, name)) return "default";
  // Anything but the word "true" reads as off; it is set, so never "default".
  return varValue(env, name).trim().toLowerCase() === "true" ? "true" : "false";
}

/** What that choice writes: lower-case "true"/"false", or nothing. */
export function boolWrite(choice: BoolChoice): string | null {
  return choice === "default" ? null : choice;
}

// -- JSON-object variables ----------------------------------------------------
// Objects of strings encoded into one variable (KUBERNETES_LABELS and the
// like), edited as a key/value table and encoded here.

export interface KvRow { key: string; value: string }

/** The value as table rows, or null where it is not an object of scalars the
 *  table can round-trip; the caller then shows a text box, never an empty table. */
export function jsonToKv(value: string): KvRow[] | null {
  const text = value.trim();
  if (!text) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.some(([, v]) => typeof v === "object" && v !== null)) return null;
  return entries.map(([key, v]) => ({ key, value: v == null ? "" : String(v) }));
}

/** ...and back, or null for an empty table (which clears the variable). Rows
 *  with no key yet stay out. */
export function kvToJson(rows: KvRow[]): string | null {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.key.trim()) out[r.key.trim()] = r.value;
  return Object.keys(out).length ? JSON.stringify(out) : null;
}

/** Why this value cannot be used, or "". The value is kept either way. */
export function varError(v: AgentEnvVar, value: string): string {
  if (v.type === "int" && value.trim() && !/^\d+$/.test(value.trim())) {
    return "whole number only";
  }
  return "";
}

/** The set variables with no control above them: from the other platform's
 *  table, no longer offered, or unreadable JSON. They keep the name/value editor
 *  so nothing the bundle carries is hidden. */
export function otherRows(env: unknown, shown: string[]): EnvRow[] {
  const known = new Set(shown);
  return envToRows(env).filter((r) => !known.has(r.name));
}
