import { execFileSync } from "node:child_process";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");

await mkdir(resolve(dist, "popup"), { recursive: true });
await mkdir(resolve(dist, "options"), { recursive: true });
await cp(resolve(root, "manifest.json"), resolve(dist, "manifest.json"));
await cp(resolve(root, "src/popup/popup.html"), resolve(dist, "popup/popup.html"));
await cp(resolve(root, "src/popup/popup.css"), resolve(dist, "popup/popup.css"));
await cp(resolve(root, "src/options/options.html"), resolve(dist, "options/options.html"));
await cp(resolve(root, "src/options/options.css"), resolve(dist, "options/options.css"));
await cp(resolve(root, "NOTICE"), resolve(dist, "NOTICE"));
await cp(resolve(root, "../LICENSE"), resolve(dist, "NEWSREAD_LICENSE"));
await cp(
  resolve(root, "third_party/SMART_HISTORY_LICENSE.txt"),
  resolve(dist, "SMART_HISTORY_LICENSE.txt"),
);

const manifestPath = resolve(dist, "manifest.json");
const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
manifest.version = JSON.parse(
  await readFile(resolve(root, "package.json"), "utf8"),
).version;
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

// Package dist/ contents (not the directory) into the zip the backend serves
// from Settings → Browser history. Uses the system `zip` so the extension
// toolchain stays dependency-free; skipping only costs the in-app download.
const packagePath = resolve(root, "newsread-history-extension.zip");
try {
  await rm(packagePath, { force: true });
  execFileSync("zip", ["-r", "-X", "-q", packagePath, "."], { cwd: dist });
  console.log(`packaged ${packagePath}`);
} catch (error) {
  console.warn(`zip packaging skipped: ${error.message}`);
}
