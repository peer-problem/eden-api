import { gzipSync } from "node:zlib";
import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST_ROOT = fileURLToPath(new URL("../dist/", import.meta.url));
const MAX_DIST_BYTES = 384 * 1024;
const MAX_INITIAL_GZIP_BYTES = 140 * 1024;

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(async (entry) => {
      const target = join(directory, entry.name);
      return entry.isDirectory() ? filesUnder(target) : [target];
    }),
  );
  return nested.flat();
}

const files = await filesUnder(DIST_ROOT);
const sizes = await Promise.all(files.map(async (file) => (await stat(file)).size));
const totalBytes = sizes.reduce((total, size) => total + size, 0);
const initialAssets = files.filter((file) => /\/assets\/[^/]+\.(?:css|js)$/.test(file));
const initialGzipBytes = (
  await Promise.all(initialAssets.map(async (file) => gzipSync(await readFile(file)).byteLength))
).reduce((total, size) => total + size, 0);

if (totalBytes > MAX_DIST_BYTES) {
  throw new Error(`Dashboard output is ${totalBytes} bytes, over the ${MAX_DIST_BYTES} byte budget.`);
}
if (initialGzipBytes > MAX_INITIAL_GZIP_BYTES) {
  throw new Error(
    `Initial JavaScript and CSS are ${initialGzipBytes} gzip bytes, over the ${MAX_INITIAL_GZIP_BYTES} byte budget.`,
  );
}

console.log(
  `Dashboard bundle gate passed: ${totalBytes} total bytes, ${initialGzipBytes} initial gzip bytes.`,
);
