# RBX Family Manager - container image
FROM python:3.12-slim AS base

# Do not write .pyc files and do not buffer stdout (so `docker logs` works live).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached between code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY . .

# Create a non-root user and the data directory used by SQLite + logs.
RUN useradd --create-home --shell /usr/sbin/nologin rbx \
    && mkdir -p /app/data \
    && chown -R rbx:rbx /app

USER rbx

# Optional health endpoint port (HEALTH_PORT).
EXPOSE 8080

# The bot is supervised by the container runtime: it restarts on crash.
CMD ["python", "bot.py"]
