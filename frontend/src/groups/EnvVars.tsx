import { useRef, useState } from "react";
import { AgentEnvVar } from "../api";
import { Disclosure, ErrorMsg, rowFieldCls as fieldCls, rowInputCls, RowEditor } from "../components";
import {
  boolChoice, BoolChoice, boolWrite, EnvRow, envRowError, envToRows, jsonToKv,
  kvToJson, KvRow, offeredVars, otherRows, Reserved, rowsToEnv, setVar,
  varError, varSet, varValue,
} from "../env";
// The section holding each reserved variable's option, off the group declarations.
import { reservedList } from "../optionGroups";

/** Agent environment variables with no setting of their own on this page.
 *
 *  Lists what BlazeMeter documents minus what the bundle already writes (both
 *  served), each with a control for its type and the agent's default. A
 *  name/value editor underneath keeps any other variable the bundle carries
 *  visible and editable. */
export function EnvVars(props: {
  env: unknown;
  /** The documented variables, minus the ones this bundle writes. Empty until
   *  read; then only the free-form rows show. */
  vars: AgentEnvVar[];
  reserved: Reserved;
  /** Kubernetes bundle rather than docker: picks which documented table shows. */
  cluster: boolean;
  /** The option, written whole: null for nothing set. What goes out comes back
   *  as `env` unchanged, which the editors' resync check relies on. */
  onChange: (v: Record<string, string> | null) => void;
}) {
  const offered = offeredVars(props.vars, props.cluster);
  // An unreadable JSON variable still has its own row (a text box); only names
  // with no row fall through to the free-form editor.
  const shown = offered.map((v) => v.name);
  const write = (name: string, value: string | null) =>
    props.onChange(setVar(props.env, name, value));

  return (
    <div className="space-y-3">
      <p className="text-2xs text-slate-400">
        {props.cluster
          ? "Added to the agent's ConfigMap. They reach the crane pod; the "
            + "engines crane spawns get their environment from crane, not from "
            + "here."
          : "Passed to the container as --env. They reach the agent; the "
            + "engines it starts get their environment from it, not from here."}
        {" "}Anything left alone is not written at all, and the agent uses its
        own default.
      </p>

      {offered.length > 0 && (
        <div className="divide-y divide-slate-100 border-y border-slate-100">
          {offered.map((v) => (
            <VarRow key={v.name} v={v} env={props.env}
              onChange={(value) => write(v.name, value)} />
          ))}
        </div>
      )}

      <OtherRows env={props.env} shown={shown} reserved={props.reserved}
        onChange={props.onChange} />
      <SetByTheBundle reserved={props.reserved} />
    </div>
  );
}

/** Every variable the bundle writes itself, and the section where the option
 *  that writes it is set, so somebody looking for one can find it. A rendered
 *  list, for the browser's find. Empty until the served table lands. */
function SetByTheBundle(props: { reserved: Reserved }) {
  const [open, setOpen] = useState(false);
  const rows = reservedList(props.reserved);
  if (!rows.length) return null;
  return (
    <div>
      <Disclosure open={open} onToggle={() => setOpen(!open)} header={<>
        Set by this bundle, elsewhere on this step
        <span className="text-slate-400">({rows.length})</span>
      </>}>
        <div className="mt-2">
          <p className="text-2xs text-slate-400">
            These are written from the settings above, so they are not offered
            here and are refused if typed in. This is where each one is set.
          </p>
          <ul className="mt-1.5 divide-y divide-slate-100 border-y border-slate-100">
            {rows.map((r) => (
              <li key={r.name}
                className="py-1.5 flex gap-3 items-baseline justify-between">
                <span className="text-2xs font-mono text-slate-700">{r.name}</span>
                <span className="text-2xs text-slate-500 text-right">
                  {r.owner ? (
                    <>
                      <span className="font-mono text-slate-600">{r.owner}</span>
                      {/* Only where a group holds the option. */}
                      {r.where && <> — {r.where}</>}
                    </>
                  ) : (
                    // Served null: no single option writes it.
                    "written by the bundle itself"
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </Disclosure>
    </div>
  );
}

/** One documented variable: what it is, its agent default, and its control. */
function VarRow(props: {
  v: AgentEnvVar; env: unknown; onChange: (v: string | null) => void;
}) {
  const { v } = props;
  const value = varValue(props.env, v.name);
  const set = varSet(props.env, v.name);
  const err = varError(v, value);
  // A table only where the value round-trips; unreadable JSON (null) is a text
  // box, never an empty table that would save `{}` over it.
  const kv = v.type === "json_object" ? jsonToKv(value) : null;
  const unreadableJson = v.type === "json_object" && kv === null;
  return (
    <div className="py-2.5 flex gap-3 items-start">
      <div className="min-w-0 grow">
        <p className="text-xs font-mono text-slate-700">
          {v.name}
          {set && (
            <span className={"ml-2 text-3xs font-sans font-semibold uppercase "
              + "tracking-wide text-bzm"}>set</span>
          )}
        </p>
        <p className="text-2xs text-slate-400">
          {v.summary}
          {v.default && <> — agent default: <span className="font-mono">{v.default}</span></>}
        </p>
        {unreadableJson && (
          <p className="text-2xs text-amber-700">
            not an object of plain values — edited as text so nothing is lost
          </p>
        )}
        <ErrorMsg msg={err} className="text-2xs" />
      </div>
      <div className="shrink-0 w-64">
        {v.type === "bool" ? (
          <TriState name={v.name} choice={boolChoice(props.env, v.name)}
            onChange={(c) => props.onChange(boolWrite(c))} />
        ) : kv ? (
          <KvTable name={v.name} value={value} rows={kv}
            onChange={(json) => props.onChange(json)} />
        ) : v.type === "pem" ? (
          <textarea className={fieldCls + " w-full font-mono h-20"}
            aria-label={v.name} value={value}
            placeholder="-----BEGIN CERTIFICATE-----"
            onChange={(e) => props.onChange(e.target.value || null)} />
        ) : (
          // Text even for `int`, so a non-number stays visible; varError flags it.
          <input className={fieldCls + " w-full"} aria-label={v.name}
            inputMode={v.type === "int" ? "numeric" : undefined}
            value={value}
            placeholder={v.default ?? v.example ?? ""}
            onChange={(e) => props.onChange(e.target.value || null)} />
        )}
      </div>
    </div>
  );
}

/** A boolean's three answers: the agent's default (writes nothing), true, false. */
function TriState(props: {
  name: string; choice: BoolChoice; onChange: (c: BoolChoice) => void;
}) {
  // Just "Default": the value it resolves to is in the row's sentence.
  const opts: { id: BoolChoice; label: string }[] = [
    { id: "default", label: "Default" },
    { id: "true", label: "On" },
    { id: "false", label: "Off" },
  ];
  return (
    <div role="radiogroup" aria-label={props.name}
      className="flex rounded-md border border-slate-300 overflow-hidden bg-white">
      {opts.map((o) => (
        <button key={o.id} type="button" role="radio"
          aria-checked={props.choice === o.id}
          onClick={() => props.onChange(o.id)}
          className={"flex-1 px-2 py-1.5 text-2xs border-r last:border-r-0 "
            + "border-slate-200 transition-colors "
            + (props.choice === o.id
              ? "bg-bzm text-white font-medium"
              : "text-slate-600 hover:bg-slate-50")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A JSON-object variable as a key/value table. Rows are local, so a key being
 *  typed does not vanish; the variable is what the named rows add up to. */
function KvTable(props: {
  name: string;
  /** The variable as it stands and the rows it parses to; the string tells a
   *  write from elsewhere apart from our own. */
  value: string; rows: KvRow[];
  onChange: (v: string | null) => void;
}) {
  const [rows, setRows] = useState<KvRow[]>(props.rows);
  // Resync on a write from elsewhere, never on our own (it would eat a
  // half-typed key).
  const emitted = useRef<string>(props.value);
  if (props.value !== emitted.current) {
    emitted.current = props.value;
    setRows(props.rows);
  }
  const update = (next: KvRow[]) => {
    setRows(next);
    const json = kvToJson(next);
    emitted.current = json ?? "";
    props.onChange(json);
  };
  return (
    <RowEditor rows={rows} onChange={update} blank={() => ({ key: "", value: "" })}
      addLabel="+ Add entry" removeLabel={(i) => `Remove ${props.name} ${i + 1}`}
      renderRow={(r, i, edit) => (<>
        <input className={rowInputCls} placeholder="key"
          aria-label={`${props.name} key ${i + 1}`} value={r.key}
          onChange={(e) => edit({ ...r, key: e.target.value })} />
        <input className={rowInputCls} placeholder="value"
          aria-label={`${props.name} value ${i + 1}`} value={r.value}
          onChange={(e) => edit({ ...r, value: e.target.value })} />
      </>)} />
  );
}

/** The variables no row above covers, edited by name. A row with no name stays
 *  out of the option; a row with a bad name stays in, so the download blocks
 *  while the row says why. */
function OtherRows(props: {
  env: unknown; shown: string[]; reserved: Reserved;
  onChange: (v: Record<string, string> | null) => void;
}) {
  const [rows, setRows] = useState<EnvRow[]>(() => otherRows(props.env, props.shown));
  const [open, setOpen] = useState(() => otherRows(props.env, props.shown).length > 0);
  // Re-read the rows when somebody else writes the option (import, reset, a
  // row above). By identity: the option is the object this editor last emitted,
  // so any other object is someone else's write.
  const emitted = useRef<unknown>(props.env);
  if (props.env !== emitted.current) {
    emitted.current = props.env;
    setRows(otherRows(props.env, props.shown));
  }
  const update = (next: EnvRow[]) => {
    setRows(next);
    // Merged with the names the rows above own, so those are kept.
    const keep = envToRows(props.env).filter((r) => props.shown.includes(r.name));
    const kv = rowsToEnv([...keep, ...next]);
    // Null when nothing is named yet: `{}` is not the option's default.
    const env = Object.keys(kv).length ? kv : null;
    emitted.current = env;
    props.onChange(env);
  };
  return (
    <div>
      <Disclosure open={open} onToggle={() => setOpen(!open)} header={<>
        Another variable by name
        {rows.length > 0 && (
          <span className="text-slate-400">({rows.length} set)</span>
        )}
      </>}>
        <div className="mt-2 space-y-1.5">
          <p className="text-2xs text-slate-400">
            For anything the list above does not carry — a variable documented
            for the other platform, one belonging to a functionality this
            location does not run, or one newer than this tool.
          </p>
          <RowEditor rows={rows} onChange={update} blank={() => ({ name: "", value: "" })}
            addLabel="+ Add variable" removeLabel={(i) => `Remove variable ${i + 1}`}
            renderRow={(r, i, edit) => (<>
              <input className={rowInputCls
                + (envRowError(rows, i, props.reserved) ? " border-red-300" : "")}
                placeholder="NAME" value={r.name}
                aria-label={`Variable name ${i + 1}`}
                onChange={(e) => edit({ ...r, name: e.target.value })} />
              <input className={rowInputCls} placeholder="value"
                value={r.value} aria-label={`Variable value ${i + 1}`}
                onChange={(e) => edit({ ...r, value: e.target.value })} />
            </>)}
            below={(_, i) => (
              <ErrorMsg msg={envRowError(rows, i, props.reserved)}
                className="mt-0.5 text-2xs" />
            )} />
        </div>
      </Disclosure>
    </div>
  );
}
