import { Field, inputCls, TextInput } from "../components";
import { ENGINE_SIZES } from "../optionGroups";

/** The engine-size picker, wherever a size is chosen.
 *
 *  Three copies before this: here, the standalone planner and the location's
 *  Calculate pane -- and they had already diverged, since only this one offered
 *  Custom. A fourth size, or a relabelled one, was three edits.
 *
 *  `onCustom` absent means the caller has nowhere to put a custom size, so the
 *  option is not offered rather than offered and ignored. */
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

/** Engine sizing. `preset` is derived from the two limits by the caller
 *  (enginePreset) rather than stored, so an imported or preset config lands on
 *  the right entry and anything unrecognised shows as Custom. */
export function SizingGroup(props: {
  preset: string;
  cpuLimit: string;
  memLimit: string;
  /** The preset select writes both limits at once; "Custom…" clears both. */
  onLimits: (cpu: string | null, mem: string | null) => void;
  onCpuLimit: (v: string | null) => void;
  onMemLimit: (v: string | null) => void;
}) {
  return (
    <>
      <EngineSizeSelect preset={props.preset} custom
        hint="KUBERNETES_RESOURCES_LIMITS_CPU / _MEMORY — the pod limits the crane stamps on every engine it spawns"
        onPreset={props.onLimits} />
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
      <p className="text-[11px] text-slate-400">
        Each concurrent engine also needs ~60GB disk (40GB of it on /tmp).
        Size worker nodes for slots × engine size.
      </p>
      {/* This said engine requests were unsettable -- "crane stamps them at
          250m/256Mi and nothing can move them" -- which a live GKE run
          disproved: the bundle sets the engine's *limits*, the location's
          overrideCPU/overrideMemory set its *requests*, and 250m/256Mi is only
          the default for a location that sets neither. The correction landed in
          the generator, the recipe and doctor, and this hint was missed. */}
      <p className="text-[11px] text-slate-400">
        These are the engine's <i>limits</i>. Its <i>requests</i> — what the
        scheduler and the autoscaler actually place on — come from the
        location's <code>overrideCPU</code> / <code>overrideMemory</code> in
        BlazeMeter, and default to 250m/256Mi. Left at that default an engine
        asks for a fraction of what it uses, so a whole run packs onto one node
        and the engines contend. Set them to match the limits above.
      </p>
    </>
  );
}
