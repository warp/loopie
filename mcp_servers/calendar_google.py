"""Google Calendar API v3 for MCP calendar tools."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import google.auth.credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from zoneinfo import ZoneInfo

from mcp_servers import google_auth_env as gae

CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar",)


def _user_timezone() -> str:
    return os.environ.get("USER_TIMEZONE", "UTC")


def _calendar_id() -> str:
    return os.environ.get("GOOGLE_CALENDAR_ID", "primary")


def _parse_iso_datetime(s: str) -> datetime:
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(_user_timezone()))
    return dt


def _to_rfc3339(dt: datetime) -> str:
    return dt.isoformat()


def _load_credentials() -> google.auth.credentials.Credentials | None:
    sa_json = gae.service_account_json()
    sa_path = gae.service_account_path()
    if sa_json:
        info = json.loads(sa_json)
        return service_account.Credentials.from_service_account_info(
            info, scopes=CALENDAR_SCOPES
        )
    if sa_path:
        return service_account.Credentials.from_service_account_file(
            gae.resolve_env_path(sa_path), scopes=CALENDAR_SCOPES
        )

    tok_json = gae.oauth_token_json()
    tok_path = gae.oauth_token_path()
    if tok_json:
        return UserCredentials.from_authorized_user_info(json.loads(tok_json), CALENDAR_SCOPES)
    if tok_path:
        return UserCredentials.from_authorized_user_file(
            gae.resolve_env_path(tok_path), CALENDAR_SCOPES
        )
    return None


def _ensure_fresh(creds: google.auth.credentials.Credentials) -> google.auth.credentials.Credentials:
    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        creds.refresh(Request())
    return creds


def _calendar_service():
    creds = _load_credentials()
    if creds is None:
        return None
    creds = _ensure_fresh(creds)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def credentials_missing_response() -> str:
    return json.dumps(
        {
            "error": "calendar_credentials_not_configured",
            "hint": (
                "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_PATH, "
                "or GOOGLE_OAUTH_TOKEN_JSON / GOOGLE_OAUTH_TOKEN_PATH (OAuth token with Calendar scope). "
                "For service accounts, share the target calendar with the SA email and set GOOGLE_CALENDAR_ID."
            ),
        },
        indent=2,
    )


def _normalize_time_field(field: dict[str, Any] | None) -> str:
    if not field:
        return ""
    if "dateTime" in field:
        return field["dateTime"]
    d = field.get("date")
    return f"{d}T00:00:00" if d else ""


def _normalize_event(ev: dict[str, Any]) -> dict[str, Any]:
    start = ev.get("start") or {}
    end = ev.get("end") or {}
    out: dict[str, Any] = {
        "event_id": ev.get("id", ""),
        "title": ev.get("summary") or "",
        "start_iso": _normalize_time_field(start),
        "end_iso": _normalize_time_field(end),
    }
    if ev.get("htmlLink"):
        out["html_link"] = ev["htmlLink"]
    return out


def create_event(title: str, start_iso: str, end_iso: str) -> str:
    """Create a calendar event; return JSON in the same normalized shape as list_events (+ html_link)."""
    svc = _calendar_service()
    if svc is None:
        return credentials_missing_response()
    tz = _user_timezone()
    try:
        start_dt = _parse_iso_datetime(start_iso)
        end_dt = _parse_iso_datetime(end_iso)
    except ValueError as e:
        return json.dumps({"error": "invalid_iso_datetime", "detail": str(e)}, indent=2)

    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": _to_rfc3339(start_dt), "timeZone": tz},
        "end": {"dateTime": _to_rfc3339(end_dt), "timeZone": tz},
    }
    try:
        created = (
            svc.events()
            .insert(calendarId=_calendar_id(), body=body)
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_calendar_api", "status": status, "reason": str(e)},
            indent=2,
        )
    out = _normalize_event(created)
    out["created_at"] = created.get("created")
    return json.dumps(out, indent=2)


def list_events(start_iso: str, end_iso: str) -> str:
    """List events in [timeMin, timeMax); normalize to the shared event record shape."""
    svc = _calendar_service()
    if svc is None:
        return credentials_missing_response()
    try:
        start_dt = _parse_iso_datetime(start_iso)
        end_dt = _parse_iso_datetime(end_iso)
    except ValueError as e:
        return json.dumps({"error": "invalid_iso_datetime", "detail": str(e)}, indent=2)

    time_min = _to_rfc3339(start_dt)
    time_max = _to_rfc3339(end_dt)
    try:
        resp = (
            svc.events()
            .list(
                calendarId=_calendar_id(),
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_calendar_api", "status": status, "reason": str(e)},
            indent=2,
        )

    items = resp.get("items") or []
    # Keep events that overlap the window (API returns instances in range; all-day edge cases OK)
    normalized = [_normalize_event(ev) for ev in items]
    return json.dumps(normalized, indent=2)
