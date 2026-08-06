// A location or agent that is gone, as opposed to one nothing could be read
// about.
//
// The page holds a location list for as long as it is open, and the account
// underneath it moves: a colleague deletes a location, an agent is removed in
// BlazeMeter's own UI, a scratch harbor is cleaned up. Acting on a row that is
// no longer there fails, and until this module it failed with whatever sentence
// BlazeMeter had written, at HTTP 502, indistinguishable from the API being
// unreachable -- which is the pair this codebase separates everywhere else
// ("could not read" and "there is nothing there" must never share a
// representation). The two have opposite remedies: press Refresh, or wait.
//
// A module rather than a check at each call site, and for the reason
// heartbeat.ts gives about the freshness rule: this is a rule, it has edge
// cases (a plain Error thrown by fetch itself, a 404 that is the SPA's static
// mount rather than an answer), and stated inline it would have no test of its
// own and be stated differently in the third place that needed it.
//
// The sentence names Refresh, never "reload the page". A reload is not the same
// action and is not free here: an AUTH_TOKEN that was *pasted* does not survive
// one (only tokens this app minted are recoverable, from the server's own
// store), so sending somebody to the browser's reload button to fix a stale list
// can cost them a credential they cannot read back.
import { ApiError } from "./api";

/** What was asked about, in the words the sentence uses. The two are separate
 *  because the remedy reads differently: a location that is gone takes its
 *  agents with it, an agent that is gone leaves a location still worth
 *  choosing from.
 *
 *  Which to pass is not a guess, and the containment settles the one case that
 *  looks ambiguous: a call naming a **ship** (status, regenerate) cannot tell a
 *  deleted agent from a deleted location -- BlazeMeter answers 404 to both --
 *  but "this agent no longer exists" is true either way, because an agent
 *  outlives its location nowhere. A call naming only a location (facts, create
 *  an agent in it) has no such doubt. So: name the *narrowest* thing the call
 *  was about, and the sentence is true whichever of them went. */
export type Subject = "location" | "agent";

/** Whether this failure is the thing being gone.
 *
 *  404 and nothing else. A 401 is a key the account has stopped accepting, a
 *  403 is an endpoint the account restricts and a 502 is BlazeMeter answering
 *  badly or not at all; none of them is evidence that anything was deleted, and
 *  reporting one as a deletion is the false "there is nothing there" this
 *  module exists to prevent. A failure that is not an ApiError at all -- fetch
 *  rejecting because the server is not running, a TypeError from somewhere
 *  else -- carries no status, so it is not this rule's business either. */
export function isGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

/** The sentence to show for a gone location or agent, or null where this
 *  failure is not that.
 *
 *  Null rather than a generic fallback: what to say about an ordinary failure
 *  is the caller's -- it already has BlazeMeter's own words and somewhere to
 *  put them -- and answering with a sentence here would have every caller
 *  render this module's wording over an error it knows more about. */
export function goneNotice(e: unknown, subject: Subject): string | null {
  if (!isGone(e)) return null;
  return `This ${subject} no longer exists in the account. Press Refresh above`
    + ` to re-read the private locations.`;
}

/** The same thing, discovered the other way: a Refresh came back and what was
 *  selected is not in the list.
 *
 *  A separate sentence because the remedy is not the same one. Nothing failed
 *  here and the account has just been re-read, so telling somebody to press
 *  Refresh would be telling them to repeat the action that found this out. */
export function vanishedNotice(subject: Subject): string {
  return `The ${subject} you had selected is no longer in the account.`
    + ` Choose another below.`;
}
