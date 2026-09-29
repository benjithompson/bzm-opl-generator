// The engine size the configure step states. The location's
// overrideCPU/overrideMemory are the engine requests, and generate derives the
// limits from them unless an option names one, so the step edits nothing and
// says where the figure came from. Plain prose: it renders as text.
import { STANDARD_SIZE } from "./optionGroups";

/** "2" -> 2, "500m" -> 0.5. null for anything unparseable, never zero. */
export function cpuCores(q: string): number | null {
  const m = /^(\d+(?:\.\d+)?)(m?)$/.exec(q.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "m" ? n / 1000 : n;
}

/** "8Gi" -> 8192, "512Mi" -> 512 (MB). Binary suffixes only; a bare number
 *  would be bytes and is refused rather than guessed. */
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

/** overrideMemory (MB, read as Mi) as emitted: Gi where whole, Mi otherwise. */
function memQuantity(mb: number): string {
  return mb % 1024 === 0 ? `${mb / 1024}Gi` : `${mb}Mi`;
}

/** What the configure step states about the engine size. `kind` is where the
 *  figure came from: the location's requests, the default (a location that
 *  sets none), noLocation (nothing to read, as in manual entry), or bundle
 *  options, which outrank the location ("override" when they disagree). */
interface SizeStatement {
  kind: "location" | "default" | "noLocation" | "bundle" | "override";
  /** The size the bundle will carry, as the quantities it emits. */
  cpu: string;
  mem: string;
  text: string;
}

// The smallest overrideMemory (MB) taken as an engine size, as
// footprint.ENGINE_MIN_DERIVED_MEM_MB: the field's unit is unreliable, and a
// 4Mi limit is an engine killed at startup. Below it, it is ignored and said so.
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
  // Each half falls to the default, as bundle_options.resolve_engine_limits does.
  const fromLoc = {
    cpu: locCpu === null ? STANDARD_SIZE.cpu : cpuQuantity(locCpu),
    mem: locMem === null ? STANDARD_SIZE.mem : memQuantity(locMem),
  };
  const cpu = optCpu ?? fromLoc.cpu;
  const mem = optMem ?? fromLoc.mem;
  const size = `${cpu} CPU / ${mem}`;

  if (optCpu || optMem) {
    const base = `Engines run at ${size} per engine, set in this bundle's `
      + "options (an imported profile, or the sizing on the first "
      + "step).";
    if (!location) return { kind: "bundle", cpu, mem, text: base };
    if (!locSet) {
      return {
        kind: "bundle", cpu, mem,
        text: base + " Engines request the same, so each is placed on a node "
          + "with room for it.",
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
        + "Re-size, or change the location in Location "
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
        : "Engines request the same, so each is placed on a node with room "
          + "for it."),
  };
}
