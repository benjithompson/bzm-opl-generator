// What the bundle is, and what that leaves on screen. What a format drops is
// the generator's IGNORED_BY_FORMAT, served; this holds the predicates the page
// hides by and each format's UI text. No React, and no imports of ours.

interface OutputFormat {
  id: string;
  label: string;
  /** One line on what you get and how you install it. */
  hint: string;
}

/** The formats, in the order the control shows them. */
export const OUTPUT_FORMATS: OutputFormat[] = [
  {
    id: "manifests",
    label: "Kubernetes manifests",
    hint: "Flat YAML you kubectl apply. Live-testable with bzm-opl-gen livetest.",
  },
  {
    id: "helm",
    label: "Helm chart",
    hint: "The chart plus a values overlay from this account. helm install / upgrade.",
  },
  {
    id: "docker",
    label: "Docker",
    hint: "One agent as one container on a host. A docker run script, not a cluster.",
  },
];

/** Is this a container on a host rather than objects in a cluster? */
export const isDocker = (format: string) => format === "docker";

/** {option: why} for what one format drops; `{}` drops nothing. */
type IgnoredOptions = Record<string, string>;

/** {format: what it drops}, from /api/ignored-options. A format with no entry
 *  has not been read; `{}` has been read and drops nothing. Both show every field. */
export type IgnoredByFormat = Record<string, IgnoredOptions>;

/** What this format drops, or null where nothing has been read for it. The
 *  one reader of the table, so the two empties stay apart. */
export function ignoredFor(
    format: string, ignored: IgnoredByFormat): IgnoredOptions | null {
  return Object.prototype.hasOwnProperty.call(ignored, format)
    ? ignored[format] : null;
}

/** Does this option reach anything in a bundle of this format? False hides
 *  the field but never clears it: the generator keeps and reports an ignored
 *  option. */
export function optionApplies(
    key: string, format: string, ignored: IgnoredByFormat): boolean {
  const drops = ignoredFor(format, ignored);
  return drops === null || !(key in drops);
}

/** `optionApplies` with the format and the table bound. */
export type Applies = (key: string) => boolean;

/** Why this option reaches nothing here, or null: the generator's own
 *  sentence, as the bundle's README prints it. */
export type WhyIgnored = (key: string) => string | null;

export function whyIgnored(
    key: string, format: string, ignored: IgnoredByFormat): string | null {
  const drops = ignoredFor(format, ignored);
  return drops === null ? null : drops[key] ?? null;
}

/** Does any of these options reach anything? How a form section that is not a
 *  group hides; groupsFor is this over a group's keys. */
export const keysApply = (keys: string[], applies: Applies) =>
  keys.some(applies);
