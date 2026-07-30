// The three values BlazeMeter shows on an agent, typed in by hand.
//
// The case this exists for: producing manifests for a customer whose BlazeMeter
// account and cluster you have no access to. They read you a harbor id, a ship
// id and a token; that is enough to render everything, because every other fact
// the generator wants has a documented default.
//
// Nothing here validates. The ids are opaque, there is no account to check them
// against, and a format guess would only ever reject input that was correct.
// The fields say what each value is instead, so a wrong paste is visible.
//
// Identity only. What the location *runs* is declared once, in the Configure
// step -- asking it here as well made one fact two questions in two
// vocabularies (funcIds here, features there).
import { Field, SecretInput, TextInput } from "../components";

export function ManualSource(props: {
  harborId: string;
  shipId: string;
  authToken: string;
  onHarborId: (v: string) => void;
  onShipId: (v: string) => void;
  onAuthToken: (v: string) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        From the agent's install command in BlazeMeter (Settings → Private
        Locations → your agent). Nothing is sent to BlazeMeter and nothing is
        checked — this only fills in what the manifests need.
      </p>

      <Field label="Harbor ID (private location)"
        hint="HARBOR_ID — identifies the location the agent joins">
        <TextInput mono placeholder="6a63a79dcc45dccca90bf440"
          value={props.harborId} onChange={props.onHarborId} />
      </Field>

      <Field label="Ship ID (agent)"
        hint="SHIP_ID — this agent's own identity, and part of the Deployment's selector">
        <TextInput mono placeholder="6a679d3445115b6651011715"
          value={props.shipId} onChange={props.onShipId} />
      </Field>

      {/* Masked: the same field the connected path now has, and the same reason
          -- see SecretInput. Left empty the bundle still generates, with the
          placeholder and a banner beside the download saying so. */}
      <Field label="Auth token"
        hint="AUTH_TOKEN — goes into the Secret. Anyone holding it can register as this agent.">
        <SecretInput placeholder="af1736ce6c96ec3ecd2c3838ad20ed3c…"
          value={props.authToken} onChange={props.onAuthToken} />
      </Field>


    </div>
  );
}
