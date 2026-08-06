// The three values BlazeMeter shows on an agent, typed in by hand.
//
// The case this exists for: producing manifests for a customer whose BlazeMeter
// account and cluster you have no access to. They read you a harbor id, a ship
// id and a token; that is enough to render everything, because every other fact
// the generator wants has a documented default.
//
// It checks their *shape* and nothing else -- see manualIds.ts, which is where
// that argument and its tests live. This file used to say it checked nothing at
// all, on the grounds that a format guess can only reject correct input; the
// shapes turned out not to be a guess, and the failure it was tolerating is
// silent (a bundle that applies cleanly and never joins anything).
//
// Identity only. What the location *runs* is declared once, in the Configure
// step -- asking it here as well made one fact two questions in two
// vocabularies (funcIds here, functionalities there).
import { Field, SecretInput, TextInput } from "../components";
import { checkId, HARBOR, IdRule, SHIP, tidy, TOKEN } from "../manualIds";

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
        From the agent&apos;s install command in BlazeMeter (Settings → Private
        Locations → your agent). Nothing is sent to BlazeMeter and nothing is
        looked up — only the shape of each value is checked here.
      </p>

      <Field label="Harbor ID (private location)" required
        hint="HARBOR_ID — identifies the location the agent joins">
        {/* Whitespace is removed rather than complained about: the install
            command wraps, so a copy off it arrives with a newline in the
            middle, and that is a paste artefact rather than a typo. */}
        <TextInput mono placeholder="0a1b2c3d4e5f60718293a4b5"
          value={props.harborId} onChange={(v) => props.onHarborId(tidy(v))} />
        <Complaint rule={HARBOR} value={props.harborId} />
      </Field>

      <Field label="Ship ID (agent)" required
        hint="SHIP_ID — this agent's own identity, and part of the Deployment's selector">
        <TextInput mono placeholder="6c5b4a39281706f5e4d3c2b1"
          value={props.shipId} onChange={(v) => props.onShipId(tidy(v))} />
        <Complaint rule={SHIP} value={props.shipId} />
      </Field>

      {/* Masked: the same field the connected path now has, and the same reason
          -- see SecretInput. Left empty the bundle still generates, with the
          placeholder and a banner beside the download saying so, which is why
          this one is not marked required. */}
      <Field label="Auth token"
        hint="AUTH_TOKEN — goes into the Secret. Anyone holding it can register as this agent.">
        <SecretInput placeholder="1a2b3c4d5e6f708192a3b4c5d6e7f809…"
          value={props.authToken} onChange={(v) => props.onAuthToken(tidy(v))} />
        <Complaint rule={TOKEN} value={props.authToken} />
      </Field>
    </div>
  );
}

/** What is wrong with the field above, where its hint would be.
 *
 *  Nothing while it is blank and nothing while it is right, so the layout does
 *  not move as a correct value is typed -- only a value that has stopped being
 *  plausible pushes anything down. */
function Complaint({ rule, value }: { rule: IdRule; value: string }) {
  const msg = checkId(rule, value);
  if (!msg) return null;
  return <span className="text-[11px] text-red-600 block">{msg}</span>;
}
