// The engine's size is one figure with two writers (#132): the bundle's
// limits (the Engine sizing group, KUBERNETES_RESOURCES_LIMITS_*) and the
// location's overrideCPU/overrideMemory, which are the pod's *requests* and
// live in the account. The scheduler places pods and the autoscaler adds
// nodes on requests alone, so the two diverging quietly is how a run packs
// onto one node -- and the GUI used to present them as strangers. This module
// is the comparison and its prose; App owns the write (the existing
// /api/locations/settings route), and the SizingGroup renders it.
//
// Match is the default, never an invariant: requests below limits is a
// legitimate choice on a fixed cluster that oversubscribes, so divergence is
// warned about and left standing.
//
// Warnings are plain prose -- no backticks, no double dash -- the same rule as
// plan.py's, because they render as text in the panel.

// The documented default the generator always emits now (ENGINE_DEFAULT_CPU /
// _MEM on that side) -- the one TS copy of the 2/8Gi figure.
import { STANDARD_SIZE } from "./optionGroups";

/** "2" -> 2, "500m" -> 0.5. null for anything unparseable, never zero: a
 *  half-typed custom limit must read as "nothing to judge yet". */
export function cpuCores(q: string): number | null {
  const m = /^(\d+(?:\.\d+)?)(m?)$/.exec(q.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "m" ? n / 1000 : n;
}

/** "8Gi" -> 8192, "512Mi" -> 512, in the MB overrideMemory takes. Only the
 *  binary suffixes: a bare number is bytes to Kubernetes and almost never what
 *  was meant, so refusing to guess beats comparing against the wrong unit. */
export function memMb(q: string): number | null {
  const m = /^(\d+(?:\.\d+)?)(Gi|Mi)$/.exec(q.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "Gi" ? n * 1024 : n;
}

/** What Apply would write: the location overrides that match the bundle's
 *  limits. `override_cpu` is null where the limit is not a whole number of
 *  cores -- the field takes whole cores only, so null is "cannot be written",
 *  never "write zero" (the same gap plan.capacity_plan expresses the same
 *  way). */
export interface MatchPatch {
  override_cpu: number | null;
  override_memory: number;
}

/** The location's requests beside the bundle's limits, as one answer.
 *
 *  "Could not read" and "there is nothing there" never share a representation:
 *  `noLocation` is a location this page cannot read (manual entry, or the
 *  list still on its way), and nothing may be said about its requests;
 *  `unset` is a location that was read and holds neither override, which is
 *  the 250m/256Mi default and worth the warning. `unjudged` is a limit still
 *  being typed -- nothing to compare yet, which is not a divergence. */
export type SizeState =
  | { kind: "noLocation" }
  | { kind: "unjudged" }
  | { kind: "unset"; limitCpu: string; limitMem: string;
      warning: string; patch: MatchPatch }
  | { kind: "diverge"; limitCpu: string; limitMem: string;
      warning: string; patch: MatchPatch }
  | { kind: "match"; note: string };

const PACKING = "The scheduler places engines and the autoscaler adds nodes "
  + "on requests alone, so a whole run can pack onto one node and the engines "
  + "slow each other down or are OOM killed.";

export function sizeState(
  cpuLimit: string | null | undefined,
  memLimit: string | null | undefined,
  /** The selected location, or null where there is none to read. */
  location: { overrideCPU?: number | null; overrideMemory?: number | null }
    | null,
): SizeState {
  // Unset limits are the documented default, which the bundle now always
  // carries -- so the comparison is against what the engines will really run
  // at, not against "nothing configured".
  const limitCpu = (typeof cpuLimit === "string" && cpuLimit)
    || STANDARD_SIZE.cpu;
  const limitMem = (typeof memLimit === "string" && memLimit)
    || STANDARD_SIZE.mem;
  if (!location) return { kind: "noLocation" };
  const limCores = cpuCores(limitCpu);
  const limMb = memMb(limitMem);
  if (limCores === null || limMb === null) return { kind: "unjudged" };

  const patch: MatchPatch = {
    override_cpu: Number.isInteger(limCores) ? limCores : null,
    override_memory: limMb,
  };
  const reqCpu = location.overrideCPU ?? null;
  const reqMem = location.overrideMemory ?? null;

  if (reqCpu === null && reqMem === null) {
    return {
      kind: "unset", limitCpu, limitMem, patch,
      warning: "This location sets no engine requests, so each engine asks "
        + "the scheduler for only the 250m CPU and 256Mi default while "
        + `running at up to ${limitCpu} CPU and ${limitMem}. ${PACKING}`,
    };
  }
  if (reqCpu === limCores && reqMem === limMb) {
    return {
      kind: "match",
      note: `This location's engine requests match the limits above: `
        + `${reqCpu} CPU and ${reqMem} MB per engine.`,
    };
  }
  // A half-set pair diverges against the defaulted half: crane fills the
  // missing field itself, so the gap is real even though nobody typed it.
  const cpuS = reqCpu === null ? "the default 250m" : `${reqCpu} CPU`;
  const memS = reqMem === null ? "the default 256Mi" : `${reqMem} MB`;
  return {
    kind: "diverge", limitCpu, limitMem, patch,
    warning: `This location requests ${cpuS} and ${memS} per engine against `
      + `the ${limitCpu} CPU / ${limitMem} limits above. ${PACKING} Matching `
      + "is the usual choice; requesting less is legitimate on a fixed "
      + "cluster that deliberately oversubscribes.",
  };
}

/** What pressing Apply does, said before it is pressed: it is one of this
 *  page's writes to the customer's account, so the sentence carries the same
 *  reach the settings panel states for Save. */
export function applyCost(
  // Only the two states that carry a patch -- a hand-built object cannot
  // bypass the state machine's judgement of what may be written.
  s: Extract<SizeState, { patch: MatchPatch }>,
): string {
  const { patch } = s;
  const wrote = patch.override_cpu === null
    ? `sets this location's engine memory request to ${patch.override_memory} `
      + `MB in BlazeMeter; the CPU request takes whole cores only, so `
      + `${s.limitCpu} cannot be written and is left alone`
    : `sets this location's engine requests to ${patch.override_cpu} CPU and `
      + `${patch.override_memory} MB in BlazeMeter`;
  return `Apply ${wrote}. That changes the location for every agent in it `
    + "and every test that starts on it, including anyone else's.";
}
