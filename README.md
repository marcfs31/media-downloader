# Media Downloader

Two cooperating pieces for saving media you can already access in your browser:

- **`extension/`** — a browser extension (Chrome/Edge Manifest V3 and Firefox)
  that overlays a small **⬇ download button** on any `<video>`, `<audio>`,
  `<img>`, or CSS background-image you hover on a page, plus a popup where you
  can paste **any link** and download whatever media is behind it. The popup
  also lists every piece of media detected on the current page.
- **`native-host/`** — a Python native-messaging host + standalone CLI. Direct
  file links (mp4, mp3, jpg, pdf, …) are streamed to disk; anything else
  (YouTube-style page URLs, HLS/DASH streams) is delegated to
  [yt-dlp](https://github.com/yt-dlp/yt-dlp).

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

The in-page download button and the popup's per-page media list work
immediately with no further setup — they use the browser's own downloads API.

### 2. Install the native host (only needed for pasted non-direct links)

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
```

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
