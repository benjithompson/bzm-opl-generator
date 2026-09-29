import { Callout, Field, TextInput } from "../components";
import { Applies } from "../formats";
import { CaMode } from "../optionGroups";

/** Custom CA trust, asked as one question: the certificate's file name. The
 *  bundle builds the ConfigMap from that file, as BlazeMeter's own docs do.
 *  Blank is allowed and becomes `<CA_CERT_FILE>`.
 *
 *  OpenShift injection is offered only on an OpenShift cluster (elsewhere
 *  nothing fills its ConfigMap). The inline and existing modes are never set
 *  here, but a profile carrying one is named, with a way back to the file mode. */
export function CaGroup(props: {
  applies: Applies;
  /** Is the target cluster OpenShift? Decides whether injection is offered. */
  openshift: boolean;
  mode: CaMode;
  onMode: (m: CaMode) => void;
  configmap: string;
  configmapKey: string;
  certFile: string;
  onCertFile: (v: string) => void;
}) {
  // Injection names its own ConfigMap key, so the file name field goes.
  const injecting = props.mode === "inject";
  // The modes only the CLI or a profile sets: named rather than silently replaced.
  const elsewhere =
    props.mode === "inline" ? "a certificate pasted in at the command line"
      : props.mode === "existing"
        ? `an existing ConfigMap (${props.configmap || "unnamed"}${
            props.configmapKey ? `, key ${props.configmapKey}` : ""})`
        : null;
  return (
    <>
      {elsewhere && (
        <Callout tone="amber" className="text-2xs space-y-1.5">
          <p>
            This bundle takes its CA trust from {elsewhere}, which was set
            outside this page. It is kept and it will be generated.
          </p>
          <button type="button"
            className="rounded-md px-2 py-1 font-medium border border-amber-400
                       text-amber-900 hover:bg-amber-100"
            onClick={() => props.onMode("file")}>
            Use a certificate file instead
          </button>
        </Callout>
      )}
      {!elsewhere && !injecting && (
        <Field label="Certificate file name"
          hint="the file your certificate is in — the ConfigMap is built from it, and crane and its engines mount it under that name">
          <TextInput mono placeholder="ca-bundle.crt"
            value={props.certFile}
            onChange={props.onCertFile} />
        </Field>
      )}
      {props.openshift && props.applies("ca_openshift_inject") && !elsewhere && (
        <label className="flex items-start gap-2 cursor-pointer select-none text-sm">
          <input type="checkbox" className="mt-1 accent-bzm"
            checked={injecting}
            onChange={(e) => props.onMode(e.target.checked ? "inject" : "file")} />
          <span>Use the OpenShift cluster trust bundle instead
            <span className="block text-2xs text-slate-400">
              an empty ConfigMap labeled inject-trusted-cabundle; the cluster
              injects and rotates ca-bundle.crt, so there is no file to name
            </span>
          </span>
        </label>
      )}
      {/* Where the certificate ends up on each platform; both point the same
          two variables at it (crane's HTTP client reads one, boto the other). */}
      <p className="text-2xs text-slate-400">
        {props.applies("ca_existing_configmap")
          ? <>Mounted read-only at /var/cm in crane; engines get the same
              ConfigMap via KUBERNETES_CA_BUNDLE_MOUNT, and
              REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.</>
          : <>Written beside the run script and mounted into the container;
              REQUESTS_CA_BUNDLE / AWS_CA_BUNDLE point at it.</>}
      </p>
    </>
  );
}
