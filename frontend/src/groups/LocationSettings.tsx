// Changing a location that already exists: the concurrency settings, after the
// agent is deployed.
//
// The case this is for is not setup, it is the correction that follows one. A
// location and its agent get built for 500 virtual users an engine, a real run
// says the figure is 1,000, and that is a change to the *location* -- none of
// these four values appears in a manifest, so nothing has to be regenerated,
// re-applied or restarted. Before this existed the answer was "go and edit it
// in BlazeMeter", which is the one place this tool otherwise never sends you.
//
// It is a write to the customer's account, so it follows the same rules as the
// other two on this page: its own control rather than a side effect of
// something else, what it costs said before it is pressed, and the answer
// reporting what the account *now holds* rather than what was typed --
// core.update_location re-reads for exactly that reason.
import { useEffect, useState } from "react";

import { api, Location, LocationSettings as Settings, LocationUpdate } from "../api";
import { Button, ErrorMsg, Field, inputCls } from "../components";

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

const shown = (v: number | null | undefined) =>
  v === null || v === undefined ? "" : String(v);

/** What the location currently says, as a draft. Re-derived whenever the
 *  location changes so the form is never showing another location's numbers. */
function draftOf(loc: Location): Draft {
  return {
    slots: shown(loc.slots),
    threads_per_engine: shown(loc.threadsPerEngine),
    override_cpu: shown(loc.overrideCPU),
    override_memory: shown(loc.overrideMemory),
  };
}

const LABELS: Record<keyof Draft, string> = {
  slots: "Concurrent engines",
  threads_per_engine: "Virtual users per engine",
  override_cpu: "Engine CPU request",
  override_memory: "Engine memory request (MB)",
};

export function LocationSettings(props: {
  location: Location;
  /** Put the changed location back into the page's own list and selection. */
  onUpdated: (loc: Location) => void;
}) {
  const { location } = props;
  const [draft, setDraft] = useState<Draft>(() => draftOf(location));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<LocationUpdate | null>(null);

  // A different location is a different form. Without this, picking another
  // location left the previous one's numbers in the fields, and Save would
  // have written them to it.
  useEffect(() => {
    setDraft(draftOf(location));
    setResult(null);
    setErr(null);
  }, [location.id, location.slots, location.threadsPerEngine,
      location.overrideCPU, location.overrideMemory]);

  const current = draftOf(location);
  const edited = (Object.keys(EMPTY) as (keyof Draft)[])
    .filter((k) => draft[k].trim() !== current[k]);
  const set = (k: keyof Draft, v: string) => setDraft({ ...draft, [k]: v });

  const save = async () => {
    setBusy(true); setErr(null); setResult(null);
    try {
      // Only the fields that changed. Sending all four would write back three
      // values this browser may have been holding since before someone else
      // edited the location.
      const body: Record<string, string> = {};
      edited.forEach((k) => { body[k] = draft[k].trim(); });
      const out = await api.updateLocation({ harbor_id: location.id, ...body });
      setResult(out);
      props.onUpdated(out.location);
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const field = (k: keyof Draft, hint: string) => (
    <Field label={LABELS[k]} hint={hint}>
      <input type="number" min={1} className={inputCls}
        placeholder={current[k] || "not set"} value={draft[k]}
        onChange={(e) => set(k, e.target.value)} />
    </Field>
  );

  return (
    <div className="border border-slate-200 rounded-md p-3 space-y-3 bg-slate-50">
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

      <div className="grid grid-cols-2 gap-3">
        {field("slots", "engines this location may run at once")}
        {field("threads_per_engine", "unset, every test start fails with 403")}
        {field("override_cpu", "what an engine pod requests; blank = 250m")}
        {field("override_memory", "in MB; blank = 256Mi")}
      </div>

      <p className="text-[11px] text-amber-700">
        Saving changes the location for <b>every agent in it</b> and every test
        that starts on it, including anyone else&apos;s.
      </p>

      <div className="flex gap-2 items-center">
        <Button onClick={save} busy={busy} disabled={edited.length === 0}>
          Save to BlazeMeter
        </Button>
        <Button kind="ghost" disabled={edited.length === 0}
          onClick={() => { setDraft(draftOf(location)); setResult(null); }}>
          Reset
        </Button>
        <span className="text-[11px] text-slate-500">
          {edited.length === 0
            ? "nothing changed yet"
            : `${edited.length} setting${edited.length === 1 ? "" : "s"}: `
              + edited.map((k) => LABELS[k].toLowerCase()).join(", ")}
        </span>
      </div>

      <ErrorMsg msg={err} />
      {result && <Outcome result={result} />}
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
