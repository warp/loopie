# Personal agent (ADK + Vertex AI + AlloyDB + Cloud Run)

Multi-agent assistant that coordinates **schedule** (MCP calendar), **tasks** (Postgres/AlloyDB + optional external-task MCP), and **notes** (database + optional external-notes MCP).

## Layout

| Path | Purpose |
|------|---------|
| [agents/personal_assistant/](agents/personal_assistant/) | ADK app: `root_agent`, tools, specialists |
| [mcp_servers/](mcp_servers/) | Stub MCP server (SSE or stdio) for demos |
| [sql/migrations/](sql/migrations/) | AlloyDB-compatible schema |
| [deploy/CLOUD_RUN.md](deploy/CLOUD_RUN.md) | Cloud Run, VPC, secrets, `adk deploy` |

## Quick start

See [.env.example](.env.example) and [deploy/CLOUD_RUN.md](deploy/CLOUD_RUN.md). For Vertex, set **`ADK_MODEL=gemini-2.0-flash-001`** (not `gemini-2.0-flash`) and a [supported region](https://cloud.google.com/vertex-ai/generative-ai/docs/learn/locations) such as **`us-central1`** if you see publisher model 404s.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Run MCP (terminal A), set `MCP_SSE_URL`, apply `sql/migrations/001_init.sql`, set `DATABASE_URL`, then:

```bash
adk web agents
```

Deploy only the agent folder:

```bash
adk deploy cloud_run agents/personal_assistant --project=... --region=...
```

## Troubleshooting

### `ConnectError` / `Failed to create MCP session`

`MCP_SSE_URL` must reach a **running** MCP SSE server. If nothing listens on that host/port, ADK fails when loading tools. Fix: start the stub from the repo root (`PYTHONPATH=. MCP_PORT=8765 python -m mcp_servers.app sse`) and keep the URL in sync, **or** set **`MCP_DISABLED=1`** in `.env` to turn off MCP toolsets until the server is up.

Omitting `MCP_SSE_URL` also disables MCP; database tools still work when `DATABASE_URL` is set.

Set **`USER_TIMEZONE`** (IANA name, e.g. `America/Los_Angeles`) so the schedule specialist can resolve phrases like “next Wednesday” using real wall-clock context injected each turn.
