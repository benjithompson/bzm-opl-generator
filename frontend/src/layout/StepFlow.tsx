// One step on screen at a time. The stepper and Back/Next are one fixed bar;
// the step scrolls inside itself. Steps arrive as children (number and title
// read off the element); whether each may be left arrives as `done`.
import {
  Children, isValidElement, ReactElement, ReactNode, useState,
} from "react";

interface StepFlowProps {
  /** Which step is open; controlled from App so a later step can send you back. */
  at: number;
  onGo: (i: number) => void;
  /** One per step, in order. `false` greys Next and shows `blockedBy`. */
  done: boolean[];
  /** Why Next is greyed, per step, said on the control itself. "" for a step
   *  that never blocks. */
  blockedBy: string[];
  /** A line under the step, outside its scroller, so always on screen. */
  footer?: ReactNode;
  children: ReactNode;
}

const dotCls = (state: "done" | "now" | "todo") =>
  "w-6 h-6 rounded-full flex items-center justify-center text-2xs font-bold "
  + ({
    done: "bg-emerald-500 text-white",
    now: "bg-bzm text-white",
    todo: "bg-slate-200 text-slate-500",
  })[state];

export function StepFlow({ at, onGo, done, blockedBy, footer, children }: StepFlowProps) {
  // Steps the user has opened: a step can be done on arrival, and a tick on
  // one nobody has looked at would claim too much.
  const [seen, setSeen] = useState<Record<number, boolean>>({ 0: true });

  const steps = Children.toArray(children).filter(isValidElement).map((el, i) => {
    const props = (el as ReactElement<{ n?: number; title?: string }>).props;
    return { node: el, n: props.n ?? i + 1, title: props.title ?? `Step ${i + 1}` };
  });
  const last = at === steps.length - 1;
  const ready = done[at] ?? true;

  const go = (i: number) => {
    const to = Math.max(0, Math.min(steps.length - 1, i));
    onGo(to);
    setSeen((s) => ({ ...s, [to]: true }));
    window.scrollTo({ top: 0 });
  };
  const stateOf = (i: number) =>
    i === at ? "now" as const
      : done[i] && seen[i] ? "done" as const : "todo" as const;

  return (
    // The page's own height, so the step scrolls inside it and the footer stays
    // last on screen. 6.75rem is the page header (2.75rem) plus main's padding.
    <div className="flex flex-col h-[calc(100vh-6.75rem)]">
      {/* The only sticky bar on the page. */}
      <div className="sticky top-0 z-20 bg-slate-50/95 backdrop-blur border-b border-slate-200 -mx-6 px-6">
        <div className="py-2 flex items-center gap-4">
          {/* Scrolls rather than running under the buttons when narrow. */}
          <div className="flex items-center gap-1.5 grow min-w-0 overflow-x-auto">
            {steps.map((s, i) => (
              <button type="button" key={s.n} onClick={() => go(i)}
                className={"flex items-center gap-1.5 rounded-full pl-1 pr-3 py-1 "
                  + (i === at ? "bg-white shadow-sm" : "hover:bg-white/60")}>
                <span className={dotCls(stateOf(i))}>
                  {stateOf(i) === "done" ? "✓" : s.n}
                </span>
                <span className={"text-xs whitespace-nowrap "
                  + (i === at ? "font-medium text-slate-900" : "text-slate-500")}>
                  {s.title}
                </span>
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <span className="text-2xs text-slate-400 whitespace-nowrap">
              Step {at + 1} of {steps.length}
            </span>
            <button type="button"
              className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              disabled={at === 0} onClick={() => go(at - 1)}>
              ← Back
            </button>
            <button type="button"
              className={"rounded-md px-4 py-1.5 text-sm font-medium "
                + (ready && !last ? "bg-bzm text-white hover:bg-bzm-dark"
                                  : "bg-slate-200 text-slate-400 cursor-not-allowed")}
              disabled={!ready || last} onClick={() => go(at + 1)}>
              Next →
            </button>
          </div>
        </div>
      </div>
      {!ready && blockedBy[at] && (
        <p className="text-2xs text-amber-700 pt-2">{blockedBy[at]}</p>
      )}
      {/* The step owns the scrolling, so the bar above never moves. */}
      <div className="mt-3 flex-1 min-h-0 overflow-y-auto pr-1">
        {steps[at]?.node}
      </div>
      {footer}
    </div>
  );
}
