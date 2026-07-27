import { JsonArea } from "../components";

/** Scheduling: both options are free-form JSON, so both are handed straight to
 *  JsonArea, which only reports a value once it parses. */
export function SchedGroup(props: {
  tolerations: unknown;
  nodeSelector: unknown;
  onTolerations: (v: unknown) => void;
  onNodeSelector: (v: unknown) => void;
}) {
  return (
    <>
      <JsonArea label="Tolerations (JSON list — crane pod + engines)"
        value={props.tolerations}
        placeholder='[{"key":"lifecycle","operator":"Equal","value":"spot","effect":"NoSchedule"}]'
        onValid={props.onTolerations} />
      <JsonArea label="Node selector (JSON object)" rows={2}
        value={props.nodeSelector}
        placeholder='{"pool":"loadtest"}'
        onValid={props.onNodeSelector} />
    </>
  );
}
