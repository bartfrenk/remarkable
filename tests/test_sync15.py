from rmpush.sync15 import Index, IndexEntry, sha256_hex


def test_index_roundtrip():
    entries = [
        IndexEntry(hash="a" * 64, type="0", entry_id="doc.content", subfiles=0, size=12),
        IndexEntry(hash="b" * 64, type="0", entry_id="doc.pdf", subfiles=0, size=4096),
    ]
    index = Index(schema_version="3", entries=entries)

    rendered = index.render()
    parsed = Index.parse(rendered)

    assert parsed.schema_version == "3"
    assert parsed.entries == entries


def test_index_parse_empty():
    index = Index.parse(b"")
    assert index.entries == []


def test_sha256_hex_is_deterministic():
    assert sha256_hex(b"hello") == sha256_hex(b"hello")
    assert sha256_hex(b"hello") != sha256_hex(b"world")


def test_index_entry_render_format():
    entry = IndexEntry(hash="deadbeef", type="80000000", entry_id="uuid-1", subfiles=4, size=100)
    assert entry.render() == "deadbeef:80000000:uuid-1:4:100"
