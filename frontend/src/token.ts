// What the download button is about to do to the agent's credential.
//
// Which of four ways a bundle's token arrived is core's rule and arrives on the
// answer (TokenReport) -- nothing here re-decides one. What is left is the
// question core cannot answer, because it is about a click that has not happened
// yet: this page holds a rotate choice, and the two of them together decide what
// to say *before* the request. A rotation announced afterwards is a post-mortem;
// the credential is already dead and the pod is already broken (#64).
//
// Plain data in, data out, and tested, because the three places this used to be
// decided -- the hint beside the button, the banner over it, and whether the
// request rotates at all -- are exactly the three that can disagree.
import { TokenBranch, TokenReport } from "./api";

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
  /** Whether asking for the bundle will issue a credential. */
  rotates: boolean;
  /** Beside the button: what the bundle will carry. */
  hint: string;
  /** Amber, before the click. Null when nothing is at stake. */
  warning: string | null;
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

/** What the next download or save will do, from the preview's own report plus the
 *  rotate choice on screen.
 *
 *  `report` is null only before the first preview lands. The branch it carries is
 *  the one a download would take *without* rotating, which is what makes it the
 *  right input here: the preview never rotates, so it is a free look at the
 *  answer.
 *
 *  A token in the form wins over the rotate box, and says so. That is core's
 *  first branch -- rotating would revoke the very token that was pasted, so it is
 *  answered rather than obeyed -- and a page that promised a rotation core will
 *  not perform would be describing a different bundle from the one it hands over.
 */
export function downloadPlan(
  report: TokenReport | null, rotate: boolean, shipId: string | null,
): DownloadPlan {
  const branch = report?.branch ?? "placeholder";
  if (branch === "given") {
    return {
      rotates: false,
      hint: CARRIES.given + (rotate
        ? " — the token in hand wins, so nothing will be issued" : ""),
      warning: null,
      incomplete: false,
    };
  }
  if (rotate) {
    return { rotates: true, hint: CARRIES.rotated,
             warning: rotateHazard(shipId), incomplete: false };
  }
  return { rotates: false, hint: CARRIES[branch], warning: null,
           incomplete: branch === "placeholder" };
}
