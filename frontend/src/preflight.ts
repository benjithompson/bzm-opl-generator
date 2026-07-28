// The cluster preflight panel, minus its JSX: how a verdict list reads, what a
// picked file has to be before it is worth sending, and what an import leaves
// behind when it is refused.
//
// No verdict is reached here. Every one of them is doctor's, arrives over
// /api/preflight in the order it decided, and is rendered in that order --
// provenance first, because it qualifies everything after it. What this file
// owns is the presentation, which is why it is plain data in, plain data out
// like optionGroups.ts and needs no DOM to test.

import { CheckStatus, PreflightCheck, PreflightOut } from "./api";

/** The header over the verdict list: what was imported, stated where it cannot
 *  be read past.
 *
 *  All three of these are already in the leading verdict's prose, and that is
 *  not enough — prose in a list of ten verdicts is exactly where a file
 *  collected by somebody with almost no access passes for a clean bill of
 *  health (#53). Nothing is decided here: the facts are doctor's, off the
 *  document, and this only puts them in the words the header uses. */
export interface EvidenceHeader {
  /** When the collector ran, or that the file did not record it. */
  collected: string;
  /** The namespace the FILE describes — not the one being preflighted. */
  describes: string;
  /** The two are different namespaces, so every namespaced verdict below
   *  describes the other one. */
  elsewhere: boolean;
  unreadable: string[];
  /** The unreadable sections as a sentence, or "" when there were none. Said
   *  in the same terms doctor uses, because it is the same distinction: not
   *  read is unverified, not absent. */
  unreadableLine: string;
}

export function evidenceHeader(out: PreflightOut): EvidenceHeader {
  const { collected_at, namespace, unreadable } = out.evidence;
  return {
    collected: collected_at ?? "an unrecorded time",
    describes: namespace ?? "an unnamed namespace",
    // A file that names no namespace is not a mismatch — there is nothing to
    // mismatch with, and warning about one would be a complaint nobody can act
    // on.
    elsewhere: namespace != null && namespace !== out.namespace,
    unreadable,
    unreadableLine: unreadable.length
      ? `could not read ${unreadable.join(", ")} — reported below as unverified, `
        + `not as absent`
      : "",
  };
}

export const EVIDENCE_SCRIPT = "scripts/bzm-cluster-evidence.sh";

/** How each verdict reads: the badge on its own row, and the colour the summary
 *  takes from the worst of them. `label` matters as much as either: a list
 *  where WARN is only amber is a list of passes to anyone who cannot see the
 *  difference, and these get read on projectors. */
export const STATUS_STYLE: Record<
    CheckStatus, { label: string; badge: string; text: string }> = {
  PASS: { label: "PASS", badge: "bg-emerald-100 text-emerald-700",
          text: "text-emerald-700" },
  WARN: { label: "WARN", badge: "bg-amber-100 text-amber-800",
          text: "text-amber-700" },
  FAIL: { label: "FAIL", badge: "bg-red-100 text-red-700",
          text: "text-red-600" },
};

/** Worst first -- the order the panel's own tone is picked in. */
const SEVERITY: CheckStatus[] = ["FAIL", "WARN", "PASS"];

export function countByStatus(checks: PreflightCheck[]): Record<CheckStatus, number> {
  const counts: Record<CheckStatus, number> = { PASS: 0, WARN: 0, FAIL: 0 };
  for (const c of checks) counts[c.status] += 1;
  return counts;
}

/** The tone for the panel as a whole, or null when nothing has been imported --
 *  which is not a pass. */
export function worstStatus(checks: PreflightCheck[]): CheckStatus | null {
  return SEVERITY.find((s) => checks.some((c) => c.status === s)) ?? null;
}

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`;

/** The one-line summary, in doctor's own terms. The consequence is stated only
 *  where something FAILed: an evidence file with sections nobody could read is
 *  all warnings, and ending that with "a test would not start" would turn a
 *  thin file into a rejection of the cluster. */
export function verdictLine(checks: PreflightCheck[]): string {
  const n = countByStatus(checks);
  const line = `${plural(n.PASS, "passed", "passed")}, `
    + `${plural(n.WARN, "warning", "warnings")}, `
    + (n.FAIL ? plural(n.FAIL, "failure", "failures") : "no failures");
  return n.FAIL
    ? `${line} — a test would not start on this location as configured`
    : line;
}

export type EvidenceRead = { doc: unknown } | { error: string };

/** Whether the picked file is JSON at all, and nothing more. Which documents
 *  count as evidence is one judgement and it lives in doctor: a copy here would
 *  start refusing files the command accepts, and it is the server's refusal --
 *  which names the schema it found and the one it wanted -- that has to reach
 *  the user. */
export function readEvidence(name: string, text: string): EvidenceRead {
  try {
    return { doc: JSON.parse(text) };
  } catch {
    return { error: `${name} is not valid JSON. Pick the file ${EVIDENCE_SCRIPT} `
      + `wrote on a machine with cluster access, unedited.` };
  }
}

/** What the panel holds. `doc` is kept rather than only its verdicts, because
 *  the preflight re-runs against it on every option change -- the verdicts have
 *  to describe the configuration on screen, not the one it had when the file
 *  was picked. */
export interface PreflightState {
  /** The file name, for saying which file the verdicts are from. */
  file: string | null;
  doc: unknown | null;
  out: PreflightOut | null;
  error: string | null;
}

export const NO_PREFLIGHT: PreflightState =
  { file: null, doc: null, out: null, error: null };

/** A file that produced verdicts. Committed only once the server has accepted
 *  it, so a refused file never displaces one that worked. */
export function imported(
    file: string, doc: unknown, out: PreflightOut): PreflightState {
  return { file, doc, out, error: null };
}

/** The same file, judged again against the configuration as it now stands. */
export function rechecked(prev: PreflightState, out: PreflightOut): PreflightState {
  return { ...prev, out, error: null };
}

/** A file that was not evidence, or a re-run the server refused. Adds the
 *  message and takes nothing away: the verdicts on screen are true of the file
 *  that produced them, and picking the wrong file next must not cost you the
 *  preflight you already had. */
export function refused(prev: PreflightState, error: string): PreflightState {
  return { ...prev, error };
}
