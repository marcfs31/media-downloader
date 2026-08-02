"""Core download logic, shared by the native messaging host and the CLI.

Two paths:
- direct:  the URL points at an actual media file (by extension or by the
           server's Content-Type) — streamed to disk with requests.
- ytdlp:   anything else is handed to yt-dlp, which knows how to extract the
           real media from thousands of sites. DRM-protected streams are
           refused outright — this tool downloads what the browser could
           already save, it does not circumvent protection.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

DIRECT_MEDIA_EXTENSIONS = {
    "mp4",
    "webm",
    "mov",
    "mkv",
    "avi",
    "m4v",
    "mp3",
    "m4a",
    "wav",
    "oga",
    "ogg",
    "weba",
    "flac",
    "aac",
    "opus",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp",
    "avif",
    "svg",
    "bmp",
    "tiff",
    "pdf",
}

DIRECT_CONTENT_TYPE_RE = re.compile(r"^(video|audio|image)/|^application/pdf$")

CHUNK_SIZE = 1024 * 256


@dataclass
class Progress:
    percent: float | None = None
    speed: str | None = None
    eta: int | None = None


ProgressCallback = Callable[[Progress], None]


class DownloadError(Exception):
    """A download failed for a reason worth showing to the user."""


class DrmProtectedError(DownloadError):
    """The requested stream is DRM-protected; downloading it is out of scope."""


def url_extension(url: str) -> str | None:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return name.rsplit(".", 1)[-1].lower()


def classify_url(url: str, head_check: bool = True, timeout: float = 10.0) -> str:
    """Return "direct" for URLs we can stream as-is, "page" for everything else."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DownloadError(f"Unsupported URL scheme: {parsed.scheme or '(none)'}")

    ext = url_extension(url)
    if ext in DIRECT_MEDIA_EXTENSIONS:
        return "direct"

    if head_check:
        try:
            response = requests.head(url, allow_redirects=True, timeout=timeout)
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            if DIRECT_CONTENT_TYPE_RE.match(content_type):
                return "direct"
        except requests.RequestException:
            pass

    return "page"


def filename_from_response(url: str, response: requests.Response) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", disposition)
    if match:
        candidate = unquote(match.group(1)).strip().strip('"')
        # A server-supplied name is untrusted input: keep only the basename so
        # a malicious "../../" can't steer the write outside dest_dir.
        candidate = Path(candidate.replace("\\", "/")).name
        if candidate:
            return candidate

    path_name = Path(unquote(urlparse(url).path)).name
    if path_name and "." in path_name:
        return path_name

    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    ext = content_type.rsplit("/", 1)[-1] if "/" in content_type else "bin"
    return f"download.{ext or 'bin'}"


def unique_path(dest_dir: Path, filename: str) -> Path:
    """Never overwrite an existing file — suffix with (1), (2), ... instead."""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(1, 1000):
        candidate = dest_dir / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise DownloadError(f"Too many existing files named like {filename} in {dest_dir}")


def download_direct(
    url: str,
    dest_dir: Path,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, allow_redirects=True, timeout=timeout) as response:
            response.raise_for_status()
            target = unique_path(dest_dir, filename_from_response(url, response))
            total = int(response.headers.get("content-length") or 0)
            written = 0
            partial = target.with_suffix(target.suffix + ".part")
            with open(partial, "wb") as fh:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    fh.write(chunk)
                    written += len(chunk)
                    if progress and total:
                        progress(Progress(percent=100.0 * written / total))
            partial.rename(target)
            return target
    except requests.RequestException as exc:
        raise DownloadError(f"Download failed: {exc}") from exc


# Browsers spawn native messaging hosts with a minimal GUI environment whose
# PATH lacks Homebrew/MacPorts locations, so a plain shutil.which("ffmpeg")
# misses an ffmpeg the user demonstrably has. Check the usual suspects too.
FFMPEG_FALLBACK_DIRS = (
    "/opt/homebrew/bin",  # Homebrew on Apple silicon
    "/usr/local/bin",  # Homebrew on Intel macs, common on Linux
    "/opt/local/bin",  # MacPorts
)


def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for directory in FFMPEG_FALLBACK_DIRS:
        candidate = Path(directory) / "ffmpeg"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def ytdlp_format_selector(have_ffmpeg: bool) -> str:
    """Build the yt-dlp format string.

    Never touches DRM-protected formats; combined with the explicit _has_drm
    check in download_via_ytdlp this makes "we don't do DRM" structural. The
    `!=?` (none-tolerant) operator is essential: many extractors (e.g.
    generic) don't set has_drm at all, and a plain `!=` filter would reject
    those perfectly fine formats.

    Merging separate best-video + best-audio streams requires ffmpeg, so
    without it we restrict selection to single-file (premerged) formats —
    lower maximum quality on some sites, but it always works.
    """
    drm_free = "[has_drm!=?true]"
    if have_ffmpeg:
        return f"bv*{drm_free}+ba{drm_free}/b{drm_free}"
    return f"b{drm_free}/bv*{drm_free}/ba{drm_free}"


def download_via_ytdlp(
    url: str,
    dest_dir: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Download a page's media via yt-dlp. Refuses DRM-protected content."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError(
            "yt-dlp is not installed. Install the native host with its dependencies "
            "(see native-host/README section of the repo README)."
        ) from exc

    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path: list[Path] = []
    ffmpeg_path = find_ffmpeg()

    def hook(d: dict[str, object]) -> None:
        status = d.get("status")
        if status == "downloading" and progress is not None:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes")
            percent = None
            if isinstance(total, (int, float)) and isinstance(downloaded, (int, float)) and total:
                percent = 100.0 * downloaded / total
            speed = d.get("_speed_str")
            eta = d.get("eta")
            progress(
                Progress(
                    percent=percent,
                    speed=str(speed).strip() if speed else None,
                    eta=int(eta) if isinstance(eta, (int, float)) else None,
                )
            )
        elif status == "finished":
            filename = d.get("filename")
            if isinstance(filename, str):
                final_path.append(Path(filename))

    options = {
        "outtmpl": str(dest_dir / "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        # quiet does NOT silence the progress bar, and the bar prints to
        # stdout — which, under the native messaging host, is the framed
        # protocol channel. Progress reaches callers via progress_hooks.
        "noprogress": True,
        "format": ytdlp_format_selector(ffmpeg_path is not None),
    }
    if ffmpeg_path is not None:
        options["ffmpeg_location"] = ffmpeg_path

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise DownloadError("yt-dlp could not extract anything from this URL.")
            if _looks_drm_protected(info):
                raise DrmProtectedError(
                    "This stream is DRM-protected. Downloading it would require "
                    "circumventing copy protection, which this tool does not do."
                )
            ydl.download([url])
    except DownloadError:
        raise
    except Exception as exc:  # yt-dlp raises its own exception zoo
        message = str(exc)
        if "drm" in message.lower():
            raise DrmProtectedError(
                "This stream is DRM-protected and cannot be downloaded."
            ) from exc
        raise DownloadError(f"yt-dlp failed: {message}") from exc

    if final_path:
        return final_path[-1]

    raise DownloadError("yt-dlp finished but reported no output file.")


def _looks_drm_protected(info: dict[str, object]) -> bool:
    if info.get("_has_drm"):
        return True
    formats = info.get("formats")
    if isinstance(formats, list) and formats:
        drm_flags = [bool(f.get("has_drm")) for f in formats if isinstance(f, dict)]
        if drm_flags and all(drm_flags):
            return True
    return False


def download(
    url: str,
    dest_dir: Path,
    progress: ProgressCallback | None = None,
) -> Path:
    """Classify and download a URL by whichever path fits it."""
    if classify_url(url) == "direct":
        return download_direct(url, dest_dir, progress)
    return download_via_ytdlp(url, dest_dir, progress)
