"""X API v2 read-only capture connector."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from lifeos.connectors.base import BaseConnector, ConnectorContext
from lifeos.connectors.http import bearer_headers, request_json
from lifeos.contracts import Actor, CaptureEvent, ConnectResult, Connection, ConnectorManifest, HealthReport, SyncBatch, content_digest, ensure_iso8601
from lifeos.errors import AuthenticationRequired, ConfigurationError, ConnectorError

BASE = "https://api.x.com/2"


class XConnector(BaseConnector):
    manifest = ConnectorManifest(
        id="org.lifeos.x",
        display_name="X",
        source_classes=("post", "mention", "person"),
        capabilities=("backfill", "incremental_sync", "revoke", "purge"),
        auth_modes=("oauth2_user", "bearer_token"),
        custody="local",
        implementation_status="experimental",
        notes="Read-only X API v2 polling adapter. Availability, history depth, and cost depend on the owner's X access tier.",
    )

    def connect(self, request: Mapping[str, Any], context: ConnectorContext) -> ConnectResult:
        secret = request.get("secret")
        if not isinstance(secret, Mapping) or not (secret.get("access_token") or secret.get("bearer_token")):
            raise ConfigurationError("X requires access_token or bearer_token in secret JSON")
        user_id = str(request.get("user_id", ""))
        if not user_id and secret.get("bearer_token") and not secret.get("access_token"):
            raise ConfigurationError("X bearer-token connections require explicit user_id")
        streams = request.get("streams", ["timeline", "mentions"])
        if isinstance(streams, str):
            streams = [streams]
        invalid = set(streams) - {"timeline", "mentions"}
        if invalid:
            raise ConfigurationError("unsupported X streams: " + ", ".join(sorted(invalid)))
        return ConnectResult(
            connection_id="con_" + uuid4().hex,
            settings={
                "user_id": user_id,
                "streams": [str(value) for value in streams],
                "page_size": min(100, max(5, int(request.get("page_size", 100)))),
                "max_pages": min(1000, max(1, int(request.get("max_pages", 10)))),
            },
            granted_scopes=tuple(str(x) for x in request.get("scopes", ["tweet.read", "users.read", "offline.access"])),
            secret_payload=dict(secret),
        )

    def _token(self, connection: Connection, context: ConnectorContext) -> str:
        secret = context.secret_for(connection)
        token = secret.get("access_token") or secret.get("bearer_token")
        if not token:
            raise AuthenticationRequired("X token is missing")
        return str(token)

    def _user_id(self, connection: Connection, context: ConnectorContext) -> str:
        configured = str(connection.settings.get("user_id", ""))
        if configured:
            return configured
        payload, _ = request_json("GET", BASE + "/users/me", headers=bearer_headers(self._token(connection, context)), params={"user.fields": "id,name,username"})
        if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping) or not payload["data"].get("id"):
            raise ConnectorError("X did not return the authenticated user id")
        return str(payload["data"]["id"])

    @staticmethod
    def _event(connection: Connection, stream: str, post: Mapping[str, Any], users: Mapping[str, Mapping[str, Any]]) -> CaptureEvent:
        author_id = str(post.get("author_id", ""))
        user = users.get(author_id, {})
        username = str(user.get("username") or author_id)
        display = str(user.get("name") or username or "Unknown X user")
        created = str(post.get("created_at") or datetime.now(timezone.utc).isoformat())
        try:
            occurred = ensure_iso8601(created)
        except ValueError:
            occurred = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        post_id = str(post.get("id") or content_digest(post))
        revisions = post.get("edit_history_tweet_ids") or []
        revision = str(revisions[-1]) if revisions else str(post.get("edit_controls") or content_digest(post))
        conversation = str(post.get("conversation_id") or post_id)
        return CaptureEvent.create(
            connector_id=XConnector.manifest.id,
            connection_id=connection.connection_id,
            source_record_id=post_id,
            source_revision=revision,
            source_thread_id=f"x:{conversation}",
            kind="x.mention" if stream == "mentions" else "x.post",
            occurred_at=occurred,
            actors=(Actor(provider_ref=f"x:{author_id}", display_name=display, metadata={"username": username}),) if author_id else (),
            text=str(post.get("text", "")),
            raw=post,
            metadata={"stream": stream, "conversation_id": conversation, "public_metrics": post.get("public_metrics")},
        )

    def _stream(
        self,
        connection: Connection,
        context: ConnectorContext,
        stream: str,
        since_id: str | None,
    ) -> tuple[list[CaptureEvent], str | None, bool]:
        user_id = self._user_id(connection, context)
        endpoint = f"/users/{user_id}/mentions" if stream == "mentions" else f"/users/{user_id}/tweets"
        pagination: str | None = None
        events: list[CaptureEvent] = []
        newest = since_id
        complete = True
        for _ in range(int(connection.settings.get("max_pages", 10))):
            payload, _ = request_json(
                "GET",
                BASE + endpoint,
                headers=bearer_headers(self._token(connection, context)),
                params={
                    "max_results": connection.settings.get("page_size", 100),
                    "since_id": since_id,
                    "pagination_token": pagination,
                    "tweet.fields": "id,text,author_id,created_at,conversation_id,edit_history_tweet_ids,public_metrics",
                    "expansions": "author_id",
                    "user.fields": "id,name,username",
                },
            )
            if not isinstance(payload, Mapping):
                raise ConnectorError("X stream response is malformed")
            includes = payload.get("includes") if isinstance(payload.get("includes"), Mapping) else {}
            users = {
                str(user.get("id")): user
                for user in includes.get("users", []) or []
                if isinstance(user, Mapping) and user.get("id")
            }
            for post in payload.get("data", []) or []:
                if not isinstance(post, Mapping):
                    continue
                events.append(self._event(connection, stream, post, users))
                post_id = str(post.get("id", ""))
                if post_id.isdigit() and (not newest or not newest.isdigit() or int(post_id) > int(newest)):
                    newest = post_id
            meta = payload.get("meta") if isinstance(payload.get("meta"), Mapping) else {}
            pagination = str(meta.get("next_token")) if meta.get("next_token") else None
            if not pagination:
                break
        if pagination:
            complete = False
        return events, newest, complete

    def _collect(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        previous = {str(k): str(v) for k, v in dict(checkpoint.get("since_ids", {})).items()}
        next_ids = dict(previous)
        events: list[CaptureEvent] = []
        complete = True
        warnings: list[str] = []
        for stream in connection.settings.get("streams", ["timeline", "mentions"]):
            try:
                stream_events, newest, done = self._stream(connection, context, str(stream), previous.get(str(stream)))
            except ConnectorError as exc:
                warnings.append(f"{stream}: {exc}")
                complete = False
                continue
            events.extend(stream_events)
            if newest:
                next_ids[str(stream)] = newest
            complete = complete and done
        return SyncBatch(events=tuple(events), checkpoint={"since_ids": next_ids}, complete=complete, warnings=tuple(warnings))

    def backfill(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._collect(connection, {}, context)

    def sync(self, connection: Connection, checkpoint: Mapping[str, Any], context: ConnectorContext) -> SyncBatch:
        return self._collect(connection, checkpoint, context)

    def health(self, connection: Connection | None, context: ConnectorContext) -> HealthReport:
        if connection is None:
            return HealthReport(state="disconnected")
        try:
            user_id = self._user_id(connection, context)
            payload, headers = request_json("GET", BASE + f"/users/{user_id}", headers=bearer_headers(self._token(connection, context)), params={"user.fields": "id,name,username"})
            details = {"user_available": isinstance(payload, Mapping)}
            for key in ("x-rate-limit-remaining", "x-rate-limit-reset", "x-rate-limit-limit"):
                if key in {k.lower(): v for k, v in headers.items()}:
                    details[key] = {k.lower(): v for k, v in headers.items()}[key]
            return HealthReport(state="healthy", details=details)
        except AuthenticationRequired as exc:
            return HealthReport(state="auth_required", error=str(exc))
        except ConnectorError as exc:
            return HealthReport(state="failed", error=str(exc))
