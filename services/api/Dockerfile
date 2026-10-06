# ANUM API image. Build context is services/api, e.g. from the repository root:
#   docker build -t anum-api services/api
# Runtime configuration is described in docs/infrastructure.md.

ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
# Direct dependencies are pinned to exact versions in pyproject.toml.
COPY pyproject.toml ./
COPY anum_api ./anum_api
RUN python -m pip install "pip==24.3.1" \
    && python -m pip install --no-compile .

FROM ${PYTHON_IMAGE} AS runtime
# The image defaults to production so it refuses development defaults (see
# anum_api/hardening.py). FORWARDED_ALLOW_IPS is read by uvicorn: only these peers may
# set X-Forwarded-For/Proto. Set it to the load balancer range in each environment.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANUM_ENVIRONMENT=production \
    FORWARDED_ALLOW_IPS=127.0.0.1
RUN groupadd --system --gid 10001 anum \
    && useradd --system --uid 10001 --gid anum --home-dir /app --shell /usr/sbin/nologin anum
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
# Alembic files let the same image run `python -m alembic upgrade head` as a migration job.
COPY alembic.ini ./
COPY migrations ./migrations
RUN mkdir -p /app/.anum /app/.anum-data \
    && chown anum:anum /app/.anum /app/.anum-data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
CMD ["uvicorn", "anum_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
