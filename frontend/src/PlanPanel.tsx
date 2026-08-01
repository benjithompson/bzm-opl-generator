// The capacity planner: a load target in, an infrastructure request out.
//
// Deliberately not a step, and deliberately not behind the step flow. Step 1
// asks for an account or the three values off a deployed agent, and the person
// this panel is for has neither -- they have a number in a planning meeting and
// a platform team to ask for a cluster. Putting the planner inside the flow
// would put the first question behind the last one, so it is a view of its own
// that the header switches to.
//
// It holds its own inputs and its own answer. Nothing else on the page reads
// them, and the one thing that flows the other way -- `onUse`, which fills in
// the location and bundle fields the plan implies -- is a single call handed
// down, so App keeps owning every piece of state the generator uses.
import { useState } from "react";

import { CapacityPlan } from "./api";
import {
  Button, cardCls, ErrorMsg, Field, Figure, NumberInput, PlanCaveats,
  TextInput,
} from "./components";
import { EngineSizeSelect } from "./groups/SizingGroup";
import { ENGINE_SIZES } from "./optionGroups";
// The ask itself -- debounce, states, what a blank target means -- shared with
// the location's own Calculate pane rather than restated here.
import { PlanInputs, useCapacityPlan, useEngineRating } from "./usePlan";

/** The plan as the generator's own vocabulary: what the location has to
 *  advertise, and what the bundle has to ask for. Named here because this is
 *  where the translation is decided; App applies it. */
export interface PlanHandover {
  /** BlazeMeter's own field names, because this is what gets typed into them:
   *  `slots` is concurrent engines, `threadsPerEngine` is virtual users each. */
  slots: number;
  threadsPerEngine: number;
  engineCpuLimit: string;
  engineMemLimit: string;
  enginesPerNode: number;
}

export function PlanPanel(props: {
  inputs: PlanInputs;
  setInputs: (v: PlanInputs) => void;
  /** Carry the plan into the generator: the location fields it implies and the
   *  bundle options that match. Undefined while nothing is connected yet is
   *  fine -- the button says what it will do either way. */
  onUse: (h: PlanHandover) => void;
}) {
  const { inputs, setInputs } = props;
  const [showDoc, setShowDoc] = useState(false);
  const [copied, setCopied] = useState(false);
  // The clipboard's own failure, which is not the plan's: the hook owns
  // whether the plan could be worked out, and a browser refusing the
  // clipboard says nothing about that.
  const [copyErr, setCopyErr] = useState<string | null>(null);

  const set = (k: keyof PlanInputs, v: string) => setInputs({ ...inputs, [k]: v });

  const { plan, err, busy } = useCapacityPlan({
    users: inputs.users, vusPerEngine: inputs.vusPerEngine,
    engineCpu: inputs.engineCpu, engineMem: inputs.engineMem,
    enginesPerNode: inputs.enginesPerNode, agents: inputs.agents,
  });

  const preset = ENGINE_SIZES.find(
    (s) => s.cpu === inputs.engineCpu && s.mem === inputs.engineMem)?.id
    ?? (inputs.engineCpu || inputs.engineMem ? "custom" : "standard");
  // What the chosen size is rated for, whether or not a target has been typed.
  // This used to come off the plan, so before the first target it showed 500 --
  // BlazeMeter's figure for the standard engine -- beside a Large one.
  const size = ENGINE_SIZES.find((s) => s.id === preset);
  const rated = useEngineRating(inputs.engineCpu || size?.cpu,
                                inputs.engineMem || size?.mem);

  const download = () => {
    if (!plan) return;
    // Built here rather than fetched: the document is already in the answer,
    // and a second round trip could only disagree with what is on screen.
    const url = URL.createObjectURL(
      new Blob([plan.document], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = plan.document_file;
    a.click();
    URL.revokeObjectURL(url);
  };

  const copy = () => {
    if (!plan) return;
    navigator.clipboard.writeText(plan.document).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => setCopyErr("could not write to the clipboard"));
  };

  return (
    <div className="space-y-4">
      <div className={cardCls}>
        <div>
          <h2 className="text-sm font-semibold text-slate-800">
            How much infrastructure will this need?
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            For the request you have to raise before any of this is deployed.
            Nothing here reaches BlazeMeter or a cluster — it is arithmetic over
            the numbers below.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Field label="Virtual user target" required
            hint="the load the test has to reach">
            <NumberInput placeholder="5000" value={inputs.users}
              onChange={(v) => set("users", v)} />
          </Field>
          {/* The placeholder follows the engine size, because so does the
              figure the plan assumes when this is blank. Showing a fixed 500
              beside a Large engine said the plan would use 500 when it uses
              1,000 -- and it did exactly that until a target was typed, since
              the figure came off the plan. It comes off the size now. */}
          <Field label="Virtual users per engine"
            hint={rated
              ? `blank uses ${rated.toLocaleString()}, what this engine size is rated for`
              : "blank uses what an engine of this size is rated for"}>
            <NumberInput placeholder={String(rated ?? 500)}
              value={inputs.vusPerEngine}
              onChange={(v) => set("vusPerEngine", v)} />
          </Field>
          <EngineSizeSelect preset={preset} custom
            hint="the pod limits every engine runs at"
            onPreset={(cpu, mem) => setInputs({
              ...inputs, engineCpu: cpu ?? "", engineMem: mem ?? "" })} />
          <Field label="Engines per node"
            hint="blank means one — they contend when they share">
            <NumberInput placeholder="1" value={inputs.enginesPerNode}
              onChange={(v) => set("enginesPerNode", v)} />
          </Field>
          {/* A location's concurrency is agents x engines per agent, so this
              divides the run rather than adding to it: two agents each run half
              the engines, in a cluster each. Blank is one, which is the answer
              for most people asking this question for the first time. */}
          <Field label="Agents"
            hint="blank means one — each runs its share, in a cluster of its own">
            <NumberInput placeholder="1" value={inputs.agents}
              onChange={(v) => set("agents", v)} />
          </Field>
          {preset === "custom" && (
            <>
              <Field label="Engine CPU limit">
                <TextInput mono placeholder="2" value={inputs.engineCpu}
                  onChange={(v) => set("engineCpu", v)} />
              </Field>
              <Field label="Engine memory limit">
                <TextInput mono placeholder="8Gi" value={inputs.engineMem}
                  onChange={(v) => set("engineMem", v)} />
              </Field>
            </>
          )}
        </div>
        <ErrorMsg msg={err ?? copyErr} />
      </div>

      <PlanResult plan={plan} busy={busy} showDoc={showDoc}
                  setShowDoc={setShowDoc} onDownload={download}
                  onCopy={copy} copied={copied}
                  onUse={() => plan && props.onUse({
                    slots: plan.location.slots,
                    threadsPerEngine: plan.location.threads_per_engine,
                    engineCpuLimit: plan.engine.cpu,
                    engineMemLimit: plan.engine.memory,
                    enginesPerNode: plan.engines_per_node,
                  })} />
    </div>
  );
}

/** The plan, and the three things you can do with one.
 *
 *  `plan` is null until there is a load target to size. The controls stay on
 *  screen anyway, disabled, with one line saying what they are waiting for:
 *  a call to action that appears only once it works leaves the panel looking
 *  finished when it is not, and gives nobody anything to aim at. Same rule the
 *  step flow's Next follows, and the same amber sentence under it.
 */
function PlanResult(props: {
  plan: CapacityPlan | null; busy: boolean; showDoc: boolean;
  setShowDoc: (v: boolean) => void; onDownload: () => void; onCopy: () => void;
  copied: boolean; onUse: () => void;
}) {
  const p = props.plan;
  const waiting = p ? "" : "enter a virtual user target above to size a plan";
  return (
    <div className={"space-y-4 transition-opacity "
      + (props.busy ? "opacity-50" : "")}>
      <div className={cardCls}>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Em-dashes rather than zeroes: nothing has been worked out yet, and
              "0 engines" is an answer. */}
          <Figure big n={p ? p.engines : "—"}
            unit={p && p.engines === 1 ? "engine" : "engines"}
            sub={p ? `${p.engine.cpu} CPU / ${p.engine.memory} each` : " "} />
          <Figure big n={p ? p.nodes : "—"}
            unit={p && p.nodes === 1 ? "node" : "nodes"}
            /* Said per agent as soon as there is more than one, because that is
               what each cluster has to be: the total is what the whole location
               costs, and nobody buys that as one pool. */
            sub={p ? (p.agents > 1
              ? `${p.nodes_per_agent} per agent, ${p.node.cpu} vCPU / ${p.node.memory} each`
              : `${p.node.cpu} vCPU / ${p.node.memory} each`) : " "} />
          <Figure big n={p ? p.peak.cpu : "—"} unit="vCPU at peak"
            sub={p ? `${p.peak.memory} RAM` : " "} />
          <Figure big n={p ? 0 : "—"} unit="when idle"
            sub="the pool exists only during a run" />
        </div>
        {p ? (
          <p className="text-xs text-slate-500">
            Plus one small always-on node for the agent
            ({p.crane.cpu_limit} CPU / {p.crane.memory_limit}), and outbound HTTPS
            to {p.egress.map((h, i) => (
              <span key={h}>{i > 0 && ", "}<code>{h}</code></span>
            ))}. Each engine also needs {p.engine.disk_gb}GB of disk,
            {" "}{p.engine.tmp_gb}GB of it under <code>/tmp</code>.
          </p>
        ) : <p className="text-xs text-amber-700">{waiting}</p>}
      </div>

      <PlanCaveats assumed={!!p?.vus_per_engine_assumed}
        vusPerEngine={p?.vus_per_engine ?? 0}
        warnings={p?.warnings ?? []} />

      <div className={cardCls}>
        <div>
          <h3 className="text-sm font-semibold text-slate-800">
            The request to send
          </h3>
          <p className="text-xs text-slate-500 mt-0.5">
            The same numbers written for a platform team that has never heard of
            BlazeMeter — what to provision, what each figure came from, and the
            four location settings that decide whether the cluster gets used.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <Button onClick={props.onDownload} disabled={!p}>
            Download
          </Button>
          <Button kind="ghost" onClick={props.onCopy} disabled={!p}>
            {props.copied ? "Copied" : "Copy"}
          </Button>
          <Button kind="ghost" onClick={() => props.setShowDoc(!props.showDoc)}
            disabled={!p}>
            {props.showDoc ? "Hide" : "Preview"}
          </Button>
        </div>
        {/* No amber line here: the stats card directly above already carries it,
            and three copies of one sentence on one screen reads as three
            different problems. The last CTA gets its own, because it is far
            enough down to be reached without the first in view. */}
        {props.showDoc && p && (
          <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200
                          rounded-md p-3 overflow-auto max-h-96 whitespace-pre-wrap">
            {p.document}
          </pre>
        )}
      </div>

      <div className={cardCls}>
        <h3 className="text-sm font-semibold text-slate-800">
          Carry this into the deployment
        </h3>
        {p ? (
          <>
            <p className="text-xs text-slate-500">
              Fills in what this plan decided, so the numbers are not retyped: a
              new location is set to run <b>{p.location.slots} concurrent
              engines</b> (its <code>slots</code>) at{" "}
              <b>{p.location.threads_per_engine.toLocaleString()} virtual users
              each</b> (<code>threadsPerEngine</code>), and the bundle asks for{" "}
              <b>{p.engine.cpu} CPU / {p.engine.memory}</b> engines at{" "}
              {p.engines_per_node} per node. Nothing is created or changed by
              this — it moves you to step 1 with the fields already filled.
            </p>
            <p className="text-xs text-slate-500">
              The two request fields —{" "}
              {p.location.override_cpu === null ? (
                <>
                  <code>overrideMemory {p.location.override_memory}</code> and
                  an <code>overrideCPU</code> this engine size cannot state (the
                  field takes whole cores) —
                </>
              ) : (
                <>
                  <code>overrideCPU {p.location.override_cpu}</code> and{" "}
                  <code>overrideMemory {p.location.override_memory}</code> —
                </>
              )}{" "}
              are set in BlazeMeter rather than here, and the document says why
              they matter.
            </p>
            {/* An empty location and an agent that has never reported are normal
                states, not a half-finished setup -- so the wait for a cluster is
                setup time rather than dead time. */}
            <p className="text-xs text-slate-500">
              None of this waits for the cluster: the location and its agent can
              be created in BlazeMeter now, and the agent simply reports nothing
              until its manifests are applied.
            </p>
            {/* The re-plan case, which is the common one after a first run: the
                location exists, so these numbers are a change to it rather than
                to a form. That calculator lives on the location itself, seeded
                from what it already says, and this view is the one for a
                location that does not exist yet. */}
            <p className="text-xs text-slate-500">
              Already have the location? Open it in step 1 and use{" "}
              <b>Calculate</b> on its settings instead — it starts from what
              that location already says, and fills its fields.
            </p>
          </>
        ) : (
          <p className="text-xs text-slate-500">
            Carries the plan's concurrent engines, virtual users per engine and
            engine size into the deployment steps. Nothing is created or changed
            by it.
          </p>
        )}
        <Button onClick={props.onUse} disabled={!p}>Use</Button>
        {waiting && <p className="text-[11px] text-amber-700">{waiting}</p>}
      </div>
    </div>
  );
}

