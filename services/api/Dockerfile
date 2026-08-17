# Production image for the ANUM API (anum-api). Build context is this
# directory (services/api), not the repo root:
#   docker build -f services/api/Dockerfile services/api
FROM python:3.12-slim

WORKDIR /app

# Copy only the dependency manifest first so the pip install layer is cached
# across rebuilds that only change application source.
COPY pyproject.toml ./

# Install core runtime dependencies only (no [test] extra: pytest/httpx test
# deps have no place in a production image). setuptools' package discovery
# (see pyproject.toml) needs the anum_api/ package directory to exist to
# resolve the build, so create a placeholder before installing.
#
# Installed with `-e` (the same editable style the README uses locally) so
# this layer only reinstalls when pyproject.toml changes: the editable
# install points at /app/anum_api rather than copying it into site-packages,
# so the real source COPY'd in below is what actually runs.
RUN mkdir anum_api && touch anum_api/__init__.py \
    && python -m pip install --disable-pip-version-check --no-cache-dir "pip==24.3.1" \
    && python -m pip install --no-cache-dir -e .

# Now bring in the real application source, database migrations (needed at
# runtime so the entrypoint can run `alembic upgrade head`), and Alembic's
# config file.
COPY anum_api/ ./anum_api/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh \
    && groupadd --system anum && useradd --system --gid anum --no-create-home anum \
    && chown -R anum:anum /app

USER anum

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
