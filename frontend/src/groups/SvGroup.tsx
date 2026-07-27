import { SvBackend } from "../api";
import { Field, inputCls, TextInput } from "../components";
import { SvCtx, SvPrereqs, svProse } from "../SvPrereqs";

// Display names only. The set of values is served from generate.SV_INGRESS_TYPES
// -- an unlabelled backend falls back to its raw name and still appears, which
// is the failure mode worth having.
const SV_INGRESS_LABELS: Record<string, string> = {
  nginx: "NGINX", istio: "Istio", contour: "Contour", openshift: "OpenShift Route",
};

/** Service virtualization: which ingress crane publishes through, and the
 *  wildcard domain and TLS secret it needs to do it.
 *
 *  `ingress` is null until one is chosen -- the select still shows its nginx
 *  default, but nothing is claimed about a backend nobody picked, so the
 *  hints fall back to the generic wording instead of inheriting nginx's.
 */
export function SvGroup(props: {
  ingress: string | null;
  ingressTypes: string[];
  openshift: boolean;
  subdomain: string;
  tlsSecret: string;
  gateway: string;
  onIngress: (v: string) => void;
  onSubdomain: (v: string | null) => void;
  onTlsSecret: (v: string | null) => void;
  onGateway: (v: string | null) => void;
  /** Whether the SV settings are complete enough to generate; false shows the
   *  reason, which is the NODEPORT conflict or the two mandatory fields. */
  ok: boolean;
  nodePortConflict: boolean;
  ctx: SvCtx;
  rbac?: SvBackend;
}) {
  const chosen = (props.ingress ?? "").trim();
  // Everything said about the chosen backend, from the one place it is written
  // down. Undefined for a backend nobody has written up: the fields below then
  // fall back to the generic wording rather than showing another backend's.
  const prose = svProse(chosen);
  return (
    <>
      <Field label="Ingress controller"
        hint={prose?.controllerHint
          ?? "must already be installed and serving the wildcard domain below"}>
        <select className={inputCls} value={props.ingress ?? "nginx"}
          onChange={(e) => props.onIngress(e.target.value)}>
          {props.ingressTypes
            // openshift publishes a route.openshift.io Route, which
            // a plain API server does not serve -- generate()
            // refuses the combination, so do not offer it.
            .filter((t) => t !== "openshift" || props.openshift)
            .map((t) => (
              <option key={t} value={t}>
                {SV_INGRESS_LABELS[t] ?? t}
              </option>
            ))}
        </select>
      </Field>
      <Field label="Wildcard domain"
        hint="endpoints become <service>-<port>-<namespace>.<domain>">
        <TextInput mono placeholder="apps.example.com"
          value={props.subdomain}
          onChange={(v) => props.onSubdomain(v || null)} />
      </Field>
      <Field label="Wildcard TLS secret"
        hint={prose?.tlsHint
          ?? "in the agent namespace; required even for HTTP virtual services"}>
        <TextInput mono placeholder="wildcard-credential"
          value={props.tlsSecret}
          onChange={(v) => props.onTlsSecret(v || null)} />
      </Field>
      {prose?.takesGateway && (
        <Field label="Istio Gateway name (optional)"
          hint="leave empty and crane creates a Gateway per virtual service">
          <TextInput mono placeholder="bzm-gateway"
            value={props.gateway}
            onChange={(v) => props.onGateway(v || null)} />
        </Field>
      )}
      {!props.ok && (
        <p className="text-[11px] text-amber-700">
          {props.nodePortConflict
            ? "Service type must be CLUSTERIP — NODEPORT sends crane to the cluster-scoped Node object, which namespaced RBAC cannot grant."
            : "Domain and TLS secret are both required — without them crane crash-loops on “TLS secret name is empty”."}
        </p>
      )}
      <SvPrereqs ingress={chosen} ctx={props.ctx} rbac={props.rbac} />
    </>
  );
}
