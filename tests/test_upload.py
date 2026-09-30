import base64
import json

from conftest import FakeSession, make_client
from remarkable.client import Document
from remarkable.sync import UPLOAD_URL


async def test_push_document_sends_expected_request_and_parses_doc_id(session: FakeSession):
    session.add("POST", UPLOAD_URL, payload={"docID": "abc-123", "hash": "deadbeef"})

    client = make_client(session, ["usertoken"])
    doc = Document("My Doc", b"%PDF-1.4 ...", "application/pdf")
    doc_id = await client.push_document(doc)

    assert doc_id == "abc-123"
    [sent] = session.requests
    assert sent.headers["Authorization"] == "Bearer usertoken"
    assert sent.headers["Content-Type"] == "application/pdf"
    assert sent.headers["rm-source"] == "RoR-Browser"
    meta = json.loads(base64.b64decode(sent.headers["rm-meta"]))
    assert meta == {"file_name": "My Doc"}
    assert sent.data == b"%PDF-1.4 ..."


async def test_push_document_retries_with_fresh_token_on_401(session: FakeSession):
    session.add("POST", UPLOAD_URL, status=401)
    session.add("POST", UPLOAD_URL, payload={"docID": "abc-123"})

    client = make_client(session, ["stale", "fresh"])
    doc_id = await client.push_document(Document("My Doc", b"%PDF", "application/pdf"))

    assert doc_id == "abc-123"
    assert [r.headers["Authorization"] for r in session.requests] == [
        "Bearer stale",
        "Bearer fresh",
    ]
