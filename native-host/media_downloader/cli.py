"""`media-downloader <url>` — the standalone path for downloading a link
without going through the browser extension. Same engine, same DRM refusal.

`media-downloader-decrypt <file>` reverses --encrypt: it's a separate command
(rather than a flag on the same command) because decrypting is a distinct
action on an existing file, not a download.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import get_download_all_default, set_download_all_default
from .crypto import DEFAULT_KEY_PATH, CryptoError, decrypt_file, get_or_create_key
from .downloader import (
    AUDIO_FORMATS,
    IMAGE_FORMATS,
    QUALITY_HEIGHTS,
    VIDEO_FORMATS,
    DownloadError,
    DownloadOptions,
    Progress,
    download,
    inspect_post,
    sha256_of,
)


def _resolve_download_all(url: str, cli_flag: bool | None) -> bool:
    """cli_flag: True (--download-all), False (--first-only), or None (ask).
    A persisted default only kicks in when the flag wasn't given at all —
    an explicit flag on the command line always wins."""
    if cli_flag is not None:
        return cli_flag
    if get_download_all_default():
        return True
    if not sys.stdin.isatty():
        return False  # no one to ask; keep today's single-item behavior

    count = inspect_post(url)
    if count <= 1:
        return False
    answer = input(f"This post has {count} items. Download all of them? [y/N] ").strip().lower()
    download_all = answer in ("y", "yes")
    remember = input("Always do this for multi-item posts? [y/N] ").strip().lower()
    if remember in ("y", "yes"):
        set_download_all_default(download_all)
    return download_all


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
    parser.add_argument(
        "-a",
        "--audio-only",
        action="store_true",
        help="Extract just the audio track instead of video",
    )
    parser.add_argument(
        "-e",
        "--encrypt",
        action="store_true",
        help="Encrypt the downloaded file at rest (AES-256-GCM). The result isn't "
        "directly playable — decrypt it first with media-downloader-decrypt. "
        "Not a substitute for full-disk encryption; see crypto.py for what this "
        "does and doesn't protect against.",
    )
    parser.add_argument(
        "-q",
        "--quality",
        choices=sorted(QUALITY_HEIGHTS) + ["best"],
        default=None,
        help="Cap video resolution (default: best available)",
    )
    parser.add_argument(
        "--video-format",
        choices=sorted(VIDEO_FORMATS),
        default="mp4",
        help="Video output container (default: mp4)",
    )
    parser.add_argument(
        "--audio-format",
        choices=sorted(AUDIO_FORMATS),
        default="m4a",
        help="Audio output format, with --audio-only (default: m4a)",
    )
    parser.add_argument(
        "--image-format",
        choices=sorted(IMAGE_FORMATS),
        default=None,
        help="Convert a downloaded image to this format (needs ffmpeg)",
    )
    parser.add_argument(
        "--strip-metadata",
        action="store_true",
        help="Remove embedded metadata (EXIF GPS/device info, container tags) via a "
        "lossless ffmpeg remux (needs ffmpeg)",
    )
    multi = parser.add_mutually_exclusive_group()
    multi.add_argument(
        "--download-all",
        dest="download_all",
        action="store_true",
        default=None,
        help="For a multi-item post (several images/videos on one tweet/gallery/etc.), "
        "download every item instead of asking",
    )
    multi.add_argument(
        "--first-only",
        dest="download_all",
        action="store_false",
        help="For a multi-item post, always download just the first item without asking",
    )
    args = parser.parse_args(argv)

    def on_progress(p: Progress) -> None:
        if p.percent is not None:
            speed = f" {p.speed}" if p.speed else ""
            print(f"\r{p.percent:5.1f}%{speed}", end="", file=sys.stderr, flush=True)

    options = DownloadOptions(
        audio_only=args.audio_only,
        encrypt=args.encrypt,
        quality=args.quality,
        video_format=args.video_format,
        audio_format=args.audio_format,
        image_format=args.image_format,
        download_all=_resolve_download_all(args.url, args.download_all),
        strip_metadata=args.strip_metadata,
    )

    try:
        path = download(args.url, args.dest, on_progress, options)
    except DownloadError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    print(f"\nSaved: {path}", file=sys.stderr)
    print(f"SHA-256: {sha256_of(path)}", file=sys.stderr)
    return 0


def decrypt_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="media-downloader-decrypt",
        description="Decrypt a file previously downloaded with media-downloader --encrypt.",
    )
    parser.add_argument("file", type=Path, help="The .enc file to decrypt")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Output path (default: alongside it)"
    )
    args = parser.parse_args(argv)

    try:
        key = get_or_create_key(DEFAULT_KEY_PATH)
        result = decrypt_file(args.file, key, dest=args.output)
    except (CryptoError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Decrypted: {result}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
