// Asking the server to size something, from either of the two places that do.
//
// The standalone planner and the location's own Calculate pane are different
// panels asking one question, and they had two copies of how to ask it: the
// same 250ms debounce, the same three pieces of state, the same "a blank target
// is not an error, it is the state the panel opens in". A copy each is how they
// come to disagree about which keystroke is worth a round trip -- and they
// already had, since only one of them asked what the engine size was rated for.
//
// The arithmetic itself is not here and must not be: it is plan.py's, and
// doctor judges live locations against the same ratio. This is only the asking.
import { useEffect, useRef, useState } from "react";

import { api, CapacityPlan } from "./api";

export interface PlanInputs {
  users: string;
  vusPerEngine: string;
  engineCpu: string;
  engineMem: string;
  enginesPerNode: string;
  /** How many agents the location will have. An input here and nowhere else in
   *  this panel's arithmetic: a location's concurrency is agents x engines per
   *  agent, so a plan that assumed one agent told a two-agent location to set
   *  twice the engines it needs. The pane inside an existing location reads the
   *  count off the location instead -- there it is a fact, here it is a
   *  decision, and this is the panel for somebody who has not made it yet. */
  agents: string;
}

export const EMPTY_PLAN_INPUTS: PlanInputs = {
  users: "", vusPerEngine: "", engineCpu: "", engineMem: "",
  enginesPerNode: "", agents: "",
};


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

export function useCapacityPlan(ask: PlanAsk): PlanState {
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
export function useEngineRating(cpu?: string, mem?: string): number | null {
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
