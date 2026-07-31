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
import { useEffect, useRef, useState } from "react";

import { api, CapacityPlan } from "./api";
import { Button, ErrorMsg, Field, inputCls, TextInput } from "./components";
import { ENGINE_SIZES } from "./optionGroups";

export interface PlanInputs {
  users: string;
  vusPerEngine: string;
  engineCpu: string;
  engineMem: string;
  enginesPerNode: string;
}

export const EMPTY_PLAN_INPUTS: PlanInputs = {
  users: "", vusPerEngine: "", engineCpu: "", engineMem: "",
  enginesPerNode: "",
};

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
  const [plan, setPlan] = useState<CapacityPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDoc, setShowDoc] = useState(false);
  const [copied, setCopied] = useState(false);

  const set = (k: keyof PlanInputs, v: string) => setInputs({ ...inputs, [k]: v });

  // Debounced, because every keystroke in a number field is a plan: typing
  // "5000" passes through 5, 50 and 500, and three answers nobody wanted arrive
  // before the one they did. The blank target is not an error -- it is the
  // state the panel opens in -- so it clears rather than refusing.
  const timer = useRef<number>();
  useEffect(() => {
    if (!inputs.users.trim()) { setPlan(null); setErr(null); return; }
    window.clearTimeout(timer.current);
    setBusy(true);
    timer.current = window.setTimeout(() => {
      api.plan({
        users: inputs.users, vus_per_engine: inputs.vusPerEngine,
        engine_cpu: inputs.engineCpu, engine_mem: inputs.engineMem,
        engines_per_node: inputs.enginesPerNode,
      })
        .then((p) => { setPlan(p); setErr(null); })
        .catch((e: Error) => { setErr(e.message); setPlan(null); })
        .finally(() => setBusy(false));
    }, 250);
    return () => window.clearTimeout(timer.current);
  }, [inputs]);

  const preset = ENGINE_SIZES.find(
    (s) => s.cpu === inputs.engineCpu && s.mem === inputs.engineMem)?.id
    ?? (inputs.engineCpu || inputs.engineMem ? "custom" : "standard");

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
    }).catch(() => setErr("could not write to the clipboard"));
  };

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
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
          <Field label="Virtual users" required
            hint="the load the test has to reach">
            <input type="number" min={1} className={inputCls}
              placeholder="5000" value={inputs.users}
              onChange={(e) => set("users", e.target.value)} />
          </Field>
          {/* The placeholder follows the engine size, because so does the
              figure the plan assumes when this is blank. Showing a fixed 500
              beside a Large engine said the plan would use 500 when it uses
              1,000. */}
          <Field label="Virtual users per engine"
            hint="blank uses what an engine of this size is rated for">
            <input type="number" min={1} className={inputCls}
              placeholder={String(plan?.engine.supported_vus ?? 500)}
              value={inputs.vusPerEngine}
              onChange={(e) => set("vusPerEngine", e.target.value)} />
          </Field>
          <Field label="Engine size"
            hint="the pod limits every engine runs at">
            <select className={inputCls} value={preset}
              onChange={(e) => {
                const p = ENGINE_SIZES.find((s) => s.id === e.target.value);
                setInputs({ ...inputs, engineCpu: p?.cpu ?? "",
                            engineMem: p?.mem ?? "" });
              }}>
              {ENGINE_SIZES.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
              <option value="custom">Custom…</option>
            </select>
          </Field>
          <Field label="Engines per node"
            hint="blank means one — they contend when they share">
            <input type="number" min={1} className={inputCls} placeholder="1"
              value={inputs.enginesPerNode}
              onChange={(e) => set("enginesPerNode", e.target.value)} />
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
        <ErrorMsg msg={err} />
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
      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Em-dashes rather than zeroes: nothing has been worked out yet, and
              "0 engines" is an answer. */}
          <Stat n={p ? p.engines : "—"}
            unit={p && p.engines === 1 ? "engine" : "engines"}
            sub={p ? `${p.engine.cpu} CPU / ${p.engine.memory} each` : " "} />
          <Stat n={p ? p.nodes : "—"}
            unit={p && p.nodes === 1 ? "node" : "nodes"}
            sub={p ? `${p.node.cpu} vCPU / ${p.node.memory} each` : " "} />
          <Stat n={p ? p.peak.cpu : "—"} unit="vCPU at peak"
            sub={p ? `${p.peak.memory} RAM` : " "} />
          <Stat n={p ? 0 : "—"} unit="when idle"
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

      {p?.vus_per_engine_assumed && (
        <div className="border border-amber-300 bg-amber-50 rounded-lg p-3">
          <p className="text-xs text-amber-900">
            <b>{p.vus_per_engine.toLocaleString()} virtual users per engine is
            assumed</b>, not measured — it is what an engine of this size is
            rated for. How many virtual users one engine really carries depends
            on what your script does between requests, and every number above is
            that figure multiplied out. Run the real script against one engine,
            find where it saturates, and put that number in the field above.
          </p>
        </div>
      )}

      {(p?.warnings ?? []).map((w) => (
        <div key={w} className="border border-slate-200 bg-slate-50 rounded-lg p-3">
          <p className="text-xs text-slate-600">{w}</p>
        </div>
      ))}

      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
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
            Download {p ? p.document_file : "capacity-request.md"}
          </Button>
          <Button kind="ghost" onClick={props.onCopy} disabled={!p}>
            {props.copied ? "Copied" : "Copy as Markdown"}
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

      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-2">
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
              <code>overrideCPU {p.location.override_cpu}</code> and{" "}
              <code>overrideMemory {p.location.override_memory_mb}</code> — are
              set in BlazeMeter rather than here, and the document says why they
              matter.
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
        <Button onClick={props.onUse} disabled={!p}>Use this plan</Button>
        {waiting && <p className="text-[11px] text-amber-700">{waiting}</p>}
      </div>
    </div>
  );
}

function Stat({ n, unit, sub }: { n: number | string; unit: string; sub: string }) {
  return (
    <div className="border border-slate-200 rounded-md p-3">
      <div className="text-2xl font-bold text-slate-900 leading-none">{n}</div>
      <div className="text-xs font-medium text-slate-600 mt-1">{unit}</div>
      <div className="text-[11px] text-slate-400 mt-0.5">{sub}</div>
    </div>
  );
}
