"""JSON on stdout, errors on stderr; no daemon or browser required."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import FIELDS, Client, FetchError, ParseError, __version__
from .client import DEFAULT_CACHE_DIR


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="gnd", description="Unofficial Games Never Die client"
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fields", help="List selectable game fields as JSON")
    for name in ("search", "get"):
        command = commands.add_parser(
            name,
            help={"search": "Search one page of games", "get": "Get a game as JSON"}[
                name
            ],
        )
        command.add_argument(
            "identifier", metavar="QUERY" if name == "search" else "SLUG_OR_URL"
        )
        command.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
        command.add_argument(
            "--no-cache", action="store_true", help="Disable disk reads and writes"
        )
        command.add_argument(
            "--refresh",
            action="store_true",
            help="Ignore cached responses for this command",
        )
        command.add_argument("--timeout", type=float, default=20, metavar="SECONDS")
        command.add_argument(
            "--output",
            "-o",
            type=Path,
            help="Create a new file; never overwrite an existing file",
        )
        if name == "search":
            command.add_argument("--page", type=int, default=1)
            command.add_argument("--limit", type=int)
        else:
            command.add_argument(
                "--fields", help="Comma-separated top-level field names; see gnd fields"
            )
            command.add_argument(
                "--steam",
                action="store_true",
                help="Also fetch Steam price, reviews and players",
            )
    args = parser.parse_args(argv)
    if args.command == "fields":
        print(json.dumps(FIELDS, ensure_ascii=False, indent=2))
        return 0
    try:
        client = Client(
            cache_dir=None if args.no_cache else args.cache_dir,
            cache_ttl=0 if args.refresh else 86400,
            timeout=args.timeout,
        )
        if args.command == "search":
            record = client.search(args.identifier, page=args.page, limit=args.limit)
        else:
            fields = (
                [s.strip() for s in args.fields.split(",")]
                if args.fields is not None
                else None
            )
            record = client.get(
                args.identifier, fields=fields, include_steam=args.steam
            )
        output = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as destination:
                destination.write(output)
        else:
            sys.stdout.write(output)
        return 0
    except BrokenPipeError:
        return 0
    except (FetchError, ParseError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"gnd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
