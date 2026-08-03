// Runs in every frame of every page (see manifest content_scripts). Two jobs:
// 1. show a small download button when hovering video/audio/img/background-image
//    elements, 2. answer the popup's "what media is on this page" query.
import browser from "webextension-polyfill";
import {
  findMediaElement,
  resolveMediaUrl,
  chooseDownloadRequest,
  scanDocumentForMedia,
} from "./media-detect";
import type { BackgroundResponse, ContentRequest, ContentResponse, MediaKind } from "./shared";

const HIDE_DELAY_MS = 250;

// The raw element last hovered — re-resolved fresh at click time (see the
// button's click handler) rather than trusting a snapshot taken at hover
// time. Many players show a static poster/preview image (often .webp) on
// hover and only mount the real <video> once playback actually starts
// (autoplay-on-hover previews, click-to-play); resolving once at hover and
// reusing that snapshot meant clicking after the swap still downloaded the
// stale poster instead of the video that's now actually there.
let currentAnchor: Element | null = null;
let hideTimer: ReturnType<typeof setTimeout> | null = null;

const button = document.createElement("button");
button.className = "mdlx-download-btn";
button.type = "button";
button.title = "Download this media";
button.textContent = "⬇";
button.style.display = "none";

function attachButton() {
  if (document.body && !button.isConnected) {
    document.body.appendChild(button);
  }
}

function positionButtonOver(el: Element) {
  const rect = el.getBoundingClientRect();
  button.style.top = `${Math.max(rect.top + 6, 6)}px`;
  button.style.left = `${Math.max(rect.right - 34, 6)}px`;
  button.style.display = "flex";
}

function scheduleHide() {
  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    button.style.display = "none";
    currentAnchor = null;
  }, HIDE_DELAY_MS);
}

function cancelHide() {
  if (hideTimer) {
    clearTimeout(hideTimer);
    hideTimer = null;
  }
}

document.addEventListener(
  "mouseover",
  (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target === button) {
      cancelHide();
      return;
    }
    const mediaEl = findMediaElement(target);
    if (!mediaEl) return;
    const resolved = resolveMediaUrl(mediaEl, location.href);
    if (!resolved) return;
    cancelHide();
    currentAnchor = target;
    attachButton();
    positionButtonOver(mediaEl);
  },
  { capture: true },
);

document.addEventListener(
  "mouseout",
  (event) => {
    const related = event.relatedTarget;
    if (related instanceof Node && button.contains(related)) return;
    scheduleHide();
  },
  { capture: true },
);

button.addEventListener("mouseenter", cancelHide);
button.addEventListener("mouseleave", scheduleHide);

button.addEventListener("click", async () => {
  if (!currentAnchor) return;
  // Everything below — including re-resolving the anchor — runs inside the
  // try/finally so the button always ends up showing ✓/✗ and re-enabling
  // itself. Previously the re-resolution ran *before* this block, so a throw
  // there (e.g. the background worker having reloaded out from under an
  // already-open tab's content script — "Extension context invalidated")
  // left the button stuck with no feedback at all, which looked exactly like
  // the click doing nothing.
  button.disabled = true;
  button.textContent = "…";
  try {
    // Re-run detection fresh rather than reusing whatever was resolved back
    // at mouseover time — see currentAnchor's comment for why that matters.
    const mediaEl = findMediaElement(currentAnchor);
    const resolved = mediaEl ? resolveMediaUrl(mediaEl, location.href) : null;
    if (!resolved) {
      button.textContent = "✗";
      return;
    }
    await downloadResolved(resolved);
    button.textContent = "✓";
  } catch (err) {
    console.error("Media Downloader: download failed", err);
    button.textContent = "✗";
  } finally {
    setTimeout(() => {
      button.textContent = "⬇";
      button.disabled = false;
    }, 1200);
  }
});

async function downloadResolved(target: {
  kind: MediaKind;
  url: string;
  sourceType?: string;
}): Promise<void> {
  const request = chooseDownloadRequest({
    kind: target.kind,
    url: target.url,
    pageUrl: location.href,
    sourceType: target.sourceType,
  });
  const response = (await browser.runtime.sendMessage(request)) as BackgroundResponse;
  if (response?.type === "DOWNLOAD_ERROR") {
    throw new Error(response.message);
  }
}

browser.runtime.onMessage.addListener((raw: unknown): ContentResponse | undefined => {
  const message = raw as ContentRequest;
  if (message?.type === "GET_PAGE_MEDIA") {
    return { type: "PAGE_MEDIA", items: scanDocumentForMedia(document, location.href) };
  }
  return undefined;
});

// Toolbar badge count: one scan at load, top frame only (so frames don't
// fight over the same badge). Not live-updated for content a page adds
// later — see background.ts's updateBadge for why that's an intentional
// simplification, not an oversight.
if (window.top === window.self) {
  const count = scanDocumentForMedia(document, location.href).length;
  browser.runtime.sendMessage({ type: "REPORT_MEDIA_COUNT", count }).catch(() => {
    // Background worker not ready yet, or this is a page the extension
    // can't message (rare) — the badge just stays blank, not worth retrying.
  });
}
