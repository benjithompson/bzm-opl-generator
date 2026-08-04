// Free-form agent environment, as data. The Environment variables area edits
// `extra_env` as rows of name and value; this module is the mapping between
// those rows and the option, plus the one judgement the rows make -- whether a
// name can be used at all. Nobody types JSON (#127's idiom, #131's area).
//
// What a name may be, and which names are taken, are both the generator's:
// ENV_NAME_RE and RESERVED_ENV in generate.py, the second served as
// /api/reserved-env. The pattern is restated here because it is four
// characters of C-identifier rule that cannot change without the language
// changing; the *list* is never restated, because a variable added to a
// template would otherwise go on being offered until somebody noticed a
// ConfigMap with a duplicate key.

import { Options } from "./api";

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
 *  `incomplete` of the env group.
 *
 *  Asked of the option with no served table, which is what a group's
 *  `incomplete` is handed -- so it answers only the half the option can answer
 *  on its own: a name no process could read. That is the half that would
 *  otherwise be silent, since a malformed name reaches generate() as a plain
 *  string and the refusal arrives from a server request the button is already
 *  disabled by.
 *
 *  A *reserved* name blocks too, and is not this: generate() refuses it, the
 *  row states it in the sentence with the owning option in it, and the two
 *  arrive together. What must not happen is only that it be accepted, and it is
 *  not. Duplicates cannot reach here at all -- two rows of one name collapse
 *  into one key on the way to the option -- which is exactly why the row, not
 *  this, is where that one is caught. */
export function envIncomplete(o: Options): boolean {
  const rows = envToRows(o.extra_env);
  return rows.some((_r, i) => !!envRowError(rows, i, {}));
}
