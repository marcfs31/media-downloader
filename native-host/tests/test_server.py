from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from media_downloader import server as server_mod
from media_downloader.certs import get_or_create_cert as real_get_or_create_cert
from media_downloader.crypto import encrypt_file, get_or_create_key
from media_downloader.downloader import DownloadError

TOKEN = "test-token"

# For talking to our own self-signed cert in tests: the point under test is
# that TLS actually wraps the connection, not certificate trust chains.
INSECURE_TLS_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
INSECURE_TLS_CONTEXT.check_hostname = False
INSECURE_TLS_CONTEXT.verify_mode = ssl.CERT_NONE


@pytest.fixture
def running_server(tmp_path: Path) -> Iterator[str]:
    srv = server_mod.CompanionServer("127.0.0.1", 0, tmp_path, TOKEN)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def running_tls_server(tmp_path: Path) -> Iterator[str]:
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()

    def fake_get_or_create_cert(*args: object, **kwargs: object) -> tuple[Path, Path]:
        return real_get_or_create_cert(cert_path, key_path, extra_hosts=())

    with mock.patch.object(server_mod, "get_or_create_cert", side_effect=fake_get_or_create_cert):
        srv = server_mod.CompanionServer("127.0.0.1", 0, dl_dir, TOKEN, tls=True)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"https://127.0.0.1:{srv.server_address[1]}"
        finally:
            srv.shutdown()
            srv.server_close()


def get_tls(base: str, path: str, token: str = TOKEN) -> tuple[int, bytes]:
    sep = "&" if "?" in path else "?"
    try:
        with urllib.request.urlopen(
            f"{base}{path}{sep}t={token}", context=INSECURE_TLS_CONTEXT
        ) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def get(base: str, path: str, token: str = TOKEN) -> tuple[int, bytes]:
    sep = "&" if "?" in path else "?"
    try:
        with urllib.request.urlopen(f"{base}{path}{sep}t={token}") as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def post_json(base: str, path: str, payload: object, token: str = TOKEN) -> tuple[int, bytes]:
    body = json.dumps(payload).encode()
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(
        f"{base}{path}{sep}t={token}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def wait_for_state(base: str, job_id: str, state: str, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = get(base, f"/api/jobs/{job_id}")
        assert status == 200
        job = json.loads(body)
        if job["state"] == state:
            return dict(job)
        time.sleep(0.02)
    raise AssertionError(f"job never reached state {state!r}")


class TestAuth:
    def test_wrong_token_is_rejected_everywhere(self, running_server: str) -> None:
        for path in ("/", "/api/jobs", "/files/abc"):
            status, _ = get(running_server, path, token="wrong")
            assert status == 401

    def test_page_serves_with_right_token(self, running_server: str) -> None:
        status, body = get(running_server, "/")
        assert status == 200
        assert b"Media Downloader" in body
        assert TOKEN.encode() in body  # embedded for the page's own API calls


class TestDownloadFlow:
    def test_full_lifecycle_success(self, running_server: str, tmp_path: Path) -> None:
        def fake_download(
            url: str, dest: Path, progress: object = None, options: object = None
        ) -> Path:
            out = dest / "clip.mp4"
            out.write_bytes(b"fake video bytes")
            return out

        with mock.patch.object(server_mod, "download", side_effect=fake_download):
            status, body = post_json(running_server, "/api/download", {"url": "https://x.test/v"})
            assert status == 202
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "done")

        assert job["filename"] == "clip.mp4"
        status, content = get(running_server, f"/files/{job_id}")
        assert status == 200
        assert content == b"fake video bytes"

    def test_failure_surfaces_error_message(self, running_server: str) -> None:
        with mock.patch.object(server_mod, "download", side_effect=DownloadError("no dice")):
            _, body = post_json(running_server, "/api/download", {"url": "https://x.test/v"})
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "error")
        assert job["message"] == "no dice"

    def test_file_not_served_before_done(self, running_server: str) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_download(
            url: str, dest: Path, progress: object = None, options: object = None
        ) -> Path:
            started.set()
            release.wait(timeout=5)
            out = dest / "late.mp4"
            out.write_bytes(b"x")
            return out

        with mock.patch.object(server_mod, "download", side_effect=slow_download):
            _, body = post_json(running_server, "/api/download", {"url": "https://x.test/v"})
            job_id = json.loads(body)["id"]
            assert started.wait(timeout=5)
            status, _ = get(running_server, f"/files/{job_id}")
            assert status == 404  # still running
            release.set()
            wait_for_state(running_server, job_id, "done")


class TestValidation:
    def test_rejects_non_http_url(self, running_server: str) -> None:
        status, _ = post_json(running_server, "/api/download", {"url": "file:///etc/passwd"})
        assert status == 400

    def test_rejects_garbage_body(self, running_server: str) -> None:
        status, _ = post_json(running_server, "/api/download", ["not", "a", "dict"])
        assert status == 400

    def test_unknown_job_404s(self, running_server: str) -> None:
        status, _ = get(running_server, "/api/jobs/nope")
        assert status == 404


class TestAudioOnly:
    def test_audio_only_flag_reaches_the_download_call(
        self, running_server: str, tmp_path: Path
    ) -> None:
        seen: dict[str, object] = {}

        def fake_download(
            url: str, dest: Path, progress: object = None, options: Any = None
        ) -> Path:
            seen["audio_only"] = options.audio_only if options else False
            out = dest / "song.m4a"
            out.write_bytes(b"x")
            return out

        with mock.patch.object(server_mod, "download", side_effect=fake_download):
            _, body = post_json(
                running_server, "/api/download", {"url": "https://x.test/v", "audio_only": True}
            )
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "done")

        assert seen["audio_only"] is True
        assert job["audio_only"] is True

    def test_defaults_to_false(self, running_server: str, tmp_path: Path) -> None:
        def fake_download(
            url: str, dest: Path, progress: object = None, options: Any = None
        ) -> Path:
            out = dest / "v.mp4"
            out.write_bytes(b"x")
            return out

        with mock.patch.object(server_mod, "download", side_effect=fake_download):
            _, body = post_json(running_server, "/api/download", {"url": "https://x.test/v"})
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "done")

        assert job["audio_only"] is False


class TestWaitParameter:
    def test_wait_blocks_until_done_and_returns_200(self, running_server: str) -> None:
        def fast_download(
            url: str, dest: Path, progress: object = None, options: object = None
        ) -> Path:
            out = dest / "v.mp4"
            out.write_bytes(b"x")
            return out

        with mock.patch.object(server_mod, "download", side_effect=fast_download):
            status, body = post_json(
                running_server, "/api/download?wait=5", {"url": "https://x.test/v"}
            )

        assert status == 200
        assert json.loads(body)["state"] == "done"

    def test_without_wait_returns_202_immediately(self, running_server: str) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_download(
            url: str, dest: Path, progress: object = None, options: object = None
        ) -> Path:
            started.set()
            release.wait(timeout=5)
            out = dest / "v.mp4"
            out.write_bytes(b"x")
            return out

        with mock.patch.object(server_mod, "download", side_effect=slow_download):
            status, body = post_json(running_server, "/api/download", {"url": "https://x.test/v"})
            assert status == 202
            assert json.loads(body)["state"] in ("queued", "running")
            started.wait(timeout=5)
            release.set()


class TestSweepOldFiles:
    def test_removes_only_files_older_than_cutoff(self, tmp_path: Path) -> None:
        old = tmp_path / "old.mp4"
        new = tmp_path / "new.mp4"
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        now = time.time()
        import os

        os.utime(old, (now - 10 * 86400, now - 10 * 86400))
        os.utime(new, (now - 1 * 3600, now - 1 * 3600))

        removed = server_mod.sweep_old_files(tmp_path, keep_days=7, now=now)

        assert removed == [old]
        assert not old.exists()
        assert new.exists()

    def test_keep_days_zero_or_negative_does_nothing(self, tmp_path: Path) -> None:
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        assert server_mod.sweep_old_files(tmp_path, keep_days=0) == []
        assert server_mod.sweep_old_files(tmp_path, keep_days=-1) == []
        assert f.exists()

    def test_ignores_subdirectories(self, tmp_path: Path) -> None:
        import os

        sub = tmp_path / "nested"
        sub.mkdir()
        now = time.time()
        os.utime(sub, (now - 30 * 86400, now - 30 * 86400))
        assert server_mod.sweep_old_files(tmp_path, keep_days=1, now=now) == []
        assert sub.exists()

    def test_missing_directory_is_a_noop(self, tmp_path: Path) -> None:
        assert server_mod.sweep_old_files(tmp_path / "nope", keep_days=1) == []


class TestJobStoreMemoryBound:
    def test_old_finished_jobs_are_pruned_beyond_the_cap(self, tmp_path: Path) -> None:
        store = server_mod.JobStore(tmp_path)
        with mock.patch.object(server_mod, "download", return_value=tmp_path / "x.mp4"):
            first_job = store.start("https://x.test/0")
            for _ in range(50):
                time.sleep(0.001)
            for i in range(1, server_mod.MAX_REMEMBERED_JOBS + 10):
                store.start(f"https://x.test/{i}")
            deadline = time.monotonic() + 5
            while (
                any(j.state == "running" or j.state == "queued" for j in store._jobs.values())
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)

        assert len(store._jobs) <= server_mod.MAX_REMEMBERED_JOBS
        assert first_job.id not in store._jobs


class TestTlsServer:
    def test_serves_https_with_self_signed_cert(self, running_tls_server: str) -> None:
        status, body = get_tls(running_tls_server, "/")
        assert status == 200
        assert b"Media Downloader" in body

    def test_wrong_token_still_rejected_over_tls(self, running_tls_server: str) -> None:
        status, _ = get_tls(running_tls_server, "/", token="wrong")
        assert status == 401

    def test_plain_http_request_to_tls_port_fails_cleanly(self, running_tls_server: str) -> None:
        # Sanity check that this really is TLS and not silently falling back
        # to plain HTTP: talking plaintext to it should not succeed.
        host_port = running_tls_server.removeprefix("https://")
        with pytest.raises((ConnectionResetError, OSError)):
            with urllib.request.urlopen(f"http://{host_port}/?t={TOKEN}", timeout=2) as res:
                res.read()


class TestInspectEndpoint:
    def test_reports_item_count(self, running_server: str) -> None:
        with mock.patch.object(server_mod, "inspect_post", return_value=4):
            status, body = get(running_server, "/api/inspect?url=https://x.test/post")
        assert status == 200
        assert json.loads(body) == {"url": "https://x.test/post", "count": 4}

    def test_rejects_missing_url(self, running_server: str) -> None:
        status, _ = get(running_server, "/api/inspect")
        assert status == 400

    def test_defaults_to_one_when_inspection_raises(self, running_server: str) -> None:
        with mock.patch.object(server_mod, "inspect_post", side_effect=RuntimeError("boom")):
            status, body = get(running_server, "/api/inspect?url=https://x.test/v")
        assert status == 200
        assert json.loads(body)["count"] == 1


class TestEncryptedFileServing:
    def test_serves_decrypted_bytes_with_display_filename(
        self, running_server: str, tmp_path: Path
    ) -> None:
        def fake_download(
            url: str, dest: Path, progress: object = None, options: Any = None
        ) -> Path:
            plain = dest / "clip.mp4"
            plain.write_bytes(b"real video bytes")
            key = get_or_create_key()
            return encrypt_file(plain, key)

        with mock.patch.object(server_mod, "download", side_effect=fake_download):
            _, body = post_json(
                running_server, "/api/download", {"url": "https://x.test/v", "encrypt": True}
            )
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "done")

        assert job["encrypt"] is True
        assert job["filename"] == "clip.mp4.enc"

        status, content = get(running_server, f"/files/{job_id}")
        assert status == 200
        assert content == b"real video bytes"  # decrypted transparently

    def test_checksum_is_reported_for_the_stored_file(
        self, running_server: str, tmp_path: Path
    ) -> None:
        import hashlib

        def fake_download(
            url: str, dest: Path, progress: object = None, options: Any = None
        ) -> Path:
            out = dest / "clip.mp4"
            out.write_bytes(b"deterministic content")
            return out

        with mock.patch.object(server_mod, "download", side_effect=fake_download):
            _, body = post_json(running_server, "/api/download", {"url": "https://x.test/v"})
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "done")

        assert job["checksum"] == hashlib.sha256(b"deterministic content").hexdigest()


class TestQualityAndFormatFields:
    def test_options_reach_the_job_and_the_download_call(self, running_server: str) -> None:
        seen: dict[str, object] = {}

        def fake_download(
            url: str, dest: Path, progress: object = None, options: Any = None
        ) -> Path:
            seen["options"] = options
            out = dest / "v.mkv"
            out.write_bytes(b"x")
            return out

        with mock.patch.object(server_mod, "download", side_effect=fake_download):
            _, body = post_json(
                running_server,
                "/api/download",
                {"url": "https://x.test/v", "quality": "720p", "video_format": "mkv"},
            )
            job_id = json.loads(body)["id"]
            job = wait_for_state(running_server, job_id, "done")

        assert seen["options"].quality == "720p"
        assert seen["options"].video_format == "mkv"
        assert job["quality"] == "720p"
        assert job["video_format"] == "mkv"

    def test_invalid_quality_rejected_before_starting_a_job(self, running_server: str) -> None:
        status, body = post_json(
            running_server, "/api/download", {"url": "https://x.test/v", "quality": "4k"}
        )
        assert status == 400
        assert "quality" in json.loads(body)["error"].lower()
