"""A minimal stand-in for `aiohttp.ClientSession` used by the tests.

`aioresponses` is incompatible with aiohttp >= 3.14, so tests register canned
responses on `FakeSession` and inspect the requests it recorded instead.
`add_library` serves a one-document library ("/MyDoc") over the sync API;
`add_documents` serves arbitrary documents and folders.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import cast

import aiohttp
import pytest

from remarkable.client import RemarkableClient
from remarkable.sync import RAW_HOST


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
    data: bytes | None


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
        payload: object = None,
        repeat: bool = False,
    ) -> None:
        """Queue a response; a `url` ending in "*" matches any URL with that prefix."""
        if payload is not None:
            body = json.dumps(payload)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._routes[(method, url)].append((FakeResponse(status, body), repeat))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> FakeResponse:
        self.requests.append(SentRequest(method, url, dict(headers or {}), data))
        queue = self._routes.get((method, url))
        if not queue:
            queue = next(
                (
                    q
                    for (m, pattern), q in self._routes.items()
                    if q and m == method and pattern.endswith("*") and url.startswith(pattern[:-1])
                ),
                None,
            )
        if not queue:
            raise AssertionError(f"Unexpected request: {method} {url}")
        response, repeat = queue[0]
        if not repeat:
            queue.popleft()
        return response

    def post(
        self, url: str, *, headers: dict[str, str] | None = None, data: bytes | None = None
    ) -> FakeResponse:
        return self.request("POST", url, headers=headers, data=data)

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> FakeResponse:
        return self.request("GET", url, headers=headers)

    def as_client_session(self) -> aiohttp.ClientSession:
        """This object, typed as the `aiohttp.ClientSession` it duck-types."""
        return cast(aiohttp.ClientSession, cast(object, self))


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


def make_client(session: FakeSession, tokens: list[str]) -> RemarkableClient:
    """A client whose Auth hands out `tokens`, advancing on each forced refresh."""
    client = RemarkableClient(credentials_path="/nonexistent", session=session.as_client_session())
    remaining = iter(tokens)
    current = next(remaining)

    async def get_user_token(force: bool = False) -> str:
        nonlocal current
        if force:
            current = next(remaining)
        return current

    client.auth.get_user_token = get_user_token  # type: ignore[method-assign]
    return client


DOC_ID = "doc-uuid"
ROOT_HASH = hashlib.sha256(b"root").hexdigest()
DOC_HASH = hashlib.sha256(b"doc").hexdigest()
META_HASH = hashlib.sha256(b"meta").hexdigest()
PDF_HASH = hashlib.sha256(b"pdf").hexdigest()
FILES_URL = f"{RAW_HOST}/sync/v3/files"
ROOT_PUT_URL = f"{RAW_HOST}/sync/v3/root"


def index_text(id: str, entries: list[tuple[str, str, int, int]], schema: int) -> str:
    lines = [str(schema)]
    if schema == 4:
        lines.append(f"0:{id}:{len(entries)}:{sum(size for *_, size in entries)}")
    lines += [f"{hash}:0:{id}:{subfiles}:{size}" for hash, id, subfiles, size in entries]
    return "\n".join(lines) + "\n"


def add_library(session: FakeSession, schema: int, generations: list[int]) -> None:
    for generation in generations:
        session.add(
            "GET",
            f"{RAW_HOST}/sync/v4/root",
            payload={"hash": ROOT_HASH, "generation": generation, "schemaVersion": schema},
        )
    session.add(
        "GET",
        f"{FILES_URL}/{ROOT_HASH}",
        body=index_text(".", [(DOC_HASH, DOC_ID, 2, 110)], schema),
        repeat=True,
    )
    session.add(
        "GET",
        f"{FILES_URL}/{DOC_HASH}",
        body=index_text(
            DOC_ID,
            [(META_HASH, f"{DOC_ID}.metadata", 0, 10), (PDF_HASH, f"{DOC_ID}.pdf", 0, 100)],
            schema,
        ),
        repeat=True,
    )
    session.add(
        "GET",
        f"{FILES_URL}/{META_HASH}",
        payload={"visibleName": "MyDoc", "parent": "", "type": "DocumentType", "version": 2},
        repeat=True,
    )
    session.add("PUT", f"{FILES_URL}/*", repeat=True)


def uploaded_blobs(session: FakeSession) -> dict[str, tuple[str, bytes]]:
    """Uploaded (hash, bytes) by rm-filename."""
    blobs: dict[str, tuple[str, bytes]] = {}
    for r in session.requests:
        if r.method == "PUT" and r.url.startswith(FILES_URL):
            assert r.data is not None
            blobs[r.headers["rm-filename"]] = (r.url.rsplit("/", 1)[1], r.data)
    return blobs


def _flat_hash(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _flat_index_text(id: str, entries: list[tuple[str, str]]) -> str:
    lines = ["4", f"0:{id}:{len(entries)}:{10 * len(entries)}"]
    lines += [f"{hash}:0:{entry_id}:1:10" for hash, entry_id in entries]
    return "\n".join(lines) + "\n"


def add_documents(session: FakeSession, docs: dict[str, dict[str, str]]) -> None:
    """A schema 4 library holding `docs` (id -> metadata), served as often as asked."""
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v4/root",
        payload={"hash": _flat_hash("root"), "generation": 7, "schemaVersion": 4},
        repeat=True,
    )
    session.add(
        "GET",
        f"{FILES_URL}/{_flat_hash('root')}",
        body=_flat_index_text(".", [(_flat_hash(id), id) for id in docs]),
        repeat=True,
    )
    for id, meta in docs.items():
        session.add(
            "GET",
            f"{FILES_URL}/{_flat_hash(id)}",
            body=_flat_index_text(id, [(_flat_hash(f"{id}.metadata"), f"{id}.metadata")]),
            repeat=True,
        )
        session.add(
            "GET", f"{FILES_URL}/{_flat_hash(f'{id}.metadata')}", payload=meta, repeat=True
        )
    session.add("PUT", f"{FILES_URL}/*", repeat=True)
