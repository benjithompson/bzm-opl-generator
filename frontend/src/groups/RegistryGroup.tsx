import { Check, Field, TextInput } from "../components";
import { Applies, WhyIgnored } from "../formats";

/** Private registry: redirects image pulls at a mirror. The registry applies
 *  to every format; the pull secret and auth stubs are Kubernetes-only. */
export function RegistryGroup(props: {
  applies: Applies;
  /** The generator's reason a field is absent, shown for the pull secret. */
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
        <p className="text-2xs text-slate-400">
          No image pull secret — {props.whyIgnored("pull_secret")}.
        </p>
      )}
    </>
  );
}
