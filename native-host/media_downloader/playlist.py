"""Parses the plain-text playlist files in `playlists/` into download targets.

Deliberately pure — no network, no yt-dlp, no filesystem beyond reading the
file itself — so the fiddly part (which line starts a track, which URL is a
real video vs. a search-results page) stays testable without touching
anything. The batch runner that actually downloads lives in cli.py.

The format is the one a human would write by hand::

    # Playlist: Arce
    # 38 tracks

    1. Toke de Queda — Arce
       https://www.youtube.com/watch?v=vm9XBp8G1Rw

    22. La Vida Que No Es Nuestra — Arce
        No official YouTube videoclip found.
        Spotify: https://open.spotify.com/track/7a4YfV8nO0wZlFwOOP1XZE

    35. Ermitaño — Astola, Ratón
        https://www.youtube.com/results?search_query=Astola+Raton+Ermitano

A numbered line opens a track; every indented line under it belongs to that
track. Three kinds come out the other end, because the source files really
do contain all three: a direct video link, a search-results link standing in
for "the official video, whichever one that is", and a track with no YouTube
link at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

EntryKind = Literal["video", "search", "unavailable"]

# A track opens with "12. Title — Artist". Continuation lines are indented and
# never start with a number, so this doubles as the block separator.
_ENTRY_START_RE = re.compile(r"^(\d+)\.[ \t]+(.*\S)[ \t]*$")
_PLAYLIST_NAME_RE = re.compile(r"^#[ \t]*Playlist:[ \t]*(.+?)[ \t]*$", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"]+")
# YouTube IDs are exactly 11 chars of base64url — validate rather than trust,
# so a malformed line becomes an "unavailable" track instead of a bad request.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
# Title and artists are separated by an em dash. Split on the LAST one: titles
# in these files carry hyphens and slashes of their own ("Numb / Encore",
# "Escúchame Mujer - 2023"), artist lists are comma-separated names.
_TITLE_ARTIST_SEP = "—"

_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
_YOUTUBE_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})


@dataclass(frozen=True)
class PlaylistEntry:
    """One numbered track. `kind` says which of the trailing fields is set:
    "video" -> url/video_id, "search" -> query, "unavailable" -> note."""

    index: int
    title: str
    artists: str
    kind: EntryKind
    url: str | None = None
    video_id: str | None = None
    query: str | None = None
    note: str | None = None

    @property
    def label(self) -> str:
        return f"{self.title} — {self.artists}" if self.artists else self.title

    @property
    def dedupe_key(self) -> str | None:
        """Identifies tracks that would download the same file twice. None for
        tracks that can't be downloaded at all — each of those is worth
        reporting separately even when two of them look alike."""
        if self.kind == "video":
            return f"video:{self.video_id}"
        if self.kind == "search":
            return f"search:{(self.query or '').casefold()}"
        return None


@dataclass(frozen=True)
class Playlist:
    name: str
    entries: tuple[PlaylistEntry, ...]
    source: Path | None = None

    @property
    def dirname(self) -> str:
        """Filesystem-safe directory name for this playlist's downloads."""
        return safe_dirname(self.name)


def safe_dirname(name: str) -> str:
    cleaned = _UNSAFE_FILENAME_RE.sub("_", name).strip().strip(".")
    return cleaned[:100] or "playlist"


def _split_title_artists(text: str) -> tuple[str, str]:
    title, sep, artists = text.rpartition(_TITLE_ARTIST_SEP)
    if not sep:
        return text.strip(), ""
    return title.strip(), artists.strip()


def _video_id_from_url(url: str) -> str | None:
    """The 11-char video ID behind a watch/youtu.be/shorts URL, else None."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    candidate: str | None = None

    if host in _YOUTUBE_SHORT_HOSTS:
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            values = parse_qs(parsed.query).get("v")
            candidate = values[0] if values else None
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else None

    if candidate and _VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def _search_query_from_url(url: str) -> str | None:
    """The decoded query behind a /results?search_query=... URL, else None."""
    parsed = urlparse(url)
    if parsed.netloc.lower() not in _YOUTUBE_HOSTS or parsed.path != "/results":
        return None
    values = parse_qs(parsed.query).get("search_query")
    query = values[0].strip() if values else ""
    return query or None


def _iter_urls(text: str) -> list[str]:
    """URLs in `text`, with trailing sentence punctuation trimmed off."""
    return [match.group(0).rstrip(".,;:)") for match in _URL_RE.finditer(text)]


def _entry_from_block(index: int, headline: str, body_lines: list[str]) -> PlaylistEntry:
    title, artists = _split_title_artists(headline)
    body = "\n".join(body_lines)
    urls = _iter_urls(body)

    for url in urls:
        video_id = _video_id_from_url(url)
        if video_id is not None:
            return PlaylistEntry(
                index=index,
                title=title,
                artists=artists,
                kind="video",
                url=f"https://www.youtube.com/watch?v={video_id}",
                video_id=video_id,
            )

    for url in urls:
        query = _search_query_from_url(url)
        if query is not None:
            return PlaylistEntry(
                index=index, title=title, artists=artists, kind="search", query=query
            )

    note = " ".join(line.strip() for line in body_lines if line.strip())
    return PlaylistEntry(
        index=index,
        title=title,
        artists=artists,
        kind="unavailable",
        note=note or "No YouTube link in the playlist file.",
    )


def parse_playlist(text: str, *, source: Path | None = None, name: str | None = None) -> Playlist:
    """Parse playlist text. `name` overrides the `# Playlist:` header; without
    either, the source filename stem is used."""
    header_name: str | None = None
    entries: list[PlaylistEntry] = []
    open_index: int | None = None
    open_headline = ""
    open_body: list[str] = []

    def close_open_entry() -> None:
        if open_index is not None:
            entries.append(_entry_from_block(open_index, open_headline, open_body))

    for line in text.splitlines():
        start = _ENTRY_START_RE.match(line)
        if start is not None:
            close_open_entry()
            open_index = int(start.group(1))
            open_headline = start.group(2)
            open_body = []
            continue

        if open_index is None:
            match = _PLAYLIST_NAME_RE.match(line)
            if match is not None and header_name is None:
                header_name = match.group(1)
        elif not line.lstrip().startswith("#"):
            open_body.append(line)

    close_open_entry()

    resolved = name or header_name or (source.stem if source is not None else None) or "playlist"
    return Playlist(name=resolved, entries=tuple(entries), source=source)


def parse_playlist_file(path: Path) -> Playlist:
    return parse_playlist(path.read_text(encoding="utf-8"), source=path)


def find_existing(dest_dir: Path, video_id: str) -> Path | None:
    """The already-downloaded file for `video_id`, if this playlist was run
    before. yt-dlp's output template ends every name with "[<id>].<ext>" and
    every post-processing step here keeps that name, so the marker is a
    reliable "already have it" check across runs.

    Matched by substring rather than glob on purpose: the brackets in the
    template are glob character-class syntax and would never match literally.
    """
    if not dest_dir.is_dir():
        return None
    marker = f"[{video_id}]"
    for path in sorted(dest_dir.iterdir()):
        if path.is_file() and marker in path.name:
            return path
    return None
