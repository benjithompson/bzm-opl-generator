// What the bundle is, and what that leaves on screen.
//
// The choice used to be made on the download step, which is one step too late:
// the configure step above it asks for a namespace, a ServiceAccount, node
// selectors and engine limits, and a docker bundle carries none of them. So the
// control moved to the top of Configure and the form follows it.
//
// What a format drops is NOT declared here. It is generate.DOCKER_IGNORED,
// fetched from /api/docker-ignored, because a second copy of two dozen option
// keys in TypeScript is the drift the SV funcId list already cost once -- a key
// added to the generator would go on being offered for a format that ignores
// it, and nothing would say so. What is here is the two predicates the page
// hides by, and the three formats' own prose, which is UI text and has no
// counterpart in the generator.
//
// Nothing here imports React, reaches a route, or imports another module of
// ours, as in optionGroups.ts and sv.ts: that is what makes formats.test.ts
// possible without a DOM -- and what lets optionGroups.ts import *this*, which
// it does for the one option whose default depends on the format.

export interface OutputFormat {
  id: string;
  label: string;
  /** One line on what you get and how you install it. */
  hint: string;
}

/** The three, in the order the control shows them. Declared here rather than in
 *  the panel that renders them because two panels read the choice now: the
 *  control is on Configure and the install command is on Download. */
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

/** Is this a container on a host rather than objects in a cluster? The one
 *  place the format is compared to a literal: every other reader asks whether a
 *  particular option applies, which is the question that actually decides
 *  anything. */
export const isDocker = (format: string) => format === "docker";

/** {option: why} for the options this format drops, from /api/docker-ignored.
 *
 *  **Empty means the table has not been read**, and `optionApplies` treats that
 *  as "every option applies" -- the only safe way to be wrong about it. Guessed
 *  the other way, a Kubernetes bundle would lose its namespace field until a
 *  fetch landed, which is a required field missing from a form nobody can fix.
 *  Showing one field too many for a moment is the cheaper mistake. */
export type IgnoredOptions = Record<string, string>;

/** Does this option reach anything in a bundle of this format?
 *
 *  False is what hides a field. It is never a refusal: the generator carries
 *  an ignored option in profile.json and names it in the bundle's README, so a
 *  value set for Kubernetes and then switched to docker is kept and reported
 *  rather than wiped -- and `generate.ignored_options` is the same rule on that
 *  side, keeping the generator from refusing what it says it ignores. Hiding
 *  the control is the point: an option nobody can see cannot be believed to
 *  have been applied. */
export function optionApplies(
    key: string, format: string, ignored: IgnoredOptions): boolean {
  return !isDocker(format) || !(key in ignored);
}

/** `optionApplies` with the format and the table already answered. The page
 *  builds one and hands it down: every consumer asks whether a particular
 *  option reaches anything, and none of them has any other business knowing
 *  which format is selected. */
export type Applies = (key: string) => boolean;

/** Why this option reaches nothing here, or null where it does.
 *
 *  The other half of the served table, and the reason the *value* is fetched
 *  rather than only the keys: the generator already has a sentence for each of
 *  these, the bundle's README prints that sentence, and a form that hides the
 *  field wants to say the same thing. Restated in TypeScript it was already a
 *  verbatim copy of one of them, free to drift the day the generator's was
 *  edited. Lower case and unpunctuated, like every other hint on the page --
 *  which is how DOCKER_IGNORED is written. */
export type WhyIgnored = (key: string) => string | null;

export function whyIgnored(
    key: string, format: string, ignored: IgnoredOptions): string | null {
  return optionApplies(key, format, ignored) ? null : ignored[key] ?? null;
}

/** Does any of these options reach anything?
 *
 *  What a *section* of the form hides by, wherever the fields in it are not a
 *  declared group: the placement card owns the namespace and the service
 *  account, Advanced owns the platform and the UID. Whole means whole -- one
 *  key still reaching something keeps the section, with the rest of its fields
 *  hidden by the predicate itself. `optionGroups.groupsFor` is this over a
 *  group's declared `keys`. */
export const keysApply = (keys: string[], applies: Applies) =>
  keys.some(applies);
