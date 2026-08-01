// What the imported cluster evidence implies about the configuration, and the
// only place it can be acted on. Sits under the preflight verdicts because it
// is the same file answering the question that comes first: not "would this
// survive the cluster" but "how should it have been configured".
//
// Every judgement on screen arrives on the row -- the strength from suggest.py,
// the state from suggest.merge(), every value already written the way
// profile.json would carry it, and the sentence for a row that cannot be
// offered. What is left to decide here is the action, in offer(). This file is
// JSX over those, which is why its rules are tested in suggestions.test.ts with
// no DOM in sight.

import { Options, Suggestion } from "./api";
import {
  Applied, canUndo, clipValue, offer, STRENGTH_STYLE, suggestionLine,
} from "./suggestions";

/** A row action. Deliberately the size of the "check endpoint" button rather
 *  than a full Button: these are one line of a dense list, and a page of
 *  primary buttons reads as a page of things to click. */
function Act({ onClick, children, tone = "plain" }: {
  onClick: () => void; children: React.ReactNode;
  tone?: "plain" | "warn";
}) {
  return (
    <button type="button" onClick={onClick}
      className={"shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium "
        + (tone === "warn"
          ? "border-amber-300 text-amber-800 hover:bg-amber-100"
          : "border-slate-300 text-slate-600 hover:bg-slate-50")}>
      {children}
    </button>
  );
}

export function SuggestionList({ suggestions, whyNothing, options, applied,
                                 onApply, onUndo }: {
  suggestions: Suggestion[];
  whyNothing: string | null;
  options: Options;
  applied: Applied;
  // The whole suggestion, not just its option: what applying replaces is the
  // value this row displayed, and handing over only the name would leave the
  // caller to find it again somewhere it is not the same value.
  onApply: (s: Suggestion, value: unknown) => void;
  onUndo: (option: string) => void;
}) {
  if (!suggestions.length) {
    // Why there is nothing, when there is a reason worth acting on -- a file
    // whose collector never reached a cluster looks exactly like a cluster that
    // constrains nothing, and only the first is worth re-collecting for.
    return whyNothing
      ? <p className="mt-2 text-[11px] text-slate-400">{whyNothing}</p> : null;
  }
  return (
    <div className="mt-3 border-t border-slate-100 pt-2">
      <p className="text-[11px] text-slate-500">
        <b className="text-slate-700">What this cluster implies</b>
        {" · "}{suggestionLine(suggestions)}
      </p>
      {/* suggest.py's reporting order, kept: the platform frames everything
          under it, then the objects the bundle references, then the cluster-wide
          posture. Sorting by whether there is a button would bury the reasoning
          under the shopping list. */}
      <ul className="mt-1.5 space-y-1.5">
        {suggestions.map((s) => {
          const act = offer(s);
          // Offered only while the option still holds what was applied: undo
          // restores what was there before that, and putting it back over a
          // value typed since would be the overwrite this panel may not make.
          const undoable = canUndo(applied, s.option, options)
            ? applied[s.option] : undefined;
          const conflict = s.state === "CONFLICT";
          return (
            <li key={s.option}
              className={"rounded-md px-2 py-1.5 text-[11px] "
                + (conflict ? "border border-amber-200 bg-amber-50"
                            : "bg-slate-50")}>
              <div className="flex items-start gap-2">
                <span className={"shrink-0 rounded px-1.5 py-0.5 text-[10px] "
                  + "font-bold uppercase tracking-wide "
                  + STRENGTH_STYLE[s.strength].badge}>
                  {STRENGTH_STYLE[s.strength].label}
                </span>
                <div className="grow min-w-[12rem]">
                  <p className="text-slate-700">
                    <code className="font-mono font-medium">{s.option}</code>
                    {/* Both values, on every row and not only on the ones that
                        disagree: applying is always a value replacing a value,
                        and the one being replaced is never left off screen. */}
                    {conflict ? (
                      <>
                        {" — configured "}
                        <code className="font-mono text-amber-900 break-all">
                          {s.current_shown}
                        </code>
                        {", and this cluster "}
                        {s.strength === "DECISIVE"
                          ? <>says <code className="font-mono text-amber-900 break-all">
                              {s.value_shown}</code></>
                          : <>can only serve{" "}
                              <code className="font-mono text-amber-900 break-all">
                                {s.candidates_shown.join(", ") || "none of them"}
                              </code></>}
                      </>
                    ) : (
                      <>
                        {" — now "}
                        <code className="font-mono text-slate-500 break-all">
                          {s.current_shown}
                        </code>
                        {s.state === "SETTLED"
                          ? ", which is what this evidence says"
                          : s.strength === "DECISIVE"
                            ? <>, evidence says <code className="font-mono break-all">
                                {s.value_shown}</code></>
                            : <>, evidence narrows it to <code className="font-mono break-all">
                                {s.candidates_shown.join(", ")}</code></>}
                      </>
                    )}
                  </p>
                  <p className="text-slate-500">{s.detail}</p>
                  {/* Why there is no button, in the column the reasoning is
                      read in -- it is an explanation, not an action, and the
                      action column is one button wide. */}
                  {act.kind === "blocked" && (
                    <p className="text-amber-700">Not offered: {act.because}</p>
                  )}
                  {s.ruled_out_shown.length > 0 && (
                    <p className="text-slate-400">
                      rules out {s.ruled_out_shown.join(", ")}
                    </p>
                  )}
                  <p className="font-mono text-[10px] text-slate-400">
                    {s.evidence.join(" · ")}
                    {" · "}{STRENGTH_STYLE[s.strength].hint}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1">
                  {act.kind === "apply" && (
                    <Act onClick={() => onApply(s, act.value)}>
                      Apply {clipValue(act.shown)}
                    </Act>
                  )}
                  {/* A different word for a different act. One click either way,
                      but this one overwrites a value somebody chose, and the row
                      above has just shown them both. */}
                  {act.kind === "replace" && (
                    <Act tone="warn" onClick={() => onApply(s, act.value)}>
                      Replace with {clipValue(act.shown)}
                    </Act>
                  )}
                  {/* One button per candidate, never a default -- not even at a
                      single candidate. Narrowing to one is still not choosing. */}
                  {act.kind === "choose" && act.candidates.map((c) => (
                    <Act key={c.shown} tone={conflict ? "warn" : "plain"}
                      onClick={() => onApply(s, c.value)}>
                      Use {clipValue(c.shown)}
                    </Act>
                  ))}
                  {undoable && (
                    <Act onClick={() => onUndo(s.option)}>
                      Undo → {clipValue(undoable.previousShown)}
                    </Act>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      {/* The claim this list does NOT make. Every option no row names is one
          this file says nothing about: left exactly as it is, and not verified
          by anything here. */}
      <p className="mt-1.5 text-[10px] text-slate-400">
        Only the options above are in this evidence. Everything else is left as
        you set it — unchanged, and unchecked.
      </p>
    </div>
  );
}
