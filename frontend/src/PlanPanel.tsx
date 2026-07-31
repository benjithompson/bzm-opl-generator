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
  threadsPerEngine: string;
  engineCpu: string;
  engineMem: string;
  enginesPerNode: string;
  name: string;
}

export const EMPTY_PLAN_INPUTS: PlanInputs = {
  users: "", threadsPerEngine: "", engineCpu: "", engineMem: "",
  enginesPerNode: "", name: "",
};

/** The plan as the generator's own vocabulary: what the location has to
 *  advertise, and what the bundle has to ask for. Named here because this is
 *  where the translation is decided; App applies it. */
export interface PlanHandover {
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
        users: inputs.users, threads_per_engine: inputs.threadsPerEngine,
        engine_cpu: inputs.engineCpu, engine_mem: inputs.engineMem,
        engines_per_node: inputs.enginesPerNode, name: inputs.name,
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
          <Field label="Concurrent users"
            hint="the load the test has to reach">
            <input type="number" min={1} className={inputCls}
              placeholder="5000" value={inputs.users}
              onChange={(e) => set("users", e.target.value)} />
          </Field>
          <Field label="Users per engine"
            hint="blank assumes BlazeMeter's figure for the engine size">
            <input type="number" min={1} className={inputCls}
              placeholder={String(plan?.engine.supported_threads ?? 500)}
              value={inputs.threadsPerEngine}
              onChange={(e) => set("threadsPerEngine", e.target.value)} />
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
          <Field label="What is being tested"
            hint="optional — titles the request document">
            <TextInput placeholder="Checkout API" value={inputs.name}
              onChange={(v) => set("name", v)} />
          </Field>
        </div>
        <ErrorMsg msg={err} />
      </div>

      {plan && <PlanResult plan={plan} busy={busy} showDoc={showDoc}
                           setShowDoc={setShowDoc} onDownload={download}
                           onCopy={copy} copied={copied}
                           onUse={() => props.onUse({
                             slots: plan.location.slots,
                             threadsPerEngine: plan.location.threads_per_engine,
                             engineCpuLimit: plan.engine.cpu,
                             engineMemLimit: plan.engine.memory,
                             enginesPerNode: plan.engines_per_node,
                           })} />}
    </div>
  );
}

function PlanResult(props: {
  plan: CapacityPlan; busy: boolean; showDoc: boolean;
  setShowDoc: (v: boolean) => void; onDownload: () => void; onCopy: () => void;
  copied: boolean; onUse: () => void;
}) {
  const p = props.plan;
  return (
    <div className={"space-y-4 transition-opacity "
      + (props.busy ? "opacity-50" : "")}>
      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-3">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Stat n={p.engines} unit={p.engines === 1 ? "engine" : "engines"}
            sub={`${p.engine.cpu} CPU / ${p.engine.memory} each`} />
          <Stat n={p.nodes} unit={p.nodes === 1 ? "node" : "nodes"}
            sub={`${p.node.cpu} vCPU / ${p.node.memory} each`} />
          <Stat n={p.peak.cpu} unit="vCPU at peak" sub={`${p.peak.memory} RAM`} />
          <Stat n={0} unit="when idle"
            sub="the pool exists only during a run" />
        </div>
        <p className="text-xs text-slate-500">
          Plus one small always-on node for the agent
          ({p.crane.cpu_limit} CPU / {p.crane.memory_limit}), and outbound HTTPS
          to {p.egress.map((h, i) => (
            <span key={h}>{i > 0 && ", "}<code>{h}</code></span>
          ))}. Each engine also needs {p.engine.disk_gb}GB of disk,
          {" "}{p.engine.tmp_gb}GB of it under <code>/tmp</code>.
        </p>
      </div>

      {/* The assumption, in the panel and not only in the document. Somebody
          who reads the node count off the screen and never opens the request
          is exactly who this has to reach. */}
      {p.threads_per_engine_assumed && (
        <div className="border border-amber-300 bg-amber-50 rounded-lg p-3">
          <p className="text-xs text-amber-900">
            <b>{p.threads_per_engine} users per engine is assumed</b>, not
            measured — it is BlazeMeter's figure for an engine this size. How
            many users one engine really carries depends on what your script
            does between requests, and every number above is that figure
            multiplied out. Run the real script against one engine, find where
            it saturates, and put that number in the field above.
          </p>
        </div>
      )}

      {p.warnings.map((w) => (
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
          <Button onClick={props.onDownload}>Download {p.document_file}</Button>
          <Button kind="ghost" onClick={props.onCopy}>
            {props.copied ? "Copied" : "Copy as Markdown"}
          </Button>
          <Button kind="ghost" onClick={() => props.setShowDoc(!props.showDoc)}>
            {props.showDoc ? "Hide" : "Preview"}
          </Button>
        </div>
        {props.showDoc && (
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
        <p className="text-xs text-slate-500">
          Fills in what this plan decided, so the numbers are not retyped: a new
          location gets <b>slots {p.location.slots}</b> and
          {" "}<b>{p.location.threads_per_engine} threads per engine</b>, and the
          bundle asks for <b>{p.engine.cpu} CPU / {p.engine.memory}</b> engines
          at {p.engines_per_node} per node. Nothing is created or changed by
          this — it moves you to step 1 with the fields already filled.
        </p>
        <p className="text-xs text-slate-500">
          The two request fields — <code>overrideCPU {p.location.override_cpu}</code>
          {" "}and <code>overrideMemory {p.location.override_memory_mb}</code> — are
          set in BlazeMeter rather than here, and the document says why they
          matter.
        </p>
        <Button onClick={props.onUse}>Use this plan</Button>
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
