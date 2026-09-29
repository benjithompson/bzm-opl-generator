import { Field, inputCls, TextInput } from "../components";
import { SvPrereqs, svProse } from "./SvPrereqs";
import { Sv } from "../sv";

// Display names only; the values are served. An unlabelled backend shows its raw name.
const SV_INGRESS_LABELS: Record<string, string> = {
  nginx: "NGINX", istio: "Istio", contour: "Contour", openshift: "OpenShift Route",
};

/** Service virtualization: the ingress crane publishes through, and the
 *  wildcard domain and TLS secret it needs. Reads the SV record and decides
 *  nothing. `sv.ingress` is null until chosen, and then the hints stay generic. */
export function SvGroup(props: {
  sv: Sv;
  onIngress: (v: string) => void;
  onSubdomain: (v: string | null) => void;
  onTlsSecret: (v: string | null) => void;
  onGateway: (v: string | null) => void;
}) {
  const sv = props.sv;
  const chosen = (sv.ingress ?? "").trim();
  // The chosen backend's prose; undefined falls back to the generic wording.
  const prose = svProse(chosen);
  return (
    <>
      <Field label="Ingress controller"
        hint={prose?.controllerHint
          ?? "must already be installed and serving the wildcard domain below"}>
        {/* The offered backends are the record's (no OpenShift Route off OpenShift). */}
        <select className={inputCls} value={sv.ingress ?? "nginx"}
          onChange={(e) => props.onIngress(e.target.value)}>
          {sv.ingressTypes.map((t) => (
            <option key={t} value={t}>
              {SV_INGRESS_LABELS[t] ?? t}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Wildcard domain"
        hint="endpoints become <service>-<port>-<namespace>.<domain>">
        <TextInput mono placeholder="apps.example.com"
          value={sv.fields.subdomain}
          onChange={(v) => props.onSubdomain(v || null)} />
      </Field>
      <Field label="Wildcard TLS secret"
        hint={prose?.tlsHint
          ?? "in the agent namespace; required even for HTTP virtual services"}>
        <TextInput mono placeholder="wildcard-credential"
          value={sv.fields.tlsSecret}
          onChange={(v) => props.onTlsSecret(v || null)} />
      </Field>
      {prose?.takesGateway && (
        <Field label="Istio Gateway name (optional)"
          hint="leave empty and crane creates a Gateway per virtual service">
          <TextInput mono placeholder="bzm-gateway"
            value={sv.fields.gateway}
            onChange={(v) => props.onGateway(v || null)} />
        </Field>
      )}
      {!sv.ok && (
        <p className="text-2xs text-amber-700">
          {sv.nodePortConflict
            ? `Service type must be CLUSTERIP for this backend — crane writes the
               Service's nodePort into the ${sv.rbac?.creates ?? "published object"},
               which nothing reaches the ingress on, so the endpoint never serves.
               Change it under Security & RBAC, or pick nginx or openshift.`
            : "Domain and TLS secret are both required — without them crane crash-loops on “TLS secret name is empty”."}
        </p>
      )}
      <SvPrereqs ingress={chosen} ctx={sv.ctx} rbac={sv.rbac} />
    </>
  );
}
