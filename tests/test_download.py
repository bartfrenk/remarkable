import io
import zipfile
from pathlib import Path

import rmscene

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


async def test_download_bundles_native_notebook_as_rmdoc(session: FakeSession, tmp_path: Path):
    """A notebook has no PDF/EPUB part; it should download as a `.rmdoc` zip of its raw parts."""
    content_hash, pagedata_hash, page_hash = "content-hash", "pagedata-hash", "page-hash"
    page_id = f"{DOC_ID}/page-uuid.rm"

    session.add(
        "GET",
        f"{RAW_HOST}/sync/v4/root",
        payload={"hash": ROOT_HASH, "generation": 1, "schemaVersion": 3},
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{ROOT_HASH}",
        body=_index_text([(DOC_HASH, DOC_ID, 4, 0)]),
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{DOC_HASH}",
        body=_index_text(
            [
                (META_HASH, f"{DOC_ID}.metadata", 0, 0),
                (content_hash, f"{DOC_ID}.content", 0, 0),
                (pagedata_hash, f"{DOC_ID}.pagedata", 0, 0),
                (page_hash, page_id, 0, 0),
            ]
        ),
        repeat=True,
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{META_HASH}",
        payload={
            "visibleName": "MyNotebook",
            "parent": "",
            "type": "DocumentType",
            "lastModified": "0",
            "pinned": False,
        },
        repeat=True,
    )
    session.add("GET", f"{RAW_HOST}/sync/v3/files/{content_hash}", body=b'{"pages": []}')
    session.add("GET", f"{RAW_HOST}/sync/v3/files/{pagedata_hash}", body=b"Blank\n")
    session.add("GET", f"{RAW_HOST}/sync/v3/files/{page_hash}", body=b"reMarkable .lines file")

    client = make_client(session, ["usertoken"])
    dest = tmp_path / "out.rmdoc"
    result = await client.download("/MyNotebook", dest)

    assert result == dest
    with zipfile.ZipFile(dest) as archive:
        assert set(archive.namelist()) == {
            f"{DOC_ID}.metadata",
            f"{DOC_ID}.content",
            f"{DOC_ID}.pagedata",
            page_id,
        }
        assert archive.read(page_id) == b"reMarkable .lines file"


async def test_download_notebook_bundles_raw_parts_as_rmdoc(session: FakeSession, tmp_path: Path):
    """`download_notebook` always bundles the raw parts, regardless of document kind."""
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
        repeat=True,
    )
    session.add("GET", f"{RAW_HOST}/sync/v3/files/{PDF_HASH}", body=b"%PDF-1.4 ...")

    client = make_client(session, ["usertoken"])
    dest = tmp_path / "out.rmdoc"
    result = await client.download_notebook("/MyDoc", dest)

    assert result == dest
    with zipfile.ZipFile(dest) as archive:
        assert set(archive.namelist()) == {f"{DOC_ID}.metadata", f"{DOC_ID}.pdf"}
        assert archive.read(f"{DOC_ID}.pdf") == b"%PDF-1.4 ..."


async def test_download_pdf_passes_through_existing_pdf(session: FakeSession, tmp_path: Path):
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
    result = await client.download_pdf("/MyDoc", dest)

    assert result == dest
    assert dest.read_bytes() == b"%PDF-1.4 ..."


async def test_download_pdf_renders_native_notebook(session: FakeSession, tmp_path: Path):
    """A notebook has no PDF part, so `download_pdf` renders its `.rm` pages into a real PDF."""
    page_blocks = list(
        rmscene.simple_text_document(  # pyright: ignore[reportUnknownMemberType]
            "Buy milk\nCall dentist"
        )
    )
    page_buffer = io.BytesIO()
    rmscene.write_blocks(page_buffer, page_blocks)  # pyright: ignore[reportUnknownMemberType]
    page_bytes = page_buffer.getvalue()

    content_hash, page_hash = "content-hash", "page-hash"
    page_rm_id = f"{DOC_ID}/page-uuid.rm"

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
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{DOC_HASH}",
        body=_index_text(
            [
                (META_HASH, f"{DOC_ID}.metadata", 0, 0),
                (content_hash, f"{DOC_ID}.content", 0, 0),
                (page_hash, page_rm_id, 0, 0),
            ]
        ),
        repeat=True,
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{META_HASH}",
        payload={
            "visibleName": "MyNotebook",
            "parent": "",
            "type": "DocumentType",
            "lastModified": "0",
            "pinned": False,
        },
    )
    session.add(
        "GET",
        f"{RAW_HOST}/sync/v3/files/{content_hash}",
        payload={"cPages": {"pages": [{"id": "page-uuid"}]}},
    )
    session.add("GET", f"{RAW_HOST}/sync/v3/files/{page_hash}", body=page_bytes)

    client = make_client(session, ["usertoken"])
    dest = tmp_path / "out.pdf"
    result = await client.download_pdf("/MyNotebook", dest)

    assert result == dest
    data = dest.read_bytes()
    assert data.startswith(b"%PDF-")
    assert len(data) > 100

    page_request = next(r for r in session.requests if r.url.endswith(f"/{page_hash}"))
    assert page_request.headers["rm-filename"] == page_rm_id
