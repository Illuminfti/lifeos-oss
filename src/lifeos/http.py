"""Small injectable HTTP transport used by provider clients."""
from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lifeos.errors import AuthorizationDenied, ProviderRateLimited, ProviderUnavailable


@dataclass(slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else {}

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse: ...


class JsonHttpClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        form: Mapping[str, Any] | None = None,
        raw_body: bytes | None = None,
        timeout: float = 30.0,
        allowed_statuses: set[int] | None = None,
    ) -> HttpResponse:
        if params:
            pairs: list[tuple[str, Any]] = []
            for key, value in params.items():
                if value is None:
                    continue
                for item in value if isinstance(value, (list, tuple)) else [value]:
                    pairs.append((key, item))
            if pairs:
                url += ("&" if "?" in url else "?") + urlencode(pairs)
        request_headers = {"Accept": "application/json", "User-Agent": "lifeos-oss/0.2"}
        request_headers.update(dict(headers or {}))
        body = raw_body
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif form is not None:
            body = urlencode(form).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = Request(url, data=body, method=method.upper(), headers=request_headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                result = HttpResponse(
                    int(response.status),
                    {k.lower(): v for k, v in response.headers.items()},
                    response.read(),
                )
        except HTTPError as exc:
            result = HttpResponse(int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, exc.read())
        except (URLError, socket.timeout, TimeoutError) as exc:
            raise ProviderUnavailable(f"provider request failed: {type(exc).__name__}") from exc
        if allowed_statuses and result.status in allowed_statuses:
            return result
        if 200 <= result.status < 300:
            return result
        if result.status in {401, 403}:
            raise AuthorizationDenied(f"provider denied request ({result.status})")
        if result.status == 429:
            retry = result.headers.get("retry-after")
            raise ProviderRateLimited(
                "provider rate limited request",
                float(retry) if retry and retry.isdigit() else None,
            )
        raise ProviderUnavailable(f"provider request failed ({result.status}): {result.text[:300]}")
