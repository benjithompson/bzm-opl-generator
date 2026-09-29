// What the sizing would change about this location, and the one control that
// writes it. These four settings live in the account, not in any manifest, so
// changing one needs no regenerate. The panel reads before -> after; Save is a
// write to the account, and it reports what the account holds afterwards.
import { useEffect, useRef, useState } from "react";

import { Api, CapacityPlan, Location, LocationSettings as Settings,
         LocationUpdate } from "../api";
import { Button, ErrorMsg, NumberInput, PlanCaveats } from "../components";
// The sizing card's ask, re-made with this location's agent count.
import { PlanAsk, useCapacityPlan } from "../usePlan";
import { goneNotice } from "../stale";
import { plural } from "../text";

/** The form, as strings. Blank means "leave this one alone", which is also
 *  what the API takes; there is no way to clear a setting here. */
interface Draft {
  slots: string;
  threads_per_engine: string;
  override_cpu: string;
  override_memory: string;
}

const EMPTY: Draft = {
  slots: "", threads_per_engine: "", override_cpu: "", override_memory: "",
};

const KEYS = Object.keys(EMPTY) as (keyof Draft)[];

const shown = (v: number | null | undefined) =>
  v === null || v === undefined ? "" : String(v);

/** Does a typed field say what the location already holds? Compared as
 *  numbers, so "4", "4.0" and " 4" agree; blank matches only blank. */
export function same(a: string, b: string): boolean {
  const [x, y] = [a.trim(), b.trim()];
  if (x === y) return true;
  if (x === "" || y === "") return false;
  return Number(x) === Number(y) && !Number.isNaN(Number(x));
}

/** What the location currently says, as a draft. */
function draftOf(loc: Location): Draft {
  return {
    slots: shown(loc.slots),
    threads_per_engine: shown(loc.threadsPerEngine),
    override_cpu: shown(loc.overrideCPU),
    override_memory: shown(loc.overrideMemory),
  };
}

/** What the fields open on: the location's values, with the sizing's over them
 *  where there is one. Filling sends nothing. A null (only override_cpu, for an
 *  engine that is not whole cores) keeps the location's value. */
function seed(loc: Location, fill: Settings | null): Draft {
  const current = draftOf(loc);
  if (!fill) return current;
  return {
    ...current,
    ...Object.fromEntries(KEYS.filter((k) => fill[k] !== null)
      .map((k) => [k, String(fill[k])])),
  };
}

const LABELS: Record<keyof Draft, string> = {
  slots: "Engines per agent",
  threads_per_engine: "Virtual users per engine",
  override_cpu: "Engine CPU request",
  override_memory: "Engine memory request (MB)",
};

const HINTS: Record<keyof Draft, string> = {
  slots: "BlazeMeter's `slots` — one agent's engines, not the location's total",
  threads_per_engine: "unset, every test start fails with 403",
  override_cpu: "replaces the bundle's engine CPU request — blank keeps it",
  override_memory: "replaces the bundle's engine memory request, in MB — "
    + "blank keeps it",
};

export function LocationSettings(props: {
  /** The route caller, so the write can be tested. */
  api: Api;
  location: Location;
  /** What the sizing card states. With no targets the fields still show. */
  profile: PlanAsk;
  /** Put the changed location back into the page's own list and selection. */
  onUpdated: (loc: Location) => void;
  /** Done with this location: fold it and open the agents. */
  onConfirm: () => void;
}) {
  const { location } = props;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<LocationUpdate | null>(null);
  // Whether anything was typed. A hand edit outranks later sizing changes
  // until Reset.
  const [touched, setTouched] = useState(false);

  // Read off the location; an empty one counts as one agent, as it soon will.
  const agents = Math.max((location.ships ?? []).length, 1);
  const { plan, err: planErr, busy: planBusy } =
    useCapacityPlan({ ...props.profile, agents: String(agents) }, props.api);
  const fill = plan?.location ?? null;

  const [draft, setDraft] = useState<Draft>(() => seed(location, fill));

  // A different location resets the fields, the outcome and the refusal.
  useEffect(() => {
    setTouched(false); setResult(null); setErr(null);
  }, [location.id]);

  // The fields follow the location and the sizing until something is typed.
  // The outcome is not cleared: a save changes the location, which re-runs this.
  useEffect(() => {
    if (touched) return;
    setDraft(seed(location, fill));
    // The plan's four values rather than the plan object, which is new every
    // render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.id, location.slots, location.threadsPerEngine,
      location.overrideCPU, location.overrideMemory, touched,
      fill?.slots, fill?.threads_per_engine, fill?.override_cpu,
      fill?.override_memory]);

  const current = draftOf(location);
  const edited = KEYS.filter((k) => !same(draft[k], current[k]));
  const set = (k: keyof Draft, v: string) => {
    setTouched(true);
    setDraft({ ...draft, [k]: v });
  };

  // The location as of now: a save answering after it changed reports nothing here.
  const locationId = useRef(location.id);
  locationId.current = location.id;
  const save = async () => {
    const forLocation = location.id;
    setBusy(true); setErr(null); setResult(null);
    try {
      // Only the changed fields, so values somebody else edited are not
      // written back.
      const body: Record<string, string> = {};
      edited.forEach((k) => { body[k] = draft[k].trim(); });
      const out = await props.api.updateLocation(
        { harbor_id: location.id, ...body });
      props.onUpdated(out.location);
      if (locationId.current === forLocation) setResult(out);
    } catch (e) {
      if (locationId.current === forLocation) {
        setErr(goneNotice(e, "location") ?? String((e as Error).message));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    // Named after its location, since it opens inside that location's row.
    <section aria-label={`${location.name} settings`}
      className="border border-slate-200 rounded-md p-3 space-y-3 bg-slate-50">
      <div>
        <p className="text-xs font-semibold text-slate-700">
          Location settings
        </p>
        <p className="text-2xs text-slate-500">
          What this location may run, in BlazeMeter. None of it is in the
          manifests, so a change here needs no regenerate and no redeploy — it
          applies to the next test that starts.
        </p>
      </div>

      <ProfileLine plan={plan} agents={agents} busy={planBusy}
        touched={touched} />
      <ErrorMsg msg={planErr} />

      {/* Before -> after, with the after column the form. */}
      <div className="space-y-1.5">
        {KEYS.map((k) => (
          // The label wraps the row, so it names the value beside it.
          <label key={k} className="flex items-center gap-2">
            <span className="grow min-w-0">
              <span className="text-xs font-medium text-slate-600">
                {LABELS[k]}
              </span>
              <span className="block text-2xs text-slate-400">{HINTS[k]}</span>
            </span>
            <span className="text-xs tabular-nums text-slate-400 w-20 text-right
                             shrink-0">
              {current[k] || "not set"}
            </span>
            <span aria-hidden="true"
              className={"text-xs shrink-0 "
                + (same(draft[k], current[k]) ? "text-slate-300" : "text-bzm")}>
              →
            </span>
            <span className="w-28 shrink-0">
              <NumberInput placeholder={current[k] || "not set"} value={draft[k]}
                onChange={(v) => set(k, v)} />
            </span>
          </label>
        ))}
      </div>

      <p className="text-2xs text-slate-500">
        <b>Engines per agent</b> multiplies: this location&apos;s concurrency is
        agents × that figure, so {plural(agents, "agent")}
        {" "}at {draft.slots || "?"} each is{" "}
        {Number(draft.slots) > 0
          ? `${agents * Number(draft.slots)} engines at once`
          : "however many you set"}. Each agent runs its share in its own
        cluster.
      </p>

      <p className="text-2xs text-slate-500">
        The bundle sets each engine&apos;s requests equal to its limits, which
        is what the Kubernetes scheduler and the autoscaler place engines on.
        These two, if set, replace those requests: leave them blank or set them
        to the engine&apos;s own size. Set lower, engines pack onto fewer nodes
        than they need.
      </p>

      {/* One button, always live: Confirm (writes nothing, moves on) when
          nothing is typed, Save when something is, with its cost beside it.
          Saving does not fold, so the outcome stays on screen. */}
      <div className="flex items-center gap-2">
        <span className={"text-2xs "
          + (edited.length ? "text-amber-700" : "text-slate-500")}>
          {edited.length === 0
            ? "nothing to save — Confirm moves on to the agent"
            : `${plural(edited.length, "setting")}: `
              + edited.map((k) => LABELS[k].toLowerCase()).join(", ")
              + " — saving changes this location for every agent in it, and "
              + "every test that starts on it, including anyone else's"}
        </span>
        <span className="grow" />
        <Button kind="ghost" disabled={edited.length === 0}
          onClick={() => {
            setTouched(false);
            setDraft(seed(location, fill));
            setResult(null);
          }}>
          Reset
        </Button>
        <Button busy={busy}
          onClick={edited.length === 0 ? props.onConfirm : save}>
          {edited.length === 0 ? "Confirm" : "Save"}
        </Button>
      </div>

      <ErrorMsg msg={err} />
      {result && <Outcome result={result} />}
    </section>
  );
}

/** Where the right-hand column came from. The run's engines are divided by
 *  the agents, since `slots` is engines per agent. */
function ProfileLine({ plan, agents, busy, touched }: {
  plan: CapacityPlan | null; agents: number; busy: boolean; touched: boolean;
}) {
  if (!plan) {
    return (
      <p className="text-2xs text-amber-700">
        No sizing yet — make one above and these fields open on what
        it would change here. They can be typed in either way.
      </p>
    );
  }
  return (
    <div className={"space-y-1 " + (busy ? "opacity-50" : "")}>
      <p className="text-2xs text-slate-500">
        {/* Every sizing in its own unit; there may be no virtual users at all. */}
        The sizing — <b>{plan.sizings.map(
          (s) => `${s.target.toLocaleString()} ${s.unit}`).join(", ")}</b>
        {" "}— needs{" "}
        <b>{plural(plan.engines, "engine")}</b>, which is{" "}
        <b>{plan.engines_per_agent} per agent</b> across this location&apos;s{" "}
        {plural(agents, "agent")}, on{" "}
        {plural(plan.nodes_per_agent, "node")} each.
        {touched && " The fields below were edited by hand and no longer follow it."}
      </p>
      <PlanCaveats compact sizings={plan.sizings} warnings={plan.warnings} />
    </div>
  );
}

/** What the account holds now, not what was typed: BlazeMeter accepts
 *  `threadsPerEngine` and may not store it, so an unchanged field is shown in
 *  amber. */
function Outcome({ result }: { result: LocationUpdate }) {
  const changed = Object.keys(result.changed) as (keyof Settings)[];
  return (
    <div className="space-y-1">
      {changed.length > 0 && (
        <p className="text-2xs text-emerald-700">
          saved: {changed.map((k) => (
            `${LABELS[k as keyof Draft].toLowerCase()} ${result.before[k] ?? "not set"} → ${result.after[k]}`
          )).join(", ")}
        </p>
      )}
      {result.ignored.length > 0 && (
        <p className="text-2xs text-amber-700">
          BlazeMeter did not store{" "}
          {result.ignored.map((k) => LABELS[k as keyof Draft].toLowerCase()).join(", ")}
          {" "}— the location still reads the old value, so this account may not
          accept that field. Set it in BlazeMeter directly.
        </p>
      )}
      {changed.length === 0 && result.ignored.length === 0 && (
        <p className="text-2xs text-slate-500">
          nothing to change — the location already held those values
        </p>
      )}
    </div>
  );
}
