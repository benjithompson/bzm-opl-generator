/** Required fields that were left blank, and the marker that stands in for one.
 *
 *  The problem this solves is an empty text box that looks like an answer. A
 *  namespace nobody typed used to block the step; a private-registry host
 *  nobody typed did not, and the bundle came out with no private registry and
 *  nothing anywhere saying so -- the group's switch was on, the field was
 *  empty, and empty is what "not using one" looks like in the options. Both
 *  become the same thing here: the field carries `<PLACEHOLDER>` into the
 *  bundle, the README names it, and applying it fails with the field named
 *  rather than deploying something subtly wrong.
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
import { Options } from "./api";
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

/** The marker still emitted for every blank field (#244). It is what `marker`
 *  would produce for an option called `placeholder`, so every reader that
 *  recognises `<KEY>` already recognises it; #245 replaces it with the per-key
 *  one and this goes. */
export const PLACEHOLDER = "<PLACEHOLDER>";

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
    const [head, sub] = key.split(".");
    if (sub === undefined) { out[head] = PLACEHOLDER; continue; }
    out[head] = { ...(out[head] as object ?? {}), [sub]: PLACEHOLDER };
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
  const list = blanks.length === 1 ? blanks[0]
    : `${blanks.slice(0, -1).join(", ")} and ${blanks[blanks.length - 1]}`;
  const is = blanks.length === 1 ? "is" : "are";
  return `${list} ${is} empty, so the bundle will carry ${PLACEHOLDER} `
    + `instead. It cannot be applied until ${blanks.length === 1
      ? "it is" : "they are"} filled in — here, or in the files afterwards.`;
}
