from __future__ import annotations

import ssl
from pathlib import Path

from media_downloader.certs import get_or_create_cert


class TestGetOrCreateCert:
    def test_generates_cert_and_key_on_first_use(self, tmp_path: Path) -> None:
        cert_path = tmp_path / "sub" / "cert.pem"
        key_path = tmp_path / "sub" / "key.pem"
        got_cert, got_key = get_or_create_cert(cert_path, key_path)
        assert got_cert == cert_path
        assert got_key == key_path
        assert cert_path.is_file()
        assert key_path.is_file()

    def test_reuses_existing_cert(self, tmp_path: Path) -> None:
        cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
        get_or_create_cert(cert_path, key_path)
        first_cert_bytes = cert_path.read_bytes()
        get_or_create_cert(cert_path, key_path)
        assert cert_path.read_bytes() == first_cert_bytes

    def test_cert_is_loadable_by_ssl(self, tmp_path: Path) -> None:
        cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
        get_or_create_cert(cert_path, key_path, extra_hosts=("192.168.1.20", "some-host.local"))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))  # raises if malformed

    def test_key_file_is_owner_only(self, tmp_path: Path) -> None:
        import stat

        cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
        get_or_create_cert(cert_path, key_path)
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
