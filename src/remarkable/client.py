from __future__ import annotations

import json
import logging
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, final

import requests

from remarkable import sync
from remarkable.auth import DEFAULT_CREDENTIALS_PATH, Auth
from remarkable.exceptions import DocumentNotFoundError, SyncProtocolError

log = logging.getLogger(__name__)


MimeType = Literal["application/pdf", "application/epub+zip"]
EntryType = Literal["DocumentType", "CollectionType", "TemplateType"]

UPLOAD_HOST = "https://internal.cloud.remarkable.com"
UPLOAD_URL = f"{UPLOAD_HOST}/doc/v2/files"


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

    def __init__(
        self,
        credentials_path: str | Path = DEFAULT_CREDENTIALS_PATH,
        session: requests.Session | None = None,
    ):
        self.session = session or requests.Session()
        self.auth = Auth(credentials_path=Path(credentials_path), session=self.session)

    def register(self, otp: str) -> None:
        self.auth.register(otp)

    def push_pdf(self, path: str | Path, name: str | None = None) -> str:
        path = Path(path)
        doc = Document(name or path.stem, path.read_bytes(), "application/pdf")
        return self.push_document(doc)

    def push_document(self, doc: Document) -> str:
        user_token = self.auth.get_user_token()
        doc_id = upload_file(self.session, user_token, doc)
        log.info("Uploaded %r as document %s", doc.name, doc_id)
        return doc_id

    def download(self, path: str, dest: str | Path | None = None) -> Path:
        user_token = self.auth.get_user_token()
        entries = list_documents(self.session, user_token)
        entry = resolve_path(entries, path)
        data, ext = download_blob(self.session, user_token, entry)

        out = Path(dest) if dest is not None else Path(f"{entry.visible_name}.{ext}")
        out.write_bytes(data)
        log.info("Downloaded %r (%d bytes) to %s", path, len(data), out)
        return out


def upload_file(session: requests.Session, user_token: str, doc: Document) -> str:
    meta = b64encode(json.dumps({"file_name": doc.name}).encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": doc.mime_type,
        "rm-meta": meta,
        "rm-source": "RoR-Browser",
    }

    resp = session.post(UPLOAD_URL, headers=headers, data=doc.data)
    if resp.status_code not in (200, 201):
        raise SyncProtocolError(f"Upload failed ({resp.status_code}): {resp.text[:300]}")

    try:
        body = resp.json()
        return body["docID"]
    except (ValueError, KeyError) as exc:
        raise SyncProtocolError(f"Unexpected upload response shape: {resp.text[:300]}") from exc


def list_documents(session: requests.Session, user_token: str) -> list[DocumentEntry]:
    root_hash = sync.get_root_hash(session, user_token)
    root_entries = sync.get_entries(session, user_token, sync.ROOT_ID, root_hash)

    entries: list[DocumentEntry] = []
    for root_entry in root_entries:
        doc_schema_id = f"{root_entry.id}.docSchema"
        parts = sync.get_entries(session, user_token, doc_schema_id, root_entry.hash)
        meta_part = next((p for p in parts if p.id.endswith(".metadata")), None)
        if meta_part is None:
            continue

        raw_meta = sync.get_text(session, user_token, meta_part.id, meta_part.hash)
        try:
            meta = json.loads(raw_meta)
            entries.append(
                DocumentEntry(
                    id=root_entry.id,
                    hash=root_entry.hash,
                    visible_name=meta["visibleName"],
                    parent=meta.get("parent", ""),
                    type=meta["type"],
                )
            )
        except (ValueError, KeyError) as exc:
            raise SyncProtocolError(
                f"Unexpected metadata shape for {root_entry.id}: {raw_meta[:300]}"
            ) from exc
    return entries


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


def download_blob(
    session: requests.Session, user_token: str, entry: DocumentEntry
) -> tuple[bytes, str]:
    if entry.type != "DocumentType":
        raise DocumentNotFoundError(f"{entry.visible_name!r} is a folder, not a document")

    doc_schema_id = f"{entry.id}.docSchema"
    parts = sync.get_entries(session, user_token, doc_schema_id, entry.hash)
    for ext in ("pdf", "epub"):
        part = next((p for p in parts if p.id.endswith(f".{ext}")), None)
        if part is not None:
            return sync.get_bytes(session, user_token, part.id, part.hash), ext

    raise DocumentNotFoundError(
        f"{entry.visible_name!r} has no downloadable PDF/EPUB content "
        "(native notebooks aren't supported)"
    )
