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
  pageUrl: string;
}

export type ContentRequest = { type: "GET_PAGE_MEDIA" } | { type: "RESOLVE_BLOB"; url: string };

export type ContentResponse =
  { type: "PAGE_MEDIA"; items: DetectedMedia[] } | { type: "RESOLVED_BLOB"; dataUrl: string };

/** Messages sent to the background service worker from content scripts or the popup. */
export type BackgroundRequest =
  | { type: "DOWNLOAD_URL"; url: string; filename?: string; pageUrl?: string }
  | { type: "DOWNLOAD_VIA_LINK"; url: string }
  | { type: "GET_DOWNLOAD_STATE" };

export type BackgroundResponse =
  | { type: "DOWNLOAD_STARTED" }
  | { type: "DOWNLOAD_ERROR"; message: string }
  | { type: "DOWNLOAD_NEEDS_NATIVE_HOST"; url: string };

/** Progress broadcasts the background worker sends to any listening popup while
 * a native-host-backed (yt-dlp) download is in flight. */
export type DownloadStatus =
  | { state: "idle" }
  | { state: "running"; url: string; percent?: number; speed?: string; eta?: number }
  | { state: "done"; url: string; path: string }
  | { state: "error"; url: string; message: string };

export type StatusBroadcast = { type: "DOWNLOAD_STATUS"; status: DownloadStatus };
