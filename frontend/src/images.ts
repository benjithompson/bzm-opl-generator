// The images view's words and exports, built from the served rows only. The
// rows carry every fact; nothing here restates a generator rule, and a registry
// field that was not read is said to be unread, never shown as empty or zero.
import { ImageRow, ImagesAnswer } from "./api";

/** A funcId's display name, or the funcId where nothing names it. */
export type LabelOf = (funcId: string) => string;

/** The rows by category, in the order the server sent them. */
export function groupByCategory(rows: ImageRow[]): { category: string; rows: ImageRow[] }[] {
  const groups = new Map<string, ImageRow[]>();
  for (const r of rows) {
    const g = groups.get(r.category);
    if (g) g.push(r); else groups.set(r.category, [r]);
  }
  return [...groups].map(([category, rs]) => ({ category, rows: rs }));
}

/** The first twelve hex digits, as `docker images` shows a digest. */
export function shortDigest(digest: string): string {
  return digest.replace(/^sha256:/, "").slice(0, 12);
}

/** A registry field in words, or null where it was read and has a value the
 *  caller formats. "not read" and "not reported" are different answers. */
function registryGap(row: ImageRow, value: unknown): string | null {
  if (row.registry_state === "unread") return "not read";
  if (row.registry_state === "not-asked") return "not asked";
  return value == null ? "not reported" : null;
}

export function sizeText(row: ImageRow): string {
  return registryGap(row, row.size_mb)
    ?? `${Math.round(row.size_mb as number).toLocaleString()} MB`;
}

export function digestText(row: ImageRow): string {
  return registryGap(row, row.digest) ?? shortDigest(row.digest as string);
}

/** What one row's tag needs from the reader. `pinned` stands alone; the other
 *  two can both apply. */
export type TagNote =
  | { kind: "pinned"; text: string }
  | { kind: "mutable"; text: string }
  | { kind: "newer"; text: string };

export function tagNotes(row: ImageRow): TagNote[] {
  const notes: TagNote[] = [];
  if (row.update_available && row.newest_tag) {
    notes.push({ kind: "newer", text: `newer tag available: ${row.newest_tag}` });
  }
  if (row.tag_mutable) {
    notes.push({ kind: "mutable",
      text: `the tag ${row.tag} names a different image after each release;`
        + " mirror by digest or re-check after an upgrade" });
  }
  return notes.length ? notes : [{ kind: "pinned", text: "pinned" }];
}

/** Every reference, one per line, for a pull script or a ticket. */
export function refsText(rows: ImageRow[]): string {
  return rows.map((r) => r.ref).join("\n") + (rows.length ? "\n" : "");
}

/** The heading line, plain prose. */
export function sourceHeading(answer: ImagesAnswer): string {
  return answer.source === "location" && answer.location
    ? `Images for location ${answer.location.name} (read from your account)`
    : "BlazeMeter's image catalogue";
}

/** Why the location's version list is not the whole story, or null where it is. */
export function listNotice(answer: ImagesAnswer): string | null {
  switch (answer.image_list_state) {
    case "unread":
      return "The location's version list could not be read from BlazeMeter."
        + " Each row says where its tag came from; check the tags before you mirror them.";
    case "no-agent":
      return "This location has no agent yet, so BlazeMeter has no version list for it."
        + " Rows from the catalogue can carry a tag such as latest; check them"
        + " again once the first agent reports.";
    default:
      return null;
  }
}

/** Why digest, size or newest tag are missing, or null where the lookup answered. */
export function registryNotice(answer: ImagesAnswer): string | null {
  const { state, detail } = answer.registry_lookup;
  const why = detail ? ` ${detail}` : "";
  if (state === "unread") {
    return "The public registry could not be read, so digest, size and newest tag"
      + ` are not known for any image.${why}`;
  }
  if (state === "partial") {
    return "The public registry answered for some images only. The rows it did"
      + ` not answer for are marked not read.${why}`;
  }
  return null;
}

/** Tags move with BlazeMeter's releases; `registry` is the bundle's mirror, or
 *  null for a sample value. */
export function driftNote(registry: string | null): { text: string; command: string } {
  return {
    text: "Tags follow BlazeMeter releases, so a mirror goes out of date after an"
      + " upgrade. Check it again after each one:",
    command: `bzm-opl-gen images --verify ${registry || "<registry>"}`,
  };
}

/** Where a row's tag came from, in words. */
export const SOURCE_TEXT: Record<ImageRow["source"], string> = {
  "location-versions": "the location's version list",
  "agent-inventory": "a running agent's inventory",
  catalogue: "the catalogue",
};

/** Yes, no, or blank where the catalogue cannot say. */
const requiredText = (r: ImageRow) =>
  r.required === null ? "" : r.required ? "yes" : "no";

const CSV_COLUMNS: [string, (r: ImageRow, labelOf: LabelOf) => string][] = [
  ["ref", (r) => r.ref],
  ["repo", (r) => r.repo],
  ["tag", (r) => r.tag],
  ["category", (r) => r.category],
  ["functionalities", (r, labelOf) => r.functionalities.map(labelOf).join("; ")],
  ["purpose", (r) => r.purpose],
  ["purpose_verified", (r) => (r.verified ? "yes" : "no")],
  ["pulled_when", (r) => r.pulled_when],
  ["required", requiredText],
  ["tag_mutable", (r) => (r.tag_mutable ? "yes" : "no")],
  ["tag_source", (r) => r.source],
  // Beside the registry fields, so a blank one is read against it.
  ["registry_state", (r) => r.registry_state],
  ["digest", (r) => r.digest ?? ""],
  ["size_mb", (r) => (r.size_mb == null ? "" : String(r.size_mb))],
  ["newest_tag", (r) => r.newest_tag ?? ""],
  ["update_available", (r) => (r.update_available == null ? ""
    : r.update_available ? "yes" : "no")],
];

const csvCell = (v: string) =>
  (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

/** RFC 4180, one row per image. Registry fields are blank unless
 *  `registry_state` is read, which is its own column. */
export function toCsv(rows: ImageRow[], labelOf: LabelOf): string {
  const lines = [CSV_COLUMNS.map(([h]) => h).join(",")];
  for (const r of rows) {
    lines.push(CSV_COLUMNS.map(([, f]) => csvCell(f(r, labelOf))).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

const mdCell = (v: string) => v.replace(/\|/g, "\\|").replace(/\s*\n\s*/g, " ");

/** A document for a ticket: the source, what could not be read, then one table
 *  per category. */
export function toMarkdown(answer: ImagesAnswer, labelOf: LabelOf,
                           registry: string | null): string {
  const out = [`# ${sourceHeading(answer)}`, ""];
  if (answer.source === "catalogue") {
    out.push("Built-in list. Tags can be latest; a location's own versions come"
      + " from the account.", "");
  }
  for (const n of [listNotice(answer), registryNotice(answer)]) {
    if (n) out.push(`> ${n}`, "");
  }
  const showRequired = answer.images.some((r) => r.required !== null);
  for (const g of groupByCategory(answer.images)) {
    out.push(`## ${g.category}`, "");
    out.push("| Image | Functionalities | Purpose | Pulled | Size | Digest | Tag |"
      + (showRequired ? " Required |" : ""));
    out.push("|---|---|---|---|---|---|---|" + (showRequired ? "---|" : ""));
    for (const r of g.rows) {
      const cells = [
        `\`${r.ref}\``,
        r.functionalities.map(labelOf).join(", "),
        r.purpose + (r.verified ? "" : " (inferred)"),
        r.pulled_when,
        sizeText(r),
        r.digest && r.registry_state === "read" ? `\`${r.digest}\`` : digestText(r),
        tagNotes(r).map((t) => t.text).join("; "),
      ];
      if (showRequired) cells.push(requiredText(r));
      out.push("| " + cells.map(mdCell).join(" | ") + " |");
    }
    out.push("");
  }
  const drift = driftNote(registry);
  out.push(`${drift.text} \`${drift.command}\``, "");
  return out.join("\n");
}

/** The download's name, without extension. */
export function fileStem(answer: ImagesAnswer): string {
  const name = answer.source === "location" && answer.location
    ? answer.location.name : "catalogue";
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `bzm-opl-images-${slug || "location"}`;
}
