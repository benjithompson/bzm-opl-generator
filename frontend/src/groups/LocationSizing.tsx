// Sizing a location that exists: "how many engines does 5,000 virtual users
// need, on *this* location?"
//
// The same arithmetic as the standalone planner and the same server call, but
// asked from inside the location it is about, and seeded from what that
// location currently says. That is the difference worth having: the standalone
// view sizes a cluster nobody has yet, and this sizes the one in front of you.
//
// It is guidance rather than a form. The fields it fills are three of the four
// above it, and on their own they say nothing about why: what a number of
// virtual users costs in engines, what those engines cost in nodes, and which
// of the figures is an assumption nobody here can check. So the pane answers
// that first and offers Apply second, and Apply writes to the draft -- the
// location is not touched until Save, which is the one control that writes.
import { useEffect, useRef, useState } from "react";

import { api, CapacityPlan, Location } from "../api";
import { Button, ErrorMsg, Field, inputCls } from "../components";
import { ENGINE_SIZES } from "../optionGroups";

export interface SizingFill {
  slots: string;
  threads_per_engine: string;
  override_cpu: string;
  override_memory: string;
}

/** What the location already says, as the pane's starting point. A calculator
 *  that opened on blank fields would make you retype what is on screen behind
 *  it, and would quietly drop the one figure the location already knows. */
function seed(loc: Location) {
  const size = ENGINE_SIZES.find(
    (s) => Number(s.cpu) === loc.overrideCPU
      && Number(s.mem.replace("Gi", "")) * 1024 === loc.overrideMemory);
  return {
    vus: "",
    vusPerEngine: loc.threadsPerEngine ? String(loc.threadsPerEngine) : "",
    engine: size?.id ?? "standard",
  };
}

export function LocationSizing(props: {
  location: Location;
  onApply: (fill: SizingFill) => void;
  onClose: () => void;
}) {
  const [form, setForm] = useState(() => seed(props.location));
  const [plan, setPlan] = useState<CapacityPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setForm(seed(props.location)), [props.location.id]);

  const size = ENGINE_SIZES.find((s) => s.id === form.engine) ?? ENGINE_SIZES[1];
  // Not a field: the location already says how many agents it has, and a box
  // to retype it is a second source for the same fact -- one that can disagree
  // with the row it sits under. An empty location counts as one, which is what
  // it will have as soon as the first agent is created.
  const agents = Math.max((props.location.ships ?? []).length, 1);

  // What this engine size is rated for, asked as soon as the size changes
  // rather than waiting for a plan: the suggestion is most use *before* the
  // target is typed, and 500 is only right for the standard engine.
  const [rated, setRated] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    api.engineVus(size.cpu, size.mem)
      .then((r) => { if (live) setRated(r.supported_vus); })
      .catch(() => { if (live) setRated(null); });
    return () => { live = false; };
  }, [size.cpu, size.mem]);

  // Debounced for the reason the standalone panel is: typing "5000" passes
  // through 5, 50 and 500, and three answers nobody wanted arrive first.
  const timer = useRef<number>();
  useEffect(() => {
    if (!form.vus.trim()) { setPlan(null); setErr(null); return; }
    window.clearTimeout(timer.current);
    setBusy(true);
    timer.current = window.setTimeout(() => {
      api.plan({ users: form.vus, vus_per_engine: form.vusPerEngine,
                 engine_cpu: size.cpu, engine_mem: size.mem,
                 agents: String(agents) })
        .then((p) => { setPlan(p); setErr(null); })
        .catch((e: Error) => { setErr(e.message); setPlan(null); })
        .finally(() => setBusy(false));
    }, 250);
    return () => window.clearTimeout(timer.current);
  }, [form.vus, form.vusPerEngine, size.cpu, size.mem, agents]);

  const apply = () => {
    if (!plan) return;
    props.onApply({
      slots: String(plan.location.slots),
      threads_per_engine: String(plan.location.threads_per_engine),
      override_cpu: plan.location.override_cpu,
      override_memory: String(plan.location.override_memory_mb),
    });
  };

  const blocked = plan ? "" : "enter a virtual user target to size this location";

  return (
    <div className="rounded-md border border-bzm/40 bg-white p-3 space-y-3">
      <div>
        <p className="text-xs font-semibold text-slate-700">
          Size this location
        </p>
        <p className="text-[11px] text-slate-500">
          How many engines the load needs, and what they cost in nodes, across
          this location&apos;s{" "}
          <b>{(props.location.ships ?? []).length || "first"} agent
          {(props.location.ships ?? []).length === 1 ? "" : "s"}</b>. Nothing
          here reaches BlazeMeter — it fills the fields above for you to review
          and save.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <Field label="Virtual user target" required
          hint="the load this location has to run">
          <input type="number" min={1} className={inputCls} placeholder="5000"
            value={form.vus}
            onChange={(e) => setForm({ ...form, vus: e.target.value })} />
        </Field>
        <Field label="Virtual users per engine"
          hint={rated
            ? `a ${size.cpu} CPU / ${size.mem} engine is rated for ${rated.toLocaleString()}`
            : "blank uses what the engine size is rated for"}>
          <input type="number" min={1} className={inputCls}
            placeholder={rated ? String(rated) : "500"}
            value={form.vusPerEngine}
            onChange={(e) => setForm({ ...form, vusPerEngine: e.target.value })} />
        </Field>
        <Field label="Engine size" hint="what each engine pod runs at">
          <select className={inputCls} value={form.engine}
            onChange={(e) => setForm({ ...form, engine: e.target.value })}>
            {ENGINE_SIZES.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </Field>
      </div>

      {rated != null && props.location.threadsPerEngine != null
        && props.location.threadsPerEngine !== rated && (
        <p className="text-[11px] text-slate-500">
          This location currently runs{" "}
          <b>{props.location.threadsPerEngine.toLocaleString()}</b> virtual users
          an engine, against a rating of <b>{rated.toLocaleString()}</b> for the
          engine size above.{" "}
          {props.location.threadsPerEngine < rated
            ? "Raising it needs fewer engines for the same load."
            : "The engines will throttle or OOM part-way up the ramp unless the "
              + "engine size goes up with it."}
        </p>
      )}

      <ErrorMsg msg={err} />

      <div className={"space-y-2 transition-opacity " + (busy ? "opacity-50" : "")}>
        {plan ? <Guidance plan={plan} /> : (
          <p className="text-[11px] text-amber-700">{blocked}</p>
        )}
      </div>

      <div className="flex gap-2 items-center">
        <Button onClick={apply} disabled={!plan}>Apply</Button>
        <Button kind="ghost" onClick={props.onClose}>Close</Button>
        {plan && (
          <span className="text-[11px] text-slate-500">
            fills engines per agent, virtual users per engine and both engine
            requests — nothing is saved until you press Save
          </span>
        )}
      </div>
    </div>
  );
}

/** The part that is not a number: what the plan costs off this page.
 *
 *  A location can be set to 20 concurrent engines in two clicks; the cluster
 *  that has to run them is the part nobody sees from here, and it is the part
 *  that fails at test time as pods that never schedule. So the nodes come
 *  first, and the assumption behind the whole calculation comes with them. */
function Guidance({ plan }: { plan: CapacityPlan }) {
  return (
    <>
      <div className="grid grid-cols-4 gap-2">
        <Figure n={plan.engines} unit={plan.engines === 1 ? "engine" : "engines"}
          sub={`${plan.engine.cpu} CPU / ${plan.engine.memory} each`} />
        <Figure n={plan.engines_per_agent} unit="per agent"
          sub={plan.agents === 1 ? "one agent" : `over ${plan.agents} agents`} />
        <Figure n={plan.nodes_per_agent}
          unit={plan.nodes_per_agent === 1 ? "node/agent" : "nodes/agent"}
          sub={`${plan.node.cpu} vCPU / ${plan.node.memory} each`} />
        <Figure n={plan.peak.cpu} unit="vCPU at peak"
          sub={`${plan.peak.memory}, per cluster`} />
      </div>
      <p className="text-[11px] text-slate-500">
        {plan.agents === 1
          ? <>This agent&apos;s cluster has to schedule{" "}
              <b>{plan.engines_per_agent}</b> engines at once, plus{" "}
              {plan.engine.disk_gb}GB of disk each.</>
          : <>Each of the <b>{plan.agents} agents</b> runs{" "}
              <b>{plan.engines_per_agent}</b> of the {plan.engines} engines, so
              every one of their clusters has to schedule that many at once,
              plus {plan.engine.disk_gb}GB of disk each.</>}
        {" "}Setting this location above what a cluster can hold is a test that
        sits waiting for pods rather than one that fails.
      </p>
      {plan.vus_per_engine_assumed && (
        <p className="text-[11px] text-amber-700">
          <b>{plan.vus_per_engine.toLocaleString()} virtual users per engine is
          assumed</b> — what an engine this size is rated for, not a measurement
          of your script. Run one engine, find where it saturates, and put that
          number in.
        </p>
      )}
      {plan.warnings.map((w) => (
        <p key={w} className="text-[11px] text-slate-500">{w}</p>
      ))}
    </>
  );
}

function Figure({ n, unit, sub }: { n: number | string; unit: string; sub: string }) {
  return (
    <div className="border border-slate-200 rounded-md px-2.5 py-2">
      <div className="text-lg font-bold text-slate-900 leading-none">{n}</div>
      <div className="text-[11px] font-medium text-slate-600 mt-0.5">{unit}</div>
      <div className="text-[10px] text-slate-400">{sub}</div>
    </div>
  );
}
