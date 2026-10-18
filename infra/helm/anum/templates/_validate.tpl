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
{{- if and (eq (upper (toString $key)) "ANUM_FINANCE_WIDER_LIVE_ENABLED") (has (lower (trim (toString $value))) (list "true" "1" "yes" "on" "y" "t")) -}}
{{- fail "ANUM_FINANCE_WIDER_LIVE_ENABLED must stay false: it requires ANUM_FINANCE_LIVE_ENABLED, which is refused while no live executor exists (docs/finance.md#wider-live). The API refuses it at startup" -}}
{{- end -}}
{{- if and (eq (upper (toString $key)) "ANUM_FINANCE_LIVE_ENABLED") (has (lower (trim (toString $value))) (list "true" "1" "yes" "on" "y" "t")) -}}
{{- fail "ANUM_FINANCE_LIVE_ENABLED must stay false: ANUM has no live executor and no write-scoped connector, so live finance execution cannot be enabled (docs/finance.md#limited-live). The API refuses it at startup" -}}
{{- end -}}
{{- if and (eq (upper (toString $key)) "ANUM_FCM_SERVICE_ACCOUNT_JSON") (trim (toString (default "" $value))) -}}
{{- fail "ANUM_FCM_SERVICE_ACCOUNT_JSON carries the Firebase service account's private key: put it in the secrets.app Secret (listed in secrets.app.optional), never in config or extraEnv (rendered into a ConfigMap). The API refuses a value that is not a service-account JSON key at startup" -}}
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
{{- include "anum.validateModules" . -}}
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

{{/*
WhatsApp, email and learning settings (anum_api/whatsapp.py WhatsAppSettings,
anum_api/email_inbox.py EmailSettings, anum_api/settings.py learning_*). The API reads
them with pydantic at import time and refuses to start on a value outside these bounds,
so the chart refuses the same values. OAuth client secrets belong in secrets.app only.
*/}}
{{- define "anum.validateModules" -}}
{{- $c := .Values.config -}}
{{- $all := merge (dict) (default (dict) .Values.extraEnv) (default (dict) $c) -}}
{{- range $key, $value := $all -}}
{{- if and (regexMatch "^ANUM_EMAIL_(GOOGLE|MICROSOFT|ZOHO)_CLIENT_SECRET$" (upper (toString $key))) (trim (toString (default "" $value))) -}}
{{- fail (printf "%s is an OAuth client secret: put it in the secrets.app Secret (listed in secrets.app.optional), never in config or extraEnv (rendered into a ConfigMap)" $key) -}}
{{- end -}}
{{- end -}}
{{- $ints := list
  (list "ANUM_WHATSAPP_WEBHOOK_MAX_BODY_BYTES" 1024 1048576)
  (list "ANUM_WHATSAPP_MEDIA_MAX_BYTES" 1024 104857600)
  (list "ANUM_WHATSAPP_REPLAY_WINDOW_HOURS" 1 720)
  (list "ANUM_WHATSAPP_WEBHOOK_BURST" 1 10000)
  (list "ANUM_WHATSAPP_MESSAGE_RETENTION_DAYS" 0 3650)
  (list "ANUM_EMAIL_INITIAL_SYNC_DAYS" 1 365)
  (list "ANUM_EMAIL_SYNC_MAX_MESSAGES" 10 2000)
  (list "ANUM_EMAIL_BODY_MAX_BYTES" 4096 4194304)
  (list "ANUM_EMAIL_ATTACHMENT_MAX_BYTES" 1024 52428800)
  (list "ANUM_EMAIL_OAUTH_STATE_TTL_SECONDS" 60 3600)
  (list "ANUM_LEARNING_SIGNAL_RETENTION_DAYS" 7 3650) -}}
{{- range $ints -}}
{{- $key := index . 0 -}}
{{- $raw := trim (toString (get $all $key | default "")) -}}
{{- if $raw -}}
{{- if or (not (regexMatch "^[0-9]{1,10}$" $raw)) (lt (atoi $raw) (index . 1)) (gt (atoi $raw) (index . 2)) -}}
{{- fail (printf "config.%s=%q must be a whole number from %d to %d (the API refuses other values at startup)" $key $raw (index . 1) (index . 2)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $floats := list
  (list "ANUM_WHATSAPP_WEBHOOK_RATE_PER_SECOND" "0" "1000" false)
  (list "ANUM_WHATSAPP_TEMPLATE_CACHE_SECONDS" "0" "86400" true)
  (list "ANUM_EMAIL_SYNC_INTERVAL_SECONDS" "0" "86400" true) -}}
{{- range $floats -}}
{{- $key := index . 0 -}}
{{- $raw := trim (toString (get $all $key | default "")) -}}
{{- if $raw -}}
{{- $low := float64 (index . 1) -}}
{{- $bad := or (not (regexMatch "^[0-9]{1,6}(\\.[0-9]+)?$" $raw)) (gt (float64 $raw) (float64 (index . 2))) -}}
{{- if not $bad -}}
{{- $bad = ternary (lt (float64 $raw) $low) (le (float64 $raw) $low) (index . 3) -}}
{{- end -}}
{{- if $bad -}}
{{- fail (printf "config.%s=%q must be a number from %s%s to %s (the API refuses other values at startup)" $key $raw (ternary "" "above " (index . 3)) (index . 1) (index . 2)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $key := list "ANUM_WHATSAPP_TRIAGE_ON_ARRIVAL" "ANUM_LEARNING_ENABLED" -}}
{{- $raw := lower (trim (toString (get $all $key | default ""))) -}}
{{- if and $raw (not (has $raw (list "true" "false" "1" "0" "yes" "no" "on" "off"))) -}}
{{- fail (printf "config.%s=%q must be true or false" $key $raw) -}}
{{- end -}}
{{- end -}}
{{- $version := trim (toString (get $all "ANUM_WHATSAPP_GRAPH_API_VERSION" | default "")) -}}
{{- if and $version (not (regexMatch "^v[0-9]{1,3}\\.[0-9]{1,2}$" $version)) -}}
{{- fail (printf "config.ANUM_WHATSAPP_GRAPH_API_VERSION=%q must look like v23.0 (the API refuses other values at startup)" $version) -}}
{{- end -}}
{{- $graph := trim (toString (get $all "ANUM_WHATSAPP_GRAPH_API_URL" | default "")) -}}
{{- if and $graph (not (regexMatch "^https://[^/\\s]+" $graph)) -}}
{{- fail "config.ANUM_WHATSAPP_GRAPH_API_URL must be an https URL (the API turns WhatsApp off otherwise)" -}}
{{- end -}}
{{- $tenant := trim (toString (get $all "ANUM_EMAIL_MICROSOFT_TENANT" | default "")) -}}
{{- if and $tenant (not (regexMatch "^[A-Za-z0-9.-]{1,100}$" $tenant)) -}}
{{- fail (printf "config.ANUM_EMAIL_MICROSOFT_TENANT=%q must be common, organizations, consumers or a tenant id (the API refuses other values at startup)" $tenant) -}}
{{- end -}}
{{- $zoho := trim (toString (get $all "ANUM_EMAIL_ZOHO_DATA_CENTRE" | default "")) -}}
{{- if and $zoho (not (has $zoho (list "com" "eu" "in" "com.au" "jp" "ca" "sa" "com.cn"))) -}}
{{- fail (printf "config.ANUM_EMAIL_ZOHO_DATA_CENTRE=%q must be one of com, eu, in, com.au, jp, ca, sa, com.cn" $zoho) -}}
{{- end -}}
{{- /* Email Center (docs/email.md#email-center): the API's startup refusals, mirrored. */ -}}
{{- $mailOn := lower (trim (toString (get $all "ANUM_MAILSERVER_ENABLED" | default "false"))) -}}
{{- if and $mailOn (not (has $mailOn (list "true" "false" "1" "0" "yes" "no" "on" "off"))) -}}
{{- fail (printf "config.ANUM_MAILSERVER_ENABLED=%q must be true or false" $mailOn) -}}
{{- end -}}
{{- if has $mailOn (list "true" "1" "yes" "on") -}}
{{- range $key := list "ANUM_MAILSERVER_IMAP_HOST" "ANUM_MAILSERVER_SMTP_HOST" -}}
{{- $host := lower (trim (toString (get $all $key | default ""))) -}}
{{- if not $host -}}
{{- fail (printf "config.%s is required when ANUM_MAILSERVER_ENABLED=true" $key) -}}
{{- end -}}
{{- if or (not (contains "." $host)) (regexMatch "^[0-9.]+$" $host) (contains ":" $host) -}}
{{- fail (printf "config.%s must be the mail server's DNS name (its TLS certificate name), not %q" $key $host) -}}
{{- end -}}
{{- end -}}
{{- $smtp := lower (trim (toString (get $all "ANUM_MAILSERVER_SMTP_HOST" | default ""))) -}}
{{- range $relay := list "brevo.com" "sendinblue.com" "mailjet.com" "sendgrid.net" "mailgun.org" "amazonaws.com" -}}
{{- if or (eq $smtp $relay) (hasSuffix (printf ".%s" $relay) $smtp) -}}
{{- fail "config.ANUM_MAILSERVER_SMTP_HOST must be your own mail server, never a relay service" -}}
{{- end -}}
{{- end -}}
{{- if not (has (trim (toString (get $all "ANUM_MAILSERVER_SMTP_SECURITY" | default "starttls"))) (list "starttls" "tls")) -}}
{{- fail "config.ANUM_MAILSERVER_SMTP_SECURITY must be starttls or tls outside local" -}}
{{- end -}}
{{- if not (trim (toString (get $all "ANUM_MAILSERVER_ALLOWED_DOMAINS" | default ""))) -}}
{{- fail "config.ANUM_MAILSERVER_ALLOWED_DOMAINS must list at least one mailbox domain" -}}
{{- end -}}
{{- if not (trim (toString (get $all "ANUM_MAILSERVER_ALLOWED_TENANTS" | default ""))) -}}
{{- fail "config.ANUM_MAILSERVER_ALLOWED_TENANTS must list the organisations allowed to use the mail server" -}}
{{- end -}}
{{- end -}}
{{- range $key := list "ANUM_EMAIL_PUBLIC_API_URL" "ANUM_EMAIL_WEB_URL" -}}
{{- $url := trim (toString (get $all $key | default "")) -}}
{{- if and $url (not (regexMatch "^https://[^/\\s]+" $url)) -}}
{{- fail (printf "config.%s must be an https URL (mail providers refuse other OAuth redirects outside localhost)" $key) -}}
{{- end -}}
{{- end -}}
{{- /* Upload malware scanning (docs/files.md#malware-scanning): the API's refusals, mirrored. */ -}}
{{- $scanner := lower (trim (toString (get $all "ANUM_FILE_SCANNER" | default "off"))) -}}
{{- if not (has $scanner (list "off" "clamav")) -}}
{{- fail (printf "config.ANUM_FILE_SCANNER=%q must be off or clamav (the API refuses other values at startup)" $scanner) -}}
{{- end -}}
{{- $scanRequired := lower (trim (toString (get $all "ANUM_FILE_SCAN_REQUIRED" | default "true"))) -}}
{{- if not (has $scanRequired (list "true" "false" "1" "0" "yes" "no" "on" "off")) -}}
{{- fail (printf "config.ANUM_FILE_SCAN_REQUIRED=%q must be true or false" $scanRequired) -}}
{{- end -}}
{{- $clamPort := trim (toString (get $all "ANUM_CLAMAV_PORT" | default "")) -}}
{{- if $clamPort -}}
{{- if or (not (regexMatch "^[0-9]{1,5}$" $clamPort)) (lt (atoi $clamPort) 1) (gt (atoi $clamPort) 65535) -}}
{{- fail (printf "config.ANUM_CLAMAV_PORT=%q must be a port from 1 to 65535 (the API refuses other values at startup)" $clamPort) -}}
{{- end -}}
{{- end -}}
{{- $clamTimeout := trim (toString (get $all "ANUM_CLAMAV_TIMEOUT_SECONDS" | default "")) -}}
{{- if $clamTimeout -}}
{{- $badTimeout := not (regexMatch "^[0-9]{1,3}(\\.[0-9]+)?$" $clamTimeout) -}}
{{- if not $badTimeout -}}
{{- $badTimeout = or (le (float64 $clamTimeout) 0.0) (gt (float64 $clamTimeout) 120.0) -}}
{{- end -}}
{{- if $badTimeout -}}
{{- fail (printf "config.ANUM_CLAMAV_TIMEOUT_SECONDS=%q must be a number above 0 to 120 (the API refuses other values at startup)" $clamTimeout) -}}
{{- end -}}
{{- end -}}
{{- if eq $scanner "clamav" -}}
{{- $clamHost := trim (toString (get $all "ANUM_CLAMAV_HOST" | default "")) -}}
{{- if not $clamHost -}}
{{- fail "config.ANUM_CLAMAV_HOST is required when ANUM_FILE_SCANNER=clamav (the clamd Service name, e.g. clamav.clamav.svc)" -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9.-]{1,253}$" $clamHost) -}}
{{- fail (printf "config.ANUM_CLAMAV_HOST=%q must be a host name or IP address, without a scheme or port" $clamHost) -}}
{{- end -}}
{{- if has $scanRequired (list "false" "0" "no" "off") -}}
{{- fail "config.ANUM_FILE_SCAN_REQUIRED must stay true outside local when ANUM_FILE_SCANNER=clamav (uploads would be stored unscanned while clamd is down); the API refuses it at startup" -}}
{{- end -}}
{{- end -}}
{{- end -}}
