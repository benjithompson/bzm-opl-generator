// What to say about the page the server is serving. Of /api/build's four
// answers, `true` is a real defect and gets an alert; "unrecorded" means not
// checked and gets a quiet note; the other two say nothing.
import type { Staleness } from "./api";

/** How loudly to say it; the caller maps it to colour, icon and ARIA role. */
type Tone = "warning" | "note";

/** A sentence about the built page: the heading, the detail, and the command
 *  (rendered as code). */
interface BuildNotice {
  tone: Tone;
  heading: string;
  detail: string;
  command: string;
}

/** The one way to rebuild it, stated once. */
export const REBUILD = "cd frontend && npm run build";

/** What to show for `/api/build`'s `stale`, or null: nothing for a page that
 *  matches (`false`) or an installed wheel (`null`). */
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
