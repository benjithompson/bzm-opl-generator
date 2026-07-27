import { Check, Field, inputCls, TextInput } from "../components";
import { ENGINE_SIZES } from "../optionGroups";

/** Engine sizing. `preset` is derived from the two limits by the caller
 *  (enginePreset) rather than stored, so an imported or preset config lands on
 *  the right entry and anything unrecognised shows as Custom. */
export function SizingGroup(props: {
  preset: string;
  cpuLimit: string;
  memLimit: string;
  emitLimitRange: boolean;
  /** The preset select writes both limits at once; "Custom…" clears both. */
  onLimits: (cpu: string | null, mem: string | null) => void;
  onCpuLimit: (v: string | null) => void;
  onMemLimit: (v: string | null) => void;
  onEmitLimitRange: (v: boolean) => void;
}) {
  return (
    <>
      <Field label="Engine size"
        hint="KUBERNETES_RESOURCES_LIMITS_CPU / _MEMORY — the pod limits the crane stamps on every engine it spawns">
        <select className={inputCls} value={props.preset}
          onChange={(e) => {
            // "Custom…" clears both, which is what makes the preset fall
            // through to "custom" and reveal the two fields.
            const p = ENGINE_SIZES.find((s) => s.id === e.target.value);
            props.onLimits(p?.cpu ?? null, p?.mem ?? null);
          }}>
          {ENGINE_SIZES.map((s) => (
            <option key={s.id} value={s.id}>{s.label}</option>
          ))}
          <option value="custom">Custom…</option>
        </select>
      </Field>
      {props.preset === "custom" && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="CPU limit">
            <TextInput mono placeholder="2" value={props.cpuLimit}
              onChange={(v) => props.onCpuLimit(v || null)} />
          </Field>
          <Field label="Memory limit">
            <TextInput mono placeholder="8Gi" value={props.memLimit}
              onChange={(v) => props.onMemLimit(v || null)} />
          </Field>
        </div>
      )}
      <Check label="Emit a namespace LimitRange (bzm_limitrange.yaml)"
        hint="Caps the namespace at the engine size and gives pods that declare
              no resources a sensible default. It cannot change the taurus engine
              itself — crane sets that pod's requests to 250m/256Mi explicitly."
        checked={props.emitLimitRange}
        onChange={props.onEmitLimitRange} />
      <p className="text-[11px] text-slate-400">
        Each concurrent engine also needs ~60GB disk (40GB of it on /tmp).
        Size worker nodes for slots × engine size.
      </p>
    </>
  );
}
