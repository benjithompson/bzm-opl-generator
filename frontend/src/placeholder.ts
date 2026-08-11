/** Required fields that were left blank, and the marker that stands in for one.
 *
 *  The problem this solves is an empty text box that looks like an answer. A
 *  namespace nobody typed used to block the step; a private-registry host
 *  nobody typed did not, and the bundle came out with no private registry and
 *  nothing anywhere saying so -- the group's switch was on, the field was
 *  empty, and empty is what "not using one" looks like in the options. Both
 *  become the same thing here: the field carries `<PRIVATE_REGISTRY>` into the
 *  bundle, the README names it, and applying it fails with the field named
 *  rather than deploying something subtly wrong. The marker is the field's own
 *  key in upper case, so the string in the file says which box was left empty
 *  without the README having to be the only place that does.
 *
 *  **The marker never enters the options this page holds.** It is applied to
 *  what is *sent* (`withPlaceholders`), so the input stays empty on screen,
 *  the session snapshot keeps the blank, and typing a value later needs no
 *  undoing of a value the page put there. A form that filled its own boxes in
 *  would be answering its own question.
 *
 *  Two halves, as on the server. `generate.REQUIRED_TEXT` fills the fields whose
 *  requirement is visible in the options alone (a namespace, a service account,
 *  an SV subdomain once an ingress is chosen); this fills the ones that are
 *  required only because a *switch on this page* is on, which the options
 *  cannot show. Sending the marker for a field the server would have filled
 *  anyway is harmless and deliberate -- it is the same value -- and it is what
 *  lets `blankRequired` be the one list the warnings are written from.
 */
import { Options, PlaceholderSource } from "./api";
import { Applies } from "./formats";
import { GroupId, OPTION_GROUPS, serviceAccountOk } from "./optionGroups";

/** The marker for one option key: the key in upper case, with a dotted key
 *  joined by an underscore. `auth_token` gives `<AUTH_TOKEN>`, `proxy.https`
 *  gives `<PROXY_HTTPS>`.
 *
 *  `generate.marker` is the same rule on the server, and the two are held to it
 *  by `test_server.py` through the examples in `fixtures.ts` -- neither side can
 *  change the rule alone. Not served, because the page has to write a marker
 *  before any response has arrived. */
export function marker(key: string): string {
  return `<${key.replace(/\./g, "_").toUpperCase()}>`;
}

/** The core fields, which belong to no group: they are on the step itself. */
const CORE: { key: string; blank: (o: Options) => boolean }[] = [
  { key: "namespace", blank: (o) => !String(o.namespace ?? "").trim() },
  { key: "service_account_name", blank: (o) => !serviceAccountOk(o) },
];

/** Every required field this configuration has left blank, in the order the
 *  form asks for them: the core two first, then the groups in their own order.
 *
 *  `applies` drops what this format has no such field as -- the same predicate
 *  the form itself hides them with, so a warning can never name a field that is
 *  not on screen. That is the off-screen blocker this codebase keeps refusing,
 *  wearing a different hat: naming a docker bundle's namespace would be a
 *  sentence about a box that format deliberately does not show. */
export function blankRequired(
    o: Options, applies: Applies,
    groupOn: Partial<Record<GroupId, boolean>>): string[] {
  const out = CORE
    .filter((c) => applies(c.key) && c.blank(o))
    .map((c) => c.key);
  for (const g of OPTION_GROUPS) {
    if (!groupOn[g.id] || !g.requires) continue;
    for (const key of g.requires(o)) {
      if (applies(key.split(".")[0]) && isBlank(o, key)) out.push(key);
    }
  }
  return out;
}

/** Is `key` -- possibly `proxy.https` -- empty in these options? */
function isBlank(o: Options, key: string): boolean {
  return !String(readKey(o, key) ?? "").trim();
}

function readKey(o: Options, key: string): unknown {
  const [head, sub] = key.split(".");
  const v = (o as Record<string, unknown>)[head];
  if (sub === undefined) return v;
  return v && typeof v === "object"
    ? (v as Record<string, unknown>)[sub] : undefined;
}

/** The options as they should be sent, with every blank required field carrying
 *  the marker. Returns the original object when there is nothing to fill, so an
 *  effect keyed on the options identity does not re-POST for a bundle that did
 *  not change. */
export function withPlaceholders(o: Options, blanks: string[]): Options {
  if (!blanks.length) return o;
  const out: Record<string, unknown> = { ...o };
  for (const key of blanks) {
    // The marker for the whole dotted key, so `proxy.https` sends
    // `<PROXY_HTTPS>` and not the sub-key's own -- the generator's
    // `placeholder_options` reports the same dotted key back, and the two have
    // to be the one name for the one field.
    const mark = marker(key);
    const [head, sub] = key.split(".");
    if (sub === undefined) { out[head] = mark; continue; }
    out[head] = { ...(out[head] as object ?? {}), [sub]: mark };
  }
  return out as Options;
}

/** What the page says about them, or "" when there is nothing to say.
 *
 *  A warning, never a blocker -- which is the whole change in posture. The step
 *  used to refuse to advance, and the refusal named a field the person was
 *  looking at, on a page that had already let them empty it. The bundle is
 *  generated now, and says of itself that it is unfinished. */
export function placeholderWarning(blanks: string[]): string {
  if (!blanks.length) return "";
  // Each field beside its own marker, because the marker is the half that is
  // useful away from this page: it is what somebody greps the bundle for once
  // the zip has been handed on. Paired rather than listed twice, or the
  // sentence is two lists the reader has to line up by position.
  const named = blanks.map((k) => `${k} (${marker(k)})`);
  const list = named.length === 1 ? named[0]
    : `${named.slice(0, -1).join(", ")} and ${named[named.length - 1]}`;
  const is = blanks.length === 1 ? "is" : "are";
  return `${list} ${is} empty, so the bundle will carry ${blanks.length === 1
    ? "that marker" : "those markers"} instead. It cannot be applied until `
    + `${blanks.length === 1 ? "it is" : "they are"} filled in — here, or in `
    + `the files afterwards.`;
}

// -- the download step's list -------------------------------------------------
// The step above says this per form; the download step says it once, over the
// whole bundle, as one row per field. It used to be four separate blocks -- the
// token, the identity, the option blanks and an unfinished group -- stacked in
// amber, which is how a bundle that generates perfectly well came to look like
// four errors. One list, one severity: every marker has to be filled in before
// the bundle is applied, and which of them the API server happens to stop first
// is the README's subject rather than this step's.

/** One field left blank, as the download step lists it. */
export interface Gap {
  /** The option key, dotted where it is nested -- what the configure step calls
   *  the field, and what `PLACEHOLDER_SOURCE` is keyed by. */
  key: string;
  /** What the bundle will carry in its place. Built here rather than read off
   *  `source`, so a row is complete before /api/placeholders answers and does
   *  not change when it does. */
  marker: string;
  /** Where the value comes from, when that has been read. **Absent is not
   *  empty**: a key the served table has no entry for, or a table that has not
   *  arrived, leaves this undefined and the row renders the field and the
   *  marker alone -- which is true either way. An empty string would be the
   *  generator saying there is no answer, which it never says. */
  source?: string;
  /** Which step fills it in: 1 is the agent step, where the identity and the
   *  credential are typed, 2 is configure. The row carries the way back,
   *  because the field it names is not on this step. */
  step: 1 | 2;
}

/** The identity and the credential, which are step 1's and are not options.
 *  `harbor_id` is a fact and `ship_id` is resolved out of one, so neither is in
 *  `blankRequired`; the token is not there either, because no form on the
 *  configure step holds it. */
const AGENT_STEP = new Set(["harbor_id", "ship_id", "auth_token"]);

/** Every field this bundle carries a marker for, in the order somebody would
 *  fill them in: the identity first, then the credential, then the options in
 *  the order the configure step asks for them.
 *
 *  `token` is `DownloadPlan.incomplete`, and its third answer is why this takes
 *  the whole value rather than a boolean. `"unread"` is no preview having
 *  landed, which is not the same as a bundle that carries a token -- listing a
 *  field as blank before anything has been read is a claim, and the row would
 *  appear on every bundle for the moment before the first preview answers and
 *  then vanish.
 *
 *  `sources` may be null throughout, and nothing here waits for it.
 */
export function gaps(
    idBlanks: string[], blanks: string[], token: boolean | "unread",
    sources: Record<string, PlaceholderSource> | null): Gap[] {
  const keys = [...idBlanks, ...(token === true ? ["auth_token"] : []),
                ...blanks];
  return keys.map((key) => {
    const source = sources?.[key]?.source;
    return {
      key, marker: marker(key), step: AGENT_STEP.has(key) ? 1 as const : 2,
      // Spread rather than `source: source` so an unread one is *absent* from
      // the object, not present and undefined. The distinction is the point of
      // the field, and `"source" in gap` has to be able to answer it.
      ...(source ? { source } : {}),
    };
  });
}

/** The bar over the folded list: what it is, in the fewest words that name the
 *  markers. Bounded, because a header cannot grow -- a truncated list ends
 *  mid-marker and says nothing about what it dropped, where a counted one is
 *  honest about the tail and opens onto it. */
export function gapSummary(list: Gap[]): string {
  const m = list.map((g) => g.marker);
  if (m.length === 0) return "";
  if (m.length === 1) return m[0];
  if (m.length <= 3) return `${m.slice(0, -1).join(", ")} and ${m[m.length - 1]}`;
  return `${m[0]}, ${m[1]} and ${m.length - 2} more`;
}
