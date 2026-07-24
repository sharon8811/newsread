import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_SETTINGS, SETTINGS_KEY } from "./config.js";
import { getSettings, saveSettings } from "./settings.js";

const values: Record<string, unknown> = {};

vi.stubGlobal("chrome", {
  storage: {
    local: {
      get: vi.fn(async (key: string) => ({ [key]: values[key] })),
      set: vi.fn(async (patch: Record<string, unknown>) => {
        Object.assign(values, patch);
      }),
    },
  },
});

describe("Chrome storage settings", () => {
  beforeEach(() => {
    delete values[SETTINGS_KEY];
  });

  it("merges stored fields with privacy-safe defaults", async () => {
    values[SETTINGS_KEY] = { paused: true, serverUrl: "https://news.example" };
    expect(await getSettings()).toEqual({
      ...DEFAULT_SETTINGS,
      paused: true,
      serverUrl: "https://news.example",
    });
  });

  it("persists partial updates without discarding pairing state", async () => {
    values[SETTINGS_KEY] = {
      ...DEFAULT_SETTINGS,
      serverUrl: "https://news.example",
      token: "secret",
    };
    const saved = await saveSettings({ paused: true });
    expect(saved.paused).toBe(true);
    expect(saved.token).toBe("secret");
  });
});
