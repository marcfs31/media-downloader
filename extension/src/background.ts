// MV3 service worker (event page on Firefox). Owns two things: performing
// direct browser.downloads.download() calls, and — for URLs pasted into the
// popup that aren't a direct media file — relaying the request to the local
// native-messaging host, which shells out to yt-dlp. See native-host/ for the
// other half of that path; this file never talks to yt-dlp directly.
import browser from "webextension-polyfill";
import { NATIVE_HOST_NAME } from "./shared";
import { companionDownloadEndpoint, companionPageUrl, parseCompanionUrl } from "./companion";
import { chooseDownloadRequest } from "./media-detect";
import type { CompanionServer } from "./companion";
import type { Runtime } from "webextension-polyfill";
import type {
  BackgroundRequest,
  BackgroundResponse,
  DownloadStatus,
  MediaKind,
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

async function configuredCompanion(): Promise<CompanionServer | null> {
  const stored = await browser.storage.local.get("companionServerUrl");
  const raw = stored.companionServerUrl;
  return typeof raw === "string" ? parseCompanionUrl(raw) : null;
}

/** The popup's "Download options" (encrypt, audio-only, quality, formats)
 * apply to anything routed through the native host or companion server
 * (pasted links, and the hover button's AV1/VP9/blob reroutes) — never to
 * the plain browser.downloads.download fast path, since the downloads API
 * doesn't hand control of the written bytes back to us afterward. */
async function downloadPreferences(): Promise<{
  encrypt: boolean;
  audioOnly: boolean;
  quality: string | null;
  videoFormat: string;
  audioFormat: string;
  stripMetadata: boolean;
}> {
  const stored = await browser.storage.local.get([
    "encryptDownloads",
    "audioOnly",
    "quality",
    "videoFormat",
    "audioFormat",
    "stripMetadata",
  ]);
  return {
    encrypt: stored.encryptDownloads === true,
    audioOnly: stored.audioOnly === true,
    quality: typeof stored.quality === "string" && stored.quality ? stored.quality : null,
    videoFormat: typeof stored.videoFormat === "string" ? stored.videoFormat : "mp4",
    audioFormat: typeof stored.audioFormat === "string" ? stored.audioFormat : "m4a",
    stripMetadata: stored.stripMetadata === true,
  };
}

/** Forwards the link to the companion server (see native-host/…/server.py).
 * This is the whole yt-dlp path on Firefox for Android, where native
 * messaging doesn't exist; on desktop it's a fallback when the host isn't
 * installed. Returns false when no server is configured or it's unreachable. */
async function downloadViaCompanion(url: string): Promise<boolean> {
  const server = await configuredCompanion();
  if (!server) return false;
  const prefs = await downloadPreferences();
  try {
    const res = await fetch(companionDownloadEndpoint(server), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        url,
        encrypt: prefs.encrypt,
        audio_only: prefs.audioOnly,
        quality: prefs.quality,
        video_format: prefs.videoFormat,
        audio_format: prefs.audioFormat,
        strip_metadata: prefs.stripMetadata,
      }),
    });
    if (!res.ok) throw new Error(`companion server answered ${res.status}`);
  } catch {
    return false;
  }
  await setStatus({
    state: "done",
    url,
    path: `sent to companion server — save the file from ${companionPageUrl(server)}`,
  });
  return true;
}

async function downloadViaNativeHost(url: string): Promise<BackgroundResponse> {
  // Firefox for Android ships browser.runtime without connectNative at all.
  if (typeof browser.runtime.connectNative !== "function") {
    if (await downloadViaCompanion(url)) return { type: "DOWNLOAD_STARTED" };
    return {
      type: "DOWNLOAD_ERROR",
      message:
        "This platform has no native messaging. Run media-downloader-serve on a computer " +
        "and paste its URL into the popup's companion server field.",
    };
  }

  let port: ReturnType<typeof browser.runtime.connectNative>;
  try {
    port = browser.runtime.connectNative(NATIVE_HOST_NAME);
  } catch (err) {
    if (await downloadViaCompanion(url)) return { type: "DOWNLOAD_STARTED" };
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
      void (async () => {
        if (await downloadViaCompanion(url)) return;
        await setStatus({
          state: "error",
          url,
          message: `Native host not installed or crashed: ${err.message}. See README > Install the native host (re-run install.py if the project folder moved).`,
        });
      })();
    }
  });

  const prefs = await downloadPreferences();
  port.postMessage({
    action: "download",
    url,
    id: requestId,
    encrypt: prefs.encrypt,
    audio: prefs.audioOnly,
    quality: prefs.quality,
    video_format: prefs.videoFormat,
    audio_format: prefs.audioFormat,
    strip_metadata: prefs.stripMetadata,
  });
  return { type: "DOWNLOAD_STARTED" };
}

/** Badge reflects the count from the content script's one-time page scan at
 * load — not live-updated for content a page adds later (infinite scroll,
 * SPA navigation), which would need a MutationObserver's ongoing cost for
 * a "nice to glance at" indicator. Simple beats complete here. */
async function updateBadge(tabId: number, count: number): Promise<void> {
  await browser.action.setBadgeText({ tabId, text: count > 0 ? String(count) : "" });
  await browser.action.setBadgeBackgroundColor({ tabId, color: "#2b6cb0" });
}

/** Whether the native host is actually reachable right now, for the popup's
 * diagnostic line — exactly the question this project's own debugging kept
 * running into by trial and error. connectNative() alone doesn't prove
 * anything (it "succeeds" even if nothing is listening on the other end on
 * some platforms); only getting a real message back — even an error one, for
 * an action the host doesn't recognize — proves a process actually answered. */
async function checkNativeHost(): Promise<boolean> {
  if (typeof browser.runtime.connectNative !== "function") return false;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: boolean) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    let port: ReturnType<typeof browser.runtime.connectNative>;
    try {
      port = browser.runtime.connectNative(NATIVE_HOST_NAME);
    } catch {
      finish(false);
      return;
    }
    port.onMessage.addListener(() => finish(true));
    port.onDisconnect.addListener(() => finish(false));
    setTimeout(() => finish(false), 1500);
    try {
      port.postMessage({ action: "__healthcheck__" });
    } catch {
      finish(false);
    }
  });
}

async function handleRequest(
  request: BackgroundRequest,
  sender: Runtime.MessageSender,
): Promise<BackgroundResponse | DownloadStatus | undefined> {
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

    case "REPORT_MEDIA_COUNT":
      if (sender.tab?.id !== undefined) await updateBadge(sender.tab.id, request.count);
      return undefined;

    case "CHECK_NATIVE_HOST":
      return { type: "NATIVE_HOST_STATUS", connected: await checkNativeHost() };
  }
}

browser.runtime.onMessage.addListener((message: unknown, sender: Runtime.MessageSender) =>
  handleRequest(message as BackgroundRequest, sender),
);

browser.storage.session.get("downloadStatus").then((stored) => {
  if (stored.downloadStatus) currentStatus = stored.downloadStatus as DownloadStatus;
});

// Right-click "Download this media" — a robust complement to the hover
// button for cases where hovering is awkward (overlapping elements, touch
// devices without hover at all). The browser resolves srcUrl/mediaType for
// us directly, so this needs none of content.ts's DOM-walking logic — just
// the same routing decision (chooseDownloadRequest) once we have a URL.
const CONTEXT_MENU_ID = "mdlx-download-this-media";

browser.runtime.onInstalled.addListener(() => {
  browser.contextMenus.create({
    id: CONTEXT_MENU_ID,
    title: "Download this media",
    contexts: ["video", "audio", "image"],
  });
});

browser.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== CONTEXT_MENU_ID) return;
  const url = info.srcUrl;
  const mediaType = info.mediaType as MediaKind | undefined;
  const pageUrl = info.pageUrl ?? tab?.url;
  if (!url || !mediaType || !pageUrl) return;

  const request = chooseDownloadRequest({ kind: mediaType, url, pageUrl });
  void handleRequest(request, { tab }).then((response) => {
    if (response && "type" in response && response.type === "DOWNLOAD_ERROR") {
      console.error("Media Downloader: context-menu download failed:", response.message);
    }
  });
});
