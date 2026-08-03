"""LAN companion server: the phone-facing version of the extension.

Phones can't run this project's extension the way desktops do — iOS Safari
extensions get neither a downloads API nor native messaging, and Android
Chrome has no extensions at all — so the mobile "version" is the same
download engine behind a tiny HTTP server with a phone-first web page:
paste (or share) a link on the phone, watch progress, then save the finished
file straight into the phone's own downloads.

Run it on the machine that has yt-dlp/ffmpeg:

    media-downloader-serve

and open the printed URL (includes a per-run access token) on any phone on
the same network. `?url=...` prefills and auto-starts a download, which makes
iOS Shortcuts / Android share-sheet integration a one-liner.

Firefox for Android can run the extension itself (popup + direct downloads);
its background worker falls back to POSTing here for yt-dlp-class links,
since native messaging doesn't exist on mobile.

Serves HTTPS by default (a self-signed cert generated on first run — see
certs.py for what that does and doesn't protect against); pass --http to
serve plain HTTP instead.
"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import secrets
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .certs import get_or_create_cert
from .crypto import decrypt_bytes, display_name, get_or_create_key
from .downloader import (
    DownloadError,
    DownloadOptions,
    Progress,
    download,
    inspect_post,
    sha256_of,
)
from .host import DEFAULT_DEST

MAX_REMEMBERED_JOBS = 100
MAX_WAIT_SECONDS = 600.0


@dataclass
class Job:
    id: str
    url: str
    options: DownloadOptions = field(default_factory=DownloadOptions)
    state: str = "queued"  # queued | running | done | error
    percent: float | None = None
    speed: str | None = None
    message: str | None = None
    filename: str | None = None
    path: Path | None = None
    checksum: str | None = None

    def to_public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "url": self.url,
            "audio_only": self.options.audio_only,
            "encrypt": self.options.encrypt,
            "quality": self.options.quality,
            "video_format": self.options.video_format,
            "audio_format": self.options.audio_format,
            "download_all": self.options.download_all,
            "strip_metadata": self.options.strip_metadata,
            "state": self.state,
            "percent": self.percent,
            "speed": self.speed,
            "message": self.message,
            "filename": self.filename,
            "checksum": self.checksum,
        }


class JobStore:
    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            return [job.to_public() for job in self._jobs.values()]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(self, url: str, options: DownloadOptions | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, url=url, options=options or DownloadOptions())
        with self._lock:
            self._jobs[job.id] = job
            # Bound memory over a long-running server: forget the oldest
            # finished jobs (their files stay on disk regardless).
            finished = [
                jid
                for jid, j in self._jobs.items()
                if j.state in ("done", "error") and jid != job.id
            ]
            for jid in finished[: max(0, len(self._jobs) - MAX_REMEMBERED_JOBS)]:
                del self._jobs[jid]
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def _run(self, job: Job) -> None:
        job.state = "running"

        def on_progress(p: Progress) -> None:
            job.percent = p.percent
            job.speed = p.speed

        try:
            path = download(job.url, self.dest, on_progress, job.options)
            job.path = path
            job.filename = path.name
            job.checksum = sha256_of(path)
            job.percent = 100.0
            job.state = "done"
        except DownloadError as exc:
            job.state = "error"
            job.message = str(exc)
        except Exception as exc:  # report, never kill the worker thread
            job.state = "error"
            job.message = f"Unexpected: {exc}"


def sweep_old_files(dest: Path, keep_days: float, now: float | None = None) -> list[Path]:
    """Delete files in dest older than keep_days. Returns what was removed.

    Only plain files directly inside dest are touched — this sweeps the
    server's own download folder, never anything the user nested inside it.
    """
    if keep_days <= 0 or not dest.is_dir():
        return []
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    removed: list[Path] = []
    for entry in dest.iterdir():
        try:
            if entry.is_file() and not entry.is_symlink() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed.append(entry)
        except OSError:
            continue  # vanished mid-sweep or unreadable — skip, not fatal
    return removed


def _sweep_loop(dest: Path, keep_days: float) -> None:
    while True:
        removed = sweep_old_files(dest, keep_days)
        for path in removed:
            print(f"cleanup: removed {path.name} (older than {keep_days:g} days)")
        time.sleep(3600)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Media Downloader</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 16px/1.45 system-ui, sans-serif;
         background: Canvas; color: CanvasText; }
  main { max-width: 560px; margin: 0 auto; padding: 20px 16px 48px; }
  h1 { font-size: 22px; margin: 8px 0 18px; }
  form { display: flex; gap: 8px; }
  input[type=url] { flex: 1; min-width: 0; padding: 12px; font-size: 16px;
                    border: 1px solid color-mix(in srgb, CanvasText 25%, Canvas);
                    border-radius: 10px; background: Field; color: FieldText; }
  button { padding: 12px 16px; font-size: 16px; border: 0; border-radius: 10px;
           background: #2b6cb0; color: #fff; }
  button:active { opacity: .8; }
  select { font-size: 14px; padding: 6px 8px; border-radius: 8px;
           border: 1px solid color-mix(in srgb, CanvasText 25%, Canvas);
           background: Field; color: FieldText; }
  ul { list-style: none; padding: 0; margin: 22px 0 0; }
  li { padding: 12px 0; border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, Canvas); }
  .u { font-size: 13px; opacity: .7; overflow: hidden; text-overflow: ellipsis;
       white-space: nowrap; }
  .bar { height: 6px; border-radius: 3px; margin-top: 8px;
         background: color-mix(in srgb, CanvasText 12%, Canvas); overflow: hidden; }
  .bar > div { height: 100%; background: #2b6cb0; transition: width .4s; }
  .err { color: #d33; font-size: 14px; margin-top: 6px; }
  a.save { display: inline-block; margin-top: 8px; font-weight: 600; color: #2b6cb0; }
  .meta { font-size: 13px; opacity: .75; margin-top: 6px; }
  .checksum { font-size: 11px; opacity: .55; margin-top: 4px; word-break: break-all; }
  .opts { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 12px; }
  .opt { display: inline-flex; align-items: center; gap: 6px;
         font-size: 14px; opacity: .85; }
  .opt input[type=checkbox] { width: 18px; height: 18px; }
  .tag { font-size: 11px; border: 1px solid currentColor; border-radius: 4px;
         padding: 0 4px; margin-left: 6px; opacity: .7; }
</style>
</head>
<body>
<main>
  <h1>&#11015; Media Downloader</h1>
  <form id="f">
    <input id="u" type="url" placeholder="Paste a link&hellip;" required
           autocomplete="off" autocapitalize="off">
    <button>Download</button>
  </form>
  <div class="opts">
    <label class="opt"><input type="checkbox" id="audio"> Audio only</label>
    <label class="opt"><input type="checkbox" id="encrypt"> Encrypt on disk</label>
    <label class="opt"><input type="checkbox" id="strip_metadata"> Strip metadata</label>
    <label class="opt">Quality
      <select id="quality">
        <option value="">Best</option>
        <option value="2160p">2160p</option>
        <option value="1080p">1080p</option>
        <option value="720p">720p</option>
        <option value="480p">480p</option>
        <option value="360p">360p</option>
      </select>
    </label>
    <label class="opt" id="video-format-row">Video
      <select id="video_format">
        <option value="mp4">mp4</option>
        <option value="mkv">mkv</option>
      </select>
    </label>
    <label class="opt" id="audio-format-row" hidden>Audio
      <select id="audio_format">
        <option value="m4a">m4a</option>
        <option value="mp3">mp3</option>
        <option value="opus">opus</option>
        <option value="wav">wav</option>
      </select>
    </label>
  </div>
  <ul id="jobs"></ul>
</main>
<script>
const TOKEN = "__TOKEN__";
const form = document.getElementById("f");
const input = document.getElementById("u");
const list = document.getElementById("jobs");
const audioBox = document.getElementById("audio");
const encryptBox = document.getElementById("encrypt");
const stripMetadataBox = document.getElementById("strip_metadata");
const qualitySelect = document.getElementById("quality");
const videoFormatSelect = document.getElementById("video_format");
const audioFormatSelect = document.getElementById("audio_format");
const videoFormatRow = document.getElementById("video-format-row");
const audioFormatRow = document.getElementById("audio-format-row");

audioBox.addEventListener("change", () => {
  videoFormatRow.hidden = audioBox.checked;
  qualitySelect.closest(".opt").hidden = audioBox.checked;
  audioFormatRow.hidden = !audioBox.checked;
});

async function api(path, options) {
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(path + sep + "t=" + encodeURIComponent(TOKEN), options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function currentOptions() {
  return {
    audio_only: audioBox.checked,
    encrypt: encryptBox.checked,
    strip_metadata: stripMetadataBox.checked,
    quality: qualitySelect.value || null,
    video_format: videoFormatSelect.value,
    audio_format: audioFormatSelect.value,
  };
}

async function startDownload(url, overrides) {
  const body = Object.assign({ url }, currentOptions(), overrides || {});
  await api("/api/download", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  await refresh();
}

async function maybeAskDownloadAll(url) {
  // Multi-item posts (a tweet/gallery with several images or videos) only
  // grab the first item by default. Peek first so we can offer "all N" —
  // remembered per-browser via localStorage, so it only asks once.
  const remembered = localStorage.getItem("mdlx-download-all");
  if (remembered === "always") return true;
  if (remembered === "never") return false;
  try {
    const info = await api("/api/inspect?url=" + encodeURIComponent(url));
    if (info.count > 1) {
      const remember = confirm(
        "This post has " + info.count + " items. Download all of them? " +
        "(Cancel downloads just the first.)"
      );
      return remember;
    }
  } catch (err) { /* inspection is best-effort */ }
  return false;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = input.value.trim();
  if (!url) return;
  input.value = "";
  try {
    const downloadAll = await maybeAskDownloadAll(url);
    await startDownload(url, { download_all: downloadAll });
  } catch (err) { alert(err.message || err); }
});

function render(jobs) {
  list.innerHTML = "";
  for (const job of jobs.reverse()) {
    const li = document.createElement("li");
    const u = document.createElement("div");
    u.className = "u"; u.textContent = job.url;
    if (job.audio_only) {
      const tag = document.createElement("span");
      tag.className = "tag"; tag.textContent = "audio";
      u.appendChild(tag);
    }
    if (job.encrypt) {
      const tag = document.createElement("span");
      tag.className = "tag"; tag.textContent = "encrypted";
      u.appendChild(tag);
    }
    if (job.strip_metadata) {
      const tag = document.createElement("span");
      tag.className = "tag"; tag.textContent = "stripped";
      u.appendChild(tag);
    }
    li.appendChild(u);
    if (job.state === "done") {
      const a = document.createElement("a");
      a.className = "save";
      a.href = "/files/" + job.id + "?t=" + encodeURIComponent(TOKEN);
      a.setAttribute("download", job.filename || "download");
      a.textContent = "Save “" + (job.filename || "file") + "”";
      li.appendChild(a);
      if (job.checksum) {
        const c = document.createElement("div");
        c.className = "checksum"; c.textContent = "sha256: " + job.checksum;
        li.appendChild(c);
      }
    } else if (job.state === "error") {
      const p = document.createElement("div");
      p.className = "err"; p.textContent = job.message || "Failed";
      li.appendChild(p);
    } else {
      const bar = document.createElement("div"); bar.className = "bar";
      const fill = document.createElement("div");
      fill.style.width = (job.percent || 0) + "%";
      bar.appendChild(fill); li.appendChild(bar);
      const meta = document.createElement("div"); meta.className = "meta";
      meta.textContent = job.state === "queued" ? "Queued…"
        : "Downloading… " + (job.percent ? job.percent.toFixed(0) + "%" : "")
          + (job.speed ? " · " + job.speed : "");
      li.appendChild(meta);
    }
    list.appendChild(li);
  }
}

async function refresh() {
  try { render(await api("/api/jobs")); } catch (err) { /* server briefly busy */ }
}
setInterval(refresh, 1200);
refresh();

// Share-sheet / Shortcuts integration: ?url=... prefills and auto-starts.
const params = new URLSearchParams(location.search);
const shared = params.get("url");
if (shared) {
  input.value = shared;
  if (params.get("audio") === "1") {
    audioBox.checked = true;
    audioBox.dispatchEvent(new Event("change"));
  }
  if (params.get("encrypt") === "1") encryptBox.checked = true;
  maybeAskDownloadAll(shared)
    .then((downloadAll) => startDownload(shared, { download_all: downloadAll }))
    .catch((err) => alert(err.message || err));
  history.replaceState(null, "", location.pathname + "?t=" + encodeURIComponent(TOKEN));
}
</script>
</body>
</html>
"""


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        host: str,
        port: int,
        dest: Path,
        token: str,
        tls: bool = False,
        extra_tls_hosts: tuple[str, ...] = (),
    ) -> None:
        self.store = JobStore(dest)
        self.token = token
        super().__init__((host, port), RequestHandler)
        if tls:
            cert_path, key_path = get_or_create_cert(extra_hosts=extra_tls_hosts)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert_path), str(key_path))
            self.socket = context.wrap_socket(self.socket, server_side=True)


class RequestHandler(BaseHTTPRequestHandler):
    server: CompanionServer  # narrows the inherited type for mypy

    # Silence per-request stderr logging; it's noise under a phone's polling.
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _authorized(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("t") or [""])[0]
        return hmac.compare_digest(supplied, self.server.token)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if not self._authorized():
            self._send_json(401, {"error": "missing or wrong token"})
            return
        route = urlparse(self.path).path

        if route == "/":
            body = PAGE_TEMPLATE.replace("__TOKEN__", self.server.token).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/jobs":
            self._send_json(200, self.server.store.list())
            return

        if route.startswith("/api/jobs/"):
            job = self.server.store.get(route.removeprefix("/api/jobs/"))
            if job is None:
                self._send_json(404, {"error": "no such job"})
                return
            self._send_json(200, job.to_public())
            return

        if route == "/api/inspect":
            self._handle_inspect()
            return

        if route.startswith("/files/"):
            self._serve_file(route.removeprefix("/files/"))
            return

        self._send_json(404, {"error": "not found"})

    def _handle_inspect(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        url = (query.get("url") or [""])[0]
        if not url.startswith(("http://", "https://")):
            self._send_json(400, {"error": "query must include url=http(s)://..."})
            return
        try:
            count = inspect_post(url)
        except Exception:
            count = 1
        self._send_json(200, {"url": url, "count": count})

    def _serve_file(self, job_id: str) -> None:
        # Files are addressed by job id only — client input never touches the
        # filesystem path, so there is no traversal surface here.
        job = self.server.store.get(job_id)
        if job is None or job.state != "done" or job.path is None or not job.path.is_file():
            self._send_json(404, {"error": "file not ready"})
            return

        if job.options.encrypt:
            # Decrypted only transiently, in memory, to serve this one
            # request — the on-disk copy stays ciphertext.
            try:
                data = decrypt_bytes(job.path.read_bytes(), get_or_create_key())
            except Exception as exc:
                self._send_json(500, {"error": f"could not decrypt: {exc}"})
                return
            name = display_name(job.path)
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{name.replace(chr(34), "")}"'
            )
            self.end_headers()
            self.wfile.write(data)
            return

        content_type = mimetypes.guess_type(job.path.name)[0] or "application/octet-stream"
        size = job.path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{job.path.name.replace(chr(34), "")}"',
        )
        self.end_headers()
        with open(job.path, "rb") as fh:
            while chunk := fh.read(256 * 1024):
                self.wfile.write(chunk)

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        if not self._authorized():
            self._send_json(401, {"error": "missing or wrong token"})
            return
        if urlparse(self.path).path != "/api/download":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            self._send_json(400, {"error": 'body must be {"url": "http(s)://..."}'})
            return

        try:
            options = DownloadOptions(
                audio_only=bool(payload.get("audio_only")),
                encrypt=bool(payload.get("encrypt")),
                quality=payload.get("quality") or None,
                video_format=str(payload.get("video_format") or "mp4"),
                audio_format=str(payload.get("audio_format") or "m4a"),
                image_format=payload.get("image_format") or None,
                download_all=bool(payload.get("download_all")),
                strip_metadata=bool(payload.get("strip_metadata")),
            )
            options.validate()
        except DownloadError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        job = self.server.store.start(url, options)

        # ?wait=<seconds> blocks the response until the job settles (or the
        # window runs out). This is what lets an iOS Shortcut's synchronous
        # "Get Contents of URL" show a real done/failed notification without
        # any push infrastructure.
        query = parse_qs(urlparse(self.path).query)
        try:
            wait = float((query.get("wait") or ["0"])[0])
        except ValueError:
            wait = 0.0
        deadline = time.monotonic() + min(wait, MAX_WAIT_SECONDS)
        while job.state in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.25)

        self._send_json(200 if job.state in ("done", "error") else 202, job.to_public())


def lan_ip() -> str:
    """Best-effort LAN address for the printed URL (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # TEST-NET, never actually reached
            return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="media-downloader-serve",
        description="Serve the phone-facing Media Downloader page on the local network.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: all)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("-d", "--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument(
        "--token",
        default=None,
        help="Access token required on every request (default: generated per run)",
    )
    parser.add_argument(
        "--keep-days",
        type=float,
        default=0.0,
        help="Delete downloaded files older than this many days (0 = never, default)",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve plain HTTP instead of HTTPS (a self-signed cert is used by default)",
    )
    args = parser.parse_args(argv)

    token = args.token or secrets.token_urlsafe(8)
    ip = lan_ip()
    server = CompanionServer(
        args.host,
        args.port,
        args.dest,
        token,
        tls=not args.http,
        extra_tls_hosts=(ip, f"{socket.gethostname()}.local"),
    )
    port = server.server_address[1]
    scheme = "http" if args.http else "https"
    print("Media Downloader companion server")
    print(f"  On this machine:  {scheme}://127.0.0.1:{port}/?t={token}")
    print(f"  On your phone:    {scheme}://{ip}:{port}/?t={token}")
    if not args.http:
        print("  (self-signed cert — your browser will warn once; trust it to continue)")
    print(f"  Downloads go to:  {args.dest}")
    if args.keep_days > 0:
        print(f"  Auto-cleanup:     files older than {args.keep_days:g} day(s) are deleted hourly")
        threading.Thread(target=_sweep_loop, args=(args.dest, args.keep_days), daemon=True).start()
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
