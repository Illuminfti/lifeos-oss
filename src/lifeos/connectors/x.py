from __future__ import annotations

from lifeos.connectors.base import BasePlugin, ConnectorContext
from lifeos.connectors.utils import to_iso
from lifeos.contracts import CaptureActor, CaptureEvent, ConnectionReceipt, ConnectorManifest, HealthReport, SyncBatch
from lifeos.errors import AuthenticationRequired, AuthorizationDenied


class Plugin(BasePlugin):
    def __init__(self, context: ConnectorContext | None = None):
        super().__init__(context)
        self.manifest = ConnectorManifest(
            id="org.lifeos.x",
            display_name="X",
            source_classes=["post", "mention", "direct_message", "person", "conversation"],
            capabilities=["backfill", "incremental_sync", "deletions", "revoke", "purge"],
            auth_modes=["oauth2_user"],
            custody="third_party",
            notes="Read-only X API client. Posting, replying, liking, following, and deleting are absent.",
        )

    def _settings(self, request):
        secret = self._secret_json(request)
        token = str(secret.get("access_token") or "")
        if not token:
            raise AuthenticationRequired("X secret requires a user access_token")
        config = self._public_config(request)
        base = str(config.get("base_url") or "https://api.x.com/2").rstrip("/")
        return secret, config, base, {"Authorization": f"Bearer {token}"}

    def connect(self, request):
        try:
            _, config, base, headers = self._settings(request)
            data = self.context.http.request(
                "GET", f"{base}/users/me", headers=headers, params={"user.fields": "id,name,username,created_at"}
            ).json().get("data") or {}
            if not data.get("id"):
                raise AuthenticationRequired("X /users/me returned no user")
            scopes = [str(value) for value in config.get("scopes") or ["tweet.read", "users.read", "offline.access"]]
            return ConnectionReceipt(
                ok=True,
                connection_id=self._connection_id(request, "x"),
                state="healthy",
                custody="third_party",
                scopes=scopes,
                public_config=config,
                provider_identity={"id": data.get("id"), "username": data.get("username"), "name": data.get("name")},
            )
        except Exception as exc:
            return self._auth_failure(exc)

    def _posts(self, endpoint, kind, request, headers, connection, since_id=None):
        config = self._public_config(request)
        events = []
        token = None
        latest = since_id
        warnings = []
        for _ in range(int(config.get("max_pages", 20))):
            params = {
                "max_results": max(5, min(int(config.get("page_size", 100)), 100)),
                "pagination_token": token,
                "since_id": since_id,
                "tweet.fields": "id,text,author_id,conversation_id,created_at,edit_history_tweet_ids,attachments,referenced_tweets",
                "expansions": "author_id",
                "user.fields": "id,name,username",
            }
            data = self.context.http.request("GET", endpoint, headers=headers, params=params).json()
            users = {str(user.get("id")): user for user in (data.get("includes") or {}).get("users") or []}
            for post in data.get("data") or []:
                post_id = str(post["id"])
                author = users.get(str(post.get("author_id"))) or {}
                history = post.get("edit_history_tweet_ids") or []
                source_revision = str((history[-1] if history else post.get("created_at")) or post_id)
                events.append(
                    CaptureEvent.build(
                        connector_id=self.manifest.id,
                        connection_id=connection,
                        source_record_id=f"{kind}:{post_id}",
                        source_revision=source_revision,
                        source_thread_id=str(post.get("conversation_id") or post_id),
                        kind=f"x.{kind}",
                        occurred_at=to_iso(post.get("created_at")),
                        text=str(post.get("text") or ""),
                        actors=[
                            CaptureActor(
                                display_name=str(author.get("name") or author.get("username") or post.get("author_id") or "Unknown user"),
                                provider_ref=str(post.get("author_id") or "") or None,
                                role="author",
                            )
                        ],
                        metadata={"post": post, "username": author.get("username")},
                    )
                )
                latest = max(str(latest or "0"), post_id, key=lambda value: int(value))
            token = (data.get("meta") or {}).get("next_token")
            if not token:
                break
        if token:
            warnings.append(f"X {kind} stopped at configured max_pages")
        return events, latest, warnings

    def _dms(self, request, headers, connection, since_id=None):
        config = self._public_config(request)
        events = []
        token = None
        latest = since_id
        warnings = []
        for _ in range(int(config.get("max_pages", 20))):
            params = {
                "max_results": min(int(config.get("dm_page_size", 100)), 100),
                "pagination_token": token,
                "event_types": "MessageCreate",
                "dm_event.fields": "id,text,event_type,created_at,sender_id,dm_conversation_id,attachments",
                "expansions": "sender_id",
                "user.fields": "id,name,username",
            }
            data = self.context.http.request(
                "GET", str(config.get("dm_endpoint") or "https://api.x.com/2/dm_events"), headers=headers, params=params
            ).json()
            users = {str(user.get("id")): user for user in (data.get("includes") or {}).get("users") or []}
            for direct_message in data.get("data") or []:
                message_id = str(direct_message["id"])
                if since_id and int(message_id) <= int(since_id):
                    continue
                author = users.get(str(direct_message.get("sender_id"))) or {}
                events.append(
                    CaptureEvent.build(
                        connector_id=self.manifest.id,
                        connection_id=connection,
                        source_record_id=f"dm:{message_id}",
                        source_revision=message_id,
                        source_thread_id=str(direct_message.get("dm_conversation_id") or message_id),
                        kind="x.direct_message",
                        occurred_at=to_iso(direct_message.get("created_at")),
                        text=str(direct_message.get("text") or ""),
                        actors=[
                            CaptureActor(
                                display_name=str(author.get("name") or author.get("username") or direct_message.get("sender_id") or "Unknown user"),
                                provider_ref=str(direct_message.get("sender_id") or "") or None,
                                role="sender",
                            )
                        ],
                        metadata={"dm": direct_message, "username": author.get("username")},
                    )
                )
                latest = max(str(latest or "0"), message_id, key=lambda value: int(value))
            token = (data.get("meta") or {}).get("next_token")
            if not token:
                break
        if token:
            warnings.append("X direct messages stopped at configured max_pages")
        return events, latest, warnings

    def _read(self, request, incremental):
        _, config, base, headers = self._settings(request)
        owner = self.context.http.request("GET", f"{base}/users/me", headers=headers).json().get("data") or {}
        user_id = str(owner.get("id") or "")
        if not user_id:
            raise AuthenticationRequired("X user identity unavailable")
        old = request.get("checkpoint") or {}
        new = {}
        events = []
        warnings = []
        connection = self._connection_id(request, "x")
        streams = [
            ("post", f"{base}/users/{user_id}/tweets", "posts"),
            ("mention", f"{base}/users/{user_id}/mentions", "mentions"),
        ]
        for kind, endpoint, key in streams:
            if config.get(f"include_{key}", True):
                try:
                    found, latest, stream_warnings = self._posts(
                        endpoint, kind, request, headers, connection, old.get(key) if incremental else None
                    )
                    events.extend(found)
                    warnings.extend(stream_warnings)
                    new[key] = latest
                except AuthorizationDenied as exc:
                    warnings.append(f"X {key} unavailable for granted tier/scopes: {exc}")
        if config.get("include_direct_messages", False):
            try:
                found, latest, stream_warnings = self._dms(
                    request, headers, connection, old.get("direct_messages") if incremental else None
                )
                events.extend(found)
                warnings.extend(stream_warnings)
                new["direct_messages"] = latest
            except AuthorizationDenied as exc:
                warnings.append(f"X direct messages unavailable for granted tier/scopes: {exc}")
        return SyncBatch(events=events, checkpoint={**old, **new}, complete=not warnings, warnings=warnings)

    def backfill(self, request):
        return self._read(request, False)

    def sync(self, request):
        return self._read(request, True)

    def health(self, request=None):
        if not request:
            return HealthReport(state="disconnected")
        try:
            _, _, base, headers = self._settings(request)
            data = self.context.http.request("GET", f"{base}/users/me", headers=headers).json().get("data") or {}
            return HealthReport(
                state="healthy",
                details={"id": data.get("id"), "username": data.get("username")},
                checkpoint=request.get("checkpoint") or {},
            )
        except Exception as exc:
            return HealthReport(state="auth_required", error=str(exc))
