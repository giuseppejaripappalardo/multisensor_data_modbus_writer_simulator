# syntax=docker/dockerfile:1.6
#
# Multisensor Modbus Simulator
# Single image: web UI (FastAPI/uvicorn) + embedded Modbus TCP server.
#
#   - 8000/tcp -> Web UI + REST API
#   -  502/tcp -> Modbus TCP (configurabile via configs/runtime.yaml)
#
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UI_HOST=0.0.0.0 \
    UI_PORT=8000 \
    SIM_CONFIG_PATH=/app/configs/runtime.yaml \
    LOG_LEVEL=INFO

WORKDIR /app

# Install dependencies first (better layer caching).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY __init__.py ./
COPY catalog.py models.py modbus_server.py modbus_client.py \
     config.py main.py ./
COPY simulator ./simulator
COPY utils ./utils
COPY webui ./webui
COPY configs ./configs
COPY tests ./tests

# Run as root: the Modbus port (502) is privileged and binding it as a
# non-root user would require either CAP_NET_BIND_SERVICE on the python
# binary or `sysctls: net.ipv4.ip_unprivileged_port_start=0`. For a dev /
# simulation container, root is the simplest portable choice.

EXPOSE 8000 502

# Lightweight liveness check on the UI.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "webui"]
