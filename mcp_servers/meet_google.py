"""Google Meet API v2 helpers for meeting spaces and transcript artifacts."""

from __future__ import annotations

import json
import re
from typing import Any

import google.auth.credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcp_servers import google_auth_env as gae

MEET_SCOPES = (
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/meetings.space.settings",
)

_MEET_CODE_RE = re.compile(r"([a-z]+-[a-z]+-[a-z]+)", re.IGNORECASE)


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
        if err.get("status"):
            out["api_status"] = err["status"]
    return out


def _load_credentials() -> google.auth.credentials.Credentials | None:
    sa_json = gae.service_account_json()
    sa_path = gae.service_account_path()
    if sa_json:
        info = json.loads(sa_json)
        return service_account.Credentials.from_service_account_info(info, scopes=MEET_SCOPES)
    if sa_path:
        return service_account.Credentials.from_service_account_file(
            gae.resolve_env_path(sa_path), scopes=MEET_SCOPES
        )

    tok_json = gae.oauth_token_json()
    tok_path = gae.oauth_token_path()
    if tok_json:
        return UserCredentials.from_authorized_user_info(json.loads(tok_json), MEET_SCOPES)
    if tok_path:
        return UserCredentials.from_authorized_user_file(
            gae.resolve_env_path(tok_path), MEET_SCOPES
        )
    return None


def _ensure_fresh(creds: google.auth.credentials.Credentials) -> google.auth.credentials.Credentials:
    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        creds.refresh(Request())
    return creds


def _meet_service():
    creds = _load_credentials()
    if creds is None:
        return None
    creds = _ensure_fresh(creds)
    return build("meet", "v2", credentials=creds, cache_discovery=False)


def credentials_missing_response() -> str:
    return json.dumps(
        {
            "error": "meet_credentials_not_configured",
            "hint": (
                "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_PATH, "
                "or GOOGLE_OAUTH_TOKEN_JSON / GOOGLE_OAUTH_TOKEN_PATH. OAuth tokens must include "
                "Meet space readonly/created/settings scopes; rerun python scripts/oauth_setup.py "
                "after enabling the Google Meet API."
            ),
        },
        indent=2,
    )


def meeting_code_from_uri(uri: str | None) -> str:
    if not uri:
        return ""
    m = _MEET_CODE_RE.search(uri)
    return m.group(1).lower() if m else ""


def meeting_code_from_event(ev: dict[str, Any]) -> str:
    conf = ev.get("conferenceData") or {}
    cid = (conf.get("conferenceId") or "").strip()
    if cid:
        return cid.lower()
    if ev.get("hangoutLink"):
        code = meeting_code_from_uri(str(ev["hangoutLink"]))
        if code:
            return code
    for ep in conf.get("entryPoints") or []:
        if isinstance(ep, dict):
            code = meeting_code_from_uri(str(ep.get("uri") or ""))
            if code:
                return code
    return ""


def _space_name_for_code(meeting_code: str) -> str:
    code = meeting_code.strip().lower()
    return code if code.startswith("spaces/") else f"spaces/{code}"


def enable_auto_transcription_for_meeting_code(meeting_code: str) -> dict[str, Any]:
    """Enable auto transcripts for a Meet space, returning normalized status metadata."""
    code = meeting_code.strip().lower()
    if not code:
        return {"error": "missing_meeting_code"}
    svc = _meet_service()
    if svc is None:
        return json.loads(credentials_missing_response())

    try:
        space = svc.spaces().get(name=_space_name_for_code(code)).execute()
        space_name = space.get("name") or _space_name_for_code(code)
        updated = (
            svc.spaces()
            .patch(
                name=space_name,
                updateMask="config.artifact_config.transcription_config.auto_transcription_generation",
                body={
                    "config": {
                        "artifactConfig": {
                            "transcriptionConfig": {
                                "autoTranscriptionGeneration": "ON",
                            }
                        }
                    }
                },
            )
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return {
            "error": "transcription_not_enabled_or_unavailable",
            "status": status,
            "meeting_code": code,
            **_http_error_payload(e),
        }

    cfg = (
        (updated.get("config") or {})
        .get("artifactConfig", {})
        .get("transcriptionConfig", {})
    )
    return {
        "meeting_code": updated.get("meetingCode") or code,
        "meeting_uri": updated.get("meetingUri"),
        "space_name": updated.get("name"),
        "auto_transcription_generation": cfg.get("autoTranscriptionGeneration"),
    }


def _conference_records_for_code(svc: Any, meeting_code: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    token: str | None = None
    filter_expr = f'space.meeting_code = "{meeting_code}"'
    while True:
        req = svc.conferenceRecords().list(pageSize=100, filter=filter_expr)
        if token:
            req = svc.conferenceRecords().list(pageSize=100, filter=filter_expr, pageToken=token)
        resp = req.execute()
        records.extend(resp.get("conferenceRecords") or [])
        token = resp.get("nextPageToken")
        if not token:
            return records


def _transcripts_for_record(svc: Any, record_name: str) -> list[dict[str, Any]]:
    transcripts: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"parent": record_name, "pageSize": 100}
        if token:
            kwargs["pageToken"] = token
        resp = svc.conferenceRecords().transcripts().list(**kwargs).execute()
        transcripts.extend(resp.get("transcripts") or [])
        token = resp.get("nextPageToken")
        if not token:
            return transcripts


def _entries_for_transcript(svc: Any, transcript_name: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    token: str | None = None
    max_entries = 1000
    while len(entries) < max_entries:
        kwargs: dict[str, Any] = {"parent": transcript_name, "pageSize": 100}
        if token:
            kwargs["pageToken"] = token
        resp = svc.conferenceRecords().transcripts().entries().list(**kwargs).execute()
        entries.extend(resp.get("transcriptEntries") or [])
        token = resp.get("nextPageToken")
        if not token:
            return entries
    return entries[:max_entries]


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    participant = entry.get("participant") or ""
    out: dict[str, Any] = {
        "start_time": entry.get("startTime"),
        "end_time": entry.get("endTime"),
        "speaker": participant.rsplit("/", 1)[-1] if participant else "",
        "text": entry.get("text") or "",
    }
    if entry.get("languageCode"):
        out["language_code"] = entry["languageCode"]
    return out


def read_transcript_for_meeting_code(meeting_code: str) -> str:
    code = meeting_code.strip().lower()
    if not code:
        return json.dumps({"error": "missing_meeting_code"}, indent=2)
    svc = _meet_service()
    if svc is None:
        return credentials_missing_response()

    try:
        records = _conference_records_for_code(svc, code)
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_meet_api", "status": status, **_http_error_payload(e)},
            indent=2,
        )

    if not records:
        return json.dumps(
            {
                "error": "transcript_not_ready",
                "meeting_code": code,
                "detail": "No completed or active conference record is visible for this meeting code yet.",
            },
            indent=2,
        )

    transcript_states: list[dict[str, Any]] = []
    try:
        for record in records:
            record_name = record.get("name") or ""
            if not record_name:
                continue
            transcripts = _transcripts_for_record(svc, record_name)
            for transcript in transcripts:
                state = transcript.get("state")
                transcript_states.append(
                    {
                        "conference_record": record_name,
                        "transcript": transcript.get("name"),
                        "state": state,
                    }
                )
                if state != "FILE_GENERATED":
                    continue
                entries = [_normalize_entry(e) for e in _entries_for_transcript(svc, transcript["name"])]
                lines = []
                for e in entries:
                    speaker = e.get("speaker") or "speaker"
                    stamp = e.get("start_time") or ""
                    prefix = f"[{stamp}] " if stamp else ""
                    lines.append(f"{prefix}{speaker}: {e.get('text') or ''}".strip())
                return json.dumps(
                    {
                        "meeting_code": code,
                        "conference_record": record_name,
                        "transcript": transcript.get("name"),
                        "entry_count": len(entries),
                        "text": "\n".join(lines),
                        "entries": entries,
                    },
                    indent=2,
                )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_meet_api", "status": status, **_http_error_payload(e)},
            indent=2,
        )

    return json.dumps(
        {
            "error": "transcript_not_ready",
            "meeting_code": code,
            "detail": "No generated transcript file is visible for this meeting yet.",
            "transcripts": transcript_states,
        },
        indent=2,
    )
