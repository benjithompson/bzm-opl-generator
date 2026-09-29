import { Field, inputCls, TextInput } from "../components";

/** How a docker agent publishes virtual services: the hostname it advertises
 *  and the certificate it serves. Never on screen with SvGroup (each one's keys
 *  are the other format's ignored options). The server checks the key is PKCS#8
 *  and the certificate covers the hostname; the hints say so. */
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
        {/* No sample name: BlazeMeter documents no required shape. */}
        <TextInput mono value={props.hostname}
          onChange={(v) => props.onHostname(v || null)} />
      </Field>
      <Field label="Certificate (PEM)"
        hint="optional — without a pair the endpoints are plain HTTP. Checked against the hostname above when the bundle is generated">
        <textarea className={inputCls + " h-24 font-mono text-2xs"}
          value={props.cert} spellCheck={false}
          placeholder="-----BEGIN CERTIFICATE-----"
          onChange={(e) => props.onCert(e.target.value || null)} />
      </Field>
      <Field label="Private key (PEM, PKCS#8)"
        hint="must carry -----BEGIN PRIVATE KEY----- — convert an RSA key with openssl pkcs8 -topk8 -nocrypt">
        <textarea className={inputCls + " h-24 font-mono text-2xs"}
          value={props.key_} spellCheck={false}
          placeholder="-----BEGIN PRIVATE KEY-----"
          onChange={(e) => props.onKey(e.target.value || null)} />
      </Field>
      <p className="text-2xs text-slate-500">
        Both are written into the bundle and mounted into the container, like
        the CA bundle — nothing here reads a path on the machine you will run
        this on. The key is a credential and is deliberately kept out of{" "}
        <code>profile.json</code>, so re-generating from a profile asks for it
        again.
      </p>
    </>
  );
}
