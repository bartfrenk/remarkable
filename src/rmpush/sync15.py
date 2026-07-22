"""Low-level implementation of reMarkable's "sync15" cloud storage protocol.

IMPORTANT: this protocol is unofficial, undocumented, and reverse-engineered
by the open-source community (see the `rmapi` and `rmapy` projects). It has
changed shape in the past and may change again. This module isolates all of
that risk in one place so it's easy to audit or patch if reMarkable changes
something.

Conceptually the cloud store is a content-addressed blob store plus a single
mutable "root" pointer, structured as a two-level Merkle tree:

    root (hash, generation)
      -> root index file (text, one line per top-level item)
           <hash>:80000000:<uuid>:<subfile_count>:<size>   (a document)
             -> that document's own index file (text)
                  <hash>:0:<uuid>.content:0:<size>
                  <hash>:0:<uuid>.metadata:0:<size>
                  <hash>:0:<uuid>.pagedata:0:<size>
                  <hash>:0:<uuid>.pdf:0:<size>

Writes are optimistic: you read the current root generation, build a new
root index that includes your addition, upload the new blobs, then attempt
to commit the new root conditioned on the generation you read. If the
generation moved (e.g. the tablet synced something in the meantime) the
commit is rejected and the caller should retry from the top. Existing
entries are only ever copied through unchanged, never rewritten or removed,
so a bug here should at worst fail to add a document -- it should not be
able to corrupt or delete anything that was already on the account.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote

import requests

from .exceptions import SyncConflictError, SyncProtocolError

logger = logging.getLogger("rmpush.sync15")

SERVICE_DISCOVERY_URL = (
    "https://service-manager-production-dot-remarkable-production.appspot.com"
    "/service/json/1/document-storage"
)

# Used if service discovery fails or returns something unexpected.
FALLBACK_STORAGE_HOST = "eu.tectonic.remarkable.com"

INDEX_SCHEMA_VERSION = "3"
COLLECTION_ENTRY_TYPE = "80000000"
FILE_ENTRY_TYPE = "0"

MAX_COMMIT_RETRIES = 5


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class IndexEntry:
    hash: str
    type: str
    entry_id: str
    subfiles: int
    size: int

    def render(self) -> str:
        return f"{self.hash}:{self.type}:{self.entry_id}:{self.subfiles}:{self.size}"

    @classmethod
    def parse(cls, line: str) -> "IndexEntry":
        parts = line.split(":")
        if len(parts) != 5:
            raise SyncProtocolError(f"Unrecognized index line: {line!r}")
        hash_, type_, entry_id, subfiles, size = parts
        return cls(hash_, type_, entry_id, int(subfiles), int(size))


@dataclass
class Index:
    schema_version: str
    entries: List[IndexEntry]

    def render(self) -> bytes:
        lines = [self.schema_version] + [e.render() for e in self.entries]
        return ("\n".join(lines) + "\n").encode("utf-8")

    @classmethod
    def parse(cls, data: bytes) -> "Index":
        text = data.decode("utf-8")
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            return cls(schema_version=INDEX_SCHEMA_VERSION, entries=[])
        schema_version, *entry_lines = lines
        return cls(schema_version, [IndexEntry.parse(l) for l in entry_lines])


@dataclass
class Root:
    hash: str
    generation: int


class Sync15Client:
    def __init__(self, session: requests.Session, user_token: str, group_hint: Optional[str] = None):
        self.session = session
        self.user_token = user_token
        self.group_hint = group_hint
        self._storage_host: Optional[str] = None

    # -- host discovery ----------------------------------------------------

    @property
    def storage_host(self) -> str:
        if self._storage_host is None:
            self._storage_host = self._discover_storage_host()
        return self._storage_host

    def _discover_storage_host(self) -> str:
        group = self.group_hint or "auth0|placeholder"
        url = (
            f"{SERVICE_DISCOVERY_URL}"
            f"?environment=production&group={quote(group, safe='')}&apiVer=2"
        )
        resp = self.session.get(url, headers=self._auth_headers())
        if resp.status_code == 200:
            try:
                data = resp.json()
                host = data.get("Host")
                if host:
                    logger.info("Discovered sync storage host: %s", host)
                    return host
            except ValueError:
                pass
        logger.warning(
            "Storage host discovery failed (status %s), falling back to %s",
            resp.status_code,
            FALLBACK_STORAGE_HOST,
        )
        return FALLBACK_STORAGE_HOST

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.user_token}"}

    def _base_url(self) -> str:
        return f"https://{self.storage_host}"

    # -- root ----------------------------------------------------------

    def get_root(self) -> Root:
        resp = self.session.get(f"{self._base_url()}/sync/v3/root", headers=self._auth_headers())
        if resp.status_code != 200:
            raise SyncProtocolError(f"Failed to fetch root ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        try:
            return Root(hash=data["hash"], generation=int(data["generation"]))
        except KeyError as exc:
            raise SyncProtocolError(f"Unexpected root response shape: {data}") from exc

    def put_root(self, new_hash: str, expected_generation: int) -> int:
        body = {"hash": new_hash, "generation": expected_generation}
        resp = self.session.put(
            f"{self._base_url()}/sync/v3/root",
            headers=self._auth_headers(),
            json=body,
        )
        if resp.status_code == 409:
            raise SyncConflictError("Root generation changed concurrently")
        if resp.status_code != 200:
            raise SyncProtocolError(f"Failed to commit root ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        return int(data.get("generation", expected_generation + 1))

    # -- blobs ----------------------------------------------------------

    def get_blob(self, blob_hash: str) -> bytes:
        resp = self.session.get(
            f"{self._base_url()}/sync/v3/files/{blob_hash}", headers=self._auth_headers()
        )
        if resp.status_code != 200:
            raise SyncProtocolError(f"Failed to fetch blob {blob_hash} ({resp.status_code})")
        return resp.content

    def put_blob(self, data: bytes) -> str:
        blob_hash = sha256_hex(data)
        resp = self.session.put(
            f"{self._base_url()}/sync/v3/files/{blob_hash}",
            headers={**self._auth_headers(), "Content-Type": "application/octet-stream"},
            data=data,
        )
        if resp.status_code not in (200, 201):
            raise SyncProtocolError(
                f"Failed to upload blob {blob_hash} ({resp.status_code}): {resp.text[:300]}"
            )
        return blob_hash

    def get_root_index(self) -> tuple[Root, Index]:
        root = self.get_root()
        if not root.hash:
            return root, Index(schema_version=INDEX_SCHEMA_VERSION, entries=[])
        blob = self.get_blob(root.hash)
        return root, Index.parse(blob)
