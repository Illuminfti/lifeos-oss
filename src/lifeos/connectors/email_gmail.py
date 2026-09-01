from __future__ import annotations

from email.utils import parseaddr

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import b64url_decode, to_iso
from lifeos.contracts import AttachmentRef, CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import FullResyncRequired
from lifeos.oauth import OAuthTokenProvider

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.email-gmail",
            display_name="Gmail",
            source_classes=["message", "thread", "person", "attachment"],
            capabilities=["backfill", "incremental_sync", "attachments", "deletions", "revoke", "purge"],
            auth_modes=["oauth2"],
            notes="Read-only Gmail API client. No send, draft, modify, or label operations.",
            config_schema={"scopes": ["https://www.googleapis.com/auth/gmail.readonly"]},
        )

    def _auth(self, request):
        secret = self._secret_json(request)
        token = OAuthTokenProvider(self.context.http).access_token(secret)
        return secret, {"Authorization": f"Bearer {token}"}

    def connect(self, request):
        try:
            _, headers = self._auth(request)
            profile = self.context.http.request("GET", f"{API}/profile", headers=headers).json()
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "gmail"),
                state="healthy",
                custody="local",
                scopes=["gmail.readonly"],
                public_config=self._public_config(request),
                provider_identity={"email": profile.get("emailAddress"), "history_id": profile.get("historyId")},
            )
        except Exception as exc:
            return self._auth_failure(exc)

    def _parts(self, payload):
        yield payload
        for part in payload.get("parts") or []:
            yield from self._parts(part)

    def _body(self, payload):
        plain = []
        html = []
        for part in self._parts(payload):
            data = (part.get("body") or {}).get("data")
            if not data:
                continue
            try:
                text = b64url_decode(data).decode("utf-8", errors="replace")
            except Exception:
                continue
            if part.get("mimeType") == "text/plain":
                plain.append(text)
            elif part.get("mimeType") == "text/html":
                html.append(text)
        return "\n".join(plain or html)

    def _message(self, raw, connection):
        payload = raw.get("payload") or {}
        headers = {str(item.get("name", "")).lower(): str(item.get("value", "")) for item in payload.get("headers") or []}
        sender_name, sender_address = parseaddr(headers.get("from", ""))
        attachments = []
        for part in self._parts(payload):
            filename = part.get("filename")
            body = part.get("body") or {}
            attachment_id = body.get("attachmentId")
            if filename or attachment_id:
                attachments.append(
                    AttachmentRef(
                        blob_ref=f"gmail:{raw.get('id')}:{attachment_id or part.get('partId', '')}",
                        mime_type=part.get("mimeType"),
                        size=body.get("size"),
                        name=filename or None,
                    )
                )
        return CaptureEvent.build(
            connector_id=self.manifest.id,
            connection_id=connection,
            source_record_id=str(raw["id"]),
            source_revision=str(raw.get("historyId") or raw.get("internalDate") or ""),
            source_thread_id=str(raw.get("threadId") or raw["id"]),
            kind="message.created",
            occurred_at=to_iso(raw.get("internalDate")),
            text=self._body(payload) or raw.get("snippet", ""),
            actors=[CaptureActor(display_name=sender_name or sender_address or "Unknown sender", provider_ref=sender_address or None, role="sender")],
            attachments=attachments,
            metadata={
                "subject": headers.get("subject"), "to": headers.get("to"), "cc": headers.get("cc"),
                "labels": raw.get("labelIds") or [], "rfc_message_id": headers.get("message-id"),
            },
        )

    def _get(self, message_id, headers, connection):
        raw = self.context.http.request(
            "GET", f"{API}/messages/{message_id}", headers=headers, params={"format": "full"}
        ).json()
        return self._message(raw, connection)

    def backfill(self, request):
        _, headers = self._auth(request)
        config = self._public_config(request)
        connection = self._connection_id(request, "gmail")
        page = None
        events = []
        latest_history = None
        max_pages = int(config.get("max_pages", 20))
        for _ in range(max_pages):
            listing = self.context.http.request(
                "GET",
                f"{API}/messages",
                headers=headers,
                params={"maxResults": min(int(config.get("page_size", 100)), 500), "pageToken": page, "q": config.get("query")},
            ).json()
            for item in listing.get("messages") or []:
                event = self._get(str(item["id"]), headers, connection)
                events.append(event)
                latest_history = max(str(event.source_revision), str(latest_history or ""), key=lambda value: int(value or 0))
            page = listing.get("nextPageToken")
            if not page:
                break
        complete = not bool(page)
        warnings = [] if complete else ["backfill stopped at configured max_pages"]
        if not latest_history:
            profile = self.context.http.request("GET", f"{API}/profile", headers=headers).json()
            latest_history = str(profile.get("historyId") or "")
        return SyncBatch(events=events, checkpoint={"history_id": latest_history}, complete=complete, warnings=warnings)

    def sync(self, request):
        _, headers = self._auth(request)
        connection = self._connection_id(request, "gmail")
        start = str((request.get("checkpoint") or {}).get("history_id") or "")
        if not start:
            raise FullResyncRequired("Gmail history cursor absent; run backfill")
        page = None
        ids = set()
        deleted = []
        latest = start
        for _ in range(100):
            response = self.context.http.request(
                "GET",
                f"{API}/history",
                headers=headers,
                params={"startHistoryId": start, "pageToken": page, "historyTypes": ["messageAdded", "messageDeleted"]},
                allowed_statuses={404},
            )
            if response.status == 404:
                raise FullResyncRequired("Gmail history cursor expired; run backfill")
            data = response.json()
            latest = str(data.get("historyId") or latest)
            for history in data.get("history") or []:
                latest = str(history.get("id") or latest)
                for item in history.get("messagesAdded") or []:
                    ids.add(str((item.get("message") or {}).get("id")))
                for item in history.get("messagesDeleted") or []:
                    message = item.get("message") or {}
                    message_id = str(message.get("id") or "")
                    if message_id:
                        deleted.append(
                            CaptureEvent.build(
                                connector_id=self.manifest.id,
                                connection_id=connection,
                                source_record_id=message_id,
                                source_revision=f"deleted:{latest}",
                                source_thread_id=message.get("threadId"),
                                kind="message.deleted",
                                occurred_at=to_iso(None),
                                deleted=True,
                            )
                        )
            page = data.get("nextPageToken")
            if not page:
                break
        deleted_ids = {event.source_record_id for event in deleted}
        events = [self._get(message_id, headers, connection) for message_id in sorted(ids) if message_id and message_id not in deleted_ids]
        events.extend(deleted)
        return SyncBatch(events=events, checkpoint={"history_id": latest}, complete=not bool(page))

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            _, headers = self._auth(request)
            profile = self.context.http.request("GET", f"{API}/profile", headers=headers).json()
            return HealthReport(state="healthy", details={"email": profile.get("emailAddress")}, checkpoint=request.get("checkpoint") or {})
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
