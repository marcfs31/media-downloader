import { describe, expect, it } from "vitest";
import { companionDownloadEndpoint, companionPageUrl, parseCompanionUrl } from "../src/companion";

describe("parseCompanionUrl", () => {
  it("parses the URL the server prints at startup", () => {
    const server = parseCompanionUrl("http://192.168.1.20:8765/?t=abc123");
    expect(server).toEqual({ base: "http://192.168.1.20:8765", token: "abc123" });
  });

  it("tolerates surrounding whitespace and extra path/query noise", () => {
    const server = parseCompanionUrl("  https://mac.local:9000/?t=tok&url=x  ");
    expect(server).toEqual({ base: "https://mac.local:9000", token: "tok" });
  });

  it("rejects URLs without a token", () => {
    expect(parseCompanionUrl("http://192.168.1.20:8765/")).toBeNull();
  });

  it("rejects non-http(s) schemes and garbage", () => {
    expect(parseCompanionUrl("ftp://x/?t=a")).toBeNull();
    expect(parseCompanionUrl("not a url")).toBeNull();
    expect(parseCompanionUrl("")).toBeNull();
  });
});

describe("endpoint builders", () => {
  const server = { base: "http://10.0.0.5:8765", token: "a/b c" };

  it("builds the download endpoint with an encoded token", () => {
    expect(companionDownloadEndpoint(server)).toBe("http://10.0.0.5:8765/api/download?t=a%2Fb%20c");
  });

  it("builds the human page URL the popup shows after a handoff", () => {
    expect(companionPageUrl(server)).toBe("http://10.0.0.5:8765/?t=a%2Fb%20c");
  });
});
