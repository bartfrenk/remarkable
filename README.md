# remarkable

Async Python library and CLI for uploading PDFs to, downloading documents
from, and deleting documents on a reMarkable tablet via the unofficial reMarkable Cloud API. The API is
undocumented and may change without notice.

## Install

```bash
make install    # CLI for your user (requires uv); undo with make uninstall
pip install -e .  # library / development
```

## Usage

Pair once with a code from https://my.remarkable.com/device/browser/connect.
Credentials are cached in `~/.config/remarkable/credentials.json`.

```bash
remarkable register YOUR-CODE
remarkable upload report.pdf --name "Q3 Report"   # or: upload a.pdf b.pdf
remarkable upload report.pdf --folder "/Work"      # into an existing folder
remarkable replace report.pdf                     # upload, trashing the old "report"
remarkable replace report.pdf --folder "/Work"     # same, inside /Work
remarkable download "/Notes/MyDoc" -o mydoc.pdf
remarkable download "/Notes/MyNotebook" --format pdf  # render a notebook to PDF
remarkable delete "/Notes/MyDoc"                  # moves it to the trash
```

```python
async with RemarkableClient() as client:
    doc_id = await client.upload_pdf("report.pdf", name="Q3 Report")
    await client.replace_pdf("report.pdf")
    path = await client.download("/Notes/MyDoc")
    path = await client.download_pdf("/Notes/MyHandwrittenNotebook")
    path = await client.download_notebook("/Notes/MyHandwrittenNotebook")
    await client.delete("/Notes/MyDoc")
```

Uploads go to the root folder unless `--folder` is given. `download` keeps a document's native format:
PDF (with any pen annotations merged onto the page) or EPUB as-is, native notebooks as a `.rmdoc`
archive (reMarkable's own backup format). `download_pdf` always produces a PDF, rendering a notebook's
handwritten pages if it has no PDF payload. `download_notebook` always produces the raw `.rmdoc`
archive, regardless of document kind.

The CLI's `--format`/`-f` flag (`rm` or `pdf`) only affects notebooks, picking between
`download_notebook`'s and `download_pdf`'s behavior; it defaults to `rm` for notebooks and `pdf` for
PDFs. Passing `--format rm` for a PDF or EPUB document fails, since there's no raw notebook form for it.

## Troubleshooting

Run with `-v` (or `logging.DEBUG`) to see each HTTP call. On 404s, compare the
hosts in `auth.py` and `sync.py` against
[rmapi-js](https://github.com/erikbrinkman/rmapi-js).
