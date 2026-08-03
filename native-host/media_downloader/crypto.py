"""At-rest encryption for downloaded files (opt-in).

Downloaded media is normally left as a plain file so it can be opened
immediately — that's the whole point of a media downloader. This module
only runs when a caller explicitly asks for it (encrypt=True on download()),
for the case where the copy sitting on disk should be unreadable to anyone
else with access to this machine.

Honest limits, stated once here rather than left implicit:
- This is NOT a substitute for full-disk encryption (FileVault/BitLocker).
  The key lives in a file on the same disk as the encrypted data, so it
  protects against the file being copied elsewhere or read by something
  that isn't this tool — not against another process running as this same
  user account.
- AES-256-GCM, whole-file, not streamed: fine for the video/audio sizes this
  tool actually handles (tens to a few hundred MB, per what's been observed
  downloading through it). A multi-GB file would need a streaming AEAD
  scheme instead, which isn't built here since nothing in this project
  downloads files that large.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE = 32  # AES-256
NONCE_SIZE = 12  # standard/recommended GCM nonce size
DEFAULT_KEY_PATH = Path.home() / ".config" / "media-downloader" / "key.bin"
ENCRYPTED_SUFFIX = ".enc"


class CryptoError(Exception):
    """Encryption/decryption failed for a reason worth showing to the user."""


def get_or_create_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Loads the local encryption key, generating one on first use."""
    if key_path.exists():
        key = key_path.read_bytes()
        if len(key) != KEY_SIZE:
            raise CryptoError(f"Corrupt encryption key at {key_path} (wrong size)")
        return key

    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = os.urandom(KEY_SIZE)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def encrypted_name(path: Path) -> Path:
    return path.with_name(path.name + ENCRYPTED_SUFFIX)


def display_name(path: Path) -> str:
    """The user-facing filename for an encrypted file — what it will be
    called again once decrypted."""
    name = path.name
    return name[: -len(ENCRYPTED_SUFFIX)] if name.endswith(ENCRYPTED_SUFFIX) else name


def encrypt_file(path: Path, key: bytes) -> Path:
    """Encrypts path in place, replacing it with path.enc. Returns the new path."""
    aesgcm = AESGCM(key)
    nonce = os.urandom(NONCE_SIZE)
    plaintext = path.read_bytes()
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    target = encrypted_name(path)
    target.write_bytes(nonce + ciphertext)
    path.unlink()
    return target


def decrypt_bytes(data: bytes, key: bytes) -> bytes:
    if len(data) < NONCE_SIZE:
        raise CryptoError("Encrypted file is too short to contain a valid nonce.")
    nonce, ciphertext = data[:NONCE_SIZE], data[NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:  # cryptography raises InvalidTag on bad key/corruption
        raise CryptoError(
            "Could not decrypt this file — wrong key, or the file is corrupted."
        ) from exc


def decrypt_file(path: Path, key: bytes, dest: Path | None = None) -> Path:
    """Decrypts an .enc file to a plaintext copy. Defaults to writing next to
    it under its original (pre-encryption) name."""
    target = dest or path.with_name(display_name(path))
    plaintext = decrypt_bytes(path.read_bytes(), key)
    target.write_bytes(plaintext)
    return target
