"""Low-level reads against reMarkable's content-addressed sync protocol.

reMarkable stores files in a Merkle-tree-like structure: a root hash points
to an index file listing top-level document/collection ids and their own
hashes; each of those points to another index file (that document's
"docSchema") listing its constituent parts (.metadata, .content, .pdf, ...)
by hash. Fetching a file's bytes means walking down this tree by hash.

This is the read counterpart to the write-side "verified upload API" used by
`client.py` — reads have no equivalent one-shot endpoint, so this module
implements just enough of the tree-walking protocol to resolve a path and
fetch its content. Endpoints and formats were cross-checked against the
`rmapi-js` client (https://github.com/erikbrinkman/rmapi-js), the same
reference this project's auth/upload code already follows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from remarkable.exceptions import SyncProtocolError

log = logging.getLogger(__name__)

RAW_HOST = "https://eu.tectonic.remarkable.com"
ROOT_ID = "root.docSchema"


@dataclass(frozen=True, slots=True)
class RawEntry:
    hash: str
    id: str
    subfiles: int
    size: int


def _auth_headers(user_token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token}", **extra}


def get_root_hash(session: requests.Session, user_token: str) -> str:
    resp = session.get(f"{RAW_HOST}/sync/v4/root", headers=_auth_headers(user_token))
    if resp.status_code != 200:
        raise SyncProtocolError(
            f"Failed to fetch root hash ({resp.status_code}): {resp.text[:300]}"
        )
    try:
        return str(resp.json()["hash"])
    except (ValueError, KeyError) as exc:
        raise SyncProtocolError(f"Unexpected root response shape: {resp.text[:300]}") from exc


def get_bytes(session: requests.Session, user_token: str, file_id: str, hash: str) -> bytes:
    resp = session.get(
        f"{RAW_HOST}/sync/v3/files/{hash}",
        headers=_auth_headers(user_token, **{"rm-filename": file_id}),
    )
    if resp.status_code != 200:
        raise SyncProtocolError(
            f"Failed to fetch {file_id!r} ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.content


def get_text(session: requests.Session, user_token: str, file_id: str, hash: str) -> str:
    return get_bytes(session, user_token, file_id, hash).decode("utf-8")


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


def get_entries(
    session: requests.Session, user_token: str, file_id: str, hash: str
) -> list[RawEntry]:
    text = get_text(session, user_token, file_id, hash)
    return parse_entries(text)
