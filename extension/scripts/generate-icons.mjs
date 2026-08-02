#!/usr/bin/env node
// Generates flat placeholder PNG icons (a down-arrow glyph) at the sizes the
// manifests reference, with zero image-library dependencies — just a minimal
// hand-rolled PNG encoder. Re-run if icons/ is ever deleted; the output is
// checked in so the build doesn't depend on this running every time.
import { deflateSync } from "node:zlib";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sizes = [16, 32, 48, 128];

const BG = [0x2b, 0x6c, 0xb0, 255]; // blue-ish square
const FG = [255, 255, 255, 255]; // white arrow

function crc32(buf) {
  let c;
  const table = crc32.table ?? (crc32.table = makeTable());
  let crc = 0xffffffff;
  for (const byte of buf) {
    c = table[(crc ^ byte) & 0xff];
    crc = c ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function makeTable() {
  const table = new Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
}

function chunk(type, data) {
  const typeBuf = Buffer.from(type, "ascii");
  const lenBuf = Buffer.alloc(4);
  lenBuf.writeUInt32BE(data.length);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([lenBuf, typeBuf, data, crcBuf]);
}

// A downward-pointing arrow (download glyph), true at pixels that should be
// drawn in the foreground color, on a normalized 0..1 grid.
function isArrowPixel(nx, ny) {
  const stemHalfWidth = 0.09;
  const inStem = Math.abs(nx - 0.5) < stemHalfWidth && ny > 0.2 && ny < 0.62;
  const headHeight = ny - 0.55;
  const headHalfWidth = 0.32 * (1 - headHeight / 0.3);
  const inHead = headHeight >= 0 && headHeight <= 0.3 && Math.abs(nx - 0.5) < headHalfWidth;
  const barY = ny > 0.78 && ny < 0.86;
  const inBar = barY && nx > 0.2 && nx < 0.8;
  return inStem || inHead || inBar;
}

function renderPng(size) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    const rowStart = y * (size * 4 + 1);
    raw[rowStart] = 0; // no filter
    for (let x = 0; x < size; x++) {
      const nx = x / size;
      const ny = y / size;
      const color = isArrowPixel(nx, ny) ? FG : BG;
      const off = rowStart + 1 + x * 4;
      raw[off] = color[0];
      raw[off + 1] = color[1];
      raw[off + 2] = color[2];
      raw[off + 3] = color[3];
    }
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type: RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const idat = deflateSync(raw);
  return Buffer.concat([
    signature,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const outdir = path.join(root, "icons");
await mkdir(outdir, { recursive: true });
for (const size of sizes) {
  const png = renderPng(size);
  await writeFile(path.join(outdir, `icon-${size}.png`), png);
  console.log(`Wrote icons/icon-${size}.png`);
}
