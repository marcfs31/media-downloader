"""`media-downloader <url>` — the standalone path for downloading a link
without going through the browser extension. Same engine, same DRM refusal.

`media-downloader-decrypt <file>` reverses --encrypt: it's a separate command
(rather than a flag on the same command) because decrypting is a distinct
action on an existing file, not a download.

`media-downloader-playlist <file>...` runs the same engine over every track in
a playlist file (see playlist.py for the format), one at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
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
    resolve_search,
    sha256_of,
)
from .playlist import Playlist, PlaylistEntry, find_existing, parse_playlist_file


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


@dataclass
class EntryResult:
    """One track's outcome, printed as the run summary and (with --report)
    serialized to JSON so a 100-track run can be checked over afterwards."""

    index: int
    title: str
    artists: str
    kind: str
    status: str  # downloaded | existing | planned | duplicate | unavailable | failed
    url: str | None = None
    path: str | None = None
    matched_title: str | None = None  # what a search actually resolved to
    detail: str | None = None  # why it was skipped or how it failed


def _resolve_entry_target(entry: PlaylistEntry) -> tuple[str, str, str | None]:
    """(video_id, url, matched_title) for a downloadable entry. Hits the
    network only for a search entry, which has no video ID until resolved."""
    if entry.video_id is not None and entry.url is not None:
        return entry.video_id, entry.url, None
    match = resolve_search(entry.query) if entry.query else None
    if match is None:
        raise DownloadError(f"No YouTube results for: {entry.query or entry.label}")
    return match.video_id, match.url, match.title


def run_playlist(
    playlist: Playlist,
    dest_dir: Path,
    options: DownloadOptions,
    dry_run: bool = False,
) -> list[EntryResult]:
    """Download every track of `playlist` into `dest_dir`, one at a time.

    A single track failing never stops the run — with 100 tracks, one
    region-locked video shouldn't cost you the other 99. Every outcome comes
    back in the returned list for the caller to summarize.
    """
    results: list[EntryResult] = []
    seen: set[str] = set()
    total = len(playlist.entries)

    def on_progress(p: Progress) -> None:
        if p.percent is not None:
            speed = f" {p.speed}" if p.speed else ""
            print(f"\r  {p.percent:5.1f}%{speed}", end="", file=sys.stderr, flush=True)

    for position, entry in enumerate(playlist.entries, start=1):
        result = EntryResult(
            index=entry.index,
            title=entry.title,
            artists=entry.artists,
            kind=entry.kind,
            status="failed",
        )
        results.append(result)
        print(f"[{position}/{total}] {entry.label}", file=sys.stderr)

        if entry.kind == "unavailable":
            result.status = "unavailable"
            result.detail = entry.note
            print(f"  skipped: {entry.note}", file=sys.stderr)
            continue

        key = entry.dedupe_key
        if key is not None and key in seen:
            result.status = "duplicate"
            result.detail = "Already handled earlier in this playlist."
            print("  skipped: repeat of an earlier track", file=sys.stderr)
            continue

        if dry_run and entry.kind == "search":
            # Resolving would mean a network round-trip per track, which is
            # exactly what --dry-run is for avoiding.
            result.status = "planned"
            result.detail = f"Would search YouTube for: {entry.query}"
            print(f"  would search: {entry.query}", file=sys.stderr)
            continue

        try:
            video_id, url, matched_title = _resolve_entry_target(entry)
        except DownloadError as exc:
            result.detail = str(exc)
            print(f"  failed: {exc}", file=sys.stderr)
            continue

        result.url = url
        result.matched_title = matched_title
        if matched_title is not None:
            print(f"  search matched: {matched_title}", file=sys.stderr)

        # Test before recording, or a video entry collides with itself: its
        # parsed key and its resolved key are the same string. Both get
        # recorded — the parsed one stops a repeated search from going back
        # over the network, the resolved one catches two different entries
        # landing on the same video.
        video_key = f"video:{video_id}"
        already_seen = video_key in seen
        seen.add(video_key)
        if key is not None:
            seen.add(key)
        if already_seen:
            result.status = "duplicate"
            result.detail = "Already handled earlier in this playlist."
            print("  skipped: repeat of an earlier track", file=sys.stderr)
            continue

        existing = find_existing(dest_dir, video_id)
        if existing is not None:
            result.status = "existing"
            result.path = str(existing)
            print(f"  already downloaded: {existing.name}", file=sys.stderr)
            continue

        if dry_run:
            result.status = "planned"
            result.detail = f"Would download {url}"
            print(f"  would download: {url}", file=sys.stderr)
            continue

        try:
            path = download(url, dest_dir, on_progress, options)
        except (DownloadError, OSError) as exc:
            # OSError too: a full disk or an unwritable folder partway through
            # a 100-track run should cost that track, not the whole run.
            result.detail = str(exc)
            print(f"\n  failed: {exc}", file=sys.stderr)
            continue
        result.status = "downloaded"
        result.path = str(path)
        print(f"\n  saved: {path.name}", file=sys.stderr)

    return results


def _print_summary(playlist: Playlist, results: list[EntryResult]) -> None:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    tally = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    print(f"\n{playlist.name}: {len(results)} tracks — {tally}", file=sys.stderr)

    attention = [r for r in results if r.status in ("failed", "unavailable")]
    if attention:
        print("Needs attention:", file=sys.stderr)
        for result in attention:
            print(f"  {result.index}. {result.title} — {result.detail}", file=sys.stderr)


def playlist_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="media-downloader-playlist",
        description="Download every track listed in a playlist file. Each playlist "
        "gets its own folder under --dest, tracks already downloaded there are "
        "skipped, and one failing track never stops the rest of the run. Exits "
        "non-zero if any track failed; tracks the playlist itself marks as having "
        "no video are reported but don't fail the run.",
    )
    parser.add_argument("files", nargs="+", type=Path, help="Playlist file(s) to download")
    parser.add_argument(
        "-d",
        "--dest",
        type=Path,
        default=Path.cwd(),
        help="Parent directory for the per-playlist folders (default: current directory)",
    )
    parser.add_argument(
        "-a",
        "--audio-only",
        action="store_true",
        help="Extract just the audio track instead of video",
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
        "--dry-run",
        action="store_true",
        help="Parse and show what would be downloaded without downloading anything",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write a JSON report of every track's outcome to this path",
    )
    args = parser.parse_args(argv)

    options = DownloadOptions(
        audio_only=args.audio_only,
        quality=args.quality,
        video_format=args.video_format,
        audio_format=args.audio_format,
    )

    report: list[dict[str, object]] = []
    failed = 0
    for source in args.files:
        try:
            playlist = parse_playlist_file(source)
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not playlist.entries:
            print(f"error: no tracks found in {source}", file=sys.stderr)
            return 1

        dest_dir = args.dest / playlist.dirname
        print(f"\n=== {playlist.name} -> {dest_dir} ===", file=sys.stderr)
        results = run_playlist(playlist, dest_dir, options, dry_run=args.dry_run)
        _print_summary(playlist, results)
        failed += sum(1 for r in results if r.status == "failed")
        report.append(
            {
                "playlist": playlist.name,
                "source": str(source),
                "dest": str(dest_dir),
                "tracks": [asdict(r) for r in results],
            }
        )

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nReport: {args.report}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
