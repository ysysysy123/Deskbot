FROM python:3.10-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install runtime dependencies: ffmpeg, libopus, git, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    libopus-dev \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications and install
COPY requirements.txt requirements-music.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-music.txt

# Copy source code and config
COPY . .

# Ensure config.yaml and data directory exist
RUN if [ ! -f config.yaml ]; then cp config.example.yaml config.yaml; fi && \
    mkdir -p data models

EXPOSE 8000 8003 8004 8006 8010

ENTRYPOINT ["python", "run.py", "--config", "config.yaml"]
