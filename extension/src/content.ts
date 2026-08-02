// Runs in every frame of every page (see manifest content_scripts). Two jobs:
// 1. show a small download button when hovering video/audio/img/background-image
//    elements, 2. answer the popup's "what media is on this page" query.
import browser from "webextension-polyfill";
import {
  findMediaElement,
  resolveMediaUrl,
  filenameFromUrl,
  scanDocumentForMedia,
} from "./media-detect";
import type {
  BackgroundRequest,
  BackgroundResponse,
  ContentRequest,
  ContentResponse,
  MediaKind,
} from "./shared";

const HIDE_DELAY_MS = 250;

let currentTarget: { kind: MediaKind; url: string } | null = null;
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
    currentTarget = null;
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
    currentTarget = { kind: resolved.kind, url: resolved.url };
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
  if (!currentTarget) return;
  button.disabled = true;
  button.textContent = "…";
  try {
    await downloadResolved(currentTarget);
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

async function downloadResolved(target: { kind: MediaKind; url: string }): Promise<void> {
  let url = target.url;
  if (url.startsWith("blob:")) {
    url = await blobUrlToDataUrl(url);
  }
  // Once a blob: URL is converted to a data: URL its path is meaningless, so
  // derive the filename from whichever URL still carries useful info.
  const filename = filenameFromUrl(target.url.startsWith("blob:") ? url : target.url, target.kind);
  const request: BackgroundRequest = {
    type: "DOWNLOAD_URL",
    url,
    filename,
    pageUrl: location.href,
  };
  const response = (await browser.runtime.sendMessage(request)) as BackgroundResponse;
  if (response?.type === "DOWNLOAD_ERROR") {
    throw new Error(response.message);
  }
}

async function blobUrlToDataUrl(blobUrl: string): Promise<string> {
  const res = await fetch(blobUrl);
  const blob = await res.blob();
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read blob"));
    reader.readAsDataURL(blob);
  });
}

browser.runtime.onMessage.addListener((raw: unknown): ContentResponse | undefined => {
  const message = raw as ContentRequest;
  if (message?.type === "GET_PAGE_MEDIA") {
    return { type: "PAGE_MEDIA", items: scanDocumentForMedia(document, location.href) };
  }
  return undefined;
});
