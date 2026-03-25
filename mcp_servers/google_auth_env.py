"""Shared Google API credential paths for Calendar and Tasks (same user or service account)."""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_env_path(p: str) -> str:
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str(_repo_root() / path)


def service_account_json() -> str | None:
    return os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")


def service_account_path() -> str | None:
    return os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH")


def oauth_token_json() -> str | None:
    return os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")


def oauth_token_path() -> str | None:
    return os.environ.get("GOOGLE_OAUTH_TOKEN_PATH")
