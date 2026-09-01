"""Telegram user-history connector backed by Telethon.

A normal Telegram bot cannot pull an account's existing personal chats. This
connector uses an explicitly authorized user session and stores that session
below the private `.lifeos/` state directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, utc_now
from lifeos.errors import AuthenticationRequired, ConfigurationError, ConnectorError


class TelegramConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.telegram",
        display_name="Telegram user history",
        source_classes=("message", "thread", "person", "group", "channel"),
        capabilities=("backfill", "incremental_sync", "attachments_metadata", "revoke", "purge"),
        auth_modes=("interactive_user",),
        custody="local",
        implementation_status="experimental",
        notes="Real Telethon adapter. Live account behavior requires owner credentials and provider validation.",
    )

    @staticmethod
    def _telethon() -> Any:
        try:
            from telethon.sync import TelegramClient
        except ImportError as exc:
            raise ConnectorError("Telegram support requires `pip install lifeos[telegram]`") from exc
        return TelegramClient

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping):
            raise ConfigurationError(
                "telegram requires secret JSON containing api_id, api_hash, and phone"
            )
        missing = [key for key in ("api_id", "api_hash", "phone") if not secret.get(key)]
        if missing:
            raise ConfigurationError("telegram secret is missing: " + ", ".join(missing))
        connection_id = "con_" + uuid4().hex
        session_dir = context.config.state_dir / "connectors" / "telegram"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / connection_id
        settings = {
            "session_path": str(session_path),
            "dialog_limit": int(request.get("dialog_limit", 100)),
            "message_limit": int(request.get("message_limit", 1000)),
            "download_media": False,
        }
        if bool(request.get("authorize", False)):
            client_cls = self._telethon()
            client = client_cls(str(session_path), int(secret["api_id"]), str(secret["api_hash"]))
            try:
                client.start(phone=str(secret["phone"]))
                if not client.is_user_authorized():
                    raise AuthenticationRequired("Telegram authorization did not complete")
            finally:
                client.disconnect()
        return ConnectResult(
            connection_id=connection_id,
            settings=settings,
            granted_scopes=("telegram:messages:read", "telegram:dialogs:read"),
            secret_payload=dict(secret),
        )

    def _client(self, connection: Connection, context: ConnectorContext) -> Any:
        secret = context.secret_for(connection)
        if not secret.get("api_id") or not secret.get("api_hash"):
            raise AuthenticationRequired("Telegram API credentials are missing")
        client_cls = self._telethon()
        return client_cls(
            str(connection.settings["session_path"]),
            int(secret["api_id"]),
            str(secret["api_hash"]),
        )

    @staticmethod
    def _sender(message: Any) -> Actor | None:
        sender = message.get_sender()
        if sender is None:
            return None
        provider_id = getattr(sender, "id", None)
        first = getattr(sender, "first_name", None)
        last = getattr(sender, "last_name", None)
        title = getattr(sender, "title", None)
        username = getattr(sender, "username", None)
        display = " ".join(part for part in (first, last) if part) or title or username or str(provider_id)
        return Actor(
            provider_ref=f"telegram:{provider_id}",
            display_name=str(display),
            kind="person" if first or last or username else "organization",
            metadata={"username": username} if username else {},
        )

    @staticmethod
    def _event(connection: Connection, dialog: Any, message: Any) -> CaptureEvent:
        chat_id = str(getattr(dialog, "id", getattr(message, "chat_id", "unknown")))
        message_id = str(getattr(message, "id"))
        actor = TelegramConnector._sender(message)
        edit_date = getattr(message, "edit_date", None)
        date = getattr(message, "date", None)
        occurred = (date.isoformat() if date else utc_now()).replace("+00:00", "Z")
        revision = edit_date.isoformat() if edit_date else message_id
        text = str(getattr(message, "message", None) or getattr(message, "raw_text", None) or "")
        metadata = {
            "dialog_name": str(getattr(dialog, "name", "")),
            "chat_id": chat_id,
            "message_id": message_id,
            "outgoing": bool(getattr(message, "out", False)),
            "has_media": getattr(message, "media", None) is not None,
            "reply_to_message_id": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
        }
        return CaptureEvent.create(
            connector_id=TelegramConnector.manifest.id,
            connection_id=connection.connection_id,
            source_record_id=f"{chat_id}:{message_id}",
            source_revision=revision,
            source_thread_id=f"telegram:{chat_id}",
            kind="message.updated" if edit_date else "message.created",
            occurred_at=occurred,
            actors=(actor,) if actor else (),
            text=text,
            metadata=metadata,
        )

    def _collect(
        self,
        connection: Connection,
        checkpoint: Mapping[str, Any],
        context: ConnectorContext,
        *,
        initial: bool,
    ) -> SyncBatch:
        client = self._client(connection, context)
        events: list[CaptureEvent] = []
        last_ids = {str(key): int(value) for key, value in dict(checkpoint.get("last_ids", {})).items()}
        warnings: list[str] = []
        try:
            client.connect()
            if not client.is_user_authorized():
                raise AuthenticationRequired(
                    "Telegram session is not authorized; reconnect with authorize=true"
                )
            dialogs = client.iter_dialogs(limit=int(connection.settings.get("dialog_limit", 100)))
            for dialog in dialogs:
                chat_id = str(getattr(dialog, "id", "unknown"))
                min_id = 0 if initial else last_ids.get(chat_id, 0)
                try:
                    messages = list(
                        client.iter_messages(
                            dialog.entity,
                            limit=int(connection.settings.get("message_limit", 1000)),
                            min_id=min_id,
                            reverse=True,
                        )
                    )
                except Exception as exc:
                    warnings.append(f"dialog {chat_id}: {type(exc).__name__}")
                    continue
                for message in messages:
                    events.append(self._event(connection, dialog, message))
                    last_ids[chat_id] = max(last_ids.get(chat_id, 0), int(message.id))
        except AuthenticationRequired:
            raise
        except Exception as exc:
            raise ConnectorError(f"Telegram sync failed: {type(exc).__name__}: {exc}") from exc
        finally:
            try:
                client.disconnect()
            except Exception:
                pass
        return SyncBatch(
            events=tuple(events),
            checkpoint={"last_ids": last_ids, "synced_at": utc_now(), "initial_complete": initial or bool(checkpoint.get("initial_complete"))},
            warnings=tuple(warnings),
        )

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._collect(connection, checkpoint, context, initial=True)

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._collect(connection, checkpoint, context, initial=False)

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            client = self._client(connection, context)
            client.connect()
            authorized = bool(client.is_user_authorized())
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
        except Exception as exc:
            return HealthReport(state="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                client.disconnect()  # type: ignore[possibly-undefined]
            except Exception:
                pass
        return HealthReport(state="healthy" if authorized else "auth_required")

    def revoke(self, connection: Connection, context: ConnectorContext) -> Mapping[str, Any]:
        session = Path(str(connection.settings.get("session_path", "")))
        deleted = 0
        for candidate in (session, session.with_suffix(".session"), session.with_suffix(".session-journal")):
            if candidate.is_file():
                candidate.unlink(missing_ok=True)
                deleted += 1
        return {"ok": True, "local_session_files_deleted": deleted, "remote_revocation": "not_requested"}
