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

# Video output containers deliberately stop at mp4/mkv, not webm: our format
# selector prefers H.264+AAC for playability (see ytdlp_format_selector), and
# webm can only hold VP8/VP9/AV1+Opus/Vorbis — muxing avc1/mp4a into it isn't
# a remux, it's a re-encode, which is slow, lossy, and not something this
# tool does implicitly.
VIDEO_FORMATS = frozenset({"mp4", "mkv"})
# FFmpegExtractAudio genuinely transcodes, so all of these are real options.
AUDIO_FORMATS = frozenset({"m4a", "mp3", "opus", "wav"})
IMAGE_FORMATS = frozenset({"jpg", "png", "webp"})
QUALITY_HEIGHTS: dict[str, int] = {
    "2160p": 2160,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}


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


@dataclass
class DownloadOptions:
    """Bundles the growing set of optional download knobs so call sites (CLI,
    native host, companion server) don't have to pass six loose kwargs each."""

    audio_only: bool = False
    encrypt: bool = False
    quality: str | None = None  # None/"best", or a QUALITY_HEIGHTS key — video only
    video_format: str = "mp4"  # a VIDEO_FORMATS member
    audio_format: str = "m4a"  # an AUDIO_FORMATS member
    image_format: str | None = None  # None, or an IMAGE_FORMATS member — direct-image only
    # A tweet/gallery/etc. attaching several images or videos to one post is
    # a single yt-dlp "playlist" result with multiple entries. download_all
    # controls what happens then: False (default) keeps today's behavior of
    # grabbing just the first/primary item; True fetches every entry and
    # zips them into one file, since the rest of this pipeline (encryption,
    # checksums, serving to a phone) is built around a single resulting path.
    download_all: bool = False
    # Strips embedded metadata (EXIF GPS/device info on images, container
    # tags — title/uploader/encoder/etc. — on video/audio) via a lossless
    # ffmpeg stream-copy remux. Off by default since it's an extra ffmpeg
    # pass most people don't need; on for anyone who cares what travels with
    # the file itself, separate from anything this tool logs or transmits.
    strip_metadata: bool = False

    def validate(self) -> None:
        if (
            self.quality is not None
            and self.quality != "best"
            and self.quality not in QUALITY_HEIGHTS
        ):
            raise DownloadError(
                f"Unknown quality {self.quality!r} (expected one of: best, "
                f"{', '.join(QUALITY_HEIGHTS)})"
            )
        if self.video_format not in VIDEO_FORMATS:
            raise DownloadError(
                f"Unknown video format {self.video_format!r} "
                f"(expected one of: {', '.join(sorted(VIDEO_FORMATS))})"
            )
        if self.audio_format not in AUDIO_FORMATS:
            raise DownloadError(
                f"Unknown audio format {self.audio_format!r} "
                f"(expected one of: {', '.join(sorted(AUDIO_FORMATS))})"
            )
        if self.image_format is not None and self.image_format not in IMAGE_FORMATS:
            raise DownloadError(
                f"Unknown image format {self.image_format!r} "
                f"(expected one of: {', '.join(sorted(IMAGE_FORMATS))})"
            )


def url_extension(url: str) -> str | None:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return name.rsplit(".", 1)[-1].lower()


TIKTOK_SHORT_LINK_RE = re.compile(r"https?://(?:vm|vt)\.tiktok\.com/\w+")
TIKTOK_PHOTO_URL_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/(?P<user>@[\w.-]+)/photo/(?P<id>\d+)"
)
TIKTOK_UNIVERSAL_DATA_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S
)


def _resolve_tiktok_short_link(url: str, timeout: float = 10.0) -> str:
    """vm.tiktok.com/vt.tiktok.com short links redirect to the real
    tiktok.com URL, which is what TIKTOK_PHOTO_URL_RE needs to match
    against. Falls back to the original URL if resolution fails."""
    if not TIKTOK_SHORT_LINK_RE.match(url):
        return url
    try:
        response = requests.head(url, allow_redirects=True, timeout=timeout)
        return response.url
    except requests.RequestException:
        return url


def _tiktok_photo_match(url: str) -> re.Match[str] | None:
    return TIKTOK_PHOTO_URL_RE.match(_resolve_tiktok_short_link(url))


def _fetch_tiktok_item(user: str, item_id: str, timeout: float = 15.0) -> dict[str, object]:
    """TikTok's /photo/ URL alias gates its post data behind a CAPTCHA for
    any non-interactive request, but the exact same post (TikTok shares one
    ID space between photo and video posts) is also reachable at its
    /video/ URL alias, which renders full data server-side through a plain,
    unauthenticated GET — no session, no challenge. Only the /photo/ path
    has this quirk; every other yt-dlp-supported URL is untouched by it."""
    url = f"https://www.tiktok.com/{user}/video/{item_id}"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DownloadError(f"Could not reach TikTok: {exc}") from exc

    import json

    match = TIKTOK_UNIVERSAL_DATA_RE.search(response.text)
    if match is None:
        raise DownloadError("Could not read TikTok's post data (page format may have changed).")
    try:
        scope = json.loads(match.group(1))["__DEFAULT_SCOPE__"]
        item = scope["webapp.video-detail"]["itemInfo"]["itemStruct"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DownloadError(
            "Could not read TikTok's post data (page format may have changed)."
        ) from exc
    if not isinstance(item, dict) or "imagePost" not in item:
        raise DownloadError("This TikTok post isn't a photo post.")
    return item


def _tiktok_live_photo_url(img: dict[str, object]) -> str | None:
    """A "Photo Mode" slide posted from a Live Photo carries a short muted
    video loop alongside its still frame. TikTok doesn't publish this
    field's name, so this checks the shapes ordinary TikTok video URLs use
    elsewhere in this same payload — a flat urlList (like imageURL itself),
    or one nested under playAddr/downloadAddr (like item.video) — and
    returns None if neither is present, so a still image downloads exactly
    as before. Unverified against a real Live Photo post; if a slide that's
    known to be one still downloads as a flat image, this shape is wrong
    and needs adjusting against an actual sample."""
    video = img.get("video")
    if not isinstance(video, dict):
        return None
    for container in (video, video.get("playAddr"), video.get("downloadAddr")):
        if not isinstance(container, dict):
            continue
        url_list = container.get("urlList")
        if isinstance(url_list, list) and url_list and isinstance(url_list[0], str):
            return url_list[0]
    return None


def download_tiktok_photo_post(
    match: re.Match[str],
    dest_dir: Path,
    progress: ProgressCallback | None = None,
    download_all: bool = False,
    audio_only: bool = False,
    image_format: str | None = None,
) -> Path:
    """Downloads every image (or, with audio_only, just the background
    track) from a TikTok "Photo Mode" post. download_all=False (the
    default, matching every other multi-item post in this app) keeps only
    the first image. A slide posted from a Live Photo downloads its motion
    clip instead of a flat still, when TikTok's response exposes one (see
    _tiktok_live_photo_url)."""
    item = _fetch_tiktok_item(match.group("user"), match.group("id"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    title = _safe_title({"title": item.get("desc"), "id": item.get("id")})

    if audio_only:
        music = item.get("music")
        music_url = music.get("playUrl") if isinstance(music, dict) else None
        if not isinstance(music_url, str) or not music_url:
            raise DownloadError("This TikTok photo post has no audio track.")
        return download_direct(music_url, dest_dir, progress)

    image_post = item.get("imagePost")
    images = image_post.get("images") if isinstance(image_post, dict) else None
    urls: list[str] = []
    for img in images or []:
        if not isinstance(img, dict):
            continue
        live_photo_url = _tiktok_live_photo_url(img)
        if live_photo_url is not None:
            urls.append(live_photo_url)
            continue
        image_url_field = img.get("imageURL")
        url_list = image_url_field.get("urlList") if isinstance(image_url_field, dict) else None
        if isinstance(url_list, list) and url_list and isinstance(url_list[0], str):
            urls.append(url_list[0])
    if not urls:
        raise DownloadError("Could not find any images in this TikTok post.")
    if not download_all:
        urls = urls[:1]

    paths: list[Path] = []
    total = len(urls)
    for i, image_url in enumerate(urls, 1):
        path = download_direct(image_url, dest_dir, image_format=image_format)
        numbered = unique_path(dest_dir, f"{title} - {i:02d}{path.suffix}")
        path.rename(numbered)
        paths.append(numbered)
        if progress:
            progress(Progress(percent=100.0 * i / total))

    if len(paths) == 1:
        return paths[0]
    return zip_files(paths, dest_dir, f"{title} ({len(paths)} items)")


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


def sha256_of(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """Streams the file rather than loading it whole — cheap, unlike the
    AEAD encryption step, which does need the whole file in memory."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


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


def convert_image(path: Path, image_format: str, ffmpeg_path: str) -> Path:
    """Converts an already-downloaded image to a different format via ffmpeg.
    No-op (returns path unchanged) if it's already in that format."""
    import subprocess

    if path.suffix.lstrip(".").lower() == image_format:
        return path
    target = unique_path(path.parent, path.stem + f".{image_format}")
    result = subprocess.run(
        [ffmpeg_path, "-y", "-i", str(path), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.is_file():
        raise DownloadError(f"Image conversion to {image_format} failed: {result.stderr.strip()}")
    path.unlink()
    return target


def strip_metadata(path: Path, ffmpeg_path: str) -> Path:
    """Removes embedded metadata (EXIF GPS/device info on images, container
    tags on video/audio) via a lossless stream-copy remux — same container,
    same encoded data, metadata dropped. Runs as a final pass over whatever
    download_direct()/download_via_ytdlp() produced, so it works the same
    regardless of which path got the file. Writes to a temp name and swaps
    it into place so the final filename is unchanged."""
    import subprocess

    tmp_target = path.with_name(f".{path.name}.stripped{path.suffix}")
    result = subprocess.run(
        [ffmpeg_path, "-y", "-i", str(path), "-map_metadata", "-1", "-c", "copy", str(tmp_target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not tmp_target.is_file():
        raise DownloadError(f"Stripping metadata failed: {result.stderr.strip()}")
    tmp_target.replace(path)
    return path


VIDEO_CONTAINER_EXTENSIONS = frozenset({"mp4", "webm", "mov", "mkv", "avi", "m4v"})
# ffprobe reports H.264 as "h264"; avc1 is the codec's fourcc tag rather than
# something ffprobe puts in codec_name, but it costs nothing to tolerate both.
H264_CODEC_NAMES = frozenset({"h264", "avc1"})
AAC_CODEC_NAMES = frozenset({"aac"})


def _probe_codecs(path: Path, ffprobe_path: str) -> tuple[str | None, str | None]:
    """Returns (video_codec, audio_codec) — the codec_name of the first
    stream of each kind ffprobe reports. Either comes back None if that
    stream is missing, or if ffprobe fails or its output can't be read; a
    probe failure is never raised, since normalize_video_codec treats
    "unknown" the same as "already fine" and leaves the file alone."""
    import json
    import subprocess

    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    streams: list[object] = []
    if result.returncode == 0:
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("streams"), list):
            streams = parsed["streams"]

    video_codec: str | None = None
    audio_codec: str | None = None
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_name = stream.get("codec_name")
        if not isinstance(codec_name, str):
            continue
        if stream.get("codec_type") == "video" and video_codec is None:
            video_codec = codec_name
        elif stream.get("codec_type") == "audio" and audio_codec is None:
            audio_codec = codec_name
    return video_codec, audio_codec


def normalize_video_codec(path: Path, ffmpeg_path: str, ffprobe_path: str) -> Path:
    """Re-encodes to H.264 a video file that came back from yt-dlp in
    something a lot of "plays .mp4" players/devices can't decode (AV1,
    HEVC, VP9, ...). ytdlp_format_selector already prefers avc1, but its
    fallback tiers don't check codec at all, so a site that simply has no
    avc1 rendition for some content (observed on TikTok) still lands here
    anyway. Re-encodes in place, same container the file already has; audio
    is stream-copied when it's already AAC, re-encoded to AAC otherwise (a
    merge can hand back Opus or other audio the container can't always hold
    as-is). No-op, returned unchanged, when the extension isn't a known
    video container, ffprobe can't find a video stream, or that stream is
    already h264/avc1."""
    if path.suffix.lstrip(".").lower() not in VIDEO_CONTAINER_EXTENSIONS:
        return path

    video_codec, audio_codec = _probe_codecs(path, ffprobe_path)
    if video_codec is None or video_codec in H264_CODEC_NAMES:
        return path

    import subprocess

    audio_codec_args = ["-c:a", "copy" if audio_codec in AAC_CODEC_NAMES else "aac"]
    tmp_target = path.with_name(f".{path.name}.normalized{path.suffix}")
    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i",
            str(path),
            "-c:v",
            "libx264",
            "-crf",
            "18",
            *audio_codec_args,
            str(tmp_target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not tmp_target.is_file():
        raise DownloadError(f"Normalizing video to H.264 failed: {result.stderr.strip()}")
    tmp_target.replace(path)
    return path


def download_direct(
    url: str,
    dest_dir: Path,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
    image_format: str | None = None,
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

            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            is_image = content_type.startswith("image/") or url_extension(url) in {
                "jpg",
                "jpeg",
                "png",
                "gif",
                "webp",
                "avif",
                "bmp",
                "tiff",
            }
            if image_format and is_image:
                ffmpeg_path = find_ffmpeg()
                if ffmpeg_path is None:
                    raise DownloadError(
                        f"Converting to {image_format} needs ffmpeg, which isn't installed."
                    )
                target = convert_image(target, image_format, ffmpeg_path)
            return target
    except requests.RequestException as exc:
        raise DownloadError(f"Download failed: {exc}") from exc


# Browsers spawn native messaging hosts with a minimal GUI environment whose
# PATH lacks Homebrew/MacPorts locations, so a plain shutil.which() misses an
# ffmpeg/ffprobe the user demonstrably has. Check the usual suspects too.
FFMPEG_FALLBACK_DIRS = (
    "/opt/homebrew/bin",  # Homebrew on Apple silicon
    "/usr/local/bin",  # Homebrew on Intel macs, common on Linux
    "/opt/local/bin",  # MacPorts
)


def _find_ffmpeg_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in FFMPEG_FALLBACK_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_ffmpeg() -> str | None:
    return _find_ffmpeg_tool("ffmpeg")


def find_ffprobe() -> str | None:
    return _find_ffmpeg_tool("ffprobe")


def ytdlp_format_selector(
    have_ffmpeg: bool, audio_only: bool = False, quality: str | None = None
) -> str:
    """Build the yt-dlp format string.

    Never touches DRM-protected formats; combined with the explicit _has_drm
    check in download_via_ytdlp this makes "we don't do DRM" structural. The
    `!=?` (none-tolerant) operator is essential: many extractors (e.g.
    generic) don't set has_drm at all, and a plain `!=` filter would reject
    those perfectly fine formats.

    Merging separate best-video + best-audio streams requires ffmpeg, so
    without it we restrict selection to single-file (premerged) formats —
    lower maximum quality on some sites, but it always works.

    Preference order chases compatibility before raw quality: H.264 (avc1) +
    AAC (mp4a) in an mp4 container plays everywhere — QuickTime, phones,
    TVs — while an unconstrained "best" usually lands on VP9/Opus in
    webm/mkv, which macOS can't open out of the box. Codec-tolerant
    fallbacks keep sites without H.264 working at all.

    quality caps the video height (e.g. "720p" -> 720) to trade quality for a
    smaller/faster download; None/"best" leaves it uncapped. Uses the same
    none-tolerant `<=?` so formats missing height metadata aren't wrongly
    excluded. Audio has no "height", so quality is ignored when audio_only.
    """
    drm_free = "[has_drm!=?true]"
    if audio_only:
        if have_ffmpeg:
            # Any best audio is fine — the FFmpegExtractAudio postprocessor
            # (see ytdlp_options) converts it to the requested codec afterwards.
            return f"ba{drm_free}/b{drm_free}"
        return f"ba[ext=m4a]{drm_free}/ba{drm_free}/b{drm_free}"

    height = QUALITY_HEIGHTS.get(quality or "")
    height_filter = f"[height<=?{height}]" if height else ""

    if have_ffmpeg:
        return (
            f"bv*[vcodec^=avc1]{drm_free}{height_filter}+ba[acodec^=mp4a]{drm_free}"
            f"/bv*[ext=mp4]{drm_free}{height_filter}+ba[ext=m4a]{drm_free}"
            f"/b[ext=mp4]{drm_free}{height_filter}"
            f"/bv*{drm_free}{height_filter}+ba{drm_free}"
            f"/b{drm_free}{height_filter}"
        )
    return (
        f"b[ext=mp4]{drm_free}{height_filter}"
        f"/b{drm_free}{height_filter}"
        f"/bv*{drm_free}{height_filter}"
        f"/ba{drm_free}"
    )


def ytdlp_options(
    dest_dir: Path,
    ffmpeg_path: str | None,
    audio_only: bool,
    quality: str | None = None,
    video_format: str = "mp4",
    audio_format: str = "m4a",
    download_all: bool = False,
) -> dict[str, object]:
    """Everything for YoutubeDL except the per-call progress hook."""
    options: dict[str, object] = {
        "outtmpl": str(dest_dir / "%(title)s [%(id)s].%(ext)s"),
        # A single post's own attached images/videos ("multi_video"/gallery
        # entries) are exactly what download_all opts into; noplaylist=True
        # is what keeps a genuine playlist/channel URL to just one item, and
        # that protection stays on regardless of download_all.
        "noplaylist": not download_all,
        "quiet": True,
        "no_warnings": True,
        # quiet does NOT silence the progress bar, and the bar prints to
        # stdout — which, under the native messaging host, is the framed
        # protocol channel. Progress reaches callers via progress_hooks.
        "noprogress": True,
        "format": ytdlp_format_selector(ffmpeg_path is not None, audio_only, quality),
    }
    if ffmpeg_path is not None:
        options["ffmpeg_location"] = ffmpeg_path
        if audio_only:
            options["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": audio_format}
            ]
        else:
            # When video+audio get merged, put them in the requested
            # container when the codecs allow it; mkv is the escape hatch
            # for combinations the requested container can't hold, preferred
            # over failing the download outright.
            options["merge_output_format"] = (
                video_format if video_format == "mkv" else f"{video_format}/mkv"
            )
    return options


def _requested_paths(result: dict[str, object]) -> list[Path]:
    """Extracts the final (post-processed) file path(s) yt-dlp produced for
    a processed result — recurses into "entries" so this works the same for
    a single video and for every item of a multi-item post/playlist."""
    entries = result.get("entries")
    if isinstance(entries, list):
        paths: list[Path] = []
        for entry in entries:
            if isinstance(entry, dict):
                paths.extend(_requested_paths(entry))
        return paths

    # requested_downloads carries the path AFTER postprocessing (e.g. the
    # merged mp4), unlike the per-stream "finished" progress hooks, which
    # would report the last fragment (typically the bare audio track).
    requested = result.get("requested_downloads")
    if isinstance(requested, list) and requested and isinstance(requested[0], dict):
        filepath = requested[0].get("filepath")
        if isinstance(filepath, str) and filepath:
            return [Path(filepath)]
    return []


def _safe_title(info: dict[str, object]) -> str:
    title = str(info.get("title") or info.get("id") or "download")
    return re.sub(r'[\\/:*?"<>|]+', "_", title).strip()[:150] or "download"


def zip_files(paths: list[Path], dest_dir: Path, name: str) -> Path:
    """Bundles multiple downloaded files into one .zip and removes the
    originals — keeps every other layer (encryption, checksums, the
    companion server's single-file serve/save flow) working unmodified
    whether a download produced one file or several."""
    import zipfile

    target = unique_path(dest_dir, f"{name}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            zf.write(path, arcname=path.name)
    for path in paths:
        path.unlink()
    return target


def inspect_post(url: str) -> int:
    """Peeks at a URL without downloading anything, to see whether it's a
    multi-item post — several images/videos attached to one tweet, gallery,
    etc. Returns the item count (1 for a single item, or for anything that
    isn't a yt-dlp-recognized URL at all)."""
    tiktok_match = _tiktok_photo_match(url)
    if tiktok_match is not None:
        try:
            item = _fetch_tiktok_item(tiktok_match.group("user"), tiktok_match.group("id"))
        except DownloadError:
            return 1
        image_post = item.get("imagePost")
        images = image_post.get("images") if isinstance(image_post, dict) else None
        return max(1, len(images)) if isinstance(images, list) else 1
    try:
        import yt_dlp
    except ImportError:
        return 1
    try:
        # extract_flat: skip resolving full format info per entry — this is
        # just a count, not a download, so keep it cheap.
        with yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "noplaylist": False, "extract_flat": True}
        ) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return 1
    if not isinstance(info, dict):
        return 1
    entries = info.get("entries")
    if isinstance(entries, list):
        return max(1, len([e for e in entries if isinstance(e, dict)]))
    return 1


def download_via_ytdlp(
    url: str,
    dest_dir: Path,
    progress: ProgressCallback | None = None,
    audio_only: bool = False,
    quality: str | None = None,
    video_format: str = "mp4",
    audio_format: str = "m4a",
    download_all: bool = False,
) -> Path:
    """Download a page's media via yt-dlp. Refuses DRM-protected content, and
    re-encodes any non-H.264 video result to H.264 when ffmpeg/ffprobe are
    both available (see normalize_video_codec)."""
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

    options = ytdlp_options(
        dest_dir, ffmpeg_path, audio_only, quality, video_format, audio_format, download_all
    )
    options["progress_hooks"] = [hook]

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
            downloaded = ydl.process_ie_result(info, download=True)
    except DownloadError:
        raise
    except Exception as exc:  # yt-dlp raises its own exception zoo
        message = str(exc)
        if "drm" in message.lower():
            raise DrmProtectedError(
                "This stream is DRM-protected and cannot be downloaded."
            ) from exc
        raise DownloadError(f"yt-dlp failed: {message}") from exc

    paths = _requested_paths(downloaded) if isinstance(downloaded, dict) else []
    if not paths and final_path:
        paths = [final_path[-1]]
    if not paths:
        raise DownloadError("yt-dlp finished but reported no output file.")

    if not audio_only and ffmpeg_path is not None:
        ffprobe_path = find_ffprobe()
        if ffprobe_path is not None:
            paths = [normalize_video_codec(p, ffmpeg_path, ffprobe_path) for p in paths]

    if len(paths) == 1:
        return paths[0]

    title = _safe_title(downloaded) if isinstance(downloaded, dict) else "post"
    return zip_files(paths, dest_dir, f"{title} ({len(paths)} items)")


def _looks_drm_protected(info: dict[str, object]) -> bool:
    if info.get("_has_drm"):
        return True
    formats = info.get("formats")
    if isinstance(formats, list) and formats:
        drm_flags = [bool(f.get("has_drm")) for f in formats if isinstance(f, dict)]
        if drm_flags and all(drm_flags):
            return True
    entries = info.get("entries")
    if isinstance(entries, list):
        return any(_looks_drm_protected(e) for e in entries if isinstance(e, dict))
    return False


def download(
    url: str,
    dest_dir: Path,
    progress: ProgressCallback | None = None,
    options: DownloadOptions | None = None,
) -> Path:
    """Classify and download a URL by whichever path fits it.

    options.audio_only always goes through yt-dlp, even for a direct video
    file — extracting the audio track is exactly the postprocessing that
    path has. options.encrypt runs the finished file through
    crypto.encrypt_file (AES-256-GCM, opt-in — see crypto.py for what this
    does and doesn't protect against); the returned Path then has a .enc
    suffix and is not directly playable — decrypt it first
    (media_downloader.crypto.decrypt_file, or the media-downloader-decrypt
    CLI). options.download_all fetches every item of a multi-item post
    (several images/videos on one tweet/gallery/etc.) instead of just the
    first, zipped into a single file. options.strip_metadata removes
    embedded EXIF/container metadata via ffmpeg before any encryption.
    """
    opts = options or DownloadOptions()
    opts.validate()
    tiktok_match = _tiktok_photo_match(url)

    if tiktok_match is not None:
        path = download_tiktok_photo_post(
            tiktok_match,
            dest_dir,
            progress,
            download_all=opts.download_all,
            audio_only=opts.audio_only,
            image_format=opts.image_format,
        )
    elif opts.audio_only:
        path = download_via_ytdlp(
            url,
            dest_dir,
            progress,
            audio_only=True,
            audio_format=opts.audio_format,
            download_all=opts.download_all,
        )
    elif classify_url(url) == "direct":
        path = download_direct(url, dest_dir, progress, image_format=opts.image_format)
    else:
        path = download_via_ytdlp(
            url,
            dest_dir,
            progress,
            quality=opts.quality,
            video_format=opts.video_format,
            download_all=opts.download_all,
        )

    if opts.strip_metadata:
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path is None:
            raise DownloadError("Stripping metadata needs ffmpeg, which isn't installed.")
        path = strip_metadata(path, ffmpeg_path)

    if opts.encrypt:
        from .crypto import encrypt_file, get_or_create_key

        path = encrypt_file(path, get_or_create_key())

    return path
