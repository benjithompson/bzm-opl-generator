import { Field, inputCls, TextInput } from "../components";
import { CaMode } from "../optionGroups";

// Who owns the bundle, per mode -- the difference that decides which one to
// pick, and the reason the modes are radios rather than three sets of fields.
const CA_MODES: [CaMode, string, string][] = [
  ["existing", "Reference an existing ConfigMap (recommended)",
    "your platform/security team owns and rotates the trust bundle (e.g. via trust-manager); manifests only reference it"],
  ["inline", "Paste PEM — generator creates the ConfigMap",
    "you own the bundle; rotation means regenerating and re-applying"],
  ["inject", "OpenShift cluster trust injection",
    "empty ConfigMap labeled inject-trusted-cabundle; the cluster injects and rotates ca-bundle.crt — OpenShift only"],
];

/** Custom CA trust. The mode is derived from the options rather than stored
 *  (caModeOf), so the radios and the group's own switch cannot disagree. */
export function CaGroup(props: {
  mode: CaMode;
  onMode: (m: CaMode) => void;
  configmap: string;
  configmapKey: string;
  bundle: string;
  onConfigmap: (v: string) => void;
  onConfigmapKey: (v: string | null) => void;
  onBundle: (v: string) => void;
}) {
  return (
    <>
      <div className="space-y-1.5 text-sm">
        {CA_MODES.map(([m, label, hint]) => (
          <label key={m} className="flex items-start gap-2 cursor-pointer select-none">
            <input type="radio" name="ca-mode" className="mt-1 accent-bzm"
              checked={props.mode === m} onChange={() => props.onMode(m)} />
            <span>{label}
              <span className="block text-[11px] text-slate-400">{hint}</span>
            </span>
          </label>
        ))}
      </div>
      {props.mode === "existing" && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="ConfigMap name">
            <TextInput mono placeholder="corp-trust-bundle"
              value={props.configmap}
              onChange={props.onConfigmap} />
          </Field>
          <Field label="Bundle key" hint="file key inside the ConfigMap">
            <TextInput mono placeholder="ca-bundle.crt"
              value={props.configmapKey}
              onChange={(v) => props.onConfigmapKey(v || null)} />
          </Field>
        </div>
      )}
      {props.mode === "inline" && (
        <Field label="CA bundle (PEM)">
          <textarea className={inputCls + " font-mono text-[10px]"} rows={3}
            placeholder="-----BEGIN CERTIFICATE-----"
            value={props.bundle}
            onChange={(e) => props.onBundle(e.target.value)} />
        </Field>
      )}
      <p className="text-[11px] text-slate-400">
        Mounted read-only at /var/cm in crane; engines get the same
        ConfigMap via KUBERNETES_CA_BUNDLE_MOUNT, and
        REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.
      </p>
    </>
  );
}
