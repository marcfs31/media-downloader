"""Native messaging host: speaks the browser's length-prefixed JSON protocol
on stdin/stdout. The extension's background worker connects to this via
browser.runtime.connectNative("com.media_downloader.host") and sends
{"action": "download", "url": ..., "id": ...}; we stream back progress/done/
error messages tagged with the same id.

Protocol reference:
https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, BinaryIO

from .downloader import DownloadError, DownloadOptions, Progress, download

DEFAULT_DEST = Path.home() / "Downloads" / "MediaDownloader"

# Native messaging caps a single host->browser message at 1 MB; ours are tiny,
# but the browser->host direction also caps at 4 GB which we never approach.
_HEADER = struct.Struct("=I")


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    raw_length = stream.read(_HEADER.size)
    if len(raw_length) < _HEADER.size:
        return None  # browser closed the pipe
    (length,) = _HEADER.unpack(raw_length)
    payload = stream.read(length)
    if len(payload) < length:
        return None
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        return None
    return message


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message).encode("utf-8")
    stream.write(_HEADER.pack(len(payload)))
    stream.write(payload)
    stream.flush()


def handle_download(request: dict[str, Any], out: BinaryIO) -> None:
    request_id = str(request.get("id", ""))
    url = request.get("url")
    if not isinstance(url, str) or not url:
        write_message(out, {"id": request_id, "type": "error", "message": "Missing url"})
        return

    last_percent: list[float] = [-5.0]

    def on_progress(p: Progress) -> None:
        # Throttle: only forward whole-ish percent steps so we don't flood the
        # pipe on fast downloads.
        if p.percent is not None and p.percent - last_percent[0] < 1.0:
            return
        if p.percent is not None:
            last_percent[0] = p.percent
        write_message(
            out,
            {
                "id": request_id,
                "type": "progress",
                "percent": p.percent,
                "speed": p.speed,
                "eta": p.eta,
            },
        )

    options = DownloadOptions(
        audio_only=bool(request.get("audio")),
        encrypt=bool(request.get("encrypt")),
        quality=request.get("quality") or None,
        video_format=str(request.get("video_format") or "mp4"),
        audio_format=str(request.get("audio_format") or "m4a"),
        image_format=request.get("image_format") or None,
        download_all=bool(request.get("download_all")),
        strip_metadata=bool(request.get("strip_metadata")),
    )
    try:
        path = download(url, DEFAULT_DEST, on_progress, options)
        write_message(out, {"id": request_id, "type": "done", "path": str(path)})
    except DownloadError as exc:
        write_message(out, {"id": request_id, "type": "error", "message": str(exc)})
    except Exception as exc:  # keep the host alive; report, don't crash
        write_message(out, {"id": request_id, "type": "error", "message": f"Unexpected: {exc}"})


def main() -> None:
    stdin = sys.stdin.buffer
    # fd 1 is the extension's framed protocol channel, and a single stray
    # print — from us, a library (yt-dlp's progress bar), or a child process
    # like ffmpeg — corrupts it and makes the browser drop the connection.
    # Steal the fd for protocol use and point fd 1 at stderr so stray writes
    # land somewhere harmless instead.
    protocol_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    stdout = os.fdopen(protocol_fd, "wb")
    while True:
        message = read_message(stdin)
        if message is None:
            break
        if message.get("action") == "download":
            handle_download(message, stdout)
        else:
            write_message(
                stdout,
                {
                    "id": str(message.get("id", "")),
                    "type": "error",
                    "message": f"Unknown action: {message.get('action')!r}",
                },
            )


if __name__ == "__main__":
    main()
