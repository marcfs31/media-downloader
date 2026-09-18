---
name: playlist-batch-downloads
type: module
related: [correctness-gate-script]
---

`media-downloader-playlist` downloads every track in a playlist file
(`playlists/*.md`). Split in two on purpose, mirroring how the extension keeps
`media-detect.ts` pure:

- `native-host/media_downloader/playlist.py` — parsing only. No network, no
  yt-dlp, no filesystem beyond reading the file. This is where the fiddly part
  lives (which line opens a track, which URL is a real video vs. a
  search-results page), so it's unit-testable without mocking anything.
- `playlist_main`/`run_playlist` in `cli.py` — the batch runner: resolves,
  skips, downloads, and collects per-track outcomes.

## Why three entry kinds

The real playlist files contain all three, so the parser has to model all
three rather than assuming every track has a video link:

- `video` — a `watch`/`youtu.be`/`shorts` link, normalized to one canonical
  watch URL so the same video written two ways still dedupes.
- `search` — a `/results?search_query=…` link standing in for "the official
  video, whichever one that is". `downloader.resolve_search` takes the top hit
  via `ytsearch1:`. **A top hit is a guess, not an identification** — the
  runner prints the matched title so a wrong guess is spottable.
- `unavailable` — no YouTube link at all (prose, a Spotify link). Reported,
  never downloaded, and deliberately does **not** fail the run's exit code:
  it's a known gap in the input, not a tool failure.

## Footguns found the hard way

- **Dedupe keys collide with themselves.** A `video` entry's parsed
  `dedupe_key` and its post-resolution key are the same string
  (`video:<id>`), so recording the key before testing it marks every track a
  duplicate of itself. Test membership first, then record. Both keys are
  recorded: the parsed one stops a repeated search from going back over the
  network, the resolved one catches two different entries landing on the same
  video.
- **`find_existing` must not use glob.** yt-dlp's output template ends every
  name with `[<video-id>].<ext>`, and `[...]` is glob character-class syntax —
  `Path.glob("*[vm9XBp8G1Rw].*")` matches a single character, never the
  literal marker. It's a substring check over `iterdir()` instead. That marker
  surviving every post-processing step (audio extraction, H.264 normalization,
  metadata stripping) is what makes re-running a playlist resume rather than
  re-download.
