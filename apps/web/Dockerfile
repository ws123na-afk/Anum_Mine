# ANUM web image: builds the Vite bundle and serves it from unprivileged nginx.
# The build context is the repository root (the web app uses the pnpm workspace):
#   docker build -f apps/web/Dockerfile -t anum-web .
# Runtime configuration is described in docs/infrastructure.md.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30-alpine

FROM ${NODE_IMAGE} AS build
ENV PNPM_HOME=/pnpm \
    CI=true
ENV PATH=$PNPM_HOME:$PATH
RUN npm install --global --no-audit --no-fund pnpm@9.12.3
WORKDIR /repo
# Workspace manifests first so the dependency layer is cached across source changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY apps/desktop/package.json apps/desktop/
COPY packages/contracts/package.json packages/contracts/
# Lifecycle scripts are skipped: the bundle needs none, and it keeps the build from
# downloading native binaries (for example onnxruntime-node) it never uses.
RUN pnpm install --frozen-lockfile --ignore-scripts --filter "@anum/web..."
COPY packages/contracts packages/contracts
COPY apps/web apps/web
# The public legal pages render docs/legal at build time (docs/legal-pages.md).
COPY docs/legal docs/legal
# The API URL is compiled into the bundle. Pass the public API origin per environment.
ARG VITE_ANUM_API_URL=http://localhost:8000
ARG VITE_ANUM_TTS_URL=
# Keycloak sign-in (docs/identity.md). Set the issuer for every shared environment: when
# it is empty the bundle falls back to local sessions, which the API refuses outside local.
# Also add the issuer origin to ANUM_CSP_CONNECT_SRC at runtime (discovery and token calls).
ARG VITE_ANUM_OIDC_ISSUER=
ARG VITE_ANUM_OIDC_CLIENT_ID=anum-web
ARG VITE_ANUM_WORKSPACE_ID=
# Optional browser error reporting (docs/observability.md#error-reporting): the public DSN of a
# Sentry-compatible project (GlitchTip), https://<public key>@<host>/<project id>. Empty: off, and
# the bundle contains no reporting code. Its origin is added to the CSP's connect-src below.
ARG VITE_SENTRY_DSN=
ARG VITE_SENTRY_ENVIRONMENT=production
# Legal documents shown without the DRAFT banner (docs/legal-pages.md): comma-separated ids
# (terms, privacy, dpa, aup, subprocessors) or `all`. Empty: every document is a draft.
ARG VITE_ANUM_LEGAL_PUBLISHED=
ENV VITE_ANUM_API_URL=$VITE_ANUM_API_URL \
    VITE_ANUM_TTS_URL=$VITE_ANUM_TTS_URL \
    VITE_ANUM_OIDC_ISSUER=$VITE_ANUM_OIDC_ISSUER \
    VITE_ANUM_OIDC_CLIENT_ID=$VITE_ANUM_OIDC_CLIENT_ID \
    VITE_ANUM_WORKSPACE_ID=$VITE_ANUM_WORKSPACE_ID \
    VITE_SENTRY_DSN=$VITE_SENTRY_DSN \
    VITE_SENTRY_ENVIRONMENT=$VITE_SENTRY_ENVIRONMENT \
    VITE_ANUM_LEGAL_PUBLISHED=$VITE_ANUM_LEGAL_PUBLISHED
RUN pnpm --filter @anum/contracts build && pnpm --filter @anum/web build \
    && node --experimental-strip-types apps/web/scripts/sentry-csp-origin.ts > /repo/csp-error-reporting-origin

FROM ${NGINX_IMAGE} AS runtime
# Apply Alpine security updates published after the base image was built (image
# scanning fails CI on fixable high and critical findings), then drop back to nginx.
USER root
RUN apk upgrade --no-cache
# Origins the page may call besides itself (API, Keycloak issuer, optional voice server). Space-separated,
# substituted into the Content-Security-Policy when the container starts.
ENV ANUM_CSP_CONNECT_SRC="http://localhost:8000"
COPY apps/web/nginx/default.conf.template /tmp/default.conf.template
COPY --from=build /repo/csp-error-reporting-origin /tmp/csp-error-reporting-origin
# The error-reporting origin is fixed at build time, like the DSN in the bundle: connect-src names it
# only when VITE_SENTRY_DSN was set (the script printed a validated scheme://host[:port] or nothing).
RUN origin="$(cat /tmp/csp-error-reporting-origin)" \
    && case "$origin" in ""|http://*|https://*) ;; *) echo "bad error-reporting origin" >&2; exit 1 ;; esac \
    && mkdir -p /etc/nginx/templates \
    && sed "s|__ANUM_CSP_ERROR_REPORTING_SRC__|${origin}|" /tmp/default.conf.template > /etc/nginx/templates/default.conf.template \
    && rm /tmp/default.conf.template /tmp/csp-error-reporting-origin
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
