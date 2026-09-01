from __future__ import annotations

from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from hashlib import sha256
import imaplib

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import to_iso
from lifeos.contracts import AttachmentRef, CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, ConfigurationError, ProviderUnavailable


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.email-imap",
            display_name="IMAP email",
            source_classes=["message", "thread", "person", "attachment"],
            capabilities=["backfill", "incremental_sync", "attachments", "revoke", "purge"],
            auth_modes=["password", "app_password"],
            notes="Read-only IMAP client using mailbox EXAMINE and BODY.PEEK. No send, delete, move, or flag mutations.",
        )

    def _secret(self, request):
        secret = self._secret_json(request)
        for key in ["host", "username", "password"]:
            if not secret.get(key):
                raise AuthenticationRequired(f"IMAP secret requires {key}")
        return secret

    @contextmanager
    def _client(self, request):
        secret = self._secret(request)
        client = None
        try:
            if secret.get("ssl", True):
                client = imaplib.IMAP4_SSL(
                    str(secret["host"]), int(secret.get("port", 993)), timeout=float(secret.get("timeout", 30))
                )
            else:
                client = imaplib.IMAP4(
                    str(secret["host"]), int(secret.get("port", 143)), timeout=float(secret.get("timeout", 30))
                )
            status, _ = client.login(str(secret["username"]), str(secret["password"]))
            if status != "OK":
                raise AuthenticationRequired("IMAP login failed")
            yield client, secret
        except imaplib.IMAP4.error as exc:
            raise AuthenticationRequired(f"IMAP authentication/protocol failure: {exc}") from exc
        except OSError as exc:
            raise ProviderUnavailable(f"IMAP connection failed: {exc}") from exc
        finally:
            if client:
                try:
                    client.logout()
                except Exception:
                    pass

    def connect(self, request):
        try:
            with self._client(request) as (client, secret):
                status, _ = client.select(str(self._public_config(request).get("folder") or "INBOX"), readonly=True)
                if status != "OK":
                    raise ConfigurationError("IMAP folder unavailable")
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "imap"),
                state="healthy",
                public_config=self._public_config(request),
                provider_identity={"username": secret["username"], "host": secret["host"]},
            )
        except Exception as exc:
            return self._auth_failure(exc)

    @staticmethod
    def _extract_text(message):
        if message.is_multipart():
            values = []
            for part in message.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                if part.get_content_type() == "text/plain":
                    try:
                        values.append(part.get_content())
                    except Exception:
                        pass
            if values:
                return "\n".join(str(value) for value in values)
        try:
            return str(message.get_content())
        except Exception:
            return ""

    def _event(self, raw, folder, uid, uidvalidity, connection):
        message = BytesParser(policy=policy.default).parsebytes(raw)
        sender_name, sender_address = parseaddr(str(message.get("From") or ""))
        attachments = []
        for part in message.walk():
            filename = part.get_filename()
            if filename:
                attachments.append(
                    AttachmentRef(
                        blob_ref=f"imap:{folder}:{uid}:{part.get('Content-ID') or filename}",
                        mime_type=part.get_content_type(),
                        size=len(part.get_payload(decode=True) or b""),
                        name=filename,
                    )
                )
        try:
            occurred = to_iso(parsedate_to_datetime(str(message.get("Date"))).isoformat())
        except Exception:
            occurred = to_iso(None)
        record = f"{folder}:{uidvalidity}:{uid}"
        return CaptureEvent.build(
            connector_id=self.manifest.id,
            connection_id=connection,
            source_record_id=record,
            source_revision=sha256(raw).hexdigest(),
            source_thread_id=str(message.get("Message-ID") or message.get("References") or record),
            kind="message.created",
            occurred_at=occurred,
            text=self._extract_text(message),
            actors=[CaptureActor(display_name=sender_name or sender_address or "Unknown sender", provider_ref=sender_address or None, role="sender")],
            attachments=attachments,
            metadata={
                "folder": folder, "uid": uid, "uidvalidity": uidvalidity,
                "subject": str(message.get("Subject") or ""), "to": str(message.get("To") or ""),
                "cc": str(message.get("Cc") or ""), "message_id": str(message.get("Message-ID") or ""),
            },
        )

    def _read(self, request, incremental):
        config = self._public_config(request)
        folders = [str(value) for value in config.get("folders") or [config.get("folder") or "INBOX"]]
        old = (request.get("checkpoint") or {}).get("folders") or {}
        new = {}
        events = []
        warnings = []
        connection = self._connection_id(request, "imap")
        with self._client(request) as (client, _):
            for folder in folders:
                status, _ = client.select(folder, readonly=True)
                if status != "OK":
                    warnings.append(f"folder unavailable: {folder}")
                    continue
                _, response = client.response("UIDVALIDITY")
                first = (response or [b"unknown"])[0]
                uidvalidity = str(first.decode() if isinstance(first, bytes) else first)
                prior = old.get(folder) or {}
                last_uid = int(prior.get("last_uid", 0)) if prior.get("uidvalidity") == uidvalidity else 0
                criterion = f"UID {last_uid + 1}:*" if incremental and last_uid else "ALL"
                status, data = client.uid("search", None, criterion)
                if status != "OK":
                    warnings.append(f"search failed: {folder}")
                    continue
                uids = [int(value) for value in (data[0] or b"").split() if value.isdigit()]
                uids = uids[-int(config.get("max_messages", 5000)):]
                for uid in uids:
                    status, parts = client.uid("fetch", str(uid), "(BODY.PEEK[])")
                    if status != "OK":
                        continue
                    raw = next((part[1] for part in parts if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
                    if raw:
                        events.append(self._event(raw, folder, uid, uidvalidity, connection))
                new[folder] = {"uidvalidity": uidvalidity, "last_uid": max(uids or [last_uid])}
                if incremental:
                    warnings.append(f"{folder}: expunge detection is not claimed without QRESYNC; new UIDs were imported")
        return SyncBatch(
            events=events,
            checkpoint={"folders": new},
            complete=not any("unavailable" in warning or "failed" in warning for warning in warnings),
            warnings=warnings,
        )

    def backfill(self, request):
        return self._read(request, False)

    def sync(self, request):
        return self._read(request, True)

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            with self._client(request) as (client, secret):
                status, _ = client.noop()
                return HealthReport(
                    state="healthy" if status == "OK" else "degraded",
                    details={"host": secret["host"], "username": secret["username"]},
                )
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
