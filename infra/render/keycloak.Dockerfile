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

ARG PYTHON_IMAGE=python:3.13-slim
ARG KEYCLOAK_IMAGE=quay.io/keycloak/keycloak:26.4

FROM ${PYTHON_IMAGE} AS realm
WORKDIR /src
COPY infra/keycloak/anum-realm.json infra/render/keycloak_realm.py ./
RUN python keycloak_realm.py anum-realm.json anum-realm.shared.json

FROM ${KEYCLOAK_IMAGE} AS build
# Build-time options: PostgreSQL and the health endpoints, baked into an optimized server.
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true
RUN /opt/keycloak/bin/kc.sh build

FROM ${KEYCLOAK_IMAGE} AS runtime
COPY --from=build /opt/keycloak/ /opt/keycloak/
COPY --from=realm --chown=1000:0 /src/anum-realm.shared.json /opt/keycloak/data/import/anum-realm.json
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
