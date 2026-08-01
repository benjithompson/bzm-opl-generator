import { Field, inputCls, TextInput } from "../components";
import { SvPrereqs, svProse } from "../SvPrereqs";
import { Sv } from "../sv";

// Display names only. The set of values is served from generate.SV_INGRESS_TYPES
// -- an unlabelled backend falls back to its raw name and still appears, which
// is the failure mode worth having.
const SV_INGRESS_LABELS: Record<string, string> = {
  nginx: "NGINX", istio: "Istio", contour: "Contour", openshift: "OpenShift Route",
};

/** Service virtualization: which ingress crane publishes through, and the
 *  wildcard domain and TLS secret it needs to do it.
 *
 *  Everything it reads is the one SV record (see sv.ts) and everything it
 *  writes is one of the four handlers -- it decides nothing itself, which is
 *  why the backends it offers and the sentence under a blocked configuration
 *  are values here rather than tests written out in the markup. `sv.ingress` is
 *  null until one is chosen: the select still shows its nginx default, but
 *  nothing is claimed about a backend nobody picked, so the hints fall back to
 *  the generic wording instead of inheriting nginx's.
 */
export function SvGroup(props: {
  sv: Sv;
  onIngress: (v: string) => void;
  onSubdomain: (v: string | null) => void;
  onTlsSecret: (v: string | null) => void;
  onGateway: (v: string | null) => void;
}) {
  const sv = props.sv;
  const chosen = (sv.ingress ?? "").trim();
  // Everything said about the chosen backend, from the one place it is written
  // down. Undefined for a backend nobody has written up: the fields below then
  // fall back to the generic wording rather than showing another backend's.
  const prose = svProse(chosen);
  return (
    <>
      <Field label="Ingress controller"
        hint={prose?.controllerHint
          ?? "must already be installed and serving the wildcard domain below"}>
        {/* Which backends may be offered here is the record's: a plain API
            server serves no route.openshift.io, and generate() refuses that
            combination rather than producing a bundle that stalls. */}
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
        <p className="text-[11px] text-amber-700">
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
