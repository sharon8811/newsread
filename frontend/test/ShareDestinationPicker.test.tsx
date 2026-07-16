import { describe, expect, it, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareDestinationPicker, {
  type ExternalShareDestination,
} from "@/components/ShareDestinationPicker";
import { makeIntegration, makePublic, makeShareTarget } from "./fixtures";

const { swrMock } = vi.hoisted(() => ({ swrMock: vi.fn() }));
vi.mock("swr", () => ({ default: swrMock }));

const bob = makePublic({ id: 2, username: "bob", name: "Bob Reader" });
const savedSlack = makeShareTarget({
  id: 11,
  platform: "slack",
  external_id: "saved-slack",
  display_name: "#saved-channel",
});
const selectedTeams: ExternalShareDestination = {
  key: "teams:selected",
  platform: "teams",
  externalId: "selected",
  displayName: "Product › General",
  targetType: "channel",
  meta: {},
  savedId: 12,
};

function renderPicker({
  recipients = [],
  externalDestinations = [],
  onAddRecipient = vi.fn(),
  onRemoveRecipient = vi.fn(),
  onAddExternal = vi.fn(),
  onRemoveExternal = vi.fn(),
}: Partial<React.ComponentProps<typeof ShareDestinationPicker>> = {}) {
  render(
    <ShareDestinationPicker
      recipients={recipients}
      externalDestinations={externalDestinations}
      onAddRecipient={onAddRecipient}
      onRemoveRecipient={onRemoveRecipient}
      onAddExternal={onAddExternal}
      onRemoveExternal={onRemoveExternal}
    />,
  );
  return { onAddRecipient, onRemoveRecipient, onAddExternal, onRemoveExternal };
}

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  swrMock.mockReset();
});

describe("<ShareDestinationPicker>", () => {
  it("keeps a mobile-safe font and explains the empty disconnected state", async () => {
    swrMock.mockImplementation((key: unknown) => {
      if (key === "/integrations" || key === "/share-targets") return { data: [] };
      return { data: undefined };
    });
    renderPicker();

    const input = screen.getByRole("combobox", { name: /Share to/ });
    expect(input).toHaveClass("text-[16px]");
    await userEvent.click(input);

    expect(screen.getByText(/connect Slack or Teams in Settings/)).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("shows defaults, merges live results, and selects saved and ad-hoc destinations", async () => {
    const liveSlack = {
      external_id: "live-slack",
      display_name: "#live-channel",
      target_type: "channel" as const,
      meta: { team: "Acme" },
      saved_id: null,
    };
    swrMock.mockImplementation((key: unknown) => {
      if (key === "/integrations") {
        return {
          data: [
            makeIntegration({ connected: true, status: "active" }),
            makeIntegration({ platform: "teams", connected: false, status: null }),
          ],
        };
      }
      if (key === "/share-targets") return { data: [savedSlack] };
      if (String(key).startsWith("/integrations/slack/targets")) {
        return {
          data: [
            {
              external_id: savedSlack.external_id,
              display_name: savedSlack.display_name,
              target_type: savedSlack.target_type,
              meta: savedSlack.meta,
              saved_id: savedSlack.id,
            },
            liveSlack,
          ],
          isLoading: false,
        };
      }
      return { data: undefined };
    });
    const onAddExternal = vi.fn();
    renderPicker({ onAddExternal });
    const input = screen.getByRole("combobox", { name: /Share to/ });

    await userEvent.click(input);
    expect(screen.getByRole("group", { name: "Slack" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Microsoft Teams" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("saved-channel"));
    expect(onAddExternal).toHaveBeenCalledWith(
      expect.objectContaining({ key: "slack:saved-slack", savedId: 11 }),
    );

    await userEvent.click(input);
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onAddExternal).toHaveBeenLastCalledWith(
      expect.objectContaining({ key: "slack:live-slack", savedId: null }),
    );
  });

  it("searches NewsRead people, filters selections, and supports removal shortcuts", async () => {
    swrMock.mockImplementation((key: unknown) => {
      if (key === "/integrations" || key === "/share-targets") return { data: [] };
      if (String(key).includes("/users/search?q=bob")) return { data: [bob] };
      return { data: undefined };
    });
    const onAddRecipient = vi.fn();
    const onRemoveRecipient = vi.fn();
    const onRemoveExternal = vi.fn();
    renderPicker({
      recipients: [bob],
      externalDestinations: [selectedTeams],
      onAddRecipient,
      onRemoveRecipient,
      onRemoveExternal,
    });
    const input = screen.getByRole("combobox", { name: /Share to/ });

    await userEvent.click(screen.getByRole("button", { name: "Remove @bob" }));
    expect(onRemoveRecipient).toHaveBeenCalledWith(2);
    await userEvent.click(screen.getByRole("button", { name: "Remove Product › General" }));
    expect(onRemoveExternal).toHaveBeenCalledWith("teams:selected");

    await userEvent.click(input);
    await userEvent.keyboard("{Backspace}");
    expect(onRemoveExternal).toHaveBeenLastCalledWith("teams:selected");

    await userEvent.type(input, "@bob");
    await waitFor(() =>
      expect(
        swrMock.mock.calls.some(([key]) => String(key).includes("/users/search?q=bob")),
      ).toBe(true),
    );
    expect(screen.queryByText("Bob Reader")).not.toBeInTheDocument();
    expect(screen.getByText("No matches")).toBeInTheDocument();
    expect(onAddRecipient).not.toHaveBeenCalled();
  });

  it("reports platform loading and errors while preserving keyboard navigation", async () => {
    swrMock.mockImplementation((key: unknown) => {
      if (key === "/integrations") {
        return {
          data: [
            makeIntegration({ connected: true, status: "active" }),
            makeIntegration({ platform: "teams", connected: true, status: "active" }),
          ],
        };
      }
      if (key === "/share-targets") return { data: [] };
      if (String(key).startsWith("/integrations/slack/targets")) {
        return { data: undefined, error: "offline", isLoading: false };
      }
      if (String(key).startsWith("/integrations/teams/targets")) {
        return { data: undefined, isLoading: true };
      }
      return { data: undefined };
    });
    renderPicker();
    const input = screen.getByRole("combobox", { name: /Share to/ });

    await userEvent.click(input);
    expect(screen.getByText("Could not load Slack destinations")).toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowUp}{ArrowDown}");
    expect(input).toHaveAttribute("aria-expanded", "true");
  });
});
