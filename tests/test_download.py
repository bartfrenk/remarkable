import json

import requests
import responses

from rmpush.client import RemarkableClient
from rmpush.sync import RAW_HOST

DOC_ID = "doc-uuid"
ROOT_HASH = "root-hash"
DOC_HASH = "doc-hash"
META_HASH = "meta-hash"
PDF_HASH = "pdf-hash"


def _index_text(entries: list[tuple[str, str, int, int]]) -> str:
    lines = ["3"]
    lines += [f"{hash}:80000000:{id}:{subfiles}:{size}" for hash, id, subfiles, size in entries]
    return "\n".join(lines) + "\n"


def _make_client() -> RemarkableClient:
    client = RemarkableClient(credentials_path="/nonexistent", session=requests.Session())
    client.auth.get_user_token = lambda force=False: "usertoken"  # type: ignore[method-assign]
    return client


@responses.activate
def test_download_resolves_path_and_writes_pdf_bytes(tmp_path):
    responses.add(
        responses.GET,
        f"{RAW_HOST}/sync/v4/root",
        json={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{RAW_HOST}/sync/v3/files/{ROOT_HASH}",
        body=_index_text([(DOC_HASH, DOC_ID, 3, 0)]),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{RAW_HOST}/sync/v3/files/{DOC_HASH}",
        body=_index_text(
            [
                (META_HASH, f"{DOC_ID}.metadata", 0, 0),
                (PDF_HASH, f"{DOC_ID}.pdf", 0, 0),
            ]
        ),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{RAW_HOST}/sync/v3/files/{META_HASH}",
        body=json.dumps(
            {
                "visibleName": "MyDoc",
                "parent": "",
                "type": "DocumentType",
                "lastModified": "0",
                "pinned": False,
            }
        ),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{RAW_HOST}/sync/v3/files/{PDF_HASH}",
        body=b"%PDF-1.4 ...",
        status=200,
    )

    client = _make_client()
    dest = tmp_path / "out.pdf"
    result = client.download("/MyDoc", dest)

    assert result == dest
    assert dest.read_bytes() == b"%PDF-1.4 ..."

    for call in responses.calls:
        assert call.request.headers["Authorization"] == "Bearer usertoken"

    pdf_call = next(c for c in responses.calls if c.request.url.endswith(f"/{PDF_HASH}"))
    assert pdf_call.request.headers["rm-filename"] == f"{DOC_ID}.pdf"
