from __future__ import annotations

import datetime
import ssl
from pathlib import Path

from cryptography import x509

from media_downloader.certs import RENEW_WITHIN_DAYS, VALID_DAYS, get_or_create_cert


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

    def test_validity_stays_under_safaris_398_day_limit(self, tmp_path: Path) -> None:
        # Regression: Safari (and Chrome) reject TLS certs valid for longer
        # than ~398 days outright — "certificate has too long a validity
        # period" — even self-signed ones the user manually trusts. An
        # earlier version of this file used a 10-year validity and users hit
        # exactly that error on real phones.
        assert VALID_DAYS < 398
        cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
        get_or_create_cert(cert_path, key_path)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        lifetime = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert lifetime.days < 398

    def test_expired_cert_is_regenerated_not_reused(self, tmp_path: Path) -> None:
        cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
        get_or_create_cert(cert_path, key_path)
        original_bytes = cert_path.read_bytes()

        # Back-date the file's mtime doesn't matter — what's checked is the
        # cert's own not_valid_after, so directly overwrite it with an
        # already-expired one to simulate time passing.
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired.test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=10))
            .not_valid_after(now - datetime.timedelta(days=1))  # already expired
            .sign(key, hashes.SHA256())
        )
        cert_path.write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))
        assert cert_path.read_bytes() != original_bytes

        get_or_create_cert(cert_path, key_path)
        renewed = x509.load_pem_x509_certificate(cert_path.read_bytes())
        assert renewed.not_valid_after_utc > now

    def test_cert_expiring_soon_is_renewed_early(self, tmp_path: Path) -> None:
        cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
        get_or_create_cert(cert_path, key_path)

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "soon.test")])
        now = datetime.datetime.now(datetime.timezone.utc)
        soon_to_expire = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=10))
            .not_valid_after(now + datetime.timedelta(days=RENEW_WITHIN_DAYS - 1))
            .sign(key, hashes.SHA256())
        )
        cert_path.write_bytes(soon_to_expire.public_bytes(serialization.Encoding.PEM))

        get_or_create_cert(cert_path, key_path)
        renewed = x509.load_pem_x509_certificate(cert_path.read_bytes())
        assert (renewed.not_valid_after_utc - now) > datetime.timedelta(days=RENEW_WITHIN_DAYS)
