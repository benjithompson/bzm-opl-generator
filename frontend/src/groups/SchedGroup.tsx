import { useState } from "react";
import {
  Check, NoticeMsg, RowEditor, rowInputCls, rowSelectCls, SegmentedControl, SubSection,
} from "../components";
import { OptionPatch } from "../optionGroups";
import {
  customSeed, placementOf, placementPatch, rowsToSelector, rowsToTolerations,
  selectorToRows, TOLERATION_EFFECTS, TOLERATION_OPERATORS, TolerationRow,
  tolerationField, tolerationsToRows, withTolerationField,
} from "../sched";

/** Scheduling: a placement choice up front, the tables a fold deeper. The
 *  choice is derived from the options (sched.placementOf), so an edit that fits
 *  none shows as "custom" rather than being rewritten. The editors keep typed
 *  rows locally; when this component rewrites the options under them it bumps
 *  `epoch` to remount them. */
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

/** A node selector as a label/value table. Rows are local, so a key being
 *  typed does not vanish; the option is what the named rows add up to. */
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
        <span className="block text-2xs text-slate-400">{props.hint}</span>
      )}
      <div className="mt-1">
        <RowEditor rows={rows} onChange={update} blank={() => ({ key: "", value: "" })}
          addLabel="+ Add label" removeLabel={(i) => `Remove ${props.label} ${i + 1}`}
          renderRow={(r, i, edit) => (<>
            <input className={rowInputCls} placeholder="label" value={r.key}
              aria-label={`${props.label} label ${i + 1}`}
              onChange={(e) => edit({ ...r, key: e.target.value })} />
            <input className={rowInputCls} placeholder="value" value={r.value}
              aria-label={`${props.label} value ${i + 1}`}
              onChange={(e) => edit({ ...r, value: e.target.value })} />
          </>)} />
      </div>
    </div>
  );
}

/** Tolerations as rows of the four fields generate reads. Edits spread over
 *  the toleration, so fields this editor does not show survive. */
function TolRows(props: {
  label: string; hint?: string; value: unknown;
  onChange: (v: TolerationRow[]) => void;
}) {
  const [rows, setRows] = useState<TolerationRow[]>(() => tolerationsToRows(props.value));
  const update = (next: TolerationRow[]) => {
    setRows(next);
    props.onChange(rowsToTolerations(next));
  };
  return (
    <div>
      <span className="text-xs font-medium text-slate-600">{props.label}</span>
      {props.hint && (
        <span className="block text-2xs text-slate-400">{props.hint}</span>
      )}
      <div className="mt-1">
        <RowEditor rows={rows} onChange={update} blank={() => ({})}
          addLabel="+ Add toleration" removeLabel={(i) => `Remove ${props.label} ${i + 1}`}
          renderRow={(r, i, edit) => {
            const set = (field: string, v: string) => edit(withTolerationField(r, field, v));
            return (<>
              <input className={rowInputCls} placeholder="key"
                value={tolerationField(r, "key")}
                aria-label={`${props.label} key ${i + 1}`}
                onChange={(e) => set("key", e.target.value)} />
              <select className={rowSelectCls + " w-24"}
                value={tolerationField(r, "operator") || "Equal"}
                aria-label={`${props.label} operator ${i + 1}`}
                onChange={(e) => set("operator", e.target.value)}>
                {TOLERATION_OPERATORS.map((op) => (
                  <option key={op} value={op}>{op}</option>
                ))}
              </select>
              {tolerationField(r, "operator") !== "Exists" && (
                <input className={rowInputCls} placeholder="value"
                  value={tolerationField(r, "value")}
                  aria-label={`${props.label} value ${i + 1}`}
                  onChange={(e) => set("value", e.target.value)} />
              )}
              <select className={rowSelectCls + " w-36"}
                value={tolerationField(r, "effect")}
                aria-label={`${props.label} effect ${i + 1}`}
                onChange={(e) => set("effect", e.target.value)}>
                {TOLERATION_EFFECTS.map((ef) => (
                  <option key={ef} value={ef}>{ef === "" ? "any effect" : ef}</option>
                ))}
              </select>
            </>);
          }} />
      </div>
    </div>
  );
}
