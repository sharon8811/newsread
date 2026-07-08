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

const UNCONFIGURED: AISettings = {
  configured: false,
  system_available: true,
  provider: null,
  model: null,
  base_url: null,
  key_hint: null,
  image: null,
};

const CONFIGURED: AISettings = {
  configured: true,
  system_available: true,
  provider: "openai",
  model: "gpt-5",
  base_url: null,
  key_hint: "5678",
  image: null,
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
    expect(call.body).toEqual({ provider: "openai", model: "gpt-5", api_key: "sk-test-12345678" });
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
    expect(lastCall(fetchMock).body).toEqual({ provider: "openai", model: "gpt-6" });
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
    await userEvent.click(screen.getByRole("checkbox"));
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
