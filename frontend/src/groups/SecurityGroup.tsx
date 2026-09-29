import { Check, Field, inputCls } from "../components";
import { Applies } from "../formats";

/** The wording for each platform: the credential lives in a Secret or an
 *  env file, and self-update is AUTO_KUBERNETES_UPDATE or AUTO_UPDATE. */
const WORDS = {
  cluster: {
    token: "AUTH_TOKEN in a Secret",
    tokenHint: "uncheck = simplified ConfigMap variant",
    update: "Agent auto-update (AUTO_KUBERNETES_UPDATE)",
    updateHint: "off keeps the agent on this bundle's image; keeping it current is then your job. On, crane rewrites its own Deployment, and re-applying this bundle conflicts with it",
    updateOff: "Default — off, re-applying keeps working",
    updateOn: "On — crane updates its own Deployment",
  },
  host: {
    token: "AUTH_TOKEN in an --env-file",
    tokenHint: "uncheck = inline on the docker run command, where ps can read it",
    update: "Agent auto-update (AUTO_UPDATE)",
    updateHint: "off keeps the agent on this bundle's image; keeping it current is then your job. On, crane pulls a newer image for the container you started",
    updateOff: "Default — off",
    updateOn: "On — crane pulls a newer image",
  },
};

/** Security & RBAC, the sole owner of `service_type` (both values work with an
 *  SV ingress). Only the credential and self-update survive a docker bundle. */
export function SecurityGroup(props: {
  applies: Applies;
  /** Is this bundle deployed into a cluster? Picks the wording only; `applies`
   *  decides what is on screen. */
  cluster: boolean;
  useSecret: boolean;
  clusterRbac: boolean;
  restrictEngines: boolean;
  serviceType: string;
  /** Tri-state: null means the bundle has not said, so the default (off)
   *  applies and no key is written. */
  autoUpdate: boolean | null;
  onUseSecret: (v: boolean) => void;
  onClusterRbac: (v: boolean) => void;
  onRestrictEngines: (v: boolean) => void;
  onServiceType: (v: string) => void;
  onAutoUpdate: (v: boolean | null) => void;
}) {
  const w = props.cluster ? WORDS.cluster : WORDS.host;
  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        <Check label={w.token} hint={w.tokenHint}
          checked={props.useSecret}
          onChange={props.onUseSecret} />
        {props.applies("cluster_rbac") && (
          <Check label="Read-only nodes ClusterRole"
            hint="optional; not needed for perf tests"
            checked={props.clusterRbac}
            onChange={props.onClusterRbac} />
        )}
      </div>
      {/* On by default; unchecking it is the dangerous state (crane's default
          engine pod is privileged, which restricted clusters refuse). */}
      {props.applies("restrict_engines") && (
        <Check label="Engines drop privileges"
          hint="uncheck only for an image needing a capability — it applies to every container crane creates. Privileged engines are refused by restricted PodSecurity, OpenShift SCC and GKE Autopilot"
          checked={props.restrictEngines}
          onChange={props.onRestrictEngines} />
      )}
      {props.applies("service_type") && (
        <Field label="Service type"
          hint="NODEPORT is BlazeMeter's default but often disallowed">
          <select className={inputCls} value={props.serviceType}
            onChange={(e) => props.onServiceType(e.target.value)}>
            <option value="CLUSTERIP">CLUSTERIP</option>
            <option value="NODEPORT">NODEPORT</option>
          </select>
        </Field>
      )}
      {/* Three states: "" writes nothing (default off). On Kubernetes, on means
          crane takes over its Deployment and later applies conflict. */}
      <Field label={w.update} hint={w.updateHint}>
        <select className={inputCls}
          value={props.autoUpdate == null ? "" : String(props.autoUpdate)}
          onChange={(e) => props.onAutoUpdate(
            e.target.value === "" ? null : e.target.value === "true")}>
          <option value="">{w.updateOff}</option>
          <option value="true">{w.updateOn}</option>
          <option value="false">Off — pinned to the image in this bundle</option>
        </select>
      </Field>
    </>
  );
}
