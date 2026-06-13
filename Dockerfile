FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=gemini-correct-fallback-v6

WORKDIR /app

# Install Python deps + Playwright (cached layer — only reruns if requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

# Copy application code (separate layer — reruns on every code change, fast)
COPY . .

# Stamp build time — runs after COPY so it is never served from a stale cache layer.
# /health returns this timestamp to prove which build is live.
RUN date -u +"%Y-%m-%dT%H:%M:%SZ" > /app/.build_time && cat /app/.build_time

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
