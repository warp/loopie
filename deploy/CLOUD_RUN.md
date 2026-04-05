# Deploying to Google Cloud Run

This project has three runnable services:

1. **ADK agent API** — multi-agent assistant (`agents/loopie`), via `adk deploy cloud_run`.
2. **MCP server** (optional) — Google Calendar and Google Tasks (when credentials are set), plus in-memory external notes (`mcp_servers/`).
3. **Loopie Web UI** — browser chat + contacts (`web/app.py`), via Docker image below.

## Prerequisites

- `gcloud` CLI authenticated; APIs enabled: Cloud Run, Vertex AI API, Secret Manager (if using secrets), AlloyDB or Cloud SQL as needed.
- Cloud Run service account (default compute SA) granted **Vertex AI User** (`roles/aiplatform.user`).
- When using `--set-secrets`, that same service account needs **Secret Manager Secret Accessor** (`roles/secretmanager.secretAccessor`) on each secret (or at project level). Without it, deploy fails with “Permission denied on secret” for `…-compute@developer.gserviceaccount.com`.

## 1. AlloyDB connectivity

Use a [VPC connector](https://cloud.google.com/alloydb/docs/quickstart/integrate-cloud-run) so Cloud Run can reach AlloyDB on private IP. Store the Postgres URI in Secret Manager (for example secret `DATABASE_URL`) and mount it on the agent service.

Apply schema:

```bash
psql "$DATABASE_URL" -f sql/migrations/001_init.sql
```

## 2. MCP server on Cloud Run (recommended for production)

Enable the [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com) on your project if you use real calendar tools.

**Credentials (mount as secrets on the MCP service only; treat refresh tokens like passwords):**

- **Service account:** Create a key, store JSON in Secret Manager. Set `GOOGLE_SERVICE_ACCOUNT_JSON` from the secret (or mount a file and set `GOOGLE_SERVICE_ACCOUNT_PATH`). Share the target calendar with the service account’s email, then set `GOOGLE_CALENDAR_ID` to that calendar’s id (often an email address) — `primary` is only valid for the OAuth user’s own calendar.
- **OAuth user:** Run [`mcp_servers/oauth_setup.py`](../mcp_servers/oauth_setup.py) locally once with a Desktop OAuth client `client_secret.json`, upload the resulting token JSON to Secret Manager, and set `GOOGLE_OAUTH_TOKEN_JSON` on the MCP service.

Optional: `USER_TIMEZONE` (e.g. `America/Los_Angeles`) for naive ISO times from tools.

Build from the repository root. Before the first `docker push`, configure Docker to use your gcloud credentials (otherwise push fails with “Unauthenticated request” / `uploadArtifacts` denied):

```bash
gcloud auth configure-docker
```

On Apple Silicon (or any non-amd64 host), build for Cloud Run’s default execution environment:

```bash
docker build --platform linux/amd64 -f mcp_servers/Dockerfile -t gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-mcp .
docker push gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-mcp
gcloud run deploy loopie-mcp \
  --image gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-mcp \
  --region $GOOGLE_CLOUD_REGION \
  --allow-unauthenticated \
  --set-secrets=GOOGLE_OAUTH_TOKEN_JSON=GOOGLE_OAUTH_TOKEN_JSON:latest
```

Grant the default Cloud Run runtime account access to the secret once (replace the secret id if you use a different name):

```bash
PROJECT_NUMBER=$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding GOOGLE_OAUTH_TOKEN_JSON \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

If the service uses `--service-account`, bind that account instead of the compute default.

Adjust secrets to match your auth choice (for example `GOOGLE_SERVICE_ACCOUNT_JSON` instead). If no credentials are set, Calendar and Tasks tools return a JSON error explaining required env vars.

Note the service URL, then set the agent env var:

`MCP_SSE_URL=https://<mcp-service-url>/sse`

(Use the exact `/sse` path your server exposes; FastMCP defaults apply.)

**If deploy fails with “failed to start and listen on PORT=8080”:** open the revision **Logs** link from the error. Common causes: image built for the wrong CPU (`arm64` locally vs `linux/amd64` on Cloud Run — use `--platform linux/amd64` as above), or `MCP_PORT` set on the service so the process listens on a different port than Cloud Run’s `PORT`. The server exposes `GET /health` for optional HTTP health checks.

## 3. ADK agent on Cloud Run

From the repo root (with the same `google-adk` version you use locally). **Put all `adk deploy cloud_run` flags before the agent directory**; only arguments after `--` are forwarded to `gcloud run deploy`.

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_REGION=us-central1
export GOOGLE_GENAI_USE_VERTEXAI=True

adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_REGION \
  --service_name=loopie-adk \
  --app_name=loopie \
  --session_service_uri=memory:// \
  agents/loopie
```

Pass extra `gcloud run deploy` flags after `--`, for example VPC and secrets:

```bash
adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_REGION \
  --service_name=loopie-adk \
  --session_service_uri=memory:// \
  agents/loopie \
  -- \
  --vpc-connector=projects/$GOOGLE_CLOUD_PROJECT/locations/$GOOGLE_CLOUD_REGION/connectors/YOUR_CONNECTOR \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_REGION=$GOOGLE_CLOUD_REGION,MCP_SSE_URL=https://YOUR-MCP-RUN-URL/sse \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest
```

Adjust secret names and connector path to match your project.

## 4. Loopie Web UI on Cloud Run

[`web/app.py`](../web/app.py) is a separate FastAPI app (static UI + `/api/chat`). It is **not** included in `adk deploy cloud_run`; deploy it with the same container pattern as the MCP server.

Build from the **repository root** (Apple Silicon: `--platform linux/amd64`):

```bash
docker build --platform linux/amd64 -f web/Dockerfile -t gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-web .
docker push gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-web
```

Example deploy (align env with your agent: Vertex, MCP URL, DB, optional People API credentials for `/api/contacts`):

```bash
gcloud run deploy loopie-web \
  --image gcr.io/$GOOGLE_CLOUD_PROJECT/loopie-web \
  --region $GOOGLE_CLOUD_REGION \
  --allow-unauthenticated \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_REGION=$GOOGLE_CLOUD_REGION,MCP_SSE_URL=https://YOUR-MCP-RUN-URL/sse \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest
```

Add the same **`--vpc-connector`** / **`--vpc-egress`** flags as the ADK service if `DATABASE_URL` uses a **private** AlloyDB IP. Grant the runtime service account **Secret Accessor** on any mounted secrets (see §2). Open the service URL at **`/`** for the UI.

## 5. Local development

Requires **Python 3.10+** for `google-adk`. Use an explicit binary if needed (e.g. `python3.12`).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt

# Terminal A — MCP SSE
PYTHONPATH=. MCP_PORT=8765 python -m mcp_servers.app sse

# Terminal B — ADK Web (pick agent app "loopie")
export GOOGLE_GENAI_USE_VERTEXAI=True
export GOOGLE_CLOUD_PROJECT=...
export GOOGLE_CLOUD_REGION=us-central1
export DATABASE_URL=postgresql://...
export MCP_SSE_URL=http://127.0.0.1:8765/sse
adk web agents --port 8080
```

If you omit `MCP_SSE_URL`, calendar and external MCP tools are disabled; database tools still work when `DATABASE_URL` is set.

## 6. Scalability and tenancy

- **Single-tenant today** — The solution is oriented around one primary Google identity and a default user id (`DEFAULT_USER_ID` in `.env.example`). Horizontal scale on Cloud Run adds replicas of the same configuration; it does not by itself turn the app into a multi-customer SaaS.
- **OAuth for many users** — To support multiple end users, the OAuth consent and token storage flow would need to be **extended** so each user (or tenant) has **their own refresh/access tokens** (and likely per-user routing from the agent or API into the MCP layer), instead of a single shared `GOOGLE_OAUTH_TOKEN_*` on the MCP service.
- **Notes database** — For strong isolation and growth at scale, the notes store would need **partitioning** (or an equivalent tenancy strategy: schemas per tenant, row-level tenant keys with partitioning, or separate databases) aligned with how you shard users and backup/restore.