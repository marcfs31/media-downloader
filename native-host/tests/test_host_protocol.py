from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from unittest import mock

from media_downloader import host
from media_downloader.downloader import DownloadError, DownloadOptions


def encode(message: dict[str, object]) -> bytes:
    payload = json.dumps(message).encode()
    return struct.pack("=I", len(payload)) + payload


def decode_all(buffer: bytes) -> list[dict[str, object]]:
    messages = []
    view = memoryview(buffer)
    while len(view) >= 4:
        (length,) = struct.unpack("=I", view[:4])
        messages.append(json.loads(bytes(view[4 : 4 + length]).decode()))
        view = view[4 + length :]
    return messages


class TestFraming:
    def test_roundtrip(self) -> None:
        out = io.BytesIO()
        host.write_message(out, {"hello": "world"})
        out.seek(0)
        assert host.read_message(out) == {"hello": "world"}

    def test_eof_returns_none(self) -> None:
        assert host.read_message(io.BytesIO()) is None

    def test_truncated_payload_returns_none(self) -> None:
        buffer = encode({"a": 1})[:-2]
        assert host.read_message(io.BytesIO(buffer)) is None


class TestHandleDownload:
    def test_success_emits_done(self, tmp_path: Path) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", return_value=tmp_path / "v.mp4"):
            host.handle_download({"id": "req-1", "url": "https://x.test/v.mp4"}, out)
        messages = decode_all(out.getvalue())
        assert messages[-1]["type"] == "done"
        assert messages[-1]["id"] == "req-1"
        assert messages[-1]["path"].endswith("v.mp4")

    def test_audio_flag_passed_through_to_download(self, tmp_path: Path) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", return_value=tmp_path / "song.m4a") as mocked:
            host.handle_download({"id": "req-audio", "url": "https://x.test/v", "audio": True}, out)
        mocked.assert_called_once_with(
            "https://x.test/v", host.DEFAULT_DEST, mock.ANY, DownloadOptions(audio_only=True)
        )

    def test_missing_audio_flag_defaults_to_video(self, tmp_path: Path) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", return_value=tmp_path / "v.mp4") as mocked:
            host.handle_download({"id": "req-video", "url": "https://x.test/v"}, out)
        mocked.assert_called_once_with(
            "https://x.test/v", host.DEFAULT_DEST, mock.ANY, DownloadOptions()
        )

    def test_encrypt_flag_passed_through_to_download(self, tmp_path: Path) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", return_value=tmp_path / "v.mp4.enc") as mocked:
            host.handle_download({"id": "req-enc", "url": "https://x.test/v", "encrypt": True}, out)
        mocked.assert_called_once_with(
            "https://x.test/v", host.DEFAULT_DEST, mock.ANY, DownloadOptions(encrypt=True)
        )

    def test_quality_and_format_flags_passed_through(self, tmp_path: Path) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", return_value=tmp_path / "v.mkv") as mocked:
            host.handle_download(
                {
                    "id": "req-fmt",
                    "url": "https://x.test/v",
                    "quality": "720p",
                    "video_format": "mkv",
                    "download_all": True,
                },
                out,
            )
        mocked.assert_called_once_with(
            "https://x.test/v",
            host.DEFAULT_DEST,
            mock.ANY,
            DownloadOptions(quality="720p", video_format="mkv", download_all=True),
        )

    def test_strip_metadata_flag_passed_through(self, tmp_path: Path) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", return_value=tmp_path / "v.mp4") as mocked:
            host.handle_download(
                {"id": "req-strip", "url": "https://x.test/v", "strip_metadata": True}, out
            )
        mocked.assert_called_once_with(
            "https://x.test/v", host.DEFAULT_DEST, mock.ANY, DownloadOptions(strip_metadata=True)
        )

    def test_failure_emits_error(self) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", side_effect=DownloadError("nope")):
            host.handle_download({"id": "req-2", "url": "https://x.test/v.mp4"}, out)
        messages = decode_all(out.getvalue())
        assert messages == [{"id": "req-2", "type": "error", "message": "nope"}]

    def test_missing_url_emits_error(self) -> None:
        out = io.BytesIO()
        host.handle_download({"id": "req-3"}, out)
        messages = decode_all(out.getvalue())
        assert messages[0]["type"] == "error"

    def test_unexpected_exception_reported_not_raised(self) -> None:
        out = io.BytesIO()
        with mock.patch.object(host, "download", side_effect=RuntimeError("boom")):
            host.handle_download({"id": "req-4", "url": "https://x.test/v.mp4"}, out)
        messages = decode_all(out.getvalue())
        assert messages[0]["type"] == "error"
        assert "boom" in messages[0]["message"]


class TestStdoutHygiene:
    def test_stray_prints_cannot_corrupt_the_protocol_channel(self, tmp_path: Path) -> None:
        # Run the real host as a subprocess with a stubbed download() that
        # prints garbage to stdout mid-download — the way yt-dlp's progress
        # bar does. Every byte on the host's real stdout must still be valid
        # protocol framing.
        import subprocess
        import sys

        sitecustomize = tmp_path / "sitecustomize.py"
        # Patch downloader.download (not host.download): `python -m` re-runs
        # host.py as __main__, whose `from .downloader import download` binds
        # whatever downloader holds at that moment — i.e. our stub.
        sitecustomize.write_text(
            "import media_downloader.downloader as d\n"
            "def fake_download(url, dest, progress=None, options=None):\n"
            "    print('\\r[download]  42% of 1.00MiB')\n"  # the yt-dlp bug, simulated
            "    from pathlib import Path\n"
            "    return Path('/tmp/fake.mp4')\n"
            "d.download = fake_download\n"
        )
        request = encode({"action": "download", "url": "https://x.test/v", "id": "hyg-1"})
        proc = subprocess.run(
            [sys.executable, "-m", "media_downloader.host"],
            input=request,
            capture_output=True,
            timeout=30,
            env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"},
        )
        messages = decode_all(proc.stdout)  # raises/derails on any stray bytes
        assert messages == [{"id": "hyg-1", "type": "done", "path": "/tmp/fake.mp4"}]
        assert b"[download]" in proc.stderr  # the stray print went to stderr instead
