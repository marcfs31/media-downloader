"""Registers this package as a browser native messaging host.

Writes (per browser) the small JSON manifest browsers look up when an
extension calls connectNative("com.media_downloader.host"), plus a launcher
script pointing at the current Python environment. Run from the environment
the host should execute in:

    python -m media_downloader.install --browser firefox
    python -m media_downloader.install --browser chrome --chrome-extension-id <ID>

Chrome/Edge/Chromium need the extension's ID because their manifests scope
access by extension origin; find it at chrome://extensions with Developer
mode on. Firefox instead matches the extension ID pinned in
manifest.firefox.json, so no flag is needed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

HOST_NAME = "com.media_downloader.host"
FIREFOX_EXTENSION_ID = "media-downloader@claude-template.local"
DESCRIPTION = "Downloads media for the Media Downloader extension (yt-dlp backend)."

CHROMIUM_FAMILY = {"chrome", "chromium", "edge"}
SUPPORTED_BROWSERS = CHROMIUM_FAMILY | {"firefox"}


def manifest_dir(browser: str) -> Path:
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        base = home / "Library" / "Application Support"
        return {
            "chrome": base / "Google" / "Chrome" / "NativeMessagingHosts",
            "chromium": base / "Chromium" / "NativeMessagingHosts",
            "edge": base / "Microsoft Edge" / "NativeMessagingHosts",
            "firefox": base / "Mozilla" / "NativeMessagingHosts",
        }[browser]
    if system == "Linux":
        return {
            "chrome": home / ".config" / "google-chrome" / "NativeMessagingHosts",
            "chromium": home / ".config" / "chromium" / "NativeMessagingHosts",
            "edge": home / ".config" / "microsoft-edge" / "NativeMessagingHosts",
            "firefox": home / ".mozilla" / "native-messaging-hosts",
        }[browser]
    if system == "Windows":
        # On Windows the manifest can live anywhere; a registry key points to
        # it (written in register_windows below).
        return home / "AppData" / "Local" / "MediaDownloaderHost"
    raise SystemExit(f"Unsupported platform: {system}")


def write_launcher(target_dir: Path) -> Path:
    """A tiny wrapper so the manifest's "path" hits this exact Python env."""
    target_dir.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    if platform.system() == "Windows":
        launcher = target_dir / f"{HOST_NAME}.bat"
        launcher.write_text(f'@echo off\n"{python}" -m media_downloader.host %*\n')
    else:
        launcher = target_dir / f"{HOST_NAME}.sh"
        launcher.write_text(f'#!/bin/sh\nexec "{python}" -m media_downloader.host "$@"\n')
        launcher.chmod(0o755)
    return launcher


def build_manifest(
    browser: str, launcher: Path, chrome_extension_id: str | None
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "name": HOST_NAME,
        "description": DESCRIPTION,
        "path": str(launcher),
        "type": "stdio",
    }
    if browser in CHROMIUM_FAMILY:
        if not chrome_extension_id:
            raise SystemExit(
                f"--chrome-extension-id is required for {browser} (see chrome://extensions "
                "with Developer mode enabled)."
            )
        manifest["allowed_origins"] = [f"chrome-extension://{chrome_extension_id}/"]
    else:
        manifest["allowed_extensions"] = [FIREFOX_EXTENSION_ID]
    return manifest


def register_windows(browser: str, manifest_path: Path) -> None:
    # The sys.platform check (not just platform.system()) is what lets mypy
    # type-check the winreg usage on non-Windows machines.
    if sys.platform != "win32":
        raise SystemExit("register_windows only makes sense on Windows.")
    import winreg

    roots = {
        "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
        "chromium": r"Software\Chromium\NativeMessagingHosts",
        "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
        "firefox": r"Software\Mozilla\NativeMessagingHosts",
    }
    key_path = f"{roots[browser]}\\{HOST_NAME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))


def install(browser: str, chrome_extension_id: str | None) -> Path:
    target_dir = manifest_dir(browser)
    launcher = write_launcher(
        target_dir if platform.system() == "Windows" else target_dir.parent / "MediaDownloaderHost"
    )
    manifest = build_manifest(browser, launcher, chrome_extension_id)

    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = f".{browser}" if platform.system() == "Windows" else ""
    manifest_path = target_dir / f"{HOST_NAME}{suffix}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + os.linesep)

    if platform.system() == "Windows":
        register_windows(browser, manifest_path)

    return manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--browser",
        choices=sorted(SUPPORTED_BROWSERS) + ["all"],
        default="all",
        help="Which browser(s) to register the host for (default: all)",
    )
    parser.add_argument(
        "--chrome-extension-id",
        help="The unpacked extension's ID (required for chrome/chromium/edge)",
    )
    args = parser.parse_args(argv)

    browsers = sorted(SUPPORTED_BROWSERS) if args.browser == "all" else [args.browser]
    for browser in browsers:
        if browser in CHROMIUM_FAMILY and not args.chrome_extension_id:
            if args.browser == "all":
                print(f"Skipping {browser}: no --chrome-extension-id given.", file=sys.stderr)
                continue
            raise SystemExit(f"--chrome-extension-id is required for {browser}.")
        path = install(browser, args.chrome_extension_id)
        print(f"Registered {HOST_NAME} for {browser}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
