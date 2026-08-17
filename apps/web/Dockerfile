# =============================================================================
# ANUM web app image.
#
# IMPORTANT - build context MUST be the monorepo root, NOT apps/web.
#
#   apps/web depends on the workspace package @anum/contracts via
#   "@anum/contracts": "workspace:*" (see apps/web/package.json and the
#   pnpm-workspace.yaml at the repo root). pnpm can only resolve workspace:*
#   dependencies when it can see the whole workspace, so this Dockerfile
#   needs the repo root (package.json, pnpm-workspace.yaml, packages/contracts,
#   apps/web) available in its build context.
#
#   Build it from the REPO ROOT like this:
#
#     docker build -f apps/web/Dockerfile -t anum-web .
#
#   NOT from inside apps/web (`docker build .` there will fail to resolve
#   @anum/contracts and/or won't have packages/contracts available at all).
# =============================================================================

# ---- build stage -----------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /workspace

# Node 22 ships corepack; use it to get the exact pnpm version this repo's
# CI uses (see .github/workflows/ci.yml: pnpm/action-setup@v4 version 9.12.3)
# so local/CI/image builds all resolve dependencies identically.
RUN corepack enable && corepack prepare pnpm@9.12.3 --activate

# Copy only what's needed to resolve and build the workspace, rather than
# `COPY . .`, so we don't drag in services/api's Python files (unrelated,
# and would bust the docker layer cache on every unrelated backend change).
# pnpm-lock.yaml* is a glob so this still works even though this repo does
# not currently commit a lockfile (see the --frozen-lockfile=false below).
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY packages/contracts packages/contracts
COPY apps/web apps/web

# This repo does not commit a pnpm-lock.yaml (see ci.yml using
# --frozen-lockfile=false), so match that here rather than requiring one.
RUN pnpm install --frozen-lockfile=false

# Vite inlines import.meta.env.VITE_* values into the bundle at BUILD time,
# not at container runtime (see apps/web/src/lib/api.ts), so the API URL has
# to be supplied as a build ARG and exported as an ENV before `vite build`
# runs. ALWAYS override this at build time, e.g.:
#   docker build -f apps/web/Dockerfile --build-arg VITE_ANUM_API_URL=https://api.example.com -t anum-web .
# Defaulted to empty rather than failing the build when omitted, but note
# apps/web/src/lib/api.ts only falls back to its localhost:8000 default via
# `?? 'http://localhost:8000'`, which does NOT catch an empty string - if
# this ARG is left unset, the bundle ships with an empty API base URL (API
# calls become same-origin relative requests), not the localhost fallback.
ARG VITE_ANUM_API_URL=""
ENV VITE_ANUM_API_URL=${VITE_ANUM_API_URL}

# Same build-time-inlining rule applies to the OIDC login config (see
# apps/web/src/lib/auth.ts). All four default to the stub-header dev flow
# (VITE_ANUM_AUTH_MODE unset/anything but "oidc") if left unset - only
# override these together, for a real Keycloak-backed deployment:
#   --build-arg VITE_ANUM_AUTH_MODE=oidc
#   --build-arg VITE_ANUM_KEYCLOAK_URL=https://your-keycloak-host
#   --build-arg VITE_ANUM_KEYCLOAK_REALM=anum
#   --build-arg VITE_ANUM_KEYCLOAK_CLIENT_ID=anum-web
ARG VITE_ANUM_AUTH_MODE=""
ARG VITE_ANUM_KEYCLOAK_URL=""
ARG VITE_ANUM_KEYCLOAK_REALM=""
ARG VITE_ANUM_KEYCLOAK_CLIENT_ID=""
ENV VITE_ANUM_AUTH_MODE=${VITE_ANUM_AUTH_MODE}
ENV VITE_ANUM_KEYCLOAK_URL=${VITE_ANUM_KEYCLOAK_URL}
ENV VITE_ANUM_KEYCLOAK_REALM=${VITE_ANUM_KEYCLOAK_REALM}
ENV VITE_ANUM_KEYCLOAK_CLIENT_ID=${VITE_ANUM_KEYCLOAK_CLIENT_ID}

# @anum/contracts must be built before apps/web, since apps/web imports its
# compiled output (see packages/contracts/package.json "main"/"exports" and
# the root package.json "build" script, which orders it the same way).
RUN pnpm --filter @anum/contracts build
RUN pnpm --filter @anum/web build

# ---- serve stage ------------------------------------------------------------
FROM nginx:alpine AS serve

COPY --from=build /workspace/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# nginx:alpine (Alpine's busybox) ships wget but not curl, so use
# `wget --spider` rather than assuming curl is present.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --spider http://127.0.0.1:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
