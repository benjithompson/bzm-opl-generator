// What the last download or save actually did.
//
// It was four pieces of state with four setters -- the credential report, the
// saved bundle, the download error and the save error -- and every call reset
// some of them by hand first. They are one fact with four spellings: an attempt
// either produced a bundle or failed to, and only one field of the answer is
// ever filled in. The residue is what made that worth fixing -- a save error
// left standing beside the next download's token report describes two different
// clicks, and no reader can tell which one is being reported.
//
// So: one record, built by one of the constructors below and never assembled
// field by field, in preflight.ts's shape (a state plus the named transitions
// that reach it). The page owns it; the download step reports the next one. That
// is what keeps the two calls beside the buttons that say what they cost, which
// is where CLAUDE.md wants every request that can touch the account.
import { SavedBundle, TokenReport } from "./api";

export interface Attempt {
  /** What happened to the credential, in core's own words. Said afterwards as
   *  well as before, because a rotation is worth confirming: the bundle just
   *  handed over is the only copy of that token. Carried by both the download
   *  and the save, since both can rotate. */
  token: TokenReport | null;
  /** Where a save landed, as the server expanded it -- which is the path a
   *  kubectl command can be copied against, and `~` is not. */
  saved: SavedBundle | null;
  /** Why the download failed. Two error fields rather than one because they are
   *  rendered in two places: a save is refused at its own button, next to the
   *  folder that was wrong. */
  downloadError: string | null;
  saveError: string | null;
}

/** Nothing attempted yet, and what a fresh attempt starts from -- so an answer
 *  never lands beside the previous attempt's leftovers. */
export const NO_ATTEMPT: Attempt = {
  token: null, saved: null, downloadError: null, saveError: null,
};

/** A zip handed to the browser. */
export const downloaded = (token: TokenReport): Attempt =>
  ({ ...NO_ATTEMPT, token });

/** A bundle written to a folder. The token report comes off the save itself:
 *  saving into a folder that already holds this ship's bundle reuses the token
 *  there, so what the save did to the credential is only knowable from its own
 *  answer. */
export const savedTo = (saved: SavedBundle): Attempt =>
  ({ ...NO_ATTEMPT, saved, token: saved.token });

export const downloadFailed = (why: string): Attempt =>
  ({ ...NO_ATTEMPT, downloadError: why });

export const saveFailed = (why: string): Attempt =>
  ({ ...NO_ATTEMPT, saveError: why });
