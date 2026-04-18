# syntax=docker/dockerfile:1
#
# Dockerfile for calcore-server (FastAPI + WeasyPrint PDF rendering)
#
# Build:
#   docker build -t tv-calibration/calcore-server:latest .
#
# Run:
#   docker compose up
#   # or:
#   docker run --rm -p 8000:8000 \
#     -v calcore-data:/app/data \
#     -e LITELLM_ENDPOINT=http://litellm:4000 \
#     tv-calibration/calcore-server:latest

FROM python:3.12-slim AS base

# WeasyPrint needs pango, cairo, and related libs for PDF rendering
# libpango-1.0: Pango layout engine
# libcairo2: 2D graphics backend
# libgdk-pixbuf2.0: image loading for embedded images in HTML
# fonts-liberation: baseline Latin fonts for PDF text rendering
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libcairo2 \
        libffi-dev \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps in a layer that only rebuilds when requirements change
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY calcore/ calcore/
COPY calibrator/ calibrator/
COPY litellm_config.yaml ./
COPY server.py cli.py ./

# Create runtime directories
RUN mkdir -p /app/data/zro-drops /app/.sessions /app/.calibration-history

# Named volume for persisted state (see docker-compose.yml)
VOLUME ["/app/data"]

# Expose the FastAPI port
EXPOSE 8000

ENV UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["uvicorn"]
CMD ["server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
