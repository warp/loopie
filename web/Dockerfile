# Loopie Web UI (FastAPI + static). Build from repository root:
#   docker build --platform linux/amd64 -f web/Dockerfile -t gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-web .

FROM python:3.12-slim

WORKDIR /app

# Root requirements (ADK, FastAPI, agent deps); paths must match -r agents/loopie/requirements.txt
COPY requirements.txt .
COPY agents/loopie/requirements.txt agents/loopie/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
  && pip install --no-cache-dir \
    "google-api-python-client>=2.150.0" \
    "google-auth>=2.29.0" \
    "google-auth-httplib2>=0.2.0" \
    "google-auth-oauthlib>=1.2.0"

COPY web/ ./web/
COPY agents/ ./agents/
COPY mcp_servers/ ./mcp_servers/

ENV PYTHONPATH=/app
ENV PORT=8080
EXPOSE 8080

# Cloud Run sets PORT; keep a default for local runs.
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
