// The harbor id, ship id and token BlazeMeter shows on an agent, typed by hand,
// for an account nobody here can reach. Only their shape is checked
// (manualIds.ts). None is required: a location that does not exist yet has no
// ids, so an empty box shows the marker the bundle will carry instead. What the
// location runs is declared on the configure step.
import { Callout, Field, SecretInput, Spinner, TextInput } from "../components";
import { blankManualIds, checkId, HARBOR, IdRule, SHIP, tidy, TOKEN }
  from "../manualIds";
import { marker, placeholderWarning } from "../placeholder";

export function ManualSource(props: {
  harborId: string;
  shipId: string;
  authToken: string;
  /** The facts for these values are being read. */
  reading: boolean;
  /** What those facts cannot tell, in the server's plain prose. */
  warnings: string[];
  onHarborId: (v: string) => void;
  onShipId: (v: string) => void;
  onAuthToken: (v: string) => void;
}) {
  const blanks = blankManualIds(props.harborId, props.shipId, props.authToken);
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        From the agent&apos;s install command in BlazeMeter (Settings → Private
        Locations → your agent). These values are not sent to BlazeMeter and
        not checked against an account; only the shape of each value is checked
        here. To choose image versions, the server reads the newest releases
        from BlazeMeter&apos;s public registry, which can take a few seconds.
      </p>

      <Field label="Harbor ID (private location)"
        hint="HARBOR_ID — identifies the location the agent joins. 24 hex characters">
        {/* Whitespace is stripped: the install command wraps when copied. */}
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

      {/* Masked, like the connected token field. */}
      <Field label="Auth token"
        hint="AUTH_TOKEN — goes into the Secret. Anyone holding it can register as this agent.">
        <SecretInput placeholder={marker(TOKEN.key)}
          value={props.authToken} onChange={(v) => props.onAuthToken(tidy(v))} />
        <Complaint rule={TOKEN} value={props.authToken} />
      </Field>

      {/* The configure step's blank-field sentence: amber, since this is the
          deliberate state before a location exists. */}
      {blanks.length > 0 && (
        <Callout tone="amber" className="text-xs">
          {placeholderWarning(blanks)}
        </Callout>
      )}

      {props.reading && (
        <p role="status" className="flex items-center gap-1.5 text-xs text-slate-500">
          <Spinner className="text-bzm" />
          reading the newest image releases from BlazeMeter&apos;s registry…
        </p>
      )}
      {/* The server's own sentences: what facts without an account cannot say. */}
      {!props.reading && props.warnings.map((w) => (
        <Callout key={w} tone="amber" className="text-xs">
          <p role="note">{w}</p>
        </Callout>
      ))}
    </div>
  );
}

/** What is wrong with the field above, in its hint's place. Nothing while it
 *  is blank or right, so a correct value moves nothing. */
function Complaint({ rule, value }: { rule: IdRule; value: string }) {
  const msg = checkId(rule, value);
  if (!msg) return null;
  return <span className="text-2xs text-red-600 block">{msg}</span>;
}
