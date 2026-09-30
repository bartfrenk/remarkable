import hashlib
import json

import pytest

from remarkable.exceptions import GenerationConflictError
from remarkable.sync import RAW_HOST, crc32c, parse_entries
from tests.conftest import FakeSession, make_client

DOC_ID = "doc-uuid"
ROOT_HASH = hashlib.sha256(b"root").hexdigest()
DOC_HASH = hashlib.sha256(b"doc").hexdigest()
META_HASH = hashlib.sha256(b"meta").hexdigest()
PDF_HASH = hashlib.sha256(b"pdf").hexdigest()
FILES_URL = f"{RAW_HOST}/sync/v3/files"
ROOT_PUT_URL = f"{RAW_HOST}/sync/v3/root"


def _index_text(id: str, entries: list[tuple[str, str, int, int]], schema: int) -> str:
    lines = [str(schema)]
    if schema == 4:
        lines.append(f"0:{id}:{len(entries)}:{sum(size for *_, size in entries)}")
    lines += [f"{hash}:0:{id}:{subfiles}:{size}" for hash, id, subfiles, size in entries]
    return "\n".join(lines) + "\n"


def _add_library(session: FakeSession, schema: int, generations: list[int]) -> None:
    for generation in generations:
        session.add(
            "GET",
            f"{RAW_HOST}/sync/v4/root",
            payload={"hash": ROOT_HASH, "generation": generation, "schemaVersion": schema},
        )
    session.add(
        "GET",
        f"{FILES_URL}/{ROOT_HASH}",
        body=_index_text(".", [(DOC_HASH, DOC_ID, 2, 110)], schema),
        repeat=True,
    )
    session.add(
        "GET",
        f"{FILES_URL}/{DOC_HASH}",
        body=_index_text(
            DOC_ID,
            [(META_HASH, f"{DOC_ID}.metadata", 0, 10), (PDF_HASH, f"{DOC_ID}.pdf", 0, 100)],
            schema,
        ),
        repeat=True,
    )
    session.add(
        "GET",
        f"{FILES_URL}/{META_HASH}",
        payload={"visibleName": "MyDoc", "parent": "", "type": "DocumentType", "version": 2},
        repeat=True,
    )
    session.add("PUT", f"{FILES_URL}/*", repeat=True)


def _uploads(session: FakeSession) -> dict[str, tuple[str, bytes]]:
    """Uploaded (hash, bytes) by rm-filename."""
    blobs: dict[str, tuple[str, bytes]] = {}
    for r in session.requests:
        if r.method == "PUT" and r.url.startswith(FILES_URL):
            assert r.data is not None
            blobs[r.headers["rm-filename"]] = (r.url.rsplit("/", 1)[1], r.data)
    return blobs


@pytest.mark.parametrize("schema", [3, 4])
async def test_delete_moves_document_to_trash(session: FakeSession, schema: int):
    _add_library(session, schema, generations=[7, 7])
    session.add("PUT", ROOT_PUT_URL, payload={"hash": "new", "generation": 8})

    await make_client(session, ["usertoken"]).delete("/MyDoc")

    blobs = _uploads(session)
    meta_hash, meta_blob = blobs[f"{DOC_ID}.metadata"]
    doc_hash, doc_blob = blobs[f"{DOC_ID}.docSchema"]
    root_hash, root_blob = blobs["root.docSchema"]
    assert meta_hash == hashlib.sha256(meta_blob).hexdigest()
    assert root_hash == hashlib.sha256(root_blob).hexdigest()

    meta = json.loads(meta_blob)
    assert meta["parent"] == "trash"
    assert meta["visibleName"] == "MyDoc"
    assert meta["version"] == 3
    assert meta["metadatamodified"] is True

    doc_parts = parse_entries(doc_blob.decode())
    assert {p.id: p.hash for p in doc_parts} == {
        f"{DOC_ID}.metadata": meta_hash,
        f"{DOC_ID}.pdf": PDF_HASH,
    }

    assert root_blob.startswith(b"4\n0:.:1:")
    [doc_entry] = parse_entries(root_blob.decode())
    assert (doc_entry.id, doc_entry.hash) == (DOC_ID, doc_hash)
    if schema == 3:
        # schema 3 stores an index under the hash of its children's hashes
        children = b"".join(bytes.fromhex(p.hash) for p in sorted(doc_parts, key=lambda p: p.id))
        assert doc_hash == hashlib.sha256(children).hexdigest()
    else:
        assert doc_hash == hashlib.sha256(doc_blob).hexdigest()

    root_put = session.requests[-1]
    assert root_put.method == "PUT" and root_put.url == ROOT_PUT_URL
    assert root_put.data is not None
    assert json.loads(root_put.data) == {
        "hash": root_hash,
        "generation": 7,
        "broadcast": True,
    }


async def test_delete_retries_when_root_changes_concurrently(session: FakeSession):
    _add_library(session, 4, generations=[7, 7, 9])
    session.add("PUT", ROOT_PUT_URL, status=412, body='{"message":"precondition failed"}\n')
    session.add("PUT", ROOT_PUT_URL, payload={"hash": "new", "generation": 10})

    await make_client(session, ["usertoken"]).delete("/MyDoc")

    root_puts = [r for r in session.requests if r.url == ROOT_PUT_URL]
    assert [json.loads(r.data or b"")["generation"] for r in root_puts] == [7, 9]


async def test_delete_gives_up_after_repeated_conflicts(session: FakeSession):
    _add_library(session, 4, generations=[1, 2, 3, 4])
    session.add("PUT", ROOT_PUT_URL, status=412, repeat=True)

    with pytest.raises(GenerationConflictError):
        await make_client(session, ["usertoken"]).delete("/MyDoc")


def test_crc32c_matches_standard_check_value():
    assert crc32c(b"123456789") == 0xE3069283
