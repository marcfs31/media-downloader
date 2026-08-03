// Companion-server helpers, kept pure for testability. The companion server
// (native-host/media_downloader/server.py) is the phone-era sibling of the
// native messaging host: on platforms with no native messaging (Firefox for
// Android) — or any desktop where the host isn't installed — the background
// worker forwards yt-dlp-class links to it over HTTP instead.

export interface CompanionServer {
  base: string;
  token: string;
}

/** Parses the URL the companion server prints at startup
 * (http://host:port/?t=TOKEN). Returns null for anything unusable. */
export function parseCompanionUrl(stored: string): CompanionServer | null {
  let url: URL;
  try {
    url = new URL(stored.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const token = url.searchParams.get("t");
  if (!token) return null;
  return { base: `${url.protocol}//${url.host}`, token };
}

export function companionDownloadEndpoint(server: CompanionServer): string {
  return `${server.base}/api/download?t=${encodeURIComponent(server.token)}`;
}

export function companionPageUrl(server: CompanionServer): string {
  return `${server.base}/?t=${encodeURIComponent(server.token)}`;
}
