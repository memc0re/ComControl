# syntax=docker/dockerfile:1
# ComControl container image.
# Stage 1 builds the static web UI; the runtime image contains only the Python
# backend and the built files (no Node.js). Base images are pinned by digest.

FROM node:26.13.0-trixie-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.lock ./
RUN pip install --require-hashes -r requirements.lock
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip install --no-deps . && rm -rf src pyproject.toml requirements.lock
COPY --from=ui /src/frontend/build /app/static
COPY LICENSE README.md /app/

# Unprivileged user; access to the controller is granted by adding the host
# group that owns its hidraw node (see compose.yaml).
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin comcontrol \
    && mkdir /data && chown comcontrol: /data
USER comcontrol

ENV COMCONTROL_DATA_DIR=/data \
    COMCONTROL_STATIC_DIR=/app/static \
    COMCONTROL_HOST=0.0.0.0 \
    COMCONTROL_PORT=8080
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=4).status == 200 else 1)"]
CMD ["comcontrol-server"]
