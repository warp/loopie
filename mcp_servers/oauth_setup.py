"""One-time OAuth consent: writes a token file for GOOGLE_OAUTH_TOKEN_PATH.

Requests Google Calendar, Google Tasks, and Google Contacts (People API) scopes so one token can drive all MCP integrations.

Usage:
  GOOGLE_OAUTH_CLIENT_SECRETS_PATH=/path/to/client_secret.json \\
  GOOGLE_OAUTH_TOKEN_PATH=/path/to/token.json \\
  python -m mcp_servers.oauth_setup

Or pass paths as argv: python -m mcp_servers.oauth_setup client_secret.json token.json

If GOOGLE_OAUTH_CLIENT_SECRETS_PATH / GOOGLE_OAUTH_TOKEN_PATH are set in the repo .env, they are loaded automatically (python-dotenv).

Enable the Google Tasks API in the same GCP project as the OAuth client.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

from mcp_servers.calendar_google import CALENDAR_SCOPES
from mcp_servers.people_google import PEOPLE_SCOPES
from mcp_servers.tasks_google import TASKS_SCOPES

OAUTH_SCOPES = list(CALENDAR_SCOPES) + list(TASKS_SCOPES) + list(PEOPLE_SCOPES)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_env() -> None:
    load_dotenv(_repo_root() / ".env")


def _resolve_path(p: str) -> str:
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str(_repo_root() / path)


def main() -> None:
    _load_env()
    if len(sys.argv) >= 3:
        secrets = _resolve_path(sys.argv[1])
        out = _resolve_path(sys.argv[2])
    else:
        secrets = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRETS_PATH", "")
        out = os.environ.get("GOOGLE_OAUTH_TOKEN_PATH", "")
        if not secrets or not out:
            print(
                "Set GOOGLE_OAUTH_CLIENT_SECRETS_PATH and GOOGLE_OAUTH_TOKEN_PATH, "
                "or run: python -m mcp_servers.oauth_setup <client_secret.json> <token_out.json>",
                file=sys.stderr,
            )
            sys.exit(1)
        secrets = _resolve_path(secrets)
        out = _resolve_path(out)
    flow = InstalledAppFlow.from_client_secrets_file(secrets, OAUTH_SCOPES)
    creds = flow.run_local_server(port=0)
    with open(out, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
