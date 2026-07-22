# rmpush

A small Python library for pushing PDF documents to a reMarkable tablet via
the reMarkable Cloud.

## ⚠️ Important caveat

reMarkable does not publish an official API. This library follows the same
device-registration and "sync15" storage protocol used by long-running
open-source clients (e.g. `rmapi`, `rmapy`), reconstructed from public
documentation of that protocol rather than tested live by me against a real
account. **Test it yourself against a non-critical document first** and
watch the verbose logs (`-v` / `logging`) if something doesn't show up.

By design, uploads only ever *add* a new document to your account's file
index — existing entries are read and copied through untouched, and the
final commit is conditioned on the sync generation you started from (an
optimistic-concurrency check), so a failed or buggy upload should not be
able to corrupt or delete anything already on your account. Worst case is
that the new document doesn't appear, or the command errors out.

## Install

```bash
pip install -e .
```

## Pair with your account (one-time)

1. Go to https://my.remarkable.com/device/browser/connect on any browser and
   copy the one-time code shown.
2. Run:

```bash
rmpush register YOUR-CODE
# or, from Python:
python -c "from rmpush import RemarkableClient; RemarkableClient().register('YOUR-CODE')"
```

This exchanges the code for a device token and caches it (along with
refreshed session tokens) at `~/.config/rmpush/credentials.json` (mode
`0600`). You only need to do this once — the device token doesn't expire
under normal use.

## Push a PDF

```bash
rmpush push report.pdf
rmpush push report.pdf --name "Q3 Report"
rmpush push a.pdf b.pdf c.pdf
```

```python
from rmpush import RemarkableClient

client = RemarkableClient()
doc_id = client.push_pdf("report.pdf", visible_name="Q3 Report")
```

Documents are uploaded to the root of your reMarkable file tree.

## If it doesn't work

Run with `-v` (CLI) or configure `logging.basicConfig(level=logging.DEBUG)`
(library) to see each HTTP call. Common failure points, roughly in order of
likelihood:

- **Storage host discovery** (`rmpush/sync15.py:_discover_storage_host`) —
  the most likely thing reMarkable has changed since this was written. You
  can override it by constructing `Sync15Client` with a known-good host, or
  hardcode `FALLBACK_STORAGE_HOST`.
- **Document content/metadata schema** (`rmpush/document.py`) — the JSON
  shape reMarkable's software expects for `.content`/`.metadata` files has
  grown new optional fields over software versions; the fields used here are
  the older, minimal set that the app has historically auto-upgraded on
  open.
- Compare against `rmapi`'s Go source (https://github.com/ddvk/rmapi) if you
  need a reference implementation to diff against.

## Layout

- `rmpush/auth.py` — device pairing, user token refresh/caching
- `rmpush/sync15.py` — low-level content-addressed sync protocol (root,
  blobs, index parsing)
- `rmpush/document.py` — builds the `.content`/`.metadata`/`.pagedata`/`.pdf`
  file set for a new document
- `rmpush/client.py` — high-level `RemarkableClient.push_pdf(...)`
- `rmpush/__main__.py` — `rmpush` CLI

## Tests

```bash
pip install -e ".[dev]"  # or just: pip install pytest
pytest
```

Only the pure-logic pieces (index line parsing/rendering, hashing) are unit
tested — there is no automated test against the real reMarkable cloud.
