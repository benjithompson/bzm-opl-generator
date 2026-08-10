import { Field, TextInput } from "../components";
import { Applies } from "../formats";
import { CaMode } from "../optionGroups";

/** Custom CA trust. The mode is derived from the options rather than stored
 *  (caModeOf), so what is on screen and the group's own switch cannot disagree.
 *
 *  **One question, and it is the certificate's file name.** This was four radio
 *  buttons -- a PEM slot, a paste box, somebody else's ConfigMap, OpenShift
 *  injection -- which asked a customer to choose an ownership model before they
 *  could say the one thing they knew. BlazeMeter's own documentation asks for
 *  neither a model nor a PEM: their agent chart takes
 *  `ca_bundle.request_ca_bundle` and `ca_bundle.aws_ca_bundle` as *file names*,
 *  and their Deployment example references a ConfigMap built from that file. So
 *  that is what this asks for, and the ownership question went away with the
 *  modes -- the ConfigMap is built from your file either way, by `helm install`
 *  or by the `kubectl create configmap` line the README prints.
 *
 *  Blank is allowed and is not an error. It becomes `<CA_CERT_FILE>`, every
 *  surface names it, and the chart refuses the install -- which is the marker
 *  rule, and the reason there is no asterisk and no red border here. A bundle
 *  is routinely generated before anybody knows what the certificate will be
 *  called.
 *
 *  Two modes survive and neither is a choice this page makes lightly:
 *
 *  - **OpenShift injection**, offered only where the cluster is OpenShift. It
 *    is the cluster's own operator filling a labeled ConfigMap, so off
 *    OpenShift it emits a ConfigMap nothing ever fills and the agent trusts
 *    nothing extra -- a silent failure, and the one thing this group must not
 *    offer there. It is the *cluster* and not the SCC posture: the posture is
 *    recommended on vanilla Kubernetes too, and reading the mode off it was how
 *    this got offered there. Turning the cluster toggle off clears the option
 *    as well as hiding the control (see AdvancedRow), so it cannot survive off
 *    screen.
 *  - **inline** and **existing**, which this page never *sets*. They are still
 *    reachable from the CLI and from a profile, and a form showing nothing for
 *    a value the bundle carries is the failure this file's rules are about --
 *    so a loaded profile carrying one is named, with the one control that gets
 *    back to the mode this page does offer.
 */
export function CaGroup(props: {
  applies: Applies;
  /** Is the target cluster OpenShift? Decides whether injection is offered at
   *  all -- see the note above. */
  openshift: boolean;
  mode: CaMode;
  onMode: (m: CaMode) => void;
  configmap: string;
  configmapKey: string;
  certFile: string;
  onCertFile: (v: string) => void;
}) {
  // Injection is the only alternative this page offers, and only sometimes. It
  // names its own ConfigMap key (OpenShift writes `ca-bundle.crt` into a
  // labeled ConfigMap), so picking it takes the file name off screen rather
  // than leaving a field that reaches nothing.
  const injecting = props.mode === "inject";
  // The two the CLI can set. Named rather than silently replaced: what is on
  // screen has to describe the bundle that will be generated.
  const elsewhere =
    props.mode === "inline" ? "a certificate pasted in at the command line"
      : props.mode === "existing"
        ? `an existing ConfigMap (${props.configmap || "unnamed"}${
            props.configmapKey ? `, key ${props.configmapKey}` : ""})`
        : null;
  return (
    <>
      {elsewhere && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2
                        text-[11px] text-amber-900 space-y-1.5">
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
        </div>
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
            <span className="block text-[11px] text-slate-400">
              an empty ConfigMap labeled inject-trusted-cabundle; the cluster
              injects and rotates ca-bundle.crt, so there is no file to name
            </span>
          </span>
        </label>
      )}
      {/* Where it ends up, which is the whole difference between the two
          platforms: a ConfigMap the pods mount, or a file beside the script the
          container mounts. Both end with the same two variables pointed at it,
          because crane's HTTP client reads one and boto the other. */}
      <p className="text-[11px] text-slate-400">
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
