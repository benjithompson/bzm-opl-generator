// What the sizing would change about this location, and the one
// control that writes it.
//
// The case this is for is not setup, it is the correction that follows one. A
// location and its agent get built for 500 virtual users an engine, a real run
// says the figure is 1,000, and that is a change to the *location* -- none of
// these four values appears in a manifest, so nothing has to be regenerated,
// re-applied or restarted. Before this existed the answer was "go and edit it
// in BlazeMeter", which is the one place this tool otherwise never sends you.
//
// It opens as a before -> after: the left column is what the account holds, the
// right column is the field that will be sent, and the profile above the list
// is what those fields open filled with. There is no separate calculator any
// more (it was a third place the same four numbers were worked out, seeded from
// a target typed per location) and no Apply -- filling a field applies nothing,
// so a button to do it was a click between the profile and the only control
// here that costs anything.
//
// It is a write to the customer's account, so it follows the same rules as the
// other two on this page: its own control rather than a side effect of
// something else, what it costs said before it is pressed, and the answer
// reporting what the account *now holds* rather than what was typed --
// core.update_location re-reads for exactly that reason.
import { useEffect, useState } from "react";

import { Api, CapacityPlan, Location, LocationSettings as Settings,
         LocationUpdate } from "../api";
import { Button, ErrorMsg, NumberInput, PlanCaveats } from "../components";
// The same ask the profile card makes, re-made with this location's agent
// count: `slots` is engines per *agent*, and the division is plan.py's.
import { PlanAsk, useCapacityPlan } from "../usePlan";

/** The form, as strings. Blank means "leave this one alone", which is also what
 *  the API takes: there is deliberately no way to *clear* a setting here, since
 *  clearing one and not mentioning it are different intents. */
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

/** Whether a typed field says the same thing the location already holds.
 *
 *  Compared as a *number*, not as text. Both sides are whole numbers by the
 *  time they get here, but the two sides are written by different hands -- the
 *  profile stringifies an int, a `type=number` input hands back whatever was
 *  typed -- so `4`, `4.0` and ` 4` are the same setting arriving three ways.
 *  Textual comparison called each of them a change, which put the panel in the
 *  state this rule is about: a Save offering to write the value already there,
 *  and a Reset live with nothing to give back.
 *
 *  Blank is not zero and not "same as anything": it means "leave this one
 *  alone", so blank matches only blank. */
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

/** What the fields open on: the location's own values, with the profile's over
 *  the top of them where there is a profile.
 *
 *  Filling is not applying. Nothing has reached the account until Save, and the
 *  numbers are in the fields where they can be read and edited first -- which
 *  is the whole reason the profile fills a draft rather than being sent.
 *
 *  A null setting is left as the location's. Only `override_cpu` can be one --
 *  the plan says null where the engine is not a whole number of cores, which
 *  overrideCPU cannot express. */
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
  override_cpu: "match the engine's CPU — 2 for a standard engine. Blank = 250m",
  override_memory: "match the engine's memory in MB — 8192 for a standard "
    + "engine. Blank = 256Mi",
};

export function LocationSettings(props: {
  /** The caller of the local routes. Handed down rather than imported: Save is
   *  one of the three writes this page makes to the customer's account, and a
   *  write that cannot be swapped for a fake is a write no test can watch. */
  api: Api;
  location: Location;
  /** What the profile is sizing, assembled once by App. Its `users` is blank
   *  until somebody sizes one, which is the "no profile yet" state -- not an
   *  error, and not a reason to hide the fields. */
  profile: PlanAsk;
  /** Put the changed location back into the page's own list and selection. */
  onUpdated: (loc: Location) => void;
  /** Done with this location: fold it away and open the agent under it.
   *
   *  The panel needed a way *out*. Its only button was Save, greyed whenever
   *  nothing had been typed -- which is the common case, since most locations
   *  are already configured -- so choosing a location opened a form whose one
   *  control was dead, and the next thing to do was somewhere else on the page
   *  with nothing pointing at it. */
  onConfirm: () => void;
}) {
  const { location } = props;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<LocationUpdate | null>(null);
  // Whether anything in here was typed. A hand edit outranks a later change to
  // the profile: the fields would otherwise be rewritten under someone who is
  // in the middle of correcting one of them. Reset gives the profile back.
  const [touched, setTouched] = useState(false);

  // Not a field: the location already says how many agents it has, and a box to
  // retype it is a second source for the same fact -- one that can disagree
  // with the row it sits under. An empty location counts as one, which is what
  // it will have as soon as the first agent is created.
  const agents = Math.max((location.ships ?? []).length, 1);
  const { plan, err: planErr, busy: planBusy } =
    useCapacityPlan({ ...props.profile, agents: String(agents) }, props.api);
  const fill = plan?.location ?? null;

  const [draft, setDraft] = useState<Draft>(() => seed(location, fill));

  // A different location is a different everything: the fields, the last
  // outcome, the last refusal, and whether any of it was typed. Without this,
  // picking another location left the previous one's numbers in the fields and
  // Save would have written them to it.
  useEffect(() => {
    setTouched(false); setResult(null); setErr(null);
  }, [location.id]);

  // The fields follow the location and the profile until something is typed
  // into them.
  //
  // The outcome is deliberately not cleared here. A save changes the location,
  // the changed location comes back through `onUpdated`, and this effect runs
  // *because of* the save that just landed -- so clearing it took the report of
  // what the account now holds off screen at the moment it arrived.
  useEffect(() => {
    if (touched) return;
    setDraft(seed(location, fill));
    // The plan's own four values rather than the plan: a fresh object every
    // render would re-seed on every keystroke anywhere on the page.
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

  const save = async () => {
    setBusy(true); setErr(null); setResult(null);
    try {
      // Only the fields that changed. Sending all four would write back three
      // values this browser may have been holding since before someone else
      // edited the location.
      const body: Record<string, string> = {};
      edited.forEach((k) => { body[k] = draft[k].trim(); });
      const out = await props.api.updateLocation(
        { harbor_id: location.id, ...body });
      setResult(out);
      props.onUpdated(out.location);
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  return (
    // Named after the location it is about: it opens inside that location's own
    // row, and "Location settings" on its own is the same heading whichever row
    // is open.
    <section aria-label={`${location.name} settings`}
      className="border border-slate-200 rounded-md p-3 space-y-3 bg-slate-50">
      <div>
        <p className="text-xs font-semibold text-slate-700">
          Location settings
        </p>
        <p className="text-[11px] text-slate-500">
          What this location may run, in BlazeMeter. None of it is in the
          manifests, so a change here needs no regenerate and no redeploy — it
          applies to the next test that starts.
        </p>
      </div>

      <ProfileLine plan={plan} agents={agents} busy={planBusy}
        touched={touched} />
      <ErrorMsg msg={planErr} />

      {/* Before -> after, with the after column editable. Two panels -- a diff
          to read and a form to fill -- would be two answers to "what is about
          to be written", and only one of them would be the one that is sent. */}
      <div className="space-y-1.5">
        {KEYS.map((k) => (
          // The label wraps the whole row, so the value it is about is the one
          // named beside it however the row is laid out.
          <label key={k} className="flex items-center gap-2">
            <span className="grow min-w-0">
              <span className="text-xs font-medium text-slate-600">
                {LABELS[k]}
              </span>
              <span className="block text-[11px] text-slate-400">{HINTS[k]}</span>
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

      <p className="text-[11px] text-slate-500">
        <b>Engines per agent</b> multiplies: this location&apos;s concurrency is
        agents × that figure, so {agents} agent{agents === 1 ? "" : "s"}
        {" "}at {draft.slots || "?"} each is{" "}
        {Number(draft.slots) > 0
          ? `${agents * Number(draft.slots)} engines at once`
          : "however many you set"}. Each agent runs its share in its own
        cluster.
      </p>

      <p className="text-[11px] text-slate-500">
        The two requests are what the Kubernetes scheduler and the autoscaler
        place engines on; the limits they run at come from the manifests. Left
        blank they default to <b>250m / 256Mi</b>, so every engine asks for a
        fraction of what it uses, the autoscaler adds one node, and a whole run
        packs onto it. Setting them to the engine&apos;s own size is what keeps
        the engines apart.
      </p>

      {/* What it costs on the left, the control that commits on the right --
          the reading order of the panel is "this is what changes, this is what
          it costs, do it".

          One button, always live, and its label is what pressing it does.
          Nothing typed, it is the way on: Confirm folds this location away and
          opens the agent under it, writing nothing. Something typed, it is
          Save, and the sentence beside it says what that costs -- which is why
          the two are not one control called Confirm that sometimes writes to
          somebody's account. Saving does not fold: the outcome under this row
          is a re-read of the location saying what it now holds, and collapsing
          the panel would take that off screen at the moment it arrived. So a
          save leaves nothing edited, the label falls back to Confirm, and the
          way on is the same button it always was. */}
      <div className="flex items-center gap-2">
        <span className={"text-[11px] "
          + (edited.length ? "text-amber-700" : "text-slate-500")}>
          {edited.length === 0
            ? "nothing to save — Confirm moves on to the agent"
            : `${edited.length} setting${edited.length === 1 ? "" : "s"}: `
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

/** Where the numbers in the right-hand column came from.
 *
 *  The per-agent division is the line worth saying out loud: the profile sizes
 *  a run, `slots` is what *one* agent may run, and the same 10 engines is 5
 *  each against two agents. Writing the run's own figure into `slots` would
 *  size this location for twenty. */
function ProfileLine({ plan, agents, busy, touched }: {
  plan: CapacityPlan | null; agents: number; busy: boolean; touched: boolean;
}) {
  if (!plan) {
    return (
      <p className="text-[11px] text-amber-700">
        No sizing yet — make one above and these fields open on what
        it would change here. They can be typed in either way.
      </p>
    );
  }
  return (
    <div className={"space-y-1 " + (busy ? "opacity-50" : "")}>
      <p className="text-[11px] text-slate-500">
        {/* Every sizing in its own unit, because two of the three are not
            virtual users -- and `plan.users` is null where no load test was
            sized at all, which this sentence used to read straight through. */}
        The sizing — <b>{plan.sizings.map(
          (s) => `${s.target.toLocaleString()} ${s.unit}`).join(", ")}</b>
        {" "}— needs{" "}
        <b>{plan.engines} engine{plan.engines === 1 ? "" : "s"}</b>, which is{" "}
        <b>{plan.engines_per_agent} per agent</b> across this location&apos;s{" "}
        {agents} agent{agents === 1 ? "" : "s"}, on{" "}
        {plan.nodes_per_agent} node{plan.nodes_per_agent === 1 ? "" : "s"} each.
        {touched && " The fields below were edited by hand and no longer follow it."}
      </p>
      <PlanCaveats compact sizings={plan.sizings} warnings={plan.warnings} />
    </div>
  );
}

/** What the account holds now -- not what was typed.
 *
 *  The distinction is the reason core re-reads: BlazeMeter's own POST accepts
 *  `threadsPerEngine` and does not store it, and a location in that state 403s
 *  every test start while a form that echoed the request back would show the
 *  number as saved. So a field that came back unchanged is reported as such,
 *  in amber, rather than folded into a green tick. */
function Outcome({ result }: { result: LocationUpdate }) {
  const changed = Object.keys(result.changed) as (keyof Settings)[];
  return (
    <div className="space-y-1">
      {changed.length > 0 && (
        <p className="text-[11px] text-emerald-700">
          saved: {changed.map((k) => (
            `${LABELS[k as keyof Draft].toLowerCase()} ${result.before[k] ?? "not set"} → ${result.after[k]}`
          )).join(", ")}
        </p>
      )}
      {result.ignored.length > 0 && (
        <p className="text-[11px] text-amber-700">
          BlazeMeter did not store{" "}
          {result.ignored.map((k) => LABELS[k as keyof Draft].toLowerCase()).join(", ")}
          {" "}— the location still reads the old value, so this account may not
          accept that field. Set it in BlazeMeter directly.
        </p>
      )}
      {changed.length === 0 && result.ignored.length === 0 && (
        <p className="text-[11px] text-slate-500">
          nothing to change — the location already held those values
        </p>
      )}
    </div>
  );
}
