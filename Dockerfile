FROM python:3.12-slim

WORKDIR /app

# Install the package. Copy only what the build needs first for better caching.
COPY pyproject.toml README.md LICENSE ./
COPY argos_mcp ./argos_mcp
RUN pip install --no-cache-dir .

# In a container Argos binds 0.0.0.0; keep it host-local by publishing the port
# to 127.0.0.1 on the host (see docker-compose.yml), or front it with the Caddy
# profile. The SQLite database lives on a mounted volume.
ENV MC_HOST=0.0.0.0 \
    MC_PORT=8765 \
    MC_DB=/data/argos.db
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).status == 200 else 1)"

CMD ["argos", "serve"]
