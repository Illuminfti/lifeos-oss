"""Generic read-only IMAP connector."""
from __future__ import annotations

from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from hashlib import sha256
import imaplib
from typing import Any, Iterable, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, ConfigurationError, ConnectorError


class ImapConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.email-imap",
        display_name="IMAP email",
        source_classes=("email", "thread", "person", "attachment_metadata"),
        capabilities=("backfill", "incremental_sync", "revoke", "purge"),
        auth_modes=("password", "oauth2"),
        custody="local",
        implementation_status="experimental",
        notes="Read-only IMAP adapter using UID checkpoints. Live servers vary and require provider validation.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping) or not secret.get("username") or not (secret.get("password") or secret.get("access_token")):
            raise ConfigurationError("IMAP requires username plus password or access_token in secret JSON")
        host = str(request.get("host", "")).strip()
        if not host:
            raise ConfigurationError("IMAP requires host")
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "host": host,
                "port": int(request.get("port", 993)),
                "ssl": bool(request.get("ssl", True)),
                "mailbox": str(request.get("mailbox", "INBOX")),
                "backfill_limit": max(1, int(request.get("backfill_limit", 1000))),
            },
            granted_scopes=("email:read",),
            secret_payload=dict(secret),
        )

    def _open(self, connection: Connection, context: ConnectorContext) -> imaplib.IMAP4:
        settings = connection.settings
        try:
            if settings.get("ssl", True):
                client: imaplib.IMAP4 = imaplib.IMAP4_SSL(str(settings["host"]), int(settings.get("port", 993)))
            else:
                client = imaplib.IMAP4(str(settings["host"]), int(settings.get("port", 143)))
            secret = context.secret_for(connection)
            username = str(secret["username"])
            if secret.get("access_token"):
                token = str(secret["access_token"])
                auth = f"user={username}\x01auth=Bearer {token}\x01\x01".encode()
                client.authenticate("XOAUTH2", lambda _: auth)
            else:
                client.login(username, str(secret["password"]))
            status, _ = client.select(str(settings.get("mailbox", "INBOX")), readonly=True)
            if status != "OK":
                raise ConnectorError("IMAP mailbox selection failed")
            return client
        except imaplib.IMAP4.error as exc:
            raise AuthenticationRequired(f"IMAP authentication failed: {exc}") from exc
        except OSError as exc:
            raise ConnectorError(f"IMAP connection failed: {exc}") from exc

    @staticmethod
    def _uidvalidity(client: imaplib.IMAP4) -> str:
        response = client.response("UIDVALIDITY")
        if response and response[1] and response[1][0]:
            value = response[1][0]
            return value.decode() if isinstance(value, bytes) else str(value)
        return "unknown"

    @staticmethod
    def _text(message: Message) -> tuple[str, list[str]]:
        text_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[str] = []
        parts: Iterable[Message] = message.walk() if message.is_multipart() else (message,)
        for part in parts:
            disposition = part.get_content_disposition()
            filename = part.get_filename()
            if filename:
                attachments.append(filename)
            if disposition == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                value = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                value = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            (text_parts if content_type == "text/plain" else html_parts).append(str(value))
        return "\n".join(text_parts or html_parts), attachments

    def _event(self, connection: Connection, uid: str, raw: bytes) -> CaptureEvent:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        sender_name, sender_address = parseaddr(str(message.get("From", "")))
        date_header = str(message.get("Date", ""))
        try:
            occurred = parsedate_to_datetime(date_header)
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            occurred_at = occurred.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:
            occurred_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        body, attachments = self._text(message)
        subject = str(message.get("Subject", ""))
        message_id = str(message.get("Message-ID", "")).strip() or f"uid:{uid}"
        references = str(message.get("References", "")).split()
        in_reply_to = str(message.get("In-Reply-To", "")).strip()
        thread = references[0] if references else in_reply_to or message_id
        digest = "sha256:" + sha256(raw).hexdigest()
        return CaptureEvent.create(
            connector_id=self.manifest.id,
            connection_id=connection.connection_id,
            source_record_id=uid,
            source_revision=digest,
            source_thread_id=thread,
            kind="email.message",
            occurred_at=occurred_at,
            actors=(Actor(provider_ref=f"email:{sender_address.lower()}", display_name=sender_name or sender_address),) if sender_address else (),
            text=f"Subject: {subject}\n\n{body}".strip(),
            metadata={
                "message_id": message_id,
                "subject": subject,
                "from": str(message.get("From", "")),
                "to": str(message.get("To", "")),
                "cc": str(message.get("Cc", "")),
                "attachment_names": attachments,
            },
        )

    def _fetch_uids(self, client: imaplib.IMAP4, uids: list[str], connection: Connection) -> list[CaptureEvent]:
        events: list[CaptureEvent] = []
        for uid in uids:
            status, data = client.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not data:
                continue
            raw = next((item[1] for item in data if isinstance(item, tuple) and isinstance(item[1], bytes)), None)
            if raw:
                events.append(self._event(connection, uid, raw))
        return events

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        client = self._open(connection, context)
        try:
            status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise ConnectorError("IMAP search failed")
            all_uids = data[0].decode().split() if data and data[0] else []
            limit = int(connection.settings.get("backfill_limit", 1000))
            selected = all_uids[-limit:]
            events = self._fetch_uids(client, selected, connection)
            last_uid = max((int(uid) for uid in all_uids), default=0)
            return SyncBatch(
                events=tuple(events),
                checkpoint={"uidvalidity": self._uidvalidity(client), "last_uid": last_uid},
                complete=len(all_uids) <= limit,
                warnings=(f"limited backfill to newest {limit} messages",) if len(all_uids) > limit else (),
            )
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        if not checkpoint.get("last_uid"):
            return self.backfill(connection, checkpoint, context)
        client = self._open(connection, context)
        try:
            validity = self._uidvalidity(client)
            if checkpoint.get("uidvalidity") not in {None, "unknown", validity}:
                return self.backfill(connection, {}, context)
            start = int(checkpoint.get("last_uid", 0)) + 1
            status, data = client.uid("search", None, f"UID {start}:*")
            if status != "OK":
                raise ConnectorError("IMAP incremental search failed")
            uids = data[0].decode().split() if data and data[0] else []
            events = self._fetch_uids(client, uids, connection)
            last_uid = max([int(checkpoint.get("last_uid", 0)), *[int(uid) for uid in uids]])
            return SyncBatch(events=tuple(events), checkpoint={"uidvalidity": validity, "last_uid": last_uid})
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            client = self._open(connection, context)
            status, _ = client.noop()
            return HealthReport(state="healthy" if status == "OK" else "degraded")
        except AuthenticationRequired as exc:
            return HealthReport(state="auth_required", error=str(exc))
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
        finally:
            try:
                client.logout()  # type: ignore[possibly-undefined]
            except Exception:
                pass
