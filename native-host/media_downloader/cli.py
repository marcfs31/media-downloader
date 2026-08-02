"""`media-downloader <url>` — the standalone path for downloading a link
without going through the browser extension. Same engine, same DRM refusal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .downloader import DownloadError, Progress, download


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="media-downloader",
        description="Download the media behind a URL — a direct file link is streamed "
        "as-is; a page URL is handed to yt-dlp. DRM-protected streams are refused.",
    )
    parser.add_argument("url", help="Direct media URL or a page URL yt-dlp supports")
    parser.add_argument(
        "-d",
        "--dest",
        type=Path,
        default=Path.cwd(),
        help="Destination directory (default: current directory)",
    )
    args = parser.parse_args(argv)

    def on_progress(p: Progress) -> None:
        if p.percent is not None:
            speed = f" {p.speed}" if p.speed else ""
            print(f"\r{p.percent:5.1f}%{speed}", end="", file=sys.stderr, flush=True)

    try:
        path = download(args.url, args.dest, on_progress)
    except DownloadError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    print(f"\nSaved: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
