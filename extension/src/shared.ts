// Constants and message-shape types shared between content.ts, background.ts,
// and popup.ts. Keeping these in one file is what keeps the three isolated
// extension contexts speaking a consistent protocol.

/** Must match the "name" field in the native messaging host manifest that
 * native-host/media_downloader/install.py registers. */
export const NATIVE_HOST_NAME = "com.media_downloader.host";

export type MediaKind = "video" | "audio" | "image";

export interface DetectedMedia {
  id: string;
  kind: MediaKind;
  url: string;
  width?: number;
  height?: number;
  /** Concatenated <source type="..."> MIME types, when present — used to
   * spot codecs (AV1, VP9) common desktop players can't open. */
  sourceType?: string;
  pageUrl: string;
}

export type ContentRequest = { type: "GET_PAGE_MEDIA" };

export type ContentResponse = { type: "PAGE_MEDIA"; items: DetectedMedia[] };

/** Messages sent to the background service worker from content scripts or the popup. */
export type BackgroundRequest =
  | { type: "DOWNLOAD_URL"; url: string; filename?: string; pageUrl?: string }
  | { type: "DOWNLOAD_VIA_LINK"; url: string }
  | { type: "GET_DOWNLOAD_STATE" }
  /** One-way: the content script's initial page scan reporting how much
   * media it found, so the toolbar badge can show a count at a glance. */
  | { type: "REPORT_MEDIA_COUNT"; count: number }
  /** Popup diagnostic: is the native host actually reachable right now? */
  | { type: "CHECK_NATIVE_HOST" };

export type BackgroundResponse =
  | { type: "DOWNLOAD_STARTED" }
  | { type: "DOWNLOAD_ERROR"; message: string }
  | { type: "DOWNLOAD_NEEDS_NATIVE_HOST"; url: string }
  | { type: "NATIVE_HOST_STATUS"; connected: boolean };

/** Progress broadcasts the background worker sends to any listening popup while
 * a native-host-backed (yt-dlp) download is in flight. */
export type DownloadStatus =
  | { state: "idle" }
  | { state: "running"; url: string; percent?: number; speed?: string; eta?: number }
  | { state: "done"; url: string; path: string }
  | { state: "error"; url: string; message: string };

export type StatusBroadcast = { type: "DOWNLOAD_STATUS"; status: DownloadStatus };
