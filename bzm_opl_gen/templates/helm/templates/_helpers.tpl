{{/*
Names default to the fixed names of the generated manifests, so a location can
move between the two without renaming objects. fullnameOverride makes them
release-scoped.
*/}}
{{- define "bzm-opl.name" -}}
{{- default "crane" .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bzm-opl.fullname" -}}
{{- default (include "bzm-opl.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The ServiceAccount name, used whether or not the chart creates it. There is no
fallback to the namespace's default account; see bzm-opl.validate.
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
Cluster-scoped names include the namespace, so two locations in two namespaces
do not collide.
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
Selector labels include the agent identity. Selectors are immutable, so
pointing an install at a different agent needs uninstall + install.
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
Images. A private registry replaces BlazeMeter's public one, crane's image
included.
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
{{/* The public registry keeps a blazemeter/ path segment. */}}
{{- printf "%s/blazemeter/crane:%s" (include "bzm-opl.dockerRegistry" .) $tag -}}
{{- end -}}
{{- end -}}

{{/*
crane-hook's image and Role name. Both are also passed to the hook as env.
*/}}
{{- define "bzm-opl.hookRoleName" -}}bzm-cranehook{{- end -}}

{{- define "bzm-opl.hookImage" -}}
{{- printf "%s/cranehook:latest" (include "bzm-opl.dockerRegistry" .) -}}
{{- end -}}

{{/*
Engine limits, defaulting to 2 CPU / 8Gi. Engine requests are set by crane from
the location's settings, not here.
*/}}
{{- define "bzm-opl.engineCpuLimit" -}}{{- default "2" .Values.engine.cpuLimit -}}{{- end -}}
{{- define "bzm-opl.engineMemoryLimit" -}}{{- default "8Gi" .Values.engine.memoryLimit -}}{{- end -}}

{{/*
Whether a proxy URL carries credentials (scheme://user:pass@host).
*/}}
{{- define "bzm-opl.proxyHasCreds" -}}
{{- $userinfo := "^[A-Za-z][A-Za-z0-9+.-]*://[^/@]+@" -}}
{{- if or (regexMatch $userinfo (.Values.proxy.http | toString))
          (regexMatch $userinfo (.Values.proxy.https | toString)) -}}true{{- end -}}
{{- end -}}

{{/* Proxy URLs with credentials go in the Secret when the chart creates one. */}}
{{- define "bzm-opl.proxyInSecret" -}}
{{- if and .Values.proxy.enabled (include "bzm-opl.proxyHasCreds" .) .Values.useSecret (not .Values.existingSecret) -}}true{{- end -}}
{{- end -}}

{{/*
Whether crane updates itself. Unset is false; see autoUpdate in values.yaml.
*/}}
{{- define "bzm-opl.autoUpdate" -}}
{{- if kindIs "bool" .Values.autoUpdate -}}
{{- .Values.autoUpdate | toString -}}
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Service virtualization backends, one per KUBERNETES_WEB_EXPOSE_TYPE value.
group/resources: what the Role grants. nodeportOk: whether the backend works
with serviceType NODEPORT. tlsSecretRead: whether it reads sv.tlsSecret.
*/}}
{{- define "bzm-opl.svBackends" -}}
nginx:
  group: networking.k8s.io
  resources: [ingresses]
  creates: Ingress
  nodeportOk: true
  tlsSecretRead: true
istio:
  group: networking.istio.io
  resources: [gateways, virtualservices]
  creates: Gateway + VirtualService
  nodeportOk: false
  tlsSecretRead: false
contour:
  group: projectcontour.io
  resources: [httpproxies]
  creates: HTTPProxy
  nodeportOk: false
  tlsSecretRead: true
openshift:
  group: route.openshift.io
  resources: [routes, routes/custom-host]
  creates: Route
  nodeportOk: true
  tlsSecretRead: false
{{- end -}}

{{/*
Whether this agent publishes virtual services. Empty and "none" both mean no.
*/}}
{{- define "bzm-opl.svEnabled" -}}
{{- $t := trim (toString (default "" .Values.sv.ingress)) -}}
{{- if and $t (ne $t "none") -}}true{{- end -}}
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
Refuses values that would deploy an agent that cannot work, naming the fix.
*/}}
{{- define "bzm-opl.validate" -}}
{{/*
A value of the form <KEY> is a field left blank when the bundle was generated.
Refuse it here: as a value it would otherwise apply cleanly and fail at run
time. harborId and shipId are not listed, because the API server already
rejects a marker in the Deployment's labels.
*/}}
{{- $blank := dict
      "authToken" .Values.authToken
      "serviceAccount.name" .Values.serviceAccount.name
      "privateRegistry" .Values.privateRegistry
      "caBundle.existingConfigMap" .Values.caBundle.existingConfigMap
      "caBundle.pem" .Values.caBundle.pem
      "proxy.http" .Values.proxy.http
      "proxy.https" .Values.proxy.https
      "sv.subdomain" .Values.sv.subdomain
      "sv.tlsSecret" .Values.sv.tlsSecret -}}
{{- range $field, $value := $blank -}}
{{- $held := trim (toString (default "" $value)) -}}
{{- if regexMatch "^<[A-Z][A-Z0-9_]*>$" $held -}}
{{- fail (printf "%s was left blank when this bundle was generated and still holds %s. Set it in bzm-opl-values.yaml (or with --set-string %s=...), or re-generate the bundle with it filled in" $field $held $field) -}}
{{- end -}}
{{- end -}}
{{- if not .Values.harborId -}}
{{- fail "harborId is required -- the private location's id, from its page in BlazeMeter (Settings -> Private Locations)" -}}
{{- end -}}
{{- if not .Values.shipId -}}
{{- fail "shipId is required -- the id of the agent this deployment runs, from the private location's page in BlazeMeter" -}}
{{- end -}}
{{- if and (not .Values.authToken) (not .Values.existingSecret) -}}
{{- fail "authToken is required -- pass the agent's token with --set-string authToken=..., or create the Secret yourself and set existingSecret" -}}
{{- end -}}
{{- if and (not .Values.serviceAccount.create) (not .Values.serviceAccount.name) -}}
{{- fail "serviceAccount.name is required when serviceAccount.create is false -- name the existing account crane runs as. The namespace's default account is never used, because binding crane's Role to it would grant every pod in the namespace the same permissions" -}}
{{- end -}}
{{- if and .Values.existingSecret (not .Values.useSecret) -}}
{{- fail "existingSecret needs useSecret: true -- with useSecret false the Secret is never referenced" -}}
{{- end -}}
{{- if not (has .Values.platform (list "k8s" "openshift")) -}}
{{- fail (printf "platform must be k8s or openshift, got %q" .Values.platform) -}}
{{- end -}}
{{- if not (has .Values.serviceType (list "CLUSTERIP" "NODEPORT")) -}}
{{- fail (printf "serviceType must be CLUSTERIP or NODEPORT, got %q" .Values.serviceType) -}}
{{- end -}}
{{- if not (has .Values.caBundle.mode (list "none" "inline" "existing" "openshiftInject")) -}}
{{- fail (printf "caBundle.mode must be one of none|inline|existing|openshiftInject, got %q" .Values.caBundle.mode) -}}
{{- end -}}
{{- if and (eq .Values.caBundle.mode "inline") (not (include "bzm-opl.caPem" .)) -}}
{{- fail "caBundle.mode is inline but neither caBundle.pem nor caBundle.file resolved to a PEM. caBundle.file is read from the chart directory -- copy the .crt in beside Chart.yaml, or use --set-file caBundle.pem=/path/to/ca.crt" -}}
{{- end -}}
{{- if and (eq .Values.caBundle.mode "openshiftInject") (ne .Values.platform "openshift") -}}
{{- fail "caBundle.mode openshiftInject requires platform: openshift -- only OpenShift fills the injected ConfigMap; elsewhere it stays empty and every TLS handshake fails" -}}
{{- end -}}
{{- if include "bzm-opl.svEnabled" . -}}
{{- $backends := include "bzm-opl.svBackends" . | fromYaml -}}
{{- $type := trim (toString .Values.sv.ingress) -}}
{{- $backend := index $backends $type -}}
{{- if not $backend -}}
{{- fail (printf "sv.ingress must be one of %s (or empty for a performance-only agent), got %q" (join "|" (keys $backends | sortAlpha)) $type) -}}
{{- end -}}
{{- if or (not .Values.sv.subdomain) (not .Values.sv.tlsSecret) -}}
{{- fail (printf "sv.ingress %s also requires sv.subdomain and sv.tlsSecret -- the wildcard domain endpoints are published under, and a TLS secret name, which crane requires even for HTTP virtual services" $type) -}}
{{- end -}}
{{- if and (ne .Values.serviceType "CLUSTERIP") (not $backend.nodeportOk) -}}
{{- fail (printf "sv.ingress %s requires serviceType: CLUSTERIP, got %s -- under NODEPORT the %s crane publishes points at a port the ingress cannot reach, and the endpoint does not serve" $type .Values.serviceType $backend.creates) -}}
{{- end -}}
{{- if and (eq $type "openshift") (ne .Values.platform "openshift") -}}
{{- fail (printf "sv.ingress openshift requires platform: openshift, got %q -- a Route needs an OpenShift cluster" .Values.platform) -}}
{{- end -}}
{{- if and .Values.sv.istioGateway (ne $type "istio") -}}
{{- fail (printf "sv.istioGateway is only meaningful with sv.ingress istio, not %s" $type) -}}
{{- end -}}
{{- end -}}
{{- if and .Values.privateRegistry (not .Values.imageOverrides) -}}
{{- fail "privateRegistry is set but imageOverrides is empty -- generate the values for this location with bzm-opl-gen generate --format helm --private-registry <registry>, which writes the map" -}}
{{- end -}}
{{- if and .Values.imageOverrides (not .Values.privateRegistry) -}}
{{- fail "imageOverrides is set but privateRegistry is not -- set both, or neither" -}}
{{- end -}}
{{- end -}}
