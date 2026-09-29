// A location or agent that is gone, told apart from one nothing could be read
// about: the remedies are opposite (press Refresh, or wait). The sentences name
// Refresh and never a page reload, which would lose a pasted AUTH_TOKEN.
import { ApiError } from "./api";

/** What the call was about. Name the narrowest thing: a 404 on an agent call
 *  may mean its location went, and "this agent no longer exists" is true either way. */
type Subject = "location" | "agent";

/** Is this failure the thing being gone? A 404 from this API and nothing else:
 *  401, 403, 502 and a status-less fetch failure say nothing was deleted. */
export function isGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

/** The sentence for a gone location or agent, or null where the failure is
 *  something else and the caller's own message applies. */
export function goneNotice(e: unknown, subject: Subject): string | null {
  if (!isGone(e)) return null;
  return `This ${subject} no longer exists in the account. Press Refresh above`
    + ` to re-read the private locations.`;
}

/** The same, found by a Refresh that no longer lists the selection. Nothing
 *  failed, so it does not say to press Refresh. */
export function vanishedNotice(subject: Subject): string {
  return `The ${subject} you had selected is no longer in the account.`
    + ` Choose another below.`;
}
