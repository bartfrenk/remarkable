import base64
import json

import pytest

from remarkable.client import Document
from remarkable.exceptions import DocumentNotFoundError
from remarkable.sync import UPLOAD_URL
from tests.conftest import ROOT_PUT_URL, FakeSession, add_documents, make_client


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


async def test_upload_document_into_folder_moves_it_after_upload(session: FakeSession):
    add_documents(
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
    add_documents(session, {})

    client = make_client(session, ["usertoken"])
    with pytest.raises(DocumentNotFoundError):
        await client.upload_document(Document("My Doc", b"%PDF", "application/pdf"), "/Work")

    assert not [r for r in session.requests if r.method == "POST"]
