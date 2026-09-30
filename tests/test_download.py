from pathlib import Path

from remarkable.sync import RAW_HOST
from tests.conftest import FakeSession, make_client

DOC_ID = "doc-uuid"
ROOT_HASH = "root-hash"
DOC_HASH = "doc-hash"
META_HASH = "meta-hash"
PDF_HASH = "pdf-hash"


def _index_text(entries: list[tuple[str, str, int, int]]) -> str:
    lines = ["3"]
    lines += [f"{hash}:80000000:{id}:{subfiles}:{size}" for hash, id, subfiles, size in entries]
    return "\n".join(lines) + "\n"


async def test_download_resolves_path_and_writes_pdf_bytes(session: FakeSession, tmp_path: Path):
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v4/root",
        payload={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{ROOT_HASH}",
        body=_index_text([(DOC_HASH, DOC_ID, 3, 0)]),
    )
    # The document index is fetched twice: once while listing, once to download.
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{DOC_HASH}",
        body=_index_text(
            [
                (META_HASH, f"{DOC_ID}.metadata", 0, 0),
                (PDF_HASH, f"{DOC_ID}.pdf", 0, 0),
            ]
        ),
        repeat=True,
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{META_HASH}",
        payload={
            "visibleName": "MyDoc",
            "parent": "",
            "type": "DocumentType",
            "lastModified": "0",
            "pinned": False,
        },
    )
    session.add("GET", f"{RAW_HOST}/sync/v3/files/{PDF_HASH}", body=b"%PDF-1.4 ...")

    client = make_client(session, ["usertoken"])
    dest = tmp_path / "out.pdf"
    result = await client.download("/MyDoc", dest)

    assert result == dest
    assert dest.read_bytes() == b"%PDF-1.4 ..."

    for request in session.requests:
        assert request.headers["Authorization"] == "Bearer usertoken"

    pdf_request = next(r for r in session.requests if r.url.endswith(f"/{PDF_HASH}"))
    assert pdf_request.headers["rm-filename"] == f"{DOC_ID}.pdf"
