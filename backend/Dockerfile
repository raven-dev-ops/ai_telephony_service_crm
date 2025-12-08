FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system deps (if needed later for Google APIs, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Copy project metadata and install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application code and utilities (seed script)
COPY app ./app
COPY seed_demo_data.py ./seed_demo_data.py

EXPOSE 8080

# Use PORT provided by Cloud Run (defaults to 8080) for health checks.
CMD ["bash", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

