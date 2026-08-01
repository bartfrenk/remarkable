from __future__ import annotations

import json
import logging
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, final

import requests

from rmpush.auth import DEFAULT_CREDENTIALS_PATH, Auth
from rmpush.exceptions import SyncProtocolError

log = logging.getLogger(__name__)


MimeType = Literal["application/pdf", "application/epub+zip"]

UPLOAD_HOST = "https://internal.cloud.remarkable.com"
UPLOAD_URL = f"{UPLOAD_HOST}/doc/v2/files"


@dataclass(frozen=True, slots=True)
class Document:
    name: str
    data: bytes
    mime_type: MimeType


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
