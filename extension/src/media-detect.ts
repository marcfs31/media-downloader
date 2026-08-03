// Pure DOM-scanning helpers, kept free of any browser.* extension API so they
// can run under plain jsdom in tests. content.ts, popup.ts, and background.ts
// (for the context-menu entry) wire these into the live page/extension APIs.
import type { BackgroundRequest, DetectedMedia, MediaKind } from "./shared";

const MIN_BACKGROUND_AREA = 48 * 48;
const MAX_BACKGROUND_SCAN_CANDIDATES = 3000;
const BACKGROUND_URL_RE = /url\((['"]?)(.*?)\1\)/i;

// AV1 and VP9 are the two common web video codecs with poor-to-nonexistent
// support in default desktop players (QuickTime never learned AV1 at all;
// VP9 is mostly a Chromium-family thing) even though the browser itself
// decodes them fine in the page — which is exactly why a raw pass-through
// download of the video element's URL can hand back a file nothing else can
// open. Bounded by non-alphanumeric characters (or string edges) so this
// doesn't false-positive on an unrelated substring like "davis1".
const RISKY_CODEC_RE = /(?:^|[^a-z0-9])(av0?1|vp0?9)(?:[^a-z0-9]|$)/i;

const MIME_TO_EXT: Record<string, string> = {
  "video/mp4": "mp4",
  "video/webm": "webm",
  "video/ogg": "ogv",
  "video/quicktime": "mov",
  "audio/mpeg": "mp3",
  "audio/mp4": "m4a",
  "audio/ogg": "oga",
  "audio/wav": "wav",
  "audio/webm": "weba",
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/svg+xml": "svg",
  "image/avif": "avif",
};

export function extensionForMime(mime: string | undefined | null): string | null {
  if (!mime) return null;
  return MIME_TO_EXT[mime.toLowerCase().split(";")[0]?.trim() ?? ""] ?? null;
}

/** Derives a reasonable download filename from a URL (or data: URL), falling
 * back to a mime-derived extension when the path has none. */
export function filenameFromUrl(url: string, fallbackKind?: MediaKind): string {
  if (url.startsWith("data:")) {
    const mimeMatch = /^data:([^;,]+)/.exec(url);
    const ext = extensionForMime(mimeMatch?.[1]) ?? defaultExtFor(fallbackKind);
    return `download.${ext}`;
  }

  try {
    const parsed = new URL(url);
    const last = parsed.pathname.split("/").filter(Boolean).pop();
    if (last && last.includes(".")) return decodeURIComponent(last);
  } catch {
    // Not a parseable absolute URL — fall through to the generic name below.
  }
  return `download.${defaultExtFor(fallbackKind)}`;
}

function defaultExtFor(kind: MediaKind | undefined): string {
  switch (kind) {
    case "video":
      return "mp4";
    case "audio":
      return "mp3";
    case "image":
      return "jpg";
    default:
      return "bin";
  }
}

function backgroundImageUrl(el: Element): string | null {
  const style = (el.ownerDocument.defaultView ?? window).getComputedStyle(el);
  const bg = style.backgroundImage;
  if (!bg || bg === "none") return null;
  const match = BACKGROUND_URL_RE.exec(bg);
  return match?.[2] ?? null;
}

function toAbsolute(url: string, pageUrl: string): string {
  try {
    return new URL(url, pageUrl).toString();
  } catch {
    return url;
  }
}

function isSubstantial(el: Element): boolean {
  const rect = (el as HTMLElement).getBoundingClientRect?.();
  if (!rect) return true;
  return rect.width * rect.height >= MIN_BACKGROUND_AREA;
}

/** Given an element the user is hovering (or any element under the cursor),
 * walk up a few ancestors to find the nearest thing we know how to download.
 *
 * Custom player skins routinely overlay controls on top of the <video>
 * element — a poster thumbnail, SVG/background-image play-pause-fullscreen
 * icons — as *siblings* of the video, not ancestors of whatever's under the
 * cursor. A plain ancestor walk hovering one of those icons or the poster
 * would never reach the real <video>, and would happily "find" the icon's
 * background-image or the poster <img> instead — downloading an SVG icon or
 * a placeholder image rather than the actual media. So at every level we
 * also search descendants for a real video/audio and prefer that over an
 * img/background-image match, which is only used as a last-resort fallback
 * if no video/audio turns up anywhere in the ancestor chain. */
export function findMediaElement(target: Element, maxAncestors = 4): Element | null {
  let el: Element | null = target;
  let fallback: Element | null = null;

  for (let i = 0; el && i <= maxAncestors; i++) {
    if (el.matches("video, audio")) return el;
    const nested = el.querySelector("video, audio");
    if (nested) return nested;
    if (!fallback && (el.matches("img") || backgroundImageUrl(el))) fallback = el;
    el = el.parentElement;
  }
  return fallback;
}

/** True when the URL (or a <source> element's declared MIME type) names a
 * codec that common desktop players can't be trusted to open, even though
 * the browser played it fine in-page. Used to decide whether a direct
 * pass-through download is safe, or whether the link needs to go through
 * the yt-dlp-backed pipeline instead (which prefers H.264/AAC). */
export function looksCodecIncompatible(url: string, sourceType?: string | null): boolean {
  if (sourceType && RISKY_CODEC_RE.test(sourceType)) return true;
  return RISKY_CODEC_RE.test(url);
}

/** The single "how should this media actually get downloaded" decision,
 * shared by the hover button (content.ts), the popup's per-item list, and
 * the right-click context-menu entry (background.ts) — previously
 * duplicated between the first two. blob: video/audio (MediaSource object
 * URLs, unfetchable in principle) and AV1/VP9 (browser-playable but not
 * desktop-player-playable) both need the yt-dlp-backed pipeline instead of
 * a raw pass-through download. */
export function chooseDownloadRequest(params: {
  kind: MediaKind;
  url: string;
  pageUrl: string;
  sourceType?: string;
}): BackgroundRequest {
  const { kind, url, pageUrl, sourceType } = params;
  if (
    (kind === "video" || kind === "audio") &&
    (url.startsWith("blob:") || looksCodecIncompatible(url, sourceType))
  ) {
    return { type: "DOWNLOAD_VIA_LINK", url: pageUrl };
  }
  return { type: "DOWNLOAD_URL", url, filename: filenameFromUrl(url, kind), pageUrl };
}

const MEDIA_FILE_PATH_RE =
  /\.(mp4|webm|mov|mkv|avi|mp3|m4a|wav|oga|ogg|weba|flac|jpe?g|png|gif|webp|avif|svg|bmp|pdf)$/i;

function isLikelyMediaFileUrl(url: string): boolean {
  try {
    return MEDIA_FILE_PATH_RE.test(new URL(url).pathname);
  } catch {
    return MEDIA_FILE_PATH_RE.test(url);
  }
}

/** Walks up from a fallback image match looking for a same-origin link to
 * another page — the shape of "recommended videos" rails: each item is just
 * a thumbnail + link, with no <video> actually loaded until you open that
 * item's own page. Downloading the thumbnail literally is essentially never
 * what "download this video" means there. Restricted to same-origin links to
 * something that isn't itself a direct media file, so this doesn't hijack a
 * "click to view full-size image" link or an unrelated cross-site link. */
function linkedPageUrl(el: Element, pageUrl: string, maxAncestors = 4): string | null {
  let base: URL;
  try {
    base = new URL(pageUrl);
  } catch {
    return null;
  }
  let node: Element | null = el;
  for (let i = 0; node && i <= maxAncestors; i++) {
    if (node.matches("a[href]")) {
      const href = node.getAttribute("href") ?? "";
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return null;
      let absolute: URL;
      try {
        absolute = new URL(href, pageUrl);
      } catch {
        return null;
      }
      if (absolute.origin !== base.origin) return null;
      if (absolute.pathname === base.pathname && absolute.search === base.search) return null;
      if (isLikelyMediaFileUrl(absolute.toString())) return null;
      return absolute.toString();
    }
    node = node.parentElement;
  }
  return null;
}

/** Element-aware wrapper around chooseDownloadRequest for content.ts's hover
 * button, where a live DOM element (not just a resolved kind/url pair) is
 * available. Adds the one case chooseDownloadRequest can't see on its own:
 * a fallback image match that's actually a thumbnail linking to another
 * page — see linkedPageUrl. */
export function chooseDownloadRequestForElement(
  el: Element,
  pageUrl: string,
): BackgroundRequest | null {
  const resolved = resolveMediaUrl(el, pageUrl);
  if (!resolved) return null;
  if (resolved.kind === "image") {
    const linked = linkedPageUrl(el, pageUrl);
    if (linked) return { type: "DOWNLOAD_VIA_LINK", url: linked };
  }
  return chooseDownloadRequest({
    kind: resolved.kind,
    url: resolved.url,
    pageUrl,
    sourceType: resolved.sourceType,
  });
}

function sourceTypesOf(media: HTMLMediaElement): string | undefined {
  const types = Array.from(media.querySelectorAll("source"))
    .map((s) => s.getAttribute("type"))
    .filter((t): t is string => !!t);
  return types.length > 0 ? types.join("; ") : undefined;
}

/** Resolves the concrete media URL + kind for an element previously returned
 * by findMediaElement (or found directly, e.g. via querySelectorAll). */
export function resolveMediaUrl(
  el: Element,
  pageUrl: string,
): { kind: MediaKind; url: string; width?: number; height?: number; sourceType?: string } | null {
  const tag = el.tagName.toLowerCase();

  if (tag === "video" || tag === "audio") {
    const media = el as HTMLMediaElement;
    const src =
      media.currentSrc ||
      media.getAttribute("src") ||
      media.querySelector("source")?.getAttribute("src");
    if (!src) return null;
    const rect = el.getBoundingClientRect?.();
    return {
      kind: tag === "video" ? "video" : "audio",
      url: toAbsolute(src, pageUrl),
      width: rect?.width,
      height: rect?.height,
      sourceType: sourceTypesOf(media),
    };
  }

  if (tag === "img") {
    const img = el as HTMLImageElement;
    const src = img.currentSrc || img.getAttribute("src");
    if (!src) return null;
    const rect = el.getBoundingClientRect?.();
    return {
      kind: "image",
      url: toAbsolute(src, pageUrl),
      width: rect?.width,
      height: rect?.height,
    };
  }

  const bg = backgroundImageUrl(el);
  if (bg) {
    const rect = el.getBoundingClientRect?.();
    return {
      kind: "image",
      url: toAbsolute(bg, pageUrl),
      width: rect?.width,
      height: rect?.height,
    };
  }

  return null;
}

/** Full-document scan used to populate the popup's "detected on this page"
 * list. Bounded and cheap-check-first so it stays fast on huge pages. */
export function scanDocumentForMedia(root: ParentNode, pageUrl: string): DetectedMedia[] {
  const results: DetectedMedia[] = [];
  const seen = new Set<string>();

  const add = (el: Element) => {
    const resolved = resolveMediaUrl(el, pageUrl);
    if (!resolved || seen.has(resolved.url)) return;
    seen.add(resolved.url);
    results.push({
      id: `media-${results.length}`,
      kind: resolved.kind,
      url: resolved.url,
      width: resolved.width,
      height: resolved.height,
      sourceType: resolved.sourceType,
      pageUrl,
    });
  };

  root.querySelectorAll("video, audio, img").forEach(add);

  const candidates = root.querySelectorAll<HTMLElement>(
    "div, section, a, span, li, header, figure",
  );
  let checked = 0;
  for (const el of candidates) {
    if (checked >= MAX_BACKGROUND_SCAN_CANDIDATES) break;
    checked++;
    if (!isSubstantial(el)) continue;
    add(el);
  }

  return results;
}
