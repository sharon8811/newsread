import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_SYSTEM_RULES,
  matchingSystemRule,
  SYSTEM_POLICY_REVISION,
} from "./system-policy.js";

describe("built-in history policy", () => {
  it("matches the shared backend contract", () => {
    const path = new URL(
      "../../shared/history-system-policy-v1.json",
      import.meta.url,
    );
    const fixture = JSON.parse(readFileSync(path, "utf8"));

    expect(fixture.revision).toBe(SYSTEM_POLICY_REVISION);
    expect(fixture.rules).toEqual(
      DEFAULT_SYSTEM_RULES.map(({ label: _, description: __, ...rule }) => rule),
    );
  });

  it("excludes exact Google search shapes without excluding useful content", () => {
    const enabled = DEFAULT_SYSTEM_RULES.map((rule) => ({
      ...rule,
      enabled: true,
    }));

    expect(
      matchingSystemRule(new URL("https://www.google.com/search?q=news"), enabled)
        ?.id,
    ).toBe("google-search");
    expect(
      matchingSystemRule(
        new URL("https://docs.google.com/document/d/useful"),
        enabled,
      ),
    ).toBeNull();
  });

  it("honors individual overrides", () => {
    const overridden = DEFAULT_SYSTEM_RULES.map((rule) => ({
      ...rule,
      enabled: rule.id !== "github-login",
    }));

    expect(
      matchingSystemRule(new URL("https://github.com/login"), overridden),
    ).toBeNull();
  });
});
