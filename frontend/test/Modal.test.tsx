import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Modal, { ModalClose, ModalTitle } from "@/components/Modal";

function Example({ onClose, placement = "center" }: { onClose: () => void; placement?: "center" | "drawer" }) {
  return (
    <div data-testid="transformed-ancestor" style={{ transform: "translateY(0)" }}>
      <Modal onClose={onClose} placement={placement}>
        <ModalTitle>Example dialog</ModalTitle>
        <p>Dialog body</p>
        <ModalClose asChild>
          <button>Close example</button>
        </ModalClose>
      </Modal>
    </div>
  );
}

describe("<Modal>", () => {
  it("portals outside transformed ancestors and applies centered placement", () => {
    render(<Example onClose={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Example dialog" });
    expect(screen.getByTestId("transformed-ancestor")).not.toContainElement(dialog);
    expect(document.body).toContainElement(dialog);
    expect(dialog).toHaveClass(
      "left-1/2",
      "top-1/2",
      "w-[calc(100%-2rem)]",
      "sm:w-[calc(100%-3rem)]",
    );
  });

  it("supports drawer placement", () => {
    render(<Example onClose={vi.fn()} placement="drawer" />);
    expect(screen.getByRole("dialog")).toHaveClass("right-0", "sm:h-dvh");
  });

  it("closes from Escape, the overlay, and an explicit close control", async () => {
    const onClose = vi.fn();
    render(<Example onClose={onClose} />);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByTestId("modal-overlay"));
    expect(onClose).toHaveBeenCalledTimes(2);
    await userEvent.click(screen.getByRole("button", { name: "Close example" }));
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});
