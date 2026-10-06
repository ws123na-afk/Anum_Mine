# ANUM backup image: infra/backup/anum_backup.py with psycopg and the PostgreSQL client
# tools, for the chart's optional backup CronJob (infra/helm/anum, docs/deployment.md).
# Build context is infra/backup:
#   docker build -t anum-backup infra/backup
#
# The runtime is the official PostgreSQL image (pg_dump/pg_restore and every library
# they link), with the Python runtime from the official Python image of the same
# Debian release copied in. pg_dump must be at least as new as the server: PG_IMAGE's
# major (17) dumps 16 and 17 servers. Keep both images on the same Debian release.

ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG PG_IMAGE=postgres:17-trixie

FROM ${PYTHON_IMAGE} AS python
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
# Same psycopg pin as services/api/pyproject.toml; pip itself is removed afterwards.
RUN python -m pip install --root-user-action=ignore "psycopg[binary]==3.2.3" \
    && python -m pip uninstall --yes --root-user-action=ignore pip

FROM ${PG_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=python /usr/local/bin/python3.13 /usr/local/bin/python3.13
COPY --from=python /usr/local/lib/python3.13 /usr/local/lib/python3.13
COPY --from=python /usr/local/lib/libpython3.13.so.1.0 /usr/local/lib/libpython3.13.so.1.0
RUN ln -s python3.13 /usr/local/bin/python \
    && ldconfig \
    && python -c "import psycopg, hashlib, json, ssl; print(psycopg.__version__)" \
    && groupadd --system --gid 10001 anum \
    && useradd --system --uid 10001 --gid anum --home-dir /backup --shell /usr/sbin/nologin anum \
    && mkdir -p /backup && chown anum:anum /backup
WORKDIR /backup
COPY anum_backup.py /usr/local/bin/anum_backup.py
USER 10001:10001
ENTRYPOINT ["python", "/usr/local/bin/anum_backup.py"]
CMD ["--help"]
