{{/* Chart name, truncated to the 63-character label limit. */}}
{{- define "anum.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Release-qualified name. Component suffixes are added by callers, so keep room. */}}
{{- define "anum.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 40 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 40 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "anum.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Selector labels for one component: (dict "root" $ "component" "api") */}}
{{- define "anum.selectorLabels" -}}
app.kubernetes.io/name: {{ include "anum.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Full labels for one component: (dict "root" $ "component" "api") */}}
{{- define "anum.labels" -}}
helm.sh/chart: {{ include "anum.chart" .root }}
{{ include "anum.selectorLabels" . }}
app.kubernetes.io/part-of: anum
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end -}}

{{/* Image reference: (dict "image" .Values.image.api "root" $) */}}
{{- define "anum.image" -}}
{{- $tag := .image.tag | default .root.Chart.AppVersion -}}
{{- if .image.digest -}}
{{- printf "%s@%s" .image.repository .image.digest -}}
{{- else -}}
{{- printf "%s:%s" .image.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "anum.configName" -}}
{{- printf "%s-config" (include "anum.fullname" .) -}}
{{- end -}}

{{/* ServiceAccount name for a component: (dict "root" $ "component" "api") */}}
{{- define "anum.serviceAccountName" -}}
{{- printf "%s-%s" (include "anum.fullname" .root) .component -}}
{{- end -}}

{{/* Secret-backed environment for the API, the worker and the retention job. */}}
{{- define "anum.secretEnv" -}}
{{- $app := .Values.secrets.app -}}
{{- range $app.required }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ $app.name }}
      key: {{ . }}
{{- end }}
{{- range $app.optional }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ $app.name }}
      key: {{ . }}
      optional: true
{{- end }}
{{- range $name, $value := .Values.extraEnv }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}

{{/* Writable scratch directories for read-only root filesystems (API image). */}}
{{- define "anum.apiVolumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: anum-state
  mountPath: /app/.anum
- name: anum-data
  mountPath: /app/.anum-data
{{- end -}}

{{- define "anum.apiVolumes" -}}
- name: tmp
  emptyDir:
    sizeLimit: 256Mi
- name: anum-state
  emptyDir:
    sizeLimit: 64Mi
- name: anum-data
  emptyDir:
    sizeLimit: 1Gi
{{- end -}}

{{/* Soft anti-affinity across nodes unless the values set their own affinity. */}}
{{- define "anum.affinity" -}}
{{- if .affinity -}}
{{ toYaml .affinity }}
{{- else -}}
podAntiAffinity:
  preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels:
            {{- include "anum.selectorLabels" (dict "root" .root "component" .component) | nindent 12 }}
{{- end -}}
{{- end -}}

{{/* Checksum of the shared ConfigMap so pods roll when settings change. */}}
{{- define "anum.configChecksum" -}}
{{- include (print .Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end -}}
