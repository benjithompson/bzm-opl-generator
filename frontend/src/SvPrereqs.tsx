import { ReactNode } from "react";
import { SvBackend } from "./api";

// Who owns each thing a virtual service needs. "you" is the one that bites: the
// bundle *names* these objects and never creates them, and a missing one fails
// silently -- the manifests apply, the agent goes idle, the mock pod runs 1/1,
// and the endpoint simply never answers.
type SvOwner = "you" | "bundle" | "none";
export type SvCtx = { ns: string; dom: string; secret: string; gateway: string };
type SvPrereq = { own: SvOwner; text: (c: SvCtx) => ReactNode };

/** Everything said about one backend, in one place. The hints are here rather
 *  than beside the fields they annotate because they are the same kind of claim
 *  as the rows below -- and split across two files, a backend added to one and
 *  forgotten in the other inherits whatever the fallback says, which for these
 *  fields was nginx's advice. */
type SvBackendProse = {
  controller: SvPrereq;
  tls: SvPrereq;
  /** A row only some backends have -- istio's Gateway. */
  extra?: SvPrereq;
  /** Shown under the endpoint host, where the defect actually bites. */
  caveat?: ReactNode;
  controllerHint: string;
  tlsHint: string;
  /** Crane reads KUBERNETES_ISTIO_GATEWAY_NAME in the istio backend alone, so
   *  only that one offers the field; generate() rejects it anywhere else. */
  takesGateway?: boolean;
};

// Per-backend prerequisites, from README "Which one to pick" -- every row of
// that table was measured on a live cluster. Editorial, and so kept here: what
// a controller demands of *you* is not in generate.py to be served. The Role
// row is the opposite -- mechanical -- and comes from /api/sv-constants rather
// than a copy here, which is why it is absent below.
//
// A backend added on the Python side and not here renders no prose at all
// rather than nginx's advice, which would be wrong in the direction that costs
// an afternoon.
const SV_PREREQS: Record<string, SvBackendProse> = {
  nginx: {
    controller: { own: "you", text: () => (
      <>An ingress controller already serving the wildcard domain, registering an{" "}
      <code>IngressClass</code> named <code>nginx</code>. Crane writes{" "}
      <code>ingressClassName: nginx</code> and offers no env var to change it, so
      nothing else will claim its Ingress.</>
    ) },
    tls: { own: "you", text: (c) => (
      <>A Secret <code>{c.secret}</code> in namespace <code>{c.ns}</code> with a
      wildcard certificate for <code>*.{c.dom}</code> — crane's Ingress references it.</>
    ) },
    controllerHint: "must already be installed and serving the wildcard domain below",
    tlsHint: "in the agent namespace; required even for HTTP virtual services",
    caveat: (
      <>On NGINX whether that host answers depends on the controller:
      crane's Ingress backend says port <code>8080</code> while the
      Service it creates publishes <code>80</code>, which by the
      Ingress spec resolves to nothing. <code>ingress-nginx</code>{" "}
      matches leniently and serves it (measured: 200); a strict
      controller builds no route and the host 503s while the mock
      stays healthy. If yours 503s, <code>bzm-opl-gen sv-expose</code>{" "}
      emits a Service + Ingress pair that resolves. Every other
      backend gets the port right.</>
    ),
  },
  istio: {
    controller: { own: "you", text: () => (
      <>Istio installed, with an ingress gateway already serving the wildcard
      domain. No <code>IngressClass</code> is involved — Istio registers none.</>
    ) },
    // Inert, but only on istio: crane writes the :443 server as PASSTHROUGH.
    tls: { own: "none", text: (c) => (
      <>Nothing to create — crane writes the <code>:443</code> server as{" "}
      <code>tls.mode: PASSTHROUGH</code> with no <code>credentialName</code>, so{" "}
      <code>{c.secret}</code> is never read and need not exist or be valid. The
      name stays mandatory (crane crash-loops without it), and an HTTPS virtual
      service terminates TLS in the mock pod itself.</>
    ) },
    // The gateway row, keyed by the backend that reads the env var rather than
    // by an `ingress === "istio"` test further down the render.
    extra: { own: "you", text: (c) => (
      c.gateway
        ? <>Gateway <code>{c.gateway}</code> must already exist — the bundle only
           names it (<code>KUBERNETES_ISTIO_GATEWAY_NAME</code>).</>
        : <>No Gateway to create — crane makes one per virtual service. Name one
           above to reuse a single Gateway instead.</>
    ) },
    controllerHint: "must already be installed and serving the wildcard domain below",
    tlsHint: "required even for HTTP — though nothing on Istio ever reads it",
    takesGateway: true,
  },
  contour: {
    controller: { own: "you", text: () => (
      <>Contour installed and already serving the wildcard domain. No{" "}
      <code>IngressClass</code> is involved — Contour registers none.</>
    ) },
    tls: { own: "you", text: (c) => (
      <>A Secret <code>{c.secret}</code> in namespace <code>{c.ns}</code> with a
      wildcard certificate for <code>*.{c.dom}</code> — crane's HTTPProxy carries
      it as <code>tls.secretName</code> and Contour validates it.</>
    ) },
    controllerHint: "must already be installed and serving the wildcard domain below",
    tlsHint: "in the agent namespace; required even for HTTP virtual services",
  },
  openshift: {
    controller: { own: "none", text: () => (
      <>Nothing to install — the cluster router already serves the domain, and no{" "}
      <code>IngressClass</code> is involved.</>
    ) },
    tls: { own: "none", text: (c) => (
      <>Not referenced — crane's Route terminates <code>edge</code>/
      <code>Allow</code> at the router, so nothing reads <code>{c.secret}</code>.
      The name stays mandatory; crane validates it at startup.</>
    ) },
    // Telling an OpenShift user to install a controller would contradict the
    // prerequisite row directly above it.
    controllerHint: "the cluster router already serves the wildcard domain below",
    tlsHint: "in the agent namespace; required even for HTTP virtual services",
  },
};

/** The prose for one backend, for the fields App renders outside this panel.
 *  Undefined for a backend nobody has written up yet -- callers fall back
 *  rather than inheriting another backend's claims. */
export function svProse(ingress: string): SvBackendProse | undefined {
  return SV_PREREQS[ingress];
}

const OWNER_BADGE: Record<SvOwner, [string, string]> = {
  you: ["you create", "bg-amber-50 text-amber-700 border-amber-200"],
  bundle: ["in the bundle", "bg-slate-100 text-slate-500 border-slate-200"],
  none: ["nothing to do", "bg-slate-50 text-slate-400 border-slate-200"],
};

function PrereqItem({ own, children }: { own: SvOwner; children: ReactNode }) {
  const badge = OWNER_BADGE[own];
  return (
    <li className="flex gap-2 items-baseline">
      <span className={`shrink-0 w-[74px] text-center rounded border px-1 py-px text-[10px] font-medium ${badge[1]}`}>
        {badge[0]}
      </span>
      <span className="text-[11px] text-slate-500">{children}</span>
    </li>
  );
}

/** Everything an SV bundle needs that it does not itself create, per backend,
 *  plus the endpoint host to check afterwards. The bundle *names* these objects
 *  and a cluster missing one gives no error at all, so both sides are spelled
 *  out while there is still time to fix it.
 *
 *  Its own file because none of it is App's business: it is prose keyed by the
 *  chosen backend. `rbac` arrives from /api/sv-constants rather than being
 *  restated here -- see the Role row.
 */
export function SvPrereqs(
    { ingress, ctx, rbac }:
    { ingress: string; ctx: SvCtx; rbac?: SvBackend }) {
  const backend = SV_PREREQS[ingress];
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50/70 px-3 py-2 space-y-1.5">
      <p className="text-xs font-medium text-slate-600">
        What a virtual service needs, and who provides it
      </p>
      <ul className="space-y-1">
        {backend && (
          <>
            <PrereqItem own={backend.controller.own}>
              {backend.controller.text(ctx)}
            </PrereqItem>
            <PrereqItem own={backend.tls.own}>
              {backend.tls.text(ctx)}
            </PrereqItem>
          </>
        )}
        {backend?.extra && (
          <PrereqItem own={ctx.gateway ? backend.extra.own : "none"}>
            {backend.extra.text(ctx)}
          </PrereqItem>
        )}
        {/* The one row that is mechanical rather than editorial,
            so it is read off generate.SV_INGRESS_BACKENDS via
            /api/sv-constants. A Role restated by hand here would
            go stale silently -- a wrong one reads as plausible
            right up until the virtual service stalls. */}
        {rbac && (
          <PrereqItem own="bundle">
            a Role on <code>{rbac.group}</code>{" "}
            <code>{rbac.resources.join(", ")}</code>; crane publishes
            one {rbac.creates} per virtual service. Namespaced only —
            no ClusterRole.
          </PrereqItem>
        )}
        <PrereqItem own="bundle">
          <code>KUBERNETES_WEB_EXPOSE_TYPE</code> /{" "}
          <code>_SUB_DOMAIN</code> / <code>_TLS_SECRET_NAME</code> in the
          agent ConfigMap — how crane learns what to publish, and where.
        </PrereqItem>
      </ul>
      <p className="text-[11px] text-slate-500">
        Once deployed, each virtual service is served at{" "}
        <code className="text-slate-700">
          &lt;service&gt;-&lt;port&gt;-{ctx.ns}.{ctx.dom}
        </code>{" "}
        — check that host. Miss one of the above and nothing errors:
        the manifests apply, the agent reports idle, the mock pod runs
        1/1, and the endpoint never answers.
      </p>
      {/* A backend's caveat belongs to the endpoint above, not to the select
          that chose it -- that is where someone is deciding whether the host
          they were just given will actually answer. */}
      {backend?.caveat && (
        <p className="text-[11px] text-amber-700">{backend.caveat}</p>
      )}
    </div>
  );
}
