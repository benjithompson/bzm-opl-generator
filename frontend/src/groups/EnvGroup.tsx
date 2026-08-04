import { useRef, useState } from "react";
import { EnvRow, envRowError, envToRows, rowsToEnv, Reserved } from "../env";

/** Environment variables the agent takes and this tool has no setting for
 *  (#131).
 *
 *  BlazeMeter's agent-environment reference is much wider than the form around
 *  it -- AUTO_KUBERNETES_UPDATE and KUBERNETES_USE_PRE_PULLING sit in the same
 *  table and only one of them is a switch here -- and the way to reach the rest
 *  was to edit the generated ConfigMap by hand, which the next regenerate
 *  reverts without saying so. So it is an option (`extra_env`), which means it
 *  travels in profile.json and a regenerate replays it.
 *
 *  Rows in the Scheduling idiom: name and value, local state, nobody types
 *  JSON. A row without a name yet is being typed and stays out of the option; a
 *  row whose name cannot be used stays *in*, so the download is blocked while
 *  the row says why -- a bad value dropped on the way to the option is a form
 *  showing a variable no bundle carries.
 *
 *  Which names are taken is served (`reserved`), never listed here: a variable
 *  added to a template would otherwise go on being offered, and the collision
 *  would surface as a ConfigMap with a duplicate key rather than as a sentence
 *  on the row.
 */
export function EnvGroup(props: {
  env: unknown;
  reserved: Reserved;
  /** Whether this bundle is a set of manifests rather than a docker script.
   *  Only the sentence changes -- the option is carried by every format. */
  cluster: boolean;
  /** The option, written whole -- `null` for "nothing set", which is its
   *  default. Normalised here rather than by the caller so that what this
   *  component emits is exactly what comes back as `env`, which is what the
   *  identity check below rests on. */
  onChange: (v: Record<string, string> | null) => void;
}) {
  const [rows, setRows] = useState<EnvRow[]>(() => envToRows(props.env));
  // ...and re-read them when somebody *else* writes the option: profile Import
  // is on this same step, and a restored session or a Reset can rewrite it too.
  // Without this the rows go on showing the variables that were replaced while
  // the bundle carries the new ones -- a form showing a variable no bundle
  // carries, which is the failure this area's rules are otherwise about.
  //
  // By identity rather than by value, and that is what makes it exact: the
  // option IS the object this component last emitted (App stores what
  // `onChange` handed it), so a difference here can only be a write from
  // somewhere else. Comparing values would resync on our own writes and take
  // the half-typed row with it -- SchedGroup's `epoch` remount is the same
  // guard from the other end, and it covers only that component's own writes.
  const emitted = useRef<unknown>(props.env);
  if (props.env !== emitted.current) {
    emitted.current = props.env;
    setRows(envToRows(props.env));
  }
  const update = (next: EnvRow[]) => {
    setRows(next);
    // `null` where nothing has a name yet -- a row mid-typing is not a variable,
    // and `{}` is not the option's default, so it would show up in profile.json
    // as a key a bundle generated without this area never had.
    const kv = rowsToEnv(next);
    const env = Object.keys(kv).length ? kv : null;
    emitted.current = env;
    props.onChange(env);
  };
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-slate-400">
        {props.cluster
          ? "Added to the agent's ConfigMap. They reach the crane pod; the "
            + "engines crane spawns get their environment from crane, not from "
            + "here."
          : "Passed to the container as --env. They reach the agent; the "
            + "engines it starts get their environment from it, not from here."}
      </p>
      <div className="space-y-1.5">
        {rows.map((r, i) => {
          const err = envRowError(rows, i, props.reserved);
          return (
            <div key={i}>
              <div className="flex items-center gap-1.5">
                <input className={rowInputCls + (err ? " border-red-300" : "")}
                  placeholder="NAME" value={r.name}
                  aria-label={`Variable name ${i + 1}`}
                  onChange={(e) => update(rows.map((x, j) =>
                    j === i ? { ...x, name: e.target.value } : x))} />
                <input className={rowInputCls} placeholder="value" value={r.value}
                  aria-label={`Variable value ${i + 1}`}
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
    </div>
  );
}

// The row styles SchedGroup argues for: not inputCls, whose w-full refuses to
// shrink inside a flex row and walks the rest of the row off the panel.
const rowInputCls =
  "mt-0.5 rounded-md border border-slate-300 px-2 py-1.5 text-xs bg-white " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm " +
  "flex-1 min-w-0";
const addBtnCls = "text-xs text-bzm hover:underline";
const removeBtnCls = "text-slate-400 hover:text-red-600 text-sm px-1 shrink-0";
