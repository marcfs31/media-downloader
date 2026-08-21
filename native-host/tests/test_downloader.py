from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from media_downloader.downloader import (
    DownloadError,
    DrmProtectedError,
    _fetch_tiktok_item,
    _looks_drm_protected,
    _tiktok_live_photo_url,
    _tiktok_photo_match,
    classify_url,
    download_direct,
    download_tiktok_photo_post,
    filename_from_response,
    inspect_post,
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


def fake_tiktok_page(
    images: list[str | dict[str, Any]] | None = None,
    music_url: str | None = "https://cdn.test/song.m4a",
) -> str:
    """Builds a synthetic page body shaped like TikTok's own
    __UNIVERSAL_DATA_FOR_REHYDRATION__ payload, with fabricated
    (non-real) image/music URLs — enough to exercise the parser without
    depending on TikTok's actual page structure or real post content. A
    plain string builds an ordinary {imageURL: {urlList: [...]}} slide; a
    dict is used as the raw image object as-is, for tests that need to add
    a Live Photo's "video" field alongside imageURL."""
    import json

    item: dict[str, Any] = {"id": "123456", "desc": "test post"}
    if images is not None:
        item["imagePost"] = {
            "images": [
                img if isinstance(img, dict) else {"imageURL": {"urlList": [img]}} for img in images
            ]
        }
    if music_url is not None:
        item["music"] = {"playUrl": music_url}
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.video-detail": {"itemInfo": {"itemStruct": item}},
        }
    }
    return (
        "<html><body>"
        f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{json.dumps(payload)}</script>'
        "</body></html>"
    )


class TestTiktokPhotoMatch:
    def test_matches_photo_url(self) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        assert match.group("user") == "@someone"
        assert match.group("id") == "123456"

    def test_resolves_short_link(self) -> None:
        head = fake_response()
        head.url = "https://www.tiktok.com/@someone/photo/123456"
        with mock.patch("requests.head", return_value=head):
            match = _tiktok_photo_match("https://vm.tiktok.com/ZN885784b")
        assert match is not None
        assert match.group("id") == "123456"

    def test_ignores_video_url(self) -> None:
        assert _tiktok_photo_match("https://www.tiktok.com/@someone/video/123456") is None

    def test_short_link_resolution_failure_falls_through(self) -> None:
        import requests

        with mock.patch("requests.head", side_effect=requests.ConnectionError):
            assert _tiktok_photo_match("https://vm.tiktok.com/ZN885784b") is None

    def test_ignores_unrelated_url(self) -> None:
        assert _tiktok_photo_match("https://example.test/watch?v=123") is None


class TestFetchTiktokItem:
    def test_extracts_image_post(self) -> None:
        page = fake_response(headers={"content-type": "text/html"})
        page.text = fake_tiktok_page(images=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"])
        with mock.patch("requests.get", return_value=page):
            item = _fetch_tiktok_item("@someone", "123456")
        assert len(item["imagePost"]["images"]) == 2  # type: ignore[index]

    def test_rejects_non_photo_post(self) -> None:
        page = fake_response(headers={"content-type": "text/html"})
        page.text = fake_tiktok_page(images=None)
        with mock.patch("requests.get", return_value=page):
            with pytest.raises(DownloadError, match="isn't a photo post"):
                _fetch_tiktok_item("@someone", "123456")

    def test_missing_universal_data_script(self) -> None:
        page = fake_response(headers={"content-type": "text/html"})
        page.text = "<html><body>nothing here</body></html>"
        with mock.patch("requests.get", return_value=page):
            with pytest.raises(DownloadError, match="page format may have changed"):
                _fetch_tiktok_item("@someone", "123456")

    def test_network_failure(self) -> None:
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError):
            with pytest.raises(DownloadError, match="Could not reach TikTok"):
                _fetch_tiktok_item("@someone", "123456")


class TestDownloadTiktokPhotoPost:
    def _fetch_mock(self, page_text: str) -> Any:
        page = fake_response(headers={"content-type": "text/html"})
        page.text = page_text
        return page

    def _image_response(self, body: bytes = b"fake-jpeg-bytes") -> Any:
        return fake_response(
            headers={"content-type": "image/jpeg", "content-length": str(len(body))},
            chunks=[body],
        )

    def test_download_all_false_keeps_first_image_only(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        page = self._fetch_mock(
            fake_tiktok_page(images=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"])
        )
        with (
            mock.patch("requests.get", side_effect=[page, self._image_response()]),
        ):
            result = download_tiktok_photo_post(match, tmp_path, download_all=False)
        assert result.exists()
        assert result.suffix == ".jpg"
        assert "01" in result.name

    def test_download_all_true_zips_every_image(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        page = self._fetch_mock(
            fake_tiktok_page(images=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg"])
        )
        with mock.patch(
            "requests.get",
            side_effect=[page, self._image_response(b"one"), self._image_response(b"two")],
        ):
            result = download_tiktok_photo_post(match, tmp_path, download_all=True)
        assert result.suffix == ".zip"

    def test_audio_only_downloads_music_track(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        page = self._fetch_mock(fake_tiktok_page(images=["https://cdn.test/a.jpg"]))
        audio = fake_response(
            headers={"content-type": "audio/mp4", "content-length": "3"}, chunks=[b"aac"]
        )
        with mock.patch("requests.get", side_effect=[page, audio]):
            result = download_tiktok_photo_post(match, tmp_path, audio_only=True)
        assert result.exists()

    def test_audio_only_without_music_raises(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        page = self._fetch_mock(fake_tiktok_page(images=["https://cdn.test/a.jpg"], music_url=None))
        with mock.patch("requests.get", return_value=page):
            with pytest.raises(DownloadError, match="no audio track"):
                download_tiktok_photo_post(match, tmp_path, audio_only=True)

    def _video_response(self, body: bytes = b"fake-mp4-bytes") -> Any:
        return fake_response(
            headers={"content-type": "video/mp4", "content-length": str(len(body))},
            chunks=[body],
        )

    def test_live_photo_slide_downloads_its_motion_clip(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        live_slide = {
            "imageURL": {"urlList": ["https://cdn.test/a.jpg"]},
            "video": {"urlList": ["https://cdn.test/a-live.mp4"]},
        }
        page = self._fetch_mock(fake_tiktok_page(images=[live_slide]))
        with mock.patch("requests.get", side_effect=[page, self._video_response()]) as get:
            result = download_tiktok_photo_post(match, tmp_path, download_all=False)
        assert result.exists()
        assert get.call_args_list[1].args[0] == "https://cdn.test/a-live.mp4"

    def test_live_photo_video_nested_under_play_addr(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        live_slide = {
            "imageURL": {"urlList": ["https://cdn.test/a.jpg"]},
            "video": {"playAddr": {"urlList": ["https://cdn.test/a-live.mp4"]}},
        }
        page = self._fetch_mock(fake_tiktok_page(images=[live_slide]))
        with mock.patch("requests.get", side_effect=[page, self._video_response()]) as get:
            download_tiktok_photo_post(match, tmp_path, download_all=False)
        assert get.call_args_list[1].args[0] == "https://cdn.test/a-live.mp4"

    def test_mixed_live_and_plain_slides_keep_their_own_source(self, tmp_path: Path) -> None:
        match = _tiktok_photo_match("https://www.tiktok.com/@someone/photo/123456")
        assert match is not None
        live_slide = {
            "imageURL": {"urlList": ["https://cdn.test/a.jpg"]},
            "video": {"urlList": ["https://cdn.test/a-live.mp4"]},
        }
        plain_slide = "https://cdn.test/b.jpg"
        page = self._fetch_mock(fake_tiktok_page(images=[live_slide, plain_slide]))
        with mock.patch(
            "requests.get",
            side_effect=[page, self._video_response(), self._image_response()],
        ) as get:
            result = download_tiktok_photo_post(match, tmp_path, download_all=True)
        assert result.suffix == ".zip"
        assert get.call_args_list[1].args[0] == "https://cdn.test/a-live.mp4"
        assert get.call_args_list[2].args[0] == "https://cdn.test/b.jpg"


class TestTiktokLivePhotoUrl:
    def test_no_video_field_is_a_plain_slide(self) -> None:
        assert _tiktok_live_photo_url({"imageURL": {"urlList": ["https://cdn.test/a.jpg"]}}) is None

    def test_flat_url_list_on_video(self) -> None:
        img = {"video": {"urlList": ["https://cdn.test/a-live.mp4"]}}
        assert _tiktok_live_photo_url(img) == "https://cdn.test/a-live.mp4"

    def test_url_list_nested_under_play_addr(self) -> None:
        img = {"video": {"playAddr": {"urlList": ["https://cdn.test/a-live.mp4"]}}}
        assert _tiktok_live_photo_url(img) == "https://cdn.test/a-live.mp4"

    def test_url_list_nested_under_download_addr(self) -> None:
        img = {"video": {"downloadAddr": {"urlList": ["https://cdn.test/a-live.mp4"]}}}
        assert _tiktok_live_photo_url(img) == "https://cdn.test/a-live.mp4"

    def test_video_field_present_but_empty_falls_back(self) -> None:
        assert _tiktok_live_photo_url({"video": {}}) is None

    def test_video_field_wrong_type_falls_back(self) -> None:
        assert _tiktok_live_photo_url({"video": "not-a-dict"}) is None


class TestInspectPostTiktokPhoto:
    def test_reports_image_count(self) -> None:
        page = fake_response(headers={"content-type": "text/html"})
        page.text = fake_tiktok_page(
            images=["https://cdn.test/a.jpg", "https://cdn.test/b.jpg", "https://cdn.test/c.jpg"]
        )
        with mock.patch("requests.get", return_value=page):
            assert inspect_post("https://www.tiktok.com/@someone/photo/123456") == 3

    def test_falls_back_to_one_on_fetch_failure(self) -> None:
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError):
            assert inspect_post("https://www.tiktok.com/@someone/photo/123456") == 1


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
    def test_with_ffmpeg_prefers_h264_aac_merge_but_excludes_drm(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=True)
        assert "+ba" in fmt  # merge path available
        assert "has_drm!=?true" in fmt  # none-tolerant DRM exclusion
        # QuickTime/macOS compatibility: H.264+AAC must be tried before an
        # unconstrained best (which tends to land on VP9/Opus webm).
        assert fmt.index("vcodec^=avc1") < fmt.index("/bv*[has_drm!=?true]+ba")
        assert "acodec^=mp4a" in fmt

    def test_with_ffmpeg_still_falls_back_to_any_codec(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=True)
        # Sites without any H.264 rendition must still work: the chain has to
        # end in unconstrained fallbacks.
        assert fmt.endswith("/bv*[has_drm!=?true]+ba[has_drm!=?true]/b[has_drm!=?true]")

    def test_without_ffmpeg_never_requests_a_merge(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=False)
        assert "+" not in fmt  # a merge would abort without ffmpeg
        assert "has_drm!=?true" in fmt

    def test_audio_only_never_requests_video(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        for have_ffmpeg in (True, False):
            fmt = ytdlp_format_selector(have_ffmpeg=have_ffmpeg, audio_only=True)
            assert "bv" not in fmt  # no video stream requested at all
            assert "has_drm!=?true" in fmt


class TestYtdlpOptions:
    def test_audio_only_with_ffmpeg_adds_extract_audio_postprocessor(self, tmp_path: Path) -> None:
        from media_downloader.downloader import ytdlp_options

        options = ytdlp_options(tmp_path, ffmpeg_path="/usr/bin/ffmpeg", audio_only=True)
        assert options["postprocessors"] == [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]

    def test_video_download_has_no_audio_postprocessor(self, tmp_path: Path) -> None:
        from media_downloader.downloader import ytdlp_options

        options = ytdlp_options(tmp_path, ffmpeg_path="/usr/bin/ffmpeg", audio_only=False)
        assert "postprocessors" not in options

    def test_without_ffmpeg_audio_only_has_no_postprocessor_either(self, tmp_path: Path) -> None:
        # There's no ffmpeg to run FFmpegExtractAudio with — the format
        # selector alone (ba-only) has to do the whole job.
        from media_downloader.downloader import ytdlp_options

        options = ytdlp_options(tmp_path, ffmpeg_path=None, audio_only=True)
        assert "postprocessors" not in options


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


class TestFindFfprobe:
    def test_falls_back_to_known_dirs_when_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import media_downloader.downloader as dl

        fake_ffprobe = tmp_path / "ffprobe"
        fake_ffprobe.write_text("#!/bin/sh\n")
        fake_ffprobe.chmod(0o755)
        monkeypatch.setattr(dl.shutil, "which", lambda _: None)
        monkeypatch.setattr(dl, "FFMPEG_FALLBACK_DIRS", (str(tmp_path),))
        assert dl.find_ffprobe() == str(fake_ffprobe)

    def test_returns_none_when_nowhere_to_be_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import media_downloader.downloader as dl

        monkeypatch.setattr(dl.shutil, "which", lambda _: None)
        monkeypatch.setattr(dl, "FFMPEG_FALLBACK_DIRS", (str(tmp_path / "empty"),))
        assert dl.find_ffprobe() is None


class TestDownloadDispatch:
    def test_audio_only_routes_through_ytdlp_even_for_a_direct_file_url(
        self, tmp_path: Path
    ) -> None:
        # A direct .mp4 link would normally stream as-is (download_direct) —
        # audio_only has to override that, since extracting audio is yt-dlp's
        # postprocessing job, not something download_direct can do at all.
        import media_downloader.downloader as dl

        with (
            mock.patch.object(dl, "download_direct") as direct,
            mock.patch.object(dl, "download_via_ytdlp") as via_ytdlp,
        ):
            via_ytdlp.return_value = tmp_path / "song.m4a"
            result = dl.download(
                "https://x.test/v.mp4", tmp_path, options=dl.DownloadOptions(audio_only=True)
            )

        direct.assert_not_called()
        via_ytdlp.assert_called_once_with(
            "https://x.test/v.mp4",
            tmp_path,
            None,
            audio_only=True,
            audio_format="m4a",
            download_all=False,
        )
        assert result == tmp_path / "song.m4a"

    def test_non_audio_direct_file_still_streams_directly(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        with (
            mock.patch.object(dl, "download_direct") as direct,
            mock.patch.object(dl, "download_via_ytdlp") as via_ytdlp,
        ):
            direct.return_value = tmp_path / "v.mp4"
            result = dl.download("https://x.test/v.mp4", tmp_path)

        via_ytdlp.assert_not_called()
        direct.assert_called_once()
        assert result == tmp_path / "v.mp4"


class TestDownloadOptionsValidate:
    def test_default_options_are_valid(self) -> None:
        from media_downloader.downloader import DownloadOptions

        DownloadOptions().validate()  # should not raise

    def test_rejects_unknown_quality(self) -> None:
        from media_downloader.downloader import DownloadOptions

        with pytest.raises(DownloadError, match="Unknown quality"):
            DownloadOptions(quality="4k").validate()

    def test_accepts_best_and_known_heights(self) -> None:
        from media_downloader.downloader import DownloadOptions

        DownloadOptions(quality="best").validate()
        DownloadOptions(quality="720p").validate()

    def test_rejects_unknown_video_format(self) -> None:
        from media_downloader.downloader import DownloadOptions

        with pytest.raises(DownloadError, match="Unknown video format"):
            DownloadOptions(video_format="webm").validate()

    def test_rejects_unknown_audio_format(self) -> None:
        from media_downloader.downloader import DownloadOptions

        with pytest.raises(DownloadError, match="Unknown audio format"):
            DownloadOptions(audio_format="flac").validate()

    def test_rejects_unknown_image_format(self) -> None:
        from media_downloader.downloader import DownloadOptions

        with pytest.raises(DownloadError, match="Unknown image format"):
            DownloadOptions(image_format="gif").validate()

    def test_validate_runs_automatically_inside_download(self, tmp_path: Path) -> None:
        from media_downloader.downloader import DownloadOptions, download

        with pytest.raises(DownloadError, match="Unknown quality"):
            download("https://x.test/v.mp4", tmp_path, options=DownloadOptions(quality="4k"))


class TestQualityHeightCap:
    def test_caps_video_height_with_ffmpeg(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=True, quality="720p")
        assert "[height<=?720]" in fmt

    def test_caps_video_height_without_ffmpeg(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=False, quality="480p")
        assert "[height<=?480]" in fmt

    def test_best_or_none_leaves_uncapped(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        assert "height" not in ytdlp_format_selector(have_ffmpeg=True, quality=None)
        assert "height" not in ytdlp_format_selector(have_ffmpeg=True, quality="best")

    def test_quality_ignored_for_audio_only(self) -> None:
        from media_downloader.downloader import ytdlp_format_selector

        fmt = ytdlp_format_selector(have_ffmpeg=True, audio_only=True, quality="720p")
        assert "height" not in fmt


class TestYtdlpOptionsFormats:
    def test_video_format_sets_merge_output_format(self, tmp_path: Path) -> None:
        from media_downloader.downloader import ytdlp_options

        options = ytdlp_options(tmp_path, "/usr/bin/ffmpeg", False, video_format="mkv")
        assert options["merge_output_format"] == "mkv"

    def test_default_video_format_falls_back_to_mkv(self, tmp_path: Path) -> None:
        from media_downloader.downloader import ytdlp_options

        options = ytdlp_options(tmp_path, "/usr/bin/ffmpeg", False, video_format="mp4")
        assert options["merge_output_format"] == "mp4/mkv"

    def test_audio_format_sets_postprocessor_codec(self, tmp_path: Path) -> None:
        from media_downloader.downloader import ytdlp_options

        options = ytdlp_options(tmp_path, "/usr/bin/ffmpeg", True, audio_format="opus")
        assert options["postprocessors"] == [
            {"key": "FFmpegExtractAudio", "preferredcodec": "opus"}
        ]

    def test_download_all_disables_noplaylist(self, tmp_path: Path) -> None:
        from media_downloader.downloader import ytdlp_options

        assert ytdlp_options(tmp_path, None, False, download_all=True)["noplaylist"] is False
        assert ytdlp_options(tmp_path, None, False, download_all=False)["noplaylist"] is True


class TestConvertImage:
    def test_noop_when_already_target_format(self, tmp_path: Path) -> None:
        from media_downloader.downloader import convert_image

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"fake jpeg")
        result = convert_image(img, "jpg", "/usr/bin/ffmpeg")
        assert result == img
        assert img.exists()

    def test_converts_via_ffmpeg_subprocess(self, tmp_path: Path) -> None:
        from media_downloader.downloader import convert_image

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"fake jpeg")

        def fake_run(cmd: list[str], **kwargs: object) -> mock.Mock:
            Path(cmd[-1]).write_bytes(b"fake png")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = convert_image(img, "png", "/usr/bin/ffmpeg")

        assert result.suffix == ".png"
        assert result.is_file()
        assert not img.exists()  # original removed

    def test_raises_on_ffmpeg_failure(self, tmp_path: Path) -> None:
        from media_downloader.downloader import convert_image

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"fake jpeg")
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stderr="bad input")):
            with pytest.raises(DownloadError, match="Image conversion"):
                convert_image(img, "webp", "/usr/bin/ffmpeg")
        assert img.exists()  # original untouched on failure


class TestDownloadDirectImageFormat:
    def test_converts_image_when_requested(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        response = fake_response(
            headers={"content-type": "image/jpeg", "content-length": "3"}, chunks=[b"jpg"]
        )
        with (
            mock.patch("requests.get", return_value=response),
            mock.patch.object(dl, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(dl, "convert_image") as convert,
        ):
            convert.return_value = tmp_path / "a.png"
            result = dl.download_direct("https://x.test/a.jpg", tmp_path, image_format="png")

        convert.assert_called_once()
        assert result == tmp_path / "a.png"

    def test_raises_when_ffmpeg_missing_and_conversion_requested(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        response = fake_response(headers={"content-type": "image/jpeg"}, chunks=[b"jpg"])
        with (
            mock.patch("requests.get", return_value=response),
            mock.patch.object(dl, "find_ffmpeg", return_value=None),
        ):
            with pytest.raises(DownloadError, match="ffmpeg"):
                dl.download_direct("https://x.test/a.jpg", tmp_path, image_format="png")

    def test_non_image_downloads_are_never_converted(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        response = fake_response(
            headers={"content-type": "video/mp4", "content-length": "3"}, chunks=[b"mp4"]
        )
        with (
            mock.patch("requests.get", return_value=response),
            mock.patch.object(dl, "convert_image") as convert,
        ):
            dl.download_direct("https://x.test/a.mp4", tmp_path, image_format="png")
        convert.assert_not_called()


class TestSha256Of:
    def test_matches_hashlib(self, tmp_path: Path) -> None:
        import hashlib

        from media_downloader.downloader import sha256_of

        f = tmp_path / "a.bin"
        f.write_bytes(b"hello world" * 1000)
        assert sha256_of(f) == hashlib.sha256(b"hello world" * 1000).hexdigest()


class TestRequestedPaths:
    def test_single_result(self, tmp_path: Path) -> None:
        from media_downloader.downloader import _requested_paths

        result = {"requested_downloads": [{"filepath": str(tmp_path / "a.mp4")}]}
        assert _requested_paths(result) == [tmp_path / "a.mp4"]

    def test_recurses_into_entries(self, tmp_path: Path) -> None:
        from media_downloader.downloader import _requested_paths

        result = {
            "entries": [
                {"requested_downloads": [{"filepath": str(tmp_path / "1.jpg")}]},
                {"requested_downloads": [{"filepath": str(tmp_path / "2.jpg")}]},
            ]
        }
        assert _requested_paths(result) == [tmp_path / "1.jpg", tmp_path / "2.jpg"]

    def test_empty_when_nothing_found(self) -> None:
        from media_downloader.downloader import _requested_paths

        assert _requested_paths({}) == []


class TestZipFiles:
    def test_bundles_and_removes_originals(self, tmp_path: Path) -> None:
        import zipfile

        from media_downloader.downloader import zip_files

        a, b = tmp_path / "1.jpg", tmp_path / "2.jpg"
        a.write_bytes(b"one")
        b.write_bytes(b"two")

        result = zip_files([a, b], tmp_path, "post (2 items)")

        assert result == tmp_path / "post (2 items).zip"
        assert not a.exists()
        assert not b.exists()
        with zipfile.ZipFile(result) as zf:
            assert sorted(zf.namelist()) == ["1.jpg", "2.jpg"]
            assert zf.read("1.jpg") == b"one"


class TestInspectPost:
    def test_returns_one_for_single_item(self) -> None:
        from media_downloader.downloader import inspect_post

        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = {"id": "abc"}
        with mock.patch("yt_dlp.YoutubeDL", return_value=fake_ydl):
            assert inspect_post("https://x.test/v") == 1

    def test_returns_entry_count_for_multi_item_post(self) -> None:
        from media_downloader.downloader import inspect_post

        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = {
            "entries": [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        }
        with mock.patch("yt_dlp.YoutubeDL", return_value=fake_ydl):
            assert inspect_post("https://x.test/post") == 3

    def test_returns_one_when_extraction_fails(self) -> None:
        from media_downloader.downloader import inspect_post

        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.side_effect = RuntimeError("boom")
        with mock.patch("yt_dlp.YoutubeDL", return_value=fake_ydl):
            assert inspect_post("https://x.test/v") == 1


class TestDrmDetectionAcrossEntries:
    def test_flags_drm_nested_in_entries(self) -> None:
        from media_downloader.downloader import _looks_drm_protected

        info = {"entries": [{"formats": [{"has_drm": False}]}, {"_has_drm": True}]}
        assert _looks_drm_protected(info)

    def test_clean_entries_are_not_flagged(self) -> None:
        from media_downloader.downloader import _looks_drm_protected

        info = {"entries": [{"formats": [{"has_drm": False}]}, {"formats": [{"has_drm": False}]}]}
        assert not _looks_drm_protected(info)


class TestStripMetadata:
    def test_replaces_file_in_place_with_same_final_name(self, tmp_path: Path) -> None:
        from media_downloader.downloader import strip_metadata

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"original bytes with fake exif/tags")

        def fake_run(cmd: list[str], **kwargs: object) -> mock.Mock:
            Path(cmd[-1]).write_bytes(b"stripped bytes")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run) as run:
            result = strip_metadata(clip, "/usr/bin/ffmpeg")

        assert result == clip  # same path, same name — not a .stripped file
        assert clip.read_bytes() == b"stripped bytes"
        args = run.call_args[0][0]
        assert "-map_metadata" in args and "-1" in args
        assert "-c" in args and "copy" in args  # lossless remux, no re-encode

    def test_raises_on_ffmpeg_failure_and_leaves_original_untouched(self, tmp_path: Path) -> None:
        from media_downloader.downloader import strip_metadata

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"original bytes")
        with mock.patch(
            "subprocess.run", return_value=mock.Mock(returncode=1, stderr="ffmpeg exploded")
        ):
            with pytest.raises(DownloadError, match="Stripping metadata"):
                strip_metadata(clip, "/usr/bin/ffmpeg")
        assert clip.read_bytes() == b"original bytes"


class TestProbeCodecs:
    def test_reads_video_and_audio_codec(self, tmp_path: Path) -> None:
        import json

        from media_downloader.downloader import _probe_codecs

        clip = tmp_path / "clip.mp4"
        payload = json.dumps(
            {
                "streams": [
                    {"codec_type": "video", "codec_name": "hevc"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ]
            }
        )
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=payload)):
            video_codec, audio_codec = _probe_codecs(clip, "/usr/bin/ffprobe")
        assert video_codec == "hevc"
        assert audio_codec == "aac"

    def test_picks_first_stream_of_each_type(self, tmp_path: Path) -> None:
        import json

        from media_downloader.downloader import _probe_codecs

        clip = tmp_path / "clip.mkv"
        payload = json.dumps(
            {
                "streams": [
                    {"codec_type": "video", "codec_name": "av1"},
                    {"codec_type": "video", "codec_name": "mjpeg"},  # e.g. embedded cover art
                    {"codec_type": "audio", "codec_name": "opus"},
                ]
            }
        )
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=payload)):
            video_codec, audio_codec = _probe_codecs(clip, "/usr/bin/ffprobe")
        assert video_codec == "av1"
        assert audio_codec == "opus"

    def test_no_video_stream_returns_none_for_video(self, tmp_path: Path) -> None:
        import json

        from media_downloader.downloader import _probe_codecs

        clip = tmp_path / "song.m4a"
        payload = json.dumps({"streams": [{"codec_type": "audio", "codec_name": "aac"}]})
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=payload)):
            video_codec, audio_codec = _probe_codecs(clip, "/usr/bin/ffprobe")
        assert video_codec is None
        assert audio_codec == "aac"

    def test_ffprobe_failure_returns_unknown(self, tmp_path: Path) -> None:
        from media_downloader.downloader import _probe_codecs

        clip = tmp_path / "clip.mp4"
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="")):
            assert _probe_codecs(clip, "/usr/bin/ffprobe") == (None, None)

    def test_unparseable_output_returns_unknown(self, tmp_path: Path) -> None:
        from media_downloader.downloader import _probe_codecs

        clip = tmp_path / "clip.mp4"
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="not json")):
            assert _probe_codecs(clip, "/usr/bin/ffprobe") == (None, None)


class TestNormalizeVideoCodec:
    def test_noop_for_non_video_extension(self, tmp_path: Path) -> None:
        from media_downloader.downloader import normalize_video_codec

        img = tmp_path / "photo.jpg"
        img.write_bytes(b"jpeg bytes")
        with mock.patch("media_downloader.downloader._probe_codecs") as probe:
            result = normalize_video_codec(img, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")
        probe.assert_not_called()
        assert result == img
        assert img.read_bytes() == b"jpeg bytes"

    def test_noop_when_already_h264(self, tmp_path: Path) -> None:
        from media_downloader.downloader import normalize_video_codec

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"h264 bytes")
        with (
            mock.patch("media_downloader.downloader._probe_codecs", return_value=("h264", "aac")),
            mock.patch("subprocess.run") as run,
        ):
            result = normalize_video_codec(clip, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")
        run.assert_not_called()
        assert result == clip
        assert clip.read_bytes() == b"h264 bytes"

    def test_noop_when_no_video_stream_detected(self, tmp_path: Path) -> None:
        from media_downloader.downloader import normalize_video_codec

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"mystery bytes")
        with (
            mock.patch("media_downloader.downloader._probe_codecs", return_value=(None, None)),
            mock.patch("subprocess.run") as run,
        ):
            result = normalize_video_codec(clip, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")
        run.assert_not_called()
        assert result == clip

    def test_transcodes_hevc_and_stream_copies_aac_audio(self, tmp_path: Path) -> None:
        from media_downloader.downloader import normalize_video_codec

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"hevc bytes")

        def fake_run(cmd: list[str], **kwargs: object) -> mock.Mock:
            Path(cmd[-1]).write_bytes(b"h264 bytes")
            return mock.Mock(returncode=0, stderr="")

        with (
            mock.patch("media_downloader.downloader._probe_codecs", return_value=("hevc", "aac")),
            mock.patch("subprocess.run", side_effect=fake_run) as run,
        ):
            result = normalize_video_codec(clip, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")

        assert result == clip  # same path, same name — not a renamed/re-extensioned file
        assert clip.read_bytes() == b"h264 bytes"
        args = run.call_args[0][0]
        assert args[0] == "/usr/bin/ffmpeg"
        assert "-c:v" in args and "libx264" in args
        assert "-crf" in args
        assert args[args.index("-c:a") + 1] == "copy"  # already AAC — no audio re-encode

    def test_transcodes_non_aac_audio_too(self, tmp_path: Path) -> None:
        from media_downloader.downloader import normalize_video_codec

        clip = tmp_path / "clip.webm"
        clip.write_bytes(b"av1+opus bytes")

        def fake_run(cmd: list[str], **kwargs: object) -> mock.Mock:
            Path(cmd[-1]).write_bytes(b"h264+aac bytes")
            return mock.Mock(returncode=0, stderr="")

        with (
            mock.patch("media_downloader.downloader._probe_codecs", return_value=("av1", "opus")),
            mock.patch("subprocess.run", side_effect=fake_run) as run,
        ):
            normalize_video_codec(clip, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")

        args = run.call_args[0][0]
        assert args[args.index("-c:a") + 1] == "aac"

    def test_raises_on_ffmpeg_failure_and_leaves_original_untouched(self, tmp_path: Path) -> None:
        from media_downloader.downloader import normalize_video_codec

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"original hevc bytes")
        with (
            mock.patch("media_downloader.downloader._probe_codecs", return_value=("hevc", "aac")),
            mock.patch(
                "subprocess.run", return_value=mock.Mock(returncode=1, stderr="ffmpeg exploded")
            ),
        ):
            with pytest.raises(DownloadError, match="Normalizing video"):
                normalize_video_codec(clip, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")
        assert clip.read_bytes() == b"original hevc bytes"


class TestDownloadStripMetadataIntegration:
    def test_strip_metadata_runs_before_encrypt(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        with (
            mock.patch.object(dl, "download_direct", return_value=tmp_path / "v.mp4"),
            mock.patch.object(dl, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(dl, "strip_metadata") as strip,
            mock.patch.object(dl, "classify_url", return_value="direct"),
        ):
            (tmp_path / "v.mp4").write_bytes(b"x")
            strip.return_value = tmp_path / "v.mp4"
            with mock.patch("media_downloader.crypto.encrypt_file") as enc:
                enc.return_value = tmp_path / "v.mp4.enc"
                with mock.patch("media_downloader.crypto.get_or_create_key", return_value=b"k"):
                    dl.download(
                        "https://x.test/v.mp4",
                        tmp_path,
                        options=dl.DownloadOptions(strip_metadata=True, encrypt=True),
                    )

        strip.assert_called_once()
        enc.assert_called_once()
        # strip_metadata's result is what gets handed to encrypt_file.
        assert enc.call_args[0][0] == tmp_path / "v.mp4"

    def test_raises_clearly_when_ffmpeg_missing(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        with (
            mock.patch.object(dl, "download_direct", return_value=tmp_path / "v.mp4"),
            mock.patch.object(dl, "find_ffmpeg", return_value=None),
            mock.patch.object(dl, "classify_url", return_value="direct"),
        ):
            with pytest.raises(DownloadError, match="ffmpeg"):
                dl.download(
                    "https://x.test/v.mp4",
                    tmp_path,
                    options=dl.DownloadOptions(strip_metadata=True),
                )


class TestDownloadViaYtdlpCodecNormalization:
    def _fake_ydl(self, downloaded: dict[str, Any]) -> mock.MagicMock:
        fake_ydl = mock.MagicMock()
        fake_ydl.__enter__.return_value.extract_info.return_value = {"id": "abc", "title": "clip"}
        fake_ydl.__enter__.return_value.process_ie_result.return_value = downloaded
        return fake_ydl

    def test_normalizes_result_when_ffmpeg_and_ffprobe_available(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"hevc bytes")
        downloaded = {
            "id": "abc",
            "title": "clip",
            "requested_downloads": [{"filepath": str(clip)}],
        }
        with (
            mock.patch("yt_dlp.YoutubeDL", return_value=self._fake_ydl(downloaded)),
            mock.patch.object(dl, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(dl, "find_ffprobe", return_value="/usr/bin/ffprobe"),
            mock.patch.object(dl, "normalize_video_codec") as normalize,
        ):
            normalize.return_value = clip
            result = dl.download_via_ytdlp("https://x.test/v", tmp_path)

        normalize.assert_called_once_with(clip, "/usr/bin/ffmpeg", "/usr/bin/ffprobe")
        assert result == clip

    def test_skips_normalization_for_audio_only(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        song = tmp_path / "song.m4a"
        song.write_bytes(b"aac bytes")
        downloaded = {
            "id": "abc",
            "title": "song",
            "requested_downloads": [{"filepath": str(song)}],
        }
        with (
            mock.patch("yt_dlp.YoutubeDL", return_value=self._fake_ydl(downloaded)),
            mock.patch.object(dl, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(dl, "find_ffprobe", return_value="/usr/bin/ffprobe"),
            mock.patch.object(dl, "normalize_video_codec") as normalize,
        ):
            dl.download_via_ytdlp("https://x.test/v", tmp_path, audio_only=True)

        normalize.assert_not_called()

    def test_skips_normalization_when_ffmpeg_missing(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"hevc bytes")
        downloaded = {
            "id": "abc",
            "title": "clip",
            "requested_downloads": [{"filepath": str(clip)}],
        }
        with (
            mock.patch("yt_dlp.YoutubeDL", return_value=self._fake_ydl(downloaded)),
            mock.patch.object(dl, "find_ffmpeg", return_value=None),
            mock.patch.object(dl, "normalize_video_codec") as normalize,
        ):
            dl.download_via_ytdlp("https://x.test/v", tmp_path)

        normalize.assert_not_called()

    def test_skips_normalization_when_ffprobe_missing(self, tmp_path: Path) -> None:
        import media_downloader.downloader as dl

        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"hevc bytes")
        downloaded = {
            "id": "abc",
            "title": "clip",
            "requested_downloads": [{"filepath": str(clip)}],
        }
        with (
            mock.patch("yt_dlp.YoutubeDL", return_value=self._fake_ydl(downloaded)),
            mock.patch.object(dl, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(dl, "find_ffprobe", return_value=None),
            mock.patch.object(dl, "normalize_video_codec") as normalize,
        ):
            dl.download_via_ytdlp("https://x.test/v", tmp_path)

        normalize.assert_not_called()

    def test_normalizes_every_item_of_a_multi_item_post_before_zipping(
        self, tmp_path: Path
    ) -> None:
        import media_downloader.downloader as dl

        clip1 = tmp_path / "1.mp4"
        clip2 = tmp_path / "2.mp4"
        clip1.write_bytes(b"one")
        clip2.write_bytes(b"two")
        downloaded = {
            "id": "abc",
            "title": "post",
            "entries": [
                {"requested_downloads": [{"filepath": str(clip1)}]},
                {"requested_downloads": [{"filepath": str(clip2)}]},
            ],
        }
        with (
            mock.patch("yt_dlp.YoutubeDL", return_value=self._fake_ydl(downloaded)),
            mock.patch.object(dl, "find_ffmpeg", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(dl, "find_ffprobe", return_value="/usr/bin/ffprobe"),
            mock.patch.object(
                dl, "normalize_video_codec", side_effect=lambda p, *_: p
            ) as normalize,
        ):
            result = dl.download_via_ytdlp("https://x.test/post", tmp_path, download_all=True)

        assert normalize.call_count == 2
        assert result.suffix == ".zip"
