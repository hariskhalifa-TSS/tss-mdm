FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MDM_DATA_DIR=/data MDM_AGENTS_DIR=/app/agents
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ /app/server/
COPY agents/ /app/agents/
COPY scripts/ /app/scripts/
RUN useradd -r -u 10001 mdm && mkdir -p /data && chown mdm /data
USER mdm
WORKDIR /app/server
EXPOSE 8000
# PORT is set by Railway/Render; defaults to 8000 elsewhere
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*' --workers ${WEB_CONCURRENCY:-1}"]
