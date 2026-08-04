import { useState } from "react";
import { Check, NoticeMsg, SegmentedControl, SubSection } from "../components";
import { OptionPatch } from "../optionGroups";
import {
  customSeed, placementOf, placementPatch, rowsToSelector, rowsToTolerations,
  selectorToRows, TOLERATION_EFFECTS, TOLERATION_OPERATORS, TolerationRow,
  tolerationField, tolerationsToRows, withTolerationField,
} from "../sched";

/** Scheduling: a prescribed choice up front, tables a layer deeper (#127).
 *
 *  The radio answers the only question most deployments have -- where should
 *  engines run -- and writes all four options at once; the fold underneath is
 *  for the cluster whose pool names and taints already exist. The choice is
 *  derived from the options (sched.placementOf), never stored, so the radio
 *  and the tables cannot disagree: a hand edit that fits no choice shows as
 *  none of them, with a sentence saying so, rather than snapping the nearest
 *  choice onto values it does not describe.
 *
 *  The editors keep what is being typed (a selector row without its key yet is
 *  local state, not an option), so when *this component* rewrites the options
 *  under them -- the radio, the override toggles -- it bumps `epoch` to
 *  remount them from the new values. Nobody types JSON here.
 */
export function SchedGroup(props: {
  tolerations: unknown;
  nodeSelector: unknown;
  engineTolerations: unknown;
  engineNodeSelector: unknown;
  onPatch: (p: OptionPatch) => void;
}) {
  const placement = placementOf({
    engine_node_selector: props.engineNodeSelector,
    engine_tolerations: props.engineTolerations,
  });
  const [epoch, setEpoch] = useState(0);
  const [open, setOpen] = useState(placement === "custom");
  const patch = (p: OptionPatch) => {
    setEpoch((e) => e + 1);
    props.onPatch(p);
  };

  const craneSel = rowsToSelector(selectorToRows(props.nodeSelector));
  const summary = Object.keys(craneSel).length
    ? "crane on " + Object.entries(craneSel).map(([k, v]) => `${k}=${v}`).join(", ")
    : "crane on any node";

  return (
    <div className="space-y-3">
      <SegmentedControl
        label="Where should engines run?"
        value={placement}
        onChange={(v) => patch(placementPatch(v as "crane" | "separate" | "anywhere"))}
        options={[
          {
            value: "crane",
            label: "With crane",
            hint: "one pool: engines follow the crane pod's placement",
          },
          {
            value: "separate",
            label: "Separate nodes",
            hint: "a dedicated engine pool, labeled and tainted pool=bzm-engines — recommended for load tests",
          },
          {
            value: "anywhere",
            label: "Anywhere",
            hint: "engines take no selector or toleration of their own, even where crane has one",
          },
        ]}
      />
      {placement === "custom" && (
        <NoticeMsg msg={"Placement is customized below and matches none of the "
          + "choices above. Picking one replaces the customization."} />
      )}
      {placement === "separate" && (
        <NoticeMsg msg={"A dedicated pool also needs the location's engine CPU "
          + "and memory override, set in Location settings: autoscalers grow "
          + "pools by what pods request, and engines requesting the default "
          + "250m all pack onto the first node added."} />
      )}
      <SubSection title="Customize placement" summary={summary}
        open={open} onToggle={() => setOpen(!open)}>
        <div className="space-y-4">
          <KvRows key={`cs${epoch}`} label="Crane node selector"
            hint="engines follow it unless overridden below"
            value={props.nodeSelector}
            onChange={(v) => props.onPatch({ node_selector: v })} />
          <TolRows key={`ct${epoch}`} label="Crane tolerations"
            hint="engines inherit them unless overridden below"
            value={props.tolerations}
            onChange={(v) => props.onPatch({ tolerations: v })} />
          <div className="space-y-2">
            <Check label="Engines use their own node selector"
              hint="off: engines inherit crane's; an empty table means engines take no selector at all"
              checked={props.engineNodeSelector != null}
              onChange={(on) => patch({
                engine_node_selector: on ? customSeed(props.nodeSelector, {}) : null,
              })} />
            {props.engineNodeSelector != null && (
              <KvRows key={`es${epoch}`} label="Engine node selector"
                value={props.engineNodeSelector}
                onChange={(v) => props.onPatch({ engine_node_selector: v })} />
            )}
          </div>
          <div className="space-y-2">
            <Check label="Engines use their own tolerations"
              hint="off: engines inherit crane's; an empty table means engines tolerate nothing"
              checked={props.engineTolerations != null}
              onChange={(on) => patch({
                engine_tolerations: on ? customSeed(props.tolerations, []) : null,
              })} />
            {props.engineTolerations != null && (
              <TolRows key={`et${epoch}`} label="Engine tolerations"
                value={props.engineTolerations}
                onChange={(v) => props.onPatch({ engine_tolerations: v })} />
            )}
          </div>
        </div>
      </SubSection>
    </div>
  );
}

// Not inputCls: that carries w-full, which is right for a field on its own
// line and wrong inside a flex row -- a w-full select refuses to shrink, the
// key input beside it collapses to nothing, and the rest of the row walks off
// the panel. Text inputs share what the fixed-width selects leave.
const rowFieldCls =
  "mt-0.5 rounded-md border border-slate-300 px-2 py-1.5 text-xs bg-white " +
  "focus:outline-none focus:ring-2 focus:ring-bzm/40 focus:border-bzm";
const rowInputCls = rowFieldCls + " flex-1 min-w-0";
const rowSelectCls = rowFieldCls + " shrink-0";
const addBtnCls = "text-xs text-bzm hover:underline";
const removeBtnCls = "text-slate-400 hover:text-red-600 text-sm px-1 shrink-0";

/** A node selector as a label/value table. Rows are local state and the
 *  option is what the non-blank rows add up to, so a key mid-typing does not
 *  flicker out of existence on every keystroke. */
function KvRows(props: {
  label: string; hint?: string; value: unknown;
  onChange: (v: Record<string, string>) => void;
}) {
  const [rows, setRows] = useState(() => selectorToRows(props.value));
  const update = (next: { key: string; value: string }[]) => {
    setRows(next);
    props.onChange(rowsToSelector(next));
  };
  return (
    <div>
      <span className="text-xs font-medium text-slate-600">{props.label}</span>
      {props.hint && (
        <span className="block text-[11px] text-slate-400">{props.hint}</span>
      )}
      <div className="mt-1 space-y-1.5">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <input className={rowInputCls} placeholder="label" value={r.key}
              aria-label={`${props.label} label ${i + 1}`}
              onChange={(e) => update(rows.map((x, j) =>
                j === i ? { ...x, key: e.target.value } : x))} />
            <input className={rowInputCls} placeholder="value" value={r.value}
              aria-label={`${props.label} value ${i + 1}`}
              onChange={(e) => update(rows.map((x, j) =>
                j === i ? { ...x, value: e.target.value } : x))} />
            <button type="button" className={removeBtnCls} title="Remove"
              aria-label={`Remove ${props.label} ${i + 1}`}
              onClick={() => update(rows.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button type="button" className={addBtnCls}
          onClick={() => update([...rows, { key: "", value: "" }])}>
          + Add label
        </button>
      </div>
    </div>
  );
}

/** Tolerations as rows of the four fields generate reads. The row object is
 *  the toleration itself and edits spread over it, so fields this editor does
 *  not know (tolerationSeconds, ...) ride through untouched. */
function TolRows(props: {
  label: string; hint?: string; value: unknown;
  onChange: (v: TolerationRow[]) => void;
}) {
  const [rows, setRows] = useState<TolerationRow[]>(() => tolerationsToRows(props.value));
  const update = (next: TolerationRow[]) => {
    setRows(next);
    props.onChange(rowsToTolerations(next));
  };
  const edit = (i: number, field: string, v: string) =>
    update(rows.map((r, j) => (j === i ? withTolerationField(r, field, v) : r)));
  return (
    <div>
      <span className="text-xs font-medium text-slate-600">{props.label}</span>
      {props.hint && (
        <span className="block text-[11px] text-slate-400">{props.hint}</span>
      )}
      <div className="mt-1 space-y-1.5">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <input className={rowInputCls} placeholder="key"
              value={tolerationField(r, "key")}
              aria-label={`${props.label} key ${i + 1}`}
              onChange={(e) => edit(i, "key", e.target.value)} />
            <select className={rowSelectCls + " w-24"}
              value={tolerationField(r, "operator") || "Equal"}
              aria-label={`${props.label} operator ${i + 1}`}
              onChange={(e) => edit(i, "operator", e.target.value)}>
              {TOLERATION_OPERATORS.map((op) => (
                <option key={op} value={op}>{op}</option>
              ))}
            </select>
            {tolerationField(r, "operator") !== "Exists" && (
              <input className={rowInputCls} placeholder="value"
                value={tolerationField(r, "value")}
                aria-label={`${props.label} value ${i + 1}`}
                onChange={(e) => edit(i, "value", e.target.value)} />
            )}
            <select className={rowSelectCls + " w-36"}
              value={tolerationField(r, "effect")}
              aria-label={`${props.label} effect ${i + 1}`}
              onChange={(e) => edit(i, "effect", e.target.value)}>
              {TOLERATION_EFFECTS.map((ef) => (
                <option key={ef} value={ef}>{ef === "" ? "any effect" : ef}</option>
              ))}
            </select>
            <button type="button" className={removeBtnCls} title="Remove"
              aria-label={`Remove ${props.label} ${i + 1}`}
              onClick={() => update(rows.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <button type="button" className={addBtnCls}
          onClick={() => update([...rows, {}])}>
          + Add toleration
        </button>
      </div>
    </div>
  );
}
