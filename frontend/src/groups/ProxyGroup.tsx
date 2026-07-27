import { Field, TextInput } from "../components";

/** HTTP(S) proxy. The whole group is one option -- a `proxy` object -- so it
 *  takes that object and a setter for one field of it, rather than five props
 *  that would have to be reassembled on the way back out. */
export function ProxyGroup(props: {
  proxy: Record<string, string | undefined>;
  onField: (k: string, v: string) => void;
}) {
  const { proxy, onField } = props;
  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        <Field label="HTTP proxy">
          <TextInput mono placeholder="http://proxy:3128"
            value={String(proxy.http ?? "")}
            onChange={(v) => onField("http", v)} />
        </Field>
        <Field label="HTTPS proxy">
          <TextInput mono placeholder="http://proxy:3128"
            value={String(proxy.https ?? "")}
            onChange={(v) => onField("https", v)} />
        </Field>
        <Field label="Username" hint="optional — proxy auth">
          <TextInput mono value={String(proxy.username ?? "")}
            onChange={(v) => onField("username", v)} />
        </Field>
        <Field label="Password">
          <TextInput mono value={String(proxy.password ?? "")}
            onChange={(v) => onField("password", v)} />
        </Field>
      </div>
      <Field label="NO_PROXY">
        <TextInput mono placeholder="kubernetes.default,127.0.0.1,localhost"
          value={String(proxy.no_proxy ?? "")}
          onChange={(v) => onField("no_proxy", v)} />
      </Field>
      <p className="text-[11px] text-slate-400">
        BlazeMeter has no separate proxy-auth env vars — credentials are
        URL-encoded into the proxy URL (user:pass@host). With
        "AUTH_TOKEN in a Secret" on, the credentialed proxy URLs move
        into the Secret instead of the ConfigMap.
      </p>
    </>
  );
}
