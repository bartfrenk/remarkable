import json
from pathlib import Path

from remarkable.sync import UPLOAD_URL
from tests.conftest import (
    DOC_ID,
    ROOT_PUT_URL,
    FakeSession,
    add_library,
    make_client,
    uploaded_blobs,
)


async def test_replace_uploads_then_trashes_existing_document(
    session: FakeSession, tmp_path: Path
):
    add_library(session, 4, generations=[7, 7])
    session.add("POST", UPLOAD_URL, payload={"docID": "new-uuid"})
    session.add("PUT", ROOT_PUT_URL, payload={"hash": "new", "generation": 8})
    pdf = tmp_path / "MyDoc.pdf"
    pdf.write_bytes(b"%PDF-1.4 new")

    result = await make_client(session, ["usertoken"]).replace_pdf(pdf)

    assert result.doc_id == "new-uuid"
    assert result.trashed_ids == [DOC_ID]
    writes = [r for r in session.requests if r.method in ("POST", "PUT")]
    assert writes[0].url == UPLOAD_URL and writes[0].data == b"%PDF-1.4 new"
    _, meta_blob = uploaded_blobs(session)[f"{DOC_ID}.metadata"]
    assert json.loads(meta_blob)["parent"] == "trash"


async def test_replace_only_uploads_when_no_document_has_the_name(
    session: FakeSession, tmp_path: Path
):
    add_library(session, 4, generations=[7])
    session.add("POST", UPLOAD_URL, payload={"docID": "new-uuid"})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF")

    result = await make_client(session, ["usertoken"]).replace_pdf(pdf, name="Other")

    assert result.doc_id == "new-uuid"
    assert result.trashed_ids == []
    assert [r.method for r in session.requests if r.method != "GET"] == ["POST"]
