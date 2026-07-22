from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Union

import requests

from .auth import DEFAULT_CREDENTIALS_PATH, Auth
from .document import build_document
from .exceptions import SyncConflictError
from .sync15 import (
    COLLECTION_ENTRY_TYPE,
    FILE_ENTRY_TYPE,
    MAX_COMMIT_RETRIES,
    Index,
    IndexEntry,
    Sync15Client,
)

logger = logging.getLogger("rmpush.client")


class RemarkableClient:
    """High-level entry point: register once, then push PDFs.

    Example:
        client = RemarkableClient()
        client.register("abcdwxyz")          # one-time pairing, see README
        client.push_pdf("report.pdf")
    """

    def __init__(
        self,
        credentials_path: Union[str, Path] = DEFAULT_CREDENTIALS_PATH,
        session: Optional[requests.Session] = None,
    ):
        self.session = session or requests.Session()
        self.auth = Auth(credentials_path=Path(credentials_path), session=self.session)

    def register(self, one_time_code: str) -> None:
        """Pair this library with your reMarkable account.

        Get a one-time code from https://my.remarkable.com/device/browser/connect
        Only needs to be done once; credentials are cached on disk afterwards.
        """
        self.auth.register(one_time_code)

    def _sync_client(self) -> Sync15Client:
        user_token = self.auth.get_user_token()
        return Sync15Client(
            self.session, user_token, group_hint=self.auth.user_id_claim()
        )

    def push_pdf(self, path: Union[str, Path], visible_name: Optional[str] = None) -> str:
        """Upload a local PDF file to the root of the reMarkable file tree.

        Returns the new document's UUID on success.
        """
        path = Path(path)
        pdf_bytes = path.read_bytes()
        name = visible_name or path.stem
        return self.push_pdf_bytes(pdf_bytes, name)

    def push_pdf_bytes(self, pdf_bytes: bytes, visible_name: str) -> str:
        doc = build_document(pdf_bytes, visible_name)
        sync = self._sync_client()

        # Upload the document's own blobs (content, metadata, pagedata, pdf).
        doc_entries = []
        for filename, contents in doc.files.items():
            blob_hash = sync.put_blob(contents)
            doc_entries.append(
                IndexEntry(
                    hash=blob_hash,
                    type=FILE_ENTRY_TYPE,
                    entry_id=filename,
                    subfiles=0,
                    size=len(contents),
                )
            )
        doc_index = Index(schema_version="3", entries=doc_entries)
        doc_index_bytes = doc_index.render()
        doc_index_hash = sync.put_blob(doc_index_bytes)
        doc_total_size = sum(e.size for e in doc_entries)

        # Optimistic-concurrency commit loop: add one entry to the root
        # index and try to commit it, retrying if the root moved underneath us.
        for attempt in range(1, MAX_COMMIT_RETRIES + 1):
            root, root_index = sync.get_root_index()
            root_index.entries.append(
                IndexEntry(
                    hash=doc_index_hash,
                    type=COLLECTION_ENTRY_TYPE,
                    entry_id=doc.doc_id,
                    subfiles=len(doc_entries),
                    size=doc_total_size,
                )
            )
            new_root_index_bytes = root_index.render()
            new_root_hash = sync.put_blob(new_root_index_bytes)

            try:
                sync.put_root(new_root_hash, root.generation)
                logger.info(
                    "Uploaded %r as document %s (attempt %d)",
                    visible_name,
                    doc.doc_id,
                    attempt,
                )
                return doc.doc_id
            except SyncConflictError:
                logger.info(
                    "Root changed concurrently, retrying (%d/%d)",
                    attempt,
                    MAX_COMMIT_RETRIES,
                )
                time.sleep(0.5 * attempt)

        raise SyncConflictError(
            f"Could not commit new document after {MAX_COMMIT_RETRIES} attempts; "
            "the root kept changing concurrently (e.g. the tablet was syncing)."
        )
