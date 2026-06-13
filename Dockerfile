FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=serve-spa-from-railway-v9

WORKDIR /app

# Install Node.js (needed to build the React dashboard)
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps + Playwright (cached layer — only reruns if requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

# Install dashboard npm deps (cached layer — only reruns if package-lock.json changes)
COPY dashboard/package.json dashboard/package-lock.json ./dashboard/
RUN cd dashboard && npm ci

# Copy application code (separate layer — reruns on every code change, fast)
COPY . .

# Build the React dashboard so it is available as static files
RUN cd dashboard && npm run build

# Stamp build time — runs after COPY so it is never served from a stale cache layer.
# /health returns this timestamp to prove which build is live.
RUN date -u +"%Y-%m-%dT%H:%M:%SZ" > /app/.build_time && cat /app/.build_time

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
