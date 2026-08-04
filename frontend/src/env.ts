// Free-form agent environment, as data. The Environment variables area edits
// `extra_env`; this module is the mapping between what is on screen and the
// option, plus the judgements the area makes -- whether a name can be used at
// all, and what a typed value means. Nobody types JSON (#127's idiom, #131's
// area).
//
// What a name may be, and which names are taken, are both the generator's:
// ENV_NAME_RE and RESERVED_ENV in generate.py, the second served as
// /api/reserved-env. The pattern is restated here because it is four
// characters of C-identifier rule that cannot change without the language
// changing; the *list* is never restated, because a variable added to a
// template would otherwise go on being offered until somebody noticed a
// ConfigMap with a duplicate key.
//
// The variables that ARE offered are served too (/api/agent-env, AgentEnvVar):
// BlazeMeter's documented reference minus everything a control on this page
// already writes. The area used to be a name box and a value box, which asked
// somebody to know that KUBERNETES_USE_PRE_PULLING exists, spell it, and know
// its value is the word `true` -- a documentation lookup performed at the
// keyboard, where a typo produces a variable the agent never reads and nothing
// says so. So the list is on screen and the row carries the control its type
// deserves. Everything below is still strings by the time it reaches the
// option: an environment variable is text, and generate.extra_env refuses
// anything that is not a scalar.

import { AgentEnvVar, Options } from "./api";

/** What the generator will accept as a variable name (generate.ENV_NAME_RE).
 *  A ConfigMap key may hold dots and dashes; a variable named with one is not
 *  reachable from the process meant to read it, so the bundle would apply
 *  cleanly and change nothing. */
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** The served table: variable name -> the option that writes it, or null where
 *  no single option does. Empty means "not read yet", and everything is
 *  allowed -- the same direction as an empty docker-ignored table, and for the
 *  same reason: a row refused on a guess is worse than one the server refuses
 *  a moment later with the authoritative sentence. */
export type Reserved = Record<string, string | null>;

export interface EnvRow { name: string; value: string }

/** The option as rows. Order is the object's own, which is the order the form
 *  wrote it in. */
export function envToRows(env: unknown): EnvRow[] {
  if (typeof env !== "object" || env === null || Array.isArray(env)) return [];
  return Object.entries(env as Record<string, unknown>)
    .map(([name, value]) => ({ name, value: value == null ? "" : String(value) }));
}

/** ...and back. A row with no name yet is still being typed and stays out of
 *  the option, exactly as a selector row without its key does (sched.ts) --
 *  otherwise every keystroke in an empty row re-POSTs the preview.
 *
 *  A row whose name is *bad* does NOT stay out: it reaches the option, so
 *  generate refuses it and the download is blocked while the row on screen says
 *  why. Dropping it would leave a form showing a variable no bundle carries. */
export function rowsToEnv(rows: EnvRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.name.trim()) out[r.name.trim()] = r.value;
  return out;
}

/** Why this row cannot be used, or "" where it can.
 *
 *  Duplicates are a row-level answer rather than an option-level one: two rows
 *  named the same collapse into one key on the way to the option, so the
 *  second silently replaces the first and neither row would say so. */
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

/** Is the area in use but unusable, so the download is blocked? The
 *  `incomplete` of the env area.
 *
 *  Asked of the option with no served table, so it answers only the half the
 *  option can answer on its own: a name no process could read. That is the half
 *  that would otherwise be silent, since a malformed name reaches generate() as
 *  a plain string and the refusal arrives from a server request the button is
 *  already disabled by.
 *
 *  A *reserved* name blocks too, and is not this: generate() refuses it, the
 *  row states it in the sentence with the owning option in it, and the two
 *  arrive together. What must not happen is only that it be accepted, and it is
 *  not. Duplicates cannot reach here at all -- two rows of one name collapse
 *  into one key on the way to the option -- which is exactly why the row, not
 *  this, is where that one is caught.
 *
 *  Nothing a catalogue control writes can fail it: the name comes off the
 *  served record. It stays because the free-form rows underneath are still a
 *  place a name is typed. */
export function envIncomplete(o: Options): boolean {
  const rows = envToRows(o.extra_env);
  return rows.some((_r, i) => !!envRowError(rows, i, {}));
}

// -- the offered variables ----------------------------------------------------

/** Which of the served variables this bundle's agent would actually read.
 *
 *  BlazeMeter documents two tables and they are not the same table -- TLS_CERT
 *  and HOSTNAME_OVERRIDE are the container agent's, the KUBERNETES_* half is
 *  crane's -- so the platform is part of each record and the area shows one
 *  side of it. Not a refusal: a variable already set that this platform does
 *  not document keeps its value and appears below as a plain row (see
 *  `otherRows`), because hiding a variable the bundle carries is the failure
 *  this whole area's rules are about. */
export function offeredVars(vars: AgentEnvVar[], cluster: boolean): AgentEnvVar[] {
  const want = cluster ? "kubernetes" : "docker";
  return vars.filter((v) => v.platforms.includes(want));
}

/** What this variable is set to, or "" -- the one reader of the option that
 *  every control on a catalogue row goes through. `""` is honest for both
 *  "unset" and "set to empty" here because the two produce the same bundle: a
 *  ConfigMap entry with an empty value is what an empty box would have to
 *  write, so the area writes nothing instead (see `setVar`). */
export function varValue(env: unknown, name: string): string {
  const found = envToRows(env).find((r) => r.name === name);
  return found ? found.value : "";
}

/** Is it set at all? Distinct from `varValue` returning "", and this is the one
 *  that decides whether a row counts as configured -- a boolean row's third
 *  position is "not set", and a summary saying "2 set" must not count a
 *  variable somebody switched back to the agent's default. */
export function varSet(env: unknown, name: string): boolean {
  return envToRows(env).some((r) => r.name === name);
}

/** Write one variable, or clear it with `null`. Returns the option whole --
 *  `null` for "nothing set", which is `extra_env`'s default and not `{}`: an
 *  empty object would show up in profile.json as a key a bundle generated
 *  without this area never had.
 *
 *  Order is preserved for a variable that was already set, and a new one is
 *  appended, so a row does not jump up the ConfigMap when it is edited. */
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

/** The three answers a boolean row has, and they stay three: the agent's own
 *  default (nothing written), on, and off.
 *
 *  A two-position toggle cannot say the first, and the difference is real in
 *  both directions -- VERIFY_SSL defaults on, KUBERNETES_USE_PRE_PULLING
 *  defaults off, so "off" is a departure for one and the default for the other,
 *  and a bundle that states a value deliberately is not the same as one that
 *  never asked. Same shape as the `auto_update` tri-state in the Security
 *  group, one file over. */
export type BoolChoice = "default" | "true" | "false";

export function boolChoice(env: unknown, name: string): BoolChoice {
  if (!varSet(env, name)) return "default";
  // Whatever else it holds -- an imported profile can carry `1`, `yes`, or a
  // typo -- reads as "on" only when it is the word the agent reads. Falling
  // back to "false" would be this page deciding what a value it did not write
  // means; falling back to "default" would be worse still, since it says the
  // variable is not set when it is.
  return varValue(env, name).trim().toLowerCase() === "true" ? "true" : "false";
}

/** What that choice writes: the lower-case word every boolean the agent reads
 *  is spelled with (generate._env_value writes the same), or nothing at all. */
export function boolWrite(choice: BoolChoice): string | null {
  return choice === "default" ? null : choice;
}

// -- JSON-object variables ----------------------------------------------------
// KUBERNETES_LABELS and KUBERNETES_CUSTOM_ANNOTATIONS_JSON are objects of
// strings encoded into one variable. The row edits them as the same key/value
// table the node selector uses, and the encoding happens here -- nobody types
// JSON, and a hand-encoded one is a brace away from a variable the agent
// cannot parse.

export interface KvRow { key: string; value: string }

/** The variable's value as table rows, or null where it is not an object of
 *  scalars this table can round-trip.
 *
 *  Null is a real answer and the caller renders a text box for it: a profile
 *  can arrive carrying an array, a nested object, or something that is not JSON
 *  at all, and a table that showed it as no rows would offer to save an empty
 *  object over a value it had merely failed to read. "Could not read" and
 *  "there is nothing there" again. */
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

/** ...and back: the variable's value, or null for a table with nothing in it,
 *  which clears the variable rather than setting it to `{}`. A row with no key
 *  yet is being typed and stays out, as everywhere else on this page. */
export function kvToJson(rows: KvRow[]): string | null {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.key.trim()) out[r.key.trim()] = r.value;
  return Object.keys(out).length ? JSON.stringify(out) : null;
}

/** Why this value cannot be used, or "" -- the row says so and the value is
 *  kept, exactly as a bad name is. Only the shapes a control can produce are
 *  judged: an integer field that has been typed into. */
export function varError(v: AgentEnvVar, value: string): string {
  if (v.type === "int" && value.trim() && !/^\d+$/.test(value.trim())) {
    return "whole number only";
  }
  return "";
}

/** The variables that are set and have no control above them: a name from
 *  neither the served list nor this platform's half of it, or a JSON one whose
 *  value no table could round-trip. They keep the name/value editor.
 *
 *  This is what makes the catalogue a list rather than a filter. The served
 *  vocabulary can lose a name -- an option added to the generator takes one
 *  away, and a profile from last month still carries it -- and a form that
 *  showed only what it recognised would show nothing for a variable the bundle
 *  is about to write. */
export function otherRows(env: unknown, shown: string[]): EnvRow[] {
  const known = new Set(shown);
  return envToRows(env).filter((r) => !known.has(r.name));
}
