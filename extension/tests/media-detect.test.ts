import { describe, expect, it, beforeEach, vi } from "vitest";
import {
  chooseDownloadRequest,
  extensionForMime,
  filenameFromUrl,
  findMediaElement,
  looksCodecIncompatible,
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

  it("prefers a sibling <video> over an SVG-icon control button's own background-image", () => {
    // The exact shape of custom player skins: video and its overlay controls
    // are siblings under a shared player container, not ancestor/descendant.
    document.body.innerHTML = `
      <div class="player">
        <video src="https://example.com/clip.mp4"></video>
        <div class="controls">
          <button id="play-btn" style="background-image: url('icon-play.svg')"></button>
        </div>
      </div>`;
    const button = document.getElementById("play-btn")!;
    const found = findMediaElement(button);
    expect(found?.tagName).toBe("VIDEO");
    expect(resolveMediaUrl(found!, "https://example.com")?.url).toBe(
      "https://example.com/clip.mp4",
    );
  });

  it("prefers a sibling <video> over a poster <img> placeholder", () => {
    document.body.innerHTML = `
      <div class="player">
        <img id="poster" src="poster-placeholder.svg" class="poster">
        <video src="https://example.com/clip.mp4"></video>
      </div>`;
    const poster = document.getElementById("poster")!;
    const found = findMediaElement(poster);
    expect(found?.tagName).toBe("VIDEO");
    expect(resolveMediaUrl(found!, "https://example.com")?.url).toBe(
      "https://example.com/clip.mp4",
    );
  });

  it("re-resolving the same anchor finds a <video> lazily inserted after the initial hover", () => {
    // The exact reported bug: many players show a static poster (often
    // .webp) on hover and only mount the real <video> once playback starts
    // (autoplay-on-hover previews, click-to-play). Resolving once at hover
    // time and reusing that snapshot meant clicking after the video
    // appeared still downloaded the stale poster. content.ts's fix is to
    // re-run findMediaElement/resolveMediaUrl on the same anchor at click
    // time — this pins that re-resolution actually picks up the new video.
    document.body.innerHTML = `
      <div class="player">
        <img id="poster" src="preview-frame.webp" class="poster">
      </div>`;
    const poster = document.getElementById("poster")!;

    const beforePlay = findMediaElement(poster);
    expect(beforePlay).toBe(poster);
    expect(resolveMediaUrl(beforePlay!, "https://example.com")).toMatchObject({
      kind: "image",
      url: "https://example.com/preview-frame.webp",
    });

    // Playback starts; the site mounts the real <video> into the same player.
    document
      .querySelector(".player")!
      .insertAdjacentHTML("beforeend", `<video src="https://example.com/clip.mp4"></video>`);

    const afterPlay = findMediaElement(poster);
    expect(afterPlay?.tagName).toBe("VIDEO");
    expect(resolveMediaUrl(afterPlay!, "https://example.com")).toMatchObject({
      kind: "video",
      url: "https://example.com/clip.mp4",
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

  it("collects <source> type attributes onto the resolved result", () => {
    document.body.innerHTML = `
      <video>
        <source src="/clip.mp4" type='video/mp4; codecs="av01.0.05M.08"'>
      </video>`;
    const video = document.querySelector("video")!;
    const resolved = resolveMediaUrl(video, "https://example.com");
    expect(resolved?.sourceType).toContain("av01");
  });
});

describe("looksCodecIncompatible", () => {
  it("flags common poorly-supported-in-desktop-players codec tokens in the URL", () => {
    expect(looksCodecIncompatible("https://cdn.test/videos/clip-av1.mp4")).toBe(true);
    expect(looksCodecIncompatible("https://cdn.test/renditions/av01/seg1.mp4")).toBe(true);
    expect(looksCodecIncompatible("https://cdn.test/vp9/clip.webm")).toBe(true);
    expect(looksCodecIncompatible("https://cdn.test/vp09_clip.webm")).toBe(true);
  });

  it("flags a codec named in the <source> type even if the URL doesn't say so", () => {
    expect(
      looksCodecIncompatible(
        "https://cdn.test/opaque-id-123.mp4",
        'video/mp4; codecs="av01.0.05M.08"',
      ),
    ).toBe(true);
  });

  it("does not false-positive on unrelated substrings", () => {
    expect(looksCodecIncompatible("https://cdn.test/davis1/clip.mp4")).toBe(false);
    expect(looksCodecIncompatible("https://cdn.test/clip.mp4")).toBe(false);
    expect(looksCodecIncompatible("https://cdn.test/clip.mp4", "video/mp4")).toBe(false);
  });

  it("is case-insensitive", () => {
    expect(looksCodecIncompatible("https://cdn.test/CLIP-AV1.mp4")).toBe(true);
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

describe("chooseDownloadRequest", () => {
  it("downloads a plain progressive video directly", () => {
    const request = chooseDownloadRequest({
      kind: "video",
      url: "https://cdn.test/clip.mp4",
      pageUrl: "https://example.com/page",
    });
    expect(request).toEqual({
      type: "DOWNLOAD_URL",
      url: "https://cdn.test/clip.mp4",
      filename: "clip.mp4",
      pageUrl: "https://example.com/page",
    });
  });

  it("reroutes a blob: video through the page URL", () => {
    const request = chooseDownloadRequest({
      kind: "video",
      url: "blob:https://example.com/abc-123",
      pageUrl: "https://example.com/page",
    });
    expect(request).toEqual({ type: "DOWNLOAD_VIA_LINK", url: "https://example.com/page" });
  });

  it("reroutes an AV1/VP9 video through the page URL", () => {
    const request = chooseDownloadRequest({
      kind: "video",
      url: "https://cdn.test/clip-av1.mp4",
      pageUrl: "https://example.com/page",
    });
    expect(request).toEqual({ type: "DOWNLOAD_VIA_LINK", url: "https://example.com/page" });
  });

  it("does not reroute a blob: image (no MSE/codec concerns for images)", () => {
    const request = chooseDownloadRequest({
      kind: "image",
      url: "blob:https://example.com/abc-123",
      pageUrl: "https://example.com/page",
    });
    expect(request.type).toBe("DOWNLOAD_URL");
  });

  it("checks sourceType too, not just the URL", () => {
    const request = chooseDownloadRequest({
      kind: "video",
      url: "https://cdn.test/opaque-id",
      pageUrl: "https://example.com/page",
      sourceType: 'video/mp4; codecs="av01.0.05M.08"',
    });
    expect(request).toEqual({ type: "DOWNLOAD_VIA_LINK", url: "https://example.com/page" });
  });
});
