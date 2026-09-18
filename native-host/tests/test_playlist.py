from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from media_downloader.cli import playlist_main, run_playlist
from media_downloader.downloader import DownloadError, DownloadOptions, SearchResult
from media_downloader.playlist import (
    Playlist,
    PlaylistEntry,
    find_existing,
    parse_playlist,
    parse_playlist_file,
    safe_dirname,
)

SAMPLE = """\
# Playlist: Arce
# Owner: Someone
# 4 tracks (some titles repeat)

1. Toke de Queda — Arce
   https://www.youtube.com/watch?v=vm9XBp8G1Rw

2. Cenizienta — Arce, Omar Montes
   https://www.youtube.com/watch?v=sEFGHSL5wX0

3. Cenizienta — Arce, Omar Montes (repeat)
   https://www.youtube.com/watch?v=sEFGHSL5wX0

4. La Vida Que No Es Nuestra — Arce
   No official YouTube videoclip found.
   Spotify: https://open.spotify.com/track/7a4YfV8nO0wZlFwOOP1XZE
"""


class TestParsePlaylist:
    def test_name_comes_from_header(self) -> None:
        assert parse_playlist(SAMPLE).name == "Arce"

    def test_name_falls_back_to_filename_stem(self) -> None:
        playlist = parse_playlist(
            "1. A — B\n   https://youtu.be/vm9XBp8G1Rw", source=Path("x/to-sing.md")
        )
        assert playlist.name == "to-sing"

    def test_explicit_name_wins_over_header(self) -> None:
        assert parse_playlist(SAMPLE, name="Override").name == "Override"

    def test_parses_every_numbered_track(self) -> None:
        entries = parse_playlist(SAMPLE).entries
        assert [e.index for e in entries] == [1, 2, 3, 4]

    def test_splits_title_and_artists(self) -> None:
        entry = parse_playlist(SAMPLE).entries[1]
        assert entry.title == "Cenizienta"
        assert entry.artists == "Arce, Omar Montes"

    def test_watch_url_becomes_video_entry(self) -> None:
        entry = parse_playlist(SAMPLE).entries[0]
        assert entry.kind == "video"
        assert entry.video_id == "vm9XBp8G1Rw"
        assert entry.url == "https://www.youtube.com/watch?v=vm9XBp8G1Rw"

    def test_track_without_youtube_link_is_unavailable(self) -> None:
        entry = parse_playlist(SAMPLE).entries[3]
        assert entry.kind == "unavailable"
        assert entry.note is not None
        assert "No official YouTube videoclip found." in entry.note
        # The Spotify reference is kept in the note rather than mistaken for a
        # download target.
        assert "open.spotify.com" in entry.note

    def test_header_comments_are_not_tracks(self) -> None:
        assert len(parse_playlist(SAMPLE).entries) == 4

    def test_empty_text_yields_no_entries(self) -> None:
        assert parse_playlist("").entries == ()

    def test_title_keeps_its_own_hyphens_and_slashes(self) -> None:
        text = (
            "1. Escúchame Mujer - 2023 — Fondo Flamenco\n"
            "   https://www.youtube.com/watch?v=wxcnSvlxEjA\n"
            "2. Numb / Encore — JAY-Z, Linkin Park\n"
            "   https://www.youtube.com/watch?v=1CafhMobx00\n"
            "3. Loco pero no tanto - Freestyle Session #21 — ZARAMAY, Nahuel The Coach\n"
            "   https://www.youtube.com/watch?v=1w7OgIMMRc4\n"
        )
        entries = parse_playlist(text).entries
        assert [e.title for e in entries] == [
            "Escúchame Mujer - 2023",
            "Numb / Encore",
            "Loco pero no tanto - Freestyle Session #21",
        ]
        assert entries[1].artists == "JAY-Z, Linkin Park"

    def test_title_without_artist_separator(self) -> None:
        entry = parse_playlist("1. Just A Title\n   https://youtu.be/vm9XBp8G1Rw").entries[0]
        assert entry.title == "Just A Title"
        assert entry.artists == ""
        assert entry.label == "Just A Title"

    def test_multiline_track_body_stays_with_its_track(self) -> None:
        text = (
            "1. First — A\n"
            "   some prose about this track\n"
            "   https://www.youtube.com/watch?v=vm9XBp8G1Rw\n"
            "2. Second — B\n"
            "   https://www.youtube.com/watch?v=Ky1ElDUyvU0\n"
        )
        entries = parse_playlist(text).entries
        assert [e.video_id for e in entries] == ["vm9XBp8G1Rw", "Ky1ElDUyvU0"]


class TestUrlForms:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=vm9XBp8G1Rw",
            "https://youtube.com/watch?v=vm9XBp8G1Rw",
            "https://m.youtube.com/watch?v=vm9XBp8G1Rw",
            "https://music.youtube.com/watch?v=vm9XBp8G1Rw",
            "https://www.youtube.com/watch?v=vm9XBp8G1Rw&list=PL123",
            "https://youtu.be/vm9XBp8G1Rw",
            "https://www.youtube.com/shorts/vm9XBp8G1Rw",
            "https://www.youtube.com/embed/vm9XBp8G1Rw",
        ],
    )
    def test_recognized_video_urls_normalize_to_one_watch_url(self, url: str) -> None:
        entry = parse_playlist(f"1. T — A\n   {url}").entries[0]
        assert entry.kind == "video"
        assert entry.url == "https://www.youtube.com/watch?v=vm9XBp8G1Rw"

    def test_trailing_punctuation_is_trimmed(self) -> None:
        entry = parse_playlist("1. T — A\n   (https://youtu.be/vm9XBp8G1Rw).").entries[0]
        assert entry.video_id == "vm9XBp8G1Rw"

    def test_malformed_video_id_is_not_a_video(self) -> None:
        entry = parse_playlist("1. T — A\n   https://www.youtube.com/watch?v=tooshort").entries[0]
        assert entry.kind == "unavailable"

    def test_non_youtube_url_is_not_a_video(self) -> None:
        entry = parse_playlist("1. T — A\n   https://open.spotify.com/track/abc").entries[0]
        assert entry.kind == "unavailable"

    def test_search_results_url_becomes_search_entry(self) -> None:
        entry = parse_playlist(
            "1. Ermitaño — Astola, Ratón\n"
            "   https://www.youtube.com/results?search_query=Astola+Raton+Ermitano+video+oficial"
        ).entries[0]
        assert entry.kind == "search"
        assert entry.query == "Astola Raton Ermitano video oficial"

    def test_watch_url_wins_over_a_search_url_in_the_same_track(self) -> None:
        entry = parse_playlist(
            "1. T — A\n"
            "   https://www.youtube.com/results?search_query=whatever\n"
            "   https://www.youtube.com/watch?v=vm9XBp8G1Rw"
        ).entries[0]
        assert entry.kind == "video"


class TestDedupeKey:
    def test_same_video_shares_a_key(self) -> None:
        entries = parse_playlist(SAMPLE).entries
        assert entries[1].dedupe_key == entries[2].dedupe_key

    def test_different_videos_have_different_keys(self) -> None:
        entries = parse_playlist(SAMPLE).entries
        assert entries[0].dedupe_key != entries[1].dedupe_key

    def test_search_key_ignores_case(self) -> None:
        a = PlaylistEntry(1, "t", "a", "search", query="Dax Eternity")
        b = PlaylistEntry(2, "t", "a", "search", query="dax eternity")
        assert a.dedupe_key == b.dedupe_key

    def test_unavailable_entries_are_never_deduped(self) -> None:
        assert parse_playlist(SAMPLE).entries[3].dedupe_key is None


class TestSafeDirname:
    def test_passes_through_a_plain_name(self) -> None:
        assert safe_dirname("To sing") == "To sing"

    def test_strips_path_separators(self) -> None:
        assert "/" not in safe_dirname("AC/DC: best")

    def test_never_returns_empty(self) -> None:
        assert safe_dirname("") == "playlist"
        assert safe_dirname("   ") == "playlist"
        assert safe_dirname("...") == "playlist"
        # Separators collapse to a placeholder rather than vanishing.
        assert safe_dirname("///") == "_"

    def test_available_on_the_playlist(self) -> None:
        assert parse_playlist(SAMPLE).dirname == "Arce"


class TestParsePlaylistFile:
    def test_reads_utf8_from_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "arce.md"
        path.write_text(SAMPLE, encoding="utf-8")
        playlist = parse_playlist_file(path)
        assert playlist.name == "Arce"
        assert playlist.source == path
        assert len(playlist.entries) == 4


class TestFindExisting:
    def test_finds_a_file_by_its_id_marker(self, tmp_path: Path) -> None:
        target = tmp_path / "Toke de Queda [vm9XBp8G1Rw].mp4"
        target.write_bytes(b"x")
        assert find_existing(tmp_path, "vm9XBp8G1Rw") == target

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        (tmp_path / "Something Else [Ky1ElDUyvU0].mp4").write_bytes(b"x")
        assert find_existing(tmp_path, "vm9XBp8G1Rw") is None

    def test_missing_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert find_existing(tmp_path / "nope", "vm9XBp8G1Rw") is None

    def test_brackets_are_matched_literally_not_as_a_glob_class(self, tmp_path: Path) -> None:
        # "[abc]" is a glob character class; a glob-based lookup would match
        # the decoy (single char "v") and miss the real file.
        (tmp_path / "decoy v.mp4").write_bytes(b"x")
        real = tmp_path / "Real [vm9XBp8G1Rw].m4a"
        real.write_bytes(b"x")
        assert find_existing(tmp_path, "vm9XBp8G1Rw") == real

    def test_ignores_directories(self, tmp_path: Path) -> None:
        (tmp_path / "nested [vm9XBp8G1Rw]").mkdir()
        assert find_existing(tmp_path, "vm9XBp8G1Rw") is None


def _playlist(*entries: PlaylistEntry) -> Playlist:
    return Playlist(name="Test", entries=entries)


def _video(index: int, video_id: str) -> PlaylistEntry:
    return PlaylistEntry(
        index=index,
        title=f"Track {index}",
        artists="Someone",
        kind="video",
        url=f"https://www.youtube.com/watch?v={video_id}",
        video_id=video_id,
    )


class TestRunPlaylist:
    def _run(
        self,
        playlist: Playlist,
        dest: Path,
        download: Any = None,
        search: Any = None,
        dry_run: bool = False,
    ) -> list[Any]:
        download = download or (lambda url, d, p, o: d / "out.mp4")
        search = search or (lambda q: None)
        with (
            mock.patch("media_downloader.cli.download", side_effect=download) as dl,
            mock.patch("media_downloader.cli.resolve_search", side_effect=search),
        ):
            results = run_playlist(playlist, dest, DownloadOptions(), dry_run=dry_run)
        self.download_mock = dl
        return results

    def test_downloads_each_distinct_track(self, tmp_path: Path) -> None:
        results = self._run(_playlist(_video(1, "vm9XBp8G1Rw"), _video(2, "Ky1ElDUyvU0")), tmp_path)
        assert [r.status for r in results] == ["downloaded", "downloaded"]
        assert self.download_mock.call_count == 2

    def test_repeated_video_is_downloaded_once(self, tmp_path: Path) -> None:
        results = self._run(_playlist(_video(1, "vm9XBp8G1Rw"), _video(2, "vm9XBp8G1Rw")), tmp_path)
        assert [r.status for r in results] == ["downloaded", "duplicate"]
        assert self.download_mock.call_count == 1

    def test_unavailable_track_is_reported_not_attempted(self, tmp_path: Path) -> None:
        entry = PlaylistEntry(1, "T", "A", "unavailable", note="No official video.")
        results = self._run(_playlist(entry), tmp_path)
        assert results[0].status == "unavailable"
        assert results[0].detail == "No official video."
        assert self.download_mock.call_count == 0

    def test_already_downloaded_track_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "Track 1 [vm9XBp8G1Rw].mp4").write_bytes(b"x")
        results = self._run(_playlist(_video(1, "vm9XBp8G1Rw")), tmp_path)
        assert results[0].status == "existing"
        assert results[0].path is not None
        assert self.download_mock.call_count == 0

    def test_one_failure_does_not_stop_the_rest(self, tmp_path: Path) -> None:
        def flaky(url: str, dest: Path, progress: Any, options: Any) -> Path:
            if "vm9XBp8G1Rw" in url:
                raise DownloadError("region locked")
            return dest / "out.mp4"

        results = self._run(
            _playlist(_video(1, "vm9XBp8G1Rw"), _video(2, "Ky1ElDUyvU0")), tmp_path, download=flaky
        )
        assert [r.status for r in results] == ["failed", "downloaded"]
        assert results[0].detail == "region locked"

    def test_disk_error_fails_one_track_not_the_run(self, tmp_path: Path) -> None:
        def out_of_space(url: str, dest: Path, progress: Any, options: Any) -> Path:
            if "vm9XBp8G1Rw" in url:
                raise OSError("No space left on device")
            return dest / "out.mp4"

        results = self._run(
            _playlist(_video(1, "vm9XBp8G1Rw"), _video(2, "Ky1ElDUyvU0")),
            tmp_path,
            download=out_of_space,
        )
        assert [r.status for r in results] == ["failed", "downloaded"]

    def test_search_entry_is_resolved_then_downloaded(self, tmp_path: Path) -> None:
        entry = PlaylistEntry(1, "Eternity", "Dax", "search", query="Dax Eternity official")
        hit = SearchResult(
            video_id="vm9XBp8G1Rw",
            url="https://www.youtube.com/watch?v=vm9XBp8G1Rw",
            title="Dax - Eternity (Official Video)",
        )
        results = self._run(_playlist(entry), tmp_path, search=lambda q: hit)
        assert results[0].status == "downloaded"
        assert results[0].url == hit.url
        # The matched title is surfaced so a wrong guess is spottable.
        assert results[0].matched_title == hit.title

    def test_search_with_no_results_fails_that_track_only(self, tmp_path: Path) -> None:
        entry = PlaylistEntry(1, "Obscure", "Nobody", "search", query="nothing matches this")
        results = self._run(_playlist(entry, _video(2, "Ky1ElDUyvU0")), tmp_path)
        assert [r.status for r in results] == ["failed", "downloaded"]
        assert "No YouTube results" in (results[0].detail or "")

    def test_search_resolving_onto_an_existing_video_is_a_duplicate(self, tmp_path: Path) -> None:
        entry = PlaylistEntry(2, "Same song", "A", "search", query="same song")
        hit = SearchResult(
            video_id="vm9XBp8G1Rw",
            url="https://www.youtube.com/watch?v=vm9XBp8G1Rw",
            title="Same song",
        )
        results = self._run(
            _playlist(_video(1, "vm9XBp8G1Rw"), entry), tmp_path, search=lambda q: hit
        )
        assert [r.status for r in results] == ["downloaded", "duplicate"]

    def test_repeated_search_query_is_resolved_once(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def search(query: str) -> SearchResult:
            calls.append(query)
            return SearchResult("vm9XBp8G1Rw", "https://www.youtube.com/watch?v=vm9XBp8G1Rw", "T")

        entry_a = PlaylistEntry(1, "T", "A", "search", query="same query")
        entry_b = PlaylistEntry(2, "T", "A", "search", query="same query")
        results = self._run(_playlist(entry_a, entry_b), tmp_path, search=search)
        assert [r.status for r in results] == ["downloaded", "duplicate"]
        assert calls == ["same query"]

    def test_dry_run_downloads_nothing(self, tmp_path: Path) -> None:
        results = self._run(_playlist(_video(1, "vm9XBp8G1Rw")), tmp_path, dry_run=True)
        assert results[0].status == "planned"
        assert self.download_mock.call_count == 0

    def test_dry_run_does_not_hit_the_network_for_searches(self, tmp_path: Path) -> None:
        def explode(query: str) -> SearchResult:
            raise AssertionError("--dry-run must not resolve searches")

        entry = PlaylistEntry(1, "T", "A", "search", query="q")
        results = self._run(_playlist(entry), tmp_path, search=explode, dry_run=True)
        assert results[0].status == "planned"


class TestPlaylistMain:
    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "list.md"
        path.write_text(f"# Playlist: Test\n\n{body}", encoding="utf-8")
        return path

    def test_exits_zero_when_every_track_downloads(self, tmp_path: Path) -> None:
        source = self._write(tmp_path, "1. T — A\n   https://youtu.be/vm9XBp8G1Rw\n")
        with mock.patch("media_downloader.cli.download", return_value=tmp_path / "out.mp4"):
            assert playlist_main([str(source), "-d", str(tmp_path)]) == 0

    def test_exits_nonzero_when_a_track_fails(self, tmp_path: Path) -> None:
        source = self._write(tmp_path, "1. T — A\n   https://youtu.be/vm9XBp8G1Rw\n")
        with mock.patch("media_downloader.cli.download", side_effect=DownloadError("nope")):
            assert playlist_main([str(source), "-d", str(tmp_path)]) == 1

    def test_unavailable_track_alone_does_not_fail_the_run(self, tmp_path: Path) -> None:
        source = self._write(tmp_path, "1. T — A\n   No official YouTube videoclip found.\n")
        assert playlist_main([str(source), "-d", str(tmp_path)]) == 0

    def test_missing_file_is_an_error(self, tmp_path: Path) -> None:
        assert playlist_main([str(tmp_path / "nope.md"), "-d", str(tmp_path)]) == 1

    def test_file_without_tracks_is_an_error(self, tmp_path: Path) -> None:
        source = tmp_path / "empty.md"
        source.write_text("# Playlist: Nothing\n", encoding="utf-8")
        assert playlist_main([str(source), "-d", str(tmp_path)]) == 1

    def test_each_playlist_gets_its_own_folder(self, tmp_path: Path) -> None:
        source = self._write(tmp_path, "1. T — A\n   https://youtu.be/vm9XBp8G1Rw\n")
        with mock.patch("media_downloader.cli.download", return_value=tmp_path / "o.mp4") as dl:
            playlist_main([str(source), "-d", str(tmp_path / "music")])
        assert dl.call_args.args[1] == tmp_path / "music" / "Test"

    def test_report_records_every_track(self, tmp_path: Path) -> None:
        source = self._write(
            tmp_path,
            "1. T — A\n   https://youtu.be/vm9XBp8G1Rw\n2. U — B\n   Nothing here.\n",
        )
        report = tmp_path / "nested" / "report.json"
        with mock.patch("media_downloader.cli.download", return_value=tmp_path / "o.mp4"):
            playlist_main([str(source), "-d", str(tmp_path), "--report", str(report)])
        import json

        data = json.loads(report.read_text(encoding="utf-8"))
        assert [t["status"] for t in data[0]["tracks"]] == ["downloaded", "unavailable"]
