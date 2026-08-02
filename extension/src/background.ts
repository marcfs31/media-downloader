// MV3 service worker (event page on Firefox). Owns two things: performing
// direct browser.downloads.download() calls, and — for URLs pasted into the
// popup that aren't a direct media file — relaying the request to the local
// native-messaging host, which shells out to yt-dlp. See native-host/ for the
// other half of that path; this file never talks to yt-dlp directly.
import browser from "webextension-polyfill";
import { NATIVE_HOST_NAME } from "./shared";
import type {
  BackgroundRequest,
  BackgroundResponse,
  DownloadStatus,
  StatusBroadcast,
} from "./shared";

const DIRECT_MEDIA_EXTENSIONS = new Set([
  "mp4",
  "webm",
  "mov",
  "mkv",
  "avi",
  "mp3",
  "m4a",
  "wav",
  "oga",
  "ogg",
  "weba",
  "flac",
  "jpg",
  "jpeg",
  "png",
  "gif",
  "webp",
  "avif",
  "svg",
  "bmp",
  "pdf",
]);

let currentStatus: DownloadStatus = { state: "idle" };

async function setStatus(status: DownloadStatus): Promise<void> {
  currentStatus = status;
  await browser.storage.session.set({ downloadStatus: status });
  const broadcast: StatusBroadcast = { type: "DOWNLOAD_STATUS", status };
  browser.runtime.sendMessage(broadcast).catch(() => {
    // No popup listening right now — status is still saved to session storage.
  });
}

function extensionOf(url: string): string | null {
  try {
    const path = new URL(url).pathname;
    const last = path.split("/").pop() ?? "";
    const dot = last.lastIndexOf(".");
    return dot === -1 ? null : last.slice(dot + 1).toLowerCase();
  } catch {
    return null;
  }
}

async function isDirectMediaUrl(url: string): Promise<boolean> {
  const ext = extensionOf(url);
  if (ext && DIRECT_MEDIA_EXTENSIONS.has(ext)) return true;

  try {
    const res = await fetch(url, { method: "HEAD" });
    const contentType = res.headers.get("content-type") ?? "";
    return /^(video|audio|image)\//.test(contentType) || contentType === "application/pdf";
  } catch {
    return false;
  }
}

async function downloadDirect(
  url: string,
  filename: string | undefined,
): Promise<BackgroundResponse> {
  try {
    await browser.downloads.download({ url, filename, saveAs: false });
    return { type: "DOWNLOAD_STARTED" };
  } catch (err) {
    return { type: "DOWNLOAD_ERROR", message: err instanceof Error ? err.message : String(err) };
  }
}

interface NativeProgressMessage {
  id: string;
  type: "progress" | "done" | "error";
  percent?: number;
  speed?: string;
  eta?: number;
  path?: string;
  message?: string;
}

async function downloadViaNativeHost(url: string): Promise<BackgroundResponse> {
  let port: ReturnType<typeof browser.runtime.connectNative>;
  try {
    port = browser.runtime.connectNative(NATIVE_HOST_NAME);
  } catch (err) {
    return {
      type: "DOWNLOAD_ERROR",
      message: `Native host unavailable: ${err instanceof Error ? err.message : String(err)}`,
    };
  }

  const requestId = crypto.randomUUID();
  await setStatus({ state: "running", url });

  port.onMessage.addListener((raw: unknown) => {
    const msg = raw as NativeProgressMessage;
    if (msg.id !== requestId) return;
    if (msg.type === "progress") {
      void setStatus({
        state: "running",
        url,
        percent: msg.percent,
        speed: msg.speed,
        eta: msg.eta,
      });
    } else if (msg.type === "done") {
      void setStatus({ state: "done", url, path: msg.path ?? "" });
    } else if (msg.type === "error") {
      void setStatus({ state: "error", url, message: msg.message ?? "Download failed" });
    }
  });

  port.onDisconnect.addListener(() => {
    const err = browser.runtime.lastError;
    if (err && currentStatus.state === "running") {
      void setStatus({
        state: "error",
        url,
        message: `Native host not installed or crashed: ${err.message}. See README > Install the native host (re-run install.py if the project folder moved).`,
      });
    }
  });

  port.postMessage({ action: "download", url, id: requestId });
  return { type: "DOWNLOAD_STARTED" };
}

async function handleRequest(
  request: BackgroundRequest,
): Promise<BackgroundResponse | DownloadStatus> {
  switch (request.type) {
    case "DOWNLOAD_URL":
      return downloadDirect(request.url, request.filename);

    case "DOWNLOAD_VIA_LINK": {
      if (await isDirectMediaUrl(request.url)) {
        return downloadDirect(request.url, undefined);
      }
      return downloadViaNativeHost(request.url);
    }

    case "GET_DOWNLOAD_STATE":
      return currentStatus;
  }
}

browser.runtime.onMessage.addListener((message: unknown) =>
  handleRequest(message as BackgroundRequest),
);

browser.storage.session.get("downloadStatus").then((stored) => {
  if (stored.downloadStatus) currentStatus = stored.downloadStatus as DownloadStatus;
});
