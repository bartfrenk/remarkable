import argparse
import logging
import sys

from .client import RemarkableClient
from .exceptions import RemarkableError


def main() -> None:
    parser = argparse.ArgumentParser(prog="remarkable", description="Push PDFs to your reMarkable")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="Pair with your reMarkable account")
    register.add_argument(
        "code", help="One-time code from https://my.remarkable.com/device/browser/connect"
    )

    push = sub.add_parser("push", help="Upload one or more PDFs")
    push.add_argument("files", nargs="+", help="Path(s) to PDF file(s)")
    push.add_argument("--name", help="Visible name (only valid with a single file)")

    download = sub.add_parser("download", help="Download a document by its reMarkable path")
    download.add_argument("path", help="reMarkable path, e.g. /Notes/MyDoc")
    download.add_argument("-o", "--output", help="Local destination file (default: ./<name>.<ext>)")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    client = RemarkableClient()

    try:
        if args.command == "register":
            client.register(args.code)
            print("Registered. Credentials saved.")
        elif args.command == "push":
            if args.name and len(args.files) > 1:
                parser.error("--name can only be used with a single file")
            for f in args.files:
                doc_id = client.push_pdf(f, name=args.name)
                print(f"Uploaded {f} -> document {doc_id}")
        elif args.command == "download":
            dest = client.download(args.path, args.output)
            print(f"Downloaded {args.path} -> {dest}")
    except RemarkableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
