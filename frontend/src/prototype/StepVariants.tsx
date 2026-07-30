// THROWAWAY -- three ways to show one step at a time instead of three stacked
// panels you scroll through.
//
// All three agree on the rule that prompted them: exactly one panel is open,
// and Back/Next never move. They disagree about where "never move" is, and how
// much of the flow you can still see while you are inside one step:
//
//   H  collapsed neighbours, controls in a fixed footer     (bottom right)
//   I  stepper across the top, controls in that same bar    (top right)
//   J  one card, arrows pinned to the middle of each edge   (mid left/right)
//
// The steps themselves are App's own <Section> elements, passed straight
// through as children -- nothing here re-implements a step, and a step's title
// and number are read off the element rather than restated. `done` is the one
// thing the elements cannot say, so it arrives as an array from App, which is
// where namespaceOk / saOk / the unfinished groups already live.

import { Children, isValidElement, ReactElement, ReactNode, useState } from "react";

export type StepKey = "H" | "I" | "J";

interface Flow {
  steps: { node: ReactNode; n: number; title: string; hint?: string }[];
  at: number;
  go: (i: number) => void;
  done: boolean[];
  /** Steps the user has actually opened. Every step but the last is "done" on
   *  arrival -- the namespace and service account have defaults and no group is
   *  mandatory -- so a tick on a step nobody has looked at claims something
   *  that did not happen. Visited is what tells the two apart. */
  seen: boolean[];
  /** May the user leave the step they are on? False greys Next and says why. */
  ready: boolean;
}

function useFlow(children: ReactNode, done: boolean[]): Flow {
  const [at, setAt] = useState(0);
  const [seen, setSeen] = useState<Record<number, boolean>>({ 0: true });
  const steps = Children.toArray(children)
    .filter(isValidElement)
    .map((el, i) => {
      const p = (el as ReactElement<{ n?: number; title?: string; hint?: string }>).props;
      return { node: el, n: p.n ?? i + 1, title: p.title ?? `Step ${i + 1}`, hint: p.hint };
    });
  const go = (i: number) => {
    const to = Math.max(0, Math.min(steps.length - 1, i));
    setAt(to);
    setSeen((s) => ({ ...s, [to]: true }));
    window.scrollTo({ top: 0 });
  };
  return { steps, at, go, done, ready: done[at] ?? true,
           seen: steps.map((_, i) => !!seen[i]) };
}

/** Why Next is greyed. Said on the control rather than only at the field that
 *  is empty: the whole point of the fixed position is that the user is looking
 *  there, not up the page. */
const WHY = [
  "fill in the agent details to continue",
  "namespace, service account and any unfinished group first",
  "",
];

const nextCls = (on: boolean) =>
  "rounded-md px-4 py-1.5 text-sm font-medium " + (on
    ? "bg-bzm text-white hover:bg-bzm-dark"
    : "bg-slate-200 text-slate-400 cursor-not-allowed");
const backCls =
  "rounded-md px-3 py-1.5 text-sm font-medium border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-40";

/** Back / step-of / Next. One component so the three variants cannot drift into
 *  disagreeing about when Next is available -- only about where it sits. */
function Controls(f: Flow & { compact?: boolean }) {
  const last = f.at === f.steps.length - 1;
  return (
    <div className="flex items-center gap-3">
      {!f.compact && !f.ready && (
        <span className="text-[11px] text-amber-700">{WHY[f.at]}</span>
      )}
      <span className="text-[11px] text-slate-400 whitespace-nowrap">
        Step {f.at + 1} of {f.steps.length}
      </span>
      <button className={backCls} disabled={f.at === 0} onClick={() => f.go(f.at - 1)}>
        ← Back
      </button>
      <button className={nextCls(f.ready && !last)} disabled={!f.ready || last}
        onClick={() => f.go(f.at + 1)}>
        Next →
      </button>
    </div>
  );
}

const dot = (state: "done" | "now" | "todo") =>
  "w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold " + ({
    done: "bg-emerald-500 text-white",
    now: "bg-bzm text-white",
    todo: "bg-slate-200 text-slate-500",
  })[state];

const stateOf = (f: Flow, i: number) =>
  i === f.at ? "now" as const
    : f.done[i] && f.seen[i] ? "done" as const : "todo" as const;

/** What a step that is not in view has to say for itself. "ready" is the case
 *  the tick would otherwise overclaim: complete on defaults, never opened. */
const stepState = (f: Flow, i: number) =>
  f.seen[i] ? (f.done[i] ? "done" : "incomplete")
    : f.done[i] ? "ready — nothing required" : "not started";

// == H -- collapsed neighbours, controls in a fixed footer ====================
// The steps you are not on stay on the page as slim bars, so the flow is still
// legible and a finished step is one click away. Controls sit in a bar pinned
// to the bottom of the viewport: the corner the user's hand is already going
// to, and it cannot move when a step is twice as tall as the last.
function StepsH(f: Flow) {
  return (
    <>
      <div className="space-y-2 pb-20">
        {f.steps.map((s, i) => (
          i === f.at ? (
            <div key={s.n}>{s.node}</div>
          ) : (
            <button key={s.n} onClick={() => f.go(i)}
              className="w-full flex items-center gap-3 rounded-xl border border-slate-200 bg-white/70 px-5 py-3 text-left hover:bg-white">
              <span className={dot(stateOf(f, i))}>
                {stateOf(f, i) === "done" ? "✓" : s.n}
              </span>
              <span className="text-sm font-medium text-slate-500">{s.title}</span>
              <span className="grow" />
              <span className="text-[11px] text-slate-400">{stepState(f, i)}</span>
            </button>
          )
        ))}
      </div>
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-slate-200 bg-white/95 backdrop-blur">
        <div className="max-w-screen-xl mx-auto px-6 py-2.5 flex items-center justify-end">
          <Controls {...f} />
        </div>
      </div>
    </>
  );
}

// == I -- stepper across the top, controls in the same bar ====================
// Nothing of the other steps but their names, and the whole flow is one bar:
// where you are, where you can go, and the way forward, all in the strip the
// eye starts at. The panel scrolls inside itself, so the page never scrolls and
// the bar is never anywhere but the top.
function StepsI(f: Flow) {
  return (
    <>
      {/* Below G's Configure/Preview tabs, which are sticky at the very top:
          two bars both claiming top-0 is one bar over the other. */}
      <div className="sticky top-[3.25rem] z-20 bg-slate-50/95 backdrop-blur border-b border-slate-200 -mx-6 px-6">
        <div className="max-w-screen-xl mx-auto py-2 flex items-center gap-4">
          <div className="flex items-center gap-1.5 grow min-w-0">
            {f.steps.map((s, i) => (
              <button key={s.n} onClick={() => f.go(i)}
                className={"flex items-center gap-1.5 rounded-full pl-1 pr-3 py-1 "
                  + (i === f.at ? "bg-white shadow-sm" : "hover:bg-white/60")}>
                <span className={dot(stateOf(f, i))}>
                  {stateOf(f, i) === "done" ? "✓" : s.n}
                </span>
                <span className={"text-xs whitespace-nowrap "
                  + (i === f.at ? "font-medium text-slate-900" : "text-slate-500")}>
                  {s.title}
                </span>
              </button>
            ))}
          </div>
          <Controls {...f} compact />
        </div>
      </div>
      {!f.ready && (
        <p className="text-[11px] text-amber-700 pt-2">{WHY[f.at]}</p>
      )}
      {/* The panel owns the scrolling, not the page. */}
      <div className="mt-3 overflow-y-auto h-[calc(100vh-11rem)] pr-1">
        {f.steps[f.at]?.node}
      </div>
    </>
  );
}

// == J -- one card, arrows pinned to the middle of each edge ==================
// The most literal reading of "always in the same area": the arrows are at the
// vertical middle of the viewport, at the edges, where nothing else ever is.
// Progress is dots under the card rather than a stepper, so the card is the
// only thing on the page.
function StepsJ(f: Flow) {
  const last = f.at === f.steps.length - 1;
  const arrow = (on: boolean) =>
    "fixed top-1/2 -translate-y-1/2 z-20 w-11 h-11 rounded-full shadow-lg flex items-center justify-center text-lg "
    + (on ? "bg-white text-slate-700 hover:bg-slate-100" : "bg-slate-100 text-slate-300 cursor-not-allowed");
  return (
    <>
      <div className="overflow-y-auto h-[calc(100vh-9.5rem)] px-2">
        {f.steps[f.at]?.node}
      </div>
      <button className={arrow(f.at > 0) + " left-4"} disabled={f.at === 0}
        onClick={() => f.go(f.at - 1)} title="previous step">‹</button>
      <button className={arrow(f.ready && !last) + " right-4"} disabled={!f.ready || last}
        onClick={() => f.go(f.at + 1)} title="next step">›</button>
      <div className="fixed inset-x-0 bottom-3 z-20 flex flex-col items-center gap-1 pointer-events-none">
        {!f.ready && (
          <span className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-3 py-0.5">
            {WHY[f.at]}
          </span>
        )}
        <div className="flex items-center gap-2 pointer-events-auto">
          {f.steps.map((s, i) => (
            <button key={s.n} onClick={() => f.go(i)} title={s.title}
              className={"h-2 rounded-full transition-all "
                + (i === f.at ? "w-6 bg-bzm"
                  : stateOf(f, i) === "done" ? "w-2 bg-emerald-500"
                  : "w-2 bg-slate-300")} />
          ))}
        </div>
      </div>
    </>
  );
}

/** `variant` null is the shipped page: three panels, stacked, all open. */
export function StepFlow(p: {
  variant: StepKey | null; done: boolean[]; children: ReactNode;
}) {
  const f = useFlow(p.children, p.done);
  if (!p.variant) return <div className="space-y-5">{p.children}</div>;
  const S = { H: StepsH, I: StepsI, J: StepsJ }[p.variant];
  return <S {...f} />;
}
