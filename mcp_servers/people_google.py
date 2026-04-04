"""Google People API v1 for MCP contact search tools."""

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


def _display_name_from_person(person: dict[str, Any]) -> str:
    """People API name entries often omit displayName; use given/family/unstructured."""
    names = person.get("names") or []
    for n in names:
        if not isinstance(n, dict):
            continue
        if n.get("displayName") and str(n["displayName"]).strip():
            return str(n["displayName"]).strip()
        if n.get("unstructuredName") and str(n["unstructuredName"]).strip():
            return str(n["unstructuredName"]).strip()
        parts = [n.get("givenName"), n.get("middleName"), n.get("familyName")]
        joined = " ".join(str(p).strip() for p in parts if p and str(p).strip())
        if joined:
            return joined
    return ""


def _email_values(person: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for e in person.get("emailAddresses") or []:
        if not isinstance(e, dict):
            continue
        v = (e.get("value") or "").strip()
        if not v:
            v = _first(e)
        if v and v not in out:
            out.append(v)
    return out


def _phone_values(person: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for p in person.get("phoneNumbers") or []:
        if not isinstance(p, dict):
            continue
        v = (p.get("value") or "").strip()
        if not v:
            v = _first(p)
        if v and v not in out:
            out.append(v)
    return out


def _nickname_strings(person: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for n in person.get("nicknames") or []:
        if not isinstance(n, dict):
            continue
        v = (n.get("value") or "").strip()
        if not v:
            v = _first(n)
        if v and v not in out:
            out.append(v)
    return out


def _normalize_contact(person: dict[str, Any]) -> dict[str, Any]:
    display_name = _display_name_from_person(person)
    emails_list = _email_values(person)
    phones_list = _phone_values(person)
    nicknames_list = _nickname_strings(person)

    out: dict[str, Any] = {
        "display_name": display_name,
        "emails": emails_list,
        "phones": phones_list,
    }
    if nicknames_list:
        out["nicknames"] = nicknames_list
    if emails_list:
        out["primary_email"] = emails_list[0]

    return out


def _query_digits(query: str) -> str:
    return re.sub(r"\D", "", query or "")


def _contact_matches_query(c: dict[str, Any], q_lower: str, q_digits: str) -> bool:
    """Match name, email, nickname (substring). Match phone only when query has 3+ digits (phone-like)."""
    if q_lower in (c.get("display_name") or "").lower():
        return True
    for e in c.get("emails") or []:
        if q_lower in str(e).lower():
            return True
    for nick in c.get("nicknames") or []:
        if q_lower in str(nick).lower():
            return True
    # Avoid substring-matching phones for text-only queries; that skewed results toward contacts with phones.
    if q_digits and len(q_digits) >= 3:
        for p in c.get("phones") or []:
            if q_digits in _query_digits(str(p)):
                return True
    return False


def search_contacts(query: str, limit: int = 10) -> str:
    """Search the user's Google Contacts (My Contacts) by listing connections and matching locally.

    people.searchContacts often misses entries that appear in contacts.google.com; connections.list
    matches the address book the user sees in Gmail/Contacts for saved contacts.
    """
    svc = _people_service()
    if svc is None:
        return credentials_missing_response()

    q = (query or "").strip()
    if not q:
        return json.dumps([], indent=2)

    limit = max(1, min(int(limit or 10), 25))
    q_lower = q.lower()
    q_digits = _query_digits(q)

    matches: list[dict[str, Any]] = []
    page_token: str | None = None
    page_size = 500
    # Cap pages so pathological address books do not loop forever (~25k contacts scanned).
    max_pages = 50

    try:
        for _ in range(max_pages):
            list_kwargs: dict[str, Any] = {
                "resourceName": "people/me",
                "pageSize": page_size,
                "personFields": "names,emailAddresses,phoneNumbers,nicknames",
            }
            if page_token:
                list_kwargs["pageToken"] = page_token
            resp = svc.people().connections().list(**list_kwargs).execute()
            for person in resp.get("connections") or []:
                if not isinstance(person, dict):
                    continue
                c = _normalize_contact(person)
                if not (
                    c.get("display_name")
                    or c.get("emails")
                    or c.get("phones")
                    or c.get("nicknames")
                ):
                    continue
                if _contact_matches_query(c, q_lower, q_digits):
                    matches.append(c)
                    if len(matches) >= limit:
                        return json.dumps(matches[:limit], indent=2)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        status = int(e.resp.status) if e.resp else None
        return json.dumps(
            {"error": "google_people_api", "status": status, "reason": str(e)},
            indent=2,
        )

    return json.dumps(matches[:limit], indent=2)

