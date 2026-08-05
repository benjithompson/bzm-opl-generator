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
import { useEffect, useRef, useState } from "react";

import { Api, CapacityPlan } from "./api";

/** The two figures the profile owns outright: what the load is, and what one
 *  engine is assumed to carry.
 *
 *  The engine size and the engines-per-node are deliberately *not* here. They
 *  are bundle options (`engine_cpu_limit`, `engine_mem_limit`,
 *  `engines_per_node`) and the profile is sized for the engine the bundle asks
 *  for -- one value with one owner. Held here as well they were two, and the
 *  planner's copy reached the options only when somebody pressed the button
 *  that copied it across, so a plan sized for a Large engine could generate a
 *  bundle asking for a standard one. */
export interface PlanInputs {
  users: string;
  vusPerEngine: string;
}

// There is deliberately no `agents` here. A location's concurrency is
// agents x engines per agent, so the arithmetic needs the count -- but this
// panel is for somebody with no cluster, and how many agents they will end up
// running is a decision they have not made yet and can change at will
// afterwards. Asked up front it is a guess that silently halves or doubles
// `slots`. The pane inside an existing location reads the count off the
// location, where it is a fact; `plan.capacity_plan` still takes `agents`, and
// that is who passes it.

export const EMPTY_PLAN_INPUTS: PlanInputs = { users: "", vusPerEngine: "" };


export interface PlanAsk {
  /** Blank means "nothing to size yet", which clears rather than refuses. */
  users: string;
  vusPerEngine?: string;
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
  const { users, vusPerEngine, engineCpu, engineMem, enginesPerNode, agents } = ask;

  // Debounced, because every keystroke in a number field is a plan: typing
  // "5000" passes through 5, 50 and 500, and three answers nobody wanted
  // arrive before the one they did.
  const timer = useRef<number>();
  useEffect(() => {
    if (!users.trim()) { setPlan(null); setErr(null); setBusy(false); return; }
    window.clearTimeout(timer.current);
    setBusy(true);
    timer.current = window.setTimeout(() => {
      api.plan({ users, vus_per_engine: vusPerEngine, engine_cpu: engineCpu,
                 engine_mem: engineMem, engines_per_node: enginesPerNode,
                 agents })
        .then((p) => { setPlan(p); setErr(null); })
        .catch((e: Error) => { setErr(e.message); setPlan(null); })
        .finally(() => setBusy(false));
    }, 250);
    return () => window.clearTimeout(timer.current);
    // Primitives, so a caller rebuilding its `ask` object every render does not
    // re-POST for a plan nothing changed about.
  }, [users, vusPerEngine, engineCpu, engineMem, enginesPerNode, agents]);

  return { plan, err, busy };
}

/** What an engine of this size is rated for, asked as soon as the size changes
 *  rather than waiting for a plan.
 *
 *  The suggestion is most use *before* a target is typed -- that is when you
 *  are choosing the size -- and 500 is only right for the standard engine, so a
 *  placeholder that waited for a plan showed 500 beside a Large one. The ratio
 *  stays on the server for the reason api.engineVus gives: doctor judges
 *  locations against the same one. */
export function useEngineRating(cpu: string | undefined, mem: string | undefined,
                                api: Api): number | null {
  const [rated, setRated] = useState<number | null>(null);
  useEffect(() => {
    if (!cpu || !mem) { setRated(null); return; }
    let live = true;
    api.engineVus(cpu, mem)
      .then((r) => { if (live) setRated(r.supported_vus); })
      .catch(() => { if (live) setRated(null); });
    return () => { live = false; };
  }, [cpu, mem]);
  return rated;
}
