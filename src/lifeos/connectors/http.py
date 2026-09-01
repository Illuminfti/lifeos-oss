"""Small provider-neutral HTTP helpers for connector plugins."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lifeos.connectors.base import ConnectorContext
from lifeos.contracts import Connection
from lifeos.errors import AuthenticationRequired, ConnectorError, RateLimited


def request_json(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    body: Mapping[str, Any] | None = None,
    form: Mapping[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[Any, Mapping[str, str]]:
    if params:
        clean: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                clean.extend((key, str(item)) for item in value)
            else:
                clean.append((key, str(value)))
        url += ("&" if "?" in url else "?") + urlencode(clean)
    payload: bytes | None = None
    outgoing = {"Accept": "application/json", "User-Agent": "lifeos/0.2", **dict(headers or {})}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        outgoing.setdefault("Content-Type", "application/json")
    elif form is not None:
        payload = urlencode({key: str(value) for key, value in form.items() if value is not None}).encode("utf-8")
        outgoing.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = Request(url, data=payload, headers=outgoing, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if not raw:
                return {}, dict(response.headers.items())
            return json.loads(raw.decode("utf-8")), dict(response.headers.items())
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            detail = ""
        if exc.code in {401, 403}:
            raise AuthenticationRequired(f"HTTP {exc.code}: {detail}") from exc
        if exc.code == 429:
            raise RateLimited(f"HTTP 429: {detail}") from exc
        raise ConnectorError(f"HTTP {exc.code} from provider: {detail}") from exc
    except URLError as exc:
        raise ConnectorError(f"provider network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"provider returned invalid JSON: {exc}") from exc


def _expired(secret: Mapping[str, Any]) -> bool:
    expires_at = secret.get("expires_at")
    if not expires_at:
        return False
    try:
        text = str(expires_at).replace("Z", "+00:00")
        return datetime.fromisoformat(text) <= datetime.now(timezone.utc) + timedelta(seconds=30)
    except ValueError:
        return False


def oauth_access_token(
    connection: Connection,
    context: ConnectorContext,
    *,
    token_url: str,
) -> str:
    secret = context.secret_for(connection)
    token = secret.get("access_token")
    if token and not _expired(secret):
        return str(token)
    refresh_token = secret.get("refresh_token")
    client_id = secret.get("client_id")
    client_secret = secret.get("client_secret")
    if not refresh_token or not client_id:
        if token:
            return str(token)
        raise AuthenticationRequired("OAuth access token is missing")
    payload, _ = request_json(
        "POST",
        token_url,
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if not isinstance(payload, Mapping) or not payload.get("access_token"):
        raise AuthenticationRequired("OAuth refresh did not return an access token")
    updated = dict(secret)
    updated["access_token"] = payload["access_token"]
    if payload.get("refresh_token"):
        updated["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        updated["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=int(payload["expires_in"]))
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    if connection.secret_ref:
        context.secrets.update(connection.secret_ref, updated)
    return str(updated["access_token"])


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
