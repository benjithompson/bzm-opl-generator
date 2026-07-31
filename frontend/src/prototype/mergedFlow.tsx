// PROTOTYPE — throwaway. Four ways to fold Plan capacity into Generate.
//
// The question: today the planner is a view of its own, the location's Calculate
// pane is a second calculator, and the location settings form is a third place
// the same four numbers appear. Planning should come first and its answer should
// carry -- as a *profile* that an existing location can be brought into line
// with, on purpose, with a confirmation.
//
//   ?variant=P  four steps -- Plan, Location & agent, Configure, Download
//   ?variant=Q  three steps under a persistent profile bar
//   ?variant=R  one scroll, target first, sections unfold as they are answered
//   ?variant=S  two-pane first step: the plan on the left, the location it is
//               being applied to on the right, the difference between them down
//               the middle
//
// Self-contained: mock locations, arithmetic done here rather than through
// /api/plan, and nothing writes anywhere. What is being judged is the shape of
// the flow, not the numbers -- those are core's and are already tested.
import { ReactNode, useMemo, useState } from "react";

export type Variant = "P" | "Q" | "R" | "S";
const VARIANTS: { id: Variant; label: string }[] = [
  { id: "P", label: "P · Plan as step 1" },
  { id: "Q", label: "Q · Profile bar" },
  { id: "R", label: "R · One scroll" },
  { id: "S", label: "S · Plan ↔ location" },
];

export function useVariant(): Variant | null {
  const v = new URLSearchParams(window.location.search).get("variant");
  return VARIANTS.some((x) => x.id === v) ? (v as Variant) : null;
}

// -- the mock account ---------------------------------------------------------

interface Loc {
  id: string; name: string; workspace: string; agents: number;
  slots: number; threadsPerEngine: number | null;
  cpu: number | null; mem: number | null;
}

const LOCS: Loc[] = [
  { id: "l1", name: "Bens Linux", workspace: "Ben T", agents: 2, slots: 1,
    threadsPerEngine: 50, cpu: null, mem: null },
  { id: "l2", name: "eu-perf-cluster", workspace: "Ben T", agents: 4, slots: 5,
    threadsPerEngine: 500, cpu: 2, mem: 8192 },
  { id: "l3", name: "staging-opl", workspace: "Platform", agents: 1, slots: 2,
    threadsPerEngine: null, cpu: null, mem: null },
];

// -- the arithmetic, as plan.py does it ---------------------------------------

const SIZES = [
  { id: "small", label: "Small — 1 CPU / 4Gi", cpu: 1, mem: 4 },
  { id: "standard", label: "Standard — 2 CPU / 8Gi", cpu: 2, mem: 8 },
  { id: "large", label: "Large — 4 CPU / 16Gi", cpu: 4, mem: 16 },
];

/** 500 VUs per 2 CPU / 8Gi, scaled on whichever dimension is tighter. */
function supportedVus(cpu: number, mem: number) {
  return Math.max(1, Math.floor(500 * Math.min(cpu / 2, mem / 8)));
}

interface Plan {
  users: number; vus: number; engines: number; sizeId: string;
  cpu: number; mem: number; agents: number; perAgent: number; nodes: number;
}

function usePlan(initialUsers = "") {
  const [users, setUsers] = useState(initialUsers);
  const [sizeId, setSizeId] = useState("standard");
  const [vusOverride, setVusOverride] = useState("");
  const [agents, setAgents] = useState("1");
  const size = SIZES.find((s) => s.id === sizeId)!;
  const rated = supportedVus(size.cpu, size.mem);
  const plan: Plan | null = useMemo(() => {
    const u = Number(users);
    if (!u || u < 1) return null;
    const vus = Number(vusOverride) || rated;
    const a = Math.max(1, Number(agents) || 1);
    const engines = Math.ceil(u / vus);
    const perAgent = Math.ceil(engines / a);
    return { users: u, vus, engines, sizeId, cpu: size.cpu, mem: size.mem,
             agents: a, perAgent, nodes: perAgent * a };
  }, [users, vusOverride, agents, sizeId, rated, size]);
  return { users, setUsers, sizeId, setSizeId, vusOverride, setVusOverride,
           agents, setAgents, rated, plan };
}

type PlanState = ReturnType<typeof usePlan>;

/** Engines per agent *for this location*, which is not the plan's own figure.
 *
 *  The planner asks how many agents there will be because before a location
 *  exists nobody can look it up. A location that exists has an answer, and it
 *  outranks the typed one -- against two agents the same 10 engines is 5 each,
 *  and writing the greenfield 10 into `slots` would size the location for
 *  twenty. This is the whole reason the plan is a *profile* rather than a set
 *  of values: what carries forward is the load target, not the arithmetic. */
function perAgentIn(plan: Plan, loc: Loc) {
  return Math.ceil(plan.engines / Math.max(loc.agents, 1));
}

/** What a plan would change about a location, in the location's own fields. */
function diff(plan: Plan, loc: Loc) {
  const rows = [
    { k: "Engines per agent", now: loc.slots, next: perAgentIn(plan, loc) },
    { k: "Virtual users per engine", now: loc.threadsPerEngine, next: plan.vus },
    { k: "Engine CPU request", now: loc.cpu, next: plan.cpu },
    { k: "Engine memory request (MB)", now: loc.mem, next: plan.mem * 1024 },
  ];
  return rows.filter((r) => r.now !== r.next);
}

const capacityOf = (l: Loc) =>
  l.threadsPerEngine ? l.agents * l.slots * l.threadsPerEngine : null;

// -- small shared furniture ---------------------------------------------------

const card = "bg-white border border-slate-200 rounded-lg";
const btn = "rounded-md px-3 py-1.5 text-sm font-medium";
const primary = `${btn} bg-bzm text-white hover:bg-bzm/90 disabled:opacity-40`;
const ghost = `${btn} border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-40`;
const inp = "mt-0.5 w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-sm";

function F({ label, hint, children }: {
  label: string; hint?: string; children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
    </label>
  );
}

function PlanFields({ s, compact }: { s: PlanState; compact?: boolean }) {
  return (
    <div className={"grid gap-3 " + (compact ? "grid-cols-2" : "sm:grid-cols-2")}>
      <F label="Virtual user target" hint="the load the test has to reach">
        <input type="number" className={inp} placeholder="5000"
          value={s.users} onChange={(e) => s.setUsers(e.target.value)} />
      </F>
      <F label="Engine size" hint="the pod limits every engine runs at">
        <select className={inp} value={s.sizeId}
          onChange={(e) => s.setSizeId(e.target.value)}>
          {SIZES.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
        </select>
      </F>
      <F label="Virtual users per engine"
        hint={`blank uses ${s.rated}, what this engine size is rated for`}>
        <input type="number" className={inp} placeholder={String(s.rated)}
          value={s.vusOverride}
          onChange={(e) => s.setVusOverride(e.target.value)} />
      </F>
      <F label="Agents available" hint="engines are shared out between them">
        <input type="number" className={inp} placeholder="1"
          value={s.agents} onChange={(e) => s.setAgents(e.target.value)} />
      </F>
    </div>
  );
}

function PlanAnswer({ plan }: { plan: Plan | null }) {
  if (!plan) {
    return (
      <p className="text-xs text-amber-700">
        enter a virtual user target to size a plan
      </p>
    );
  }
  const stat = (n: number | string, unit: string, sub: string) => (
    <div>
      <div className="text-xl font-bold text-slate-900 tabular-nums leading-none">{n}</div>
      <div className="text-[11px] text-slate-500 mt-0.5">{unit}</div>
      <div className="text-[10px] text-slate-400">{sub}</div>
    </div>
  );
  return (
    <div className="grid grid-cols-4 gap-3">
      {stat(plan.engines, plan.engines === 1 ? "engine" : "engines",
            `${plan.cpu} CPU / ${plan.mem}Gi each`)}
      {stat(plan.perAgent, "engines per agent", `across ${plan.agents} agent${plan.agents === 1 ? "" : "s"}`)}
      {stat(plan.nodes, plan.nodes === 1 ? "node" : "nodes", "one engine each")}
      {stat(plan.engines * plan.cpu, "vCPU at peak",
            `${plan.engines * plan.mem}Gi RAM`)}
    </div>
  );
}

/** The confirmation every variant shows before a location is changed. One
 *  component because it is one decision -- the differences between the variants
 *  are where it is reached from, not what it says. */
function ApplyModal({ plan, loc, onClose, onDone }: {
  plan: Plan; loc: Loc; onClose: () => void; onDone: (msg: string) => void;
}) {
  const rows = diff(plan, loc);
  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4"
      onClick={onClose}>
      <div className={card + " w-[34rem] max-w-full p-4 space-y-3"}
        onClick={(e) => e.stopPropagation()}>
        <div>
          <h3 className="text-sm font-semibold text-slate-900">
            Change {loc.name} in BlazeMeter?
          </h3>
          <p className="text-[11px] text-slate-500 mt-0.5">
            This writes to the account. None of it is in the manifests, so
            nothing has to be regenerated — it applies to the next test that
            starts, for every agent in this location and everyone else&apos;s
            tests too.
          </p>
        </div>
        <table className="w-full text-xs">
          <tbody>
            {rows.map((r) => (
              <tr key={r.k} className="border-t border-slate-100">
                <td className="py-1.5 text-slate-600">{r.k}</td>
                <td className="py-1.5 text-right tabular-nums text-slate-400">
                  {r.now ?? "not set"}
                </td>
                <td className="py-1.5 px-2 text-slate-300">→</td>
                <td className="py-1.5 text-right tabular-nums font-medium">{r.next}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td className="py-2 text-slate-500">
                nothing to change — this location already matches the plan
              </td></tr>
            )}
          </tbody>
        </table>
        <div className="flex gap-2">
          <button className={primary} disabled={rows.length === 0}
            onClick={() => { onDone(`applied to ${loc.name}`); onClose(); }}>
            Apply
          </button>
          <button className={ghost} onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

/** How a location stands against the plan, on its row in the list. */
function Fit({ plan, loc }: { plan: Plan | null; loc: Loc }) {
  if (!plan) {
    const c = capacityOf(loc);
    return (
      <span className="text-[11px] text-slate-400">
        {c === null ? "no rating" : `${c.toLocaleString()} VUs`}
      </span>
    );
  }
  const n = diff(plan, loc).length;
  return (
    <span className="text-right shrink-0">
      {n === 0
        ? <span className="text-[11px] text-emerald-700">matches the plan</span>
        : <span className="text-[11px] text-amber-700">
            {n} setting{n === 1 ? "" : "s"} differ
          </span>}
      {/* Said on the row, because it is the number the plan's own "engines per
          agent" gets replaced by here, and it changes per location. */}
      <span className="block text-[10px] text-slate-400">
        {loc.agents} agent{loc.agents === 1 ? "" : "s"} → {perAgentIn(plan, loc)} each
      </span>
    </span>
  );
}

function LocationList({ plan, picked, onPick, onApply }: {
  plan: Plan | null; picked: string | null;
  onPick: (id: string) => void; onApply: (l: Loc) => void;
}) {
  return (
    <div className="space-y-1.5">
      {LOCS.map((l) => (
        <div key={l.id}
          className={"border rounded-md px-3 py-2 flex items-center gap-3 cursor-pointer "
            + (picked === l.id ? "border-bzm bg-sky-50" : "border-slate-200 hover:bg-slate-50")}
          onClick={() => onPick(l.id)}>
          <div className="grow min-w-0">
            <div className="text-sm font-medium text-slate-800">{l.name}</div>
            <div className="text-[11px] text-slate-400">
              {l.workspace} · {l.agents} agent{l.agents === 1 ? "" : "s"} ·{" "}
              {l.slots} engines/agent ·{" "}
              {l.threadsPerEngine ?? "no"} VUs/engine
            </div>
          </div>
          <Fit plan={plan} loc={l} />
          {plan && picked === l.id && diff(plan, l).length > 0 && (
            <button className={ghost + " shrink-0"}
              onClick={(e) => { e.stopPropagation(); onApply(l); }}>
              Apply plan
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function Stepper({ at, go, steps }: {
  at: number; go: (n: number) => void; steps: string[];
}) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {steps.map((s, i) => (
        <button key={s} onClick={() => go(i)}
          className={"rounded-full px-3 py-1 text-xs font-medium flex items-center gap-1.5 "
            + (i === at ? "bg-bzm text-white"
              : i < at ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500")}>
          <span className="tabular-nums">{i < at ? "✓" : i + 1}</span>{s}
        </button>
      ))}
    </div>
  );
}

/** Stand-in for the two steps this prototype is not changing. */
function Placeholder({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className={card + " p-4"}>
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      <ul className="mt-2 space-y-1">
        {lines.map((l) => (
          <li key={l} className="text-xs text-slate-400 border border-dashed
                                 border-slate-200 rounded px-2 py-1.5">{l}</li>
        ))}
      </ul>
    </div>
  );
}

const CONFIGURE = ["Feature: Performance / Service virtualization",
                   "Namespace, service account, RBAC",
                   "Registry · Proxy · CA trust · Scheduling · Security"];
const DOWNLOAD = ["Format: manifests or Helm chart", "Download bundle (.zip)",
                  "Preflight the target cluster", "Agent status"];

// -- P: plan is step 1 --------------------------------------------------------

function VariantP({ toast }: { toast: (m: string) => void }) {
  const s = usePlan();
  const [at, setAt] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const [applying, setApplying] = useState<Loc | null>(null);
  const loc = LOCS.find((l) => l.id === picked) ?? null;
  const steps = ["Plan capacity", "Location & agent", "Configure", "Download"];

  return (
    <div className="max-w-screen-lg mx-auto p-6 space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Stepper at={at} go={setAt} steps={steps} />
        <span className="grow" />
        <button className={ghost} disabled={at === 0} onClick={() => setAt(at - 1)}>← Back</button>
        <button className={primary} disabled={at === steps.length - 1 || (at === 0 && !s.plan)}
          onClick={() => setAt(at + 1)}>Next →</button>
      </div>

      {/* The plan rides the flow once it exists: every later step is being
          taken *for* this number, and a step 1 you cannot see from step 3 is
          how the engine size ends up disagreeing with what was requested. */}
      {s.plan && (
        <div className="flex items-center gap-2 text-xs bg-sky-50 border border-sky-200
                        rounded-md px-3 py-1.5">
          <span className="font-semibold text-sky-900">Profile</span>
          <span className="text-sky-800">
            {s.plan.users.toLocaleString()} VUs · {s.plan.engines} engines
            {" "}({s.plan.perAgent}/agent) · {s.plan.cpu} CPU / {s.plan.mem}Gi
            {" "}· {s.plan.vus} VUs each
          </span>
          <span className="grow" />
          {at !== 0 && (
            <button className="text-sky-700 underline" onClick={() => setAt(0)}>
              Edit plan
            </button>
          )}
        </div>
      )}

      {at === 0 && (
        <div className={card + " p-4 space-y-3"}>
          <div>
            <h3 className="text-sm font-semibold text-slate-800">
              How much infrastructure will this need?
            </h3>
            <p className="text-xs text-slate-500">
              First, because it decides everything after it — and it works
              before any of it exists. Nothing here reaches BlazeMeter or a
              cluster.
            </p>
          </div>
          <PlanFields s={s} />
          <div className="border-t border-slate-100 pt-3"><PlanAnswer plan={s.plan} /></div>
          <div className="flex gap-2">
            <button className={ghost} disabled={!s.plan}
              onClick={() => toast("capacity-request.md downloaded")}>
              Download request
            </button>
            <span className="text-[11px] text-slate-400 self-center">
              the markdown to attach to the infrastructure ticket
            </span>
          </div>
        </div>
      )}

      {at === 1 && (
        <div className={card + " p-4 space-y-3"}>
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Location &amp; agent</h3>
            <p className="text-xs text-slate-500">
              Each location is measured against the plan. Picking one that
              differs offers to bring it into line — that is a write to the
              account, so it asks first.
            </p>
          </div>
          <LocationList plan={s.plan} picked={picked} onPick={setPicked}
            onApply={setApplying} />
          <button className={ghost} onClick={() => toast("new location, seeded from the plan")}>
            + New location{s.plan ? ` (${s.plan.perAgent} × ${s.plan.vus})` : ""}
          </button>
          {loc && (
            <div className="border-t border-slate-100 pt-3">
              <p className="text-xs text-slate-500">
                Agent: <b>agent-1</b> · 2 agents in this location
              </p>
            </div>
          )}
        </div>
      )}

      {at === 2 && <Placeholder title="Configure" lines={CONFIGURE} />}
      {at === 3 && <Placeholder title="Download &amp; verify" lines={DOWNLOAD} />}

      {applying && s.plan && (
        <ApplyModal plan={s.plan} loc={applying} onClose={() => setApplying(null)}
          onDone={toast} />
      )}
    </div>
  );
}

// -- Q: the profile bar -------------------------------------------------------

function VariantQ({ toast }: { toast: (m: string) => void }) {
  const s = usePlan("5000");
  const [at, setAt] = useState(0);
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string | null>("l1");
  const [applying, setApplying] = useState<Loc | null>(null);
  const steps = ["Location & agent", "Configure", "Download"];

  return (
    <div className="max-w-screen-lg mx-auto p-6 space-y-4">
      {/* Not a step. The plan is a property of the whole session -- it is true
          in step 3 as much as in step 1 -- so it sits above the flow and is
          editable from anywhere, and the flow keeps the three steps it had. */}
      <div className={card + " overflow-hidden"}>
        <div className="flex items-center gap-3 px-3 py-2">
          <span className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold">
            Capacity profile
          </span>
          <span className="text-sm text-slate-800">
            {s.plan
              ? <>{s.plan.users.toLocaleString()} VUs · {s.plan.engines} engines
                  {" "}({s.plan.perAgent}/agent) · {s.plan.cpu} CPU / {s.plan.mem}Gi</>
              : <span className="text-amber-700">not sized yet</span>}
          </span>
          <span className="grow" />
          <button className={ghost} onClick={() => setOpen(!open)}>
            {open ? "Done" : "Edit"}
          </button>
        </div>
        <div className={"grid transition-[grid-template-rows] duration-200 "
          + (open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
          <div className="overflow-hidden">
            <div className="border-t border-slate-200 p-3 space-y-3 bg-slate-50">
              <PlanFields s={s} compact />
              <PlanAnswer plan={s.plan} />
              <div className="flex gap-2">
                <button className={primary} onClick={() => setOpen(false)}>Apply</button>
                <button className={ghost} onClick={() => setOpen(false)}>Cancel</button>
                <button className={ghost} disabled={!s.plan}
                  onClick={() => toast("capacity-request.md downloaded")}>
                  Download request
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <Stepper at={at} go={setAt} steps={steps} />
        <span className="grow" />
        <button className={ghost} disabled={at === 0} onClick={() => setAt(at - 1)}>← Back</button>
        <button className={primary} disabled={at === steps.length - 1}
          onClick={() => setAt(at + 1)}>Next →</button>
      </div>

      {at === 0 && (
        <div className={card + " p-4 space-y-3"}>
          <h3 className="text-sm font-semibold text-slate-800">Location &amp; agent</h3>
          <LocationList plan={s.plan} picked={picked} onPick={setPicked}
            onApply={setApplying} />
          {picked && s.plan && (
            <div className="border border-slate-200 rounded-md bg-slate-50 p-3 space-y-2">
              <p className="text-xs font-semibold text-slate-700">Location settings</p>
              {/* No Calculate button here: the profile above *is* the
                  calculator, and a second one was the redundancy. */}
              <DiffTable plan={s.plan} loc={LOCS.find((l) => l.id === picked)!} />
              <div className="flex gap-2 items-center">
                <button className={primary}
                  onClick={() => setApplying(LOCS.find((l) => l.id === picked)!)}>
                  Apply profile
                </button>
                <button className={ghost} onClick={() => toast("opened the fields for a manual edit")}>
                  Edit by hand
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {at === 1 && <Placeholder title="Configure" lines={CONFIGURE} />}
      {at === 2 && <Placeholder title="Download &amp; verify" lines={DOWNLOAD} />}

      {applying && s.plan && (
        <ApplyModal plan={s.plan} loc={applying} onClose={() => setApplying(null)}
          onDone={toast} />
      )}
    </div>
  );
}

function DiffTable({ plan, loc }: { plan: Plan; loc: Loc }) {
  const rows = diff(plan, loc);
  if (!rows.length) {
    return <p className="text-[11px] text-emerald-700">
      this location already matches the profile
    </p>;
  }
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map((r) => (
          <tr key={r.k} className="border-t border-slate-200">
            <td className="py-1 text-slate-600">{r.k}</td>
            <td className="py-1 text-right tabular-nums text-slate-400">{r.now ?? "not set"}</td>
            <td className="py-1 px-2 text-slate-300">→</td>
            <td className="py-1 text-right tabular-nums font-medium text-slate-800">{r.next}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// -- R: one scroll, target first ---------------------------------------------

function VariantR({ toast }: { toast: (m: string) => void }) {
  const s = usePlan();
  const [picked, setPicked] = useState<string | null>(null);
  const [applying, setApplying] = useState<Loc | null>(null);
  const loc = LOCS.find((l) => l.id === picked) ?? null;

  // Each section appears once the one above it has an answer. No stepper, no
  // Back/Next: the page is the sequence, and what is unanswered is what is
  // missing rather than what is behind a button.
  const block = (n: number, title: string, on: boolean, body: ReactNode) => (
    <section className={card + " p-4 space-y-3 transition-opacity "
      + (on ? "" : "opacity-40 pointer-events-none")}>
      <div className="flex items-baseline gap-2">
        <span className={"w-5 h-5 rounded-full text-[11px] font-bold flex items-center "
          + "justify-center shrink-0 "
          + (on ? "bg-bzm text-white" : "bg-slate-200 text-slate-500")}>{n}</span>
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      </div>
      {body}
    </section>
  );

  return (
    <div className="max-w-screen-md mx-auto p-6 space-y-4">
      <div className={card + " p-6 text-center space-y-3"}>
        <h2 className="text-base font-semibold text-slate-800">
          How many virtual users do you need to run?
        </h2>
        <input type="number" autoFocus placeholder="5000" value={s.users}
          onChange={(e) => s.setUsers(e.target.value)}
          className="w-48 mx-auto block text-center text-2xl font-bold tabular-nums
                     rounded-md border border-slate-300 px-3 py-2" />
        <p className="text-xs text-slate-500">
          Everything below follows from this. It works before anything is
          deployed — the answer is a request you can hand to a platform team.
        </p>
      </div>

      {block(1, "The plan", !!s.plan, (
        <>
          <PlanAnswer plan={s.plan} />
          <details className="text-xs">
            <summary className="cursor-pointer text-slate-500">Assumptions</summary>
            <div className="mt-2"><PlanFields s={s} compact /></div>
          </details>
          <button className={ghost} disabled={!s.plan}
            onClick={() => toast("capacity-request.md downloaded")}>
            Download request
          </button>
        </>
      ))}

      {block(2, "Where it runs", !!s.plan, (
        <>
          <p className="text-xs text-slate-500">
            Locations you already have, measured against the plan.
          </p>
          <LocationList plan={s.plan} picked={picked} onPick={setPicked}
            onApply={setApplying} />
        </>
      ))}

      {block(3, "Configure the bundle", !!loc,
        <Placeholder title="" lines={CONFIGURE} />)}
      {block(4, "Download & verify", !!loc,
        <Placeholder title="" lines={DOWNLOAD} />)}

      {applying && s.plan && (
        <ApplyModal plan={s.plan} loc={applying} onClose={() => setApplying(null)}
          onDone={toast} />
      )}
    </div>
  );
}

// -- S: plan on the left, the location it lands on to the right ---------------

function VariantS({ toast }: { toast: (m: string) => void }) {
  const s = usePlan("5000");
  const [at, setAt] = useState(0);
  const [picked, setPicked] = useState<string | null>("l1");
  const [applying, setApplying] = useState<Loc | null>(null);
  const loc = LOCS.find((l) => l.id === picked) ?? null;
  const steps = ["Capacity & location", "Configure", "Download"];
  const rows = s.plan && loc ? diff(s.plan, loc) : [];

  return (
    <div className="max-w-screen-xl mx-auto p-6 space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Stepper at={at} go={setAt} steps={steps} />
        <span className="grow" />
        <button className={ghost} disabled={at === 0} onClick={() => setAt(at - 1)}>← Back</button>
        <button className={primary} disabled={at === steps.length - 1 || !loc}
          onClick={() => setAt(at + 1)}>Next →</button>
      </div>

      {at === 0 ? (
        // One step, because they are one question asked twice: what the load
        // needs, and what the location currently offers. Side by side, the
        // difference is the middle column rather than something to remember
        // between two screens.
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] gap-4 items-start">
          <div className={card + " p-4 space-y-3"}>
            <h3 className="text-sm font-semibold text-slate-800">What the load needs</h3>
            <PlanFields s={s} compact />
            <div className="border-t border-slate-100 pt-3"><PlanAnswer plan={s.plan} /></div>
            <button className={ghost} disabled={!s.plan}
              onClick={() => toast("capacity-request.md downloaded")}>
              Download request
            </button>
          </div>

          <div className="lg:w-44 space-y-2 lg:pt-16">
            {!loc || !s.plan ? (
              <p className="text-[11px] text-slate-400 text-center">
                pick a location to compare
              </p>
            ) : rows.length === 0 ? (
              <p className="text-[11px] text-emerald-700 text-center">
                {loc.name} already matches
              </p>
            ) : (
              <>
                <p className="text-[11px] text-amber-700 text-center">
                  {rows.length} setting{rows.length === 1 ? "" : "s"} differ
                </p>
                <button className={primary + " w-full"}
                  onClick={() => setApplying(loc)}>
                  Apply →
                </button>
                <p className="text-[10px] text-slate-400 text-center">
                  writes to the account; asks first
                </p>
              </>
            )}
          </div>

          <div className={card + " p-4 space-y-3"}>
            <h3 className="text-sm font-semibold text-slate-800">Where it will run</h3>
            <LocationList plan={s.plan} picked={picked} onPick={setPicked}
              onApply={setApplying} />
            {loc && s.plan && (
              <div className="border-t border-slate-100 pt-3 space-y-2">
                <p className="text-xs font-semibold text-slate-700">
                  {loc.name} · location settings
                </p>
                <DiffTable plan={s.plan} loc={loc} />
              </div>
            )}
          </div>
        </div>
      ) : at === 1 ? <Placeholder title="Configure" lines={CONFIGURE} />
        : <Placeholder title="Download &amp; verify" lines={DOWNLOAD} />}

      {applying && s.plan && (
        <ApplyModal plan={s.plan} loc={applying} onClose={() => setApplying(null)}
          onDone={toast} />
      )}
    </div>
  );
}

// -- the switcher -------------------------------------------------------------

export function MergedFlowPrototype({ variant }: { variant: Variant }) {
  const [toast, setToast] = useState<string | null>(null);
  const say = (m: string) => {
    setToast(m);
    window.setTimeout(() => setToast(null), 2200);
  };
  const Body = { P: VariantP, Q: VariantQ, R: VariantR, S: VariantS }[variant];
  return (
    <div className="min-h-screen bg-slate-50 pb-20">
      <div className="bg-white border-b border-slate-200 px-4 py-2.5">
        <span className="text-sm font-bold text-slate-900">
          <span className="text-bzm">BlazeMeter</span> OPL Generator
        </span>
        <span className="ml-3 text-[11px] uppercase tracking-wide text-amber-700 font-semibold">
          prototype — flow {variant}
        </span>
      </div>
      <Body toast={say} />
      {toast && (
        <div className="fixed bottom-16 left-1/2 -translate-x-1/2 z-50 bg-slate-900
                        text-white text-xs rounded-md px-3 py-2 shadow-lg">
          {toast}
        </div>
      )}
      <div className="fixed bottom-0 inset-x-0 bg-slate-900 text-slate-300 px-4 py-2
                      flex items-center gap-2 flex-wrap text-xs z-40">
        <span className="uppercase tracking-wide text-slate-500 font-semibold">Variant</span>
        {VARIANTS.map((v) => (
          <a key={v.id} href={`?variant=${v.id}`}
            className={"rounded px-2 py-1 " + (v.id === variant
              ? "bg-white text-slate-900 font-semibold" : "hover:bg-slate-700")}>
            {v.label}
          </a>
        ))}
        <span className="grow" />
        <a href="?" className="underline hover:text-white">leave the prototype</a>
      </div>
    </div>
  );
}
