"""Builds the set of files reMarkable expects for a single PDF document."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Dict

from pypdf import PdfReader

logger = logging.getLogger("rmpush.document")


def _page_count(pdf_bytes: bytes) -> int:
    try:
        return len(PdfReader(__import__("io").BytesIO(pdf_bytes)).pages)
    except Exception:
        logger.warning("Could not determine PDF page count; defaulting to 1")
        return 1


def _content_json(page_count: int) -> bytes:
    return json.dumps(
        {
            "extraMetadata": {},
            "fileType": "pdf",
            "fontName": "",
            "lastOpenedPage": 0,
            "lineHeight": -1,
            "margins": 100,
            "orientation": "portrait",
            "pageCount": page_count,
            "pages": [],
            "textScale": 1,
            "transform": {
                "m11": 1, "m12": 0, "m13": 0,
                "m21": 0, "m22": 1, "m23": 0,
                "m31": 0, "m32": 0, "m33": 1,
            },
        }
    ).encode("utf-8")


def _metadata_json(visible_name: str) -> bytes:
    return json.dumps(
        {
            "deleted": False,
            "lastModified": str(int(time.time() * 1000)),
            "metadatamodified": True,
            "modified": True,
            "parent": "",
            "pinned": False,
            "synced": False,
            "type": "DocumentType",
            "version": 1,
            "visibleName": visible_name,
        }
    ).encode("utf-8")


@dataclass
class NewDocument:
    doc_id: str
    visible_name: str
    files: Dict[str, bytes]  # filename -> contents, e.g. "<uuid>.pdf"


def build_document(pdf_bytes: bytes, visible_name: str) -> NewDocument:
    doc_id = str(uuid.uuid4())
    page_count = _page_count(pdf_bytes)

    files = {
        f"{doc_id}.content": _content_json(page_count),
        f"{doc_id}.metadata": _metadata_json(visible_name),
        f"{doc_id}.pagedata": b"",
        f"{doc_id}.pdf": pdf_bytes,
    }
    return NewDocument(doc_id=doc_id, visible_name=visible_name, files=files)
