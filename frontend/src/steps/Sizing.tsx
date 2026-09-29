// The sizing card, step 1's first: what the run needs (a sizing, not a
// profile), before anything is connected. /api/plan needs no key, account or
// cluster, and must stay that way. Nothing here applies anything: the fields
// are the sizing, and the location panels below read them.
import { useState } from "react";

import { Api, SizingModel } from "../api";
import { Button, cardCls, Check, Collapse, ErrorMsg, Field, Figure, inputCls, NumberInput,
         PlanCaveats, TextInput } from "../components";
import { EngineSizeSelect } from "../groups/EngineSizeSelect";
import { ENGINE_SIZES } from "../optionGroups";
import { remove, save, SavedSizing, sizingNamed } from "../sizings";
import { PlanAsk, PlanInputs, useCapacityPlan, useEngineRating } from "../usePlan";

export function Sizing(props: {
  /** The route caller; /api/plan reaches nothing outside this process. */
  api: Api;
  /** What is being sized, the same record every location row measures against. */
  ask: PlanAsk;
  /** The served sizing models. Empty until they land, and then there are no fields. */
  models: SizingModel[];
  inputs: PlanInputs;
  setInputs: (v: PlanInputs) => void;
  /** Sizings saved under a name, held by App with the session. */
  saved: SavedSizing[];
  setSaved: (v: SavedSizing[]) => void;
  /** Writes the bundle's engine size, which the sizing is for. */
  setEngine: (cpu: string | null, mem: string | null) => void;
  setPerNode: (v: string) => void;
}) {
  const { ask, inputs, models } = props;
  // View state; nothing downstream reads it.
  const [open, setOpen] = useState(false);
  const [showDoc, setShowDoc] = useState(false);
  const [copied, setCopied] = useState(false);
  // The clipboard's own failure, separate from the plan's.
  const [copyErr, setCopyErr] = useState<string | null>(null);

  // No `agents`: the card sizes the run; each location re-asks with its own count.
  const { plan, err, busy } = useCapacityPlan(ask, props.api);

  // A saved sizing's name, as typed.
  const [name, setName] = useState("");

  const setTarget = (fid: string, v: string) => props.setInputs(
    { ...inputs, targets: { ...inputs.targets, [fid]: v } });
  const setFigure = (fid: string, v: string) => props.setInputs(
    { ...inputs, figures: { ...inputs.figures, [fid]: v } });
  // Unticking keeps the target, so ticking again does not lose it.
  const toggle = (fid: string, on: boolean) => props.setInputs({
    ...inputs,
    functionalities: on
      ? [...inputs.functionalities, fid]
      : inputs.functionalities.filter((f) => f !== fid),
  });
  const sized = (m: SizingModel) => inputs.functionalities.includes(
    m.functionality);
  // What the plan said about a model, if anything.
  const answer = (fid: string) =>
    plan?.sizings.find((s) => s.functionality === fid) ?? null;

  // Blank limits are the standard engine. "Custom…" is remembered rather than
  // derived: it clears both limits, which would read back as Standard.
  const [custom, setCustom] = useState(false);
  const preset = custom ? "custom" : (ENGINE_SIZES.find(
    (s) => s.cpu === ask.engineCpu && s.mem === ask.engineMem)?.id
    ?? (ask.engineCpu || ask.engineMem ? "custom" : "standard"));
  const size = ENGINE_SIZES.find((s) => s.id === preset);
  // What the chosen size is rated for, per model, before any target is typed.
  const rated = useEngineRating(ask.engineCpu || size?.cpu,
                                ask.engineMem || size?.mem, props.api);
  const ratedFor = (fid: string) => rated?.[fid] ?? null;

  const download = () => {
    if (!plan) return;
    // The document is already in the answer; no second request.
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
    <section className="border border-slate-200 rounded-lg overflow-hidden bg-white">
      {/* The summary and the Edit control; the form starts folded. */}
      <div className="flex items-center gap-3 px-3 py-2.5 bg-slate-50
                      border-b border-slate-200">
        <div className="grow min-w-0">
          <p className="text-2xs uppercase tracking-wide text-slate-400 font-semibold">
            Sizing
          </p>
          <p className={"text-sm mt-0.5 " + (busy ? "opacity-50" : "")}>
            {plan ? (
              // The whole chain: the total is node capacity, which includes
              // what each node spends on itself, so no two adjacent figures
              // multiply into it.
              <span className="text-slate-800 tabular-nums">
                {/* Every sizing in its own unit. */}
                {plan.sizings.map((s) => (
                  `${s.target.toLocaleString()} ${s.unit}`)).join(" + ")}
                {" · "}{plan.engines} engine
                {plan.engines === 1 ? "" : "s"} × {plan.engine.cpu} CPU
                {" / "}{plan.engine.memory} · {plan.nodes} node
                {plan.nodes === 1 ? "" : "s"} × {plan.node.cpu} vCPU
                {" / "}{plan.node.memory} ·{" "}
                <span className="font-semibold">
                  {plan.peak.cpu} vCPU / {plan.peak.memory}
                </span> total
              </span>
            ) : <span className="text-amber-700">not sized yet</span>}
          </p>
        </div>
        <Button kind="ghost" onClick={() => setOpen(!open)}>
          {open ? "Done" : "Edit"}
        </Button>
      </div>

      <Collapse open={open}>
        <div className="p-3 space-y-3">
          <p className="text-xs text-slate-500">
            How much infrastructure this run needs, for the request you have to
            raise before any of it is deployed. Nothing here reaches BlazeMeter
            or a cluster, and nothing here writes anything — the locations
            below open on what these numbers would change about them.
          </p>

          <SavedSizings name={name} setName={setName} saved={props.saved}
            setSaved={props.setSaved} inputs={inputs}
            setInputs={props.setInputs} />

          {/* One block per served model, in its own unit. A location running
              several is sized for the largest; the server says which. */}
          {models.map((m) => (
            <div key={m.functionality}
              className="border border-slate-200 rounded-md p-3 space-y-2">
              <Check label={m.label} checked={sized(m)}
                hint={`sized in ${m.unit}`}
                onChange={(on) => toggle(m.functionality, on)} />
              {sized(m) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <Field label={_cap(m.unit)} required
                    hint={`what this run has to reach, in ${m.unit}`}>
                    <NumberInput
                      value={inputs.targets[m.functionality] ?? ""}
                      onChange={(v) => setTarget(m.functionality, v)} />
                  </Field>
                  {/* No box for an unmeasured model; the server's warning or
                      refusal explains it. */}
                  {m.measured ? (
                    <Field label={_cap(m.figure_unit)}
                      hint={_figureHint(ratedFor(m.functionality))}>
                      <NumberInput
                        placeholder={String(answer(m.functionality)?.per_pod
                          ?? ratedFor(m.functionality) ?? "")}
                        value={inputs.figures[m.functionality] ?? ""}
                        onChange={(v) => setFigure(m.functionality, v)} />
                    </Field>
                  ) : (
                    <p className="text-2xs text-amber-700 self-center">
                      No measured figure for {m.figure_unit}, so this is
                      stated in the request rather than sized from. Why, and
                      what to do about it, is below.
                    </p>
                  )}
                </div>
              )}
            </div>
          ))}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* The bundle's own engine size, also edited on the configure step. */}
            <EngineSizeSelect preset={preset} custom
              hint="the pod limits every engine runs at — the bundle asks for these"
              onPreset={(cpu, mem) => {
                setCustom(cpu === null && mem === null);
                props.setEngine(cpu, mem);
              }} />
            <Field label="Engines per node"
              hint="blank means one — they contend when they share">
              <NumberInput placeholder="1" value={ask.enginesPerNode ?? ""}
                onChange={props.setPerNode} />
            </Field>
            {preset === "custom" && (
              <>
                <Field label="Engine CPU limit">
                  <TextInput mono placeholder="2" value={ask.engineCpu ?? ""}
                    onChange={(v) => props.setEngine(v, ask.engineMem ?? "")} />
                </Field>
                <Field label="Engine memory limit">
                  <TextInput mono placeholder="8Gi" value={ask.engineMem ?? ""}
                    onChange={(v) => props.setEngine(ask.engineCpu ?? "", v)} />
                </Field>
              </>
            )}
          </div>
          <ErrorMsg msg={err ?? copyErr} />

          <div className={"space-y-3 transition-opacity " + (busy ? "opacity-50" : "")}>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              {/* Em-dashes, not zeroes: nothing has been worked out yet. */}
              <Figure big n={plan ? plan.engines : "—"}
                unit={plan && plan.engines === 1 ? "engine" : "engines"}
                sub={plan ? `${plan.engine.cpu} CPU / ${plan.engine.memory} each` : " "} />
              <Figure big n={plan ? plan.nodes : "—"}
                unit={plan && plan.nodes === 1 ? "node" : "nodes"}
                sub={plan ? `${plan.node.cpu} vCPU / ${plan.node.memory} each` : " "} />
              <Figure big n={plan ? plan.peak.cpu : "—"} unit="vCPU at peak"
                sub={plan ? `${plan.peak.memory} RAM` : " "} />
              <Figure big n={plan ? 0 : "—"} unit="when idle"
                sub="the pool exists only during a run" />
            </div>
            {/* Which sizing drove the pod count (the server's `driven_by`),
                only where there are several. */}
            {plan && plan.sizings.length > 1 && (
              <p className="text-xs text-slate-500">
                Sized for the{" "}
                <b>{models.find((m) => m.functionality === plan.driven_by)
                  ?.label ?? plan.driven_by}</b> sizing, the largest of
                these.
              </p>
            )}
            {plan ? (
              <p className="text-xs text-slate-500">
                Plus one small always-on node for the agent
                ({plan.crane.cpu_limit} CPU / {plan.crane.memory_limit}), and
                outbound HTTPS to {plan.egress.map((h, i) => (
                  <span key={h}>{i > 0 && ", "}<code>{h}</code></span>
                ))}. Each engine also needs {plan.engine.disk_gb}GB of disk,
                {" "}{plan.engine.tmp_gb}GB of it under <code>/tmp</code>.
              </p>
            ) : !err && (
              // Only where nothing has been asked; a refusal explains itself.
              <p className="text-xs text-amber-700">
                tick a functionality and give it a target to size this run
              </p>
            )}
            <PlanCaveats sizings={plan?.sizings ?? []}
              warnings={plan?.warnings ?? []} />
          </div>

          {/* The request document, written for a platform team, beside the
              fields that decide it. */}
          <div className={cardCls}>
            <div>
              <h3 className="text-sm font-semibold text-slate-800">
                The request to send
              </h3>
              <p className="text-xs text-slate-500 mt-0.5">
                The same numbers written for a platform team that has never
                heard of BlazeMeter — what to provision, what each figure came
                from, and the four location settings that decide whether the
                cluster gets used.
              </p>
            </div>
            <div className="flex gap-2 flex-wrap items-center">
              <Button onClick={download} disabled={!plan}>Download</Button>
              <Button kind="ghost" onClick={copy} disabled={!plan}>
                {copied ? "Copied" : "Copy"}
              </Button>
              <Button kind="ghost" onClick={() => setShowDoc(!showDoc)}
                disabled={!plan}>
                {showDoc ? "Hide" : "Preview"}
              </Button>
              {!plan && !err && (
                <span className="text-2xs text-amber-700">
                  give a sizing above a target
                </span>
              )}
            </div>
            {showDoc && plan && (
              <pre className="text-2xs font-mono bg-slate-50 border border-slate-200
                              rounded-md p-3 overflow-auto max-h-96 whitespace-pre-wrap">
                {plan.document}
              </pre>
            )}
          </div>
        </div>
      </Collapse>
    </section>
  );
}


/** The unit as a field label (served lower-case, as prose). */
function _cap(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** What leaving this model's figure blank means, with the rated number where
 *  it is known; a general sentence while it is not. */
function _figureHint(rated: number | null) {
  return rated
    ? `blank uses ${rated.toLocaleString()}, what this engine size is rated for`
    : "blank uses what a pod of this size is rated for";
}


/** Saved sizings: pick one to fill the fields, or save what is in them now
 *  under a name. Picking writes the fields and nothing else. */
function SavedSizings(props: {
  name: string; setName: (v: string) => void;
  saved: SavedSizing[]; setSaved: (v: SavedSizing[]) => void;
  inputs: PlanInputs; setInputs: (v: PlanInputs) => void;
}) {
  const { saved, name } = props;
  const exists = saved.some((s) => s.name === name.trim());
  return (
    <div className="space-y-1">
      {/* The hint sits under the whole row, so the two controls stay aligned. */}
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Saved sizings">
          <select className={inputCls} value=""
            onChange={(e) => {
              const picked = sizingNamed(saved, e.target.value);
              if (picked) { props.setInputs(picked); props.setName(e.target.value); }
            }}>
            <option value="">Pick a sizing…</option>
            {saved.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Save as">
          <TextInput placeholder="Staging" value={name}
            onChange={props.setName} />
        </Field>
        <div className="flex gap-2 pb-0.5">
          <Button kind="ghost" disabled={!name.trim()}
            onClick={() => props.setSaved(save(saved, name, props.inputs))}>
            Save
          </Button>
          <Button kind="ghost" disabled={!exists}
            onClick={() => { props.setSaved(remove(saved, name.trim()));
                             props.setName(""); }}>
            Delete
          </Button>
        </div>
      </div>
      <p className="text-2xs text-slate-400">
        Starting points, not recommendations — picking one fills the fields below.
      </p>
    </div>
  );
}
