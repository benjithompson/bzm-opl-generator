// What a hand-typed harbor id, ship id and AUTH_TOKEN have to look like.
//
// ManualSource used to say that nothing here validates, on the grounds that a
// format guess can only reject input that was correct. The shapes turned out
// not to be a guess: harbor and ship ids are Mongo ObjectIds -- 24 hex
// characters, which is what every id in this repo's fixtures and every one read
// off a live account is -- and the token is 64. So the check is worth having,
// because the alternative is what it was: a transposed or truncated paste
// renders a bundle that applies cleanly and leaves an agent that never joins
// anything, with nothing on screen having objected.
//
// It checks the shape and nothing else. Whether a well-formed id *exists* is a
// question only the account can answer, and manual entry is for the case where
// nobody here can reach it -- so a value that looks right is accepted, and the
// message names the shape it expected rather than claiming the value is wrong.

/** How many characters each value carries, and what it is called on screen. */
const HEX = /^[0-9a-fA-F]+$/;

export interface IdRule {
  label: string;
  length: number;
}

export const HARBOR: IdRule = { label: "Harbor ID", length: 24 };
export const SHIP: IdRule = { label: "Ship ID", length: 24 };
export const TOKEN: IdRule = { label: "Auth token", length: 64 };

/** The complaint about `value`, or null if there is nothing to say.
 *
 *  Blank is never a complaint. Whether a value is *needed* is a different
 *  question, asked where the step decides it can be left -- saying "required"
 *  from here would put a red message under a field nobody has reached yet. */
export function checkId(rule: IdRule, value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  if (!HEX.test(v)) {
    // Named rather than counted: one pasted character from the wrong keyboard
    // layout is invisible in a 24-character string, so say which it was.
    const bad = [...v].filter((c) => !HEX.test(c));
    const uniq = [...new Set(bad)];
    return `${rule.label} is ${rule.length} hexadecimal characters (0-9, a-f)`
      + ` — this has ${uniq.length === 1 ? "a " : ""}`
      + uniq.slice(0, 4).map((c) => (c === " " ? "space" : `“${c}”`)).join(", ")
      + ` in it`;
  }
  if (v.length !== rule.length) {
    return `${rule.label} is ${rule.length} characters — this is ${v.length}`
      + (v.length < rule.length ? ", so some of it is missing" : "");
  }
  return null;
}

/** Whitespace inside a pasted value, which is the one thing worth fixing rather
 *  than reporting: BlazeMeter's install command wraps, and a copy off it arrives
 *  with a newline in the middle. Trimming the ends is not enough. */
export function tidy(value: string): string {
  return value.replace(/\s+/g, "");
}

/** Is what has been typed usable? Blank ids are not -- there is nothing to
 *  generate for -- but a blank token is, since the bundle carries a placeholder
 *  and says so. */
export function manualComplete(
  harbor: string, ship: string, token: string,
): boolean {
  return !!harbor.trim() && !!ship.trim()
    && !checkId(HARBOR, harbor) && !checkId(SHIP, ship)
    && !checkId(TOKEN, token);
}
