import argparse
import asyncio
import logging
import sys

from .client import RemarkableClient
from .exceptions import RemarkableError


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="remarkable", description="Upload, download and delete reMarkable documents"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="Pair with your reMarkable account")
    register.add_argument(
        "code", help="One-time code from https://my.remarkable.com/device/browser/connect"
    )

    upload = sub.add_parser("upload", help="Upload one or more PDFs")
    upload.add_argument("files", nargs="+", help="Path(s) to PDF file(s)")
    upload.add_argument("--name", help="Visible name (only valid with a single file)")
    upload.add_argument("--folder", help="Existing folder to upload into, e.g. /Notes")

    download = sub.add_parser("download", help="Download a document by its reMarkable path")
    download.add_argument("path", help="reMarkable path, e.g. /Notes/MyDoc")
    download.add_argument(
        "-o", "--output", help="Local destination file (default: ./<name>.<ext>)"
    )

    delete = sub.add_parser("delete", help="Move documents or folders to the trash")
    delete.add_argument("paths", nargs="+", help="reMarkable path(s), e.g. /Notes/MyDoc")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "upload" and args.name and len(args.files) > 1:
        parser.error("--name can only be used with a single file")

    try:
        asyncio.run(_run(args))
    except RemarkableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


async def _run(args: argparse.Namespace) -> None:
    async with RemarkableClient() as client:
        if args.command == "register":
            await client.register(args.code)
            print("Registered. Credentials saved.")
        elif args.command == "upload":
            for f in args.files:
                doc_id = await client.upload_pdf(f, name=args.name, folder=args.folder)
                print(f"Uploaded {f} -> document {doc_id}")
        elif args.command == "download":
            dest = await client.download(args.path, args.output)
            print(f"Downloaded {args.path} -> {dest}")
        elif args.command == "delete":
            for path in args.paths:
                await client.delete(path)
                print(f"Moved {path} to the trash")


if __name__ == "__main__":
    main()
