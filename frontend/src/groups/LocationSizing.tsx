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
import { useEffect, useState } from "react";

import { CapacityPlan, Location, LocationSettings } from "../api";
import {
  Button, ErrorMsg, Field, Figure, NumberInput, PlanCaveats,
} from "../components";
import { ENGINE_SIZES } from "../optionGroups";
import { EngineSizeSelect } from "./SizingGroup";
// How the ask is made -- debounce, states, and what a blank target means --
// shared with the standalone planner, which used to hold a second copy.
import { useCapacityPlan, useEngineRating } from "../usePlan";

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
  /** The plan's own location block, handed over unchanged: it already carries
   *  the settings' names and units, so there is nothing here to translate. */
  onApply: (fill: LocationSettings) => void;
  onClose: () => void;
}) {
  const [form, setForm] = useState(() => seed(props.location));

  useEffect(() => setForm(seed(props.location)), [props.location.id]);

  const size = ENGINE_SIZES.find((s) => s.id === form.engine) ?? ENGINE_SIZES[1];
  // Not a field: the location already says how many agents it has, and a box
  // to retype it is a second source for the same fact -- one that can disagree
  // with the row it sits under. An empty location counts as one, which is what
  // it will have as soon as the first agent is created.
  const agents = Math.max((props.location.ships ?? []).length, 1);

  const rated = useEngineRating(size.cpu, size.mem);
  const { plan, err, busy } = useCapacityPlan({
    users: form.vus, vusPerEngine: form.vusPerEngine,
    engineCpu: size.cpu, engineMem: size.mem, agents: String(agents),
  });

  const apply = () => { if (plan) props.onApply(plan.location); };

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
          <NumberInput placeholder="5000" value={form.vus}
            onChange={(v) => setForm({ ...form, vus: v })} />
        </Field>
        <Field label="Virtual users per engine"
          hint={rated
            ? `a ${size.cpu} CPU / ${size.mem} engine is rated for ${rated.toLocaleString()}`
            : "blank uses what the engine size is rated for"}>
          <NumberInput placeholder={rated ? String(rated) : "500"}
            value={form.vusPerEngine}
            onChange={(v) => setForm({ ...form, vusPerEngine: v })} />
        </Field>
        {/* No Custom: this pane fills a location's *request* fields, and
            BlazeMeter takes whole cores there -- an arbitrary size is a plan
            whose CPU request cannot be applied. */}
        <EngineSizeSelect preset={form.engine} hint="what each engine pod runs at"
          onPreset={(cpu) => setForm({
            ...form,
            engine: ENGINE_SIZES.find((x) => x.cpu === cpu)?.id ?? form.engine,
          })} />
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
      <PlanCaveats compact assumed={plan.vus_per_engine_assumed}
        vusPerEngine={plan.vus_per_engine} warnings={plan.warnings} />
    </>
  );
}
