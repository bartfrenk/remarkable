"""reMarkable Cloud HTTP API: uploads and content-addressed sync reads.

reMarkable stores files in a Merkle-tree-like structure: a root hash points
to an index file listing top-level document/collection ids and their own
hashes; each of those points to another index file (that document's
"docSchema") listing its constituent parts (.metadata, .content, .pdf, ...)
by hash. Fetching a file's bytes means walking down this tree by hash.

Uploads go through a one-shot "verified upload API"; reads have no equivalent
endpoint, so `SyncApi` exposes just enough of the tree-walking protocol for
`client.py` to resolve a path and fetch its content. `SyncApi` is the single
place where HTTP requests are made and authenticated. Endpoints and formats
were cross-checked against the `rmapi-js` client
(https://github.com/erikbrinkman/rmapi-js), the same reference this project's
auth code follows.
"""

from __future__ import annotations

import json
import logging
from base64 import b64encode
from dataclasses import dataclass
from typing import Any, final

import requests

from remarkable.auth import Auth
from remarkable.exceptions import SyncProtocolError

log = logging.getLogger(__name__)

RAW_HOST = "https://eu.tectonic.remarkable.com"
UPLOAD_HOST = "https://internal.cloud.remarkable.com"
UPLOAD_URL = f"{UPLOAD_HOST}/doc/v2/files"
ROOT_ID = "root.docSchema"


@dataclass(frozen=True, slots=True)
class RawEntry:
    hash: str
    id: str
    subfiles: int
    size: int


def parse_entries(text: str) -> list[RawEntry]:
    lines = text.strip("\n").split("\n")
    version, *lines = lines
    if version == "4":
        # schema 4 has an extra "0:id:count:size" info line before entries
        _info, *lines = lines
    elif version != "3":
        raise SyncProtocolError(f"Unsupported sync schema version: {version!r}")

    entries: list[RawEntry] = []
    for line in lines:
        try:
            hash, _type, id, subfiles, size = line.split(":")
            entries.append(RawEntry(hash=hash, id=id, subfiles=int(subfiles), size=int(size)))
        except ValueError as exc:
            raise SyncProtocolError(f"Malformed sync index line: {line!r}") from exc
    return entries


@final
class SyncApi:
    """All HTTP calls to the reMarkable Cloud, authenticated via `Auth`."""

    def __init__(self, session: requests.Session, auth: Auth):
        self.session = session
        self.auth = auth

    def _request(
        self,
        method: str,
        url: str,
        what: str,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        def send(force_refresh: bool) -> requests.Response:
            token = self.auth.get_user_token(force=force_refresh)
            all_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
            return self.session.request(method, url, headers=all_headers, **kwargs)

        resp = send(force_refresh=False)
        if resp.status_code == 401:
            log.info("Got 401 for %s; retrying with a fresh user token", what)
            resp = send(force_refresh=True)
        if not resp.ok:
            raise SyncProtocolError(f"{what} failed ({resp.status_code}): {resp.text[:300]}")
        return resp

    def root_hash(self) -> str:
        resp = self._request("GET", f"{RAW_HOST}/sync/v4/root", "Fetching root hash")
        try:
            return str(resp.json()["hash"])
        except (ValueError, KeyError) as exc:
            raise SyncProtocolError(f"Unexpected root response shape: {resp.text[:300]}") from exc

    def get_bytes(self, file_id: str, hash: str) -> bytes:
        resp = self._request(
            "GET",
            f"{RAW_HOST}/sync/v3/files/{hash}",
            f"Fetching {file_id!r}",
            headers={"rm-filename": file_id},
        )
        return resp.content

    def get_text(self, file_id: str, hash: str) -> str:
        return self.get_bytes(file_id, hash).decode("utf-8")

    def get_entries(self, file_id: str, hash: str) -> list[RawEntry]:
        return parse_entries(self.get_text(file_id, hash))

    def upload(self, name: str, data: bytes, mime_type: str) -> str:
        meta = b64encode(json.dumps({"file_name": name}).encode("utf-8")).decode("ascii")
        resp = self._request(
            "POST",
            UPLOAD_URL,
            f"Uploading {name!r}",
            headers={"Content-Type": mime_type, "rm-meta": meta, "rm-source": "RoR-Browser"},
            data=data,
        )
        try:
            return resp.json()["docID"]
        except (ValueError, KeyError) as exc:
            raise SyncProtocolError(f"Unexpected upload response shape: {resp.text[:300]}") from exc
