import { Check, Field, inputCls } from "../components";

/** Security & RBAC, sole owner of `service_type`. The SV group used to force
 *  CLUSTERIP and this select hid NODEPORT whenever an ingress was configured;
 *  #60 ran that pairing live and it publishes fine on namespaced RBAC, so both
 *  values are offered whatever else is on. */
export function SecurityGroup(props: {
  useSecret: boolean;
  clusterRbac: boolean;
  serviceType: string;
  onUseSecret: (v: boolean) => void;
  onClusterRbac: (v: boolean) => void;
  onServiceType: (v: string) => void;
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
      <Field label="Service type"
        hint="NODEPORT is BlazeMeter's default but often disallowed">
        <select className={inputCls} value={props.serviceType}
          onChange={(e) => props.onServiceType(e.target.value)}>
          <option value="CLUSTERIP">CLUSTERIP</option>
          <option value="NODEPORT">NODEPORT</option>
        </select>
      </Field>
    </>
  );
}
