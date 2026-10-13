# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-base
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates libgl1 libglu1-mesa libglib2.0-0 libgomp1 \
       libxrender1 libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

FROM python-base AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM python-base AS slicer
# Official Linux release, pinned by version and checksum. No network plugin is installed.
ADD https://github.com/bambulab/BambuStudio/releases/download/v02.08.02.61/BambuStudio_ubuntu22.04-v02.08.02.61-20260820225108.AppImage /tmp/bambu.AppImage
RUN echo '69426a59682574591590f51c913d3889baa19252d1c8e9fcae2497eb0ed6bf92  /tmp/bambu.AppImage' | sha256sum -c - \
    && chmod +x /tmp/bambu.AppImage \
    && cd /opt && /tmp/bambu.AppImage --appimage-extract > /dev/null \
    && mv squashfs-root bambu-studio && rm /tmp/bambu.AppImage
COPY backend/dreamer/profiles.py /tmp/profiles.py
RUN python /tmp/profiles.py /opt/bambu-studio/resources/profiles/BBL /opt/dreamer-profiles

FROM python-base AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Dreamer" \
      org.opencontainers.image.description="Prompt-to-CAD workbench" \
      org.opencontainers.image.revision=$VCS_REF
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DREAMER_DATA_DIR=/data \
    DREAMER_SLICER_BIN=/usr/local/bin/bambu-studio \
    DREAMER_MACHINE_PROFILE=/opt/dreamer-profiles/machine.json \
    DREAMER_PROCESS_PROFILE=/opt/dreamer-profiles/process.json \
    DREAMER_FILAMENT_PROFILE=/opt/dreamer-profiles/filament.json \
    XDG_CACHE_HOME=/tmp/dreamer-cache \
    MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libwebkit2gtk-4.1-0 libgstreamer-plugins-base1.0-0 xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 dreamer \
    && useradd --uid 10001 --gid dreamer --create-home dreamer \
    && mkdir /data && chown dreamer:dreamer /data
COPY --from=dependencies /app/.venv /app/.venv
COPY --from=slicer /opt/bambu-studio /opt/bambu-studio
COPY --from=slicer /opt/dreamer-profiles /opt/dreamer-profiles
COPY --chmod=755 scripts/bambu-studio-headless /usr/local/bin/bambu-studio
COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY backend/dreamer/ /app/backend/dreamer/
COPY scripts/resolve_dispatch.py /app/scripts/resolve_dispatch.py
USER 10001:10001
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).close()"]
CMD ["uvicorn", "dreamer.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
