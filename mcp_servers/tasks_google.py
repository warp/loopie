"""Google Tasks API v1 for MCP external_task_* tools."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import google.auth.credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcp_servers import google_auth_env as gae

TASKS_SCOPES = ("https://www.googleapis.com/auth/tasks",)

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_due_for_google_tasks(due_raw: str) -> str:
    """Tasks API expects RFC3339 with timezone; LLMs often send date-only or naive datetimes."""
    s = due_raw.strip()
    if not s:
        return s
    if _DATE_ONLY.fullmatch(s):
        return f"{s}T00:00:00.000Z"
    s2 = s.replace(" ", "T", 1) if (" " in s and "T" not in s) else s
    try:
        if s2.endswith("Z"):
            dt = datetime.fromisoformat(s2[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(s2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return s
    u = dt.astimezone(timezone.utc).replace(microsecond=0)
    return u.strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def _http_error_payload(e: HttpError) -> dict[str, Any]:
    out: dict[str, Any] = {"reason": str(e)}
    raw = getattr(e, "content", None) or b""
    if not isinstance(raw, bytes):
        return out
    try:
        data = json.loads(raw.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return out
    err = data.get("error")
    if isinstance(err, dict):
        if err.get("message"):
            out["message"] = err["message"]
        if err.get("errors"):
            out["errors"] = err["errors"]
    return out


def _load_credentials() -> google.auth.credentials.Credentials | None:
    sa_json = gae.service_account_json()
    sa_path = gae.service_account_path()
    if sa_json:
        info = json.loads(sa_json)
        return service_account.Credentials.from_service_account_info(info, scopes=TASKS_SCOPES)
    if sa_path:
        return service_account.Credentials.from_service_account_file(
            gae.resolve_env_path(sa_path), scopes=TASKS_SCOPES
        )

    tok_json = gae.oauth_token_json()
    tok_path = gae.oauth_token_path()
    if tok_json:
        return UserCredentials.from_authorized_user_info(json.loads(tok_json), TASKS_SCOPES)
    if tok_path:
        return UserCredentials.from_authorized_user_file(
            gae.resolve_env_path(tok_path), TASKS_SCOPES
        )
    return None


def _ensure_fresh(creds: google.auth.credentials.Credentials) -> google.auth.credentials.Credentials:
    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        creds.refresh(Request())
    return creds


def _tasks_service():
    creds = _load_credentials()
    if creds is None:
        return None
    creds = _ensure_fresh(creds)
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def credentials_missing_response() -> str:
    return json.dumps(
        {
            "error": "tasks_credentials_not_configured",
            "hint": (
                "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_PATH, "
                "or GOOGLE_OAUTH_TOKEN_JSON / GOOGLE_OAUTH_TOKEN_PATH (OAuth token must include Tasks scope). "
                "Run python -m mcp_servers.oauth_setup after enabling the Google Tasks API. "
                "Optional: GOOGLE_TASKS_LIST_ID for a specific list."
            ),
        },
        indent=2,
    )


def _tasklist_id(svc) -> str:
    explicit = os.environ.get("GOOGLE_TASKS_LIST_ID", "").strip()
    if explicit:
        return explicit
    try:
        resp = svc.tasklists().list(maxResults=10).execute()
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        raise RuntimeError(
            json.dumps(
                {"error": "google_tasks_api", "status": status, "reason": str(e)},
                indent=2,
            )
        ) from e
    items = resp.get("items") or []
    if not items:
        raise RuntimeError(
            json.dumps(
                {"error": "no_task_lists", "hint": "Create a list in Google Tasks or set GOOGLE_TASKS_LIST_ID."},
                indent=2,
            )
        )
    return items[0]["id"]


def _normalize_task(t: dict[str, Any]) -> dict[str, Any]:
    st = t.get("status") or "needsAction"
    out: dict[str, Any] = {
        "task_id": t.get("id", ""),
        "title": t.get("title") or "",
        "due_iso": t.get("due"),
        "status": "done" if st == "completed" else "open",
    }
    if t.get("selfLink"):
        out["self_link"] = t["selfLink"]
    return out


def create_task(title: str, due_iso: str | None = None) -> str:
    svc = _tasks_service()
    if svc is None:
        return credentials_missing_response()
    try:
        tl = _tasklist_id(svc)
    except RuntimeError as e:
        return str(e.args[0])

    due_norm: str | None = None
    if due_iso and due_iso.strip():
        due_norm = _normalize_due_for_google_tasks(due_iso)

    body: dict[str, Any] = {"title": title}
    if due_norm:
        body["due"] = due_norm

    def _insert(task_body: dict[str, Any]) -> dict[str, Any]:
        return svc.tasks().insert(tasklist=tl, body=task_body).execute()

    try:
        created = _insert(body)
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        detail = _http_error_payload(e)
        # Common failure: malformed due (e.g. date-only) — retry once without due so work isn't lost.
        if due_norm and status == 400:
            try:
                created = _insert({"title": title})
                out = _normalize_task(created)
                out["due_omitted_after_api_error"] = True
                out["tasks_api_error"] = detail
                return json.dumps(out, indent=2)
            except HttpError:
                pass
        return json.dumps({"error": "google_tasks_api", "status": status, **detail}, indent=2)
    return json.dumps(_normalize_task(created), indent=2)


def list_tasks() -> str:
    svc = _tasks_service()
    if svc is None:
        return credentials_missing_response()
    try:
        tl = _tasklist_id(svc)
    except RuntimeError as e:
        return str(e.args[0])

    try:
        resp = svc.tasks().list(tasklist=tl, showCompleted=True, showHidden=True, maxResults=100).execute()
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_tasks_api", "status": status, **_http_error_payload(e)},
            indent=2,
        )

    items = resp.get("items") or []
    normalized = [_normalize_task(t) for t in items]
    return json.dumps(normalized, indent=2)


def complete_task(task_id: str) -> str:
    svc = _tasks_service()
    if svc is None:
        return credentials_missing_response()
    try:
        tl = _tasklist_id(svc)
    except RuntimeError as e:
        return str(e.args[0])

    try:
        updated = (
            svc.tasks()
            .patch(tasklist=tl, task=task_id, body={"status": "completed"})
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_tasks_api", "status": status, **_http_error_payload(e)},
            indent=2,
        )
    return json.dumps(_normalize_task(updated), indent=2)
