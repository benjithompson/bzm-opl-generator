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
//
// **None of the three is required, and the empty box says what it will become.**
// A location that does not exist yet has no ids to read off anywhere, and the
// manifests are routinely what a customer's platform team has to approve before
// anybody creates one -- so a blank field is answered with its own marker rather
// than with a refusal, which is the rule the rest of this form's fields already
// keep (see placeholder.ts). What each box shows while it is empty is therefore
// the marker itself and not a sample id: it is the string that ends up in the
// bundle, so the form and the file say one thing.
import { Field, SecretInput, TextInput } from "../components";
import { blankManualIds, checkId, HARBOR, IdRule, SHIP, tidy, TOKEN }
  from "../manualIds";
import { marker, placeholderWarning } from "../placeholder";

export function ManualSource(props: {
  harborId: string;
  shipId: string;
  authToken: string;
  onHarborId: (v: string) => void;
  onShipId: (v: string) => void;
  onAuthToken: (v: string) => void;
}) {
  const blanks = blankManualIds(props.harborId, props.shipId, props.authToken);
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        From the agent&apos;s install command in BlazeMeter (Settings → Private
        Locations → your agent). Nothing is sent to BlazeMeter and nothing is
        looked up — only the shape of each value is checked here.
      </p>

      <Field label="Harbor ID (private location)"
        hint="HARBOR_ID — identifies the location the agent joins. 24 hex characters">
        {/* Whitespace is removed rather than complained about: the install
            command wraps, so a copy off it arrives with a newline in the
            middle, and that is a paste artefact rather than a typo. */}
        <TextInput mono placeholder={marker(HARBOR.key)}
          value={props.harborId} onChange={(v) => props.onHarborId(tidy(v))} />
        <Complaint rule={HARBOR} value={props.harborId} />
      </Field>

      <Field label="Ship ID (agent)"
        hint="SHIP_ID — this agent's own identity, and part of the Deployment's selector">
        <TextInput mono placeholder={marker(SHIP.key)}
          value={props.shipId} onChange={(v) => props.onShipId(tidy(v))} />
        <Complaint rule={SHIP} value={props.shipId} />
      </Field>

      {/* Masked: the same field the connected path now has, and the same reason
          -- see SecretInput. */}
      <Field label="Auth token"
        hint="AUTH_TOKEN — goes into the Secret. Anyone holding it can register as this agent.">
        <SecretInput placeholder={marker(TOKEN.key)}
          value={props.authToken} onChange={(v) => props.onAuthToken(tidy(v))} />
        <Complaint rule={TOKEN} value={props.authToken} />
      </Field>

      {/* The same sentence the configure step's blank fields get, from the same
          function: the bundle is generated, it carries the markers, and it cannot
          be applied until they are filled in. Amber rather than red, and beside
          the boxes rather than over the step, because nothing here is wrong --
          this is the deliberate state for a location BlazeMeter has not issued
          ids for yet. */}
      {blanks.length > 0 && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2
                      text-xs text-amber-800">
          {placeholderWarning(blanks)}
        </p>
      )}
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
