"""Media Downloader native host: direct-file downloads plus a yt-dlp backend.

Two entry points share downloader.py:
- host.py    — the browser-facing native messaging host (stdio protocol),
- cli.py     — `media-downloader <url>` for use without a browser.
"""

__version__ = "0.1.0"
