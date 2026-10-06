# ANUM web image: builds the Vite bundle and serves it from unprivileged nginx.
# The build context is the repository root (the web app uses the pnpm workspace):
#   docker build -f apps/web/Dockerfile -t anum-web .
# Runtime configuration is described in docs/infrastructure.md.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.28-alpine

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
# The API URL is compiled into the bundle. Pass the public API origin per environment.
ARG VITE_ANUM_API_URL=http://localhost:8000
ARG VITE_ANUM_TTS_URL=
ENV VITE_ANUM_API_URL=$VITE_ANUM_API_URL \
    VITE_ANUM_TTS_URL=$VITE_ANUM_TTS_URL
RUN pnpm --filter @anum/contracts build && pnpm --filter @anum/web build

FROM ${NGINX_IMAGE} AS runtime
# Origins the page may call besides itself (API, optional voice server). Space-separated,
# substituted into the Content-Security-Policy when the container starts.
ENV ANUM_CSP_CONNECT_SRC="http://localhost:8000"
COPY apps/web/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
