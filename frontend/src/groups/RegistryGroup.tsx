import { Check, Field, TextInput } from "../components";

/** Private registry: the three options that redirect image pulls at a mirror. */
export function RegistryGroup(props: {
  registry: string;
  pullSecret: string;
  registryAuth: boolean;
  onRegistry: (v: string | null) => void;
  onPullSecret: (v: string | null) => void;
  onRegistryAuth: (v: boolean) => void;
}) {
  return (
    <>
      {/* Auto-update is not mentioned any more: it is off by default for every
          bundle now, not something a registry decides. */}
      <Field label="Registry" hint="sets DOCKER_REGISTRY + IMAGE_OVERRIDES, emits bzm-opl-image-mirror.sh">
        <TextInput mono value={props.registry}
          placeholder="registry.corp.com/bzm"
          onChange={(v) => props.onRegistry(v || null)} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="imagePullSecret name"
          hint="existing docker-registry Secret in the namespace; lets the kubelet pull the crane image from your registry">
          <TextInput mono value={props.pullSecret}
            onChange={(v) => props.onPullSecret(v || null)} />
        </Field>
        <Check label="Registry auth env stubs"
          hint="commented DOCKER_REGISTRY_USERNAME/PASSWORD"
          checked={props.registryAuth}
          onChange={props.onRegistryAuth} />
      </div>
    </>
  );
}
