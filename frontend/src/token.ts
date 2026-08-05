// What the download button is about to do to the agent's credential, and what
// this app still holds of one it minted.
//
// Two questions about one value, so one module: what a click will cost, and
// what a refresh left behind (#123). Both are answers *about* a credential and
// neither is one -- nothing here holds a token, and the second half deals only
// in which of four things the server said.
//
// Which of four ways a bundle's token arrived is core's rule and arrives on the
// answer (TokenReport) -- nothing here re-decides one. What is left is what to
// say about it *before* the click, which is the only moment that helps: a
// credential problem announced afterwards is a post-mortem, and the pod is
// already broken (#64).
//
// It took a rotate choice too, because the download step had a box that minted
// one. That box is gone -- minting is step 1's, on the agent the credential
// belongs to, where what it kills is on screen -- so the page never asks for a
// rotation and this never describes one. `rotate_token: false` is still *sent*
// rather than assumed: it is the request, and #104 is about that being one
// value rather than a flag each caller converts.
//
// Plain data in, data out, and tested, because the places this used to be
// decided -- the hint beside the button and whether the request rotates at all
// -- are exactly the ones that can disagree.
//
// What it hands back for the third of those is the *request*, not a flag (#104).
// A boolean is advice: the two buttons each turned it into a `rotate_token`
// argument, and the conversion sat outside everything this module's tests can
// reach -- so the one failure the module exists to prevent lived in the two
// lines it did not own. A TokenRequest is spread into the body as it stands,
// which leaves nothing to convert and no second place to convert it differently.
import { TokenBranch, TokenReport, TokenRequest } from "./api";

/** What a rotation will do, named against the agent it will do it to.
 *
 *  The server says this too (core.rotation_warning, before it mints), and this
 *  copy is not that one: it is said while the box is being ticked, which is the
 *  only moment at which anybody can still decide not to. */
export const rotateHazard = (shipId: string | null) =>
  `A new AUTH_TOKEN${shipId ? ` for agent ${shipId}` : ""} kills the current one `
  + "at once: anything already running on it answers 404 and sits at 0/1 "
  + "Running until this bundle is re-applied, Secret included.";

export interface DownloadPlan {
  /** What the next bundle request carries about the credential. Handed to
   *  api.downloadZip whole -- it is the request, so the button decides nothing
   *  about it. */
  request: TokenRequest;
  /** Beside the button: what the bundle will carry. */
  hint: string;
  /** The bundle cannot be applied as it stands, so say so over the button
   *  rather than in a README nobody opens after the download. */
  incomplete: boolean;
}

const CARRIES: Record<TokenBranch, string> = {
  given: "the generated AUTH_TOKEN",
  rotated: "a NEW AUTH_TOKEN, issued now",
  // No request from this page produces `reused` any more -- Save to folder went
  // to the CLI, so `out_dir` is a constant null. The sentence stays because the
  // branch is the server's to send and the header it arrives in is cast without
  // validation: dropping it here would not stop it arriving, only leave the
  // line beside the button blank when it did.
  reused: "the AUTH_TOKEN already in that folder",
  placeholder: "AUTH_TOKEN left as a placeholder — fill it in before applying",
};

/** What the next download will do, from the preview's own report.
 *
 *  `report` is null only before the first preview lands, and reads as the
 *  placeholder: a bundle claimed to carry a token it may not have is the
 *  failure worth avoiding.
 *
 *  Every branch sends the same request now. That is not the same as sending
 *  nothing -- `given` and `placeholder` are still distinct answers about what this
 *  bundle carries, and the hint says which -- and it is why the report is still
 *  read rather than the branch being assumed.
 */
export function downloadPlan(report: TokenReport | null): DownloadPlan {
  const branch = report?.branch ?? "placeholder";
  return { request: { rotate_token: false }, hint: CARRIES[branch],
           incomplete: branch === "placeholder" };
}


/** What the server said it still holds for the selected agent (#123).
 *
 *  Four states, because the question is asked over a request and a request has
 *  a before as well as three afters:
 *
 *    asking  -- the answer is outstanding. Not "none": for the moment before it
 *               lands the field is empty for a reason that has nothing to do
 *               with the agent, and the sentence for `none` would be a claim
 *               made without having asked.
 *    held    -- there is one, and it is already in the field. Silently, on
 *               purpose: a token claims nothing about the world, so there is
 *               nothing to caveat.
 *    none    -- this process holds no token for that ship. A ship this app never
 *               minted for, one whose credential was typed over, and a server
 *               that has restarted since, are all honestly this -- so the
 *               sentence is about what is held, never about what was minted.
 *    unread  -- the server could not be asked. **Not `none`.** This is the
 *               distinction this codebase keeps everywhere: an agent nobody
 *               minted for and an agent nobody could ask about are different
 *               answers, and only one of them is entitled to say a credential
 *               cannot be read back. */
export type Recall = "asking" | "held" | "none" | "unread";

/** How the store's answer reads. `auth_token` is null for "holds none"; a
 *  failed request never reaches here, because it is not an answer. */
export const recalled = (answer: { auth_token: string | null }): Recall =>
  (answer.auth_token ? "held" : "none");

/** What to say beside an agent with no credential in hand, or null for nothing.
 *
 *  Only reached where the field is empty -- a token in it explains itself. Kept
 *  here rather than as a ternary at the field because the two sentences it
 *  chooses between are the two states that must never be confused, and one of
 *  them was the only one that existed before there was a store to ask. */
export function recallNote(recall: Recall): string | null {
  if (recall === "unread") {
    // No claim about the agent: the app may well be holding this one's token
    // and simply be unable to say so. What it offers instead is the way on,
    // which is the same way on an agent it never created has.
    return "could not ask this app what it still holds for this agent — "
      + "paste the token, or try again";
  }
  if (recall === "none") {
    return "its token was issued once, at creation, and cannot be read back";
  }
  return null;
}
