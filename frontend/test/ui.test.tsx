import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Avatar from "@/components/ui/Avatar";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Chip from "@/components/ui/Chip";
import ErrorText from "@/components/ui/ErrorText";
import Field from "@/components/ui/Field";
import Toggle from "@/components/ui/Toggle";

describe("Button", () => {
  it("maps variants onto the design-system classes", () => {
    const { rerender } = render(<Button>Go</Button>);
    const btn = () => screen.getByRole("button", { name: "Go" });
    expect(btn()).toHaveClass("btn");
    expect(btn()).toHaveAttribute("type", "button");

    rerender(<Button variant="primary">Go</Button>);
    expect(btn()).toHaveClass("btn", "btn-accent");
    rerender(<Button variant="ghost">Go</Button>);
    expect(btn()).toHaveClass("btn", "btn-ghost");
    rerender(<Button variant="danger">Go</Button>);
    expect(btn()).toHaveClass("btn", "btn-danger");
  });

  it("supports the small size and custom classes", () => {
    render(
      <Button size="sm" className="w-full">
        Go
      </Button>,
    );
    expect(screen.getByRole("button")).toHaveClass("text-[12px]", "w-full");
  });

  it("disables itself and reports busy while loading", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Saving…
      </Button>,
    );
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
    await userEvent.click(btn).catch(() => undefined);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("keeps submit type when asked", () => {
    render(<Button type="submit">Save</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "submit");
  });
});

describe("Badge", () => {
  it("renders tones", () => {
    const { rerender } = render(<Badge>Done</Badge>);
    expect(screen.getByText("Done")).toHaveClass("text-ink-faint");
    rerender(<Badge tone="accent">Done</Badge>);
    expect(screen.getByText("Done")).toHaveClass("text-accent");
    rerender(
      <Badge tone="accent-strong" title="tier">
        Done
      </Badge>,
    );
    expect(screen.getByText("Done")).toHaveClass("bg-accent-soft");
    expect(screen.getByText("Done")).toHaveAttribute("title", "tier");
  });
});

describe("Chip", () => {
  it("reflects active state and forwards clicks", async () => {
    const onClick = vi.fn();
    const { rerender } = render(<Chip onClick={onClick}>Topic</Chip>);
    const chip = screen.getByRole("button", { name: "Topic" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    expect(chip).toHaveClass("text-ink-dim");

    await userEvent.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);

    rerender(
      <Chip active onClick={onClick}>
        Topic
      </Chip>,
    );
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(chip).toHaveClass("bg-accent-soft");
  });
});

describe("Avatar", () => {
  it("shows the uppercased initial with size classes", () => {
    render(<Avatar name="sharon" title="@sharon" />);
    const el = screen.getByTitle("@sharon");
    expect(el).toHaveTextContent("S");
    expect(el).toHaveClass("h-7", "w-7");
  });

  it("falls back to ? and supports sizes and overlays", () => {
    render(
      <Avatar name={undefined} size="lg" title="who">
        <span data-testid="overlay" />
      </Avatar>,
    );
    const el = screen.getByTitle("who");
    expect(el).toHaveTextContent("?");
    expect(el).toHaveClass("h-8", "w-8");
    expect(screen.getByTestId("overlay")).toBeInTheDocument();
  });
});

describe("ErrorText", () => {
  it("announces errors via role=alert", () => {
    render(<ErrorText className="mt-2">Nope</ErrorText>);
    expect(screen.getByRole("alert")).toHaveTextContent("Nope");
    expect(screen.getByRole("alert")).toHaveClass("text-danger", "mt-2");
  });

  it("renders nothing without content", () => {
    render(<ErrorText>{null}</ErrorText>);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Field", () => {
  it("associates the label with the input", () => {
    render(<Field label="Email" defaultValue="a@b.c" />);
    expect(screen.getByLabelText("Email")).toHaveValue("a@b.c");
  });

  it("shows the hint until an error replaces it", () => {
    const { rerender } = render(<Field label="Username" hint="Pick wisely" />);
    expect(screen.getByText("Pick wisely")).toBeInTheDocument();

    rerender(<Field label="Username" hint="Pick wisely" error="Taken" />);
    expect(screen.queryByText("Pick wisely")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Taken");
  });

  it("forwards input props and honors an explicit id", () => {
    render(<Field label="Password" id="pw" type="password" minLength={8} required />);
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("id", "pw");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toBeRequired();
  });
});

describe("Toggle", () => {
  it("is a switch that flips its value", async () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Mute feed" />);
    const toggle = screen.getByRole("switch", { name: "Mute feed" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveAttribute("type", "button");

    await userEvent.click(toggle);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("supports disabled", async () => {
    const onChange = vi.fn();
    render(<Toggle checked disabled onChange={onChange} label="Mute feed" />);
    const toggle = screen.getByRole("switch");
    expect(toggle).toBeDisabled();
    await userEvent.click(toggle).catch(() => undefined);
    expect(onChange).not.toHaveBeenCalled();
  });
});
