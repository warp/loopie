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

# Cap event description size for LLM payloads (full text available via API if we add get-by-id later).
_EVENT_DESCRIPTION_MAX_LEN = 2000


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


def _truncate_description(text: str) -> tuple[str, bool]:
    t = (text or "").strip()
    if len(t) <= _EVENT_DESCRIPTION_MAX_LEN:
        return t, False
    return t[:_EVENT_DESCRIPTION_MAX_LEN], True


def _hangout_or_conference_uri(ev: dict[str, Any]) -> str:
    if ev.get("hangoutLink"):
        return str(ev["hangoutLink"]).strip()
    conf = ev.get("conferenceData") or {}
    for ep in conf.get("entryPoints") or []:
        if not isinstance(ep, dict):
            continue
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return str(ep["uri"]).strip()
    for ep in conf.get("entryPoints") or []:
        if isinstance(ep, dict) and ep.get("uri"):
            return str(ep["uri"]).strip()
    return ""


def _normalize_attendees(ev: dict[str, Any]) -> list[dict[str, Any]]:
    raw = ev.get("attendees") or []
    out: list[dict[str, Any]] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        email = (a.get("email") or "").strip()
        display = (a.get("displayName") or "").strip()
        status = (a.get("responseStatus") or "").strip()
        if not email and not display:
            continue
        row: dict[str, Any] = {
            "email": email,
            "display_name": display,
            "response_status": status,
        }
        if a.get("organizer"):
            row["is_organizer"] = True
        out.append(row)
    return out


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

    loc = (ev.get("location") or "").strip()
    if loc:
        out["location"] = loc

    desc = ev.get("description")
    if isinstance(desc, str) and desc.strip():
        body, truncated = _truncate_description(desc)
        out["description"] = body
        if truncated:
            out["description_truncated"] = True

    attendees = _normalize_attendees(ev)
    if attendees:
        out["attendees"] = attendees

    hang = _hangout_or_conference_uri(ev)
    if hang:
        out["hangout_link"] = hang

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


def update_event(
    event_id: str,
    title: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    """Patch an existing calendar event by id. Omit optional fields to leave them unchanged.

    When moving time: pass both start_iso and end_iso, or pass only start_iso to preserve duration,
    or only end_iso to change end while keeping start. Timed events only (not all-day date-only).
    """
    svc = _calendar_service()
    if svc is None:
        return credentials_missing_response()
    eid = (event_id or "").strip()
    if not eid:
        return json.dumps({"error": "missing_event_id"}, indent=2)

    tz = _user_timezone()
    try:
        existing = (
            svc.events()
            .get(calendarId=_calendar_id(), eventId=eid)
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_calendar_api", "status": status, "reason": str(e)},
            indent=2,
        )

    start_f = existing.get("start") or {}
    end_f = existing.get("end") or {}
    if "dateTime" not in start_f or "dateTime" not in end_f:
        return json.dumps(
            {
                "error": "unsupported_event_type",
                "detail": "calendar_update_event supports timed events only, not all-day date-only events.",
            },
            indent=2,
        )

    body: dict[str, Any] = {}
    if title is not None:
        body["summary"] = title
    if location is not None:
        body["location"] = location
    if description is not None:
        body["description"] = description

    if start_iso is not None or end_iso is not None:
        try:
            old_start = _parse_iso_datetime(start_f["dateTime"])
            old_end = _parse_iso_datetime(end_f["dateTime"])
        except (ValueError, KeyError) as e:
            return json.dumps({"error": "invalid_existing_event_times", "detail": str(e)}, indent=2)

        duration = old_end - old_start
        if start_iso is not None and end_iso is not None:
            new_start = _parse_iso_datetime(start_iso)
            new_end = _parse_iso_datetime(end_iso)
        elif start_iso is not None:
            new_start = _parse_iso_datetime(start_iso)
            new_end = new_start + duration
        else:
            assert end_iso is not None
            new_start = old_start
            new_end = _parse_iso_datetime(end_iso)

        if new_end <= new_start:
            return json.dumps(
                {"error": "invalid_time_range", "detail": "end must be after start."},
                indent=2,
            )
        body["start"] = {"dateTime": _to_rfc3339(new_start), "timeZone": tz}
        body["end"] = {"dateTime": _to_rfc3339(new_end), "timeZone": tz}

    if not body:
        return json.dumps(
            {"error": "no_updates", "detail": "Pass at least one of title, start_iso, end_iso, location, description."},
            indent=2,
        )

    try:
        updated = (
            svc.events()
            .patch(calendarId=_calendar_id(), eventId=eid, body=body)
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_calendar_api", "status": status, "reason": str(e)},
            indent=2,
        )

    out = _normalize_event(updated)
    out["updated_at"] = updated.get("updated")
    return json.dumps(out, indent=2)


def _parse_invitee_emails(attendee_emails: str) -> list[str]:
    """Split comma/semicolon/newline-separated list; strip; require '@'; dedupe case-insensitively."""
    raw = (attendee_emails or "").replace(";", ",").replace("\n", ",")
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        e = part.strip()
        if not e or "@" not in e:
            continue
        key = e.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def invite_to_event(event_id: str, attendee_emails: str) -> str:
    """Add guests to an existing event by email. attendee_emails: comma-separated (or semicolon/newline). Sends invite notifications."""
    svc = _calendar_service()
    if svc is None:
        return credentials_missing_response()
    eid = (event_id or "").strip()
    if not eid:
        return json.dumps({"error": "missing_event_id"}, indent=2)

    new_addrs = _parse_invitee_emails(attendee_emails)
    if not new_addrs:
        return json.dumps(
            {
                "error": "no_valid_emails",
                "detail": "Provide at least one email containing @, separated by commas.",
            },
            indent=2,
        )

    try:
        existing = (
            svc.events()
            .get(calendarId=_calendar_id(), eventId=eid)
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_calendar_api", "status": status, "reason": str(e)},
            indent=2,
        )

    current = existing.get("attendees") or []
    merged: list[dict[str, Any]] = []
    existing_lower: set[str] = set()
    for a in current:
        if not isinstance(a, dict):
            continue
        em = (a.get("email") or "").strip()
        if em:
            existing_lower.add(em.lower())
        merged.append(dict(a))

    added: list[str] = []
    for em in new_addrs:
        if em.lower() not in existing_lower:
            merged.append({"email": em})
            existing_lower.add(em.lower())
            added.append(em)

    if not added:
        out = _normalize_event(existing)
        out["invites_skipped"] = "all_emails_already_attendees"
        return json.dumps(out, indent=2)

    body = {"attendees": merged}
    try:
        updated = (
            svc.events()
            .patch(
                calendarId=_calendar_id(),
                eventId=eid,
                body=body,
                sendUpdates="all",
            )
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_calendar_api", "status": status, "reason": str(e)},
            indent=2,
        )

    out = _normalize_event(updated)
    out["updated_at"] = updated.get("updated")
    out["invited_emails"] = added
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
