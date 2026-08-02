import browser from "webextension-polyfill";
import { filenameFromUrl } from "./media-detect";
import type {
  BackgroundRequest,
  BackgroundResponse,
  ContentRequest,
  ContentResponse,
  DetectedMedia,
  DownloadStatus,
  StatusBroadcast,
} from "./shared";

const urlInput = document.getElementById("mdlx-url-input") as HTMLInputElement;
const urlSubmit = document.getElementById("mdlx-url-submit") as HTMLButtonElement;
const statusEl = document.getElementById("mdlx-status") as HTMLParagraphElement;
const mediaList = document.getElementById("mdlx-media-list") as HTMLUListElement;

function renderStatus(status: DownloadStatus): void {
  statusEl.classList.remove("mdlx-status-error");
  switch (status.state) {
    case "idle":
      statusEl.hidden = true;
      return;
    case "running": {
      const pct = status.percent !== undefined ? ` ${status.percent.toFixed(0)}%` : "";
      const speed = status.speed ? ` (${status.speed})` : "";
      statusEl.textContent = `Downloading…${pct}${speed}`;
      break;
    }
    case "done":
      statusEl.textContent = `Saved: ${status.path}`;
      break;
    case "error":
      statusEl.textContent = status.message;
      statusEl.classList.add("mdlx-status-error");
      break;
  }
  statusEl.hidden = false;
}

async function submitUrl(): Promise<void> {
  const url = urlInput.value.trim();
  if (!url) return;
  urlSubmit.disabled = true;
  try {
    const request: BackgroundRequest = { type: "DOWNLOAD_VIA_LINK", url };
    const response = (await browser.runtime.sendMessage(request)) as BackgroundResponse;
    if (response.type === "DOWNLOAD_ERROR") {
      renderStatus({ state: "error", url, message: response.message });
    }
  } finally {
    urlSubmit.disabled = false;
  }
}

urlSubmit.addEventListener("click", () => void submitUrl());
urlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") void submitUrl();
});

function renderMediaList(items: DetectedMedia[]): void {
  mediaList.innerHTML = "";
  if (items.length === 0) {
    const li = document.createElement("li");
    li.className = "mdlx-empty";
    li.textContent = "No media detected on this page.";
    mediaList.appendChild(li);
    return;
  }

  for (const item of items) {
    const li = document.createElement("li");

    const kind = document.createElement("span");
    kind.className = "mdlx-kind";
    kind.textContent = item.kind;

    const url = document.createElement("span");
    url.className = "mdlx-url";
    url.textContent = item.url;
    url.title = item.url;

    const button = document.createElement("button");
    button.textContent = "⬇";
    button.title = "Download";
    button.addEventListener("click", async () => {
      button.disabled = true;
      const request: BackgroundRequest = {
        type: "DOWNLOAD_URL",
        url: item.url,
        filename: filenameFromUrl(item.url, item.kind),
        pageUrl: item.pageUrl,
      };
      const response = (await browser.runtime.sendMessage(request)) as BackgroundResponse;
      if (response.type === "DOWNLOAD_ERROR") {
        renderStatus({ state: "error", url: item.url, message: response.message });
      }
      button.disabled = false;
    });

    li.append(kind, url, button);
    mediaList.appendChild(li);
  }
}

async function loadMediaList(): Promise<void> {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    renderMediaList([]);
    return;
  }
  try {
    const request: ContentRequest = { type: "GET_PAGE_MEDIA" };
    const response = (await browser.tabs.sendMessage(tab.id, request)) as ContentResponse;
    renderMediaList(response.type === "PAGE_MEDIA" ? response.items : []);
  } catch {
    mediaList.innerHTML = "";
    const li = document.createElement("li");
    li.className = "mdlx-empty";
    li.textContent = "Can't scan this page (browser-internal pages are off-limits to extensions).";
    mediaList.appendChild(li);
  }
}

browser.runtime.onMessage.addListener((raw: unknown) => {
  const message = raw as StatusBroadcast;
  if (message?.type === "DOWNLOAD_STATUS") renderStatus(message.status);
});

browser.runtime
  .sendMessage({ type: "GET_DOWNLOAD_STATE" } satisfies BackgroundRequest)
  .then((status: unknown) => renderStatus(status as DownloadStatus));

void loadMediaList();
