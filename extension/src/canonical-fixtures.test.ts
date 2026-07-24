import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  canonicalizeCaptureDocument,
  hashCaptureDocument,
} from "./capture-document.js";
import type { CaptureDocument } from "./types.js";

interface FixtureCase {
  name: string;
  canonical_json: string;
  sha256: string;
}

interface FixtureFile {
  hash_prefix: string;
  cases: FixtureCase[];
}

describe("canonical capture fixtures", () => {
  it("locks the byte-level hash contract shared with the backend", () => {
    const path = new URL(
      "../../shared/history-capture-v1-fixtures.json",
      import.meta.url,
    );
    const fixtures = JSON.parse(
      readFileSync(path, "utf8"),
    ) as FixtureFile;

    for (const fixture of fixtures.cases) {
      const digest = createHash("sha256")
        .update(fixtures.hash_prefix, "utf8")
        .update(fixture.canonical_json, "utf8")
        .digest("hex");
      expect(digest, fixture.name).toBe(fixture.sha256);
    }
  });

  it("normalizes the shared v2 document identically to the backend", async () => {
    const path = new URL(
      "../../shared/history-canonicalization-v1.json",
      import.meta.url,
    );
    const fixture = JSON.parse(readFileSync(path, "utf8")) as {
      input: CaptureDocument;
      canonical_json: string;
      content_hash: string;
    };

    const canonical = canonicalizeCaptureDocument(fixture.input);

    expect(canonical.canonicalJson).toBe(fixture.canonical_json);
    expect(await hashCaptureDocument(canonical.canonicalJson)).toBe(
      fixture.content_hash,
    );
  });
});
