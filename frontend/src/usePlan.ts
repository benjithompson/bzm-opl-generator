// Asking the server to size something, from either of the two places that do.
//
// The sizing card and each location's own panel are different views
// of one question, and they had two copies of how to ask it: the same 250ms
// debounce, the same three pieces of state, the same "a blank target is not an
// error, it is the state the panel opens in". A copy each is how they come to
// disagree about which keystroke is worth a round trip -- and they already had,
// since only one of them asked what the engine size was rated for.
//
// The two ask the same thing with one difference: the card sizes the run
// (no agents, so one), and a location row re-asks it with the agent count that
// location actually has, because `slots` is engines per *agent*. That division
// is plan.py's and is not restated on this side.
//
// The arithmetic itself is not here and must not be: it is plan.py's, and
// doctor judges live locations against the same ratio. This is only the asking.
import { useEffect, useMemo, useRef, useState } from "react";

import { Api, CapacityPlan } from "./api";

/** What the sizing owns outright: which functionalities are being sized, what
 *  each is being asked for in its own unit, and what one pod is supplied as
 *  carrying where the figure is not left to the model.
 *
 *  Keyed by funcId rather than a field per model, because the models are
 *  **served** (`/api/sizing-models`) for the reason the functionalities are: a
 *  fourth has to reach the card by being added to plan.py's table. Named fields
 *  here would be the second declaration that a served table exists to avoid.
 *
 *  The engine size and the engines-per-node are deliberately *not* here. They
 *  are bundle options (`engine_cpu_limit`, `engine_mem_limit`,
 *  `engines_per_node`) and the sizing is for the engine the bundle asks
 *  for -- one value with one owner. Held here as well they were two, and the
 *  planner's copy reached the options only when somebody pressed the button
 *  that copied it across, so a plan sized for a Large engine could generate a
 *  bundle asking for a standard one. */
export interface PlanInputs {
  /** funcIds, and the card shows a field group for each. Empty is the state a
   *  fresh page is in, not a sizing of nothing. */
  functionalities: string[];
  /** funcId -> the target, in that model's unit. Blank is "not typed yet". */
  targets: Record<string, string>;
  /** funcId -> the per-pod figure supplied for that model. A model whose
   *  figure has never been measured has no entry here and no box to make one:
   *  see `SizingModel.measured`. */
  figures: Record<string, string>;
}

// There is deliberately no `agents` here. A location's concurrency is
// agents x engines per agent, so the arithmetic needs the count -- but this
// panel is for somebody with no cluster, and how many agents they will end up
// running is a decision they have not made yet and can change at will
// afterwards. Asked up front it is a guess that silently halves or doubles
// `slots`. The pane inside an existing location reads the count off the
// location, where it is a fact; `plan.capacity_plan` still takes `agents`, and
// that is who passes it.

// A fresh page sizes performance: it is what most locations run, and a card
// that opened on no functionality at all would open on no fields either. One
// tick, never a target -- nothing here guesses how big somebody's run is.
export const EMPTY_PLAN_INPUTS: PlanInputs = {
  functionalities: ["performance"], targets: {}, figures: {} };


/** One functionality being sized, as the route takes it. */
export interface SizingAsk {
  functionality: string;
  target: string;
  /** Absent where the model has no measured figure to override. */
  figure?: string;
}

export interface PlanAsk {
  /** Every model being sized. Empty, or every target blank, means "nothing to
   *  size yet", which clears rather than refuses. */
  sizings: SizingAsk[];
  engineCpu?: string;
  engineMem?: string;
  enginesPerNode?: string;
  /** Blank or absent is one agent — plan.py holds that default, not this. */
  agents?: string;
}

export interface PlanState {
  plan: CapacityPlan | null;
  err: string | null;
  busy: boolean;
}

/** `api` is the caller of the local routes, handed down from App like every
 *  other route on this page rather than imported here: a module-level import
 *  leaves nowhere to put a fake, and these two panels are the only ones whose
 *  requests could not be driven through the page's own seam. Fixed for the
 *  page's lifetime, which is why it is in no dependency array below. */
export function useCapacityPlan(ask: PlanAsk, api: Api): PlanState {
  const [plan, setPlan] = useState<CapacityPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { engineCpu, engineMem, enginesPerNode, agents } = ask;
  // The rows, and the same rows as one string. `ask.sizings` is rebuilt every
  // render by whoever assembles it, so a dependency on the array itself would
  // re-POST on every keystroke anywhere on the page; the primitives below
  // cannot express a list, so the string is what the effect depends on.
  //
  // The memo is keyed by that string and hands back the array the string was
  // made of. It used to be parsed back out of it inside the effect, which is a
  // round trip through JSON to recover a value already in scope -- and one
  // that would quietly retype `SizingAsk` as whatever JSON.parse returns.
  const sized = ask.sizings.filter((s) => s.target.trim());
  const rows = JSON.stringify(sized);
  // `rows` and not `sized` in the dependency list, deliberately: the string is
  // what says whether these are the same rows, and the array is a new object
  // every render.
  const sizings = useMemo(() => sized, [rows]);

  // Debounced, because every keystroke in a number field is a plan: typing
  // "5000" passes through 5, 50 and 500, and three answers nobody wanted
  // arrive before the one they did.
  const timer = useRef<number>();
  useEffect(() => {
    if (!sizings.length) { setPlan(null); setErr(null); setBusy(false); return; }
    window.clearTimeout(timer.current);
    setBusy(true);
    timer.current = window.setTimeout(() => {
      // Every model as a row, including performance: the route takes `users`
      // as the performance shorthand too, and sending one thing two ways is
      // how the two come to disagree about which was meant.
      api.plan({ sizings: sizings.map((s) => ({
        functionality: s.functionality, target: s.target,
        figure: s.figure ?? "" })),
        engine_cpu: engineCpu, engine_mem: engineMem,
        engines_per_node: enginesPerNode, agents })
        .then((p) => { setPlan(p); setErr(null); })
        .catch((e: Error) => { setErr(e.message); setPlan(null); })
        .finally(() => setBusy(false));
    }, 250);
    return () => window.clearTimeout(timer.current);
    // Primitives and the memo above, so a caller rebuilding its `ask` object
    // every render does not re-POST for a plan nothing changed about.
  }, [sizings, engineCpu, engineMem, enginesPerNode, agents]);

  return { plan, err, busy };
}

/** What a pod of this size is rated for in each model's unit, asked as soon as
 *  the size changes rather than waiting for a plan.
 *
 *  The suggestion is most use *before* a target is typed -- that is when you
 *  are choosing the size -- and 500 is only right for the standard engine, so a
 *  placeholder that waited for a plan showed 500 beside a Large one. The ratios
 *  stay on the server for the reason api.engineVus gives: doctor judges
 *  locations against the same one.
 *
 *  Every model, not the performance one: the route answers per funcId, so a
 *  field renders what its own row is rated for instead of the card testing
 *  which model it is drawing. `null` here is the whole answer being absent (no
 *  size yet, or the read failed); a null *inside* it is a model with no
 *  measured figure, which is a different thing and stays a different thing. */
export function useEngineRating(cpu: string | undefined, mem: string | undefined,
                                api: Api): Record<string, number | null> | null {
  const [rated, setRated] = useState<Record<string, number | null> | null>(null);
  useEffect(() => {
    if (!cpu || !mem) { setRated(null); return; }
    let live = true;
    api.engineVus(cpu, mem)
      .then((r) => { if (live) setRated(r.rated); })
      .catch(() => { if (live) setRated(null); });
    return () => { live = false; };
  }, [cpu, mem]);
  return rated;
}
