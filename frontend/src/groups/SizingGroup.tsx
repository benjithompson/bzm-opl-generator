import { Button, ErrorMsg, Field, inputCls, TextInput } from "../components";
import { applyCost, MatchPatch, SizeState } from "../engineSize";
import { ENGINE_SIZES } from "../optionGroups";

/** The engine-size picker, wherever a size is chosen.
 *
 *  Three copies before this: here, the standalone planner and the location's
 *  Calculate pane -- and they had already diverged, since only this one offered
 *  Custom. A fourth size, or a relabelled one, was three edits. Two now: this
 *  group and step 1's capacity profile, which edit the same two options.
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
  /** What the location's requests say beside these limits -- the size is one
   *  figure with two writers (#132), and this is the second one, read off the
   *  selected location. See engineSize.ts for the states. */
  size: SizeState;
  /** The one write here: the location overrides matching the limits, through
   *  the same settings route as the location panel's Save. App owns it. */
  onApply: (patch: MatchPatch) => void;
  applyBusy: boolean;
  applyErr: string | null;
}) {
  const s = props.size;
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
      {/* The other writer of this figure: the location's overrideCPU /
          overrideMemory are the engine's *requests*, which the scheduler and
          the autoscaler place on. The states are engineSize.ts's, and "no
          location to read" (manual entry) is not "the location sets nothing"
          -- only the second may warn. */}
      {s.kind === "noLocation" && (
        <p className="text-[11px] text-slate-400">
          These are the engine's <i>limits</i>. Its <i>requests</i> — what the
          scheduler and the autoscaler actually place on — come from the
          location's <code>overrideCPU</code> / <code>overrideMemory</code> in
          BlazeMeter, and default to 250m/256Mi. Nothing here can read the
          location, so set them there to match the limits above.
        </p>
      )}
      {s.kind === "match" && (
        <p className="text-[11px] text-emerald-700">{s.note}</p>
      )}
      {(s.kind === "unset" || s.kind === "diverge") && (
        <div className="space-y-1.5">
          <p className="text-[11px] text-amber-700">{s.warning}</p>
          {/* The cost before the control, like every other account write on
              this page -- and the divergence may stand: Apply offers the
              match, it never enforces one. */}
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-slate-500">{applyCost(s)}</span>
            <span className="grow" />
            <Button busy={props.applyBusy} onClick={() => props.onApply(s.patch)}>
              Apply
            </Button>
          </div>
          <ErrorMsg msg={props.applyErr} />
        </div>
      )}
    </>
  );
}
