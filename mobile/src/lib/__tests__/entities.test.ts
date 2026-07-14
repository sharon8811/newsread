import {
  entityDisplayName,
  entityKey,
  entityKindLabel,
  isNameEntity,
} from "../entities";
import type { ArticleEntity } from "../types";

function makeEntity(over: Partial<ArticleEntity> = {}): ArticleEntity {
  return {
    id: 1,
    kind: "person",
    key: "peter thiel",
    url: "",
    source: "ner",
    badge: {},
    data: { name: "Peter Thiel" },
    ...over,
  };
}

describe("isNameEntity", () => {
  it("marks person/org/product as name entities, enricher kinds as not", () => {
    expect(isNameEntity({ kind: "person" })).toBe(true);
    expect(isNameEntity({ kind: "org" })).toBe(true);
    expect(isNameEntity({ kind: "product" })).toBe(true);
    expect(isNameEntity({ kind: "github" })).toBe(false);
    expect(isNameEntity({ kind: "arxiv" })).toBe(false);
  });
});

describe("entityKindLabel", () => {
  it("maps known kinds and falls back to the raw kind", () => {
    expect(entityKindLabel("person")).toBe("Person");
    expect(entityKindLabel("github")).toBe("GitHub");
    expect(entityKindLabel("mystery")).toBe("mystery");
  });
});

describe("entityDisplayName", () => {
  it("prefers the badge label, then data.name, then the key", () => {
    expect(
      entityDisplayName(makeEntity({ badge: { label: "acme/x" }, data: {} })),
    ).toBe("acme/x");
    expect(entityDisplayName(makeEntity())).toBe("Peter Thiel");
    expect(entityDisplayName(makeEntity({ data: {} }))).toBe("peter thiel");
    // Non-string junk in the payloads never surfaces.
    expect(
      entityDisplayName(makeEntity({ badge: { label: 7 }, data: { name: 9 } })),
    ).toBe("peter thiel");
  });
});

describe("entityKey", () => {
  it("builds the SWR key once the route param resolves", () => {
    expect(entityKey(7)).toBe("/entities/7");
    expect(entityKey("7")).toBe("/entities/7");
    expect(entityKey(undefined)).toBeNull();
    expect(entityKey(null)).toBeNull();
  });
});
