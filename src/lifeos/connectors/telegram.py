from __future__ import annotations

import asyncio
from pathlib import Path

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import to_iso
from lifeos.contracts import AttachmentRef, CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, ConfigurationError


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.telegram",
            display_name="Telegram",
            source_classes=["message", "thread", "person", "group", "channel", "attachment"],
            capabilities=["backfill", "incremental_sync", "attachments", "deletions", "revoke", "purge"],
            auth_modes=["interactive_user"],
            notes="Telethon user client for owner-authorized chat history. A Bot API token cannot substitute for personal history. No send operations.",
        )

    def _secret(self, request):
        value = self._secret_json(request)
        if not value.get("api_id") or not value.get("api_hash"):
            raise AuthenticationRequired("Telegram secret requires api_id and api_hash")
        if not value.get("session") and not value.get("session_file"):
            raise AuthenticationRequired("Telegram secret requires a StringSession or private session_file")
        return value

    @staticmethod
    def _telethon():
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            return TelegramClient, StringSession
        except ImportError as exc:
            raise ConfigurationError("install lifeos[telegram] to use Telegram") from exc

    @staticmethod
    def _run(coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        raise ConfigurationError("Telegram sync cannot run inside an existing event loop; use the CLI or daemon boundary")

    def _client(self, secret):
        TelegramClient, StringSession = self._telethon()
        session = StringSession(str(secret["session"])) if secret.get("session") else str(Path(str(secret["session_file"])).expanduser())
        return TelegramClient(session, int(secret["api_id"]), str(secret["api_hash"]), receive_updates=False)

    async def _identity(self, request):
        secret = self._secret(request)
        client = self._client(secret)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                if secret.get("interactive") and secret.get("phone"):
                    await client.start(phone=str(secret["phone"]))
                else:
                    raise AuthenticationRequired(
                        "Telegram session is not authorized; create it interactively in the private secret location"
                    )
            owner = await client.get_me()
            return {
                "id": str(owner.id),
                "username": getattr(owner, "username", None),
                "name": " ".join(
                    value for value in [getattr(owner, "first_name", None), getattr(owner, "last_name", None)] if value
                ),
            }
        finally:
            await client.disconnect()

    def connect(self, request):
        try:
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "telegram"),
                state="healthy",
                public_config=self._public_config(request),
                provider_identity=self._run(self._identity(request)),
            )
        except Exception as exc:
            return self._auth_failure(exc)

    async def _collect(self, request, incremental):
        secret = self._secret(request)
        config = self._public_config(request)
        connection = self._connection_id(request, "telegram")
        client = self._client(secret)
        await client.connect()
        events = []
        new = {}
        old = (request.get("checkpoint") or {}).get("dialogs") or {}
        warnings = []
        try:
            if not await client.is_user_authorized():
                raise AuthenticationRequired("Telegram session is not authorized")
            dialog_filter = {str(value) for value in config.get("dialog_ids") or []}
            dialog_limit = int(config.get("dialog_limit", 200))
            message_limit = int(config.get("message_limit_per_dialog", 500))
            overlap = int(config.get("overlap_messages", 20))
            async for dialog in client.iter_dialogs(limit=dialog_limit):
                dialog_id = str(dialog.id)
                if dialog_filter and dialog_id not in dialog_filter:
                    continue
                prior = int((old.get(dialog_id) or {}).get("last_message_id", 0))
                minimum_id = max(0, prior - overlap) if incremental else 0
                last = prior
                seen = 0
                async for message in client.iter_messages(dialog.entity, limit=message_limit, min_id=minimum_id, reverse=True):
                    if not getattr(message, "id", None):
                        continue
                    sender = await message.get_sender()
                    sender_id = str(getattr(sender, "id", "")) if sender else ""
                    sender_name = (
                        " ".join(
                            value for value in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if value
                        )
                        or getattr(sender, "title", None)
                        or getattr(sender, "username", None)
                        or sender_id
                        or "Unknown sender"
                    )
                    media = getattr(message, "file", None)
                    attachments = []
                    if media:
                        attachments.append(
                            AttachmentRef(
                                blob_ref=f"telegram:{dialog_id}:{message.id}:media",
                                mime_type=getattr(media, "mime_type", None),
                                size=getattr(media, "size", None),
                                name=getattr(media, "name", None),
                            )
                        )
                    events.append(
                        CaptureEvent.build(
                            connector_id=self.manifest.id,
                            connection_id=connection,
                            source_record_id=f"{dialog_id}:{message.id}",
                            source_revision=to_iso(getattr(message, "edit_date", None) or getattr(message, "date", None)),
                            source_thread_id=dialog_id,
                            kind="message.updated" if getattr(message, "edit_date", None) else "message.created",
                            occurred_at=to_iso(getattr(message, "date", None)),
                            text=str(getattr(message, "message", None) or ""),
                            actors=[CaptureActor(display_name=str(sender_name), provider_ref=sender_id or None, role="sender")],
                            attachments=attachments,
                            metadata={
                                "dialog_id": dialog_id,
                                "dialog_title": str(getattr(dialog, "name", None) or ""),
                                "message_id": message.id,
                                "reply_to": getattr(message, "reply_to_msg_id", None),
                                "outgoing": bool(getattr(message, "out", False)),
                            },
                        )
                    )
                    last = max(last, int(message.id))
                    seen += 1
                new[dialog_id] = {"last_message_id": last}
                if seen >= message_limit:
                    warnings.append(f"dialog {dialog_id} hit message_limit_per_dialog")
            return SyncBatch(events=events, checkpoint={"dialogs": new}, complete=not warnings, warnings=warnings)
        finally:
            await client.disconnect()

    def backfill(self, request):
        return self._run(self._collect(request, False))

    def sync(self, request):
        return self._run(self._collect(request, True))

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            return HealthReport(
                state="healthy",
                details=self._run(self._identity(request)),
                checkpoint=request.get("checkpoint") or {},
            )
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
