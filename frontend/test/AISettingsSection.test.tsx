import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AISettingsSection from "@/components/AISettingsSection";
import type { AISettings } from "@/lib/api";

const { swrMock, mutateMock } = vi.hoisted(() => ({
  swrMock: vi.fn(),
  mutateMock: vi.fn(),
}));
vi.mock("swr", () => ({ default: swrMock, mutate: mutateMock }));

const DEFAULT_PROMPT =
  "{article_title} showcased in a gritty noir comic book splash page. " +
  "High contrast chiaroscuro lighting, heavy ink lines, dramatic angle. " +
  "Full bleed, edge-to-edge artwork, masterpiece.";

const UNCONFIGURED: AISettings = {
  configured: false,
  system_available: true,
  provider: null,
  model: null,
  base_url: null,
  key_hint: null,
  supports_vision: false,
  image: null,
  image_generation_available: false,
  image_prompt: null,
  default_image_prompt: DEFAULT_PROMPT,
  image_gen_monthly_limit: null,
  image_generations_this_month: 0,
};

const CONFIGURED: AISettings = {
  configured: true,
  system_available: true,
  provider: "openai",
  model: "gpt-5",
  base_url: null,
  key_hint: "5678",
  supports_vision: false,
  image: null,
  image_generation_available: false,
  image_prompt: null,
  default_image_prompt: DEFAULT_PROMPT,
  image_gen_monthly_limit: null,
  image_generations_this_month: 0,
};

function withSettings(settings: AISettings | undefined) {
  swrMock.mockReturnValue({ data: settings });
}

function okFetch(body: unknown = {}) {
  const mock = vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => body });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function lastCall(fetchMock: ReturnType<typeof vi.fn>) {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return {
    url: url as string,
    method: (init as RequestInit).method,
    body: (init as RequestInit).body
      ? JSON.parse((init as RequestInit).body as string)
      : undefined,
  };
}

describe("<AISettingsSection>", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    vi.unstubAllGlobals();
  });

  it("defaults to the system model with the key fields hidden", () => {
    withSettings(UNCONFIGURED);
    render(<AISettingsSection />);
    expect(screen.getByLabelText("Model provider")).toHaveValue("system");
    expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test connection" })).not.toBeInTheDocument();
  });

  it("warns when there is no system default to fall back to", () => {
    withSettings({ ...UNCONFIGURED, system_available: false });
    render(<AISettingsSection />);
    expect(screen.getByText(/AI features need your own key/)).toBeInTheDocument();
  });

  it("saving a new key PUTs provider, model and key", async () => {
    withSettings(UNCONFIGURED);
    const fetchMock = okFetch(CONFIGURED);
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "openai");
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled(); // no key or model yet

    await userEvent.type(screen.getByLabelText("API key"), "sk-test-12345678");
    await userEvent.type(screen.getByLabelText("Model"), "gpt-5");
    await userEvent.click(save);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = lastCall(fetchMock);
    expect(call.url).toContain("/ai/settings");
    expect(call.method).toBe("PUT");
    expect(call.body).toEqual({
      provider: "openai",
      model: "gpt-5",
      api_key: "sk-test-12345678",
      supports_vision: false,
    });
    expect(mutateMock).toHaveBeenCalledWith("/ai/settings");
    expect(mutateMock).toHaveBeenCalledWith("/ai/status");
    // The typed key never lingers in the field.
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(await screen.findByText("AI settings saved.")).toBeInTheDocument();
  });

  it("custom provider requires and sends the base URL", async () => {
    withSettings(UNCONFIGURED);
    const fetchMock = okFetch(CONFIGURED);
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "custom");
    await userEvent.type(screen.getByLabelText("API key"), "sk-any-12345678");
    await userEvent.type(screen.getByLabelText("Model"), "llama");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(); // base URL missing

    await userEvent.type(screen.getByLabelText("Base URL"), "http://ollama.local/v1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(lastCall(fetchMock).body.base_url).toBe("http://ollama.local/v1");
  });

  it("prefills stored settings and keeps the key when left blank", async () => {
    withSettings(CONFIGURED);
    const fetchMock = okFetch(CONFIGURED);
    render(<AISettingsSection />);

    expect(screen.getByLabelText("Model provider")).toHaveValue("openai");
    expect(screen.getByLabelText("Model")).toHaveValue("gpt-5");
    expect(screen.getByLabelText("API key")).toHaveAttribute("placeholder", "••••••••5678");

    await userEvent.clear(screen.getByLabelText("Model"));
    await userEvent.type(screen.getByLabelText("Model"), "gpt-6");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(lastCall(fetchMock).body).toEqual({
      provider: "openai",
      model: "gpt-6",
      supports_vision: false,
    });
  });

  it("sends supports_vision when the vision toggle is on", async () => {
    withSettings(CONFIGURED);
    const fetchMock = okFetch(CONFIGURED);
    render(<AISettingsSection />);

    await userEvent.click(screen.getByRole("checkbox", { name: /Model can read images/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(lastCall(fetchMock).body.supports_vision).toBe(true);
  });

  it("prefills the vision toggle from the stored settings", () => {
    withSettings({ ...CONFIGURED, supports_vision: true });
    render(<AISettingsSection />);
    expect(screen.getByRole("checkbox", { name: /Model can read images/ })).toBeChecked();
  });

  it("switching provider demands a fresh key", async () => {
    withSettings(CONFIGURED);
    okFetch(CONFIGURED);
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "anthropic");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("API key"), "sk-ant-12345678");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("choosing the system default DELETEs the stored settings", async () => {
    withSettings(CONFIGURED);
    const fetchMock = okFetch();
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "system");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = lastCall(fetchMock);
    expect(call.url).toContain("/ai/settings");
    expect(call.method).toBe("DELETE");
    expect(await screen.findByText("Using the system default model.")).toBeInTheDocument();
  });

  it("tests a typed key against the form values", async () => {
    withSettings(UNCONFIGURED);
    const fetchMock = okFetch({ ok: true, detail: null, model: "gpt-5" });
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "openai");
    await userEvent.type(screen.getByLabelText("API key"), "sk-test-12345678");
    await userEvent.type(screen.getByLabelText("Model"), "gpt-5");
    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = lastCall(fetchMock);
    expect(call.url).toContain("/ai/settings/test");
    expect(call.body).toEqual({
      provider: "openai",
      model: "gpt-5",
      api_key: "sk-test-12345678",
      base_url: "",
    });
    expect(await screen.findByText("Connection works (gpt-5).")).toBeInTheDocument();
  });

  it("shows the provider error when the test fails", async () => {
    withSettings(CONFIGURED);
    okFetch({ ok: false, detail: "invalid api key", model: "gpt-5" });
    render(<AISettingsSection />);

    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/invalid api key/)).toBeInTheDocument();
  });

  it("tests stored settings without echoing the key", async () => {
    withSettings(CONFIGURED);
    const fetchMock = okFetch({ ok: true, detail: null, model: "gpt-5" });
    render(<AISettingsSection />);

    await userEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(lastCall(fetchMock).body).toEqual({ model: "gpt-5" });
  });

  it("includes the image model block when enabled", async () => {
    withSettings(UNCONFIGURED);
    const fetchMock = okFetch(CONFIGURED);
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "openai");
    await userEvent.type(screen.getByLabelText("API key"), "sk-test-12345678");
    await userEvent.type(screen.getByLabelText("Model"), "gpt-5");
    await userEvent.click(screen.getByRole("checkbox", { name: /Image generation/ }));
    expect(screen.getByText("optional — uses your main key")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Image model"), "gpt-image-1");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(lastCall(fetchMock).body.image).toEqual({ provider: "openai", model: "gpt-image-1" });
  });

  it("surfaces backend validation errors", async () => {
    withSettings(UNCONFIGURED);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 422,
        ok: false,
        json: async () => ({ detail: "An API key is required" }),
      }),
    );
    render(<AISettingsSection />);

    await userEvent.selectOptions(screen.getByLabelText("Model provider"), "openai");
    await userEvent.type(screen.getByLabelText("API key"), "sk-test-12345678");
    await userEvent.type(screen.getByLabelText("Model"), "gpt-5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("An API key is required")).toBeInTheDocument();
  });
});


describe("<AISettingsSection> image prompt", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    vi.unstubAllGlobals();
  });

  it("is hidden when no image generation is available", () => {
    withSettings(UNCONFIGURED);
    render(<AISettingsSection />);
    expect(screen.queryByLabelText("Article image prompt")).not.toBeInTheDocument();
  });

  it("shows the default prompt as placeholder when generation is available", () => {
    withSettings({ ...UNCONFIGURED, image_generation_available: true });
    render(<AISettingsSection />);
    const textarea = screen.getByLabelText("Article image prompt");
    expect(textarea).toHaveValue("");
    expect(textarea).toHaveAttribute("placeholder", DEFAULT_PROMPT);
  });

  it("saves a custom prompt via PATCH /users/me", async () => {
    withSettings({ ...UNCONFIGURED, image_generation_available: true });
    const fetchMock = okFetch();
    render(<AISettingsSection />);

    await userEvent.type(
      screen.getByLabelText("Article image prompt"),
      // userEvent treats "{" as a key descriptor; "{{" types a literal brace.
      "Watercolor of {{article_title}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find(([url]) => (url as string).includes("/users/me"));
    expect(patch).toBeTruthy();
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
      image_prompt: "Watercolor of {article_title}",
    });
  });

  it("resets to the default prompt", async () => {
    withSettings({
      ...UNCONFIGURED,
      image_generation_available: true,
      image_prompt: "My custom prompt",
    });
    const fetchMock = okFetch();
    render(<AISettingsSection />);

    expect(screen.getByLabelText("Article image prompt")).toHaveValue("My custom prompt");
    await userEvent.click(screen.getByRole("button", { name: "Reset to default" }));
    expect(screen.getByLabelText("Article image prompt")).toHaveValue("");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find(([url]) => (url as string).includes("/users/me"));
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({ image_prompt: "" });
  });

  it("does not PATCH when the prompt is unchanged", async () => {
    withSettings({
      ...UNCONFIGURED,
      image_generation_available: true,
      image_prompt: "My custom prompt",
    });
    const fetchMock = okFetch();
    render(<AISettingsSection />);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.getByText("Using the system default model.")).toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls.some(([url]) => (url as string).includes("/users/me"))).toBe(false);
  });
});

describe("<AISettingsSection> monthly image budget", () => {
  beforeEach(() => {
    swrMock.mockReset();
    mutateMock.mockClear();
    vi.unstubAllGlobals();
  });

  it("shows this month's usage against the limit", () => {
    withSettings({
      ...UNCONFIGURED,
      image_generation_available: true,
      image_gen_monthly_limit: 100,
      image_generations_this_month: 7,
    });
    const { container } = render(<AISettingsSection />);
    expect(screen.getByLabelText("Monthly image budget")).toHaveValue(100);
    expect(container.textContent).toContain("7 images generated this month of 100");
  });

  it("saves the budget via PATCH /users/me", async () => {
    withSettings({ ...UNCONFIGURED, image_generation_available: true });
    const fetchMock = okFetch();
    render(<AISettingsSection />);

    await userEvent.type(screen.getByLabelText("Monthly image budget"), "25");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find(([url]) => (url as string).includes("/users/me"));
    expect(patch).toBeTruthy();
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
      image_gen_monthly_limit: 25,
    });
  });

  it("clears the budget back to unlimited", async () => {
    withSettings({
      ...UNCONFIGURED,
      image_generation_available: true,
      image_gen_monthly_limit: 25,
    });
    const fetchMock = okFetch();
    render(<AISettingsSection />);

    await userEvent.clear(screen.getByLabelText("Monthly image budget"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const patch = fetchMock.mock.calls.find(([url]) => (url as string).includes("/users/me"));
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
      image_gen_monthly_limit: null,
    });
  });
});
