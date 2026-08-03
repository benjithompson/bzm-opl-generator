import { Field, inputCls, TextInput } from "../components";
import { Applies } from "../formats";
import { CaMode } from "../optionGroups";

// Who owns the bundle, per mode -- the difference that decides which one to
// pick, and the reason the modes are radios rather than three sets of fields.
// `key` is the option the mode writes, so a format that carries no ConfigMap
// drops the two ConfigMap modes without this file knowing which format that is.
const CA_MODES: { mode: CaMode; label: string; hint: string; key: string }[] = [
  {
    mode: "existing",
    label: "Reference an existing ConfigMap (recommended)",
    hint: "your platform/security team owns and rotates the trust bundle (e.g. via trust-manager); manifests only reference it",
    key: "ca_existing_configmap",
  },
  {
    mode: "inline",
    label: "Paste PEM — generator creates the ConfigMap",
    hint: "you own the bundle; rotation means regenerating and re-applying",
    key: "ca_bundle",
  },
  {
    mode: "inject",
    label: "OpenShift cluster trust injection",
    hint: "empty ConfigMap labeled inject-trusted-cabundle; the cluster injects and rotates ca-bundle.crt — OpenShift only",
    key: "ca_openshift_inject",
  },
];

/** Custom CA trust. The mode is derived from the options rather than stored
 *  (caModeOf), so the radios and the group's own switch cannot disagree.
 *
 *  With one mode left there is nothing to choose, so the radios go and the PEM
 *  field is the group -- a single radio that cannot be unpicked is a control
 *  pretending to be a question. That is the docker case: the bundle writes the
 *  PEM beside its script and mounts the file, and the other two modes name a
 *  ConfigMap there is nothing to read one out of. `generate._ca_cfg` agrees --
 *  it stops counting those two as competing modes for that format, so a bundle
 *  configured for Kubernetes and switched here is not refused over the
 *  ConfigMap name it still carries. */
export function CaGroup(props: {
  applies: Applies;
  mode: CaMode;
  onMode: (m: CaMode) => void;
  configmap: string;
  configmapKey: string;
  bundle: string;
  onConfigmap: (v: string) => void;
  onConfigmapKey: (v: string | null) => void;
  onBundle: (v: string) => void;
}) {
  const modes = CA_MODES.filter((m) => props.applies(m.key));
  // Nothing to choose: the mode is the only one this format has, whatever the
  // options say. `caModeOf` can only have read one the format does not carry
  // (Kubernetes, then switched), and that value is kept -- the generator names
  // it in the README rather than dropping it -- but what is on screen is the
  // field that reaches something.
  const single = modes.length === 1;
  const mode = single ? modes[0].mode : props.mode;
  return (
    <>
      {!single && (
        <div className="space-y-1.5 text-sm">
          {modes.map((m) => (
            <label key={m.mode} className="flex items-start gap-2 cursor-pointer select-none">
              <input type="radio" name="ca-mode" className="mt-1 accent-bzm"
                checked={mode === m.mode} onChange={() => props.onMode(m.mode)} />
              <span>{m.label}
                <span className="block text-[11px] text-slate-400">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
      )}
      {mode === "existing" && (
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
      {mode === "inline" && (
        <Field label="CA bundle (PEM)">
          <textarea className={inputCls + " font-mono text-[10px]"} rows={3}
            placeholder="-----BEGIN CERTIFICATE-----"
            value={props.bundle}
            onChange={(e) => props.onBundle(e.target.value)} />
        </Field>
      )}
      {/* Where it ends up, which is the whole difference between the two
          platforms: a ConfigMap the pods mount, or a file beside the script the
          container mounts. Both end with the same two variables pointed at it,
          because crane's HTTP client reads one and boto the other. */}
      <p className="text-[11px] text-slate-400">
        {!single
          ? <>Mounted read-only at /var/cm in crane; engines get the same
              ConfigMap via KUBERNETES_CA_BUNDLE_MOUNT, and
              REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.</>
          : <>Written beside the run script and mounted into the container;
              REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.</>}
      </p>
    </>
  );
}
