// What to say about the page the server is serving (#238).
//
// `/api/build` answers four things about the built page, and only one of them
// is a defect. A module rather than a ternary in the header, for the reason
// stale.ts gives: this is a rule with edge cases -- two answers say nothing at
// all, and the two that do say something must not look alike -- and stated
// inline in JSX it would have no test of its own.
//
// The tone is the load-bearing part. `true` is the failure #224 was, where a
// page older than its server showed every option for a docker bundle and the
// server was the last thing suspected, so it is an alert. `"unrecorded"` is
// nothing known to be wrong: the page records nothing about what it was built
// from, so it has not been checked. Dressing that as a warning would put an
// amber bar on every checkout carrying a page built before #237, and a banner
// that cries wolf is one people learn to ignore -- which is precisely what the
// failure it exists for cannot afford.
import type { Staleness } from "./api";

/** How loudly to say it. Two words rather than a boolean, because what a caller
 *  does with it is a colour, an icon and an ARIA role, and "not a warning" is
 *  not the same instruction as "the opposite of a warning". */
export type Tone = "warning" | "note";

/** A sentence about the built page, in three parts: the caller renders the
 *  heading strongly, the detail beside it and the command as code. Split rather
 *  than one string because the command is markup on screen and a bare string
 *  would have every caller find it again with a regex. */
export interface BuildNotice {
  tone: Tone;
  heading: string;
  detail: string;
  command: string;
}

/** The one way to rebuild it, stated once. */
export const REBUILD = "cd frontend && npm run build";

/** What to show for `/api/build`'s `stale`, or null for nothing to show.
 *
 *  Null for two of the four, and they are null for different reasons rather
 *  than by being lumped together: `false` is a page that was compared and
 *  matches, which needs no sentence, and `null` is a wheel, where there are no
 *  sources for the question to be about and a sentence would be about nothing.
 *  Neither is a state anybody has to act on, and this is the only place that
 *  decides so. */
export function buildNotice(stale: Staleness): BuildNotice | null {
  if (stale === true) {
    return {
      tone: "warning",
      heading: "This page was not built from the code serving it.",
      detail: "Options a format hides may still be shown, and a bundle"
        + " downloaded here may be missing files. Rebuild it with",
      command: REBUILD,
    };
  }
  if (stale === "unrecorded") {
    return {
      tone: "note",
      heading: "This page records nothing about what it was built from.",
      detail: "It was built before that record existed, so whether it matches"
        + " the code serving it has not been checked. A rebuild answers the"
        + " question:",
      command: REBUILD,
    };
  }
  return null;
}
