import { describe, expect, it, beforeEach, vi } from "vitest";
import {
  extensionForMime,
  filenameFromUrl,
  findMediaElement,
  resolveMediaUrl,
  scanDocumentForMedia,
} from "../src/media-detect";

describe("extensionForMime", () => {
  it("maps known mime types", () => {
    expect(extensionForMime("video/mp4")).toBe("mp4");
    expect(extensionForMime("image/jpeg;charset=binary")).toBe("jpg");
  });

  it("returns null for unknown or missing mime types", () => {
    expect(extensionForMime("application/x-nonsense")).toBeNull();
    expect(extensionForMime(undefined)).toBeNull();
  });
});

describe("filenameFromUrl", () => {
  it("uses the last path segment when it has an extension", () => {
    expect(filenameFromUrl("https://example.com/videos/clip.mp4?x=1")).toBe("clip.mp4");
  });

  it("derives a name from a data: URL's mime type", () => {
    expect(filenameFromUrl("data:video/webm;base64,AAAA")).toBe("download.webm");
  });

  it("falls back to a kind-appropriate extension when nothing else is known", () => {
    expect(filenameFromUrl("https://example.com/download", "audio")).toBe("download.mp3");
    expect(filenameFromUrl("https://example.com/download")).toBe("download.bin");
  });
});

describe("findMediaElement / resolveMediaUrl", () => {
  it("finds a video element directly", () => {
    document.body.innerHTML = `<video src="https://example.com/a.mp4"></video>`;
    const video = document.querySelector("video")!;
    expect(findMediaElement(video)).toBe(video);
    expect(resolveMediaUrl(video, "https://example.com")).toMatchObject({
      kind: "video",
      url: "https://example.com/a.mp4",
    });
  });

  it("walks up ancestors to find a background-image container", () => {
    document.body.innerHTML = `
      <div style="background-image: url('/bg.jpg')">
        <span><em id="leaf">hover me</em></span>
      </div>`;
    const leaf = document.getElementById("leaf")!;
    const found = findMediaElement(leaf);
    expect(found).not.toBeNull();
    expect(resolveMediaUrl(found!, "https://example.com/page")).toMatchObject({
      kind: "image",
      url: "https://example.com/bg.jpg",
    });
  });

  it("returns null when nothing resolvable is nearby", () => {
    document.body.innerHTML = `<div><span id="leaf">plain text</span></div>`;
    const leaf = document.getElementById("leaf")!;
    expect(findMediaElement(leaf)).toBeNull();
  });

  it("prefers an <img> currentSrc over a bare src when both exist", () => {
    document.body.innerHTML = `<img src="/a.jpg" />`;
    const img = document.querySelector("img")! as HTMLImageElement;
    expect(resolveMediaUrl(img, "https://example.com")?.url).toBe("https://example.com/a.jpg");
  });
});

describe("scanDocumentForMedia", () => {
  beforeEach(() => {
    // jsdom has no layout engine, so every element reports a zero-size rect.
    // Real browsers return real geometry; stub it here so the "is this
    // background-image container substantial enough to count" heuristic has
    // something to gate on.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 200,
      height: 200,
      top: 0,
      left: 0,
      right: 200,
      bottom: 200,
      x: 0,
      y: 0,
      toJSON() {
        return {};
      },
    });
  });

  it("collects video, img, and background-image media, de-duplicated by URL", () => {
    document.body.innerHTML = `
      <video src="https://example.com/a.mp4"></video>
      <img src="https://example.com/a.jpg" />
      <img src="https://example.com/a.jpg" />
      <div style="background-image: url('https://example.com/bg.png')"></div>
    `;
    const items = scanDocumentForMedia(document, "https://example.com/page");
    const urls = items.map((i) => i.url).sort();
    expect(urls).toEqual([
      "https://example.com/a.jpg",
      "https://example.com/a.mp4",
      "https://example.com/bg.png",
    ]);
  });

  it("returns an empty list for a page with no media", () => {
    document.body.innerHTML = `<p>Just text.</p>`;
    expect(scanDocumentForMedia(document, "https://example.com")).toEqual([]);
  });
});
