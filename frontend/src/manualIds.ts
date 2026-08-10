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
  /** The field this value is, in the generator's own vocabulary. It is what the
   *  marker is built from (`marker(rule.key)`), so the string shown in the empty
   *  box is the string the bundle will carry -- one fact rather than two that
   *  agree. `harbor_id` is a fact and the other two are options; that difference
   *  matters on the way out, and not here. */
  key: string;
}

export const HARBOR: IdRule = { label: "Harbor ID", length: 24, key: "harbor_id" };
export const SHIP: IdRule = { label: "Ship ID", length: 24, key: "ship_id" };
export const TOKEN: IdRule = { label: "Auth token", length: 64, key: "auth_token" };

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

/** Is what has been typed usable? Every field may be blank; what is refused is
 *  a value that is *there* and is not the shape an id comes in.
 *
 *  All three used to be two questions and one of them was wrong. A blank token
 *  was already usable -- the bundle carries `<AUTH_TOKEN>` and says so -- while a
 *  blank id stopped the page, on the reading that there is nothing to generate
 *  for. There is: a customer whose private location does not exist yet needs the
 *  manifests before the ids exist, because the manifests are what their platform
 *  team has to approve. So all three are the same answer now, the bundle carries
 *  `<HARBOR_ID>` / `<SHIP_ID>` / `<AUTH_TOKEN>` for whichever was left, and the
 *  page says so beside the fields and again beside the download button.
 *
 *  What this still refuses is the failure it was written for: a truncated or
 *  transposed paste renders a bundle that applies cleanly and leaves an agent
 *  that never joins anything. A blank field cannot do that -- the marker is not a
 *  legal label value, so the cluster refuses the Deployment and names it. */
export function manualComplete(
  harbor: string, ship: string, token: string,
): boolean {
  return !checkId(HARBOR, harbor) && !checkId(SHIP, ship)
    && !checkId(TOKEN, token);
}

/** Which of these fields the bundle will carry a marker for, in the order the
 *  form asks for them.
 *
 *  **The token is a field this may be asked about, rather than one it always
 *  reports on.** The form asks about all three, because somebody looking at three
 *  empty boxes is owed one answer about them. The download step asks about the
 *  two ids only: it already has a line for the credential, and that line is read
 *  off the *token branch the server reports* rather than off this form -- a
 *  bundle can come by a token four ways, and only one of them is this box. Two
 *  sentences about one credential is how a page starts contradicting itself, so
 *  leaving the argument out is how that caller says which question it is
 *  asking. */
export function blankManualIds(
  harbor: string, ship: string, token?: string,
): string[] {
  const asked: [IdRule, string | undefined][] =
    [[HARBOR, harbor], [SHIP, ship], [TOKEN, token]];
  return asked
    .filter(([, v]) => v !== undefined && !String(v).trim())
    .map(([r]) => r.key);
}
