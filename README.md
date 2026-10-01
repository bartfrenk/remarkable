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
remarkable delete "/Notes/MyDoc"                  # moves it to the trash
```

```python
async with RemarkableClient() as client:
    doc_id = await client.upload_pdf("report.pdf", name="Q3 Report")
    await client.replace_pdf("report.pdf")
    path = await client.download("/Notes/MyDoc")
    await client.delete("/Notes/MyDoc")
```

Uploads go to the root folder unless `--folder` is given. Only PDF and EPUB documents can be downloaded,
not handwritten notebooks.

## Troubleshooting

Run with `-v` (or `logging.DEBUG`) to see each HTTP call. On 404s, compare the
hosts in `auth.py` and `sync.py` against
[rmapi-js](https://github.com/erikbrinkman/rmapi-js).
