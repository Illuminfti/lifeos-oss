"""Gmail read-only capture connector."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Any, Iterable, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.connectors.http import bearer_headers, oauth_access_token, request_json
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest
from lifeos.errors import AuthenticationRequired, ConfigurationError, ConnectorError

BASE = "https://gmail.googleapis.com/gmail/v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _b64(value: str) -> str:
    if not value:
        return ""
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _headers(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in payload.get("headers", []) or []:
        if isinstance(item, Mapping) and item.get("name"):
            result[str(item["name"]).lower()] = str(item.get("value", ""))
    return result


def _body(payload: Mapping[str, Any]) -> str:
    mime = str(payload.get("mimeType", ""))
    data = payload.get("body", {}).get("data") if isinstance(payload.get("body"), Mapping) else None
    if data and (mime.startswith("text/plain") or not payload.get("parts")):
        return _b64(str(data))
    plain: list[str] = []
    html: list[str] = []
    for part in payload.get("parts", []) or []:
        if not isinstance(part, Mapping):
            continue
        text = _body(part)
        if not text:
            continue
        if str(part.get("mimeType", "")).startswith("text/plain"):
            plain.append(text)
        else:
            html.append(text)
    return "\n".join(plain or html)


class GmailConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.email-gmail",
        display_name="Gmail",
        source_classes=("email", "thread", "person", "attachment_metadata"),
        capabilities=("backfill", "incremental_sync", "deletions", "revoke", "purge"),
        auth_modes=("oauth2",),
        custody="local",
        implementation_status="experimental",
        notes="Read-only Gmail REST adapter. Live provider validation requires owner OAuth credentials.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping) or not (secret.get("access_token") or secret.get("refresh_token")):
            raise ConfigurationError("Gmail requires OAuth secret JSON")
        scopes = tuple(str(x) for x in request.get("scopes", ["https://www.googleapis.com/auth/gmail.readonly"]))
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "user_id": str(request.get("user_id", "me")),
                "query": str(request.get("query", "")),
                "page_size": min(500, max(1, int(request.get("page_size", 100)))),
                "max_pages": min(1000, max(1, int(request.get("max_pages", 20)))),
            },
            granted_scopes=scopes,
            secret_payload=dict(secret),
        )

    def _auth(self, connection: Connection, context: ConnectorContext) -> dict[str, str]:
        return bearer_headers(oauth_access_token(connection, context, token_url=TOKEN_URL))

    def _message(self, connection: Connection, context: ConnectorContext, message_id: str) -> CaptureEvent:
        user = str(connection.settings.get("user_id", "me"))
        value, _ = request_json(
            "GET",
            f"{BASE}/users/{user}/messages/{message_id}",
            headers=self._auth(connection, context),
            params={"format": "full"},
        )
        if not isinstance(value, Mapping):
            raise ConnectorError("Gmail returned a malformed message")
        payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
        headers = _headers(payload)
        sender_name, sender_address = parseaddr(headers.get("from", ""))
        sender_display = sender_name or sender_address or "Unknown sender"
        internal_ms = int(value.get("internalDate", 0) or 0)
        occurred = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if internal_ms else datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        attachment_names: list[str] = []
        stack = list(payload.get("parts", []) or []) if isinstance(payload, Mapping) else []
        while stack:
            part = stack.pop()
            if not isinstance(part, Mapping):
                continue
            if part.get("filename"):
                attachment_names.append(str(part["filename"]))
            stack.extend(item for item in part.get("parts", []) or [] if isinstance(item, Mapping))
        text = _body(payload)
        subject = headers.get("subject", "")
        combined = f"Subject: {subject}\n\n{text}".strip()
        return CaptureEvent.create(
            connector_id=self.manifest.id,
            connection_id=connection.connection_id,
            source_record_id=str(value.get("id", message_id)),
            source_revision=str(value.get("historyId") or content_digest(value)),
            source_thread_id=str(value.get("threadId") or message_id),
            kind="email.message",
            occurred_at=occurred,
            actors=(Actor(provider_ref=f"email:{sender_address.lower()}", display_name=sender_display),) if sender_address else (),
            text=combined,
            raw=value,
            metadata={
                "subject": subject,
                "from": headers.get("from"),
                "to": headers.get("to"),
                "cc": headers.get("cc"),
                "labels": list(value.get("labelIds", []) or []),
                "attachment_names": attachment_names,
            },
        )

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        user = str(connection.settings.get("user_id", "me"))
        token: str | None = None
        events: list[CaptureEvent] = []
        warnings: list[str] = []
        for _ in range(int(connection.settings.get("max_pages", 20))):
            page, _ = request_json(
                "GET",
                f"{BASE}/users/{user}/messages",
                headers=self._auth(connection, context),
                params={
                    "maxResults": connection.settings.get("page_size", 100),
                    "q": connection.settings.get("query") or None,
                    "pageToken": token,
                },
            )
            if not isinstance(page, Mapping):
                raise ConnectorError("Gmail message list is malformed")
            for item in page.get("messages", []) or []:
                if not isinstance(item, Mapping) or not item.get("id"):
                    continue
                try:
                    events.append(self._message(connection, context, str(item["id"])))
                except ConnectorError as exc:
                    warnings.append(f"message {item.get('id')}: {exc}")
            token = str(page.get("nextPageToken")) if page.get("nextPageToken") else None
            if not token:
                break
        profile, _ = request_json(
            "GET",
            f"{BASE}/users/{user}/profile",
            headers=self._auth(connection, context),
        )
        history_id = str(profile.get("historyId", "")) if isinstance(profile, Mapping) else ""
        return SyncBatch(
            events=tuple(events),
            checkpoint={"history_id": history_id, "backfill_page_token": token},
            complete=token is None,
            warnings=tuple(warnings),
        )

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        history_id = str(checkpoint.get("history_id", ""))
        if not history_id:
            return self.backfill(connection, checkpoint, context)
        user = str(connection.settings.get("user_id", "me"))
        token: str | None = None
        events: list[CaptureEvent] = []
        seen: set[str] = set()
        latest = history_id
        for _ in range(int(connection.settings.get("max_pages", 20))):
            page, _ = request_json(
                "GET",
                f"{BASE}/users/{user}/history",
                headers=self._auth(connection, context),
                params={"startHistoryId": history_id, "pageToken": token, "maxResults": connection.settings.get("page_size", 100)},
            )
            if not isinstance(page, Mapping):
                raise ConnectorError("Gmail history response is malformed")
            latest = str(page.get("historyId") or latest)
            for history in page.get("history", []) or []:
                if not isinstance(history, Mapping):
                    continue
                latest = max(latest, str(history.get("id") or latest), key=lambda x: int(x) if x.isdigit() else 0)
                for field in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                    for wrapper in history.get(field, []) or []:
                        message = wrapper.get("message") if isinstance(wrapper, Mapping) else None
                        if isinstance(message, Mapping) and message.get("id") and str(message["id"]) not in seen:
                            seen.add(str(message["id"]))
                            events.append(self._message(connection, context, str(message["id"])))
                for wrapper in history.get("messagesDeleted", []) or []:
                    message = wrapper.get("message") if isinstance(wrapper, Mapping) else None
                    if not isinstance(message, Mapping) or not message.get("id"):
                        continue
                    message_id = str(message["id"])
                    events.append(
                        CaptureEvent.create(
                            connector_id=self.manifest.id,
                            connection_id=connection.connection_id,
                            source_record_id=message_id,
                            source_revision=f"deleted:{history.get('id', latest)}",
                            source_thread_id=str(message.get("threadId") or message_id),
                            kind="email.deleted",
                            occurred_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                            deleted=True,
                        )
                    )
            token = str(page.get("nextPageToken")) if page.get("nextPageToken") else None
            if not token:
                break
        return SyncBatch(events=tuple(events), checkpoint={"history_id": latest}, complete=token is None)

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            value, _ = request_json(
                "GET",
                f"{BASE}/users/{connection.settings.get('user_id', 'me')}/profile",
                headers=self._auth(connection, context),
            )
            return HealthReport(state="healthy", checkpoint={"history_id": value.get("historyId")} if isinstance(value, Mapping) else None)
        except AuthenticationRequired as exc:
            return HealthReport(state="auth_required", error=str(exc))
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
