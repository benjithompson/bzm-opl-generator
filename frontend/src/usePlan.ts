// Asking the server to size something, for the sizing card and for each
// location's panel. The arithmetic is plan.py's; a location re-asks with its
// own agent count, since `slots` is engines per agent.
import { useEffect, useMemo, useRef, useState } from "react";

import { Api, CapacityPlan } from "./api";

/** What the sizing card owns: which functionalities are sized, a target per
 *  model and any supplied per-pod figure, keyed by funcId because the models
 *  are served. The engine size is a bundle option and not here. */
export interface PlanInputs {
  /** The funcIds being sized. */
  functionalities: string[];
  /** funcId -> the target, in that model's unit. Blank is "not typed yet". */
  targets: Record<string, string>;
  /** funcId -> the supplied per-pod figure. None for an unmeasured model. */
  figures: Record<string, string>;
}

// No `agents`: somebody with no cluster has not decided it yet. A location's
// panel reads the count off the location.

// A fresh page sizes performance, with no target.
export const EMPTY_PLAN_INPUTS: PlanInputs = {
  functionalities: ["performance"], targets: {}, figures: {} };


/** One functionality being sized, as the route takes it. */
interface SizingAsk {
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

interface PlanState {
  plan: CapacityPlan | null;
  err: string | null;
  busy: boolean;
}

/** Ask the server to size `ask`, debounced. Blank targets clear the plan
 *  rather than erroring. */
export function useCapacityPlan(ask: PlanAsk, api: Api): PlanState {
  const [plan, setPlan] = useState<CapacityPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { engineCpu, engineMem, enginesPerNode, agents } = ask;
  // `ask.sizings` is rebuilt every render, so the effect keys on the rows as a
  // string and the memo hands back the matching array.
  const sized = ask.sizings.filter((s) => s.target.trim());
  const rows = JSON.stringify(sized);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const sizings = useMemo(() => sized, [rows]);

  // Debounced: typing 5000 passes through 5, 50 and 500.
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!sizings.length) { setPlan(null); setErr(null); setBusy(false); return; }
    window.clearTimeout(timer.current);
    setBusy(true);
    timer.current = window.setTimeout(() => {
      // Every model as a row, performance included.
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
    // Primitives and the memo, so a rebuilt `ask` does not re-POST.
  }, [api, sizings, engineCpu, engineMem, enginesPerNode, agents]);

  return { plan, err, busy };
}

/** What a pod of this size is rated for, per model, as soon as the size
 *  changes. Null when there is no size or the read failed; a null entry is a
 *  model with no measured figure. */
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
  }, [api, cpu, mem]);
  return rated;
}
