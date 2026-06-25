# V13.4 Cloud Dockerfile — 毕方灵犀·天眼 云端容器
# Supports: GitHub Actions / VPS / Local Docker
FROM python:3.11-slim

LABEL maintainer="BifangLingxi <bifang.lingxi@claw.ai>"
LABEL description="V13.4 Cloud-Native Stock Screening System"

# Set environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai \
    CLOUD_MODE=docker

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget cron tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements_cloud.txt .
RUN pip install --no-cache-dir -r requirements_cloud.txt

# Copy application code
COPY cloud_pipeline.py cloud_data_fetcher.py cloud_holy_grail.py cloud_notify.py ./
COPY cloud_monitor.py ./

# Create directories
RUN mkdir -p cloud_outputs cloud_cache cloud_state logs

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import json; json.load(open('cloud_state/step_GUARDIAN.json'))" || exit 1

# Entry point: monitor daemon (for VPS) or pipeline (for one-shot)
ENTRYPOINT ["python", "cloud_monitor.py"]
