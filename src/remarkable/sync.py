"""reMarkable Cloud HTTP API: uploads and content-addressed sync reads.

reMarkable stores files in a Merkle-tree-like structure: a root hash points
to an index file listing top-level document/collection ids and their own
hashes; each of those points to another index file (that document's
"docSchema") listing its constituent parts (.metadata, .content, .pdf, ...)
by hash. Fetching a file's bytes means walking down this tree by hash.

Uploads go through a one-shot "verified upload API"; reads and edits have no
equivalent endpoint, so `SyncApi` exposes just enough of the tree protocol for
`client.py` to resolve a path, fetch its content, and rewrite its metadata.
An edit uploads new blobs bottom-up (file, then document index, then root
index) and finally swaps the root hash, guarded by the root's generation so
concurrent writers can't silently clobber each other. `SyncApi` is the single
place where HTTP requests are made and authenticated. Endpoints and formats
were cross-checked against the `rmapi-js` client
(https://github.com/erikbrinkman/rmapi-js), the same reference this project's
auth code follows.
"""

from __future__ import annotations

import hashlib
import json
import logging
from base64 import b64encode
from dataclasses import dataclass
from typing import final

import aiohttp

from remarkable.auth import Auth
from remarkable.exceptions import GenerationConflictError, SyncProtocolError

log = logging.getLogger(__name__)

RAW_HOST = "https://eu.tectonic.remarkable.com"
UPLOAD_HOST = "https://internal.cloud.remarkable.com"
UPLOAD_URL = f"{UPLOAD_HOST}/doc/v2/files"
ROOT_ID = "root.docSchema"
FILE_TYPE = 0
INDEX_TYPE = 80000000


@dataclass(frozen=True, slots=True)
class Root:
    hash: str
    generation: int
    schema_version: int


@dataclass(frozen=True, slots=True)
class RawEntry:
    hash: str
    type: int
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
            hash, type, id, subfiles, size = line.split(":")
            entries.append(
                RawEntry(hash=hash, type=int(type), id=id, subfiles=int(subfiles), size=int(size))
            )
        except ValueError as exc:
            raise SyncProtocolError(f"Malformed sync index line: {line!r}") from exc
    return entries


def format_entries(
    id: str, entries: list[RawEntry], schema_version: int
) -> tuple[bytes, RawEntry]:
    """Serialize an index file, returning its bytes and the entry pointing at it.

    `id` is a document id, or "root" for the root index.
    """
    ordered = sorted(entries, key=lambda e: e.id)
    size = sum(e.size for e in ordered)
    lines = [str(schema_version)]
    if schema_version == 4:
        lines.append(f"0:{'.' if id == 'root' else id}:{len(ordered)}:{size}")
    elif schema_version != 3:
        raise SyncProtocolError(f"Unsupported sync schema version: {schema_version!r}")
    for e in ordered:
        line_type = FILE_TYPE if schema_version == 4 else e.type
        lines.append(f"{e.hash}:{line_type}:{e.id}:{e.subfiles}:{e.size}")
    data = ("\n".join(lines) + "\n").encode("utf-8")

    if schema_version == 3:
        # schema 3 hashes an index by its children's hashes, not its own bytes
        hash = hashlib.sha256(b"".join(bytes.fromhex(e.hash) for e in ordered)).hexdigest()
        entry_type = INDEX_TYPE
    else:
        hash = hashlib.sha256(data).hexdigest()
        entry_type = FILE_TYPE
    return data, RawEntry(hash=hash, type=entry_type, id=id, subfiles=len(ordered), size=size)


def _make_crc32c_table() -> list[int]:
    table: list[int] = []
    for n in range(256):
        crc = n
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC32C_TABLE = _make_crc32c_table()


def crc32c(data: bytes) -> int:
    """CRC-32C (Castagnoli), which the blob store requires alongside uploads."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC32C_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


@final
class SyncApi:
    """All HTTP calls to the reMarkable Cloud, authenticated via `Auth`."""

    def __init__(self, session: aiohttp.ClientSession, auth: Auth):
        self.session = session
        self.auth = auth

    async def _request(
        self,
        method: str,
        url: str,
        what: str,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> bytes:
        async def send(force_refresh: bool) -> tuple[int, bytes]:
            token = await self.auth.get_user_token(force=force_refresh)
            all_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
            async with self.session.request(method, url, headers=all_headers, data=data) as resp:
                return resp.status, await resp.read()

        status, body = await send(force_refresh=False)
        if status == 401:
            log.info("Got 401 for %s; retrying with a fresh user token", what)
            status, body = await send(force_refresh=True)
        if not 200 <= status < 300:
            text = body.decode("utf-8", errors="replace")
            if status == 412 or "precondition failed" in text:
                raise GenerationConflictError(f"{what} lost a race with another sync client")
            raise SyncProtocolError(f"{what} failed ({status}): {text[:300]}")
        return body

    async def get_root(self) -> Root:
        body = await self._request("GET", f"{RAW_HOST}/sync/v4/root", "Fetching root hash")
        try:
            raw = json.loads(body)
            return Root(
                hash=str(raw["hash"]),
                generation=int(raw["generation"]),
                schema_version=int(raw["schemaVersion"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise SyncProtocolError(f"Unexpected root response shape: {body[:300]!r}") from exc

    async def put_root(self, hash: str, generation: int) -> None:
        """Point the account at a new root index.

        `generation` must be that of the root the new index was derived from;
        otherwise this raises `GenerationConflictError`.
        """
        await self._request(
            "PUT",
            f"{RAW_HOST}/sync/v3/root",
            "Updating root hash",
            data=json.dumps({"hash": hash, "generation": generation, "broadcast": True}).encode(),
        )

    async def get_bytes(self, file_id: str, hash: str) -> bytes:
        return await self._request(
            "GET",
            f"{RAW_HOST}/sync/v3/files/{hash}",
            f"Fetching {file_id!r}",
            headers={"rm-filename": file_id},
        )

    async def get_text(self, file_id: str, hash: str) -> str:
        return (await self.get_bytes(file_id, hash)).decode("utf-8")

    async def get_entries(self, file_id: str, hash: str) -> list[RawEntry]:
        return parse_entries(await self.get_text(file_id, hash))

    async def put_bytes(self, file_id: str, data: bytes) -> RawEntry:
        hash = hashlib.sha256(data).hexdigest()
        await self._put_blob(file_id, hash, data)
        return RawEntry(hash=hash, type=FILE_TYPE, id=file_id, subfiles=0, size=len(data))

    async def put_entries(self, id: str, entries: list[RawEntry], schema_version: int) -> RawEntry:
        """Upload an index file; `id` is a document id, or "root" for the root."""
        data, entry = format_entries(id, entries, schema_version)
        await self._put_blob(f"{id}.docSchema", entry.hash, data)
        return entry

    async def _put_blob(self, file_id: str, hash: str, data: bytes) -> None:
        crc = b64encode(crc32c(data).to_bytes(4, "big")).decode("ascii")
        await self._request(
            "PUT",
            f"{RAW_HOST}/sync/v3/files/{hash}",
            f"Uploading {file_id!r}",
            headers={"rm-filename": file_id, "x-goog-hash": f"crc32c={crc}"},
            data=data,
        )

    async def upload(self, name: str, data: bytes, mime_type: str) -> str:
        meta = b64encode(json.dumps({"file_name": name}).encode("utf-8")).decode("ascii")
        body = await self._request(
            "POST",
            UPLOAD_URL,
            f"Uploading {name!r}",
            headers={"Content-Type": mime_type, "rm-meta": meta, "rm-source": "RoR-Browser"},
            data=data,
        )
        try:
            return str(json.loads(body)["docID"])
        except (ValueError, KeyError) as exc:
            raise SyncProtocolError(f"Unexpected upload response shape: {body[:300]!r}") from exc
