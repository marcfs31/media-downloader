# Media Downloader

Two cooperating pieces for saving media you can already access in your browser:

- **`extension/`** — a browser extension (Chrome/Edge Manifest V3 and Firefox)
  that overlays a small **⬇ download button** on any `<video>`, `<audio>`,
  `<img>`, or CSS background-image you hover on a page, a right-click
  **"Download this media"** context-menu entry, and a popup where you can
  paste **any link** and download whatever media is behind it. The popup also
  lists every piece of media detected on the current page, with per-item
  download and copy-link buttons, and the toolbar icon badges how much media
  it found.
- **`native-host/`** — a Python native-messaging host + standalone CLI. Direct
  file links (mp4, mp3, jpg, pdf, …) are streamed to disk; anything else
  (YouTube-style page URLs, HLS/DASH streams, blob:-sourced video) is
  delegated to [yt-dlp](https://github.com/yt-dlp/yt-dlp).

**Scope, deliberately:** this tool downloads what your browser could already
save. It **refuses DRM-protected streams** (Netflix, Disney+, Spotify, …) —
both via yt-dlp format filtering and an explicit `has_drm` check — because
that would mean circumventing copy protection. Respect each site's terms of
service and copyright law; only download content you have the right to save.

## Setup

### 1. Build and load the extension

```bash
cd extension && pnpm install && pnpm build
```

- **Chrome/Edge**: open `chrome://extensions` (or `edge://extensions`), enable
  Developer mode, *Load unpacked* → pick `extension/dist/chrome`. The manifest
  pins a `key`, so the extension ID is always
  `jkflcccmckpakodmodoonlanhbcpnlfb` no matter where it's loaded from.
- **Firefox**: open `about:debugging#/runtime/this-firefox`, *Load Temporary
  Add-on* → pick any file inside `extension/dist/firefox`.

The in-page download button, the right-click menu entry, and the popup's
per-page media list work immediately with no further setup — they use the
browser's own downloads API. The popup shows a **"Native host: ✓/✗"** line so
you always know whether step 2 below is actually working.

### 2. Install the native host (needed for pasted non-direct links)

```bash
cd native-host
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m media_downloader.install --browser firefox
.venv/bin/python -m media_downloader.install --browser edge --chrome-extension-id jkflcccmckpakodmodoonlanhbcpnlfb
.venv/bin/python -m media_downloader.install --browser chrome --chrome-extension-id jkflcccmckpakodmodoonlanhbcpnlfb
```

Re-run the `install` command whenever this folder moves — the registered
launcher embeds absolute paths to this checkout's venv.

Downloads land in `~/Downloads/MediaDownloader/`. There's also a standalone
CLI that skips the browser entirely:

```bash
.venv/bin/media-downloader https://example.com/some-page-or-file -d ~/Downloads
.venv/bin/media-downloader --audio-only --audio-format mp3 https://example.com/song -d ~/Music
```

## Download options

Available in the popup's collapsible **"Download options"** panel, the phone
page, and as CLI flags — applies to anything routed through the native
host/companion server (pasted links, and the hover button's/context menu's
AV1·VP9·blob-sourced-video reroutes). It does **not** apply to plain direct
file downloads (a straightforward `.mp4`/`.jpg` link): those go straight
through the browser's own downloads API, which never hands the written bytes
back to this extension to transform afterward.

| Option | What it does | CLI flag |
|---|---|---|
| Audio only | Extracts just the audio track | `-a, --audio-only` |
| Quality | Caps video resolution (2160p/1080p/720p/480p/360p/best) | `-q, --quality` |
| Video format | Output container: `mp4` (default, universal) or `mkv` (fallback for streams mp4 can't hold). `webm` is deliberately not offered — our format selector prefers H.264/AAC for playability, and muxing that into webm would need a lossy re-encode, not a remux. | `--video-format` |
| Audio format | Output codec when audio-only: `m4a`/`mp3`/`opus`/`wav` | `--audio-format` |
| Encrypt at rest | AES-256-GCM-encrypts the finished file (`.enc` suffix); the companion server serves it decrypted on save (the on-disk copy stays ciphertext), but the native-host path needs an explicit decrypt step. **Not** a substitute for full-disk encryption (FileVault/BitLocker) — the key lives in a file on the same disk. | `-e, --encrypt` (decrypt with `media-downloader-decrypt <file>`) |
| Strip metadata | Removes embedded EXIF/container metadata via a lossless ffmpeg stream-copy remux (same file, same quality, tags gone) | `--strip-metadata` |
| Image format | Converts a direct image download to `jpg`/`png`/`webp` (needs ffmpeg) | `--image-format` |

**Multi-item posts** — a tweet/gallery with several attached images or videos
is one link with multiple items. By default only the first is downloaded; say
yes when asked (or tick "Download all" on the phone page) to get every item
zipped into one file. The CLI remembers your answer after asking once
("Always do this for multi-item posts?"); to skip the prompt outright use
`--download-all` or `--first-only`.

**Checksum** — every completed download reports a SHA-256 (CLI stderr, the
companion server's job list and phone-page UI) for verifying the file arrived
intact.

## Phones (iOS / Android)

Mobile browsers can't run the desktop setup — iOS Safari extensions get no
downloads API and no native messaging, and Android Chrome has no extensions
at all — so the phone version is the **companion server**: the same download
engine behind a phone-first web page, served over **HTTPS by default** (a
self-signed cert generated on first run — pass `--http` to opt out).

```bash
cd native-host && .venv/bin/media-downloader-serve
```

It prints a URL like `https://192.168.1.20:8765/?t=<token>` — open that on any
phone on the same Wi-Fi (the per-run token keeps other LAN devices out; the
browser will warn once about the self-signed cert — that's expected for a LAN
server with no public hostname, tap through/trust it to continue). Paste a
link, pick any [download options](#download-options), watch progress, then
tap **Save** to stream the finished file into the phone's own downloads.
Files also stay in `~/Downloads/MediaDownloader/` on the machine running the
server.

- **Share-sheet integration**: appending `&url=<link>` (and optionally
  `&audio=1`/`&encrypt=1`) auto-starts a download. An iOS Shortcut is the
  cleanest way to wire this up — add it to the share sheet, restrict input to
  URLs, and use **Get Contents of URL** as a `POST` to
  `.../api/download?t=<token>&wait=25` with a JSON body of
  `{"url": "Shortcut Input"}`. The `wait` parameter makes the request block
  until the download finishes (or 25s pass), so a **Show Notification** step
  afterward can report the real filename or error — no push service needed,
  since the whole thing rides on the Shortcut's own synchronous HTTP call.
  Any Android URL-forwarding app can do the equivalent.
- **Firefox for Android** can additionally run the extension itself (the
  popup's per-page media list + direct downloads work natively). For
  yt-dlp-class links, paste the companion server URL into the popup's
  *Companion server* field — the extension forwards those links to it, since
  Android has no native messaging. The same field works on desktop as a
  fallback when the native host isn't installed.
- **Cleanup**: `media-downloader-serve --keep-days 7` deletes files in the
  download folder older than N days, checked hourly — handy since a phone
  workflow tends to accumulate files on the Mac that have already been saved
  to the phone. Off by default.

### Dev workflow

`extension/`: `pnpm dev` (watch build), `pnpm test`, `pnpm lint`,
`pnpm typecheck`. `native-host/` (after adding the dev group:
`.venv/bin/pip install -e . --group dev`): `.venv/bin/pytest`,
`.venv/bin/ruff check .`, `.venv/bin/mypy media_downloader`.
`./scripts/run-checks.sh` from the repo root runs everything CI runs.

## Repo infrastructure

This project was bootstrapped from the Claude Code starter template
(`~/Claude/Template`) — see [CLAUDE.md](CLAUDE.md) for the correctness gate,
hooks, and conventions that came with it.
