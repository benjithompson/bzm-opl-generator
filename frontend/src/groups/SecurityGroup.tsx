import { Check, Field, inputCls } from "../components";
import { Applies } from "../formats";

/** The two wordings for the two platforms.
 *
 *  Both controls that survive a format with no cluster mean the same thing on
 *  each and are *named* differently by BlazeMeter -- the credential lives in a
 *  Secret or in an --env-file, and self-update is AUTO_KUBERNETES_UPDATE or
 *  AUTO_UPDATE, which are one word apart and different mechanisms. Side by side
 *  in one table rather than interleaved as six ternaries through the markup:
 *  what differs is prose, and prose is easier to keep honest when both versions
 *  are on the same screen. */
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

/** Security & RBAC, sole owner of `service_type`. The SV group used to force
 *  CLUSTERIP and this select hid NODEPORT whenever an ingress was configured;
 *  #60 ran that pairing live and it publishes fine on namespaced RBAC, so both
 *  values are offered whatever else is on.
 *
 *  Two of these five survive a format with no cluster in it (see WORDS); the
 *  RBAC, the Service type and the engine security context are pod and cluster
 *  fields and go. */
export function SecurityGroup(props: {
  applies: Applies;
  /** Is this bundle deployed into a cluster? Which of the two vocabularies the
   *  prose is in, and nothing else -- `applies` decides what is on screen.
   *
   *  Handed down rather than inferred here from `applies("cluster_rbac")`,
   *  which is what it was: that reads one option's presence as a fact about the
   *  platform, so the day `cluster_rbac` leaves the ignored table four labels
   *  silently change language. App knows the format; this is App saying so. */
  cluster: boolean;
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
      {/* On by default, and the only one here whose *unchecked* state is the
          dangerous one -- crane's own default engine pod is privileged, which
          restricted PodSecurity, OpenShift SCC and GKE Autopilot all refuse
          after the agent is already online. The hint says what unchecking
          costs rather than what checking buys. */}
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
      {/* Three options because the option has three states -- "" writes no key
          and takes the default, which is off. On is the one that costs
          something and the hint says what: crane takes ownership of its own
          Deployment within seconds of install, and any later apply -- kubectl
          or helm -- fails on a conflict --force-conflicts cannot resolve. That
          hazard is the Kubernetes one's alone, which is why the wording is
          per-platform: there is no Deployment on a docker host to fight over. */}
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
