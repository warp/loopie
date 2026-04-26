# Loopie (ADK + Vertex AI + AlloyDB + Cloud Run)

Multi-agent assistant that coordinates **schedule** (MCP calendar), **tasks** (Google Tasks MCP), and **notes** (database + optional external-notes MCP).

## Layout

| Path | Purpose |
|------|---------|
| [agents/loopie/](agents/loopie/) | ADK app: `root_agent`, tools, specialists |
| [mcp_servers/](mcp_servers/) | MCP server (Google Calendar & Tasks; in-memory external notes) over SSE or stdio |
| [web/](web/) | Browser UI + API (`uvicorn web.app:app`); Docker: [`web/Dockerfile`](web/Dockerfile) |
| [sql/migrations/](sql/migrations/) | AlloyDB-compatible schema |
| [deploy/CLOUD_RUN.md](deploy/CLOUD_RUN.md) | Cloud Run, VPC, secrets, `adk deploy`, Loopie Web image |

## Quick start

**Python 3.10+** is required (`google-adk` 1.19+ does not publish wheels for older versions). On macOS, prefer an explicit interpreter if `python3` is still 3.9, for example `python3.12 -m venv .venv`.

See [.env.example](.env.example) and [deploy/CLOUD_RUN.md](deploy/CLOUD_RUN.md). For Vertex, set **`ADK_MODEL=gemini-2.5-flash`** and a [supported region](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations) such as **`us-central1`** if you see publisher model 404s.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

Use `python -m pip` (not bare `pip3`) so installs always target the active venv.

Run MCP in terminal A:

```bash
PYTHONPATH=. python -m mcp_servers.app sse
```

Then set `MCP_SSE_URL`, apply `sql/migrations/001_init.sql`, set `DATABASE_URL`, and start the Loopie browser UI/API:

```bash
uvicorn web.app:app --reload
```

Deploy only the agent folder:

```bash
adk deploy cloud_run --project=... --region=... agents/loopie
```

## Scalability and tenancy

The app is **single-tenant** as shipped (one default user id and typically one Google OAuth token for MCP). Extending to many users requires **per-user OAuth tokens** and a **partitioned or otherwise tenant-scoped notes database**. See [deploy/CLOUD_RUN.md §5](deploy/CLOUD_RUN.md#5-scalability-and-tenancy).

## Troubleshooting

### `No matching distribution found for google-adk>=...` / “Requires-Python >=3.10”

The interpreter that ran `pip` is **older than 3.10**, or `pip3` pointed at **system** Python instead of `.venv` (watch for “Defaulting to user installation”). Recreate the venv with **Python 3.10+** and install with **`python -m pip install -r requirements.txt`** after `source .venv/bin/activate`. Check with `python -V` and `which python`.

### `ConnectError` / `Failed to create MCP session`

`MCP_SSE_URL` must reach a **running** MCP SSE server. If nothing listens on that host/port, ADK fails when loading tools. Fix: start the MCP server from the repo root (`PYTHONPATH=. MCP_PORT=8765 python -m mcp_servers.app sse`) and keep the URL in sync, **or** set **`MCP_DISABLED=1`** in `.env` to turn off MCP toolsets until the server is up.

Omitting `MCP_SSE_URL` also disables MCP; database tools still work when `DATABASE_URL` is set.

Set **`USER_TIMEZONE`** (IANA name, e.g. `America/Los_Angeles`) so the schedule specialist can resolve phrases like “next Wednesday” using real wall-clock context injected each turn.
