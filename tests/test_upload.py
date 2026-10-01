import base64
import hashlib
import json

import pytest

from remarkable.client import Document
from remarkable.exceptions import DocumentNotFoundError
from remarkable.sync import RAW_HOST, UPLOAD_URL
from tests.conftest import FakeSession, make_client


async def test_upload_document_sends_expected_request_and_parses_doc_id(session: FakeSession):
    session.add("POST", UPLOAD_URL, payload={"docID": "abc-123", "hash": "deadbeef"})

    client = make_client(session, ["usertoken"])
    doc = Document("My Doc", b"%PDF-1.4 ...", "application/pdf")
    doc_id = await client.upload_document(doc)

    assert doc_id == "abc-123"
    [sent] = session.requests
    assert sent.headers["Authorization"] == "Bearer usertoken"
    assert sent.headers["Content-Type"] == "application/pdf"
    assert sent.headers["rm-source"] == "RoR-Browser"
    meta = json.loads(base64.b64decode(sent.headers["rm-meta"]))
    assert meta == {"file_name": "My Doc"}
    assert sent.data == b"%PDF-1.4 ..."


async def test_upload_document_retries_with_fresh_token_on_401(session: FakeSession):
    session.add("POST", UPLOAD_URL, status=401)
    session.add("POST", UPLOAD_URL, payload={"docID": "abc-123"})

    client = make_client(session, ["stale", "fresh"])
    doc_id = await client.upload_document(Document("My Doc", b"%PDF", "application/pdf"))

    assert doc_id == "abc-123"
    assert [r.headers["Authorization"] for r in session.requests] == [
        "Bearer stale",
        "Bearer fresh",
    ]


FOLDER_ID = "folder-uuid"
FILES_URL = f"{RAW_HOST}/sync/v3/files"
ROOT_PUT_URL = f"{RAW_HOST}/sync/v3/root"


def _hash(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _index_text(id: str, entries: list[tuple[str, str]]) -> str:
    lines = ["4", f"0:{id}:{len(entries)}:{10 * len(entries)}"]
    lines += [f"{hash}:0:{entry_id}:1:10" for hash, entry_id in entries]
    return "\n".join(lines) + "\n"


def _add_library(session: FakeSession, docs: dict[str, dict[str, str]]) -> None:
    """A schema 4 library holding `docs` (id -> metadata), served as often as asked."""
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v4/root",
        payload={"hash": _hash("root"), "generation": 7, "schemaVersion": 4},
        repeat=True,
    )
    session.add(
        "GET",
        f"{FILES_URL}/{_hash('root')}",
        body=_index_text(".", [(_hash(id), id) for id in docs]),
        repeat=True,
    )
    for id, meta in docs.items():
        session.add(
            "GET",
            f"{FILES_URL}/{_hash(id)}",
            body=_index_text(id, [(_hash(f"{id}.metadata"), f"{id}.metadata")]),
            repeat=True,
        )
        session.add("GET", f"{FILES_URL}/{_hash(f'{id}.metadata')}", payload=meta, repeat=True)
    session.add("PUT", f"{FILES_URL}/*", repeat=True)


async def test_upload_document_into_folder_moves_it_after_upload(session: FakeSession):
    _add_library(
        session,
        {
            FOLDER_ID: {"visibleName": "Work", "parent": "", "type": "CollectionType"},
            "abc-123": {"visibleName": "My Doc", "parent": "", "type": "DocumentType"},
        },
    )
    session.add("POST", UPLOAD_URL, payload={"docID": "abc-123"})
    session.add("PUT", ROOT_PUT_URL, payload={"hash": "new", "generation": 8})

    client = make_client(session, ["usertoken"])
    doc = Document("My Doc", b"%PDF", "application/pdf")
    doc_id = await client.upload_document(doc, folder="/Work")

    assert doc_id == "abc-123"
    metas = [
        json.loads(r.data or b"")
        for r in session.requests
        if r.method == "PUT" and r.headers.get("rm-filename") == "abc-123.metadata"
    ]
    assert [m["parent"] for m in metas] == [FOLDER_ID]
    assert session.requests[-1].url == ROOT_PUT_URL


async def test_upload_document_into_missing_folder_uploads_nothing(session: FakeSession):
    _add_library(session, {})

    client = make_client(session, ["usertoken"])
    with pytest.raises(DocumentNotFoundError):
        await client.upload_document(Document("My Doc", b"%PDF", "application/pdf"), "/Work")

    assert not [r for r in session.requests if r.method == "POST"]
