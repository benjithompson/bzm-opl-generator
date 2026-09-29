// What the download button will do to the agent's credential, and what the
// server still holds of a token this app minted. Nothing here holds a token.
// How a bundle's token arrived is core's rule (TokenReport); this only says it
// before the click, and produces the request so no caller converts a flag.
import { TokenBranch, TokenReport, TokenRequest } from "./api";

/** What a rotation does, named against the agent it does it to. Shown before
 *  the click, the one moment it can still be reconsidered. */
export const rotateHazard = (shipId: string | null) =>
  `A new AUTH_TOKEN${shipId ? ` for agent ${shipId}` : ""} kills the current one `
  + "at once: anything already running on it answers 404 and sits at 0/1 "
  + "Running until this bundle is re-applied, Secret included.";

export interface DownloadPlan {
  /** What the next bundle request carries about the credential, sent as it stands. */
  request: TokenRequest;
  /** Beside the button: what the bundle will carry. */
  hint: string;
  /** Whether the bundle leaves the AUTH_TOKEN as a marker, or "unread" before
   *  the first preview. A string so `=== true` and `=== false` both miss it:
   *  the panel shows no row for it, and the hint stays cautious. */
  incomplete: boolean | "unread";
}

const CARRIES: Record<TokenBranch, string> = {
  given: "the generated AUTH_TOKEN",
  rotated: "a NEW AUTH_TOKEN, issued now",
  // No request from this page produces `reused`, but the server may still send
  // it, and a missing sentence would leave the line blank.
  reused: "the AUTH_TOKEN already in that folder",
  placeholder: "AUTH_TOKEN left as a placeholder — fill it in before applying",
};

/** What the next download will do, from the preview's own report. Before the
 *  first preview (`report` null) the hint assumes a placeholder and
 *  `incomplete` is "unread". */
export function downloadPlan(report: TokenReport | null): DownloadPlan {
  const branch = report?.branch ?? "placeholder";
  return { request: { rotate_token: false }, hint: CARRIES[branch],
           incomplete: report ? branch === "placeholder" : "unread" };
}


/** What the server said it holds for the selected agent: `asking` (answer
 *  outstanding), `held` (in the field), `none` (it holds none) or `unread` (it
 *  could not be asked). Only `none` may say a token cannot be read back. */
export type Recall = "asking" | "held" | "none" | "unread";

/** How the lookup's answer reads; a failed request never gets here. */
export const recalled = (answer: { auth_token: string | null }): Recall =>
  (answer.auth_token ? "held" : "none");

/** What to say beside an agent whose token field is empty, or null. */
export function recallNote(recall: Recall): string | null {
  if (recall === "unread") {
    // No claim about the agent; the app may hold its token and be unable to say.
    return "could not ask this app what it still holds for this agent — "
      + "paste the token, or try again";
  }
  if (recall === "none") {
    return "its token was issued once, at creation, and cannot be read back";
  }
  return null;
}
