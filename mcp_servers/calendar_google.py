"""Google Calendar API v3 for MCP calendar tools."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
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


def _env_fallback_duration_minutes() -> int:
    """When Calendar settings cannot be read, use env or 60 (Google's default for defaultEventLength)."""
    raw = os.environ.get("DEFAULT_EVENT_DURATION_MINUTES", "60").strip()
    try:
        n = int(raw)
        return max(1, min(n, 24 * 60))
    except ValueError:
        return 60


def _fetch_default_event_length_minutes(svc: Any) -> int:
    """User's default event length from Calendar settings (defaultEventLength), else env fallback."""
    try:
        r = svc.settings().get(setting="defaultEventLength").execute()
        val = (r.get("value") or "").strip()
        if val.isdigit():
            return max(1, min(int(val), 24 * 60))
    except HttpError:
        pass
    except Exception:
        pass
    return _env_fallback_duration_minutes()


def _parse_local_date_only(s: str) -> date | None:
    """If start_iso is strictly YYYY-MM-DD (no time), return that date; else None."""
    raw = (s or "").strip()
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _business_weekdays_set() -> set[int]:
    """Weekdays when business hours apply (Python: Monday=0 … Sunday=6). Default Mon–Fri."""
    raw = os.environ.get("BUSINESS_DAYS", "0,1,2,3,4").strip()
    out: set[int] = set()
    for part in raw.split(","):
        p = part.strip()
        if p.isdigit():
            n = int(p)
            if 0 <= n <= 6:
                out.add(n)
    return out if out else {0, 1, 2, 3, 4}


def _business_hour_bounds() -> tuple[int, int]:
    """Start and end hour (0–23) in USER_TIMEZONE; end is exclusive (e.g. 9–17 = 9:00–17:00)."""
    try:
        sh = int(os.environ.get("BUSINESS_HOURS_START", "9"))
        eh = int(os.environ.get("BUSINESS_HOURS_END", "17"))
    except ValueError:
        return 9, 17
    sh = max(0, min(23, sh))
    eh = max(0, min(24, eh))
    if eh <= sh:
        eh = min(24, sh + 8)
    return sh, eh


def _business_window_for_day(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    sh, eh = _business_hour_bounds()
    start = datetime.combine(day, time(hour=sh, minute=0), tzinfo=tz)
    end = datetime.combine(day, time(hour=eh, minute=0), tzinfo=tz)
    return start, end


def _ceil_to_minute_step(dt: datetime, step: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    if step <= 1:
        return dt
    total = dt.hour * 60 + dt.minute
    extra = (step - (total % step)) % step
    if extra == 0:
        return dt
    return dt + timedelta(minutes=extra)


def _parse_api_dt(s: str) -> datetime:
    t = (s or "").strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    return datetime.fromisoformat(t)


def _merge_busy_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged: list[tuple[datetime, datetime]] = [intervals[0]]
    for a, b in intervals[1:]:
        la, lb = merged[-1]
        if a <= lb:
            merged[-1] = (la, max(lb, b))
        else:
            merged.append((a, b))
    return merged


def _freebusy_busy_merged(
    svc: Any,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    cid = _calendar_id()
    body: dict[str, Any] = {
        "timeMin": _to_rfc3339(window_start),
        "timeMax": _to_rfc3339(window_end),
        "timeZone": _user_timezone(),
        "items": [{"id": cid}],
    }
    resp = svc.freebusy().query(body=body).execute()
    cals = resp.get("calendars") or {}
    cal = cals.get(cid)
    if cal is None and cals:
        cal = next(iter(cals.values()))
    if not cal:
        return []
    raw_busy = cal.get("busy") or []
    intervals: list[tuple[datetime, datetime]] = []
    for b in raw_busy:
        if not isinstance(b, dict):
            continue
        try:
            intervals.append((_parse_api_dt(b["start"]), _parse_api_dt(b["end"])))
        except (KeyError, ValueError):
            continue
    return _merge_busy_intervals(intervals)


def _interval_overlaps_busy(
    a: datetime,
    b: datetime,
    busy: list[tuple[datetime, datetime]],
) -> bool:
    for bs, be in busy:
        if a < be and b > bs:
            return True
    return False


def _first_gap_in_window(
    svc: Any,
    window_start: datetime,
    window_end: datetime,
    duration_mins: int,
    step_mins: int,
) -> tuple[datetime, datetime] | None:
    busy = _freebusy_busy_merged(svc, window_start, window_end)
    dur = timedelta(minutes=duration_mins)
    step = timedelta(minutes=step_mins)
    cursor = window_start
    while cursor + dur <= window_end:
        if not _interval_overlaps_busy(cursor, cursor + dur, busy):
            return cursor, cursor + dur
        cursor += step
    return None


def _find_next_free_slot(
    svc: Any,
    first_day: date,
    duration_mins: int,
) -> tuple[datetime, datetime] | None:
    """Earliest free slot of duration_mins within business hours, using free/busy on this calendar."""
    tz = ZoneInfo(_user_timezone())
    now = datetime.now(tz)
    today = now.date()
    start_day = first_day if first_day >= today else today
    max_days = max(1, min(int(os.environ.get("SLOT_SEARCH_MAX_DAYS", "14")), 60))
    step = max(5, min(int(os.environ.get("SLOT_SEARCH_STEP_MINUTES", "15")), 60))
    wd = _business_weekdays_set()

    for offset in range(max_days):
        day = start_day + timedelta(days=offset)
        if day.weekday() not in wd:
            continue
        win_start, win_end = _business_window_for_day(day, tz)
        if day == now.date() and now > win_start:
            cursor = _ceil_to_minute_step(max(now, win_start), step)
        else:
            cursor = _ceil_to_minute_step(win_start, step)
        if cursor + timedelta(minutes=duration_mins) > win_end:
            continue
        slot = _first_gap_in_window(svc, cursor, win_end, duration_mins, step)
        if slot:
            return slot
    return None


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


def create_event(title: str, start_iso: str, end_iso: str | None = None) -> str:
    """Create a calendar event; return JSON in the same normalized shape as list_events (+ html_link).

    - If start_iso is **date-only** ``YYYY-MM-DD`` (no time): pick the earliest free slot of the
      default event length within BUSINESS_HOURS / BUSINESS_DAYS (see env) using free/busy.
      Omit end_iso in this mode.
    - Otherwise: timed start. If end_iso is omitted or empty, end = start + default event length
      (Calendar setting defaultEventLength or DEFAULT_EVENT_DURATION_MINUTES).
    """
    svc = _calendar_service()
    if svc is None:
        return credentials_missing_response()
    tz = _user_timezone()
    end_raw = (end_iso or "").strip()

    d_only = _parse_local_date_only(start_iso)
    if d_only is not None:
        if end_raw:
            return json.dumps(
                {
                    "error": "invalid_create_event_args",
                    "detail": (
                        "When start_iso is date-only (YYYY-MM-DD), omit end_iso; "
                        "a free slot is chosen using default duration."
                    ),
                },
                indent=2,
            )
        duration_mins = _fetch_default_event_length_minutes(svc)
        slot = _find_next_free_slot(svc, d_only, duration_mins)
        if slot is None:
            return json.dumps(
                {
                    "error": "no_free_slot",
                    "detail": (
                        f"No contiguous free slot of {duration_mins} minutes found within business hours "
                        f"(BUSINESS_HOURS_START/END, BUSINESS_DAYS) in the next SLOT_SEARCH_MAX_DAYS days."
                    ),
                },
                indent=2,
            )
        start_dt, end_dt = slot
        used_default = True
        default_mins = duration_mins
        picked_slot = True
    else:
        picked_slot = False
        try:
            start_dt = _parse_iso_datetime(start_iso)
        except ValueError as e:
            return json.dumps({"error": "invalid_iso_datetime", "detail": str(e)}, indent=2)

        used_default = False
        default_mins: int | None = None
        if not end_raw:
            default_mins = _fetch_default_event_length_minutes(svc)
            end_dt = start_dt + timedelta(minutes=default_mins)
            used_default = True
        else:
            try:
                end_dt = _parse_iso_datetime(end_raw)
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
    if used_default and default_mins is not None:
        out["default_duration_minutes"] = default_mins
    if picked_slot:
        out["free_slot_selected"] = True
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


def _get_event_timed_bounds(ev: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """Start/end datetimes for a timed event; None for all-day or invalid."""
    start = ev.get("start") or {}
    end = ev.get("end") or {}
    if "dateTime" not in start or "dateTime" not in end:
        return None
    try:
        return _parse_api_dt(start["dateTime"]), _parse_api_dt(end["dateTime"])
    except (ValueError, TypeError):
        return None


def _freebusy_query_calendars(
    svc: Any,
    tmin: datetime,
    tmax: datetime,
    calendar_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Free/busy for each calendar id: {busy: merged intervals, errors: optional list}."""
    ids = list(dict.fromkeys(calendar_ids))
    if not ids:
        return {}
    body: dict[str, Any] = {
        "timeMin": _to_rfc3339(tmin),
        "timeMax": _to_rfc3339(tmax),
        "timeZone": _user_timezone(),
        "items": [{"id": x} for x in ids],
    }
    resp = svc.freebusy().query(body=body).execute()
    cals = resp.get("calendars") or {}
    out: dict[str, dict[str, Any]] = {}
    for cid in ids:
        raw: dict[str, Any] | None = None
        for k, v in cals.items():
            if isinstance(k, str) and k.lower() == cid.lower():
                raw = v if isinstance(v, dict) else None
                break
        if raw is None:
            out[cid] = {"busy": [], "errors": [{"reason": "calendarNotInResponse"}]}
            continue
        errs = raw.get("errors")
        busy_raw = raw.get("busy") or []
        intervals: list[tuple[datetime, datetime]] = []
        for b in busy_raw:
            if isinstance(b, dict):
                try:
                    intervals.append((_parse_api_dt(b["start"]), _parse_api_dt(b["end"])))
                except (KeyError, ValueError):
                    continue
        out[cid] = {
            "busy": _merge_busy_intervals(intervals),
            "errors": errs,
        }
    return out


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
    """Add guests to an existing event by email. attendee_emails: comma-separated (or semicolon/newline). Sends invite notifications.

    Optionally checks each new invitee's calendar via FreeBusy (when shared with the authenticated
    user) so the event time does not overlap their busy intervals. See INVITE_* env vars.
    """
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

    to_add = [em for em in new_addrs if em.lower() not in existing_lower]

    if not to_add:
        out = _normalize_event(existing)
        out["invites_skipped"] = "all_emails_already_attendees"
        return json.dumps(out, indent=2)

    check_fb = os.environ.get("INVITE_CHECK_ATTENDEE_FREEBUSY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    block_busy = os.environ.get("INVITE_BLOCK_ON_ATTENDEE_BUSY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    allow_unknown = os.environ.get("INVITE_ALLOW_FREEBUSY_UNAVAILABLE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    availability_meta: dict[str, Any] = {}
    if check_fb:
        bounds = _get_event_timed_bounds(existing)
        if bounds is None:
            availability_meta["attendee_availability"] = "skipped_not_timed_event"
        else:
            es, ee = bounds
            fb = _freebusy_query_calendars(svc, es, ee, to_add)
            busy_conflicts: list[str] = []
            unverified: list[str] = []
            for em in to_add:
                info = fb.get(em)
                if info is None:
                    for k, v in fb.items():
                        if k.lower() == em.lower():
                            info = v
                            break
                if not isinstance(info, dict):
                    unverified.append(em)
                    continue
                errs = info.get("errors")
                busy = info.get("busy") or []
                if errs:
                    unverified.append(em)
                    continue
                if _interval_overlaps_busy(es, ee, busy):
                    busy_conflicts.append(em)
            if busy_conflicts and block_busy:
                return json.dumps(
                    {
                        "error": "attendee_busy",
                        "detail": (
                            "These invitees have a conflicting busy block during this event's time "
                            "(based on their free/busy visibility to this account)."
                        ),
                        "conflicting_emails": busy_conflicts,
                    },
                    indent=2,
                )
            if busy_conflicts and not block_busy:
                availability_meta["attendee_busy_warning_nonblocking"] = busy_conflicts
            if unverified:
                availability_meta["attendee_freebusy_unverified"] = unverified
                if not allow_unknown:
                    return json.dumps(
                        {
                            "error": "attendee_calendar_not_visible",
                            "detail": (
                                "Could not read free/busy for some invitees (calendar not shared or "
                                "no access). Set INVITE_ALLOW_FREEBUSY_UNAVAILABLE=1 to invite anyway."
                            ),
                            "emails": unverified,
                        },
                        indent=2,
                    )

    for em in to_add:
        merged.append({"email": em})
        existing_lower.add(em.lower())
    added = list(to_add)

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
    if availability_meta:
        out["invite_availability"] = availability_meta
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
