import { Field, inputCls, TextInput } from "../components";

/** How a docker agent publishes its virtual services: the hostname it
 *  advertises, and the certificate it serves them with.
 *
 *  The peer of `SvGroup`, and the two are never on screen together -- each
 *  group's keys are the other format's ignored options, so `groupsFor` shows
 *  exactly one of them (#182).
 *
 *  Everything it writes is one of three handlers and it decides nothing itself.
 *  The two checks worth knowing about happen on the server, at generate time,
 *  and are named in the hints rather than repeated here as rules: a private key
 *  that is not PKCS#8, and a hostname the certificate does not cover, are both
 *  agents that start, report online and serve nothing a client will accept.
 */
export function SvDockerGroup(props: {
  hostname: string;
  cert: string;
  key_: string;
  onHostname: (v: string | null) => void;
  onCert: (v: string | null) => void;
  onKey: (v: string | null) => void;
}) {
  return (
    <>
      <Field label="Hostname"
        hint="HOSTNAME_OVERRIDE — endpoint URLs are built from this and a port, rather than from this host's IP address">
        {/* No placeholder resembling a name. BlazeMeter's own example value is
            `C123ABCXYZ` and nothing they publish says what shape it has to be,
            so a suggestion here would be this page inventing one. */}
        <TextInput mono value={props.hostname}
          onChange={(v) => props.onHostname(v || null)} />
      </Field>
      <Field label="Certificate (PEM)"
        hint="optional — without a pair the endpoints are plain HTTP. Checked against the hostname above when the bundle is generated">
        <textarea className={inputCls + " h-24 font-mono text-[11px]"}
          value={props.cert} spellCheck={false}
          placeholder="-----BEGIN CERTIFICATE-----"
          onChange={(e) => props.onCert(e.target.value || null)} />
      </Field>
      <Field label="Private key (PEM, PKCS#8)"
        hint="must carry -----BEGIN PRIVATE KEY----- — convert an RSA key with openssl pkcs8 -topk8 -nocrypt">
        <textarea className={inputCls + " h-24 font-mono text-[11px]"}
          value={props.key_} spellCheck={false}
          placeholder="-----BEGIN PRIVATE KEY-----"
          onChange={(e) => props.onKey(e.target.value || null)} />
      </Field>
      <p className="text-[11px] text-slate-500">
        Both are written into the bundle and mounted into the container, like
        the CA bundle — nothing here reads a path on the machine you will run
        this on. The key is a credential and is deliberately kept out of{" "}
        <code>profile.json</code>, so re-generating from a profile asks for it
        again.
      </p>
    </>
  );
}
