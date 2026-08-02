// Pure DOM-scanning helpers, kept free of any browser.* extension API so they
// can run under plain jsdom in tests. content.ts is the only caller that wires
// these into the live page and the extension messaging layer.
import type { DetectedMedia, MediaKind } from "./shared";

const MIN_BACKGROUND_AREA = 48 * 48;
const MAX_BACKGROUND_SCAN_CANDIDATES = 3000;
const BACKGROUND_URL_RE = /url\((['"]?)(.*?)\1\)/i;

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
 * walk up a few ancestors to find the nearest thing we know how to download. */
export function findMediaElement(target: Element, maxAncestors = 4): Element | null {
  let el: Element | null = target;
  for (let i = 0; el && i <= maxAncestors; i++) {
    if (el.matches("video, audio, img")) return el;
    if (backgroundImageUrl(el)) return el;
    el = el.parentElement;
  }
  return null;
}

/** Resolves the concrete media URL + kind for an element previously returned
 * by findMediaElement (or found directly, e.g. via querySelectorAll). */
export function resolveMediaUrl(
  el: Element,
  pageUrl: string,
): { kind: MediaKind; url: string; width?: number; height?: number } | null {
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
