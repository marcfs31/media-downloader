import browser from "webextension-polyfill";
import { parseCompanionUrl } from "./companion";
import { chooseDownloadRequest } from "./media-detect";
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

    const copyButton = document.createElement("button");
    copyButton.textContent = "📋";
    copyButton.title = "Copy link";
    copyButton.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(item.url);
        copyButton.textContent = "✓";
      } catch {
        copyButton.textContent = "✗";
      } finally {
        setTimeout(() => (copyButton.textContent = "📋"), 1000);
      }
    });

    const button = document.createElement("button");
    button.textContent = "⬇";
    button.title = "Download";
    button.addEventListener("click", async () => {
      button.disabled = true;
      const request = chooseDownloadRequest({
        kind: item.kind,
        url: item.url,
        pageUrl: item.pageUrl,
        sourceType: item.sourceType,
      });
      const response = (await browser.runtime.sendMessage(request)) as BackgroundResponse;
      if (response.type === "DOWNLOAD_ERROR") {
        renderStatus({ state: "error", url: item.url, message: response.message });
      }
      button.disabled = false;
    });

    li.append(kind, url, copyButton, button);
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

const audioOnlyBox = document.getElementById("mdlx-audio-only") as HTMLInputElement;
const encryptBox = document.getElementById("mdlx-encrypt") as HTMLInputElement;
const stripMetadataBox = document.getElementById("mdlx-strip-metadata") as HTMLInputElement;
const qualitySelect = document.getElementById("mdlx-quality") as HTMLSelectElement;
const videoFormatSelect = document.getElementById("mdlx-video-format") as HTMLSelectElement;
const audioFormatSelect = document.getElementById("mdlx-audio-format") as HTMLSelectElement;
const qualityRow = document.getElementById("mdlx-quality-row") as HTMLLabelElement;
const videoFormatRow = document.getElementById("mdlx-video-format-row") as HTMLLabelElement;
const audioFormatRow = document.getElementById("mdlx-audio-format-row") as HTMLLabelElement;

const DOWNLOAD_OPTION_KEYS = [
  "audioOnly",
  "encryptDownloads",
  "stripMetadata",
  "quality",
  "videoFormat",
  "audioFormat",
] as const;

function updateFormatRowVisibility(): void {
  qualityRow.hidden = audioOnlyBox.checked;
  videoFormatRow.hidden = audioOnlyBox.checked;
  audioFormatRow.hidden = !audioOnlyBox.checked;
}

browser.storage.local.get([...DOWNLOAD_OPTION_KEYS]).then((stored) => {
  audioOnlyBox.checked = stored.audioOnly === true;
  encryptBox.checked = stored.encryptDownloads === true;
  stripMetadataBox.checked = stored.stripMetadata === true;
  if (typeof stored.quality === "string") qualitySelect.value = stored.quality;
  if (typeof stored.videoFormat === "string") videoFormatSelect.value = stored.videoFormat;
  if (typeof stored.audioFormat === "string") audioFormatSelect.value = stored.audioFormat;
  updateFormatRowVisibility();
});

audioOnlyBox.addEventListener("change", () => {
  void browser.storage.local.set({ audioOnly: audioOnlyBox.checked });
  updateFormatRowVisibility();
});
encryptBox.addEventListener("change", () => {
  void browser.storage.local.set({ encryptDownloads: encryptBox.checked });
});
stripMetadataBox.addEventListener("change", () => {
  void browser.storage.local.set({ stripMetadata: stripMetadataBox.checked });
});
qualitySelect.addEventListener("change", () => {
  void browser.storage.local.set({ quality: qualitySelect.value });
});
videoFormatSelect.addEventListener("change", () => {
  void browser.storage.local.set({ videoFormat: videoFormatSelect.value });
});
audioFormatSelect.addEventListener("change", () => {
  void browser.storage.local.set({ audioFormat: audioFormatSelect.value });
});

const companionInput = document.getElementById("mdlx-companion-input") as HTMLInputElement;
const companionSave = document.getElementById("mdlx-companion-save") as HTMLButtonElement;
const companionStatus = document.getElementById("mdlx-companion-status") as HTMLParagraphElement;

function showCompanionStatus(text: string, isError: boolean): void {
  companionStatus.textContent = text;
  companionStatus.classList.toggle("mdlx-status-error", isError);
  companionStatus.hidden = false;
}

browser.storage.local.get("companionServerUrl").then((stored) => {
  if (typeof stored.companionServerUrl === "string") {
    companionInput.value = stored.companionServerUrl;
  }
});

companionSave.addEventListener("click", async () => {
  const raw = companionInput.value.trim();
  if (raw && !parseCompanionUrl(raw)) {
    showCompanionStatus("Paste the full URL the server prints, including ?t=<token>.", true);
    return;
  }
  await browser.storage.local.set({ companionServerUrl: raw });
  showCompanionStatus(raw ? "Saved." : "Cleared.", false);
});

const nativeHostStatus = document.getElementById("mdlx-native-host-status") as HTMLParagraphElement;

browser.runtime
  .sendMessage({ type: "CHECK_NATIVE_HOST" } satisfies BackgroundRequest)
  .then((raw: unknown) => {
    const response = raw as BackgroundResponse;
    const connected = response?.type === "NATIVE_HOST_STATUS" && response.connected;
    nativeHostStatus.textContent = connected
      ? "Native host: ✓ connected"
      : "Native host: ✗ not reachable (see README > Install the native host)";
    nativeHostStatus.classList.toggle("mdlx-status-error", !connected);
  });

browser.runtime.onMessage.addListener((raw: unknown) => {
  const message = raw as StatusBroadcast;
  if (message?.type === "DOWNLOAD_STATUS") renderStatus(message.status);
});

browser.runtime
  .sendMessage({ type: "GET_DOWNLOAD_STATE" } satisfies BackgroundRequest)
  .then((status: unknown) => renderStatus(status as DownloadStatus));

void loadMediaList();
