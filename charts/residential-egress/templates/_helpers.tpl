{{- define "resip.name" -}}
{{- printf "%s-resip" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- define "resip.labels" -}}
app.kubernetes.io/name: residential-egress
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
{{- end -}}
{{- define "resip.selector" -}}
app.kubernetes.io/name: residential-egress
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
{{- define "resip.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}
{{- define "resip.claim" -}}
{{- default (include "resip.name" .) .Values.persistence.existingClaim -}}
{{- end -}}
{{- define "resip.marzbanClaim" -}}
{{- default (printf "%s-marzban" (include "resip.name" .)) .Values.marzban.persistence.existingClaim -}}
{{- end -}}
