from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lifeos.errors import AuthenticationRequired, AuthorizationDenied
from lifeos.http import HttpTransport


class OAuthTokenProvider:
    def __init__(self, http: HttpTransport):
        self.http = http

    def access_token(self, secret: dict[str, Any]) -> str:
        token = str(secret.get("access_token") or "")
        if token and not self._expired(secret.get("expires_at")):
            return token
        if not all([secret.get("refresh_token"), secret.get("client_id"), secret.get("token_uri")]):
            raise AuthenticationRequired("access token absent/expired and refresh credentials incomplete")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": secret["refresh_token"],
            "client_id": secret["client_id"],
        }
        if secret.get("client_secret"):
            form["client_secret"] = secret["client_secret"]
        payload = self.http.request("POST", str(secret["token_uri"]), form=form).json()
        refreshed = str(payload.get("access_token") or "")
        if not refreshed:
            raise AuthorizationDenied("token endpoint returned no access_token")
        return refreshed

    @staticmethod
    def _expired(value: Any) -> bool:
        if not value:
            return False
        try:
            if isinstance(value, (int, float)):
                return float(value) <= datetime.now(timezone.utc).timestamp() + 60
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() <= datetime.now(timezone.utc).timestamp() + 60
        except (ValueError, TypeError):
            return True
