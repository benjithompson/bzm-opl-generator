// What the last download actually did.
//
// It was four pieces of state with four setters -- the credential report, the
// saved bundle, the download error and the save error -- and every call reset
// some of them by hand first. They are one fact with several spellings: an
// attempt either produced a bundle or failed to, and only one field of the
// answer is ever filled in. The residue is what made that worth fixing -- a
// save error left standing beside the next download's token report describes
// two different clicks, and no reader can tell which one is being reported.
//
// So: one record, built by one of the constructors below and never assembled
// field by field: a state plus the named transitions that reach it. The page
// owns it; the download step reports the next one.
// That is what keeps the call beside the button that says what it costs, which
// is where CLAUDE.md wants every request that can touch the account.
//
// Two of the four fields went with the Save to folder button: writing a bundle
// into a directory is the CLI's (`generate -o`) and the MCP server's
// (`opl_bundle`), and this step hands over a zip. The shape is why that was
// cheap -- one fact, one constructor per outcome, so removing an outcome was
// deleting a constructor rather than unpicking which resets still had to run.
import { TokenReport } from "./api";

export interface Attempt {
  /** What happened to the credential, in core's own words. Said afterwards as
   *  well as before, because a rotation is worth confirming: the bundle just
   *  handed over is the only copy of that token. */
  token: TokenReport | null;
  /** Why the download failed. */
  downloadError: string | null;
}

/** Nothing attempted yet, and what a fresh attempt starts from -- so an answer
 *  never lands beside the previous attempt's leftovers. */
export const NO_ATTEMPT: Attempt = { token: null, downloadError: null };

/** A zip handed to the browser. */
export const downloaded = (token: TokenReport): Attempt =>
  ({ ...NO_ATTEMPT, token });

export const downloadFailed = (why: string): Attempt =>
  ({ ...NO_ATTEMPT, downloadError: why });
