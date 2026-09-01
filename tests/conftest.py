from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

import pytest

from lifeos.http import HttpResponse
from lifeos.wiki import init_brain


class FakeHttp:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], deque[Any]] = defaultdict(deque)
        self.calls: list[dict[str, Any]] = []

    def add(self, method: str, url: str, value: Any) -> None:
        self.routes[(method.upper(), url)].append(value)

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        call = {"method": method.upper(), "url": url, **kwargs}
        self.calls.append(call)
        queue = self.routes.get((method.upper(), url))
        if not queue:
            raise AssertionError(f"unexpected HTTP request: {method} {url}")
        value = queue.popleft()
        if callable(value):
            value = value(call)
        if isinstance(value, HttpResponse):
            return value
        if isinstance(value, tuple):
            status, payload = value
        else:
            status, payload = 200, value
        import json

        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return HttpResponse(int(status), {}, body)


def response(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> HttpResponse:
    import json

    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return HttpResponse(status, headers or {}, body)


@pytest.fixture
def brain(tmp_path: Path) -> Path:
    return init_brain(tmp_path / "brain")


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def secret_file(tmp_path: Path):
    def create(value: dict[str, Any], name: str = "secret.json") -> str:
        import json
        import os

        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, 0o600)
        return f"file:{path.resolve()}"

    return create
