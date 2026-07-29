import { Check, Field, inputCls } from "../components";

/** Security & RBAC, sole owner of `service_type`. The SV group used to force
 *  CLUSTERIP and this select hid NODEPORT whenever an ingress was configured;
 *  #60 ran that pairing live and it publishes fine on namespaced RBAC, so both
 *  values are offered whatever else is on. */
export function SecurityGroup(props: {
  useSecret: boolean;
  clusterRbac: boolean;
  restrictEngines: boolean;
  serviceType: string;
  /** Tri-state, and null is a value: it means the bundle has not said, so the
   *  default (off) applies. A checkbox cannot hold that -- it would have to
   *  show the resolved answer as if someone had chosen it, and ticking it
   *  would then write a key that was never there. */
  autoUpdate: boolean | null;
  onUseSecret: (v: boolean) => void;
  onClusterRbac: (v: boolean) => void;
  onRestrictEngines: (v: boolean) => void;
  onServiceType: (v: string) => void;
  onAutoUpdate: (v: boolean | null) => void;
}) {
  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        <Check label="AUTH_TOKEN in a Secret"
          hint="uncheck = simplified ConfigMap variant"
          checked={props.useSecret}
          onChange={props.onUseSecret} />
        <Check label="Read-only nodes ClusterRole"
          hint="optional; not needed for perf tests"
          checked={props.clusterRbac}
          onChange={props.onClusterRbac} />
      </div>
      {/* On by default, and the only one here whose *unchecked* state is the
          dangerous one -- crane's own default engine pod is privileged, which
          restricted PodSecurity, OpenShift SCC and GKE Autopilot all refuse
          after the agent is already online. The hint says what unchecking
          costs rather than what checking buys. */}
      <Check label="Engines drop privileges"
        hint="uncheck only for an image needing a capability, and it unchecks for every container crane creates; privileged engines are rejected by restricted PodSecurity, OpenShift SCC and GKE Autopilot"
        checked={props.restrictEngines}
        onChange={props.onRestrictEngines} />
      <Field label="Service type"
        hint="NODEPORT is BlazeMeter's default but often disallowed">
        <select className={inputCls} value={props.serviceType}
          onChange={(e) => props.onServiceType(e.target.value)}>
          <option value="CLUSTERIP">CLUSTERIP</option>
          <option value="NODEPORT">NODEPORT</option>
        </select>
      </Field>
      {/* Three options because the option has three states -- "" writes no key
          and takes the default, which is off. On is the one that costs
          something and the hint says what: crane takes ownership of its own
          Deployment within seconds of install, and the next `helm upgrade`
          fails on a conflict --force-conflicts cannot resolve. The variable is
          named in the label because BlazeMeter has an AUTO_UPDATE too -- the
          Docker-side switch, inert here -- and this is not it. */}
      <Field label="Agent auto-update (AUTO_KUBERNETES_UPDATE)"
        hint="off by default: the agent stays on the image in this bundle, and keeping it current is your job. On, crane rewrites its own Deployment and `helm upgrade` stops working">
        <select className={inputCls}
          value={props.autoUpdate == null ? "" : String(props.autoUpdate)}
          onChange={(e) => props.onAutoUpdate(
            e.target.value === "" ? null : e.target.value === "true")}>
          <option value="">Default — off, upgrades keep working</option>
          <option value="true">On — crane updates its own Deployment</option>
          <option value="false">Off — pinned to the image in this bundle</option>
        </select>
      </Field>
    </>
  );
}
