import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  CONTENT_HASH_PREFIX,
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

interface CanonicalizationFixtureCase {
  name: string;
  input: CaptureDocument;
  canonical_json: string;
  content_hash: string;
}

interface CanonicalizationFixtureFile {
  hash_prefix: string;
  cases: CanonicalizationFixtureCase[];
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

  it("normalizes the shared v2 corpus identically to the backend", async () => {
    const path = new URL(
      "../../shared/history-canonicalization-v1.json",
      import.meta.url,
    );
    const fixtures = JSON.parse(
      readFileSync(path, "utf8"),
    ) as CanonicalizationFixtureFile;

    expect(fixtures.hash_prefix).toBe(CONTENT_HASH_PREFIX);
    for (const fixture of fixtures.cases) {
      const canonical = canonicalizeCaptureDocument(fixture.input);
      expect(canonical.canonicalJson, fixture.name).toBe(
        fixture.canonical_json,
      );
      expect(
        await hashCaptureDocument(canonical.canonicalJson),
        fixture.name,
      ).toBe(fixture.content_hash);
    }
  });
});
