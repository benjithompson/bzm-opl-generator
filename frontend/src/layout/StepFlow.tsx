// One step on screen at a time, with the way forward always in the same place.
//
// The three steps used to be stacked, so a long one pushed the next one off the
// bottom and the controls that matter -- what is unfinished, what is next --
// moved with it. Here the stepper and Back/Next are one bar at the top, the
// step scrolls inside itself, and the page does not scroll at all: the controls
// cannot move, whatever the step's height.
//
// The steps arrive as children, which is what keeps this file from knowing
// anything about them: their number and title are read off the element. The one
// thing an element cannot say about itself is whether it is finished enough to
// leave, so that arrives as `done` from the caller, where namespaceOk / saOk /
// the unfinished groups already live.
import {
  Children, isValidElement, ReactElement, ReactNode, useState,
} from "react";

interface StepFlowProps {
  /** Which step is open, and how to move. Controlled from App because the
   *  download step has to be able to send you back to the one holding an
   *  unfinished group -- a step flow whose position only it can see leaves that
   *  as a sentence instead of a button. */
  at: number;
  onGo: (i: number) => void;
  /** One per step, in order. `false` greys Next and shows `blockedBy`. */
  done: boolean[];
  /** Why Next is greyed, per step. Said on the control rather than only at the
   *  field that is empty: the point of the fixed position is that the user is
   *  looking there, not up the page. "" for a step that never blocks. */
  blockedBy: string[];
  /** A line under the step, outside its scroller and so always on screen --
   *  what the flow adds up to rather than part of any one step. Inside the
   *  scrolling area it would sit at the bottom of a panel taller than the
   *  window, which is to say nowhere. */
  footer?: ReactNode;
  children: ReactNode;
}

const dotCls = (state: "done" | "now" | "todo") =>
  "w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold "
  + ({
    done: "bg-emerald-500 text-white",
    now: "bg-bzm text-white",
    todo: "bg-slate-200 text-slate-500",
  })[state];

export function StepFlow({ at, onGo, done, blockedBy, footer, children }: StepFlowProps) {
  // Steps the user has opened. Every step but the last is "done" on arrival --
  // the namespace and service account have defaults and no group is mandatory
  // -- so a tick on a step nobody has looked at claims something that did not
  // happen. Visited is what tells the two apart.
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
    // A flex column of the height the page actually has, so the step scrolls
    // inside it and the footer is the last thing on screen. It was a scroller
    // of `100vh - 13rem` with the footer after it, and the arithmetic was
    // always going to be wrong for somebody: on a 900px window it put the line
    // 21px below the fold. What is above this varies -- the blocked-by sentence
    // comes and goes -- so nothing here should be counting rem.
    // 6.75rem is what is above and below this now that the preview is a drawer
    // rather than a tab bar: the page header (2.75rem) plus main's own padding
    // (1.5rem top and bottom). Measured rather than guessed.
    <div className="flex flex-col h-[calc(100vh-6.75rem)]">
      {/* The only sticky bar on the page now that the preview is a drawer
          rather than a tab strip above this one -- two bars both claiming
          top-0 was one bar over the other. */}
      <div className="sticky top-0 z-20 bg-slate-50/95 backdrop-blur border-b border-slate-200 -mx-6 px-6">
        <div className="py-2 flex items-center gap-4">
          {/* Scrolls rather than collides: the drawer on the right takes the
              width the three pills and the Back/Next pair used to share, and
              flex children do not shrink below their content -- so without
              this the step titles ran under the buttons. */}
          <div className="flex items-center gap-1.5 grow min-w-0 overflow-x-auto">
            {steps.map((s, i) => (
              <button key={s.n} onClick={() => go(i)}
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
            <span className="text-[11px] text-slate-400 whitespace-nowrap">
              Step {at + 1} of {steps.length}
            </span>
            <button
              className="rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              disabled={at === 0} onClick={() => go(at - 1)}>
              ← Back
            </button>
            <button
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
        <p className="text-[11px] text-amber-700 pt-2">{blockedBy[at]}</p>
      )}
      {/* The step owns the scrolling, not the page, so the bar above never
          leaves the top of the window. The height leaves room for the footer,
          which is outside the scroller for the same reason the stepper is above
          it: it must not move. */}
      <div className="mt-3 flex-1 min-h-0 overflow-y-auto pr-1">
        {steps[at]?.node}
      </div>
      {footer}
    </div>
  );
}
