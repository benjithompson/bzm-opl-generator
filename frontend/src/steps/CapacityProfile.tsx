// The capacity profile: what the run needs, above the locations it might run on.
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
// Nothing in it applies anything. The fields *are* the profile: there is no
// Apply, because there is nothing to apply it to that is not already reading
// them -- the location panels below take their `after` column straight from
// this, and the engine size is the bundle's own option rather than a copy of
// one.
import { useState } from "react";

import { Api } from "../api";
import { Button, cardCls, ErrorMsg, Field, Figure, NumberInput, PlanCaveats,
         TextInput } from "../components";
import { EngineSizeSelect } from "../groups/SizingGroup";
import { ENGINE_SIZES } from "../optionGroups";
import { PlanAsk, PlanInputs, useCapacityPlan, useEngineRating } from "../usePlan";

export function CapacityProfile(props: {
  /** The caller of the local routes, handed down like every other route on this
   *  page. /api/plan reaches nothing outside this process, which is the whole
   *  reason this card works unconnected -- but it is still a request, and a
   *  request this page cannot swap is a request its tests cannot drive. */
  api: Api;
  /** What is being sized. The same record every location row measures itself
   *  against, assembled once by App -- two of its fields are the planner's own
   *  (below) and three are bundle options. */
  ask: PlanAsk;
  setInputs: (v: PlanInputs) => void;
  /** The engine the bundle asks for. Written here because the profile is sized
   *  for that engine and not for a second one held beside it. */
  setEngine: (cpu: string | null, mem: string | null) => void;
  setPerNode: (v: string) => void;
}) {
  const { ask } = props;
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

  // The two the planner owns, back out of the ask they were assembled into.
  const inputs: PlanInputs = {
    users: ask.users, vusPerEngine: ask.vusPerEngine ?? "" };
  const set = (k: keyof PlanInputs, v: string) =>
    props.setInputs({ ...inputs, [k]: v });

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
  // What the chosen size is rated for, whether or not a target has been typed:
  // the figure is most use *before* one is, since that is when the size is
  // being chosen.
  const rated = useEngineRating(ask.engineCpu || size?.cpu,
                                ask.engineMem || size?.mem, props.api);

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
            Capacity profile
          </p>
          <p className={"text-sm mt-0.5 " + (busy ? "opacity-50" : "")}>
            {plan ? (
              <span className="text-slate-800 tabular-nums">
                {plan.users.toLocaleString()} VUs · {plan.engines} engine
                {plan.engines === 1 ? "" : "s"} · {plan.engine.cpu} CPU
                {" / "}{plan.engine.memory}
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

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Virtual user target" required
                hint="the load the test has to reach">
                <NumberInput placeholder="5000" value={ask.users}
                  onChange={(v) => set("users", v)} />
              </Field>
              {/* The placeholder follows the engine size, because so does the
                  figure the plan assumes when this is blank. A fixed 500 beside
                  a Large engine said the plan would use 500 when it uses
                  1,000. */}
              <Field label="Virtual users per engine"
                hint={rated
                  ? `blank uses ${rated.toLocaleString()}, what this engine size is rated for`
                  : "blank uses what an engine of this size is rated for"}>
                <NumberInput placeholder={String(rated ?? 500)}
                  value={ask.vusPerEngine ?? ""}
                  onChange={(v) => set("vusPerEngine", v)} />
              </Field>
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
              {plan ? (
                <p className="text-xs text-slate-500">
                  Plus one small always-on node for the agent
                  ({plan.crane.cpu_limit} CPU / {plan.crane.memory_limit}), and
                  outbound HTTPS to {plan.egress.map((h, i) => (
                    <span key={h}>{i > 0 && ", "}<code>{h}</code></span>
                  ))}. Each engine also needs {plan.engine.disk_gb}GB of disk,
                  {" "}{plan.engine.tmp_gb}GB of it under <code>/tmp</code>.
                </p>
              ) : (
                <p className="text-xs text-amber-700">
                  enter a virtual user target to size a profile
                </p>
              )}
              <PlanCaveats assumed={!!plan?.vus_per_engine_assumed}
                vusPerEngine={plan?.vus_per_engine ?? 0}
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
                {!plan && (
                  <span className="text-[11px] text-amber-700">
                    enter a virtual user target above
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
