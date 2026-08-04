// The engine size is one figure, and the location is where it is set (#132).
// The location's overrideCPU/overrideMemory are the engine pod's *requests*,
// and generate derives the bundle's limits from them when no explicit option
// names one -- so requests and limits agree by construction, and the configure
// step does not edit the size at all. What it renders instead is this module's
// statement: the size the bundle will carry, where that figure came from, and
// where to change it (Location settings, which is the one manual writer).
//
// Statements are plain prose -- no backticks, no double dash -- the same rule
// as plan.py's warnings, because they render as text in the panel.

// The documented default (ENGINE_DEFAULT_CPU/MEM on the generator's side) --
// the one TS copy of the 2/8Gi figure.
import { STANDARD_SIZE } from "./optionGroups";

/** "2" -> 2, "500m" -> 0.5. null for anything unparseable, never zero. */
export function cpuCores(q: string): number | null {
  const m = /^(\d+(?:\.\d+)?)(m?)$/.exec(q.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "m" ? n / 1000 : n;
}

/** "8Gi" -> 8192, "512Mi" -> 512, in the MB overrideMemory speaks. Only the
 *  binary suffixes: a bare number is bytes to Kubernetes and almost never what
 *  was meant, so refusing to guess beats comparing against the wrong unit. */
export function memMb(q: string): number | null {
  const m = /^(\d+(?:\.\d+)?)(Gi|Mi)$/.exec(q.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "Gi" ? n * 1024 : n;
}

/** overrideCPU (whole cores, per the API) as the quantity the bundle emits. */
function cpuQuantity(cores: number): string {
  return Number.isInteger(cores) ? String(cores)
    : `${Math.round(cores * 1000)}m`;
}

/** overrideMemory (MB, read as Mi -- the planner's own equivalence) as the
 *  quantity the bundle emits: the Gi form where it is whole, Mi otherwise, so
 *  4096 arrives as 4Gi and an odd 8196 stays 8196Mi. format_memory's rule. */
function memQuantity(mb: number): string {
  return mb % 1024 === 0 ? `${mb / 1024}Gi` : `${mb}Mi`;
}

/** What the configure step states about the engine size.
 *
 *  `kind` is where the figure came from, and the states stay distinct:
 *  - "location": derived from the location's requests, so the two halves of
 *    the figure agree by construction.
 *  - "default": the location was read and sets nothing -- the documented
 *    default, with the packing gap named.
 *  - "noLocation": there is no location to read (manual entry, or the list
 *    still loading), which must not be worded as "the location sets nothing".
 *  - "bundle" / "override": explicit options (an imported profile, or the
 *    capacity profile on step 1) -- they outrank the location, and where the
 *    location asks for something else that is said, never silent. */
export interface SizeStatement {
  kind: "location" | "default" | "noLocation" | "bundle" | "override";
  /** The size the bundle will carry, as the quantities it emits. */
  cpu: string;
  mem: string;
  text: string;
}

// The smallest overrideMemory (MB) read as an engine size, mirroring
// generate.ENGINE_MIN_DERIVED_MEM_MB: the field's unit is unreliable (one
// real account holds 32, 4000 and 8196), and a derived 4Mi limit is an engine
// OOMKilled at startup. Below it the memory half is disregarded -- and said
// to be, never silently.
const MIN_DERIVED_MEM_MB = 1024;

export function sizeStatement(
  cpuOpt: string | null | undefined,
  memOpt: string | null | undefined,
  /** The selected location, or null where there is none to read. */
  location: { overrideCPU?: number | null; overrideMemory?: number | null }
    | null,
): SizeStatement {
  const optCpu = typeof cpuOpt === "string" && cpuOpt.trim() ? cpuOpt : null;
  const optMem = typeof memOpt === "string" && memOpt.trim() ? memOpt : null;
  const locCpu = location?.overrideCPU ?? null;
  const rawLocMem = location?.overrideMemory ?? null;
  const locMem = rawLocMem !== null && rawLocMem >= MIN_DERIVED_MEM_MB
    ? rawLocMem : null;
  const disregard = rawLocMem !== null && rawLocMem < MIN_DERIVED_MEM_MB
    ? ` The location's engine memory request of ${rawLocMem} MB is below `
      + "what an engine can start in and was not used for the size; check "
      + "its unit in Location settings."
    : "";
  const locSet = locCpu !== null || locMem !== null;
  // What the location implies, each half falling to the default -- the same
  // resolution generate.resolve_engine_limits applies.
  const fromLoc = {
    cpu: locCpu === null ? STANDARD_SIZE.cpu : cpuQuantity(locCpu),
    mem: locMem === null ? STANDARD_SIZE.mem : memQuantity(locMem),
  };
  const cpu = optCpu ?? fromLoc.cpu;
  const mem = optMem ?? fromLoc.mem;
  const size = `${cpu} CPU / ${mem}`;

  if (optCpu || optMem) {
    const base = `Engines run at ${size} per engine, set in this bundle's `
      + "options (an imported profile, or the capacity profile on the first "
      + "step).";
    if (!location) return { kind: "bundle", cpu, mem, text: base };
    if (!locSet) {
      return {
        kind: "bundle", cpu, mem,
        text: base + " This location sets no engine requests, so engines are "
          + "placed at the 250m/256Mi default and can pack onto one node; set "
          + "the location's engine requests in Location settings to match.",
      };
    }
    const same = cpuCores(cpu) !== null && memMb(mem) !== null
      && cpuCores(cpu) === cpuCores(fromLoc.cpu)
      && memMb(mem) === memMb(fromLoc.mem);
    if (same) {
      return {
        kind: "bundle", cpu, mem,
        text: `Engines run at ${size} per engine, set in this bundle's `
          + "options and matching the location's engine requests.",
      };
    }
    return {
      kind: "override", cpu, mem,
      text: `This bundle overrides the location's engine size: ${size} in `
        + `the bundle against ${fromLoc.cpu} CPU / ${fromLoc.mem} from the `
        + "location's requests. The scheduler places engines on the "
        + "location's requests, so they will not match what engines run at. "
        + "Re-size the capacity profile, or change the location in Location "
        + "settings, to bring them together.",
    };
  }

  if (!location) {
    return {
      kind: "noLocation", cpu, mem,
      text: `Engines run at ${size} per engine, the documented default. A `
        + "location that sets engine CPU and memory requests sizes its "
        + "bundles to match; nothing here can read one, so set them in "
        + "BlazeMeter.",
    };
  }
  if (locSet) {
    return {
      kind: "location", cpu, mem,
      text: `Engines run at ${size} per engine, from this location's engine `
        + "requests. The bundle carries the same figure as limits, so "
        + "requests and limits match. Change it in Location settings on the "
        + `agent step; the bundle follows the location.${disregard}`,
    };
  }
  return {
    kind: "default", cpu, mem,
    text: `Engines run at ${size} per engine, the documented default. `
      + (disregard
        ? disregard.trim()
        : "This location sets no engine requests, so engines are placed at "
          + "the 250m/256Mi default and can pack onto one node. Set the "
          + "location's engine CPU and memory requests in Location settings; "
          + "the bundle then carries the location's figure as limits."),
  };
}
