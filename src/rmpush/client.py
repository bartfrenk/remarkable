from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import requests

from .auth import DEFAULT_CREDENTIALS_PATH, Auth
from .upload import upload_file

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

    def push_pdf(self, path: Union[str, Path], visible_name: Optional[str] = None) -> str:
        """Upload a local PDF file to the root of the reMarkable file tree.

        Returns the new document's UUID on success.
        """
        path = Path(path)
        pdf_bytes = path.read_bytes()
        name = visible_name or path.stem
        return self.push_pdf_bytes(pdf_bytes, name)

    def push_pdf_bytes(self, pdf_bytes: bytes, visible_name: str) -> str:
        user_token = self.auth.get_user_token()
        doc_id = upload_file(self.session, user_token, visible_name, pdf_bytes, "application/pdf")
        logger.info("Uploaded %r as document %s", visible_name, doc_id)
        return doc_id
