// What the download button is about to do to the agent's credential.
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
 *  nothing -- `reused` and `given` are still distinct answers about what this
 *  bundle carries, and the hint says which -- and it is why the report is still
 *  read rather than the branch being assumed.
 */
export function downloadPlan(report: TokenReport | null): DownloadPlan {
  const branch = report?.branch ?? "placeholder";
  return { request: { rotate_token: false }, hint: CARRIES[branch],
           incomplete: branch === "placeholder" };
}
