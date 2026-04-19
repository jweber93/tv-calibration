FROM python:3.12-slim

# Mirror production system deps exactly
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libcairo2 \
        libffi-dev \
        fonts-liberation \
        git \
        curl \
        make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Pre-bake deps into the image layer — faster container startup
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
