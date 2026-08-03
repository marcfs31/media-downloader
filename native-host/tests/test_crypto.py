from __future__ import annotations

import stat
from pathlib import Path

import pytest

from media_downloader.crypto import (
    CryptoError,
    decrypt_bytes,
    decrypt_file,
    display_name,
    encrypt_file,
    encrypted_name,
    get_or_create_key,
)


class TestGetOrCreateKey:
    def test_generates_a_32_byte_key_on_first_use(self, tmp_path: Path) -> None:
        key_path = tmp_path / "sub" / "key.bin"
        key = get_or_create_key(key_path)
        assert len(key) == 32
        assert key_path.exists()

    def test_reuses_an_existing_key(self, tmp_path: Path) -> None:
        key_path = tmp_path / "key.bin"
        first = get_or_create_key(key_path)
        second = get_or_create_key(key_path)
        assert first == second

    def test_key_file_is_permissioned_owner_only(self, tmp_path: Path) -> None:
        key_path = tmp_path / "key.bin"
        get_or_create_key(key_path)
        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600

    def test_corrupt_key_file_raises(self, tmp_path: Path) -> None:
        key_path = tmp_path / "key.bin"
        key_path.write_bytes(b"too-short")
        with pytest.raises(CryptoError):
            get_or_create_key(key_path)


class TestNaming:
    def test_encrypted_name_appends_suffix(self) -> None:
        assert encrypted_name(Path("clip.mp4")) == Path("clip.mp4.enc")

    def test_display_name_strips_suffix(self) -> None:
        assert display_name(Path("clip.mp4.enc")) == "clip.mp4"

    def test_display_name_passthrough_without_suffix(self) -> None:
        assert display_name(Path("clip.mp4")) == "clip.mp4"


class TestEncryptDecryptRoundtrip:
    def test_roundtrip_recovers_original_bytes(self, tmp_path: Path) -> None:
        key = get_or_create_key(tmp_path / "key.bin")
        original = tmp_path / "clip.mp4"
        original.write_bytes(b"totally real video bytes" * 1000)

        encrypted = encrypt_file(original, key)

        assert encrypted == tmp_path / "clip.mp4.enc"
        assert not original.exists()  # plaintext is replaced, not left alongside
        assert encrypted.read_bytes() != b"totally real video bytes" * 1000

        decrypted = decrypt_file(encrypted, key)
        assert decrypted == tmp_path / "clip.mp4"
        assert decrypted.read_bytes() == b"totally real video bytes" * 1000

    def test_decrypt_bytes_matches_decrypt_file(self, tmp_path: Path) -> None:
        key = get_or_create_key(tmp_path / "key.bin")
        original = tmp_path / "song.m4a"
        original.write_bytes(b"audio bytes go here")
        encrypted = encrypt_file(original, key)
        assert decrypt_bytes(encrypted.read_bytes(), key) == b"audio bytes go here"

    def test_wrong_key_fails_to_decrypt(self, tmp_path: Path) -> None:
        key = get_or_create_key(tmp_path / "key1.bin")
        wrong_key = get_or_create_key(tmp_path / "key2.bin")
        original = tmp_path / "clip.mp4"
        original.write_bytes(b"secret video content")
        encrypted = encrypt_file(original, key)

        with pytest.raises(CryptoError):
            decrypt_file(encrypted, wrong_key)

    def test_corrupted_ciphertext_fails_to_decrypt(self, tmp_path: Path) -> None:
        key = get_or_create_key(tmp_path / "key.bin")
        original = tmp_path / "clip.mp4"
        original.write_bytes(b"secret video content")
        encrypted = encrypt_file(original, key)

        tampered = bytearray(encrypted.read_bytes())
        tampered[-1] ^= 0xFF
        encrypted.write_bytes(bytes(tampered))

        with pytest.raises(CryptoError):
            decrypt_file(encrypted, key)

    def test_decrypt_to_explicit_destination(self, tmp_path: Path) -> None:
        key = get_or_create_key(tmp_path / "key.bin")
        original = tmp_path / "clip.mp4"
        original.write_bytes(b"video content")
        encrypted = encrypt_file(original, key)

        dest = tmp_path / "elsewhere" / "output.mp4"
        dest.parent.mkdir()
        result = decrypt_file(encrypted, key, dest=dest)
        assert result == dest
        assert dest.read_bytes() == b"video content"

    def test_two_files_encrypted_with_same_key_use_different_nonces(self, tmp_path: Path) -> None:
        # Reusing a nonce with the same key breaks AES-GCM's confidentiality
        # guarantee, so this is worth pinning as a regression test.
        key = get_or_create_key(tmp_path / "key.bin")
        a = tmp_path / "a.mp4"
        b = tmp_path / "b.mp4"
        a.write_bytes(b"identical content")
        b.write_bytes(b"identical content")
        enc_a = encrypt_file(a, key)
        enc_b = encrypt_file(b, key)
        assert enc_a.read_bytes() != enc_b.read_bytes()
