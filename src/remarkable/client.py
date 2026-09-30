from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, final

import aiohttp

from remarkable.auth import DEFAULT_CREDENTIALS_PATH, Auth
from remarkable.exceptions import DocumentNotFoundError, SyncProtocolError
from remarkable.sync import ROOT_ID, RawEntry, SyncApi

log = logging.getLogger(__name__)


MimeType = Literal["application/pdf", "application/epub+zip"]
EntryType = Literal["DocumentType", "CollectionType", "TemplateType"]

# Caps concurrent connections when the client creates its own session, so
# walking a large library doesn't open hundreds of requests at once.
MAX_CONNECTIONS = 16


@dataclass(frozen=True, slots=True)
class Document:
    name: str
    data: bytes
    mime_type: MimeType


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    id: str
    hash: str
    visible_name: str
    parent: str
    type: EntryType


@final
class RemarkableClient:
    """Async client for the reMarkable Cloud.

    Use as an async context manager so the underlying HTTP session is closed:

        async with RemarkableClient() as client:
            await client.push_pdf("report.pdf")

    Construct it inside a running event loop (aiohttp requires one). If you
    pass your own `session`, you remain responsible for closing it.
    """

    def __init__(
        self,
        credentials_path: str | Path = DEFAULT_CREDENTIALS_PATH,
        session: aiohttp.ClientSession | None = None,
    ):
        self._owns_session = session is None
        self.session = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=MAX_CONNECTIONS)
        )
        self.auth = Auth(self.session, credentials_path=Path(credentials_path))
        self.api = SyncApi(self.session, self.auth)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session:
            await self.session.close()

    async def register(self, otp: str) -> None:
        await self.auth.register(otp)

    async def push_pdf(self, path: str | Path, name: str | None = None) -> str:
        path = Path(path)
        doc = Document(name or path.stem, path.read_bytes(), "application/pdf")
        return await self.push_document(doc)

    async def push_document(self, doc: Document) -> str:
        doc_id = await self.api.upload(doc.name, doc.data, doc.mime_type)
        log.info("Uploaded %r as document %s", doc.name, doc_id)
        return doc_id

    async def list_documents(self) -> list[DocumentEntry]:
        root_hash = await self.api.root_hash()
        root_entries = await self.api.get_entries(ROOT_ID, root_hash)
        results = await asyncio.gather(*(self._load_entry(e) for e in root_entries))
        return [entry for entry in results if entry is not None]

    async def _load_entry(self, root_entry: RawEntry) -> DocumentEntry | None:
        parts = await self.api.get_entries(f"{root_entry.id}.docSchema", root_entry.hash)
        meta_part = next((p for p in parts if p.id.endswith(".metadata")), None)
        if meta_part is None:
            return None

        raw_meta = await self.api.get_text(meta_part.id, meta_part.hash)
        try:
            meta = json.loads(raw_meta)
            return DocumentEntry(
                id=root_entry.id,
                hash=root_entry.hash,
                visible_name=meta["visibleName"],
                parent=meta.get("parent", ""),
                type=meta["type"],
            )
        except (ValueError, KeyError) as exc:
            raise SyncProtocolError(
                f"Unexpected metadata shape for {root_entry.id}: {raw_meta[:300]}"
            ) from exc

    async def download(self, path: str, dest: str | Path | None = None) -> Path:
        entry = resolve_path(await self.list_documents(), path)
        data, ext = await self._download_blob(entry)

        out = Path(dest) if dest is not None else Path(f"{entry.visible_name}.{ext}")
        out.write_bytes(data)
        log.info("Downloaded %r (%d bytes) to %s", path, len(data), out)
        return out

    async def _download_blob(self, entry: DocumentEntry) -> tuple[bytes, str]:
        if entry.type != "DocumentType":
            raise DocumentNotFoundError(f"{entry.visible_name!r} is a folder, not a document")

        parts = await self.api.get_entries(f"{entry.id}.docSchema", entry.hash)
        for ext in ("pdf", "epub"):
            part = next((p for p in parts if p.id.endswith(f".{ext}")), None)
            if part is not None:
                return await self.api.get_bytes(part.id, part.hash), ext

        raise DocumentNotFoundError(
            f"{entry.visible_name!r} has no downloadable PDF/EPUB content "
            "(native notebooks aren't supported)"
        )


def resolve_path(entries: list[DocumentEntry], path: str) -> DocumentEntry:
    segments = [s for s in path.strip("/").split("/") if s]
    if not segments:
        raise DocumentNotFoundError("Path must name a document, not the root")

    parent = ""
    entry: DocumentEntry | None = None
    for i, segment in enumerate(segments):
        candidates = [e for e in entries if e.parent == parent and e.visible_name == segment]
        if not candidates:
            raise DocumentNotFoundError(f"No such path: {path!r} (missing {segment!r})")
        entry = candidates[0]

        is_last = i == len(segments) - 1
        if not is_last:
            if entry.type != "CollectionType":
                raise DocumentNotFoundError(f"{'/'.join(segments[: i + 1])!r} is not a folder")
            parent = entry.id

    assert entry is not None
    return entry
