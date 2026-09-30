"""A minimal stand-in for `aiohttp.ClientSession` used by the tests.

`aioresponses` is incompatible with aiohttp >= 3.14, so tests register canned
responses on `FakeSession` and inspect the requests it recorded instead.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import pytest

from remarkable.client import RemarkableClient


@dataclass
class FakeResponse:
    status: int = 200
    body: bytes = b""

    async def read(self) -> bytes:
        return self.body

    async def text(self) -> str:
        return self.body.decode("utf-8")

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass


@dataclass
class SentRequest:
    method: str
    url: str
    headers: dict[str, str]
    data: Any


@dataclass
class FakeSession:
    requests: list[SentRequest] = field(default_factory=list)
    _routes: dict[tuple[str, str], deque[tuple[FakeResponse, bool]]] = field(
        default_factory=lambda: defaultdict(deque)
    )

    def add(
        self,
        method: str,
        url: str,
        *,
        status: int = 200,
        body: bytes | str = b"",
        payload: Any = None,
        repeat: bool = False,
    ) -> None:
        if payload is not None:
            body = json.dumps(payload)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._routes[(method, url)].append((FakeResponse(status, body), repeat))

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append(
            SentRequest(method, url, dict(kwargs.get("headers") or {}), kwargs.get("data"))
        )
        queue = self._routes.get((method, url))
        if not queue:
            raise AssertionError(f"Unexpected request: {method} {url}")
        response, repeat = queue[0]
        if not repeat:
            queue.popleft()
        return response

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


def make_client(session: FakeSession, tokens: list[str]) -> RemarkableClient:
    """A client whose Auth hands out `tokens`, advancing on each forced refresh."""
    client = RemarkableClient(credentials_path="/nonexistent", session=session)  # type: ignore[arg-type]
    remaining = iter(tokens)
    current = next(remaining)

    async def get_user_token(force: bool = False) -> str:
        nonlocal current
        if force:
            current = next(remaining)
        return current

    client.auth.get_user_token = get_user_token  # type: ignore[method-assign]
    return client
