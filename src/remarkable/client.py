from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal, Self, final

import aiohttp
import rmscene
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.pdfgen import canvas as pdfcanvas
from rmc.exporters import svg as rmc_svg
from svglib.svglib import svg2rlg

from remarkable.auth import DEFAULT_CREDENTIALS_PATH, Auth
from remarkable.exceptions import (
    DocumentNotFoundError,
    GenerationConflictError,
    SyncProtocolError,
    UnsupportedFormatError,
)
from remarkable.sync import ROOT_ID, RawEntry, SyncApi

log = logging.getLogger(__name__)


MimeType = Literal["application/pdf", "application/epub+zip"]
EntryType = Literal["DocumentType", "CollectionType", "TemplateType"]
Format = Literal["pdf", "rm"]

# Caps concurrent connections when the client creates its own session, so
# walking a large library doesn't open hundreds of requests at once.
MAX_CONNECTIONS = 16

# How often an edit is re-applied on top of a root another client changed
# while we were writing.
MAX_ROOT_ATTEMPTS = 3

TRASH_ID = "trash"


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


@dataclass(frozen=True, slots=True)
class Replacement:
    doc_id: str
    trashed_ids: list[str]


@final
class RemarkableClient:
    """Async client for the reMarkable Cloud.

    Use as an async context manager so the underlying HTTP session is closed:

        async with RemarkableClient() as client:
            await client.upload_pdf("report.pdf")

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

    async def upload_pdf(
        self, path: str | Path, name: str | None = None, folder: str | None = None
    ) -> str:
        path = Path(path)
        doc = Document(name or path.stem, path.read_bytes(), "application/pdf")
        return await self.upload_document(doc, folder)

    async def replace_pdf(
        self, path: str | Path, name: str | None = None, folder: str | None = None
    ) -> Replacement:
        """Upload a PDF and trash any document with the same name in `folder` (default: root).

        The new document is uploaded before the old one is trashed, so a failure
        never leaves the library without either version.
        """
        path = Path(path)
        doc = Document(name or path.stem, path.read_bytes(), "application/pdf")
        entries = await self.list_documents()
        folder_id = resolve_folder(entries, folder) if folder else None
        existing = [
            e
            for e in entries
            if e.parent == (folder_id or "")
            and e.visible_name == doc.name
            and e.type == "DocumentType"
        ]
        doc_id = await self._upload(doc, folder_id)
        for entry in existing:
            await self._update_metadata(entry.id, {"parent": TRASH_ID})
            log.info("Moved previous %r (%s) to the trash", doc.name, entry.id)
        return Replacement(doc_id, [e.id for e in existing])

    async def upload_document(self, doc: Document, folder: str | None = None) -> str:
        """Upload `doc` to the root, then move it into `folder` (a path) if given."""
        folder_id = resolve_folder(await self.list_documents(), folder) if folder else None
        return await self._upload(doc, folder_id)

    async def _upload(self, doc: Document, folder_id: str | None) -> str:
        doc_id = await self.api.upload(doc.name, doc.data, doc.mime_type)
        log.info("Uploaded %r as document %s", doc.name, doc_id)

        if folder_id is not None:
            await self._update_metadata(doc_id, {"parent": folder_id})
            log.info("Moved %r into folder %s", doc.name, folder_id)
        return doc_id

    async def list_documents(self) -> list[DocumentEntry]:
        root = await self.api.get_root()
        root_entries = await self.api.get_entries(ROOT_ID, root.hash)
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

    async def download(
        self, path: str, dest: str | Path | None = None, fmt: Format | None = None
    ) -> Path:
        """Download a document.

        With no `fmt`, downloads in its native format: PDF/EPUB as-is, native
        notebooks as a `.rmdoc` archive (see `download_notebook`). `fmt="pdf"`
        always returns a PDF (see `download_pdf`). `fmt="rm"` always returns
        the raw `.rmdoc` archive (see `download_notebook`), but raises
        `UnsupportedFormatError` for documents that already have a PDF or
        EPUB payload, since there's no raw notebook form for those.
        """
        if fmt == "pdf":
            return await self.download_pdf(path, dest)
        if fmt == "rm":
            await self._require_notebook(path)
            return await self.download_notebook(path, dest)

        entry = resolve_path(await self.list_documents(), path)
        parts = await self._get_parts(entry)
        for ext in ("pdf", "epub"):
            part = _find_part(parts, ext)
            if part is not None:
                data = await self.api.get_bytes(part.id, part.hash)
                return self._write(path, entry, data, ext, dest)

        data = await self._bundle_rmdoc(parts)
        return self._write(path, entry, data, "rmdoc", dest)

    async def _require_notebook(self, path: str) -> None:
        entry = resolve_path(await self.list_documents(), path)
        parts = await self._get_parts(entry)
        for ext in ("pdf", "epub"):
            if _find_part(parts, ext) is not None:
                raise UnsupportedFormatError(
                    f"{path!r} is a {ext.upper()} document; only format 'pdf' is "
                    "supported for it, not 'rm'"
                )

    async def download_pdf(self, path: str, dest: str | Path | None = None) -> Path:
        """Download a document as PDF.

        Documents with a PDF part download as-is. Native notebooks, which
        have no PDF payload, are rendered page-by-page from their `.rm`
        strokes.
        """
        entry = resolve_path(await self.list_documents(), path)
        parts = await self._get_parts(entry)
        part = _find_part(parts, "pdf")
        if part is not None:
            data = await self.api.get_bytes(part.id, part.hash)
        else:
            data = await self._render_notebook(entry.id, parts)
        return self._write(path, entry, data, "pdf", dest)

    async def download_notebook(self, path: str, dest: str | Path | None = None) -> Path:
        """Download a document's raw sync parts bundled as a `.rmdoc` archive.

        `.rmdoc` is reMarkable's own backup/archive format: a zip of the
        document's `.content`, `.metadata`, `.pagedata` and per-page `.rm`
        files, keyed by the same ids used on the sync API.
        """
        entry = resolve_path(await self.list_documents(), path)
        parts = await self._get_parts(entry)
        data = await self._bundle_rmdoc(parts)
        return self._write(path, entry, data, "rmdoc", dest)

    def _write(
        self, path: str, entry: DocumentEntry, data: bytes, ext: str, dest: str | Path | None
    ) -> Path:
        out = Path(dest) if dest is not None else Path(f"{entry.visible_name}.{ext}")
        out.write_bytes(data)
        log.info("Downloaded %r (%d bytes) to %s", path, len(data), out)
        return out

    async def delete(self, path: str) -> None:
        """Move the document or folder at `path` to the trash, as the tablet does."""
        entry = resolve_path(await self.list_documents(), path)
        await self._update_metadata(entry.id, {"parent": TRASH_ID})
        log.info("Moved %r to the trash", path)

    async def _update_metadata(self, doc_id: str, changes: dict[str, object]) -> None:
        for attempt in range(1, MAX_ROOT_ATTEMPTS + 1):
            root = await self.api.get_root()
            entries = await self.api.get_entries(ROOT_ID, root.hash)
            index = next((i for i, e in enumerate(entries) if e.id == doc_id), None)
            if index is None:
                raise DocumentNotFoundError(f"Document {doc_id} no longer exists")

            entries[index] = await self._rewrite_metadata(
                entries[index], changes, root.schema_version
            )
            # The cloud rejects newly written schema 3 root indexes.
            new_root = await self.api.put_entries("root", entries, schema_version=4)
            try:
                await self.api.put_root(new_root.hash, root.generation)
                return
            except GenerationConflictError:
                if attempt == MAX_ROOT_ATTEMPTS:
                    raise
                log.info("Root changed while editing %s; retrying", doc_id)

    async def _rewrite_metadata(
        self, entry: RawEntry, changes: dict[str, object], schema_version: int
    ) -> RawEntry:
        parts = await self.api.get_entries(f"{entry.id}.docSchema", entry.hash)
        index = next((i for i, p in enumerate(parts) if p.id.endswith(".metadata")), None)
        if index is None:
            raise SyncProtocolError(f"Document {entry.id} has no metadata")

        meta_part = parts[index]
        raw_meta = await self.api.get_text(meta_part.id, meta_part.hash)
        try:
            meta = json.loads(raw_meta)
            meta.update(changes)
            meta["version"] = int(meta.get("version", 0)) + 1
        except (ValueError, TypeError, AttributeError) as exc:
            raise SyncProtocolError(
                f"Unexpected metadata shape for {entry.id}: {raw_meta[:300]}"
            ) from exc
        meta["metadatamodified"] = True

        data = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        parts[index] = await self.api.put_bytes(meta_part.id, data)
        return await self.api.put_entries(entry.id, parts, schema_version)

    async def _get_parts(self, entry: DocumentEntry) -> list[RawEntry]:
        if entry.type != "DocumentType":
            raise DocumentNotFoundError(f"{entry.visible_name!r} is a folder, not a document")
        return await self.api.get_entries(f"{entry.id}.docSchema", entry.hash)

    async def _render_notebook(self, doc_id: str, parts: list[RawEntry]) -> bytes:
        """Render a native notebook's pages to a single PDF.

        Reads the page order from `.content` (preferring the modern `cPages`
        list, falling back to the legacy flat `pages` list, same as
        rmapi-js's `pageOrder`), then renders each page's `.rm` strokes to
        SVG via `rmscene`/`rmc` and composites the pages into one PDF with
        svglib/reportlab. `rmc`'s own PDF export shells out to Inkscape,
        which we avoid by rendering through reportlab directly.
        """
        content_part = _find_part(parts, "content")
        if content_part is None:
            raise SyncProtocolError(f"Document {doc_id} has no .content file")
        raw_content = await self.api.get_text(content_part.id, content_part.hash)
        page_ids: list[str]
        try:
            content = json.loads(raw_content)
            c_pages = content.get("cPages")
            if c_pages:
                # Modern schema: ordered pages, any carrying a "deleted" key are gone.
                page_ids = [p["id"] for p in c_pages["pages"] if "deleted" not in p]
            else:
                # Legacy schema: a flat, already-ordered list of page ids.
                page_ids = content.get("pages") or []
        except (ValueError, KeyError, TypeError) as exc:
            raise SyncProtocolError(
                f"Unexpected .content shape for {doc_id}: {raw_content[:300]}"
            ) from exc

        by_id = {part.id: part for part in parts}
        page_parts = [
            by_id[page_file_id]
            for page_id in page_ids
            if (page_file_id := f"{doc_id}/{page_id}.rm") in by_id
        ]
        if not page_parts:
            raise DocumentNotFoundError(f"Document {doc_id} has no renderable pages")

        pages = await asyncio.gather(*(self.api.get_bytes(p.id, p.hash) for p in page_parts))
        return await asyncio.to_thread(_render_pdf, pages)

    async def _bundle_rmdoc(self, parts: list[RawEntry]) -> bytes:
        """Zip a document's raw parts into a `.rmdoc` archive.

        Used for native notebooks, which have no rendered PDF/EPUB payload to
        download directly. `.rmdoc` is reMarkable's own backup/archive format:
        a zip of the document's `.content`, `.metadata`, `.pagedata` and
        per-page `.rm` files, keyed by the same ids used on the sync API.
        """
        contents = await asyncio.gather(*(self.api.get_bytes(p.id, p.hash) for p in parts))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for part, data in zip(parts, contents):
                archive.writestr(part.id, data)
        return buffer.getvalue()


def _find_part(parts: list[RawEntry], ext: str) -> RawEntry | None:
    return next((p for p in parts if p.id.endswith(f".{ext}")), None)


def _render_pdf(pages: list[bytes]) -> bytes:
    """Render a notebook's pages (raw `.rm` bytes, in order) to a PDF."""
    drawings = [_page_drawing(data) for data in pages]
    buffer = io.BytesIO()
    canvas_ = pdfcanvas.Canvas(buffer, pagesize=(drawings[0].width, drawings[0].height))
    for drawing in drawings:
        canvas_.setPageSize((drawing.width, drawing.height))
        renderPDF.draw(drawing, canvas_, 0, 0)
        canvas_.showPage()
    canvas_.save()
    return buffer.getvalue()


def _page_drawing(data: bytes) -> Drawing:
    tree = rmscene.read_tree(io.BytesIO(data))
    svg_text = io.StringIO()
    rmc_svg.tree_to_svg(tree, svg_text)  # pyright: ignore[reportUnknownMemberType]
    drawing = svg2rlg(io.BytesIO(svg_text.getvalue().encode("utf-8")))
    if drawing is None:
        raise SyncProtocolError("Could not parse rendered notebook page SVG")
    return drawing


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


def resolve_folder(entries: list[DocumentEntry], path: str) -> str:
    entry = resolve_path(entries, path)
    if entry.type != "CollectionType":
        raise DocumentNotFoundError(f"{path!r} is not a folder")
    return entry.id
