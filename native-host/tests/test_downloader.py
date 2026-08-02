from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from media_downloader.downloader import (
    DownloadError,
    DrmProtectedError,
    _looks_drm_protected,
    classify_url,
    download_direct,
    filename_from_response,
    unique_path,
    url_extension,
)


def fake_response(
    headers: dict[str, str] | None = None,
    chunks: list[bytes] | None = None,
    status: int = 200,
) -> mock.MagicMock:
    response = mock.MagicMock()
    response.headers = headers or {}
    response.status_code = status
    response.iter_content.return_value = iter(chunks or [])
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=False)
    if status >= 400:
        import requests

        response.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    else:
        response.raise_for_status.return_value = None
    return response


class TestUrlExtension:
    def test_simple(self) -> None:
        assert url_extension("https://x.test/a/b/movie.MP4") == "mp4"

    def test_no_extension(self) -> None:
        assert url_extension("https://x.test/watch") is None

    def test_query_ignored(self) -> None:
        assert url_extension("https://x.test/song.mp3?token=abc.def") == "mp3"


class TestClassifyUrl:
    def test_direct_by_extension(self) -> None:
        assert classify_url("https://x.test/v.webm", head_check=False) == "direct"

    def test_page_without_head_check(self) -> None:
        assert classify_url("https://x.test/watch?v=123", head_check=False) == "page"

    def test_direct_by_content_type(self) -> None:
        head = fake_response(headers={"content-type": "video/mp4"})
        with mock.patch("requests.head", return_value=head):
            assert classify_url("https://x.test/stream") == "direct"

    def test_page_when_head_fails(self) -> None:
        import requests

        with mock.patch("requests.head", side_effect=requests.ConnectionError):
            assert classify_url("https://x.test/watch") == "page"

    def test_rejects_non_http(self) -> None:
        with pytest.raises(DownloadError):
            classify_url("file:///etc/passwd", head_check=False)


class TestFilenameFromResponse:
    def test_content_disposition_wins(self) -> None:
        response = fake_response(headers={"content-disposition": 'attachment; filename="clip.mp4"'})
        assert filename_from_response("https://x.test/dl?id=1", response) == "clip.mp4"

    def test_content_disposition_path_traversal_stripped(self) -> None:
        response = fake_response(
            headers={"content-disposition": 'attachment; filename="../../evil.sh"'}
        )
        assert filename_from_response("https://x.test/dl", response) == "evil.sh"

    def test_falls_back_to_url_path(self) -> None:
        response = fake_response()
        assert filename_from_response("https://x.test/media/song.mp3", response) == "song.mp3"

    def test_falls_back_to_content_type(self) -> None:
        response = fake_response(headers={"content-type": "image/png"})
        assert filename_from_response("https://x.test/dl", response) == "download.png"


class TestUniquePath:
    def test_free_name_used_as_is(self, tmp_path: Path) -> None:
        assert unique_path(tmp_path, "a.mp4") == tmp_path / "a.mp4"

    def test_collision_suffixed(self, tmp_path: Path) -> None:
        (tmp_path / "a.mp4").touch()
        (tmp_path / "a (1).mp4").touch()
        assert unique_path(tmp_path, "a.mp4") == tmp_path / "a (2).mp4"


class TestDownloadDirect:
    def test_streams_to_named_file(self, tmp_path: Path) -> None:
        response = fake_response(
            headers={"content-length": "8"},
            chunks=[b"1234", b"5678"],
        )
        with mock.patch("requests.get", return_value=response):
            path = download_direct("https://x.test/v.mp4", tmp_path)
        assert path == tmp_path / "v.mp4"
        assert path.read_bytes() == b"12345678"
        assert not path.with_suffix(".mp4.part").exists()

    def test_reports_progress(self, tmp_path: Path) -> None:
        response = fake_response(headers={"content-length": "8"}, chunks=[b"1234", b"5678"])
        seen: list[float] = []
        with mock.patch("requests.get", return_value=response):
            download_direct(
                "https://x.test/v.mp4",
                tmp_path,
                progress=lambda p: seen.append(p.percent or 0.0),
            )
        assert seen == [50.0, 100.0]

    def test_http_error_raises_download_error(self, tmp_path: Path) -> None:
        response = fake_response(status=404)
        with mock.patch("requests.get", return_value=response):
            with pytest.raises(DownloadError):
                download_direct("https://x.test/missing.mp4", tmp_path)

    def test_network_error_raises_download_error(self, tmp_path: Path) -> None:
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(DownloadError, match="Download failed"):
                download_direct("https://x.test/v.mp4", tmp_path)


class TestDrmDetection:
    def test_top_level_flag(self) -> None:
        assert _looks_drm_protected({"_has_drm": True})

    def test_all_formats_drm(self) -> None:
        info: dict[str, Any] = {"formats": [{"has_drm": True}, {"has_drm": True}]}
        assert _looks_drm_protected(info)

    def test_some_formats_clear(self) -> None:
        info: dict[str, Any] = {"formats": [{"has_drm": True}, {"has_drm": False}]}
        assert not _looks_drm_protected(info)

    def test_clean_info(self) -> None:
        assert not _looks_drm_protected({"formats": [{"has_drm": False}]})

    def test_drm_error_is_download_error_subclass(self) -> None:
        assert issubclass(DrmProtectedError, DownloadError)


class TestYtdlpFormatSelector:
    def test_with_ffmpeg_prefers_merge_but_excludes_drm(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=True)
        assert "+ba" in fmt  # merge path available
        assert "has_drm!=?true" in fmt  # none-tolerant DRM exclusion

    def test_without_ffmpeg_never_requests_a_merge(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=False)
        assert "+" not in fmt  # a merge would abort without ffmpeg
        assert "has_drm!=?true" in fmt


class TestFindFfmpeg:
    def test_falls_back_to_known_dirs_when_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import media_downloader.downloader as dl

        fake_ffmpeg = tmp_path / "ffmpeg"
        fake_ffmpeg.write_text("#!/bin/sh\n")
        fake_ffmpeg.chmod(0o755)
        monkeypatch.setattr(dl.shutil, "which", lambda _: None)
        monkeypatch.setattr(dl, "FFMPEG_FALLBACK_DIRS", (str(tmp_path),))
        assert dl.find_ffmpeg() == str(fake_ffmpeg)

    def test_returns_none_when_nowhere_to_be_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import media_downloader.downloader as dl

        monkeypatch.setattr(dl.shutil, "which", lambda _: None)
        monkeypatch.setattr(dl, "FFMPEG_FALLBACK_DIRS", (str(tmp_path / "empty"),))
        assert dl.find_ffmpeg() is None
