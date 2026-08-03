import { Check, Field, TextInput } from "../components";
import { Applies, WhyIgnored } from "../formats";

/** Private registry: the three options that redirect image pulls at a mirror.
 *
 *  The registry itself is the one thing here every format has -- a docker host
 *  in an air-gapped network needs it as much as a cluster does, and the mirror
 *  script is emitted either way. The other two are Kubernetes: the pull secret
 *  is the kubelet's credential, and the auth stubs are ConfigMap lines. */
export function RegistryGroup(props: {
  applies: Applies;
  /** The generator's own reason, for the one field whose absence needs one:
   *  "no box for the credential" reads as an omission unless the page says
   *  what does authenticate the pull, and the generator has already written
   *  that sentence for the bundle's README. */
  whyIgnored: WhyIgnored;
  registry: string;
  pullSecret: string;
  registryAuth: boolean;
  onRegistry: (v: string | null) => void;
  onPullSecret: (v: string | null) => void;
  onRegistryAuth: (v: boolean) => void;
}) {
  const secret = props.applies("pull_secret");
  const auth = props.applies("registry_auth");
  return (
    <>
      {/* Auto-update is not mentioned any more: it is off by default for every
          bundle now, not something a registry decides. */}
      <Field label="Registry" hint="sets DOCKER_REGISTRY + IMAGE_OVERRIDES, emits bzm-opl-image-mirror.sh">
        <TextInput mono value={props.registry}
          placeholder="registry.corp.com/bzm"
          onChange={(v) => props.onRegistry(v || null)} />
      </Field>
      {(secret || auth) && (
        <div className="grid grid-cols-2 gap-2">
          {secret && (
            <Field label="imagePullSecret name"
              hint="existing docker-registry Secret in the namespace; lets the kubelet pull the crane image from your registry">
              <TextInput mono value={props.pullSecret}
                onChange={(v) => props.onPullSecret(v || null)} />
            </Field>
          )}
          {auth && (
            <Check label="Registry auth env stubs"
              hint="commented DOCKER_REGISTRY_USERNAME/PASSWORD"
              checked={props.registryAuth}
              onChange={props.onRegistryAuth} />
          )}
        </div>
      )}
      {props.whyIgnored("pull_secret") && (
        <p className="text-[11px] text-slate-400">
          No image pull secret — {props.whyIgnored("pull_secret")}.
        </p>
      )}
    </>
  );
}
