# Keycloak for a shared ANUM environment on Render (docs/deploy-render.md).
# Build context is the repository root (keycloak.Dockerfile.dockerignore limits it):
#   docker build -f infra/render/keycloak.Dockerfile -t anum-keycloak .
#
# The realm is the development realm from infra/keycloak/anum-realm.json with the
# seeded user removed and the web client's URLs set to ${ANUM_WEB_ORIGIN}
# (infra/render/keycloak_realm.py). No credential is baked in: the database
# connection (KC_DB_URL_HOST/PORT/DATABASE, KC_DB_USERNAME/PASSWORD), the public
# URL (KC_HOSTNAME) and the one-time bootstrap admin (KC_BOOTSTRAP_ADMIN_USERNAME/
# PASSWORD) come from the service's environment at runtime.
#
# The ANUM login theme (infra/keycloak/themes/anum: live password rules on the sign-up
# and change-password pages) is always installed. "Sign in with Apple" needs a Keycloak
# provider extension Keycloak does not ship (Apple answers with form_post and wants a
# client secret signed with the team's key): set APPLE_PROVIDER_URL to the extension's
# release jar and APPLE_PROVIDER_SHA256 to its SHA-256 (docs/identity.md#apple). The
# build refuses a URL without a checksum, or a jar that does not match it. Both empty:
# no Apple provider, and social_login.py refuses to configure one.

ARG PYTHON_IMAGE=python:3.13-slim
ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:26.4

FROM ${PYTHON_IMAGE} AS providers
ARG APPLE_PROVIDER_URL=""
ARG APPLE_PROVIDER_SHA256=""
RUN mkdir -p /providers \
 && if [ -n "$APPLE_PROVIDER_URL" ]; then \
      case "$APPLE_PROVIDER_URL" in https://*) ;; *) echo "APPLE_PROVIDER_URL must be https" >&2; exit 1 ;; esac; \
      [ -n "$APPLE_PROVIDER_SHA256" ] || { echo "APPLE_PROVIDER_SHA256 is required with APPLE_PROVIDER_URL" >&2; exit 1; }; \
      python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "$APPLE_PROVIDER_URL" /providers/apple-identity-provider.jar; \
      echo "$APPLE_PROVIDER_SHA256  /providers/apple-identity-provider.jar" | sha256sum -c -; \
    fi

FROM ${PYTHON_IMAGE} AS realm
WORKDIR /src
COPY infra/keycloak/anum-realm.json infra/render/keycloak_realm.py ./
RUN python keycloak_realm.py anum-realm.json anum-realm.shared.json

FROM ${KEYCLOAK_IMAGE} AS build
# Providers must be present when the optimized server is built.
COPY --from=providers --chown=1000:0 /providers/ /opt/keycloak/providers/
# Build-time options: PostgreSQL and the health endpoints, baked into an optimized server.
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true
RUN /opt/keycloak/bin/kc.sh build

FROM ${KEYCLOAK_IMAGE} AS runtime
COPY --from=build /opt/keycloak/ /opt/keycloak/
COPY --from=realm --chown=1000:0 /src/anum-realm.shared.json /opt/keycloak/data/import/anum-realm.json
COPY --chown=1000:0 infra/keycloak/themes/anum /opt/keycloak/themes/anum
# TLS ends at Render's edge: Keycloak serves plain HTTP on 8080 behind it, trusts the
# X-Forwarded-* headers for scheme and client address, and builds every URL from
# KC_HOSTNAME (the public https URL), so the issuer never depends on request headers.
ENV KC_DB=postgres \
    KC_HTTP_ENABLED=true \
    KC_HTTP_PORT=8080 \
    KC_PROXY_HEADERS=xforwarded
USER 1000
EXPOSE 8080
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
# --import-realm creates the anum realm on first start and skips it once it exists.
CMD ["start", "--optimized", "--import-realm"]
