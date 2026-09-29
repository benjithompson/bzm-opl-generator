import { Field, inputCls } from "../components";
import { ENGINE_SIZES } from "../optionGroups";

/** The engine-size picker for the sizing card, which writes the two limit
 *  options. The configure step states the size rather than editing it. */
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
          // "Custom…" clears both, which reveals the two fields.
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
