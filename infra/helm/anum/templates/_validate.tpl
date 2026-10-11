{{/*
Refuse to render configurations the API or worker would refuse at startup
(anum_api/hardening.py, anum_api/identity.py, anum_api/worker.py), plus deployment
mistakes the API cannot see (local file storage behind several replicas, missing
hosts). Failing at `helm template`/`helm upgrade` is cheaper than a crash loop.
*/}}
{{- define "anum.validate" -}}
{{- $c := .Values.config -}}
{{- $env := lower (toString $c.ANUM_ENVIRONMENT) | trim -}}
{{- if or (eq $env "") (eq $env "local") (eq $env "test") -}}
{{- fail (printf "config.ANUM_ENVIRONMENT=%q is a development environment; the chart deploys shared environments only (staging, production, ...)" $env) -}}
{{- end -}}
{{- if ne (lower (toString $c.ANUM_AUTH_MODE)) "oidc" -}}
{{- fail "config.ANUM_AUTH_MODE must be oidc outside local/test" -}}
{{- end -}}
{{- if not (hasPrefix "https://" (toString $c.ANUM_KEYCLOAK_ISSUER)) -}}
{{- fail "config.ANUM_KEYCLOAK_ISSUER must be the https issuer URL of the Keycloak realm" -}}
{{- end -}}
{{- range $key, $value := merge (dict) (default (dict) $c) (default (dict) .Values.extraEnv) -}}
{{- if has (upper (toString $key)) (list "KEYCLOAK_ADMIN" "KEYCLOAK_ADMIN_PASSWORD" "KC_BOOTSTRAP_ADMIN_USERNAME" "KC_BOOTSTRAP_ADMIN_PASSWORD") -}}
{{- fail (printf "%s is a Keycloak admin credential: ANUM never uses it, it must not be in config or extraEnv (rendered into a ConfigMap), and the API refuses the compose default admin/admin at startup" $key) -}}
{{- end -}}
{{- end -}}
{{- if not $c.ANUM_OIDC_AUDIENCE -}}
{{- fail "config.ANUM_OIDC_AUDIENCE is required" -}}
{{- end -}}
{{- $origins := fromJsonArray (toString $c.ANUM_CORS_ORIGINS) -}}
{{- if not $origins -}}
{{- fail "config.ANUM_CORS_ORIGINS must be a JSON list of the exact https web origins" -}}
{{- end -}}
{{- range $origins -}}
{{- $o := toString . -}}
{{- if or (contains "*" $o) (not (hasPrefix "https://" $o)) (regexMatch "^https://(localhost|127\\.|0\\.0\\.0\\.0|\\[::1\\]|[^/]*\\.localhost)" $o) -}}
{{- fail (printf "config.ANUM_CORS_ORIGINS entry %q must be an https origin that is not local or a wildcard" $o) -}}
{{- end -}}
{{- end -}}
{{- if ne (toString $c.ANUM_REPOSITORY_BACKEND) "postgresql" -}}
{{- fail "config.ANUM_REPOSITORY_BACKEND must be postgresql (the in-memory repository is per process and refused by the worker)" -}}
{{- end -}}
{{- if and (eq (toString $c.ANUM_EVENT_BUS) "nats") (not $c.ANUM_NATS_URL) -}}
{{- fail "config.ANUM_NATS_URL is required when ANUM_EVENT_BUS=nats" -}}
{{- end -}}
{{- if eq (toString $c.ANUM_RUNTIME_BACKEND) "temporal" -}}
{{- if not $c.ANUM_TEMPORAL_TARGET -}}
{{- fail "config.ANUM_TEMPORAL_TARGET is required when ANUM_RUNTIME_BACKEND=temporal" -}}
{{- end -}}
{{- if not .Values.worker.enabled -}}
{{- fail "worker.enabled must be true when ANUM_RUNTIME_BACKEND=temporal (nothing would execute runs)" -}}
{{- end -}}
{{- end -}}
{{- if eq (toString $c.ANUM_OBJECT_STORAGE_BACKEND) "s3" -}}
{{- if or (not $c.ANUM_S3_ENDPOINT) (not $c.ANUM_S3_BUCKET) -}}
{{- fail "config.ANUM_S3_ENDPOINT and config.ANUM_S3_BUCKET are required when ANUM_OBJECT_STORAGE_BACKEND=s3" -}}
{{- end -}}
{{- else -}}
{{- if or (gt (int .Values.api.replicas) 1) .Values.api.hpa.enabled -}}
{{- fail "ANUM_OBJECT_STORAGE_BACKEND other than s3 keeps files on one pod's disk: use s3 with more than one API replica or the HPA" -}}
{{- end -}}
{{- if eq $env "production" -}}
{{- fail "ANUM_OBJECT_STORAGE_BACKEND must be s3 in production" -}}
{{- end -}}
{{- end -}}
{{- if and (eq $env "production") (eq (toString $c.ANUM_MODEL_PROVIDER) "mock") -}}
{{- fail "ANUM_MODEL_PROVIDER=mock is a release blocker in production" -}}
{{- end -}}
{{- range $key := list "ANUM_MODEL_FAST_EFFORT" "ANUM_MODEL_CAPABLE_EFFORT" -}}
{{- if not (regexMatch "^(|low|medium|high|xhigh|max)$" (toString (index $c $key | default ""))) -}}
{{- fail (printf "config.%s must be low, medium, high, xhigh, max or empty (the API refuses other values at startup)" $key) -}}
{{- end -}}
{{- end -}}
{{- $dsn := trim (toString (default "" (coalesce (get (default (dict) .Values.extraEnv) "ANUM_SENTRY_DSN") $c.ANUM_SENTRY_DSN))) -}}
{{- if $dsn -}}
{{- if not (regexMatch "^https?://[^@/?#]+@[^/?#]+/([^?#]*/)?[0-9]+/?$" $dsn) -}}
{{- fail "config.ANUM_SENTRY_DSN must look like https://<public key>@<host>/<project id>" -}}
{{- end -}}
{{- if and (not (hasPrefix "https://" $dsn)) (or (not (regexMatch "^http://[^@/]+@[^.:/@\\[\\]]+(:[0-9]+)?/" $dsn)) (regexMatch "^http://[^@/]+@localhost(:[0-9]+)?/" $dsn)) -}}
{{- fail "config.ANUM_SENTRY_DSN must use https, or http to a service name without dots such as glitchtip (the API refuses other clear-text DSNs at startup)" -}}
{{- end -}}
{{- end -}}
{{- if ne (lower (toString $c.ANUM_RATE_LIMIT_ENABLED)) "true" -}}
{{- fail "config.ANUM_RATE_LIMIT_ENABLED must stay true outside local" -}}
{{- end -}}
{{- $retention := toString (default "30" $c.ANUM_CONVERSATION_RETENTION_DAYS) -}}
{{- if or (not (regexMatch "^[0-9]{1,4}$" $retention)) (gt (atoi $retention) 3650) -}}
{{- fail (printf "config.ANUM_CONVERSATION_RETENTION_DAYS=%q must be a whole number of days from 0 (keep forever) to 3650" $retention) -}}
{{- end -}}
{{- if not .Values.secrets.app.name -}}
{{- fail "secrets.app.name is required (an existing Secret with ANUM_SECRETS_KEY and ANUM_DATABASE_URL)" -}}
{{- end -}}
{{- if and .Values.migration.enabled (not .Values.secrets.migration.name) -}}
{{- fail "secrets.migration.name is required when migration.enabled" -}}
{{- end -}}
{{- if and .Values.web.enabled (not .Values.web.cspConnectSrc) -}}
{{- fail "web.cspConnectSrc is required: the API origin and the Keycloak issuer origin" -}}
{{- end -}}
{{- if .Values.ingress.enabled -}}
{{- if or (and .Values.web.enabled (not .Values.ingress.hosts.web)) (and .Values.api.enabled (not .Values.ingress.hosts.api)) -}}
{{- fail "ingress.hosts.web and ingress.hosts.api are required when ingress.enabled" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.backup.enabled (not .Values.backup.persistentVolumeClaim) -}}
{{- fail "backup.persistentVolumeClaim is required when backup.enabled" -}}
{{- end -}}
{{- end -}}
