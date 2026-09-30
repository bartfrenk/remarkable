# remarkable

A small Python library for uploading PDF documents to and downloading documents
from a reMarkable tablet via the reMarkable Cloud.

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
remarkable register YOUR-CODE
# or, from Python:
python -c "
import asyncio
from remarkable import RemarkableClient

async def main():
    async with RemarkableClient() as client:
        await client.register('YOUR-CODE')

asyncio.run(main())
"
```

This exchanges the code for a device token and caches it (along with
refreshed session tokens) at `~/.config/remarkable/credentials.json` (mode
`0600`). You only need to do this once — the device token doesn't expire
under normal use.

## Upload a PDF

```bash
remarkable upload report.pdf
remarkable upload report.pdf --name "Q3 Report"
remarkable upload a.pdf b.pdf c.pdf
```

The library is async (built on `aiohttp`); use the client as an async
context manager so its HTTP session gets closed:

```python
import asyncio

from remarkable import RemarkableClient


async def main() -> None:
    async with RemarkableClient() as client:
        doc_id = await client.upload_pdf("report.pdf", name="Q3 Report")


asyncio.run(main())
```

Documents are uploaded to the root of your reMarkable file tree.

## Download a document

```bash
remarkable download "/Notes/MyDoc"
remarkable download "/Notes/MyDoc" -o mydoc.pdf
```

```python
async with RemarkableClient() as client:
    path = await client.download("/Notes/MyDoc")
```

Paths are resolved against `visibleName`/folder structure, e.g.
`/Folder/Subfolder/MyDoc`. Only documents uploaded as PDF or EPUB can be
downloaded this way — native reMarkable notebooks (handwritten pages) are
stored as a multi-file archive and aren't supported.

Downloading walks reMarkable's content-addressed sync protocol (root hash →
per-document index → content blob) rather than a single endpoint, since the
cloud API has no one-shot "download by id" call — see `remarkable/sync.py`.

## If it doesn't work

Run with `-v` (CLI) or configure `logging.basicConfig(level=logging.DEBUG)`
(library) to see each HTTP call. If you get a 404/host-not-found, reMarkable
has likely moved its endpoints again — check `remarkable/auth.py` (`AUTH_BASE`),
and `remarkable/sync.py` (`UPLOAD_HOST`, `RAW_HOST`) against
the current `rmapi-js` source for the new hosts, or file an issue against
this repo.

## Layout

- `remarkable/auth.py` — device pairing, user token refresh/caching
- `remarkable/sync.py` — `SyncApi`: all authenticated HTTP calls (upload, sync-tree reads)
- `remarkable/client.py` — high-level `RemarkableClient` (`upload_pdf`, `download`, ...)
- `remarkable/__main__.py` — `remarkable` CLI

## Tests

```bash
pip install -e ".[dev]"
pytest
```

`test_upload.py` mocks the HTTP layer (via `responses`) to check the request
shape; auth/registration is exercised live rather than mocked, since it
depends on a real one-time pairing code.
