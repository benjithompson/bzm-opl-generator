import { Check, Field, inputCls } from "../components";

/** Security & RBAC. `service_type` is written here and by the SV group, which
 *  forces it to CLUSTERIP -- hence `svOn`: with an SV ingress configured the
 *  select offers the one value that works, rather than a state whose only
 *  outcome is a blocked download. */
export function SecurityGroup(props: {
  useSecret: boolean;
  clusterRbac: boolean;
  serviceType: string;
  svOn: boolean;
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
        hint={props.svOn
          ? "locked to CLUSTERIP: service virtualization reaches pods through the ingress, and NODEPORT would need cluster-scoped node access"
          : "NODEPORT is BlazeMeter's default but often disallowed"}>
        <select className={inputCls} value={props.serviceType}
          onChange={(e) => props.onServiceType(e.target.value)}>
          <option value="CLUSTERIP">CLUSTERIP</option>
          {/* Offering NODEPORT while SV is on would only lead to a
              blocked download; make the bad state unreachable. */}
          {!props.svOn && <option value="NODEPORT">NODEPORT</option>}
        </select>
      </Field>
    </>
  );
}
