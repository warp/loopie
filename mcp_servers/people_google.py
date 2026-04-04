"""Google People API v1 for MCP contact search tools."""

from __future__ import annotations

import json
from typing import Any

import google.auth.credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcp_servers import google_auth_env as gae

PEOPLE_SCOPES = ("https://www.googleapis.com/auth/contacts.readonly",)


def _load_credentials() -> google.auth.credentials.Credentials | None:
    sa_json = gae.service_account_json()
    sa_path = gae.service_account_path()
    if sa_json:
        info = json.loads(sa_json)
        return service_account.Credentials.from_service_account_info(info, scopes=PEOPLE_SCOPES)
    if sa_path:
        return service_account.Credentials.from_service_account_file(
            gae.resolve_env_path(sa_path), scopes=PEOPLE_SCOPES
        )

    tok_json = gae.oauth_token_json()
    tok_path = gae.oauth_token_path()
    if tok_json:
        return UserCredentials.from_authorized_user_info(json.loads(tok_json), PEOPLE_SCOPES)
    if tok_path:
        return UserCredentials.from_authorized_user_file(
            gae.resolve_env_path(tok_path), PEOPLE_SCOPES
        )
    return None


def _ensure_fresh(
    creds: google.auth.credentials.Credentials,
) -> google.auth.credentials.Credentials:
    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        creds.refresh(Request())
    return creds


def _people_service():
    creds = _load_credentials()
    if creds is None:
        return None
    creds = _ensure_fresh(creds)
    return build("people", "v1", credentials=creds, cache_discovery=False)


def credentials_missing_response() -> str:
    return json.dumps(
        {
            "error": "people_credentials_not_configured",
            "hint": (
                "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_PATH, "
                "or GOOGLE_OAUTH_TOKEN_JSON / GOOGLE_OAUTH_TOKEN_PATH (OAuth token must include contacts.readonly scope). "
                "Enable the Google People API and re-run python -m mcp_servers.oauth_setup after updating scopes."
            ),
        },
        indent=2,
    )


def _first(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for v in value:
            s = _first(v)
            if s:
                return s
        return ""
    if isinstance(value, dict):
        for k in ("displayName", "value", "formattedValue"):
            if k in value and isinstance(value[k], str) and value[k].strip():
                return value[k].strip()
        return ""
    return ""


def _normalize_contact(person: dict[str, Any]) -> dict[str, Any]:
    resource_name = (person.get("resourceName") or "").strip()
    names = person.get("names") or []
    emails = person.get("emailAddresses") or []
    phones = person.get("phoneNumbers") or []

    display_name = _first(names) or ""
    out: dict[str, Any] = {
        "contact_id": resource_name,
        "display_name": display_name,
        "emails": [],
        "phones": [],
    }

    for e in emails:
        v = _first(e)
        if v and v not in out["emails"]:
            out["emails"].append(v)
    for p in phones:
        v = _first(p)
        if v and v not in out["phones"]:
            out["phones"].append(v)

    return out


def search_contacts(query: str, limit: int = 10) -> str:
    svc = _people_service()
    if svc is None:
        return credentials_missing_response()

    q = (query or "").strip()
    if not q:
        return json.dumps([], indent=2)

    limit = max(1, min(int(limit or 10), 25))

    try:
        resp = (
            svc.people()
            .searchContacts(
                query=q,
                pageSize=limit,
                readMask="names,emailAddresses,phoneNumbers",
            )
            .execute()
        )
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_people_api", "status": status, "reason": str(e)},
            indent=2,
        )

    results = resp.get("results") or []
    normalized: list[dict[str, Any]] = []
    for r in results:
        person = r.get("person") or {}
        c = _normalize_contact(person)
        if c.get("contact_id") and (c.get("display_name") or c.get("emails") or c.get("phones")):
            normalized.append(c)

    return json.dumps(normalized[:limit], indent=2)

