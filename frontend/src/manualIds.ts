// The shape a hand-typed harbor id, ship id and AUTH_TOKEN must have: ids are
// 24 hex characters (Mongo ObjectIds) and the token 64. A truncated paste would
// otherwise build a bundle that applies and never joins anything. Only the
// shape is checked; whether an id exists only the account can say.

const HEX = /^[0-9a-fA-F]+$/;

export interface IdRule {
  label: string;
  length: number;
  /** The option or fact key, which the marker for an empty box is built from. */
  key: string;
}

export const HARBOR: IdRule = { label: "Harbor ID", length: 24, key: "harbor_id" };
export const SHIP: IdRule = { label: "Ship ID", length: 24, key: "ship_id" };
export const TOKEN: IdRule = { label: "Auth token", length: 64, key: "auth_token" };

/** What is wrong with `value`, or null. Blank is never a complaint here;
 *  whether a value is needed is decided elsewhere. */
export function checkId(rule: IdRule, value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  if (!HEX.test(v)) {
    // Name the offending characters: one from the wrong keyboard layout is
    // invisible in a 24-character string.
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

/** Strip whitespace anywhere in a paste: BlazeMeter's install command wraps. */
export function tidy(value: string): string {
  return value.replace(/\s+/g, "");
}

/** Is what has been typed usable? Blanks are (the bundle carries a marker, for
 *  a location that does not exist yet); a value that is there and misshapen is
 *  not. */
export function manualComplete(
  harbor: string, ship: string, token: string,
): boolean {
  return !checkId(HARBOR, harbor) && !checkId(SHIP, ship)
    && !checkId(TOKEN, token);
}

/** Which fields the bundle will carry a marker for, in form order. Leave
 *  `token` out to ask about the ids only, as the download step does (its token
 *  line comes from the server's report). */
export function blankManualIds(
  harbor: string, ship: string, token?: string,
): string[] {
  const asked: [IdRule, string | undefined][] =
    [[HARBOR, harbor], [SHIP, ship], [TOKEN, token]];
  return asked
    .filter(([, v]) => v !== undefined && !String(v).trim())
    .map(([r]) => r.key);
}
