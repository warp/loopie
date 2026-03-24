# Deploying to Google Cloud Run

This project has two runnable services:

1. **ADK agent API** — multi-agent assistant (`agents/personal_assistant`).
2. **MCP stub** (optional) — calendar / external tasks / notes for tool integration (`mcp_servers/`).

## Prerequisites

- `gcloud` CLI authenticated; APIs enabled: Cloud Run, Vertex AI API, Secret Manager (if using secrets), AlloyDB or Cloud SQL as needed.
- Cloud Run service account (default compute SA) granted **Vertex AI User** (`roles/aiplatform.user`).

## 1. AlloyDB connectivity

Use a [VPC connector](https://cloud.google.com/alloydb/docs/quickstart/integrate-cloud-run) so Cloud Run can reach AlloyDB on private IP. Store the Postgres URI in Secret Manager (for example secret `DATABASE_URL`) and mount it on the agent service.

Apply schema:

```bash
psql "$DATABASE_URL" -f sql/migrations/001_init.sql
```

## 2. MCP stub on Cloud Run (recommended for production)

Build from the repository root:

```bash
docker build -f mcp_servers/Dockerfile -t gcr.io/$GOOGLE_CLOUD_PROJECT/personal-agent-mcp .
docker push gcr.io/$GOOGLE_CLOUD_PROJECT/personal-agent-mcp
gcloud run deploy personal-agent-mcp \
  --image gcr.io/$GOOGLE_CLOUD_PROJECT/personal-agent-mcp \
  --region $GOOGLE_CLOUD_REGION \
  --allow-unauthenticated
```

Note the service URL, then set the agent env var:

`MCP_SSE_URL=https://<mcp-service-url>/sse`

(Use the exact `/sse` path your server exposes; the stub uses FastMCP defaults.)

## 3. ADK agent on Cloud Run

From the repo root (with the same `google-adk` version you use locally):

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_GENAI_USE_VERTEXAI=True

adk deploy cloud_run agents/personal_assistant \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_REGION \
  --service_name=personal-assistant-adk \
  --app_name=personal_assistant \
  --session_service_uri=memory://
```

Pass extra `gcloud run deploy` flags after `--`, for example VPC and secrets:

```bash
adk deploy cloud_run agents/personal_assistant \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_REGION \
  --service_name=personal-assistant-adk \
  --session_service_uri=memory:// \
  -- \
  --vpc-connector=projects/$GOOGLE_CLOUD_PROJECT/locations/$GOOGLE_CLOUD_REGION/connectors/YOUR_CONNECTOR \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_LOCATION=$GOOGLE_CLOUD_REGION,MCP_SSE_URL=https://YOUR-MCP-RUN-URL/sse \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest
```

Adjust secret names and connector path to match your project.

## 4. Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Terminal A — MCP SSE
PYTHONPATH=. MCP_PORT=8765 python -m mcp_servers.app sse

# Terminal B — ADK Web (pick agent app "personal_assistant")
export GOOGLE_GENAI_USE_VERTEXAI=True
export GOOGLE_CLOUD_PROJECT=...
export GOOGLE_CLOUD_LOCATION=us-central1
export DATABASE_URL=postgresql://...
export MCP_SSE_URL=http://127.0.0.1:8765/sse
adk web agents --port 8080
```

If you omit `MCP_SSE_URL`, calendar and external MCP tools are disabled; database tools still work when `DATABASE_URL` is set.
