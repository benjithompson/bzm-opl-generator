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

{{- define "bzm-opl.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "bzm-opl.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
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
{{- printf "gcr.io/verdant-bulwark-278/blazemeter/crane:%s" $tag -}}
{{- end -}}
{{- end -}}

{{/*
Quantity parsing. Kubernetes compares quantities server-side, but the LimitRange
ceiling has to be decided at render time -- `max` must never fall below crane's
own limits or the LimitRanger rejects the crane pod in its own namespace. These
two turn a quantity string into a number so the bigger one can be picked; the
original string is what gets emitted, so nothing is reformatted.
*/}}
{{- define "bzm-opl.cpuMillis" -}}
{{- $v := . | toString | trim -}}
{{- if hasSuffix "m" $v -}}
{{- trimSuffix "m" $v | float64 | int64 -}}
{{- else -}}
{{- mulf ($v | float64) 1000.0 | int64 -}}
{{- end -}}
{{- end -}}

{{- define "bzm-opl.memBytes" -}}
{{- $v := . | toString | trim -}}
{{- $units := dict "Ki" 1024.0 "Mi" 1048576.0 "Gi" 1073741824.0 "Ti" 1099511627776.0 "K" 1000.0 "M" 1000000.0 "G" 1000000000.0 "T" 1000000000000.0 -}}
{{- $out := "" -}}
{{- range $suffix, $mult := $units -}}
{{- if and (eq $out "") (hasSuffix $suffix $v) -}}
{{- $out = mulf (trimSuffix $suffix $v | float64) $mult | int64 | toString -}}
{{- end -}}
{{- end -}}
{{- if eq $out "" -}}{{- $v | float64 | int64 -}}{{- else -}}{{- $out -}}{{- end -}}
{{- end -}}

{{/*
Engine sizing. BlazeMeter's documented footprint is the fallback; requests
default to the limits, because an engine that requests an eighth of what it uses
is scheduled onto a node that cannot actually give it that.
*/}}
{{- define "bzm-opl.engineCpuLimit" -}}{{- default "2" .Values.engine.cpuLimit -}}{{- end -}}
{{- define "bzm-opl.engineMemoryLimit" -}}{{- default "8Gi" .Values.engine.memoryLimit -}}{{- end -}}
{{- define "bzm-opl.engineCpuRequest" -}}
{{- default (include "bzm-opl.engineCpuLimit" .) .Values.engine.cpuRequest -}}
{{- end -}}
{{- define "bzm-opl.engineMemoryRequest" -}}
{{- default (include "bzm-opl.engineMemoryLimit" .) .Values.engine.memoryRequest -}}
{{- end -}}

{{/*
The floor the namespace ceiling must clear: whichever is bigger of the engine
and crane. Below the engine, the LimitRange contradicts itself -- `default` is
the engine size, and a `default` above `max` is rejected by the API server at
apply time rather than ignored. Below crane, the crane pod is rejected in its
own namespace. Split out from the two helpers below so the validation and the
value it validates cannot compute it differently.
*/}}
{{- define "bzm-opl.limitRangeFloorCpu" -}}
{{- $engine := include "bzm-opl.engineCpuLimit" . -}}
{{- $crane := .Values.crane.resources.limits.cpu | toString -}}
{{- if gt (int64 (include "bzm-opl.cpuMillis" $engine)) (int64 (include "bzm-opl.cpuMillis" $crane)) -}}
{{- $engine -}}{{- else -}}{{- $crane -}}{{- end -}}
{{- end -}}

{{- define "bzm-opl.limitRangeFloorMemory" -}}
{{- $engine := include "bzm-opl.engineMemoryLimit" . -}}
{{- $crane := .Values.crane.resources.limits.memory | toString -}}
{{- if gt (int64 (include "bzm-opl.memBytes" $engine)) (int64 (include "bzm-opl.memBytes" $crane)) -}}
{{- $engine -}}{{- else -}}{{- $crane -}}{{- end -}}
{{- end -}}

{{- define "bzm-opl.limitRangeMaxCpu" -}}
{{- default (include "bzm-opl.limitRangeFloorCpu" .) .Values.limitRange.maxCpu -}}
{{- end -}}

{{- define "bzm-opl.limitRangeMaxMemory" -}}
{{- default (include "bzm-opl.limitRangeFloorMemory" .) .Values.limitRange.maxMemory -}}
{{- end -}}

{{/*
A proxy URL carrying credentials (scheme://user:pass@host) must not land in a
ConfigMap. Detecting it from the URL is the whole check -- BlazeMeter has no
separate proxy-auth env vars, so userinfo in the URL is the only form
credentials can take.
*/}}
{{- define "bzm-opl.proxyHasCreds" -}}
{{- $urls := list (.Values.proxy.http | toString) (.Values.proxy.https | toString) -}}
{{- $found := "" -}}
{{- range $u := $urls -}}
{{- if regexMatch "^[A-Za-z][A-Za-z0-9+.-]*://[^/@]+@" $u -}}{{- $found = "true" -}}{{- end -}}
{{- end -}}
{{- $found -}}
{{- end -}}

{{/* Where the proxy URLs go: the Secret when they carry credentials and a
Secret is in play, the ConfigMap otherwise. */}}
{{- define "bzm-opl.proxyInSecret" -}}
{{- if and .Values.proxy.enabled (include "bzm-opl.proxyHasCreds" .) .Values.useSecret (not .Values.existingSecret) -}}true{{- end -}}
{{- end -}}

{{/*
Whether crane updates itself. Unset follows the registry: a sealed location
cannot pull a newer image anyway, so auto-update is off there and on otherwise.

Worth setting explicitly when Helm manages this release. With auto-update on,
crane rewrites its own Deployment's `.image` (and `.spec.strategy.type`) as
field manager `OpenAPI-Generator`, and Helm applies server-side -- so the next
`helm upgrade` fails on a field-ownership conflict rather than a diff. Observed
on a live cluster, not inferred. Turning it off leaves Helm the only writer, at
the cost of upgrading the agent being your job.
*/}}
{{- define "bzm-opl.autoUpdate" -}}
{{- if kindIs "bool" .Values.autoUpdate -}}
{{- .Values.autoUpdate | toString -}}
{{- else if .Values.privateRegistry -}}
false
{{- else -}}
true
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
{{- if not .Values.harborId -}}
{{- fail "harborId is required -- get it from `bzm-opl-gen locations --account-name \"<ACCOUNT>\"` or the private location's page in the BlazeMeter UI" -}}
{{- end -}}
{{- if not .Values.shipId -}}
{{- fail "shipId is required -- a location can have several agents, and this deployment is one of them. `bzm-opl-gen locations` lists the ships per location" -}}
{{- end -}}
{{- if and (not .Values.authToken) (not .Values.existingSecret) -}}
{{- fail "authToken is required -- generate one on the private location in the BlazeMeter UI. Pass it with --set-string authToken=... rather than committing it, or create the Secret yourself and set existingSecret" -}}
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
{{- if and (eq .Values.serviceType "NODEPORT") (not .Values.clusterRbac) -}}
{{- fail "serviceType NODEPORT requires clusterRbac: true -- crane resolves its advertised address from the Node object, and denied it falls back to 127.0.0.1 without erroring. Prefer serviceType CLUSTERIP, which needs no cluster-scoped access" -}}
{{- end -}}
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
{{- if gt (int64 (include "bzm-opl.cpuMillis" (include "bzm-opl.engineCpuRequest" .))) (int64 (include "bzm-opl.cpuMillis" (include "bzm-opl.engineCpuLimit" .))) -}}
{{- fail (printf "engine.cpuRequest (%s) exceeds engine.cpuLimit (%s) -- Kubernetes rejects such a pod outright" (include "bzm-opl.engineCpuRequest" .) (include "bzm-opl.engineCpuLimit" .)) -}}
{{- end -}}
{{- if gt (int64 (include "bzm-opl.memBytes" (include "bzm-opl.engineMemoryRequest" .))) (int64 (include "bzm-opl.memBytes" (include "bzm-opl.engineMemoryLimit" .))) -}}
{{- fail (printf "engine.memoryRequest (%s) exceeds engine.memoryLimit (%s) -- Kubernetes rejects such a pod outright" (include "bzm-opl.engineMemoryRequest" .) (include "bzm-opl.engineMemoryLimit" .)) -}}
{{- end -}}
{{- if .Values.limitRange.enabled -}}
{{/*
Only reachable when maxCpu/maxMemory are set by hand -- unset, they derive from
this same floor. The API server catches the engine half of this too, but only at
apply time: `helm upgrade --set engine.memoryLimit=6Gi` against a max pinned at
4Gi fails mid-release with "default request value 6Gi is greater than max value
4Gi", having already applied the ConfigMap. Failing the render keeps that from
being a half-applied release.
*/}}
{{- $floorCpu := include "bzm-opl.limitRangeFloorCpu" . -}}
{{- if lt (int64 (include "bzm-opl.cpuMillis" (include "bzm-opl.limitRangeMaxCpu" .))) (int64 (include "bzm-opl.cpuMillis" $floorCpu)) -}}
{{- fail (printf "limitRange.maxCpu (%s) is below %s, the larger of the engine limit (%s) and crane's own (%s). A max under the engine makes the LimitRange contradict itself -- its `default` is the engine size, and the API server rejects a default above max. A max under crane gets the crane pod rejected in its own namespace. Leave maxCpu unset to derive it" (include "bzm-opl.limitRangeMaxCpu" .) $floorCpu (include "bzm-opl.engineCpuLimit" .) (.Values.crane.resources.limits.cpu | toString)) -}}
{{- end -}}
{{- $floorMem := include "bzm-opl.limitRangeFloorMemory" . -}}
{{- if lt (int64 (include "bzm-opl.memBytes" (include "bzm-opl.limitRangeMaxMemory" .))) (int64 (include "bzm-opl.memBytes" $floorMem)) -}}
{{- fail (printf "limitRange.maxMemory (%s) is below %s, the larger of the engine limit (%s) and crane's own (%s). A max under the engine makes the LimitRange contradict itself -- its `default` is the engine size, and the API server rejects a default above max. A max under crane gets the crane pod rejected in its own namespace. Leave maxMemory unset to derive it" (include "bzm-opl.limitRangeMaxMemory" .) $floorMem (include "bzm-opl.engineMemoryLimit" .) (.Values.crane.resources.limits.memory | toString)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
