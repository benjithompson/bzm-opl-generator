// The sizing: what the run needs, above the locations it might run on.
//
// A *sizing*, never a profile (#155). A profile in this repo is a JSON file
// of generator options -- `profiles/*.json`, `out/profile.json`, `--profile`
// -- and this is a different kind of thing entirely: a statement of the
// capacity one functionality needs, which the planner turns into engines,
// nodes and a machine size.
//
// It was a view of its own -- "Plan capacity", beside Generate in the drawer --
// and being beside it was the problem. The planner is the *first* question ("how
// much cluster does 5,000 users need?"), the generator is the last, and a person
// who has neither an account nor a cluster had to know that the answer to the
// first one had to be carried into the second by hand, with a button, from
// another screen. So it is step 1's first card: sized before anything is
// connected, and every location below it measured against what it says.
//
// It reaches nothing, and that is the requirement rather than a property.
// /api/plan is arithmetic in this process -- no key, no account, no cluster --
// which is why this card renders and computes on a page nobody has connected.
// Any dependency added here puts the first step behind a later one.
//
// Nothing in it applies anything. The fields *are* the sizing: there is no
// Apply, because there is nothing to apply it to that is not already reading
// them -- the location panels below take their `after` column straight from
// this, and the engine size is the bundle's own option rather than a copy of
// one.
import { useState } from "react";

import { Api, SizingModel } from "../api";
import { Button, cardCls, Check, ErrorMsg, Field, Figure, inputCls, NumberInput,
         PlanCaveats, TextInput } from "../components";
import { EngineSizeSelect } from "../groups/SizingGroup";
import { ENGINE_SIZES } from "../optionGroups";
import { remove, save, SavedSizing, sizingNamed } from "../sizings";
import { PlanAsk, PlanInputs, useCapacityPlan, useEngineRating } from "../usePlan";

export function Sizing(props: {
  /** The caller of the local routes, handed down like every other route on this
   *  page. /api/plan reaches nothing outside this process, which is the whole
   *  reason this card works unconnected -- but it is still a request, and a
   *  request this page cannot swap is a request its tests cannot drive. */
  api: Api;
  /** What is being sized. The same record every location row measures itself
   *  against, assembled once by App -- the sizing rows are the planner's own
   *  (below) and the rest are bundle options. */
  ask: PlanAsk;
  /** The three models, served. Empty until /api/sizing-models lands, and then
   *  the card has no fields: a unit invented here to fill the gap is the one
   *  thing that would put a figure on screen this tool never measured. */
  models: SizingModel[];
  inputs: PlanInputs;
  setInputs: (v: PlanInputs) => void;
  /** Sizings saved under a name, and the writer for them. Held by App with
   *  every other piece of session state, for the same reason. */
  saved: SavedSizing[];
  setSaved: (v: SavedSizing[]) => void;
  /** The engine the bundle asks for. Written here because the sizing is for
   *  that engine and not for a second one held beside it. */
  setEngine: (cpu: string | null, mem: string | null) => void;
  setPerNode: (v: string) => void;
}) {
  const { ask, inputs, models } = props;
  // Open/closed, and what the request block is showing: the card's own, like
  // every other disclosure on this page. Nothing downstream reads them.
  const [open, setOpen] = useState(false);
  const [showDoc, setShowDoc] = useState(false);
  const [copied, setCopied] = useState(false);
  // The clipboard's own failure, which is not the plan's: the hook owns whether
  // the plan could be worked out, and a browser refusing the clipboard says
  // nothing about that.
  const [copyErr, setCopyErr] = useState<string | null>(null);

  // No `agents`: the card sizes the run, and how many agents will serve it is a
  // fact about a location, not about the load. Each location row re-asks with
  // its own count -- see usePlan.
  const { plan, err, busy } = useCapacityPlan(ask, props.api);

  // What a saved sizing would be called. The card's own, like every other
  // disclosure here: nothing downstream reads a half-typed name.
  const [name, setName] = useState("");

  const setTarget = (fid: string, v: string) => props.setInputs(
    { ...inputs, targets: { ...inputs.targets, [fid]: v } });
  const setFigure = (fid: string, v: string) => props.setInputs(
    { ...inputs, figures: { ...inputs.figures, [fid]: v } });
  // Ticking a functionality on and off. The target it was given is kept while
  // it is off: unticking is "not this run", and coming back to a box somebody
  // has to fill in again reads as the page having lost it.
  const toggle = (fid: string, on: boolean) => props.setInputs({
    ...inputs,
    functionalities: on
      ? [...inputs.functionalities, fid]
      : inputs.functionalities.filter((f) => f !== fid),
  });
  const sized = (m: SizingModel) => inputs.functionalities.includes(
    m.functionality);
  // What the plan said about a model, where it said anything: the three-valued
  // answer lives on the server and this only renders it.
  const answer = (fid: string) =>
    plan?.sizings.find((s) => s.functionality === fid) ?? null;

  // Blank is the standard engine, which is what both sides assume when no size
  // is named (plan.py, and generate.ENGINE_DEFAULT_CPU / _MEM) -- so a card that
  // had not been touched showed "Custom" and two empty boxes for a size it was
  // in fact planning against.
  //
  // Which is why "Custom…" has to be remembered rather than derived: it clears
  // both limits, and cleared limits read back as Standard, so choosing it
  // snapped the select straight back and the two fields never appeared. It is a
  // view's state -- which fields are on screen -- and not a value anything
  // downstream reads.
  const [custom, setCustom] = useState(false);
  const preset = custom ? "custom" : (ENGINE_SIZES.find(
    (s) => s.cpu === ask.engineCpu && s.mem === ask.engineMem)?.id
    ?? (ask.engineCpu || ask.engineMem ? "custom" : "standard"));
  const size = ENGINE_SIZES.find((s) => s.id === preset);
  // What the chosen size is rated for, per model, whether or not a target has
  // been typed: the figure is most use *before* one is, since that is when the
  // size is being chosen. Keyed by funcId, so each row reads its own -- the
  // card asks nothing about which model it is drawing.
  const rated = useEngineRating(ask.engineCpu || size?.cpu,
                                ask.engineMem || size?.mem, props.api);
  const ratedFor = (fid: string) => rated?.[fid] ?? null;

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
    <section className="border border-slate-200 rounded-lg overflow-hidden bg-white">
      {/* The summary and the one control. A card that opened on its form would
          make every visit to step 1 begin with a calculator, and most of them
          are about the agent below it. */}
      <div className="flex items-center gap-3 px-3 py-2.5 bg-slate-50
                      border-b border-slate-200">
        <div className="grow min-w-0">
          <p className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold">
            Sizing
          </p>
          <p className={"text-sm mt-0.5 " + (busy ? "opacity-50" : "")}>
            {plan ? (
              // The whole chain, because the total is node capacity and no two
              // adjacent figures multiply into it: 10 engines at 2 CPU is 20,
              // and the answer is 30, the difference being the CPU and memory
              // a node spends on itself before any pod sees any (one per node,
              // so it scales with nodes rather than with engines). Stated as
              // "10 engines × 2 CPU / 8Gi · 30 vCPU total" it read as
              // arithmetic that does not work, and a summary a reader has to
              // distrust is worse than one that is longer.
              <span className="text-slate-800 tabular-nums">
                {/* Every sizing in its own unit, because two of the three are
                    not virtual users and a summary that said VUs about a
                    browser suite would be a figure about somebody else's
                    workload. */}
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

      {/* Downward, inside the card, on the same 0fr -> 1fr grid as every other
          disclosure here. */}
      <div aria-hidden={!open}
        className={"grid transition-[grid-template-rows] duration-[180ms] ease-out "
          + (open ? "grid-rows-[1fr]" : "grid-rows-[0fr] invisible")}>
        <div className="overflow-hidden">
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

            {/* One block per model, each asked for in its own unit. A location
                that runs several is sized for the largest of them, which is the
                server's rule and is stated below rather than worked out here.
                Rendered from the served table: a fourth model arrives by being
                added to plan.py, and until the table lands there is nothing to
                render, because a unit invented here to fill the gap would put a
                figure on screen this tool never measured. */}
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
                    {/* A model with no measured figure gets no box. Blank there
                        would be a figure nobody supplied, which is the state
                        this whole card has to keep apart from a figure this
                        tool chose -- and the explanation is the server's
                        sentence, which arrives as a warning below or as the
                        refusal in its place. */}
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
                      <p className="text-[11px] text-amber-700 self-center">
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
              {/* The bundle's own engine size, edited here as well as in the
                  Configure step's Sizing group: one option, two views of it. */}
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
                {/* Em-dashes rather than zeroes: nothing has been worked out
                    yet, and "0 engines" is an answer. */}
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
              {/* Which sizing the pod count came from. Only where there is more
                  than one, because with one it is the only answer there could
                  be -- and it is the server's `driven_by` rather than the
                  largest of what is on screen, which would be this page
                  deciding it a second time. */}
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
                // Only where nothing has been asked. With a refusal on screen
                // the reason is the refusal, and telling somebody to give a
                // target they have just given reads as the page not listening.
                <p className="text-xs text-amber-700">
                  tick a functionality and give it a target to size this run
                </p>
              )}
              <PlanCaveats sizings={plan?.sizings ?? []}
                warnings={plan?.warnings ?? []} />
            </div>

            {/* The request document, inside the editor: it is the same numbers
                written for a platform team, so it belongs beside the fields
                that decide them rather than in a card of its own. */}
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
                  <span className="text-[11px] text-amber-700">
                    give a sizing above a target
                  </span>
                )}
              </div>
              {showDoc && plan && (
                <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200
                                rounded-md p-3 overflow-auto max-h-96 whitespace-pre-wrap">
                  {plan.document}
                </pre>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}


/** The unit as a field label. Served lower-case, because it is prose in the
 *  request document first ("of up to 5,000 virtual users") and a label
 *  second. */
function _cap(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** What blank does in this model's figure box.
 *
 *  One sentence with the number in it wherever the number is known, which since
 *  the rating is served per model is every model with a measured figure. It was
 *  two wordings and a branch on `functionality === "performance"`: the route
 *  answered in virtual users alone, so a browser field could only say what
 *  blank *meant* about a figure the server could already have given it.
 *
 *  Null is still a real answer -- the rating has not arrived, or the size does
 *  not parse -- and the general sentence is what it says. */
function _figureHint(rated: number | null) {
  return rated
    ? `blank uses ${rated.toLocaleString()}, what this engine size is rated for`
    : "blank uses what a pod of this size is rated for";
}


/** Sizings saved under a name: pick one to fill the fields, or name what is in
 *  them now.
 *
 *  Picking is the only thing on this card that could be called "apply", and it
 *  is not one: it writes the fields, and the fields *are* the sizing. There is
 *  still nothing to apply them to -- the location panels below read them where
 *  they stand, and the engine size is the bundle's own option. */
function SavedSizings(props: {
  name: string; setName: (v: string) => void;
  saved: SavedSizing[]; setSaved: (v: SavedSizing[]) => void;
  inputs: PlanInputs; setInputs: (v: PlanInputs) => void;
}) {
  const { saved, name } = props;
  const exists = saved.some((s) => s.name === name.trim());
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field label="Saved sizings"
        hint="starting points, not recommendations — picking one fills the fields below">
        <select className={inputCls} value=""
          onChange={(e) => {
            const picked = sizingNamed(saved, e.target.value);
            if (picked) { props.setInputs(picked); props.setName(e.target.value); }
          }}>
          <option value="">Pick a sizing…</option>
          {saved.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
        </select>
      </Field>
      <Field label="Save this as">
        <TextInput placeholder="Black Friday" value={name}
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
  );
}
