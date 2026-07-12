# Setuq backend engine (FastAPI). Build context = engine/.
#   docker build -t setuq-engine ./engine
#   docker run --env-file engine/.env -p 8001:8001 setuq-engine
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: build tools only while installing, then removed to keep image slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for layer caching. Includes ecs-logging (structured
# ECS logs) and redis; install valkey too so CACHE_BACKEND=valkey works in-image.
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && pip install "valkey==6.0.2"

# App source
COPY app ./app
COPY config ./config

# Optional runtime files (schema/prompt overrides) are mounted or baked as needed.
# Run as non-root.
RUN useradd --create-home --uid 10001 setuq \
    && chown -R setuq:setuq /app
USER setuq

EXPOSE 8001

# Honour LOG_LEVEL / provider / cache backend via env at run time.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
