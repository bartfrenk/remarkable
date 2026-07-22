# rmpush

A small Python library for pushing PDF documents to a reMarkable tablet via
the reMarkable Cloud.

## ⚠️ Important caveat

reMarkable does not publish an official API. This library's auth flow and
upload endpoint were cross-checked against the source of the actively
maintained `rmapi-js` client (https://github.com/erikbrinkman/rmapi-js) and
**verified live**: device pairing, user token refresh, and PDF upload have
all been run successfully against a real account. That said, this is still
an unofficial, undocumented API that can change without notice — if
something breaks, run with `-v` / debug logging first.

Uploads use reMarkable's own one-shot "add this file" endpoint
(`POST /doc/v2/files`) rather than hand-reconstructing the underlying sync
protocol (a content-addressed blob store plus a hash-tree root index that
the official clients use for full two-way sync). That's deliberately the
smaller surface: one request creates the whole document server-side, so
there's no root/generation state for a bug here to corrupt.

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
(library) to see each HTTP call. If you get a 404/host-not-found, reMarkable
has likely moved its endpoints again — check `rmpush/auth.py` (`AUTH_BASE`)
and `rmpush/upload.py` (`UPLOAD_HOST`) against the current `rmapi-js` source
for the new hosts, or file an issue against this repo.

## Layout

- `rmpush/auth.py` — device pairing, user token refresh/caching
- `rmpush/upload.py` — the one-shot PDF/EPUB upload endpoint
- `rmpush/client.py` — high-level `RemarkableClient.push_pdf(...)`
- `rmpush/__main__.py` — `rmpush` CLI

## Tests

```bash
pip install -e ".[dev]"
pytest
```

`test_upload.py` mocks the HTTP layer (via `responses`) to check the request
shape; auth/registration is exercised live rather than mocked, since it
depends on a real one-time pairing code.
