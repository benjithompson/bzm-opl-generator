// What the last download did, as one record built by one constructor per
// outcome, so a new answer never lands beside the previous click's leftovers.
import { TokenReport } from "./api";

export interface Attempt {
  /** What happened to the credential, in core's own words. */
  token: TokenReport | null;
  /** Why the download failed. */
  downloadError: string | null;
}

/** Nothing attempted yet; every attempt starts from it. */
export const NO_ATTEMPT: Attempt = { token: null, downloadError: null };

/** A zip handed to the browser. */
export const downloaded = (token: TokenReport): Attempt =>
  ({ ...NO_ATTEMPT, token });

export const downloadFailed = (why: string): Attempt =>
  ({ ...NO_ATTEMPT, downloadError: why });
