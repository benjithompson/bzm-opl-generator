/** Required fields left blank, and the marker that stands in for one.
 *
 *  A blank required field is sent as `<KEY>`, so applying the bundle fails
 *  naming the field instead of deploying something subtly wrong. The marker is
 *  applied only to what is sent, never to the options the page holds.
 *  required_fields.REQUIRED_TEXT covers what the options alone show is required; this
 *  adds what only a switch on the page shows. */
import { Options, PlaceholderSource } from "./api";
import { Applies } from "./formats";
import { GroupId, OPTION_GROUPS, serviceAccountOk } from "./optionGroups";

/** The marker for an option key: upper case, a dotted key joined by an
 *  underscore (`proxy.https` gives `<PROXY_HTTPS>`). Same rule as
 *  markers.marker, held equal through fixtures.ts. */
export function marker(key: string): string {
  return `<${key.replace(/\./g, "_").toUpperCase()}>`;
}

/** The core fields, which belong to no group: they are on the step itself. */
const CORE: { key: string; blank: (o: Options) => boolean }[] = [
  { key: "namespace", blank: (o) => !String(o.namespace ?? "").trim() },
  { key: "service_account_name", blank: (o) => !serviceAccountOk(o) },
];

/** Every required field left blank, in form order: the core two, then each
 *  group's. Only fields this format shows (`applies`), so a warning never names
 *  one that is not on screen. */
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

/** The options as sent, each blank required field carrying its marker. The same
 *  object when there is nothing to fill, so the preview does not re-POST. */
export function withPlaceholders(o: Options, blanks: string[]): Options {
  if (!blanks.length) return o;
  const out: Record<string, unknown> = { ...o };
  for (const key of blanks) {
    // The whole dotted key's marker, as generate's placeholder_options reports it.
    const mark = marker(key);
    const [head, sub] = key.split(".");
    if (sub === undefined) { out[head] = mark; continue; }
    out[head] = { ...(out[head] as object ?? {}), [sub]: mark };
  }
  return out as Options;
}

/** The configure step's warning about blank fields, or "". A warning, never a
 *  blocker: the bundle is generated and says it is unfinished. */
export function placeholderWarning(blanks: string[]): string {
  if (!blanks.length) return "";
  // Each field beside its marker, which is what somebody greps the bundle for.
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
// One row per field the bundle carries a marker for, all one severity.

/** One field left blank, as the download step lists it. */
export interface Gap {
  /** The option key, dotted where nested, as PLACEHOLDER_SOURCE keys it. */
  key: string;
  /** What the bundle carries in its place, built here so the row is complete
   *  before /api/placeholders answers. */
  marker: string;
  /** Where the value comes from, once read. Absent, not empty, when unread. */
  source?: string;
  /** The step that fills it in (1: agent, 2: configure), for the way back. */
  step: 1 | 2;
}

/** Step 1's fields: the identity and the credential, which are not options. */
const AGENT_STEP = new Set(["harbor_id", "ship_id", "auth_token"]);

/** Every field the bundle carries a marker for, in fill-in order: identity,
 *  credential, options. `token` is DownloadPlan.incomplete; "unread" lists no
 *  token row. `sources` may be null. */
export function gaps(
    idBlanks: string[], blanks: string[], token: boolean | "unread",
    sources: Record<string, PlaceholderSource> | null): Gap[] {
  const keys = [...idBlanks, ...(token === true ? ["auth_token"] : []),
                ...blanks];
  return keys.map((key) => {
    const source = sources?.[key]?.source;
    return {
      key, marker: marker(key), step: AGENT_STEP.has(key) ? 1 as const : 2,
      // Absent rather than undefined, so `"source" in gap` tells unread apart.
      ...(source ? { source } : {}),
    };
  });
}

/** The folded list's header: the markers, with a long tail counted rather
 *  than truncated. */
export function gapSummary(list: Gap[]): string {
  const m = list.map((g) => g.marker);
  if (m.length === 0) return "";
  if (m.length === 1) return m[0];
  if (m.length <= 3) return `${m.slice(0, -1).join(", ")} and ${m[m.length - 1]}`;
  return `${m[0]}, ${m[1]} and ${m.length - 2} more`;
}
