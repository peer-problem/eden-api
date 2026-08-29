import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = resolve(directory, entry);
    return statSync(path).isDirectory() ? sourceFiles(path) : /\.(ts|tsx)$/.test(entry) ? [path] : [];
  });
}

describe("browser architecture boundary", () => {
  const files = sourceFiles(resolve(process.cwd(), "src"));
  const sources = files.map((path) => ({ path, content: readFileSync(path, "utf8") }));

  it("contains no direct external host or alternate network client", () => {
    for (const source of sources) {
      expect(source.content, source.path).not.toMatch(/https?:\/\//i);
      expect(source.content, source.path).not.toMatch(/\b(XMLHttpRequest|WebSocket|EventSource)\b/);
    }
  });

  it("centralizes fetch in the same-origin v1 client", () => {
    const fetchFiles = sources.filter(({ content }) => /\bfetch\s*\(/.test(content));
    expect(fetchFiles.map(({ path }) => path.replace(process.cwd(), ""))).toEqual([
      "/src/api/client.ts",
    ]);
    expect(fetchFiles[0]?.content).toContain('path.startsWith("/v1/")');
    expect(fetchFiles[0]?.content).toContain('credentials: "same-origin"');
  });

  it("does not embed secret environment key names", () => {
    const forbidden = [
      "PUBLIC_DATA_SERVICE_KEY",
      "NAVER_CLIENT_SECRET",
      "YOUTUBE_API_KEY",
      "META_ACCESS_TOKEN",
      "X_BEARER_TOKEN",
      "DB_PASSWORD",
      "LLM_API_KEY",
    ];
    for (const source of sources) {
      for (const name of forbidden) expect(source.content, `${source.path}: ${name}`).not.toContain(name);
    }
  });
});
