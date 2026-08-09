{{/*
Names. The defaults are the fixed names `bzm-opl-gen generate` emits rather than
release-derived ones, so a location can move between the generated manifests and
this chart without renaming objects, and so the selectors in BlazeMeter's own
docs (-l role=role-crane) keep matching. fullnameOverride is there for anyone
who does want release-scoped names.
*/}}
{{- define "bzm-opl.name" -}}
{{- default "crane" .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bzm-opl.fullname" -}}
{{- default (include "bzm-opl.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
One name whether or not we create the account: `create` decides only whether
serviceaccount.yaml renders. Deliberately NOT the `helm create` scaffold, which
falls back to the namespace's `default` account when create is false -- that
renders, installs, runs, and silently binds crane's Role to the account every
other pod in the namespace runs as. serviceAccount.name is required instead when
create is false; see bzm-opl.validate.
*/}}
{{- define "bzm-opl.serviceAccountName" -}}
{{- default (include "bzm-opl.fullname" .) .Values.serviceAccount.name -}}
{{- end -}}

{{- define "bzm-opl.configMapName" -}}blazemeter-configmap{{- end -}}
{{- define "bzm-opl.secretName" -}}
{{- default "blazemeter-secret" .Values.existingSecret -}}
{{- end -}}
{{- define "bzm-opl.roleName" -}}role-{{ include "bzm-opl.fullname" . }}{{- end -}}
{{- define "bzm-opl.roleBindingName" -}}role-binding-{{ include "bzm-opl.fullname" . }}{{- end -}}

{{/*
Cluster-scoped names carry the namespace, unlike the rest. Two locations in two
namespaces are a normal thing to have, and a bare `cluster-role-binding-crane`
would make the second install collide with the first -- Helm refuses to adopt an
object another release owns, so it fails at install rather than quietly
repointing the first location's binding.
*/}}
{{- define "bzm-opl.clusterRoleName" -}}
{{- printf "cluster-role-%s-%s" (include "bzm-opl.fullname" .) .Release.Namespace | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "bzm-opl.clusterRoleBindingName" -}}
{{- printf "cluster-role-binding-%s-%s" (include "bzm-opl.fullname" .) .Release.Namespace | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bzm-opl.caConfigMapName" -}}
{{- if eq .Values.caBundle.mode "existing" -}}
{{- required "caBundle.existingConfigMap is required when caBundle.mode is existing" .Values.caBundle.existingConfigMap -}}
{{- else -}}
blazemeter-cacerts
{{- end -}}
{{- end -}}

{{/*
Selector labels. harbor_id/ship_id are part of the selector in the generated
manifests, so they are here too -- but selectors are immutable, which means
repointing a deployment at a different agent needs a delete, not an upgrade.
NOTES.txt says so.
*/}}
{{- define "bzm-opl.selectorLabels" -}}
role: {{ include "bzm-opl.roleName" . }}
harbor_id: {{ .Values.harborId | quote }}
ship_id: {{ .Values.shipId | quote }}
{{- end -}}

{{- define "bzm-opl.labels" -}}
{{ include "bzm-opl.selectorLabels" . }}
app.kubernetes.io/name: {{ include "bzm-opl.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/component: private-location-agent
app.kubernetes.io/part-of: blazemeter
{{- end -}}

{{/*
Images. BlazeMeter's public project is the default registry; a private registry
replaces it wholesale, and crane's own image moves with it (the generator does
the same rewrite, keeping the tag).
*/}}
{{- define "bzm-opl.dockerRegistry" -}}
{{- default "gcr.io/verdant-bulwark-278" .Values.privateRegistry | trimSuffix "/" -}}
{{- end -}}

{{- define "bzm-opl.craneImage" -}}
{{- $tag := default "latest" .Values.image.tag -}}
{{- if .Values.image.repository -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- else if .Values.privateRegistry -}}
{{- printf "%s/crane:%s" (trimSuffix "/" .Values.privateRegistry) $tag -}}
{{- else -}}
{{/* The public project keeps a `blazemeter/` segment that a mirror does not,
which is why this is not simply <registry>/crane. The root itself comes from
the one helper that carries it. */}}
{{- printf "%s/blazemeter/crane:%s" (include "bzm-opl.dockerRegistry" .) $tag -}}
{{- end -}}
{{- end -}}

{{/*
crane-hook's image and its own Role. The image follows privateRegistry like
everything else BlazeMeter ships; the Role is named for the tool that made it
rather than upstream's `test-hookrole`, so a stray one is traceable in six
months. Both names are also written into the Pod's env -- crane-hook is told
what it is called -- which is why they are helpers and not literals.
*/}}
{{- define "bzm-opl.hookRoleName" -}}bzm-cranehook{{- end -}}

{{- define "bzm-opl.hookImage" -}}
{{- printf "%s/cranehook:latest" (include "bzm-opl.dockerRegistry" .) -}}
{{- end -}}

{{/*
Engine sizing. BlazeMeter's documented footprint is the fallback. Only limits
are settable: crane stamps the engine pod's *requests* itself, and nothing this
chart can emit changes them.
*/}}
{{- define "bzm-opl.engineCpuLimit" -}}{{- default "2" .Values.engine.cpuLimit -}}{{- end -}}
{{- define "bzm-opl.engineMemoryLimit" -}}{{- default "8Gi" .Values.engine.memoryLimit -}}{{- end -}}

{{/*
A proxy URL carrying credentials (scheme://user:pass@host) must not land in a
ConfigMap. Detecting it from the URL is the whole check -- BlazeMeter has no
separate proxy-auth env vars, so userinfo in the URL is the only form
credentials can take.
*/}}
{{- define "bzm-opl.proxyHasCreds" -}}
{{- $userinfo := "^[A-Za-z][A-Za-z0-9+.-]*://[^/@]+@" -}}
{{- if or (regexMatch $userinfo (.Values.proxy.http | toString))
          (regexMatch $userinfo (.Values.proxy.https | toString)) -}}true{{- end -}}
{{- end -}}

{{/* Where the proxy URLs go: the Secret when they carry credentials and a
Secret is in play, the ConfigMap otherwise. */}}
{{- define "bzm-opl.proxyInSecret" -}}
{{- if and .Values.proxy.enabled (include "bzm-opl.proxyHasCreds" .) .Values.useSecret (not .Values.existingSecret) -}}true{{- end -}}
{{- end -}}

{{/*
Whether crane updates itself. Unset is OFF -- this chart departing from
BlazeMeter's own Kubernetes manifest, because on breaks the chart's own upgrade
path. The live evidence and the cost of the default are in values.yaml, beside
the setting somebody actually reads.
*/}}
{{- define "bzm-opl.autoUpdate" -}}
{{- if kindIs "bool" .Values.autoUpdate -}}
{{- .Values.autoUpdate | toString -}}
{{- else -}}
false
{{- end -}}
{{- end -}}

{{- define "bzm-opl.caEnabled" -}}
{{- if ne .Values.caBundle.mode "none" -}}true{{- end -}}
{{- end -}}

{{- define "bzm-opl.caPem" -}}
{{- if .Values.caBundle.pem -}}
{{- .Values.caBundle.pem -}}
{{- else if .Values.caBundle.file -}}
{{- .Files.Get .Values.caBundle.file -}}
{{- end -}}
{{- end -}}

{{/*
Preflight. Every combination rejected here fails *silently* on a cluster: the
manifests apply, the pod runs, and the agent simply never comes online -- or
comes online pulling images from somewhere you thought you had cut off. Refusing
to render is the only signal that arrives before someone has spent an afternoon
on it, so each message names the fix.
*/}}
{{- define "bzm-opl.validate" -}}
{{/*
The generator writes a marker into a value somebody left blank, so that a bundle
handed on unfinished says so instead of carrying an empty string. A marker is
<KEY> -- the option's own key in upper case -- and the test below is the shape
rather than one string, because the key differs per field and a chart may also
be installed from a bundle an older version of the generator wrote. The pattern
is generate.MARKER_PATTERN, restated here because Go templates cannot import it,
and tests/test_helm.py holds the two equal.

The API server stops a marked *name* -- no marker is a legal RFC 1123 name --
and nothing stops a marked *value*: authToken, caBundle.pem, privateRegistry
and the proxy URLs are a Secret entry, a ConfigMap entry and environment
variables, and all of them apply cleanly (measured, #230). So `helm install` is
the only thing between those and an agent that deploys and then fails, which is
why this list is the values rather than the names.

caBundle.pem is deliberately included even though `ca_bundle_slot` renders it on
purpose: a slot is a bundle waiting for a certificate, and installing one is
still installing an agent that trusts nothing extra. The flat manifests refuse
it too, and from somewhere else -- they have no install step, so the refusal
rides into the cluster as a `ca-slot-check` initContainer on the crane
Deployment (#241) and the pod stops before crane starts. Nothing of that belongs
here: this is the install step the manifests do not have.
First, and before the emptiness checks below, because
"you left this blank" is the more specific answer and the one that names the
form it was left blank on.
*/}}
{{- $blank := dict
      "authToken" .Values.authToken
      "serviceAccount.name" .Values.serviceAccount.name
      "privateRegistry" .Values.privateRegistry
      "caBundle.existingConfigMap" .Values.caBundle.existingConfigMap
      "caBundle.pem" .Values.caBundle.pem
      "proxy.http" .Values.proxy.http
      "proxy.https" .Values.proxy.https -}}
{{- range $field, $value := $blank -}}
{{- $held := trim (toString (default "" $value)) -}}
{{- if regexMatch "^<[A-Z][A-Z0-9_]*>$" $held -}}
{{/*
The marker is printed from the value rather than built from $field, and the two
are not the same string: $field is this chart's own path (serviceAccount.name)
while the marker names the generator's option (<SERVICE_ACCOUNT_NAME>). Both
belong in the message -- the path is what you set, the marker is what you grep.
*/}}
{{- fail (printf "%s was left blank when this bundle was generated and still holds %s. Set it in bzm-opl-values.yaml (or with --set-string %s=...), or re-generate the bundle with it filled in -- installing as it stands would deploy an agent that cannot work" $field $held $field) -}}
{{- end -}}
{{- end -}}
{{- if not .Values.harborId -}}
{{- fail "harborId is required -- get it from `bzm-opl-gen locations --account-name \"<account>\"` or the private location's page in the BlazeMeter UI" -}}
{{- end -}}
{{- if not .Values.shipId -}}
{{- fail "shipId is required -- a location can have several agents, and this deployment is one of them. `bzm-opl-gen locations` lists the ships per location" -}}
{{- end -}}
{{- if and (not .Values.authToken) (not .Values.existingSecret) -}}
{{- fail "authToken is required -- generate one on the private location in the BlazeMeter UI. Pass it with --set-string authToken=... rather than committing it, or create the Secret yourself and set existingSecret" -}}
{{- end -}}
{{- if and (not .Values.serviceAccount.create) (not .Values.serviceAccount.name) -}}
{{- fail "serviceAccount.name is required when serviceAccount.create is false -- with nothing creating an account, the name is the only thing saying which existing one crane runs as and which one the RoleBinding grants to. Leaving it empty would fall back to the namespace's `default` account, which installs cleanly and hands crane's permissions to every other pod in the namespace" -}}
{{- end -}}
{{- if and .Values.existingSecret (not .Values.useSecret) -}}
{{- fail "existingSecret needs useSecret: true -- with useSecret false the token is expected in the ConfigMap and the Secret is never referenced" -}}
{{- end -}}
{{- if not (has .Values.platform (list "k8s" "openshift")) -}}
{{- fail (printf "platform must be k8s or openshift, got %q" .Values.platform) -}}
{{- end -}}
{{- if not (has .Values.serviceType (list "CLUSTERIP" "NODEPORT")) -}}
{{- fail (printf "serviceType must be CLUSTERIP or NODEPORT, got %q" .Values.serviceType) -}}
{{- end -}}
{{/*
NODEPORT deliberately has no clusterRbac requirement here. It used to, on the
theory that crane read the Node object to build its advertised address; a live
performance location with namespaced RBAC only came online, created its
NodePort Service through the namespaced Role, and ran an engine to completion.
See the serviceType comment in values.yaml.
*/}}
{{- if not (has .Values.caBundle.mode (list "none" "inline" "existing" "openshiftInject")) -}}
{{- fail (printf "caBundle.mode must be one of none|inline|existing|openshiftInject, got %q" .Values.caBundle.mode) -}}
{{- end -}}
{{- if and (eq .Values.caBundle.mode "inline") (not (include "bzm-opl.caPem" .)) -}}
{{- fail "caBundle.mode is inline but neither caBundle.pem nor caBundle.file resolved to a PEM. caBundle.file is read from the chart directory -- copy the .crt in beside Chart.yaml, or use --set-file caBundle.pem=/path/to/ca.crt" -}}
{{- end -}}
{{- if and (eq .Values.caBundle.mode "openshiftInject") (ne .Values.platform "openshift") -}}
{{- fail "caBundle.mode openshiftInject requires platform: openshift -- the ConfigMap is filled in by OpenShift's cluster network operator, and on plain Kubernetes it stays empty, so crane would mount an empty trust bundle and fail every TLS handshake" -}}
{{- end -}}
{{- if and .Values.privateRegistry (not .Values.imageOverrides) -}}
{{- fail "privateRegistry is set but imageOverrides is empty -- crane resolves engine images per key, and a key it cannot find falls back to BlazeMeter's public registry without logging anything. Generate the map for this location with `bzm-opl-gen generate --private-registry <registry>` and copy IMAGE_OVERRIDES out of out/bzm_configmap.yaml" -}}
{{- end -}}
{{- if and .Values.imageOverrides (not .Values.privateRegistry) -}}
{{- fail "imageOverrides is set but privateRegistry is not -- IMAGE_OVERRIDES is only emitted alongside DOCKER_REGISTRY, so these overrides would be silently dropped" -}}
{{- end -}}
{{- end -}}
