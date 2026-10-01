# One image serving the website and the API on one port.
#   docker build -t reel-fact-checker .                          # text + article checks
#   docker build -t reel-fact-checker --build-arg WITH_VIDEO=true .  # adds ffmpeg + local Whisper
# Secrets are never baked in: pass them at run time (see docs/DEPLOY.md).

FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Empty API base: the page calls the API on its own origin.
ENV VITE_API_BASE_URL=""
RUN npm run build

FROM python:3.11-slim
ARG WITH_VIDEO=false
RUN if [ "$WITH_VIDEO" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*; \
    fi
WORKDIR /app/backend
COPY backend/requirements-lock.txt backend/requirements-video.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt \
 && if [ "$WITH_VIDEO" = "true" ]; then pip install --no-cache-dir -r requirements-video.txt; fi
COPY backend/ ./
COPY --from=frontend /build/dist /app/frontend
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app
ENV ENVIRONMENT=production \
    DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend \
    COOKIE_SECURE=true \
    HF_HOME=/data/models \
    PORT=8000
# No VOLUME instruction: some platforms (Railway) reject it. Mount /data with `docker run -v` or the host's volume setting.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
