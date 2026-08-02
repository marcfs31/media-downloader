#!/usr/bin/env node
// Bundles src/*.ts with esbuild and assembles two dist/ trees, one per browser,
// since Chrome and Firefox need slightly different manifest.json shapes (see
// manifest.chrome.json vs manifest.firefox.json) but share every other asset.
import { context } from "esbuild";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const watch = process.argv.includes("--watch");

const entryPoints = ["src/background.ts", "src/content.ts", "src/popup.ts"];

const browsers = [
  { name: "chrome", manifest: "manifest.chrome.json" },
  { name: "firefox", manifest: "manifest.firefox.json" },
];

async function copyStaticAssets({ name, manifest }) {
  const outdir = path.join(root, "dist", name);
  const manifestJson = await readFile(path.join(root, manifest), "utf8");
  await writeFile(path.join(outdir, "manifest.json"), manifestJson);
  await cp(path.join(root, "src", "popup.html"), path.join(outdir, "popup.html"));
  await cp(path.join(root, "src", "styles.css"), path.join(outdir, "styles.css"));
  await cp(path.join(root, "icons"), path.join(outdir, "icons"), { recursive: true });
}

async function buildBrowser(browser) {
  const outdir = path.join(root, "dist", browser.name);
  await rm(outdir, { recursive: true, force: true });
  await mkdir(outdir, { recursive: true });

  const ctx = await context({
    entryPoints: entryPoints.map((f) => path.join(root, f)),
    outdir,
    bundle: true,
    format: "iife",
    target: "es2022",
    sourcemap: true,
    logLevel: "info",
    plugins: [
      {
        name: "copy-static-assets",
        setup(build) {
          build.onEnd(() => copyStaticAssets(browser));
        },
      },
    ],
  });

  if (watch) {
    await ctx.watch();
    console.log(`Watching dist/${browser.name} for changes...`);
  } else {
    await ctx.rebuild();
    await ctx.dispose();
    console.log(`Built dist/${browser.name}`);
  }
}

for (const browser of browsers) {
  await buildBrowser(browser);
}

if (watch) {
  // Keep the process alive while esbuild's watchers run in the background.
  await new Promise(() => {});
}
