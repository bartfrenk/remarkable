import base64
import json

import requests
import responses

from rmpush.upload import UPLOAD_URL, upload_file


@responses.activate
def test_upload_file_sends_expected_request_and_parses_doc_id():
    responses.add(
        responses.POST,
        UPLOAD_URL,
        json={"docID": "abc-123", "hash": "deadbeef"},
        status=200,
    )

    session = requests.Session()
    doc_id = upload_file(session, "usertoken", "My Doc", b"%PDF-1.4 ...", "application/pdf")

    assert doc_id == "abc-123"
    sent = responses.calls[0].request
    assert sent.headers["Authorization"] == "Bearer usertoken"
    assert sent.headers["Content-Type"] == "application/pdf"
    assert sent.headers["rm-source"] == "RoR-Browser"
    meta = json.loads(base64.b64decode(sent.headers["rm-meta"]))
    assert meta == {"file_name": "My Doc"}
    assert sent.body == b"%PDF-1.4 ..."
