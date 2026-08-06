import { useRef, useState } from "react";
import { AgentEnvVar } from "../api";
import {
  boolChoice, BoolChoice, boolWrite, EnvRow, envRowError, envToRows, jsonToKv,
  kvToJson, KvRow, offeredVars, otherRows, Reserved, rowsToEnv, setVar,
  varError, varSet, varValue,
} from "../env";
// Which section of this step holds the option that writes a reserved variable.
// From the group declarations, because they already carry the keys -- a table
// here mapping variable to section would be a third copy of one fact.
import { reservedList } from "../optionGroups";

/** Environment variables the agent takes and this tool has no setting of its
 *  own for (#131).
 *
 *  It was a switch, a name box and a value box. Reaching AUTO_KUBERNETES_UPDATE
 *  through it meant already knowing the variable exists, spelling it, and
 *  knowing that its value is the word `true` -- a documentation lookup done at
 *  the keyboard, where a typo produces a variable the agent never reads and
 *  nothing anywhere says so. So the reference is on screen: every variable
 *  BlazeMeter documents that no control on this page already writes, each with
 *  the control its type deserves and the agent's own default stated beside it.
 *
 *  The list is served (`vars`, /api/agent-env) and never enumerated here, which
 *  is what makes it the *remainder*: the proxy trio, the engine limits, the
 *  registry and the rest have their own groups on this step, and they drop out
 *  of this list by being in the generator's RESERVED_ENV rather than by a
 *  second table here agreeing that they should. An option removed later hands
 *  its variable back with no edit on this side.
 *
 *  Underneath, the name/value editor survives for the variables no row above
 *  covers -- a name from the other platform's table, one the vocabulary has
 *  since lost, a JSON value no table can round-trip. It is not the way in any
 *  more; it is what stops the area hiding a variable the bundle carries.
 */
export function EnvVars(props: {
  env: unknown;
  /** BlazeMeter's documented variables, minus the ones this bundle writes. An
   *  empty list means the page has not read it yet -- the area then shows the
   *  free-form rows alone rather than claiming there is nothing to offer. */
  vars: AgentEnvVar[];
  reserved: Reserved;
  /** Whether this bundle is a set of manifests rather than a docker script. It
   *  picks which of BlazeMeter's two tables is on screen -- and only that: the
   *  option itself is carried by every format. */
  cluster: boolean;
  /** The option, written whole -- `null` for "nothing set", which is its
   *  default. Normalised in env.ts rather than by the caller so that what this
   *  component emits is exactly what comes back as `env`, which is what the
   *  identity check in the free-form editor rests on. */
  onChange: (v: Record<string, string> | null) => void;
}) {
  const offered = offeredVars(props.vars, props.cluster);
  // A JSON variable whose value no key/value table can round-trip is edited as
  // text, so it is not "shown" by its own row in the sense that matters here --
  // it is, and the row below renders the text box. Only names with no row at
  // all fall through to the free-form editor.
  const shown = offered.map((v) => v.name);
  const write = (name: string, value: string | null) =>
    props.onChange(setVar(props.env, name, value));

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-slate-400">
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

/** Every variable the bundle writes itself, and where the thing that writes it
 *  is set (#150).
 *
 *  The list above is a remainder, and a remainder says nothing about what was
 *  taken out of it. AUTO_KUBERNETES_UPDATE was reported as missing from it: it
 *  is not missing, the bundle writes it off the `auto_update` option -- a
 *  tri-state inside a group titled "Security & RBAC", behind a hint about agent
 *  self-update -- so the only route from the name to the control was to open a
 *  group about RBAC on a hunch. The refusal already said "set it with
 *  auto_update instead", but only to somebody who had typed the name into the
 *  editor above, which is the one thing a person who thinks it is missing will
 *  not do.
 *
 *  A rendered list rather than a search box, and closed rather than absent: the
 *  browser's own find is the search this area needs, and it only works on what
 *  is on the page. Served (`reserved`), never enumerated here -- same rule as
 *  the offered list, and empty means the table has not landed, which is a fold
 *  with nothing to say rather than a claim that nothing is taken. */
function SetByTheBundle(props: { reserved: Reserved }) {
  const [open, setOpen] = useState(false);
  const rows = reservedList(props.reserved);
  if (!rows.length) return null;
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-700">
        <span className="text-slate-400">{open ? "▾" : "▸"}</span>
        Set by this bundle, elsewhere on this step
        <span className="text-slate-400">({rows.length})</span>
      </button>
      {open && (
        <div className="mt-2">
          <p className="text-[11px] text-slate-400">
            These are written from the settings above, so they are not offered
            here and are refused if typed in. This is where each one is set.
          </p>
          <ul className="mt-1.5 divide-y divide-slate-100 border-y border-slate-100">
            {rows.map((r) => (
              <li key={r.name}
                className="py-1.5 flex gap-3 items-baseline justify-between">
                <span className="text-[11px] font-mono text-slate-700">{r.name}</span>
                <span className="text-[11px] text-slate-500 text-right">
                  {r.owner ? (
                    <>
                      <span className="font-mono text-slate-600">{r.owner}</span>
                      {/* Only where there is one. A group is a place on this
                          step; an option no group owns is set from the location
                          or the format, and naming a section for it would send
                          somebody to a row that is not there. */}
                      {r.where && <> — {r.where}</>}
                    </>
                  ) : (
                    // The served null, kept as its own sentence: no option owns
                    // it, which is not the same as nobody having said which.
                    "written by the bundle itself"
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** One documented variable: what it is, what the agent does without it, and the
 *  one control that writes it. The name is the row's own -- it comes off the
 *  served record, so it is the one thing here nobody can mistype. */
function VarRow(props: {
  v: AgentEnvVar; env: unknown; onChange: (v: string | null) => void;
}) {
  const { v } = props;
  const value = varValue(props.env, v.name);
  const set = varSet(props.env, v.name);
  const err = varError(v, value);
  // A key/value table only where the value is one it can hand back unchanged.
  // Null from jsonToKv is "could not read this", not "empty" -- an array or a
  // nested object arrives that way from an imported profile, and a table
  // showing it as no rows would offer to save `{}` over it.
  const kv = v.type === "json_object" ? jsonToKv(value) : null;
  const unreadableJson = v.type === "json_object" && kv === null;
  return (
    <div className="py-2.5 flex gap-3 items-start">
      <div className="min-w-0 grow">
        <p className="text-xs font-mono text-slate-700">
          {v.name}
          {set && (
            <span className={"ml-2 text-[10px] font-sans font-semibold uppercase "
              + "tracking-wide text-bzm"}>set</span>
          )}
        </p>
        <p className="text-[11px] text-slate-400">
          {v.summary}
          {v.default && <> — agent default: <span className="font-mono">{v.default}</span></>}
        </p>
        {unreadableJson && (
          <p className="text-[11px] text-amber-700">
            not an object of plain values — edited as text so nothing is lost
          </p>
        )}
        {err && <p className="text-[11px] text-red-600">{err}</p>}
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
          // A text box even for `int`: type="number" hides what was typed when
          // it is not a number, so a profile carrying "8O00" would show an
          // empty field beside a variable the bundle still writes. varError
          // says so instead.
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

/** A boolean's three answers, because there are three: the agent's default,
 *  which writes nothing, and the two values that write themselves. See
 *  env.boolChoice -- a two-position switch would have to pick one of the three
 *  to be unable to express, and every candidate is a real state here. */
function TriState(props: {
  name: string; choice: BoolChoice; onChange: (c: BoolChoice) => void;
}) {
  // Just "Default" -- the value it resolves to is two lines to the left, in the
  // row's own sentence. Carrying it on the button as well wrapped the segment
  // onto a second line and made every boolean row taller than the ones around
  // it, to restate something already on screen.
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
          className={"flex-1 px-2 py-1.5 text-[11px] border-r last:border-r-0 "
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

/** A JSON-object variable as the key/value table this page uses everywhere
 *  else. Rows are local state and the variable is what the named rows add up
 *  to, so a key mid-typing does not flicker out of existence on every
 *  keystroke -- SchedGroup's node selector, same shape and same reason. */
function KvTable(props: {
  name: string;
  /** The variable as it stands, and the rows it parses to. Both, because the
   *  string is what says whether somebody else has written it since -- an
   *  imported profile, a Reset -- while the rows are what is edited. */
  value: string; rows: KvRow[];
  onChange: (v: string | null) => void;
}) {
  const [rows, setRows] = useState<KvRow[]>(props.rows);
  // The same guard the free-form editor carries, and for the same reason: a
  // write from anywhere else must land in the table, and our own must not
  // resync it and take the half-typed key with it.
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
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input className={fieldCls + " flex-1 min-w-0"} placeholder="key"
            aria-label={`${props.name} key ${i + 1}`} value={r.key}
            onChange={(e) => update(rows.map((x, j) =>
              j === i ? { ...x, key: e.target.value } : x))} />
          <input className={fieldCls + " flex-1 min-w-0"} placeholder="value"
            aria-label={`${props.name} value ${i + 1}`} value={r.value}
            onChange={(e) => update(rows.map((x, j) =>
              j === i ? { ...x, value: e.target.value } : x))} />
          <button type="button" className={removeBtnCls} title="Remove"
            aria-label={`Remove ${props.name} ${i + 1}`}
            onClick={() => update(rows.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button type="button" className={addBtnCls}
        onClick={() => update([...rows, { key: "", value: "" }])}>
        + Add entry
      </button>
    </div>
  );
}

/** The variables no row above covers, still edited by name.
 *
 *  Rows in the Scheduling idiom: local state, a row without a name yet stays
 *  out of the option, and a row whose name cannot be used stays *in*, so the
 *  download is blocked while the row says why -- a bad value dropped on the way
 *  to the option is a form showing a variable no bundle carries. Which names
 *  are taken is served (`reserved`), never listed here.
 */
function OtherRows(props: {
  env: unknown; shown: string[]; reserved: Reserved;
  onChange: (v: Record<string, string> | null) => void;
}) {
  const [rows, setRows] = useState<EnvRow[]>(() => otherRows(props.env, props.shown));
  const [open, setOpen] = useState(() => otherRows(props.env, props.shown).length > 0);
  // ...and re-read them when somebody *else* writes the option: profile Import
  // is on this same step, a restored session or a Reset can rewrite it, and so
  // can every row above this one. Without this the rows go on showing the
  // variables that were replaced while the bundle carries the new ones -- a
  // form showing a variable no bundle carries, which is the failure this area's
  // rules are otherwise about.
  //
  // By identity rather than by value, and that is what makes it exact: the
  // option IS the object this page last emitted, so a difference here can only
  // be a write from somewhere else. Comparing values would resync on our own
  // writes and take the half-typed row with it.
  const emitted = useRef<unknown>(props.env);
  if (props.env !== emitted.current) {
    emitted.current = props.env;
    setRows(otherRows(props.env, props.shown));
  }
  const update = (next: EnvRow[]) => {
    setRows(next);
    // Merged with the rows above rather than replacing them: this editor owns
    // only the names it shows, and `rowsToEnv` over its own rows alone would
    // wipe every variable a control above had set.
    const keep = envToRows(props.env).filter((r) => props.shown.includes(r.name));
    const kv = rowsToEnv([...keep, ...next]);
    // `null` where nothing has a name yet -- a row mid-typing is not a variable,
    // and `{}` is not the option's default, so it would show up in profile.json
    // as a key a bundle generated without this area never had.
    const env = Object.keys(kv).length ? kv : null;
    emitted.current = env;
    props.onChange(env);
  };
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-[11px] text-slate-500 hover:text-slate-700">
        <span className="text-slate-400">{open ? "▾" : "▸"}</span>
        Another variable by name
        {rows.length > 0 && (
          <span className="text-slate-400">({rows.length} set)</span>
        )}
      </button>
      {open && (
        <div className="mt-2 space-y-1.5">
          <p className="text-[11px] text-slate-400">
            For anything the list above does not carry — a variable documented
            for the other platform, one belonging to a functionality this
            location does not run, or one newer than this tool.
          </p>
          {rows.map((r, i) => {
            const err = envRowError(rows, i, props.reserved);
            return (
              <div key={i}>
                <div className="flex items-center gap-1.5">
                  <input className={fieldCls + " flex-1 min-w-0" + (err ? " border-red-300" : "")}
                    placeholder="NAME" value={r.name}
                    aria-label={`Variable name ${i + 1}`}
                    onChange={(e) => update(rows.map((x, j) =>
                      j === i ? { ...x, name: e.target.value } : x))} />
                  <input className={fieldCls + " flex-1 min-w-0"} placeholder="value"
                    value={r.value} aria-label={`Variable value ${i + 1}`}
                    onChange={(e) => update(rows.map((x, j) =>
                      j === i ? { ...x, value: e.target.value } : x))} />
                  <button type="button" className={removeBtnCls} title="Remove"
                    aria-label={`Remove variable ${i + 1}`}
                    onClick={() => update(rows.filter((_, j) => j !== i))}>×</button>
                </div>
                {err && <p className="mt-0.5 text-[11px] text-red-600">{err}</p>}
              </div>
            );
          })}
          <button type="button" className={addBtnCls}
            onClick={() => update([...rows, { name: "", value: "" }])}>
            + Add variable
          </button>
        </div>
      )}
    </div>
  );
}

// The row styles SchedGroup argues for: not inputCls, whose w-full refuses to
// shrink inside a flex row and walks the rest of the row off the panel.
const fieldCls =
  "rounded-md border border-slate-300 px-2 py-1.5 text-xs bg-white " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm";
const addBtnCls = "text-xs text-bzm hover:underline";
const removeBtnCls = "text-slate-400 hover:text-red-600 text-sm px-1 shrink-0";
