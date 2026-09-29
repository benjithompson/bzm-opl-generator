import { Field, inputCls } from "../components";
import { ENGINE_SIZES } from "../optionGroups";

/** The engine-size picker, wherever a size is chosen.
 *
 *  Three copies before this: here, the standalone planner and the location's
 *  Calculate pane -- and they had already diverged, since only this one offered
 *  Custom. A fourth size, or a relabelled one, was three edits. One consumer
 *  now: step 1's sizing, which writes the two options as a
 *  prescription. The configure step no longer edits the size at all (#132) --
 *  it derives from the location's engine requests, set in Location settings,
 *  and the step renders engineSize.sizeStatement instead. */
export function EngineSizeSelect(props: {
  preset: string;
  onPreset: (cpu: string | null, mem: string | null) => void;
  label?: string;
  hint?: string;
  custom?: boolean;
}) {
  return (
    <Field label={props.label ?? "Engine size"} hint={props.hint}>
      <select className={inputCls} value={props.preset}
        onChange={(e) => {
          // "Custom…" clears both, which is what makes the preset fall
          // through to "custom" and reveal the two fields.
          const p = ENGINE_SIZES.find((s) => s.id === e.target.value);
          props.onPreset(p?.cpu ?? null, p?.mem ?? null);
        }}>
        {ENGINE_SIZES.map((s) => (
          <option key={s.id} value={s.id}>{s.label}</option>
        ))}
        {props.custom && <option value="custom">Custom…</option>}
      </select>
    </Field>
  );
}
