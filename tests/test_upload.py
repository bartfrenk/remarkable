import base64
import json

import requests
import responses

from remarkable.client import Document, RemarkableClient
from remarkable.sync import UPLOAD_URL


def _make_client(tokens: list[str]) -> RemarkableClient:
    client = RemarkableClient(credentials_path="/nonexistent", session=requests.Session())
    remaining = iter(tokens)
    current = next(remaining)

    def get_user_token(force: bool = False) -> str:
        nonlocal current
        if force:
            current = next(remaining)
        return current

    client.auth.get_user_token = get_user_token  # type: ignore[method-assign]
    return client


@responses.activate
def test_push_document_sends_expected_request_and_parses_doc_id():
    responses.add(
        responses.POST,
        UPLOAD_URL,
        json={"docID": "abc-123", "hash": "deadbeef"},
        status=200,
    )

    client = _make_client(["usertoken"])
    doc = Document("My Doc", b"%PDF-1.4 ...", "application/pdf")
    doc_id = client.push_document(doc)

    assert doc_id == "abc-123"
    sent = responses.calls[0].request
    assert sent.headers["Authorization"] == "Bearer usertoken"
    assert sent.headers["Content-Type"] == "application/pdf"
    assert sent.headers["rm-source"] == "RoR-Browser"
    meta = json.loads(base64.b64decode(sent.headers["rm-meta"]))
    assert meta == {"file_name": "My Doc"}
    assert sent.body == b"%PDF-1.4 ..."


@responses.activate
def test_push_document_retries_with_fresh_token_on_401():
    responses.add(responses.POST, UPLOAD_URL, status=401)
    responses.add(responses.POST, UPLOAD_URL, json={"docID": "abc-123"}, status=200)

    client = _make_client(["stale", "fresh"])
    doc_id = client.push_document(Document("My Doc", b"%PDF", "application/pdf"))

    assert doc_id == "abc-123"
    assert [c.request.headers["Authorization"] for c in responses.calls] == [
        "Bearer stale",
        "Bearer fresh",
    ]
