"""Uploads a single file to reMarkable via the cloud's simple upload API.

This is the same high-level endpoint reMarkable's own web/desktop clients
use for a one-shot "add this PDF" action: a single POST with the raw file
bytes, and the server builds the document's metadata server-side. It's
simpler and less failure-prone than reconstructing the full sync protocol
(content-addressed blob store + hash-tree root index) by hand, and it's
verified against the currently-maintained `rmapi-js` client rather than
reverse-engineered from scratch.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Literal

import requests

from .exceptions import SyncProtocolError

logger = logging.getLogger("rmpush.upload")

UPLOAD_HOST = "https://internal.cloud.remarkable.com"
UPLOAD_URL = f"{UPLOAD_HOST}/doc/v2/files"

UploadMimeType = Literal["application/pdf", "application/epub+zip"]


def upload_file(
    session: requests.Session,
    user_token: str,
    visible_name: str,
    data: bytes,
    mime_type: UploadMimeType = "application/pdf",
) -> str:
    """Upload raw file bytes as a new document. Returns the new document's id."""
    meta = base64.b64encode(json.dumps({"file_name": visible_name}).encode("utf-8")).decode(
        "ascii"
    )
    resp = session.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Content-Type": mime_type,
            "rm-meta": meta,
            "rm-source": "RoR-Browser",
        },
        data=data,
    )
    if resp.status_code not in (200, 201):
        raise SyncProtocolError(f"Upload failed ({resp.status_code}): {resp.text[:300]}")

    try:
        body = resp.json()
        return body["docID"]
    except (ValueError, KeyError) as exc:
        raise SyncProtocolError(f"Unexpected upload response shape: {resp.text[:300]}") from exc
