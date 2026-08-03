"""Self-signed TLS certificate for the companion server's HTTPS listener.

A locally-generated, self-signed cert is the only practical option for a LAN
server with no public hostname — there's no CA that would issue one for it.
Phones will show a "not trusted" warning on first connect; that's the normal,
expected trade-off of any LAN-only HTTPS dev server (the browser has no way
to verify identity), and the fix is the standard one: tap through/trust it
once. The point of TLS here isn't proving identity, it's stopping the
traffic itself — the access token, the URLs, the downloaded bytes — from
being plainly readable by anything else on the Wi-Fi.
"""

from __future__ import annotations

import datetime
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

DEFAULT_CERT_DIR = Path.home() / ".config" / "media-downloader"
CERT_PATH = DEFAULT_CERT_DIR / "server-cert.pem"
KEY_PATH = DEFAULT_CERT_DIR / "server-key.pem"
VALID_DAYS = 3650


def get_or_create_cert(
    cert_path: Path = CERT_PATH,
    key_path: Path = KEY_PATH,
    extra_hosts: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Returns (cert_path, key_path), generating a self-signed cert on first
    use. extra_hosts (the LAN IP, the mDNS hostname) go into the cert's
    Subject Alternative Names so a phone connecting by either doesn't also
    get a hostname-mismatch warning on top of the expected untrusted-issuer one."""
    if cert_path.is_file() and key_path.is_file():
        return cert_path, key_path

    cert_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "media-downloader.local")]
    )

    san_names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ip_address("127.0.0.1")),
    ]
    for host in extra_hosts:
        try:
            san_names.append(x509.IPAddress(ip_address(host)))
        except ValueError:
            san_names.append(x509.DNSName(host))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
